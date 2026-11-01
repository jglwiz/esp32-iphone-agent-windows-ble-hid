import tempfile, unittest, sys, time, hashlib
import cv2, numpy as np
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'pc'))
from service import Action, SerialWorker
from test_service import FakeBridge, fake_frame

class NativeBridge(FakeBridge):
 def request(self,op,**kw):
  super().request(op,**kw)
  return {'state':'ok','connected':True,'boot_id':'b','connection_generation':1,'device_id':'d','supports_absolute_pointer':True,'absolute_descriptor_version':1,'absolute_ready':True}

class NativeServiceTests(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.bridge=NativeBridge();
  async def read(client):
   from frame_capture import Frame
   return Frame(cv2.imencode('.jpg',np.zeros((1080,496,3),dtype=np.uint8))[1].tobytes(),time.time(),'test:1')
  self.w=SerialWorker(bridge_factory=lambda **kw:self.bridge,frame_reader=read,runtime=Path(self.tmp.name));self.w.start();self.w.device_id='d';self.w.phone_id='phone';self.w.geometry=(496,1080,'portrait');self.w.profile={'schema_version':1,'profile_id':'p','device_id':'d','phone_id':'phone','descriptor_version':1,'geometry':[496,1080,'portrait'],'coordinate_space':'full_screenshot_pixels','verification':'verified','verified_region':[186,455,310,651],'axes':[{'slope':1.,'offset':0.},{'slope':1.,'offset':0.}],'frame_source_id':'test','frame_sequence':0,'frame_timestamp':0.,'frame_sha256':'0'*64}
 def tearDown(self):self.w.stop();self.tmp.cleanup()
 def test_single_move_and_dedup(self):
  a=Action(request_id='native',coordinate_backend='native_hid',diagnostic_uncalibrated=True,steps=[{'op':'move_to','x':100,'y':300}],capture=False)
  f,_=self.w.submit(a);r=f.result(3);self.assertEqual(r['absolute'][0]['status'],'report_submitted');self.assertIs(f,self.w.submit(a)[0]);self.assertEqual(sum(x[0]=='abs.move' for x in self.bridge.calls),1)
 def test_entire_batch_rejected_before_input(self):
  a=Action(request_id='unsafe',coordinate_backend='native_hid',steps=[{'op':'key','usage':4},{'op':'click_at','x':100,'y':300}],capture=False)
  r=self.w.submit(a)[0].result(3);self.assertEqual(r['input_state'],'not_started');self.assertFalse(any(op in ('keyboard.tap','abs.click') for op,_ in self.bridge.calls))
 def test_diagnostic_move_with_unverified_legacy_profile(self):
  self.w.profile['verification']='needs_verification'
  for key in list(self.w.profile):
   if key.startswith('frame_'):del self.w.profile[key]
  a=Action(request_id='legacy-diagnostic',coordinate_backend='native_hid',diagnostic_uncalibrated=True,steps=[{'op':'move_to','x':440,'y':450}],capture=False)
  r=self.w.submit(a)[0].result(3);self.assertEqual(r['input_state'],'reports_sent');self.assertEqual(self.w.profile['verification'],'needs_verification');self.assertEqual(sum(op=='abs.move' for op,_ in self.bridge.calls),1)
 def test_diagnostic_move_without_profile(self):
  self.w.profile=None
  a=Action(request_id='no-profile',coordinate_backend='native_hid',diagnostic_uncalibrated=True,steps=[{'op':'move_to','x':440,'y':450}],capture=False)
  self.assertEqual(self.w.submit(a)[0].result(3)['input_state'],'reports_sent');self.assertIsNone(self.w.profile)
 def test_diagnostic_click_cannot_bypass_verification(self):
  self.w.profile['verification']='needs_verification'
  a=Action(request_id='diagnostic-click',coordinate_backend='native_hid',diagnostic_uncalibrated=True,steps=[{'op':'key','usage':4},{'op':'click_at','x':248,'y':540}],capture=False)
  r=self.w.submit(a)[0].result(3);self.assertEqual(r['input_state'],'not_started');self.assertFalse(any(op in ('keyboard.tap','abs.click') for op,_ in self.bridge.calls))
 def test_geometry_required_even_without_capture(self):
  self.w.frames.reader=fake_frame(60);a=Action(request_id='geometry',coordinate_backend='native_hid',diagnostic_uncalibrated=True,steps=[{'op':'move_to','x':100,'y':300}],capture=False,timeout_seconds=1)
  self.assertEqual(self.w.submit(a)[0].result(3)['input_state'],'not_started');self.assertFalse(any(op=='abs.move' for op,_ in self.bridge.calls))

 def test_fault_invalidates_profile(self):
  self.w.profile={'verification':'verified'};before=self.w.profile_generation
  self.w.set_fault('device rebooted');self.assertEqual(self.w.profile['verification'],'needs_verification');self.assertGreater(self.w.profile_generation,before);self.assertIsNone(self.w.geometry)

 def test_static_old_frame_is_geometry_only(self):
  from frame_capture import Frame
  jpg=cv2.imencode('.jpg',np.zeros((1080,496,3),np.uint8))[1].tobytes();timestamp=time.time()-60
  async def read(client):return Frame(jpg,timestamp,'test:1')
  self.w.frames.reader=read
  self.w.profile['frame_sequence']=0
  a=Action(request_id='old',coordinate_backend='native_hid',diagnostic_uncalibrated=True,steps=[{'op':'move_to','x':100,'y':300}],capture=False)
  r=self.w.submit(a)[0].result(3);self.assertEqual(r['input_state'],'reports_sent');self.assertTrue(r['geometry_frame']['cached_static']);self.assertEqual(r['geometry_frame']['frame_time'],timestamp)
 def test_changed_source_invalidates_profile(self):
  from frame_capture import Frame
  jpg=cv2.imencode('.jpg',np.zeros((1080,496,3),np.uint8))[1].tobytes()
  async def read(client):return Frame(jpg,time.time(),'other:2')
  self.w.frames.reader=read
  a=Action(request_id='source',coordinate_backend='native_hid',diagnostic_uncalibrated=True,steps=[{'op':'move_to','x':100,'y':300}],capture=False)
  r=self.w.submit(a)[0].result(3);self.assertEqual(r['input_state'],'not_started');self.assertEqual(self.w.profile['verification'],'needs_verification');self.assertFalse(any(op=='abs.move' for op,_ in self.bridge.calls))
 def test_old_final_frame_does_not_repeat_input(self):
  from frame_capture import Frame
  jpg=cv2.imencode('.jpg',np.zeros((1080,496,3),np.uint8))[1].tobytes();timestamp=time.time()-60
  async def read(client):return Frame(jpg,timestamp,'test:1')
  self.w.frames.reader=read
  a=Action(request_id='old-final',coordinate_backend='native_hid',diagnostic_uncalibrated=True,steps=[{'op':'move_to','x':100,'y':300}],capture=True,settle_ms=0,timeout_seconds=1)
  f,_=self.w.submit(a);r=f.result(3);self.assertEqual(r['input_state'],'reports_sent');self.assertIn('capture_error',r);self.assertIs(self.w.submit(a)[0],f);self.assertEqual(sum(op=='abs.move' for op,_ in self.bridge.calls),1)

 def test_frame_sequence_regression_invalidates(self):
  from frame_capture import Frame
  jpg=cv2.imencode('.jpg',np.zeros((1080,496,3),np.uint8))[1].tobytes();calls=[0]
  async def read(client):
   calls[0]+=1;return Frame(jpg,time.time(),'test:'+('2' if calls[0]==1 else '1'))
  self.w.frames.reader=read
  a=Action(request_id='seq1',coordinate_backend='native_hid',diagnostic_uncalibrated=True,steps=[{'op':'move_to','x':100,'y':300}],capture=False);self.assertEqual(self.w.submit(a)[0].result(3)['input_state'],'reports_sent')
  b=Action(request_id='seq2',coordinate_backend='native_hid',diagnostic_uncalibrated=True,steps=[{'op':'move_to','x':101,'y':300}],capture=False);self.assertEqual(self.w.submit(b)[0].result(3)['input_state'],'not_started');self.assertEqual(self.w.profile['verification'],'needs_verification')
 def test_future_timestamp_rejected(self):
  from frame_capture import Frame
  jpg=cv2.imencode('.jpg',np.zeros((1080,496,3),np.uint8))[1].tobytes()
  async def read(client):return Frame(jpg,time.time()+20,'test:2')
  self.w.frames.reader=read
  a=Action(request_id='future',coordinate_backend='native_hid',diagnostic_uncalibrated=True,steps=[{'op':'move_to','x':100,'y':300}],capture=False);self.assertEqual(self.w.submit(a)[0].result(3)['input_state'],'not_started');self.assertEqual(self.w.profile['verification'],'needs_verification')
