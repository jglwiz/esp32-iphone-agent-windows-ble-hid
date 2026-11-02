import json,sys,time,unittest,urllib.request,urllib.error
from pathlib import Path

from iphone_backend.calibration import EventGate
from iphone_backend.calibration_receiver import Receiver
class ReceiverTests(unittest.TestCase):
 def setUp(self):
  self.g=EventGate();self.r=Receiver(self.g,'127.0.0.1',0);self.r.start();self.base='http://127.0.0.1:'+str(self.r.server.server_port)
 def tearDown(self):self.r.close()
 def post(self,path,data):
  req=urllib.request.Request(self.base+path,data=json.dumps(data).encode(),headers={'Content-Type':'application/json'},method='POST')
  return urllib.request.urlopen(req,timeout=2).status
 def test_token_ack_and_event_chain(self):
  g=self.g
  with self.assertRaises(urllib.error.HTTPError):urllib.request.urlopen(self.base+'/?token=wrong')
  self.assertEqual(self.post('/ready',{'token':g.token,'session':'s','width':320,'height':500}),204)
  p=g.arm();self.assertEqual(self.post('/ack',{'token':g.token,'session':'s','probe':p}),204)
  for kind in ('pointerdown','pointerup','click'):
   self.assertEqual(self.post('/event',{'token':g.token,'session':'s','probe':p,'kind':kind,'x':80,'y':100,'width':320,'height':500}),204)
  self.assertEqual(len(g.consume(p)),3)
  with self.assertRaises(urllib.error.HTTPError):self.post('/event',{'token':g.token,'session':'s','probe':p,'kind':'click','x':80,'y':100,'width':320,'height':500})
 def test_does_not_expose_files_or_control(self):
  with self.assertRaises(urllib.error.HTTPError):urllib.request.urlopen(self.base+'/service.py?token='+self.g.token)
