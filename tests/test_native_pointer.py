import sys, unittest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'pc'))
from native_pointer import map_target, validate_profile

class NativePointerTests(unittest.TestCase):
 def profile(self):
  return {'schema_version':1,'profile_id':'p','device_id':'d','phone_id':'p','descriptor_version':1,'geometry':[496,1080,'portrait'],'coordinate_space':'full_screenshot_pixels','verification':'verified','verified_region':[50,250,440,850],'axes':[{'slope':1.,'offset':12.},{'slope':1.,'offset':-10.}]}
 def test_boundaries_and_no_clamping(self):
  self.assertEqual(map_target(0,0),[0,0]);self.assertEqual(map_target(495,1079),[32767,32767])
  with self.assertRaises(ValueError):map_target(0,0,self.profile())
  with self.assertRaises(ValueError):map_target(495,1000,self.profile(),click=True)
 def test_inverse_and_identity_binding(self):
  p=self.profile();validate_profile(p,device_id='d',phone_id='p',descriptor_version=1,geometry=(496,1080,'portrait'))
  self.assertEqual(map_target(112,290,p,click=True),map_target(100,300))
  with self.assertRaises(ValueError):validate_profile(p,device_id='other',phone_id='p',descriptor_version=1,geometry=(496,1080,'portrait'))
  p['verification']='needs_verification'
  with self.assertRaises(ValueError):validate_profile(p,device_id='d',phone_id='p',descriptor_version=1,geometry=(496,1080,'portrait'))
