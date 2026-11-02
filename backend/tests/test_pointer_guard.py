import sys, unittest, tempfile, threading, time, base64, json
from pathlib import Path
from unittest.mock import patch
import numpy as np
import cv2

from iphone_backend.pointer_guard import response, Stability, guard, choose_anchor
from iphone_backend.native_pointer import map_target


def screen(point):
    im=np.full((1080,496,3),30,np.uint8)
    cv2.circle(im,tuple(map(round,point)),13,(170,170,170),-1)
    return im


class GuardTests(unittest.TestCase):
    def test_anchor_avoids_textured_patch_and_challenge_overlap(self):
        image=screen((248,540))
        anchor=choose_anchor(image,(456,80),(376,180))
        self.assertNotEqual(anchor,[248,540])
        for target in ((24,60),(248,535),(471,1010)):
            probe=(target[0]-80,target[1]-100)
            anchor=choose_anchor(image,target,probe)
            self.assertGreaterEqual(min(np.linalg.norm(np.array(anchor)-p) for p in (target,probe)),80)
    def test_response_requires_two_expected_footprints(self):
        a=(100,300); b=(300,700)
        self.assertTrue(response(screen(a),screen(b),a,b)['valid'])
        self.assertFalse(response(screen(a),screen(a),a,b)['valid'])
        self.assertFalse(response(screen(a),screen((195,379)),a,b)['valid'])
        changed=screen(b);changed[400:500,30:300]=255
        self.assertFalse(response(screen(a),changed,a,b)['valid'])
    def test_thin_tail_removed_from_shape_but_counted_as_change(self):
        a=(100,300);b=(300,700)
        image=screen(b)
        image[313:345,100]=170  # thin tail connected to disappearing footprint
        result=response(screen(a),image,a,b)
        self.assertTrue(result['valid'],result)
        self.assertGreater(result['outside_pixels'],20)
        # A large unexplained change must still fail; opening is not an exemption.
        image[345:355,90:110]=170
        self.assertFalse(response(screen(a),image,a,b)['valid'])
    def test_local_scene_is_classified_but_strict_response_still_rejects(self):
        a=(100,300);b=(300,700)
        image=screen(b);image[940:960,440:460]=255
        result=response(screen(a),image,a,b)
        self.assertFalse(result['valid']);self.assertEqual(len(result['localized_scene_changes']),1)
        image[800:900,30:150]=255
        self.assertFalse(response(screen(a),image,a,b)['localized_scene_changes'])
        image=screen(b);image[680:700,330:345]=255
        self.assertFalse(response(screen(a),image,a,b)['localized_scene_changes'])
    def test_top_edge_not_masked_but_clock_change_ignored(self):
        a=(248,160);b=(248,0)
        image=screen(b);image[20:40,20:80]=255
        self.assertTrue(response(screen(a),image,a,b)['valid'])
    def test_distinct_frames_temporal_span_and_jitter(self):
        s=Stability();ok={'valid':True,'center':[100,200]}
        self.assertFalse(s.add('1',0,ok));self.assertFalse(s.add('1',1,ok))
        self.assertFalse(s.add('2',.1,ok));self.assertTrue(s.add('3',.2,ok))
        self.assertFalse(s.add('4',.3,{'valid':False}))
        self.assertFalse(s.add('5',.4,ok))
        self.assertFalse(s.add('6',.5,{'valid':True,'center':[105,200]}))
    def test_guard_delayed_frames_and_direction_failure(self):
        for reverse, local_scene, review_scene in ((False,False,False),(True,False,False),(False,True,False),(False,True,True)):
            with tempfile.TemporaryDirectory() as tmp:
                class Worker:
                    runtime=Path(tmp)
                    point=(60,200);previous=point;index=0;calls=[];invalidated=False;lag=0;moves=0
                    def check(self,*args):pass
                    def pause(self,seconds,*args):time.sleep(seconds)
                    def send(self,c,d,op,**kw):
                        self.calls.append(op);self.previous=self.point;self.moves+=1
                        x=kw['x']/32767*495;y=kw['y']/32767*1079
                        self.point=(495-x,1079-y) if reverse else (x,y)
                        self.lag=2
                    def capture(self,*args,**kw):
                        self.index+=1
                        point=self.previous if self.lag else self.point
                        self.lag=max(0,self.lag-1)
                        im=screen(point)
                        if local_scene and self.moves>=3:im[940:960,440:460]=255
                        raw=cv2.imencode('.jpg',im)[1].tobytes()
                        return {},{'frame_id':f's:{self.index}','age_seconds':0,'frame_time':time.time(),'jpeg_base64':base64.b64encode(raw).decode()}
                    def invalidate_geometry(self):self.invalidated=True
                w=Worker()
                failed=reverse or (local_scene and not review_scene)
                if failed:
                    with self.assertRaisesRegex(RuntimeError,'click blocked'):guard(w,(250,550),None,threading.Event(),time.perf_counter()+8,review_local_scene=review_scene)
                    self.assertEqual(w.invalidated,reverse)
                else:self.assertEqual(guard(w,(250,550),None,threading.Event(),time.perf_counter()+8,review_local_scene=review_scene)['state'],'passed')
                self.assertNotIn('abs.click',w.calls)
                result=json.loads(next(Path(tmp).rglob('result.json')).read_text())
                self.assertGreater(len(result['frames']),3)
                self.assertEqual(result['state'],'failed' if failed else 'passed')
                if local_scene and review_scene:self.assertTrue(result['scene_changes'])

if __name__=='__main__':unittest.main()
