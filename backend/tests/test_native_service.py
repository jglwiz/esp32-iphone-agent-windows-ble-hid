import tempfile, unittest, sys, time, hashlib
from unittest.mock import patch
import cv2, numpy as np
from pathlib import Path

from iphone_backend.service import Action, SerialWorker
from test_service import FakeBridge, fake_frame

class NativeBridge(FakeBridge):
 def request(self,op,**kw):
  super().request(op,**kw)
  return {'state':'ok','connected':True,'boot_id':'b','connection_generation':1,'device_id':'d','supports_absolute_pointer':True,'absolute_descriptor_version':1,'absolute_ready':True}

class NativeServiceTests(unittest.TestCase):
 def setUp(self):
  # These tests cover queue/profile gates. Real temporal guard has separate tests.
  def verified(w,target,profile,cancel,deadline):
   from iphone_backend.native_pointer import map_target
   hid=map_target(*target,profile);w.send(cancel,deadline,"abs.move",x=hid[0],y=hid[1])
   return {"state":"passed","version":1}
  self.guard_patch=patch("iphone_backend.orchestration.verify_pointer_response",side_effect=verified);self.guard_patch.start();self.addCleanup(self.guard_patch.stop)
  self.tmp=tempfile.TemporaryDirectory();self.bridge=NativeBridge();
  async def read(client):
   from iphone_backend.airplay.frames import Frame
   return Frame(cv2.imencode('.jpg',np.zeros((1080,496,3),dtype=np.uint8))[1].tobytes(),time.time(),'test:1')
  self.w=SerialWorker(bridge_factory=lambda **kw:self.bridge,frame_reader=read,runtime=Path(self.tmp.name));self.w.start();self.w.device_id='d';self.w.phone_id='phone';self.w.geometry=(496,1080,'portrait');self.w.profile={'schema_version':1,'profile_id':'p','device_id':'d','phone_id':'phone','descriptor_version':1,'geometry':[496,1080,'portrait'],'coordinate_space':'full_screenshot_pixels','verification':'verified','verified_region':[186,455,310,651],'axes':[{'slope':1.,'offset':0.},{'slope':1.,'offset':0.}],'frame_source_id':'test','frame_sequence':0,'frame_timestamp':0.,'frame_sha256':'0'*64}
 def tearDown(self):self.w.stop();self.tmp.cleanup()
 def test_single_move_and_dedup(self):
  a=Action(request_id='native',coordinate_backend='native_hid',diagnostic_uncalibrated=True,steps=[{'op':'move_to','x':100,'y':300}],capture=False)
  f,_=self.w.submit(a);r=f.result(3);self.assertEqual(r['absolute'][0]['status'],'report_submitted');self.assertIs(f,self.w.submit(a)[0]);self.assertEqual(sum(x[0]=='abs.move' for x in self.bridge.calls),1)
 def test_manual_visual_move_cannot_click(self):
  with self.assertRaises(ValueError):
   Action(request_id='manual-click',coordinate_backend='native_hid',diagnostic_uncalibrated=True,manual_visual=True,steps=[{'op':'click_at','x':100,'y':300}])
  a=Action(request_id='manual-move',coordinate_backend='native_hid',diagnostic_uncalibrated=True,manual_visual=True,steps=[{'op':'move_to','x':100,'y':300}],settle_ms=0)
  with patch('iphone_backend.orchestration.verify_pointer_response',side_effect=AssertionError('Automatic guard must not run')):
   r=self.w.submit(a)[0].result(3)
  self.assertEqual(r['input_state'],'reports_sent');self.assertEqual(r['absolute'][0]['pointer_check']['state'],'manual_review_required')
  self.assertFalse(any(op=='abs.click' for op,_ in self.bridge.calls))
  bad=Action(request_id='bad-reviewed-click',coordinate_backend='native_hid',diagnostic_uncalibrated=True,manual_visual=True,reviewed_action_id='unknown',steps=[{'op':'click_at','x':100,'y':300}],settle_ms=0)
  self.assertEqual(self.w.submit(bad)[0].result(3)['input_state'],'not_started')
  good=Action(request_id='reviewed-click',coordinate_backend='native_hid',diagnostic_uncalibrated=True,manual_visual=True,reviewed_action_id='manual-move',steps=[{'op':'click_at','x':100,'y':300}],settle_ms=0)
  self.assertEqual(self.w.submit(good)[0].result(3)['input_state'],'reports_sent')
  self.assertEqual(sum(op=='abs.click' for op,_ in self.bridge.calls),1)
 def test_entire_batch_rejected_before_input(self):
  self.w.profile['axes'][0]['offset']=20.
  a=Action(request_id='unsafe',coordinate_backend='native_hid',steps=[{'op':'key','usage':4},{'op':'click_at','x':0,'y':300}],capture=False)
  r=self.w.submit(a)[0].result(3);self.assertEqual(r['input_state'],'not_started');self.assertFalse(any(op in ('keyboard.tap','abs.click') for op,_ in self.bridge.calls))
 def test_click_outside_measured_region(self):
  a=Action(request_id='outside-region',coordinate_backend='native_hid',steps=[{'op':'click_at','x':414,'y':997}],capture=False)
  r=self.w.submit(a)[0].result(3);self.assertEqual(r['input_state'],'reports_sent');self.assertEqual(sum(op=='abs.click' for op,_ in self.bridge.calls),1)
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
  from iphone_backend.airplay.frames import Frame
  jpg=cv2.imencode('.jpg',np.zeros((1080,496,3),np.uint8))[1].tobytes();timestamp=time.time()-60
  async def read(client):return Frame(jpg,timestamp,'test:1')
  self.w.frames.reader=read
  self.w.profile['frame_sequence']=0
  a=Action(request_id='old',coordinate_backend='native_hid',steps=[{'op':'move_to','x':248,'y':540}],capture=False)
  r=self.w.submit(a)[0].result(3);self.assertEqual(r['input_state'],'reports_sent');self.assertTrue(r['geometry_frame']['cached_static']);self.assertEqual(r['geometry_frame']['frame_time'],timestamp)
 def test_changed_source_invalidates_profile(self):
  from iphone_backend.airplay.frames import Frame
  jpg=cv2.imencode('.jpg',np.zeros((1080,496,3),np.uint8))[1].tobytes()
  async def read(client):return Frame(jpg,time.time(),'other:2')
  self.w.frames.reader=read
  a=Action(request_id='source',coordinate_backend='native_hid',steps=[{'op':'move_to','x':100,'y':300}],capture=False)
  r=self.w.submit(a)[0].result(3);self.assertEqual(r['input_state'],'not_started');self.assertEqual(self.w.profile['verification'],'needs_verification');self.assertFalse(any(op=='abs.move' for op,_ in self.bridge.calls))
 def test_old_final_frame_does_not_repeat_input(self):
  from iphone_backend.airplay.frames import Frame
  jpg=cv2.imencode('.jpg',np.zeros((1080,496,3),np.uint8))[1].tobytes();timestamp=time.time()-60
  async def read(client):return Frame(jpg,timestamp,'test:1')
  self.w.frames.reader=read
  a=Action(request_id='old-final',coordinate_backend='native_hid',steps=[{'op':'move_to','x':248,'y':540}],capture=True,settle_ms=0,timeout_seconds=1)
  f,_=self.w.submit(a);r=f.result(3);self.assertEqual(r['input_state'],'reports_sent');self.assertIn('capture_error',r);self.assertIs(self.w.submit(a)[0],f);self.assertEqual(sum(op=='abs.move' for op,_ in self.bridge.calls),1)

 def test_frame_sequence_regression_invalidates(self):
  from iphone_backend.airplay.frames import Frame
  jpg=cv2.imencode('.jpg',np.zeros((1080,496,3),np.uint8))[1].tobytes();calls=[0]
  async def read(client):
   calls[0]+=1;return Frame(jpg,time.time(),'test:'+('2' if calls[0]==1 else '1'))
  self.w.frames.reader=read
  a=Action(request_id='seq1',coordinate_backend='native_hid',diagnostic_uncalibrated=True,steps=[{'op':'move_to','x':100,'y':300}],capture=False);self.assertEqual(self.w.submit(a)[0].result(3)['input_state'],'reports_sent')
  b=Action(request_id='seq2',coordinate_backend='native_hid',diagnostic_uncalibrated=True,steps=[{'op':'move_to','x':101,'y':300}],capture=False);self.assertEqual(self.w.submit(b)[0].result(3)['input_state'],'not_started');self.assertEqual(self.w.profile['verification'],'needs_verification')
 def test_future_timestamp_rejected(self):
  from iphone_backend.airplay.frames import Frame
  jpg=cv2.imencode('.jpg',np.zeros((1080,496,3),np.uint8))[1].tobytes()
  async def read(client):return Frame(jpg,time.time()+20,'test:2')
  self.w.frames.reader=read
  a=Action(request_id='future',coordinate_backend='native_hid',diagnostic_uncalibrated=True,steps=[{'op':'move_to','x':100,'y':300}],capture=False);self.assertEqual(self.w.submit(a)[0].result(3)['input_state'],'not_started');self.assertEqual(self.w.profile['verification'],'needs_verification')

 def test_diagnostic_new_source_ignores_saved_mapping(self):
  import copy
  self.w.profile['verification']='needs_verification'
  self.w.profile['frame_source_id']='obsolete'
  self.w.profile['axes'][0]['offset']=40
  self.w.geometry_last=(99999,time.time(), 'old')
  self.w.geometry_source='obsolete'
  saved=copy.deepcopy(self.w.profile)
  a=Action(request_id='fresh-diagnostic',coordinate_backend='native_hid',diagnostic_uncalibrated=True,steps=[{'op':'move_to','x':100,'y':300}],capture=False)
  r=self.w.submit(a)[0].result(3)
  self.assertEqual(r['input_state'],'reports_sent')
  self.assertEqual(self.w.profile,saved)
  self.assertEqual(r['absolute'][0]['hid'],[round(100/495*32767),round(300/1079*32767)])
  b=Action(request_id='still-no-click',coordinate_backend='native_hid',steps=[{'op':'click_at','x':248,'y':540}],capture=False)
  self.assertEqual(self.w.submit(b)[0].result(3)['input_state'],'not_started')
  self.assertFalse(any(op=='abs.click' for op,_ in self.bridge.calls))

 def test_diagnostic_rejects_mixed_buttons_before_any_input(self):
  a=Action(request_id='mixed',coordinate_backend='native_hid',diagnostic_uncalibrated=True,steps=[{'op':'move_to','x':100,'y':300},{'op':'click'}],capture=False)
  self.assertEqual(self.w.submit(a)[0].result(3)['input_state'],'not_started')
  self.assertFalse(any(op in ('abs.move','mouse.click') for op,_ in self.bridge.calls))

 def test_diagnostic_rejects_old_frame_with_saved_profile(self):
  from iphone_backend.airplay.frames import Frame
  jpg=cv2.imencode('.jpg',np.zeros((1080,496,3),np.uint8))[1].tobytes()
  async def read(client): return Frame(jpg,time.time()-10,'test:3')
  self.w.frames.reader=read
  a=Action(request_id='stale-diagnostic',coordinate_backend='native_hid',diagnostic_uncalibrated=True,steps=[{'op':'move_to','x':100,'y':300}],capture=False)
  self.assertEqual(self.w.submit(a)[0].result(3)['input_state'],'not_started')
  self.assertFalse(any(op=='abs.move' for op,_ in self.bridge.calls))

 def test_guard_failure_blocks_click_and_invalidates_profile(self):
  def fail(w,*args):
   w.invalidate_geometry()
   raise RuntimeError('pointer response mismatch; evidence=saved')
  with patch('iphone_backend.orchestration.verify_pointer_response',side_effect=fail):
   a=Action(request_id='guard-failure',coordinate_backend='native_hid',steps=[{'op':'click_at','x':248,'y':540}],capture=False)
   r=self.w.submit(a)[0].result(3)
  self.assertIn('pointer response mismatch',r['error'])
  self.assertTrue(r['released'])
  self.assertEqual(self.w.profile['verification'],'needs_verification')
  self.assertFalse(any(op=='abs.click' for op,_ in self.bridge.calls))

 def test_verified_move_returns_evidence_without_waiting_for_static_frame(self):
  import base64
  path=Path(self.tmp.name)/'checked.jpg'
  path.write_bytes(cv2.imencode('.jpg',np.zeros((1080,496,3),np.uint8))[1].tobytes())
  frame={'path':str(path),'frame_id':'test:2','frame_time':time.time(),'after_action':None}
  with patch('iphone_backend.orchestration.verify_pointer_response',return_value={'state':'passed','version':1,'frame':frame}):
   a=Action(request_id='response-frame',coordinate_backend='native_hid',steps=[{'op':'move_to','x':248,'y':540}],capture=True)
   r=self.w.submit(a)[0].result(3)
  self.assertTrue(r['frame']['pointer_response_verified'])
  self.assertFalse(r['frame']['after_action'])
  self.assertNotIn('capture_error',r)
  self.assertNotIn('settled_at',r)

 def test_startup_diagnostics_are_read_once_by_serial_owner(self):
  self.assertEqual(sum(op=='diag' for op,_ in self.bridge.calls),1)
  self.assertIn('captured_at',self.w.hid_diagnostics)
  self.assertIsNone(self.w.hid_diagnostics['report_map_sha256'])  # fake has no real map
