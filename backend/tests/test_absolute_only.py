"""Absolute-only policy regressions; device behavior requires separate live testing."""
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from iphone_backend.service import Action, SerialWorker
from test_service import FakeBridge


class AbsoluteOnlyTests(unittest.TestCase):
    def test_relative_actions_and_backend_are_rejected_before_input(self):
        for op in ('move', 'tap', 'drag'):
            with self.subTest(op=op), self.assertRaises(ValueError):
                Action(request_id='removed', steps=[{'op': 'click'}, {'op': op, 'dx': 1, 'dy': 0}])
        with self.assertRaises(ValueError):
            Action(request_id='legacy', coordinate_backend='visual_relative')
        with self.assertRaises(ValueError):
            Action(request_id='hidden-relative', steps=[{'op': 'click', 'dx': 1}])

    def test_absolute_coordinates_and_held_input_still_validate(self):
        for steps in ([{'op': 'click_at', 'x': 50}],
                      [{'op': 'move_to', 'x': 496, 'y': 50}],
                      [{'op': 'mouse_down'}, {'op': 'click_at', 'x': 50, 'y': 50}]):
            with self.assertRaises(ValueError):
                Action(request_id='bad', steps=steps)

    def test_default_absolute_does_not_fall_back_when_capability_missing(self):
        with tempfile.TemporaryDirectory() as folder, patch.dict(os.environ, {'BLE_HID_COORDINATE_BACKEND': 'native_hid'}):
            fake = FakeBridge()
            worker = SerialWorker(bridge_factory=lambda **kw: fake, runtime=Path(folder))
            worker.device_id = 'device'
            worker.start()
            try:
                action = Action(request_id='no-capability', capture=False,
                                steps=[{'op': 'move_to', 'x': 250, 'y': 540}, {'op': 'click'}])
                result = worker.submit(action)[0].result(3)
                self.assertEqual(result['coordinate_backend'], 'native_hid')
                self.assertEqual(result['input_state'], 'not_started')
                self.assertFalse(any(op in ('mouse.move', 'mouse.click', 'abs.move', 'abs.click') for op, _ in fake.calls))
            finally:
                worker.stop()

    def test_legacy_deployment_setting_cannot_enable_relative_backend(self):
        with tempfile.TemporaryDirectory() as folder, patch.dict(os.environ, {'BLE_HID_COORDINATE_BACKEND': 'visual_relative'}):
            with self.assertLogs('iphone_backend.orchestration', level='WARNING'):
                worker = SerialWorker(runtime=Path(folder))
            self.assertEqual(worker.coordinate_backend, 'native_hid')


if __name__ == '__main__':
    unittest.main()
