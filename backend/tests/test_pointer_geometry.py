import sys, unittest
from pathlib import Path
import cv2
import numpy as np

from iphone_backend.pointer_guard import response
from iphone_backend.pointer_geometry import arc_center, clipped_disk_center


class GeometryTests(unittest.TestCase):
    def pair(self, actual=(270,600)):
        background=np.zeros((1080,496,3),np.uint8)
        background[580:650,268:340]=63
        before=background.copy();cv2.circle(before,(350,540),14,(63,63,63),-1)
        after=background.copy();cv2.circle(after,actual,14,(63,63,63),-1)
        return before,after

    def test_occluded_disk_is_measured_without_target_prior(self):
        before,after=self.pair()
        result=response(before,after,(350,540),(270,600))
        self.assertTrue(result['valid'],result)
        self.assertLess(np.linalg.norm(np.array(result['center'])-(270,600)),2)
        wrong=response(before,after,(350,540),(290,600))
        self.assertFalse(wrong['valid'],wrong)

    def test_scene_change_still_rejected(self):
        before,after=self.pair();after[800:900,40:200]=255
        self.assertFalse(response(before,after,(350,540),(270,600))['valid'])

    def test_small_fragment_cannot_establish_center(self):
        mask=np.zeros((1080,496),np.uint8);mask[590:596,258:263]=1
        self.assertIsNone(arc_center(mask,14))

    def test_saved_camera_overlap(self):
        root=Path(__file__).resolve().parents[1]
        folder=root/'tests/fixtures/camera-overlap'
        if not folder.exists(): self.skipTest('Local private evidence not distributed')
        before=cv2.imread(str(folder/'003.jpg'));after=cv2.imread(str(folder/'012.jpg'))
        result=response(before,after,(350,540),(270,600))
        print('saved_overlap',result)
        self.assertTrue(result['valid'],result)
        self.assertLess(np.linalg.norm(np.array(result['center'])-(270,600)),3)
        self.assertFalse(response(before,after,(350,540),(280,600))['valid'])
        self.assertFalse(response(before,before,(350,540),(270,600))['valid'])

class ClippedBoundaryTests(unittest.TestCase):
    def test_four_half_disks_with_reference_radius_variation(self):
        for point in ((248,0),(248,1079),(0,540),(495,540)):
            mask=np.zeros((1080,496),np.uint8);cv2.circle(mask,point,14,1,-1)
            fit=clipped_disk_center(mask,16)
            self.assertIsNotNone(fit,point)
            self.assertLess(np.linalg.norm(np.array(fit['center'])-point),1)
    def test_short_corner_and_non_circular_shapes_rejected(self):
        mask=np.zeros((1080,496),np.uint8);cv2.circle(mask,(0,0),14,1,-1)
        self.assertIsNone(clipped_disk_center(mask,14))
        mask[:]=0;mask[0:15,230:265]=1
        self.assertIsNone(clipped_disk_center(mask,14))
    def test_boundary_fit_never_uses_target_to_hide_position_error(self):
        before=np.zeros((1080,496,3),np.uint8);after=before.copy()
        cv2.circle(before,(168,100),16,(170,170,170),-1)
        cv2.circle(after,(248,0),14,(170,170,170),-1)
        self.assertTrue(response(before,after,(168,100),(248,0))['valid'])
        self.assertFalse(response(before,after,(168,100),(270,0))['valid'])
