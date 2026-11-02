"""Explicit migration helper: prepare first, then hand over the idle old API."""
import argparse
import json
import os
from pathlib import Path
import subprocess
import socket
import time
import urllib.request
import uuid
import psutil

BACKEND=Path(__file__).resolve().parents[1]
PROJECT=BACKEND.parent
CONFIG=BACKEND/'.deployment.json'
EVIDENCE=BACKEND/'.runtime'/'deployment'
OPENER=urllib.request.build_opener(urllib.request.ProxyHandler({}))

def get(url):
    with OPENER.open(url,timeout=3) as response:return json.load(response)

def post(url,data,token):
    request=urllib.request.Request(url,data=json.dumps(data).encode(),
        headers={'Content-Type':'application/json','Authorization':'Bearer '+token},method='POST')
    with OPENER.open(request,timeout=5) as response:return json.load(response)

def old_processes(health):
    api=psutil.Process(health['pid'])
    expected=(PROJECT/'pc'/'service.py').resolve()
    if not any(Path(arg).is_absolute() and Path(arg).resolve()==expected for arg in api.cmdline()):
        raise RuntimeError('API is not the expected legacy source')
    manager=api.parent()
    if manager is None or not any(Path(arg).name=='service_manager.py' for arg in manager.cmdline()):
        raise RuntimeError('Legacy manager identity mismatch')
    if Path(manager.cwd()).resolve()!=PROJECT:raise RuntimeError('Legacy manager working directory mismatch')
    return api,manager

def prepare():
    health=get('http://127.0.0.1:8892/health')
    api,manager=old_processes(health)
    capture=get('http://127.0.0.1:8891/health')
    source_env=api.environ()
    capture_env=psutil.Process(capture['pid']).environ()
    env={key:value for key,value in source_env.items() if key.startswith('BLE_HID_')}
    env.pop('BLE_HID_MANAGED',None)
    env.update(IPHONE_PROJECT_ROOT=str(PROJECT),IPHONE_RUNTIME_DIR=str(PROJECT/'.runtime'),
        IPHONE_LEGACY_CAPTURE_OWNER=capture['owner'],
        UXPLAY_DIR=capture_env.get('UXPLAY_DIR',str(PROJECT.parent/'uxplay-windows')),
        HIDAGENT_DATA=capture_env.get('HIDAGENT_DATA',str(Path.home()/'.hidagent')),
        AIRPLAY_NAME=capture_env.get('AIRPLAY_NAME','iPhone-Agent'))
    if len(env.get('BLE_HID_RESTART_TOKEN',''))<32:raise RuntimeError('Missing management token')
    CONFIG.write_text(json.dumps(env,indent=2),encoding='utf-8')
    EVIDENCE.mkdir(parents=True,exist_ok=True)
    (EVIDENCE/'before.json').write_text(json.dumps({'health':health,'capture':capture,
        'manager_pid':manager.pid,'manager_created_at':manager.create_time()},indent=2),encoding='utf-8')
    print('Deployment configuration prepared; token kept in ignored local file')

def launch():
    env=dict(os.environ,**json.loads(CONFIG.read_text(encoding='utf-8')))
    env.pop('PYTHONPATH',None)
    EVIDENCE.mkdir(parents=True,exist_ok=True)
    with (EVIDENCE/'manager.log').open('ab') as out:
        return subprocess.Popen([str(BACKEND/'.venv'/'Scripts'/'python.exe'),'-m','iphone_backend'],
            cwd=BACKEND,env=env,stdin=subprocess.DEVNULL,stdout=out,stderr=out,
            creationflags=subprocess.CREATE_NO_WINDOW)

def handover():
    env=json.loads(CONFIG.read_text(encoding='utf-8'))
    before=get('http://127.0.0.1:8892/health')
    if before['active_id'] or before['queue_size'] or before['fault']:
        raise RuntimeError('Legacy API must be healthy and idle')
    api,manager=old_processes(before)
    original_capture=get('http://127.0.0.1:8891/health')
    manager.suspend()
    retired=False
    try:
        post('http://127.0.0.1:8892/admin/restart',{'request_id':uuid.uuid4().hex,
             'expected_instance_id':before['instance_id']},env['BLE_HID_RESTART_TOKEN'])
        if api.wait(timeout=25)!=75:raise RuntimeError('Old API did not release serial cleanly')
        # Only this verified, suspended legacy manager remains; its API has exited.
        manager.terminate()
        manager.wait(timeout=5)
        retired=True
    finally:
        if not retired and manager.is_running():manager.resume()
    new=launch()
    deadline=time.monotonic()+30
    while time.monotonic()<deadline:
        if new.poll() is not None:raise RuntimeError('New manager exited; inspect deployment/manager.log')
        try:
            current=get('http://127.0.0.1:8892/health')
            if current['instance_id']!=before['instance_id'] and current.get('components'):
                capture=get('http://127.0.0.1:8891/health')
                if capture['pid']!=original_capture['pid']:raise RuntimeError('Capture unexpectedly replaced')
                (EVIDENCE/'after.json').write_text(json.dumps({'health':current,'capture':capture,'manager_pid':new.pid},indent=2),encoding='utf-8')
                print(json.dumps({'api_pid':current['pid'],'manager_pid':new.pid,'capture_pid':capture['pid'],'fault':current['fault']}))
                return
        except (OSError,ValueError):pass
        time.sleep(.2)
    raise RuntimeError('New API readiness timed out; inspect state, do not start a second instance')

def recover_manager():
    config=json.loads(CONFIG.read_text(encoding='utf-8'))
    request=urllib.request.Request('http://127.0.0.1:8894/health',headers={'Authorization':'Bearer '+config['BLE_HID_RESTART_TOKEN']})
    with OPENER.open(request,timeout=3) as response: state=json.load(response)
    if state.get('api_state')!='attention_required':raise RuntimeError('Manager not in confirmed failed state')
    with socket.socket() as probe:
        probe.setsockopt(socket.SOL_SOCKET,socket.SO_EXCLUSIVEADDRUSE,1)
        probe.bind(('127.0.0.1',8892))
    process=psutil.Process(state['pid'])
    if 'iphone_backend' not in process.cmdline() or process.children():
        raise RuntimeError('Manager identity or child ownership changed; inspect manually')
    process.terminate()
    process.wait(timeout=5)
    print(json.dumps({'manager_pid':launch().pid}))

def restart_manager():
    """Reload only the verified manager; retain API, serial and capture owners."""
    config=json.loads(CONFIG.read_text(encoding='utf-8'))
    headers={'Authorization':'Bearer '+config['BLE_HID_RESTART_TOKEN']}
    def authenticated(port,path):
        with OPENER.open(urllib.request.Request(f'http://127.0.0.1:{port}'+path,headers=headers),timeout=3) as response:
            return json.load(response)
    state=authenticated(8894,'/health')
    identity=authenticated(8892,'/admin/identity')
    health=get('http://127.0.0.1:8892/health')
    capture=get('http://127.0.0.1:8891/health')
    if state.get('api_state')!='ready' or state.get('capture_pending') or health['active_id'] or health['queue_size']:
        raise RuntimeError('Manager and API must be idle and ready')
    manager=psutil.Process(state['pid'])
    api=psutil.Process(identity['pid'])
    registered=json.loads((Path(config['IPHONE_RUNTIME_DIR'])/'api-instance.json').read_text())
    if any(identity.get(key)!=registered.get(key) for key in ('pid','created_at','instance_id')):
        raise RuntimeError('API registration does not match authenticated identity')
    if api.create_time()!=identity['created_at'] or identity['runtime_dir']!=config['IPHONE_RUNTIME_DIR']:
        raise RuntimeError('API process/configuration mismatch')
    command=manager.cmdline()
    if 'iphone_backend' not in command or '--role' in command or manager.environ().get('IPHONE_RUNTIME_DIR')!=config['IPHONE_RUNTIME_DIR']:
        raise RuntimeError('Manager command/configuration mismatch')
    receiver=sorted(p.pid for p in psutil.process_iter(['name']) if (p.info['name'] or '').lower()=='uxplay-windows.exe')
    manager.terminate()  # Terminate this process only, never its children.
    manager.wait(timeout=5)
    launched=launch()
    deadline=time.monotonic()+30
    while time.monotonic()<deadline:
        if launched.poll() is not None:raise RuntimeError('New manager exited; inspect logs')
        try:
            after=authenticated(8894,'/health')
            if after['api_state']=='attention_required':raise RuntimeError(str(after))
            if after['api_state']=='ready':break
        except OSError:pass
        time.sleep(.2)
    else:raise RuntimeError('Manager adoption timed out; inspect without replay')
    current=authenticated(8892,'/admin/identity')
    current_capture=get('http://127.0.0.1:8891/health')
    receiver_after=sorted(p.pid for p in psutil.process_iter(['name']) if (p.info['name'] or '').lower()=='uxplay-windows.exe')
    if current!=identity or current_capture['pid']!=capture['pid'] or receiver_after!=receiver:
        raise RuntimeError('A retained owner changed during manager handover')
    (EVIDENCE/'manager-reload.json').write_text(json.dumps({'before':state,'after':after,'api':current,'capture':current_capture,'receiver_pids':receiver_after},indent=2),encoding='utf-8')
    print(json.dumps({'manager_pid':after['pid'],'retained_api_pid':current['pid'],'retained_capture_pid':current_capture['pid']}))

def adopt_manager():
    config=json.loads(CONFIG.read_text(encoding='utf-8'))
    headers={'Authorization':'Bearer '+config['BLE_HID_RESTART_TOKEN']}
    with OPENER.open(urllib.request.Request('http://127.0.0.1:8894/health',headers=headers),timeout=3) as response:state=json.load(response)
    with OPENER.open(urllib.request.Request('http://127.0.0.1:8892/admin/identity',headers=headers),timeout=3) as response:identity=json.load(response)
    if state.get('api_state')!='attention_required':raise RuntimeError('Manager is not in failed supervision state')
    manager=psutil.Process(state['pid'])
    api=psutil.Process(identity['pid'])
    if 'iphone_backend' not in manager.cmdline() or api.pid not in {p.pid for p in manager.children(recursive=True)}:
        raise RuntimeError('API is not a verified descendant of this manager')
    if api.create_time()!=identity['created_at'] or identity['runtime_dir']!=config['IPHONE_RUNTIME_DIR']:
        raise RuntimeError('API identity/configuration mismatch')
    target=Path(config['IPHONE_RUNTIME_DIR'])/'api-instance.json'
    target.write_text(json.dumps({k:identity[k] for k in ('pid','created_at','instance_id')}),encoding='utf-8')
    manager.terminate()  # API child remains alive; new manager authenticates before adopting it.
    manager.wait(timeout=5)
    print(json.dumps({'manager_launcher_pid':launch().pid,'retained_api_pid':api.pid}))

if __name__=='__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('operation',choices=('prepare','handover','start','recover-manager','adopt-manager','restart-manager'))
    args=parser.parse_args()
    if args.operation=='prepare':prepare()
    elif args.operation=='handover':handover()
    elif args.operation=='recover-manager':recover_manager()
    elif args.operation=='adopt-manager':adopt_manager()
    elif args.operation=='restart-manager':restart_manager()
    else:print(json.dumps({'manager_pid':launch().pid}))
