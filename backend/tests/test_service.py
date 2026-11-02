import json
from pathlib import Path
import sys
import tempfile
import threading
import time
import unittest


try:
    from pydantic import ValidationError
    from iphone_backend.service import Action, SerialWorker
    from iphone_backend.airplay.frames import Frame
except ModuleNotFoundError as exc:
    if exc.name in ('fastapi', 'pydantic'):
        raise unittest.SkipTest('Install pc/requirements-service.txt for service tests')
    raise


class FakeBridge:
    def __init__(self, **kwargs):
        self.calls = []
        self.thread_ids = set()
        self.started = threading.Event()

    def request(self, op, **kwargs):
        self.thread_ids.add(threading.get_ident())
        self.calls.append((op, kwargs))
        if op == 'keyboard.tap':
            self.started.set()
            time.sleep(.015)
        return {'state': 'ok', 'boot_id': 'test'}

    def close(self):
        self.request('release_all')


def fake_frame(age=0):
    async def read(client):
        return Frame(b'jpeg-test', time.time()-age, 'test:1')
    return read


class ServiceTests(unittest.TestCase):
    def test_modifier_only_usage_and_reserved_error_usages(self):
        action = Action(request_id='modifier-only', capture=False, steps=[
            {'op':'key_down', 'usage':0, 'modifiers':2},
            {'op':'key_down', 'usage':4, 'modifiers':2},
            {'op':'key_up'}])
        result = self.worker.submit(action)[0].result(3)
        self.assertEqual(result['input_state'], 'reports_sent')
        self.assertTrue(result['released'])
        keys = [(op, kw) for op, kw in self.fake.calls if op.startswith('keyboard.')]
        self.assertEqual([(op, kw.get('usage'), kw.get('modifiers')) for op, kw in keys],
                         [('keyboard.down',0,2), ('keyboard.down',4,2), ('keyboard.up',None,None)])
        for usage in (1,2,3):
            with self.assertRaises(ValidationError):
                Action(request_id='error-usage', steps=[{'op':'key','usage':usage}])

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.fake = FakeBridge()
        self.worker = SerialWorker(bridge_factory=lambda **kw: self.fake,
            frame_reader=fake_frame(), runtime=Path(self.tmp.name))
        self.worker.start()

    def tearDown(self):
        self.worker.stop()
        self.tmp.cleanup()

    def test_duplicate_request_does_not_repeat_input(self):
        a = Action(request_id='same', steps=[{'op': 'text', 'text': 'abc'}], settle_ms=0)
        f, _ = self.worker.submit(a)
        again, _ = self.worker.submit(a)
        self.assertIs(f, again)
        r = f.result(3)
        self.assertEqual(r['input_state'], 'reports_sent')
        self.assertEqual(sum(op == 'keyboard.tap' for op, _ in self.fake.calls), 3)
        self.assertGreaterEqual(r['frame']['frame_time'], r['input_finished_at'])
        self.assertEqual(len(self.fake.thread_ids), 1)
        with self.assertRaisesRegex(ValueError, 'conflict'):
            self.worker.submit(Action(request_id='same', steps=[{'op':'click'}]))

    def test_cancel_releases_and_stops_remaining_text(self):
        a = Action(request_id='cancel', steps=[{'op':'text','text':'a'*100}], capture=False)
        f, cancel = self.worker.submit(a)
        self.assertTrue(self.fake.started.wait(1))
        cancel.set()
        r = f.result(2)
        self.assertIn('error', r)
        self.assertTrue(r['released'])
        self.assertLess(sum(op == 'keyboard.tap' for op, _ in self.fake.calls), 100)
        self.assertEqual(self.fake.calls[-1][0], 'release_all')

    def test_bad_later_step_rejected_before_any_input(self):
        before = list(self.fake.calls)
        for text in ('abc中文', 'hello\n'):
            with self.assertRaises(ValidationError):
                Action(request_id='invalid', steps=[{'op':'click'}, {'op':'text','text':text}])
        self.assertEqual(self.fake.calls, before)

    def test_capture_failure_does_not_replay_input(self):
        self.worker.frames.reader = fake_frame(60)
        a = Action(request_id='stale', steps=[{'op':'click'}], timeout_seconds=1, settle_ms=0)
        f, _ = self.worker.submit(a)
        r = f.result(3)
        self.assertEqual(r['input_state'], 'reports_sent')
        self.assertIn('capture_error', r)
        self.assertEqual(sum(op == 'mouse.click' for op, _ in self.fake.calls), 1)
        self.assertIs(self.worker.submit(a)[0], f)

    def test_held_input_is_released_before_return(self):
        f, _ = self.worker.submit(Action(request_id='held', steps=[{'op':'mouse_down'}], capture=False))
        self.assertTrue(f.result(2)['released'])
        self.assertEqual([op for op, _ in self.fake.calls][-2:], ['mouse.down','release_all'])

    def test_frame_must_follow_settle_not_just_input(self):
        # Simulate capture backlog: timestamp after input but before settle cannot pass.
        self.worker.frames.reader = fake_frame(.5)
        f, _ = self.worker.submit(Action(request_id='settle', steps=[{'op':'click'}],
                                        settle_ms=700, timeout_seconds=1))
        r = f.result(3)
        self.assertIn('capture_error', r)
        self.assertNotIn('frame', r)

    def test_expired_queued_action_never_clicks(self):
        f1, _ = self.worker.submit(Action(request_id='long', steps=[{'op':'wait','seconds':1.2}], capture=False))
        f2, _ = self.worker.submit(Action(request_id='expired', steps=[{'op':'click'}], capture=False, timeout_seconds=1))
        f1.result(3)
        self.assertIn('error', f2.result(2))
        self.assertFalse(any(op == 'mouse.click' for op, _ in self.fake.calls))


if __name__ == '__main__':
    unittest.main()
