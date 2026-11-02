"""Explicit runtime acceptance probes; never sends application messages."""
import argparse
import json
from pathlib import Path
import time
import urllib.request
import uuid
import psutil

BACKEND=Path(__file__).resolve().parents[1]
CONFIG=json.loads((BACKEND/'.deployment.json').read_text(encoding='utf-8'))
OUT=BACKEND/'.runtime'/'acceptance'
OUT.mkdir(parents=True,exist_ok=True)
OPENER=urllib.request.build_opener(urllib.request.ProxyHandler({}))

def call(path, body=None, port=8892):
    request=urllib.request.Request(f'http://127.0.0.1:{port}'+path,
        data=json.dumps(body).encode() if body is not None else None,
        headers={'Authorization':'Bearer '+CONFIG['BLE_HID_RESTART_TOKEN'],'Content-Type':'application/json'})
    with OPENER.open(request,timeout=5) as response:return json.load(response)

def save(name,data):
    (OUT/name).write_text(json.dumps(data,indent=2),encoding='utf-8')

def pids():
    return sorted(p.pid for p in psutil.process_iter(['name']) if (p.info['name'] or '').lower()=='uxplay-windows.exe')

def restart():
    before=call('/health')
    capture=call('/health',port=8891)
    receiver=pids()
    task='capture-acceptance-'+uuid.uuid4().hex
    started=call('/screen/start',{'request_id':task})
    deadline=time.monotonic()+10
    while time.monotonic()<deadline:
        start_result=call('/screen/start/'+task)
        if start_result['state'] in ('ready','failed','unknown'):break
        time.sleep(.1)
    if start_result['state']!='ready':raise RuntimeError(str(start_result))
    requested=call('/admin/restart',{'request_id':'restart-'+uuid.uuid4().hex,'expected_instance_id':before['instance_id']})
    save('restart-before.json',{'health':before,'capture':capture,'receiver':receiver,'start_task':task,'start_result':start_result,'requested':requested})
    deadline=time.monotonic()+40
    while time.monotonic()<deadline:
        try:
            after=call('/health')
            manager=call('/health',port=8894)
            if after['instance_id']!=before['instance_id'] and manager['api_state']=='ready':break
        except OSError:pass
        time.sleep(.2)
    else:raise RuntimeError('Restart not confirmed; inspect manager, do not replay')
    after_capture=call('/health',port=8891)
    result={'health':after,'manager':manager,'capture':after_capture,'receiver':pids(),'start_result':call('/screen/start/'+task)}
    save('restart-after.json',result)
    assert after['fault'] is None,result
    assert before['status']['boot_id']==after['status']['boot_id'],'Backend restart unexpectedly rebooted device'
    assert capture['pid']==after_capture['pid'] and receiver==pids(),'AirPlay process changed'
    assert result['start_result']['state']=='ready'
    print(json.dumps({'api_pid':after['pid'],'manager_pid':manager['pid'],'capture_pid':after_capture['pid'],'restart_verified':True}))

def reset():
    before=call('/health')
    capture=call('/health',port=8891)
    receiver=pids()
    status=before['status']
    body={'request_id':'device-reset-'+uuid.uuid4().hex,'expected_instance_id':before['instance_id'],
          'expected_device_id':status['device_id'],'expected_boot_id':status['boot_id']}
    accepted=call('/device/reset',body)
    save('reset-before.json',{'health':before,'capture':capture,'receiver':receiver,'request':body,'accepted':accepted})
    deadline=time.monotonic()+40
    while time.monotonic()<deadline:
        result=call('/device/reset/'+body['request_id'])
        if result['state'] in ('unknown','failed'):raise RuntimeError(str(result))
        if result['state']=='device_rebooted' and result.get('hid_ready'):break
        time.sleep(.2)
    else:raise RuntimeError('Reset/readiness not confirmed; do not repeat reset')
    after=call('/health')
    repeated=call('/device/reset',body)
    time.sleep(.5)
    current=call('/health')
    save('reset-after.json',{'health':after,'result':result,'repeated':repeated,'current':current,'capture':call('/health',port=8891),'receiver':pids()})
    assert current['status']['boot_id']==after['status']['boot_id']!=status['boot_id']
    assert current['status']['device_id']==status['device_id']
    assert current['profile'] is None or current['profile']['verification']=='needs_verification'
    assert call('/health',port=8891)['pid']==capture['pid'] and pids()==receiver
    print(json.dumps({'device_reboot_verified':True,'boot_id':after['status']['boot_id'],'hid_ready':result['hid_ready']}))

if __name__=='__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('operation',choices=('restart','reset'))
    args=parser.parse_args()
    restart() if args.operation=='restart' else reset()
