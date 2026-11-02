from pathlib import Path
import tempfile
import time
import unittest
import threading
from unittest.mock import patch
from fastapi.testclient import TestClient
from iphone_backend.app import create_app
from iphone_backend.service import SerialWorker
from test_service import FakeBridge, fake_frame

class ResetBridge(FakeBridge):
    def __init__(self):
        super().__init__()
        self.boot='before'
        self.resets=0
    def request(self, op, **kw):
        result=super().request(op,**kw)
        return dict(result, boot_id=self.boot, device_id='board',keyboard_ready=True,absolute_ready=True)
    def reset(self, **kw):
        self.resets+=1
        self.boot='after'
        return self.request('status')

class ResetTests(unittest.TestCase):
    def test_completion_cannot_turn_a_live_snapshot_into_interruption(self):
        with tempfile.TemporaryDirectory() as folder:
            bridge=ResetBridge()
            worker=SerialWorker(bridge_factory=lambda **kw:bridge,frame_reader=fake_frame(),runtime=Path(folder))
            app=create_app(worker,restart_token='x'*32)
            import hashlib, json
            file=Path(folder)/'device-resets'/(hashlib.sha256(b'race').hexdigest()+'.json')
            record={'state':'resetting','request':{'expected_device_id':'board','expected_boot_id':'before'}}
            file.write_text(json.dumps(record))
            attempted,done=threading.Event(),threading.Event()
            def finish():
                attempted.set()
                with worker.lock:worker.reset_pending=None
                done.set()
            finisher=threading.Thread(target=finish)
            def read_stale(path):
                finisher.start()
                self.assertTrue(attempted.wait(1))
                done.wait(.1)
                return dict(record)
            with TestClient(app,client=('127.0.0.1',1234)) as client:
                worker.reset_pending='race'
                with patch('iphone_backend.device_reset.read_record',side_effect=read_stale):
                    result=client.get('/device/reset/race',headers={'Authorization':'Bearer '+'x'*32}).json()
                finisher.join(1)
                self.assertEqual(result['state'],'resetting')

    def test_reset_and_repeated_id(self):
        with tempfile.TemporaryDirectory() as folder:
            bridge=ResetBridge()
            worker=SerialWorker(bridge_factory=lambda **kw:bridge,frame_reader=fake_frame(),runtime=Path(folder))
            app=create_app(worker,restart_token='x'*32)
            headers={'Authorization':'Bearer '+'x'*32}
            with TestClient(app,client=('127.0.0.1',1234)) as client:
                body=dict(request_id='r1',expected_instance_id=worker.instance_id,
                          expected_device_id='board',expected_boot_id='before')
                self.assertEqual(client.post('/device/reset',json=body).status_code,401)
                self.assertEqual(client.post('/device/reset',json=body,headers=headers).status_code,202)
                for _ in range(100):
                    result=client.get('/device/reset/r1',headers=headers).json()
                    if result['state']=='device_rebooted': break
                    time.sleep(.02)
                self.assertEqual(result['state'],'device_rebooted')
                self.assertEqual(result['status']['boot_id'],'after')
                self.assertEqual(client.post('/device/reset',json=body,headers=headers).status_code,202)
                self.assertEqual(bridge.resets,1)
                self.assertEqual(client.post('/device/reset',json=dict(body,request_id='r2'),headers=headers).status_code,409)
