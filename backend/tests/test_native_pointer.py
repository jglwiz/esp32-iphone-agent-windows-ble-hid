import sys, unittest
from pathlib import Path

from iphone_backend.native_pointer import map_target, validate_profile

class NativePointerTests(unittest.TestCase):
 def profile(self):
  return {'schema_version':1,'profile_id':'p','device_id':'d','phone_id':'p','descriptor_version':1,'geometry':[496,1080,'portrait'],'coordinate_space':'full_screenshot_pixels','verification':'verified','verified_region':[50,250,440,850],'axes':[{'slope':1.,'offset':12.},{'slope':1.,'offset':-10.}]}
 def test_boundaries_and_no_clamping(self):
  self.assertEqual(map_target(0,0),[0,0]);self.assertEqual(map_target(495,1079),[32767,32767])
  with self.assertRaises(ValueError):map_target(0,0,self.profile())
  self.assertEqual(map_target(495,1000,self.profile(),click=True),map_target(483,1010))
  with self.assertRaises(ValueError):map_target(496,1000,self.profile(),click=True)
  with self.assertRaises(ValueError):map_target(495,1079,self.profile(),click=True)
 def test_inverse_and_identity_binding(self):
  p=self.profile();validate_profile(p,device_id='d',phone_id='p',descriptor_version=1,geometry=(496,1080,'portrait'))
  self.assertEqual(map_target(112,290,p,click=True),map_target(100,300))
  with self.assertRaises(ValueError):validate_profile(p,device_id='other',phone_id='p',descriptor_version=1,geometry=(496,1080,'portrait'))
  p['verification']='needs_verification'
  with self.assertRaises(ValueError):validate_profile(p,device_id='d',phone_id='p',descriptor_version=1,geometry=(496,1080,'portrait'))

 def test_negative_axes_rejected(self):
  for slopes in ((-1.,-1.),(-1.,1.),(1.,-1.)):
   p=self.profile();p['axes']=[{'slope':v,'offset':0. if v>0 else float((495,1079)[i])} for i,v in enumerate(slopes)]
   with self.assertRaises(ValueError):validate_profile(p,device_id='d',phone_id='p',descriptor_version=1,geometry=(496,1080,'portrait'))
   for click in (False,True):
    with self.assertRaises(ValueError):map_target(248,540,p,click=click)

 def test_direct_mapping_rejects_invalid_axes(self):
  for slope in (0.,float('nan'),float('inf'),True):
   p=self.profile();p['axes'][0]['slope']=slope
   for click in (False,True):
    with self.assertRaises(ValueError):map_target(248,540,p,click=click)

 def test_move_and_click_share_positive_mapping(self):
  p=self.profile()
  self.assertEqual(map_target(248,540,p),map_target(248,540,p,click=True))
