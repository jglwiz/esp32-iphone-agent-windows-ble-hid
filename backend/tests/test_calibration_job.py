import json,sys,tempfile,threading,time,unittest
from pathlib import Path
from unittest.mock import patch
import cv2,numpy as np

from iphone_backend.calibration_jobs import CalibrationJob

class FakeReceiver:
 def __init__(self,gate,host='0.0.0.0',port=8893):self.server=type('S',(),{'server_port':1234})();self.gate=gate
 def start(self):self.gate.ready(self.gate.token,'session',320,502);self.gate.last_poll=time.monotonic()
 def close(self):pass
class FakeBridge:
 def request(self,op,**kw):return {'state':'ok'}
class FakeWorker:
 def __init__(self,folder):
  self.runtime=folder;self.device_id='dev';self.phone_id='phone';self.lock=threading.Lock();self.calibration_reserved=None;self.profile=None;self.profile_generation=0;self.geometry_last=None;self.fault=None;self.bridge=FakeBridge();self.job=None;self.calls=[];self.offset=(0,0);self.frame_seq=0
 def set_fault(self,reason):self.fault=reason
 def record_status(self,status):pass
 def check(self,c,d):
  if c.is_set() or time.perf_counter()>d:raise RuntimeError('cancelled')
 def send(self,c,d,op,**kw):
  self.check(c,d);self.calls.append(op)
  if op=='status':return {'device_id':'dev','supports_absolute_pointer':True,'absolute_descriptor_version':1,'absolute_ready':True,'boot_id':'b','connection_generation':1}
  if op=='abs.click':
   x=kw['x']/32767*495+self.offset[0];y=kw['y']/32767*1079+self.offset[1]
   # markers at 40,225 and 455,880
   cx=26+(x-40)/(455-40)*268;cy=46+(y-225)/(880-225)*420
   g=self.job.gate
   for k in ('pointerdown','pointerup','click'):g.event(g.token,'session',g.armed,k,cx,cy,320,502)
  return {'state':'ok'}
 def pause(self,seconds,c,d):
  self.check(c,d);g=self.job.gate;g.last_poll=time.monotonic()
  if g.armed and not g.acknowledged:g.ack(g.token,'session',g.armed)
 def capture(self,after,c,d):
  import base64
  image=np.full((1080,496,3),255,np.uint8)
  for x,y in ((40,225),(455,225),(40,880),(455,880)):cv2.rectangle(image,(x-7,y-7),(x+7,y+7),(255,255,0),-1)
  data=base64.b64encode(cv2.imencode('.jpg',image)[1].tobytes()).decode();self.frame_seq+=1
  return {'state':'available'},{'snapshot_version':1,'atomic_snapshot':True,'frame_id':'fake:'+str(self.frame_seq),'frame_time':time.time()+1,'jpeg_base64':data}

class CalibrationJobTests(unittest.TestCase):
 @patch.dict('os.environ',{'BLE_HID_CALIBRATION_HOST':'192.0.2.1'})
 @patch('iphone_backend.calibration_jobs.Receiver',FakeReceiver)
 def test_complete_and_release_before_publish(self):
  with tempfile.TemporaryDirectory() as tmp:
   w=FakeWorker(Path(tmp));j=CalibrationJob('a','calibrate',w);w.job=j;j.run()
   self.assertEqual(j.result['state'],'passed',j.result.get('error'));self.assertEqual(len(j.result['samples']),16)
   self.assertEqual(w.calls.count('abs.click'),16);self.assertEqual(w.calls[-1],'release_all');self.assertEqual(w.profile['verification'],'verified')
   self.assertTrue((Path(tmp)/'calibration-profile.json').exists())
 @patch.dict('os.environ',{'BLE_HID_CALIBRATION_HOST':'192.0.2.1'})
 @patch('iphone_backend.calibration_jobs.Receiver',FakeReceiver)
 def test_release_failure_does_not_publish(self):
  with tempfile.TemporaryDirectory() as tmp:
   w=FakeWorker(Path(tmp));j=CalibrationJob('a','calibrate',w);w.job=j;send=w.send
   def fail(c,d,op,**kw):
    if op=='release_all':raise RuntimeError('release failed')
    return send(c,d,op,**kw)
   w.send=fail;j.run();self.assertEqual(j.result['state'],'failed');self.assertIsNone(w.profile);self.assertFalse((Path(tmp)/'calibration-profile.json').exists())

 @patch.dict('os.environ',{'BLE_HID_CALIBRATION_HOST':'192.0.2.1'})
 @patch('iphone_backend.calibration_jobs.Receiver',FakeReceiver)
 def test_bias_correction_on_holdout(self):
  with tempfile.TemporaryDirectory() as tmp:
   w=FakeWorker(Path(tmp));w.offset=(12,-10);j=CalibrationJob('bias','calibrate',w);w.job=j;j.run()
   self.assertEqual(j.result['state'],'passed',j.result.get('error'));self.assertEqual(j.result['mapping'],'inverse_affine')
   self.assertLessEqual(j.result['profile']['max_holdout_error'],8)
 @patch.dict('os.environ',{'BLE_HID_CALIBRATION_HOST':'192.0.2.1'})
 @patch('iphone_backend.calibration_jobs.Receiver',FakeReceiver)
 def test_evidence_failure_fails_closed(self):
  with tempfile.TemporaryDirectory() as tmp:
   w=FakeWorker(Path(tmp));j=CalibrationJob('disk','calibrate',w);w.job=j
   def diskfull():raise OSError('disk full')
   j.save=diskfull;j.run();self.assertEqual(j.result['state'],'failed');self.assertIsNotNone(w.fault);self.assertIsNone(w.profile)

 @patch.dict('os.environ',{'BLE_HID_CALIBRATION_HOST':'192.0.2.1'})
 @patch('iphone_backend.calibration_jobs.Receiver',FakeReceiver)
 @patch('iphone_backend.calibration_jobs.atomic_save',side_effect=OSError('replace failed'))
 def test_profile_commit_failure_keeps_old_profile(self,_):
  with tempfile.TemporaryDirectory() as tmp:
   folder=Path(tmp);old={'profile_id':'previous','verification':'verified'}
   (folder/'calibration-profile.json').write_text(json.dumps(old),encoding='utf-8')
   w=FakeWorker(folder);w.profile=old.copy();j=CalibrationJob('commit','calibrate',w);w.job=j;j.run()
   self.assertEqual(j.result['state'],'failed');self.assertEqual(w.profile,old)
   self.assertEqual(json.loads((folder/'calibration-profile.json').read_text()),old)

 @patch.dict('os.environ',{'BLE_HID_CALIBRATION_HOST':'192.0.2.1'})
 @patch('iphone_backend.calibration_jobs.Receiver',FakeReceiver)
 def test_cancel_prevents_later_clicks(self):
  with tempfile.TemporaryDirectory() as tmp:
   w=FakeWorker(Path(tmp));j=CalibrationJob('cancel','calibrate',w);w.job=j;send=w.send
   def cancel_after(c,d,op,**kw):
    result=send(c,d,op,**kw)
    if op=='abs.click':c.set()
    return result
   w.send=cancel_after;j.run();self.assertEqual(j.result['state'],'cancelled');self.assertEqual(w.calls.count('abs.click'),1);self.assertIsNone(w.profile)
 @patch.dict('os.environ',{'BLE_HID_CALIBRATION_HOST':'192.0.2.1'})
 @patch('iphone_backend.calibration_jobs.Receiver',FakeReceiver)
 def test_geometry_change_before_click_rejected(self):
  with tempfile.TemporaryDirectory() as tmp:
   w=FakeWorker(Path(tmp));j=CalibrationJob('geometry','calibrate',w);w.job=j;geom=j.geometry;count=[0]
   def changed(after=None):
    marks,frame,path=geom(after);count[0]+=1
    if count[0]>1:marks[0]+=10
    return marks,frame,path
   j.geometry=changed;j.run();self.assertEqual(j.result['state'],'failed');self.assertEqual(w.calls.count('abs.click'),0)
