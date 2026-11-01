import sys,tempfile,threading,time,unittest
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'pc'))
from fastapi.testclient import TestClient
from service import SerialWorker,create_app
from test_service import FakeBridge,fake_frame

class ScopeHttpTests(unittest.TestCase):
 def test_exclusivity_replay_conflict_and_expiry(self):
  with tempfile.TemporaryDirectory() as tmp:
   worker=SerialWorker(bridge_factory=lambda **kw:FakeBridge(),frame_reader=fake_frame(),runtime=Path(tmp));entered=threading.Event();proceed=threading.Event();review_entered=threading.Event();finish=threading.Event();calls=[]
   def sample(job):
    entered.set();proceed.wait(3);job.review_deadline=time.monotonic()+900;job.result['state']='awaiting_review';job.owner.calibration_reserved=None
   def approve(command):
    calls.append(1);review_entered.set();finish.wait(3);command.job.result['state']='passed';command.job.owner.calibration_reserved=None
   with patch('scope.ScopeJob.run',sample),patch('scope.ScopeApproval.run',approve),TestClient(create_app(worker)) as client:
    worker.profile={'profile_id':'p','verification':'verified'}
    data={'request_id':'scope-test'};r=client.post('/scope-validations',json=data);self.assertEqual(r.status_code,202,r.text);jid=r.json()['job_id'];self.assertTrue(entered.wait(2))
    self.assertEqual(client.post('/scope-validations',json=data).json()['job_id'],jid)
    self.assertEqual(client.post('/action',json={'request_id':'blocked','steps':[{'op':'click'}],'capture':False}).status_code,409)
    self.assertEqual(client.post('/scope-validations',json={'request_id':'bad','region':[0,0,495,1079]}).status_code,422)
    proceed.set()
    for _ in range(50):
     if worker.scope_jobs[jid].result['state']=='awaiting_review':break
     time.sleep(.02)
    review={'reviewer':'reviewer','observations':{}}
    self.assertEqual(client.post('/scope-validations/'+jid+'/approve',json=review).status_code,202);self.assertTrue(review_entered.wait(2))
    self.assertEqual(client.post('/scope-validations/'+jid+'/approve',json=review).status_code,202)
    self.assertEqual(client.post('/scope-validations/'+jid+'/approve',json={'reviewer':'other','observations':{}}).status_code,409)
    finish.set();time.sleep(.1);self.assertEqual(len(calls),1)
