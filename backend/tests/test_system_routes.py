from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from fastapi.testclient import TestClient
from iphone_backend.app import create_app
from iphone_backend.service import SerialWorker
from test_service import fake_frame

class SystemRoutes(unittest.TestCase):
    def test_absent_device_keeps_api_and_frame_available(self):
        def missing(**kwargs):raise OSError('device absent')
        with tempfile.TemporaryDirectory() as folder:
            owner=SerialWorker(bridge_factory=missing,frame_reader=fake_frame(),runtime=Path(folder))
            with TestClient(create_app(owner)) as client:
                health=client.get('/health').json()
                self.assertTrue(health['backend_ready'])
                self.assertFalse(health['components']['esp32']['ready'])
                self.assertEqual(client.get('/frame').status_code,200)
            self.assertTrue(owner.shutdown_clean)

    def test_log_auth_whitelist_limits_and_redaction(self):
        def missing(**kwargs):raise OSError('device absent')
        with tempfile.TemporaryDirectory() as folder,patch('iphone_backend.config.RUNTIME_DIR',Path(folder)):
            token='secret'*8
            (Path(folder)/'backend.log').write_text('\n'.join([token+' data']*700))
            owner=SerialWorker(bridge_factory=missing,frame_reader=fake_frame(),runtime=Path(folder))
            with TestClient(create_app(owner,restart_token=token),client=('127.0.0.1',1)) as client:
                self.assertEqual(client.get('/system/logs').status_code,401)
                headers={'Authorization':'Bearer '+token}
                result=client.get('/system/logs',params={'source':'backend','limit':500},headers=headers)
                self.assertEqual(len(result.json()['lines']),500)
                self.assertNotIn(token,result.text)
                self.assertEqual(client.get('/system/logs',params={'source':'../../secret'},headers=headers).status_code,400)
                self.assertEqual(client.get('/system/logs',params={'limit':501},headers=headers).status_code,422)
