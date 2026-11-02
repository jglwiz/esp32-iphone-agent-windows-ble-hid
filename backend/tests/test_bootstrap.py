"""First-run deployment tests with no serial, process launch or HTTP traffic."""
import contextlib
import importlib.util
import io
import json
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch

from PIL import Image

spec = importlib.util.spec_from_file_location(
    'bootstrap_tool', Path(__file__).resolve().parents[1] / 'tools' / 'bootstrap.py')
bootstrap = importlib.util.module_from_spec(spec)
spec.loader.exec_module(bootstrap)


class Response(io.BytesIO):
    def __init__(self, data, status=200, headers=None):
        super().__init__(data)
        self.status, self.headers = status, headers or {}


class BootstrapTests(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.root = Path(folder.name)
        for name, value in {'PROJECT': self.root, 'BACKEND': self.root,
                            'CONFIG': self.root / '.deployment.json'}.items():
            mock = patch.object(bootstrap, name, value)
            mock.start()
            self.addCleanup(mock.stop)
        self.receiver = self.root / 'receiver'
        self.receiver.mkdir()
        (self.receiver / 'uxplay-windows.exe').touch()

    def initialize(self):
        with contextlib.redirect_stdout(io.StringIO()) as output:
            bootstrap.initialize(self.receiver)
        return output.getvalue()

    def test_init_is_local_and_never_overwrites_token(self):
        output = self.initialize()
        original = bootstrap.CONFIG.read_bytes()
        config = bootstrap.read_config()
        self.assertGreaterEqual(len(config['BLE_HID_RESTART_TOKEN']), 32)
        self.assertNotIn(config['BLE_HID_RESTART_TOKEN'], output)
        self.assertTrue(Path(config['IPHONE_RUNTIME_DIR']).is_absolute())
        with self.assertRaises(ValueError):
            self.initialize()
        self.assertEqual(original, bootstrap.CONFIG.read_bytes())

    def test_missing_receiver_does_not_create_config(self):
        with self.assertRaises(ValueError):
            bootstrap.initialize(self.root / 'missing')
        self.assertFalse(bootstrap.CONFIG.exists())

    def check(self, *, ble=True, age=0, no_frame=False, wrong_owner=False):
        self.initialize()
        image = io.BytesIO()
        Image.new('RGB', (496, 1080)).save(image, format='JPEG')
        replies = [
            {'api_state': 'ready'},
            {'runtime_dir': str(self.root / 'other' if wrong_owner else self.root / '.runtime'),
             'instance_id': 'test'},
            {'backend_ready': True, 'instance_id': 'test',
             'status': {'keyboard_ready': ble, 'mouse_ready': ble}},
        ]
        requests = []
        def open_response(request, timeout):
            requests.append(request)
            if replies:
                return Response(json.dumps(replies.pop(0)).encode())
            return Response(image.getvalue(), 204 if no_frame else 200,
                            {'X-Frame-Time': str(time.time() - age), 'X-Frame-Id': 'test:1'})
        with patch.object(bootstrap.OPENER, 'open', side_effect=open_response):
            with contextlib.redirect_stdout(io.StringIO()):
                result = bootstrap.check()
        self.assertTrue(all(request.get_method() == 'GET' for request in requests))
        return result

    def test_ready_services_and_fresh_image_pass(self):
        self.assertEqual(self.check(), 0)

    def test_ble_not_ready_fails(self):
        self.assertEqual(self.check(ble=False), 1)

    def test_stale_frame_fails(self):
        self.assertEqual(self.check(age=60), 1)

    def test_no_frame_fails(self):
        self.assertEqual(self.check(no_frame=True), 1)

    def test_other_deployment_fails(self):
        with self.assertRaises(ValueError):
            self.check(wrong_owner=True)


if __name__ == '__main__':
    unittest.main()
