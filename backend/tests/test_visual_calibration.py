import copy, hashlib, json, sys, tempfile, time, unittest
from pathlib import Path
from unittest.mock import patch
import cv2
import numpy as np

from fastapi.testclient import TestClient
from iphone_backend.service import SerialWorker,create_app
from iphone_backend.visual_calibration import evaluate
from test_native_service import NativeBridge
from iphone_backend.airplay.frames import Frame

class EncryptedBridge(NativeBridge):
 def request(self,op,**kw):
  return {**super().request(op,**kw),'encrypted':True}

class VisualCalibrationTests(unittest.TestCase):
 def setUp(self):
  self.guard_patch=patch("iphone_backend.orchestration.verify_pointer_response",return_value={"state":"passed","version":1});self.guard_patch.start();self.addCleanup(self.guard_patch.stop)
 def test_signed_fit_and_uncertainty(self):
  commands=[[150,300],[350,300],[350,700],[150,700],[250,500],[350,700]]
  training=[{'command':p,'actual':p,'uncertainty_px':3.} for p in commands]
  holdout=[{'target':p,'command':p,'actual':p,'uncertainty_px':3.} for p in [[200,450],[300,450],[200,650],[300,650],[250,550]]]
  _,axes,region,_=evaluate(training,holdout)
  self.assertAlmostEqual(axes[0]['slope'],1.)
  negative=copy.deepcopy(training)
  for row in negative:row['actual']=[495-row['command'][0],1079-row['command'][1]]
  with self.assertRaisesRegex(ValueError,'Direction anomaly'):evaluate(negative,holdout)
  self.assertEqual(region,[200,450,300,650])
  bad=copy.deepcopy(holdout);bad[0]['actual']=[206,450]
  with self.assertRaises(ValueError):evaluate(training,bad)
  bad=copy.deepcopy(holdout);bad[-1]['target']=[260,550]
  with self.assertRaises(ValueError):evaluate(training,bad)

 def test_fullscreen_grid(self):
  points=[[x,y] for y in (60,535,1010) for x in (24,248,471)]
  training=[{'command':p,'actual':p,'uncertainty_px':3.} for p in points]
  holdout=[{'target':p,'command':p,'actual':p,'uncertainty_px':3.} for p in [[40,80],[456,80],[40,980],[456,980],[248,530]]]
  self.assertEqual(evaluate(training,holdout)[2],[40,80,456,980])
  bad=copy.deepcopy(training);bad[-1]=bad[0]
  with self.assertRaises(ValueError):evaluate(bad,holdout)
  bad=copy.deepcopy(holdout);bad[0]['actual']=[50,80]
  with self.assertRaises(ValueError):evaluate(training,bad)

 def test_review_evidence_and_native_click(self):
  with tempfile.TemporaryDirectory() as tmp:
   bridge=EncryptedBridge(); seq=[0]
   jpg=cv2.imencode('.jpg',np.zeros((1080,496,3),np.uint8))[1].tobytes()
   async def reader(client):
    seq[0]+=1
    return Frame(jpg,time.time(),'visual:'+str(seq[0]))
   worker=SerialWorker(bridge_factory=lambda **kw:bridge,frame_reader=reader,runtime=Path(tmp))
   worker.device_id='d';worker.phone_id='phone'
   with TestClient(create_app(worker)) as client:
    commands=[[x,y] for y in (60,535,1010) for x in (24,248,471)]
    targets=[[40,80],[456,80],[40,980],[456,980],[248,530]]
    review={'request_id':'review','reviewer':'test-reviewer','scene':'home','training':[],'holdout':[]}
    for i,p in enumerate(commands+targets):
     action_id='sample-'+str(i)
     response=client.post('/action',json={'request_id':action_id,'coordinate_backend':'native_hid','diagnostic_uncalibrated':True,'steps':[{'op':'move_to','x':p[0],'y':p[1]}],'capture':True,'settle_ms':0})
     result=response.json();self.assertEqual(result['input_state'],'reports_sent',result)
     observation={'action_id':action_id,'center':[float(p[0]),float(p[1])],'uncertainty_px':3.,'image_sha256':hashlib.sha256(jpg).hexdigest()}
     if i>=len(commands): observation['target']=targets[i-len(commands)]
     review['training' if i<len(commands) else 'holdout'].append(observation)
    def submit(body):
     response=client.post('/visual-calibrations/review',json=body)
     self.assertEqual(response.status_code,202,response.text)
     for _ in range(150):
      state=client.get('/visual-calibrations/'+body['request_id']).json()
      if state['state'] in ('passed','failed'): return state
      time.sleep(.01)
     self.fail('review timeout')
    bad=copy.deepcopy(review);bad['request_id']='tamper';bad['training'][0]['image_sha256']='0'*64
    self.assertEqual(submit(bad)['state'],'failed');self.assertIsNone(worker.profile)
    bad=copy.deepcopy(review);bad['request_id']='duplicate';bad['training'][1]=bad['training'][0]
    self.assertEqual(submit(bad)['state'],'failed')
    bad=copy.deepcopy(review);bad['request_id']='stale-generation'
    worker.profile_generation+=1
    self.assertEqual(submit(bad)['state'],'failed');worker.profile_generation-=1
    state=submit(review);self.assertEqual(state['state'],'passed',state)
    self.assertFalse(state['profile']['click_delivery_verified'])
    self.assertEqual(client.post('/visual-calibrations/review',json=review).json()['job_id'],state['job_id'])
    conflict=copy.deepcopy(review);conflict['reviewer']='other'
    self.assertEqual(client.post('/visual-calibrations/review',json=conflict).status_code,409)
    response=client.post('/action',json={'request_id':'native-click','coordinate_backend':'native_hid','steps':[{'op':'click_at','x':250,'y':550}],'capture':False}).json()
    self.assertEqual(response['input_state'],'reports_sent',response)
    sent=[kw for op,kw in bridge.calls if op=='abs.click']
    self.assertEqual(len(sent),1)
    self.assertEqual([sent[0]['x'],sent[0]['y']],[round(250/495*32767),round(550/1079*32767)])
    response=client.post('/action',json={'request_id':'outside','coordinate_backend':'native_hid','steps':[{'op':'click_at','x':400,'y':550}],'capture':False}).json()
    self.assertEqual(response['input_state'],'reports_sent')
    self.assertEqual(sum(op=='abs.click' for op,_ in bridge.calls),2)

if __name__=='__main__':unittest.main()
