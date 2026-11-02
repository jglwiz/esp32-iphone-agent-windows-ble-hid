import sys
import tempfile
import threading
import unittest
from concurrent.futures import Future
from pathlib import Path
from unittest.mock import Mock


from fastapi.testclient import TestClient
from iphone_backend.service import SerialWorker, create_app
from iphone_backend.service_manager import RESTART_EXIT, supervise
from test_service import FakeBridge, fake_frame


class RestartTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.bridge = FakeBridge()
        self.worker = SerialWorker(bridge_factory=lambda **kw:self.bridge,
                                   frame_reader=fake_frame(), runtime=Path(self.tmp.name))
        self.callback = Mock()
        self.token = 't'*32
        self.headers = {'Authorization':'Bearer '+self.token}
        self.body = {'request_id':'restart-1','expected_instance_id':self.worker.instance_id}

    def tearDown(self):
        self.tmp.cleanup()

    def client(self, enabled=True, host='127.0.0.1'):
        return TestClient(create_app(self.worker, restart_callback=self.callback if enabled else None,
                                    restart_token=self.token), client=(host,12345))

    def test_response_idempotence_and_admission_closed(self):
        with self.client() as client:
            for _ in range(2):
                self.assertEqual(client.post('/admin/restart',json=self.body,headers=self.headers).status_code,202)
            self.callback.assert_called_once()
            self.assertTrue(client.get('/health').json()['restart']['pending'])
            self.assertEqual(client.post('/action',json={'request_id':'late','steps':[]}).status_code,409)
            self.assertEqual(client.post('/calibrations',json={'request_id':'late','mode':'verify'}).status_code,503)
            self.assertEqual(client.post('/scope-validations',json={'request_id':'late'}).status_code,503)
            self.assertEqual(client.post('/admin/restart',json={**self.body,'request_id':'another'},headers=self.headers).status_code,409)
        self.assertTrue(self.worker.shutdown_clean)
        self.assertFalse(self.worker.thread.is_alive())
        self.assertIn('release_all',[op for op,_ in self.bridge.calls])

    def test_auth_and_old_instance(self):
        with self.client() as client:
            self.assertEqual(client.post('/admin/restart',json=self.body).status_code,401)
            self.assertEqual(client.post('/admin/restart',json={**self.body,'expected_instance_id':'old'},headers=self.headers).status_code,409)
            self.assertIsNone(self.worker.restart_request)
            self.callback.assert_not_called()

    def test_remote_rejected(self):
        with self.client(host='192.0.2.1') as client:
            self.assertEqual(client.post('/admin/restart',json=self.body,headers=self.headers).status_code,403)
            self.callback.assert_not_called()

    def test_unmanaged_disabled(self):
        with self.client(enabled=False) as client:
            self.assertFalse(client.get('/health').json()['restart']['enabled'])
            self.assertEqual(client.post('/admin/restart',json=self.body,headers=self.headers).status_code,503)

    def test_busy_rejected_without_cancelling(self):
        with self.client() as client:
            pending, cancel = Future(), threading.Event()
            self.worker.entries['busy'] = ('payload',pending,cancel)
            self.assertEqual(client.post('/admin/restart',json=self.body,headers=self.headers).status_code,409)
            self.assertFalse(cancel.is_set())
            self.assertIsNone(self.worker.restart_request)
            pending.set_result({})
            self.worker.calibration_reserved='sampling'
            self.assertEqual(client.post('/admin/restart',json=self.body,headers=self.headers).status_code,409)
            self.worker.calibration_reserved=None
            self.callback.assert_not_called()

    def test_release_failure_blocks_clean_restart(self):
        original = self.bridge.request
        def request(op, **kw):
            if op == 'release_all': raise OSError('release unavailable')
            return original(op, **kw)
        self.bridge.request=request
        with self.client() as client:
            self.assertEqual(client.post('/admin/restart',json=self.body,headers=self.headers).status_code,202)
        self.assertFalse(self.worker.shutdown_clean)
        self.assertIn('Shutdown cleanup failed',self.worker.fault)


class ManagerTests(unittest.TestCase):
    def test_only_clean_restart_exit_relaunches(self):
        first=Mock(pid=11);first.wait.return_value=RESTART_EXIT
        second=Mock(pid=12);second.wait.return_value=0
        spawn=Mock(side_effect=[first,second]);ready=Mock(side_effect=['a','b'])
        self.assertEqual(supervise(spawn,ready),0)
        self.assertEqual(spawn.call_count,2)
        self.assertEqual(ready.call_args_list[1].args,(second,'a'))

    def test_crash_does_not_loop(self):
        child=Mock(pid=11);child.wait.return_value=1
        spawn=Mock(return_value=child)
        self.assertEqual(supervise(spawn,Mock(return_value='a')),1)
        spawn.assert_called_once()

    def test_health_failure_waits_without_replacement(self):
        child=Mock(pid=11)
        spawn=Mock(return_value=child)
        self.assertEqual(supervise(spawn,Mock(side_effect=RuntimeError('fault'))),1)
        child.wait.assert_called_once();spawn.assert_called_once()
