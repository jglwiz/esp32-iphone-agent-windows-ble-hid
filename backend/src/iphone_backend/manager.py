"""Async supervisor and authenticated capture-start IPC."""
import asyncio
import hashlib
import json
import os
import secrets
import subprocess
import socket
import sys
import time
from pathlib import Path
from urllib.parse import unquote
import httpx
import psutil
import uvicorn
from .airplay.service import CaptureService
from .config import RUNTIME_DIR
from .service_manager import manager_lock, port_available, RESTART_EXIT

def save(path, value):
    temporary = path.with_suffix('.tmp')
    with temporary.open('w',encoding='utf-8') as out:
        json.dump(value,out)
        out.flush()
        os.fsync(out.fileno())
    os.replace(temporary,path)

class Manager:
    def __init__(self, token):
        self.token = token
        self.capture = CaptureService()
        self.tasks = {}
        self.folder = RUNTIME_DIR/'capture-tasks'
        self.folder.mkdir(parents=True,exist_ok=True)
        self.api_state = 'starting'
        self.api_error = None

    def path(self, key):
        return self.folder/(hashlib.sha256(key.encode()).hexdigest()+'.json')

    async def start_capture(self,key):
        result = {'request_id':key,'state':'starting'}
        save(self.path(key),result)
        try: result.update(await self.capture.ensure_started())
        except Exception as exc: result.update(state='failed',error=str(exc))
        result['finished_at']=time.time()
        save(self.path(key),result)

    async def __call__(self, scope, receive, send):
        if scope['type'] != 'http': return
        headers = dict(scope['headers'])
        status, result = 200, {}
        if not secrets.compare_digest(headers.get(b'authorization',b''), ('Bearer '+self.token).encode()):
            status,result=401,{'error':'Unauthorized'}
        else:
            path=unquote(scope['path'])
            if path=='/health':
                result={'pid':os.getpid(),'api_state':self.api_state,'api_error':self.api_error,
                        'capture_pending':any(not task.done() for task in self.tasks.values())}
            elif path=='/screen/start' and scope['method']=='POST':
                data=b''
                while True:
                    event=await receive()
                    if event['type']=='http.disconnect': return
                    data+=event.get('body',b'')
                    if len(data)>4096: break
                    if not event.get('more_body'): break
                try:
                    key=json.loads(data)['request_id']
                    if not isinstance(key,str) or not 1<=len(key)<=80 or len(data)>4096: raise ValueError()
                    file=self.path(key)
                    if file.exists(): result=json.loads(file.read_text())
                    else:
                        result={'request_id':key,'state':'accepted'}
                        save(file,result)
                        self.tasks[key]=asyncio.create_task(self.start_capture(key))
                    status=202
                except (ValueError,KeyError,TypeError): status,result=400,{'error':'Invalid request'}
            elif path.startswith('/screen/start/'):
                file=self.path(path.removeprefix('/screen/start/'))
                if file.exists(): result=json.loads(file.read_text())
                else: status,result=404,{'error':'Unknown task'}
            else: status,result=404,{'error':'Unknown operation'}
        body=json.dumps(result).encode()
        await send({'type':'http.response.start','status':status,'headers':[(b'content-type',b'application/json')]})
        await send({'type':'http.response.body','body':body})

    async def api_health(self):
        async with httpx.AsyncClient(trust_env=False,timeout=1) as client:
            response=await client.get('http://127.0.0.1:8892/health')
            response.raise_for_status()
            return response.json()

    async def supervise(self):
        registration=RUNTIME_DIR/'api-instance.json'
        child=None
        try:
            try:
                health=await self.api_health()
            except httpx.TransportError:
                # Windows can report an absent localhost listener as ConnectTimeout.
                # Confirm exclusive bind before treating a transport failure as absence.
                port_available()
                health=None
            if health:
                if not registration.exists(): raise RuntimeError('Existing API is not registered; controlled handover required')
                identity=json.loads(registration.read_text())
                process=psutil.Process(identity['pid'])
                if health.get('pid')!=identity['pid'] or health.get('instance_id')!=identity['instance_id'] or process.create_time()!=identity['created_at']:
                    raise RuntimeError('Existing API identity mismatch; no replacement started')
                async with httpx.AsyncClient(trust_env=False,timeout=1) as client:
                    response=await client.get('http://127.0.0.1:8892/admin/identity',headers={'Authorization':'Bearer '+self.token})
                    response.raise_for_status()
                    verified=response.json()
                if (verified.get('pid')!=identity['pid'] or verified.get('created_at')!=identity['created_at']
                    or verified.get('runtime_dir')!=str(RUNTIME_DIR) or verified.get('protocol')!=1
                    or verified.get('instance_id')!=identity['instance_id']):
                    raise RuntimeError('Existing API management identity incompatible')
            else:
                process=None
            while True:
                if process is None:
                    env=dict(os.environ,BLE_HID_MANAGED='1')
                    with (RUNTIME_DIR/'backend.log').open('ab') as log:
                        child=subprocess.Popen([sys.executable,'-m','iphone_backend','--role','api'],
                            env=env,stdin=subprocess.DEVNULL,stdout=log,stderr=log,
                            creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
                    process=psutil.Process(child.pid)
                    save(RUNTIME_DIR/'api-launch.json',{'pid':child.pid,'created_at':process.create_time()})
                    deadline=time.monotonic()+20
                    while True:
                        if child.poll() is not None: raise RuntimeError('API exited during startup')
                        try:
                            health=await self.api_health()
                            candidate_pid=health.get('pid')
                            descendants={p.pid for p in process.children(recursive=True)}
                            if candidate_pid==child.pid or candidate_pid in descendants:
                                candidate=psutil.Process(candidate_pid)
                                command=candidate.cmdline()
                                if 'iphone_backend' not in command or '--role' not in command or 'api' not in command:
                                    raise RuntimeError('API descendant command identity mismatch')
                                process=candidate
                                break
                        except httpx.HTTPError: pass
                        if time.monotonic()>deadline: raise RuntimeError('API readiness timed out; retained existing owner')
                        await asyncio.sleep(.1)
                    identity={'pid':process.pid,'created_at':process.create_time(),'instance_id':health['instance_id']}
                    save(registration,identity)
                self.api_state='ready'
                while process.is_running():
                    if child is not None and child.poll() is not None: break
                    await asyncio.sleep(.2)
                proof=RUNTIME_DIR/('api-exit-'+identity['instance_id']+'.json')
                if not proof.exists() or not json.loads(proof.read_text()).get('clean_restart'):
                    raise RuntimeError('API exited without clean restart proof; no automatic replay')
                if child is not None:
                    deadline=time.monotonic()+5
                    while child.poll() is None and time.monotonic()<deadline:
                        await asyncio.sleep(.05)
                    if child.returncode!=RESTART_EXIT:
                        raise RuntimeError('API launcher restart exit code mismatch')
                process=None
                self.api_state='restarting'
        except Exception as exc:
            self.api_state='attention_required'
            self.api_error=f'{type(exc).__name__}: {exc}'

    async def run(self):
        listener=socket.socket()
        if os.name=='nt': listener.setsockopt(socket.SOL_SOCKET,socket.SO_EXCLUSIVEADDRUSE,1)
        listener.bind(('127.0.0.1',int(os.environ.get('IPHONE_MANAGER_PORT','8894'))))
        listener.setblocking(False)
        supervisor=None
        try:
            try: await self.capture.ensure_started()
            except Exception as exc: self.api_error='Capture: '+str(exc)
            for file in self.folder.glob('*.json'):
                value=json.loads(file.read_text())
                if value.get('state') in ('accepted','starting'):
                    try: value.update(state='ready',health=await self.capture.health())
                    except Exception: value.update(state='unknown',error='Manager interrupted; inspect before new request')
                    save(file,value)
            supervisor=asyncio.create_task(self.supervise())
            server=uvicorn.Server(uvicorn.Config(self,lifespan='off',access_log=False,proxy_headers=False))
            await server.serve(sockets=[listener])
        finally:
            if supervisor:
                supervisor.cancel()
                await asyncio.gather(supervisor,return_exceptions=True)
            listener.close()

def main():
    token=os.environ.get('BLE_HID_RESTART_TOKEN','')
    if len(token)<32: raise SystemExit('Set BLE_HID_RESTART_TOKEN (at least 32 characters)')
    RUNTIME_DIR.mkdir(parents=True,exist_ok=True)
    with manager_lock(RUNTIME_DIR/'service-manager.lock'):
        asyncio.run(Manager(token).run())
