import json
from pathlib import Path
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

import cv2
import numpy as np


from iphone_backend.absolute_pointer import run
from iphone_backend.service import Action, SerialWorker
from iphone_backend.airplay.frames import Frame
from test_service import FakeBridge


class AbsoluteTests(unittest.TestCase):
    def test_stalled_pointer_stops_before_click(self):
        position = {'status':'estimated','x':22,'y':22,'old_center':[250,400]}
        calls = []
        def perform(steps, path, **kwargs):
            calls.extend(steps)
            cv2.imwrite(str(path), np.zeros((1080,496,3),np.uint8))
            return {'released':True,'frame':{'frame_time':time.time()},'input_state':'reports_sent'}
        with tempfile.TemporaryDirectory() as d, patch('iphone_backend.absolute_pointer.locate', return_value=position):
            result = run((2,2),Path(d)/'trial',perform,click=True)
        self.assertIn('no_progress',result['reason'])
        self.assertEqual(result['requests'],4)  # wake, acquire, two failed moves
        self.assertFalse(any(s['op']=='click' for s in calls))

    def test_validation_requires_coordinates_and_rejects_held_input(self):
        for steps in [[{'op':'click_at','x':50}],
                      [{'op':'move_to','x':496,'y':50}],
                      [{'op':'mouse_down'},{'op':'click_at','x':50,'y':50}]]:
            with self.assertRaises(ValueError):
                Action(request_id='bad',steps=steps)

    def test_service_absolute_is_single_owner_and_deduplicated(self):
        fake = FakeBridge()
        async def reader(client):
            image = np.zeros((1080,496,3),np.uint8)
            return Frame(cv2.imencode('.jpg', image)[1].tobytes(),time.time(),'fake')
        def controller(target, folder, perform, click=False):
            folder.mkdir(parents=True)
            result = perform([{'op':'move','dx':10,'dy':0}],folder/'m.jpg',settle_ms=0)
            self.assertTrue(result['released'])
            perform([],folder/'verify.jpg',settle_ms=0)
            return {'status':'reached'}
        with tempfile.TemporaryDirectory() as d, patch('iphone_backend.orchestration.run_absolute',side_effect=controller):
            worker = SerialWorker(bridge_factory=lambda **kw:fake,frame_reader=reader,runtime=Path(d))
            original_capture = worker.capture
            capture_boundaries = []
            def capture(after, cancel, deadline):
                capture_boundaries.append(after)
                return original_capture(after, cancel, deadline)
            worker.capture = capture
            worker.start()
            try:
                a=Action(request_id='abs',steps=[{'op':'move_to','x':250,'y':540}],capture=False)
                future,_=worker.submit(a)
                self.assertIs(future,worker.submit(a)[0])
                result=future.result(5)
                self.assertEqual(result['input_state'],'reports_sent')
                self.assertEqual(result['absolute'][0]['status'],'reached')
                self.assertEqual(sum(op=='mouse.move' for op,_ in fake.calls),1)
                self.assertEqual(len(fake.thread_ids),1)
                self.assertIsNotNone(capture_boundaries[0])
                self.assertIsNone(capture_boundaries[1])  # static no-input recheck
            finally:
                worker.stop()

    def test_failed_absolute_prevents_later_click_and_releases(self):
        with tempfile.TemporaryDirectory() as d, patch('iphone_backend.orchestration.run_absolute',return_value={
                'status':'failed','reason':'no_progress'}):
            fake=FakeBridge()
            worker=SerialWorker(bridge_factory=lambda **kw:fake,runtime=Path(d))
            worker.start()
            try:
                result=worker.submit(Action(request_id='fail',steps=[
                    {'op':'move_to','x':2,'y':2},{'op':'click'}],capture=False))[0].result(3)
                self.assertIn('no_progress',result['error'])
                self.assertTrue(result['released'])
                self.assertFalse(any(op=='mouse.click' for op,_ in fake.calls))
            finally:
                worker.stop()


if __name__=='__main__':
    unittest.main()
