import sys,unittest,tempfile
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'pc'))
from calibration import EventGate,fit_samples,validate_holdout,atomic_save,load_unverified

class CalibrationTests(unittest.TestCase):
 def test_event_gate_rejects_wrong_session_and_replay(self):
  g=EventGate();g.ready(g.token,'session',320,500);p=g.arm();g.ack(g.token,'session',p)
  with self.assertRaises(ValueError):g.event(g.token,'wrong',p,'pointerdown',2,3,320,500)
  for k in ('pointerdown','pointerup','click'):g.event(g.token,'session',p,k,2,3,320,500)
  self.assertEqual(len(g.consume(p)),3)
  with self.assertRaises(ValueError):g.consume(p)
 def test_out_of_order_invalidates(self):
  g=EventGate();g.ready(g.token,'s',320,500);p=g.arm();g.ack(g.token,'s',p)
  with self.assertRaises(ValueError):g.event(g.token,'s',p,'click',2,3,320,500)
  with self.assertRaises(ValueError):g.consume(p)
 def test_fit_and_holdout(self):
  points=[(100,200),(400,200),(250,500),(100,800),(400,800),(250,500)]
  samples=[{'command':p,'actual':(p[0]+12,p[1]-10)} for p in points]
  mode,axes,_=fit_samples(samples);self.assertEqual(mode,'inverse_affine');self.assertAlmostEqual(axes[0]['offset'],12)
  validate_holdout([{'target':[1,2],'actual':[1,2]} for _ in range(5)])
  with self.assertRaises(ValueError):validate_holdout([{'target':[1,2],'actual':[20,2]} for _ in range(5)])
 def test_persistence_requires_reverification(self):
  with tempfile.TemporaryDirectory() as t:
   p=Path(t)/'p.json';atomic_save(p,{'verification':'verified'});self.assertEqual(load_unverified(p)['verification'],'needs_verification')

class CalibrationInputTests(unittest.TestCase):
 def test_invalid_holdout(self):
  for value in (float('nan'), float('inf'), True):
   with self.assertRaises(ValueError):validate_holdout([{'target':[1,2],'actual':[value,2]} for _ in range(5)])
  with self.assertRaises(ValueError):validate_holdout([{'target':[1],'actual':[1,2]} for _ in range(5)])
 def test_viewport_change_invalidates(self):
  g=EventGate();g.ready(g.token,'s',320,500);g.arm()
  with self.assertRaises(ValueError):g.ready(g.token,'s',321,500)
  self.assertEqual(g.invalid,'viewport_changed')
