import sys, tempfile, threading, time, unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient
from iphone_backend.service import SerialWorker,create_app
from test_service import FakeBridge,fake_frame

class StubReceiver:
 def __init__(self,gate,host='0.0.0.0',port=8893):self.server=type('S',(),{'server_port':1234})()
 def start(self):pass
 def close(self):pass

class CalibrationHttpTests(unittest.TestCase):
 @patch.dict('os.environ',{'BLE_HID_CALIBRATION_HOST':'192.0.2.1'})
 @patch('iphone_backend.calibration_jobs.Receiver',StubReceiver)
 def test_idempotence_cancel_and_exclusive(self):
  with tempfile.TemporaryDirectory() as tmp:
   worker=SerialWorker(bridge_factory=lambda **kw:FakeBridge(),frame_reader=fake_frame(),runtime=Path(tmp)); entered=threading.Event();release=threading.Event()
   def run(job):
    entered.set();release.wait(3);job.finish('cancelled');worker.calibration_reserved=None
   with patch('iphone_backend.calibration_jobs.CalibrationJob.run',run),TestClient(create_app(worker)) as client:
    a=client.post('/calibrations',json={'request_id':'unique','mode':'calibrate'});self.assertEqual(a.status_code,202,a.text)
    job_id=a.json()['job_id'];self.assertEqual(client.post('/calibrations',json={'request_id':'unique','mode':'calibrate'}).json()['job_id'],job_id)
    self.assertEqual(client.post('/calibrations',json={'request_id':'unique','mode':'verify'}).status_code,409)
    j=worker.calibration_jobs[job_id];j.gate.ready(j.gate.token,'s',320,500);self.assertTrue(entered.wait(2))
    self.assertEqual(client.post('/action',json={'request_id':'during','steps':[{'op':'click'}],'capture':False}).status_code,409)
    self.assertEqual(client.post('/calibrations/'+job_id+'/cancel').status_code,200);self.assertTrue(j.cancel.is_set());release.set()
