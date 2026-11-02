"""One controlled migration of the verified old capture worker; retains UxPlay."""
import json
import os
from pathlib import Path
import time
import urllib.request
import uuid
import psutil

BACKEND=Path(__file__).resolve().parents[1]
CONFIG=BACKEND/'.deployment.json'
OUT=BACKEND/'.runtime'/'capture-cutover'
OPENER=urllib.request.build_opener(urllib.request.ProxyHandler({}))

def call(path,port=8892,body=None):
    config=json.loads(CONFIG.read_text(encoding='utf-8'))
    request=urllib.request.Request(f'http://127.0.0.1:{port}'+path,
        data=json.dumps(body).encode() if body is not None else None,
        headers={'Authorization':'Bearer '+config['BLE_HID_RESTART_TOKEN'],'Content-Type':'application/json'})
    with OPENER.open(request,timeout=4) as response:return json.load(response)

def main():
    config=json.loads(CONFIG.read_text(encoding='utf-8'))
    before=call('/health',8891)
    expected=str((BACKEND.parent.parent/'upstream-windows'/'server'/'hidagent').resolve())
    if os.path.normcase(before['owner'])!=os.path.normcase(expected):
        raise RuntimeError('Not the expected legacy capture instance; no process changed')
    api=call('/health')
    if api['active_id'] or api['queue_size']:raise RuntimeError('API must be idle before capture switch')
    if call('/health',8894)['api_state']!='ready':raise RuntimeError('Manager must be ready')
    process=psutil.Process(before['pid'])
    if process.cmdline()[-2:]!=['-m','hidagent.capture_worker']:
        raise RuntimeError('Capture command identity mismatch')
    receiver=sorted(p.pid for p in psutil.process_iter(['name']) if (p.info['name'] or '').lower()=='uxplay-windows.exe')
    OUT.mkdir(parents=True,exist_ok=True)
    (OUT/'before.json').write_text(json.dumps({'capture':before,'api':api,'receiver_pids':receiver},indent=2),encoding='utf-8')
    process.terminate()
    process.wait(timeout=5)
    task='capture-cutover-'+uuid.uuid4().hex
    accepted=call('/screen/start',body={'request_id':task})
    (OUT/'request.json').write_text(json.dumps({'request_id':task,'accepted':accepted},indent=2),encoding='utf-8')
    deadline=time.monotonic()+20
    while time.monotonic()<deadline:
        result=call('/screen/start/'+task)
        if result['state'] in ('ready','failed','unknown'):break
        time.sleep(.2)
    if result['state']!='ready':raise RuntimeError(str(result))
    after=call('/health',8891)
    expected_new=str((BACKEND/'src'/'iphone_backend'/'airplay').resolve())
    if os.path.normcase(after['owner'])!=os.path.normcase(expected_new):raise RuntimeError('New capture owner mismatch')
    config.pop('IPHONE_LEGACY_CAPTURE_OWNER',None)
    CONFIG.write_text(json.dumps(config,indent=2),encoding='utf-8')
    receiver_after=sorted(p.pid for p in psutil.process_iter(['name']) if (p.info['name'] or '').lower()=='uxplay-windows.exe')
    (OUT/'after.json').write_text(json.dumps({'capture':after,'task':result,'receiver_pids':receiver_after},indent=2),encoding='utf-8')
    if receiver_after!=receiver:
        raise RuntimeError('Receiver process changed during capture cutover; inspect evidence and reconnect mirroring before acceptance')
    print(json.dumps({'capture_pid':after['pid'],'live':after['live'],'owner':after['owner'],'note':'If no frame, reconnect iPhone screen mirroring; do not restart receiver'}))

if __name__=='__main__':main()
