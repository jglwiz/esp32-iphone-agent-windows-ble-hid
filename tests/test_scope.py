import json,sys,tempfile,time,unittest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'pc'))
from scope import ScopeJob,REGION
from test_calibration_job import FakeWorker

class Worker(FakeWorker):
 def __init__(self,p):
  super().__init__(p);self.instance_id='test-instance';self.device_id='dev';self.phone_id='phone';self.seq=0
  self.profile={'schema_version':1,'profile_id':'original','device_id':'dev','phone_id':'phone','descriptor_version':1,'geometry':[496,1080,'portrait'],'coordinate_space':'full_screenshot_pixels','verification':'verified','verified_region':[186,455,310,651],'axes':[{'slope':1.,'offset':0.},{'slope':1.,'offset':0.}],'mapping':'identity'}
 def accept_geometry(self,frame,profile):pass
 def capture(self,after,c,d):
  state,frame=super().capture(after,c,d);self.seq+=1;frame.update(frame_id=str(self.seq),after_action=after is not None);return state,frame
 def pause(self,seconds,c,d):self.check(c,d)
 def send(self,c,d,op,**kw):
  self.check(c,d);self.calls.append(op)
  if op=='status':return {'device_id':'dev','supports_absolute_pointer':True,'absolute_descriptor_version':1,'absolute_ready':True,'boot_id':'b','connection_generation':1}
  return {'state':'ok'}
class ScopeTests(unittest.TestCase):
 def review(self,job):return {'reviewer':'independent reviewer','observations':{s['sample_id']:{'center':s['target'],'uncertainty_px':3} for s in job.result['samples']}}
 def test_fixed_scope_and_approval(self):
  with tempfile.TemporaryDirectory() as tmp:
   w=Worker(Path(tmp));j=ScopeJob(w,'id');j.run();self.assertEqual(j.result['state'],'awaiting_review');self.assertEqual(len(j.result['samples']),31)
   j.approve(self.review(j));self.assertEqual(j.result['state'],'passed');self.assertEqual(w.profile['verified_region'],REGION);self.assertEqual(w.calls.count('abs.move'),31)
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
 def test_error_plus_uncertainty_is_bound(self):
  with tempfile.TemporaryDirectory() as tmp:
   w=Worker(Path(tmp));j=ScopeJob(w,'id');j.run();review=self.review(j);item=review['observations']['sample-01'];item['center']=[item['center'][0]+5.1,item['center'][1]];item['uncertainty_px']=3;j.approve(review);self.assertEqual(j.result['state'],'failed');self.assertEqual(w.profile['profile_id'],'original')
