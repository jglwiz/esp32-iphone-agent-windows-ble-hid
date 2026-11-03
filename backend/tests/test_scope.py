import json,sys,tempfile,time,unittest
from pathlib import Path

from iphone_backend.scope import ScopeJob,REGION,template
from unittest.mock import patch
import base64
from test_calibration_job import FakeWorker

class Worker(FakeWorker):
 def __init__(self,p):
  super().__init__(p);self.instance_id='test-instance';self.device_id='dev';self.phone_id='phone';self.seq=0
  self.profile={'schema_version':1,'profile_id':'original','device_id':'dev','phone_id':'phone','descriptor_version':1,'geometry':[496,1080,'portrait'],'coordinate_space':'full_screenshot_pixels','verification':'verified','verified_region':[186,455,310,651],'axes':[{'slope':1.,'offset':0.},{'slope':1.,'offset':0.}],'mapping':'identity'}
 def accept_geometry(self,frame,profile):pass
 def capture(self,after,c,d,**kwargs):
  state,frame=super().capture(after,c,d);self.seq+=1;frame.update(frame_id=str(self.seq),after_action=after is not None);return state,frame
 def pause(self,seconds,c,d):self.check(c,d)
 def send(self,c,d,op,**kw):
  self.check(c,d);self.calls.append(op)
  if op=='status':return {'device_id':'dev','supports_absolute_pointer':True,'absolute_descriptor_version':1,'absolute_ready':True,'boot_id':'b','connection_generation':1}
  return {'state':'ok'}
class ScopeTests(unittest.TestCase):
 def setUp(self):
  def check(w,target,profile,c,d,**kw):
   w.send(c,d,'abs.move');state,frame=w.capture(None,c,d)
   path=w.runtime/('guard-'+frame['frame_id']+'.jpg');path.write_bytes(base64.b64decode(frame.pop('jpeg_base64')));frame['path']=str(path)
   return {'state':'passed','frame':frame}
  mock=patch('iphone_backend.scope.verify_pointer_response',side_effect=check);mock.start();self.addCleanup(mock.stop)

 def test_usable_area_is_explicit_and_repeated(self):
  points=template()
  self.assertEqual(REGION,[24,60,471,1010]);self.assertEqual(len(points),36)
  for x,y in points:self.assertTrue((24<=x<=471 and 60<=y<=1010) or (x,y) in ((248,0),(0,540),(495,540),(248,1079)))
  for x in (24,248,471):
   for y in (60,540,1010):self.assertGreaterEqual(points.count((x,y)),3)
 def review(self,job):return {'reviewer':'independent reviewer','observations':{s['sample_id']:{'center':s['target'],'uncertainty_px':3} for s in job.result['samples']}}
 def test_fixed_scope_and_approval(self):
  with tempfile.TemporaryDirectory() as tmp:
   w=Worker(Path(tmp));j=ScopeJob(w,'id');j.run();self.assertEqual(j.result['state'],'awaiting_review');self.assertEqual(len(j.result['samples']),len(template()))
   j.approve(self.review(j));self.assertEqual(j.result['state'],'passed');self.assertEqual(w.profile['verified_region'],REGION);self.assertEqual(w.calls.count('abs.move'),len(template()))
 def test_tampered_image_rejected(self):
  with tempfile.TemporaryDirectory() as tmp:
   w=Worker(Path(tmp));j=ScopeJob(w,'id');j.run();Path(j.result['samples'][0]['image']).write_bytes(b'changed');j.approve(self.review(j));self.assertEqual(j.result['state'],'failed');self.assertEqual(w.profile['profile_id'],'original')
 def test_uncertainty_and_generation_rejected(self):
  with tempfile.TemporaryDirectory() as tmp:
   w=Worker(Path(tmp));j=ScopeJob(w,'id');j.run();review=self.review(j);review['observations']['sample-01']['uncertainty_px']=9;j.approve(review);self.assertEqual(j.result['state'],'failed');self.assertEqual(w.profile['profile_id'],'original')
  with tempfile.TemporaryDirectory() as tmp:
   w=Worker(Path(tmp));j=ScopeJob(w,'id');j.run();w.profile_generation+=1;j.approve(self.review(j));self.assertEqual(j.result['state'],'failed');self.assertEqual(w.profile['profile_id'],'original')

 def test_expired_review_cannot_publish(self):
  with tempfile.TemporaryDirectory() as tmp:
   w=Worker(Path(tmp));j=ScopeJob(w,'id');j.run();j.review_deadline=time.monotonic()-1;j.approve(self.review(j));self.assertEqual(j.result['state'],'failed');self.assertEqual(w.profile['profile_id'],'original')
 def test_review_budget_is_separate_from_completed_sampling(self):
  with tempfile.TemporaryDirectory() as tmp:
   w=Worker(Path(tmp));j=ScopeJob(w,'id');j.run();j.deadline=time.perf_counter()-1
   j.approve(self.review(j));self.assertEqual(j.result['state'],'passed')

 def test_error_plus_uncertainty_is_bound(self):
  with tempfile.TemporaryDirectory() as tmp:
   w=Worker(Path(tmp));j=ScopeJob(w,'id');j.run();review=self.review(j);item=review['observations']['sample-01'];item['center']=[item['center'][0]+5.1,item['center'][1]];item['uncertainty_px']=3;j.approve(review);self.assertEqual(j.result['state'],'failed');self.assertEqual(w.profile['profile_id'],'original')

 def test_guard_failure_stops_without_fitting_or_publication(self):
  with tempfile.TemporaryDirectory() as tmp:
   w=Worker(Path(tmp));j=ScopeJob(w,'id')
   with patch('iphone_backend.scope.verify_pointer_response',side_effect=RuntimeError('direction mismatch; evidence saved')) as call:
    j.run()
   self.assertEqual(j.result['state'],'failed');self.assertEqual(call.call_count,1)
   self.assertEqual(w.profile['profile_id'],'original');self.assertFalse(j.result['samples'])
 def test_fixed_mapping_can_be_reverified_after_restart(self):
  with tempfile.TemporaryDirectory() as tmp:
   w=Worker(Path(tmp));w.profile['verification']='needs_verification';axes=json.loads(json.dumps(w.profile['axes']))
   j=ScopeJob(w,'id');j.run();j.approve(self.review(j))
   self.assertEqual(j.result['state'],'passed');self.assertEqual(w.profile['axes'],axes)
   self.assertEqual(w.profile['verification'],'verified')

 def test_scene_change_requires_explicit_review(self):
  with tempfile.TemporaryDirectory() as tmp:
   w=Worker(Path(tmp));j=ScopeJob(w,'id');j.run()
   j.result['samples'][0]['pointer_check']['scene_changes']=[{'frame_id':'f'}]
   j.approve(self.review(j));self.assertEqual(j.result['state'],'failed')
   self.assertEqual(w.profile['profile_id'],'original')
  with tempfile.TemporaryDirectory() as tmp:
   w=Worker(Path(tmp));j=ScopeJob(w,'id');j.run()
   j.result['samples'][0]['pointer_check']['scene_changes']=[{'frame_id':'f'}]
   review=self.review(j);review['observations']['sample-01']['scene_reviewed']=True
   j.approve(review);self.assertEqual(j.result['state'],'passed')
