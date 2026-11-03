"""Production capture/service tests using only synthetic loopback servers and fake serial."""
import asyncio
import base64
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from io import BytesIO
import json
from pathlib import Path
import socket
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

from PIL import Image
import httpx
import uvicorn
from fastapi.testclient import TestClient


from iphone_backend.airplay.frames import Frame, FrameCapture
from iphone_backend.service import Action, SerialWorker, create_app
from iphone_backend.service_client import act
from test_service import FakeBridge


image = BytesIO()
Image.new('RGB', (8, 8), 'green').save(image, format='JPEG')
JPEG = image.getvalue()


@contextmanager
def source(mode='fast'):
    stop = threading.Event()
    entered = threading.Event()

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass

        def do_GET(self):
            entered.set()
            try:
                if mode == 'headers' and stop.wait(2):
                    return
                self.send_response(204 if mode == 'empty' else 200)
                if mode != 'legacy':
                    self.send_header('X-Frame-Snapshot-Version', '1')
                    self.send_header('X-Frame-Id', 'synthetic:1')
                timestamp = time.time() - (60 if mode == 'stale' else 0)
                self.send_header('X-Frame-Time', 'nan' if mode == 'invalid_time' else str(timestamp))
                if mode == 'chunked':
                    self.send_header('Transfer-Encoding', 'chunked')
                self.end_headers()
                if mode == 'empty':
                    return
                if mode == 'stall':
                    stop.wait(2)
                    return
                if mode in ('drip', 'chunked'):
                    for byte in JPEG:
                        chunk = bytes([byte])
                        self.wfile.write(b'1\r\n'+chunk+b'\r\n' if mode == 'chunked' else chunk)
                        self.wfile.flush()
                        if stop.wait(.02):
                            return
                    if mode == 'chunked':
                        self.wfile.write(b'0\r\n\r\n')
                else:
                    self.wfile.write(b'broken' if mode == 'invalid_jpeg' else JPEG)
            except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
                pass

    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    server.daemon_threads = False
    thread = threading.Thread(target=server.serve_forever, kwargs={'poll_interval': .01})
    thread.start()
    try:
        yield f'http://127.0.0.1:{server.server_port}/frame', entered
    finally:
        stop.set()
        server.shutdown()
        server.server_close()
        thread.join(2)
        assert not thread.is_alive()


class CaptureTests(unittest.TestCase):
    def test_http_capability_validation_and_states(self):
        for mode, expected in [('fast', 'available'), ('legacy', 'invalid'),
                               ('empty', 'unavailable'), ('stale', 'stale_or_unknown'),
                               ('invalid_time', 'invalid'), ('invalid_jpeg', 'invalid')]:
            with self.subTest(mode=mode), source(mode) as (url, _):
                state, frame = asyncio.run(FrameCapture(url).capture(None, time.perf_counter()+.3, lambda:False))
                self.assertEqual(state['state'], expected)
                if frame:
                    self.assertEqual(base64.b64decode(frame['jpeg_base64']), JPEG)
                    Image.open(BytesIO(base64.b64decode(frame['jpeg_base64']))).load()
                    self.assertIsNone(frame['after_action'])
                if mode == 'legacy':
                    self.assertIn('lacks atomic', state['reason'])

    def test_settle_boundary_rejects_transition_frames_and_accepts_equality(self):
        async def run():
            for accepted in (100.0, 100.01):
                stamps = iter((98.0, 99.9, accepted))
                calls = []
                async def read(client):
                    value = next(stamps)
                    calls.append(value)
                    return Frame(JPEG, value, str(value))
                with patch('iphone_backend.airplay.frames.time.time', return_value=100.1):
                    state, frame = await FrameCapture(reader=read).capture(100.0, time.perf_counter()+1, lambda:False)
                self.assertEqual(state['state'], 'available')
                self.assertEqual(calls, [98.0, 99.9, accepted])
                self.assertEqual(frame['settled_at'], 100.0)
                self.assertTrue(frame['after_action'])
        asyncio.run(run())

    def test_deadline_and_thread_cancellation_with_slow_responses(self):
        for mode in ('headers', 'stall', 'drip', 'chunked'):
            for cancel_test in (False, True):
                with self.subTest(mode=mode, cancel=cancel_test), source(mode) as (url, entered):
                    cancel = threading.Event()
                    trigger = []
                    def set_cancel():
                        if entered.wait(1):
                            time.sleep(.05)
                            trigger.append(time.perf_counter())
                            cancel.set()
                    thread = threading.Thread(target=set_cancel) if cancel_test else None
                    if thread:
                        thread.start()
                    started = time.perf_counter()
                    deadline = started + (1.5 if cancel_test else .25)
                    async def run():
                        result = await FrameCapture(url).capture(None, deadline, cancel.is_set)
                        self.assertFalse([t for t in asyncio.all_tasks() if t is not asyncio.current_task() and not t.done()])
                        return result
                    state, frame = asyncio.run(run())
                    elapsed = time.perf_counter()
                    if thread:
                        thread.join(2)
                        self.assertTrue(trigger)
                    self.assertEqual(state['state'], 'cancelled' if cancel_test else 'unavailable')
                    self.assertIsNone(frame)
                    boundary = trigger[0] if cancel_test else deadline
                    self.assertLessEqual(elapsed-boundary, .2)

    def test_precancelled_does_not_connect(self):
        state, frame = asyncio.run(FrameCapture('http://127.0.0.1:1').capture(None, time.perf_counter()+1, lambda:True))
        self.assertEqual(state['state'], 'cancelled')
        self.assertIsNone(frame)

    def test_future_timestamp_is_invalid_and_expired_budget_skips_reader(self):
        calls = []
        async def read(client):
            calls.append(1)
            return Frame(JPEG, time.time()+10, 'future:1')
        capture = FrameCapture(reader=read)
        state, _ = asyncio.run(capture.capture(None, time.perf_counter()-1, lambda:False))
        self.assertEqual(state['state'], 'unavailable')
        self.assertEqual(calls, [])
        state, _ = asyncio.run(capture.capture(None, time.perf_counter()+1, lambda:False))
        self.assertEqual(state['state'], 'invalid')


class IntegrationTests(unittest.TestCase):
    def test_capture_keeps_serial_lease_and_next_action_usable(self):
        with tempfile.TemporaryDirectory() as folder, source('stale') as (url, _):
            fake = FakeBridge()
            original = fake.request
            timestamps = []
            def request(op, **kw):
                now = time.perf_counter()
                if timestamps and now-timestamps[-1] >= 2:
                    raise RuntimeError('simulated two-second lease expired')
                timestamps.append(now)
                return original(op, **kw)
            fake.request = request
            worker = SerialWorker(lambda **_:fake, runtime=Path(folder), frame_url=url)
            worker.start()
            try:
                result = worker.submit(Action(request_id='long-capture', steps=[{'op':'click'}], settle_ms=0))[0].result(4)
                self.assertEqual(result['capture']['state'], 'stale_or_unknown')
                self.assertGreaterEqual(sum(op=='ping' for op,_ in fake.calls), 4)
                next_result = worker.submit(Action(request_id='next', steps=[{'op':'click'}], capture=False))[0].result(2)
                self.assertEqual(next_result['input_state'], 'reports_sent')
                self.assertEqual(len(fake.thread_ids), 1)
                self.assertIsNone(worker.fault)
            finally:
                worker.stop()

    def test_client_disconnect_requests_capture_cancellation(self):
        from fastapi import HTTPException
        with tempfile.TemporaryDirectory() as folder, source('stall') as (url, entered):
            fake = FakeBridge()
            worker = SerialWorker(lambda **_:fake, runtime=Path(folder), frame_url=url)
            app = create_app(worker)
            endpoint = next(route.endpoint for route in app.routes if getattr(route, 'path', '') == '/action')
            class Request:
                async def is_disconnected(self):
                    return entered.is_set()
            worker.start()
            try:
                with self.assertRaises(HTTPException) as failure:
                    asyncio.run(endpoint(Action(request_id='disconnect', steps=[{'op':'click'}], settle_ms=0), Request()))
                self.assertEqual(failure.exception.status_code, 499)
                result = worker.entries['disconnect'][1].result(1)
                self.assertEqual(result['capture']['state'], 'cancelled')
                self.assertEqual(result['input_state'], 'reports_sent')
                self.assertEqual(sum(op=='mouse.click' for op,_ in fake.calls), 1)
            finally:
                worker.stop()

    def test_real_client_response_files_dedup_and_conflict(self):
        with tempfile.TemporaryDirectory() as folder, source() as (url, _):
            fake = FakeBridge()
            worker = SerialWorker(lambda **_:fake, runtime=Path(folder)/'runtime', frame_url=url)
            sock = socket.socket()
            sock.bind(('127.0.0.1', 0))
            base = f'http://127.0.0.1:{sock.getsockname()[1]}'
            server = uvicorn.Server(uvicorn.Config(create_app(worker), log_level='error', lifespan='on'))
            thread = threading.Thread(target=server.run, kwargs={'sockets':[sock]})
            thread.start()
            try:
                end = time.perf_counter()+5
                while not server.started and thread.is_alive() and time.perf_counter()<end:
                    time.sleep(.01)
                self.assertTrue(server.started)
                first = act([{'op':'click'}], Path(folder)/'first.jpg', request_id='same', settle_ms=0, base_url=base)
                second = act([{'op':'click'}], Path(folder)/'second.jpg', request_id='same', settle_ms=0, base_url=base)
                self.assertEqual(first['action']['state'], first['input_state'])
                self.assertEqual(first['release']['state'], 'released')
                self.assertEqual(first['capture']['state'], 'available')
                self.assertEqual(first['frame'], second['frame'])
                for name in ('first','second'):
                    Image.open(Path(folder)/f'{name}.jpg').load()
                    for suffix in ('.json', '.request.json', '.result.json'):
                        self.assertIsInstance(json.loads((Path(folder)/(name+suffix)).read_text()), dict)
                self.assertEqual((Path(folder)/'first.jpg').read_bytes(), JPEG)
                observed = act([], Path(folder)/'observe.jpg', settle_ms=0, base_url=base)
                self.assertEqual(observed['action']['state'], 'no_input')
                self.assertIsNone(observed['frame']['after_action'])
                self.assertEqual(sum(op == 'mouse.click' for op,_ in fake.calls), 1)
                with httpx.Client(trust_env=False) as client:
                    response = client.post(base+'/action', json={'request_id':'same','steps':[]})
                    self.assertEqual(response.status_code, 409)
            finally:
                server.should_exit = True
                thread.join(5)
                sock.close()
                self.assertFalse(thread.is_alive())

    def test_cancel_and_shutdown_during_capture_release_first(self):
        for shutdown in (False, True):
            with self.subTest(shutdown=shutdown), tempfile.TemporaryDirectory() as folder, source('stall') as (url, entered):
                fake = FakeBridge()
                worker = SerialWorker(lambda **_:fake, runtime=Path(folder), frame_url=url)
                with TestClient(create_app(worker)) as client:
                    future, _ = worker.submit(Action(request_id='slow', steps=[{'op':'mouse_down'}], settle_ms=0))
                    self.assertTrue(entered.wait(2))
                    self.assertEqual(fake.calls[-1][0], 'release_all')
                    began = time.perf_counter()
                    if shutdown:
                        worker.stop()
                    else:
                        self.assertEqual(client.post('/cancel/slow').status_code, 200)
                    result = future.result(1)
                    self.assertLess(time.perf_counter()-began, .2)
                    self.assertEqual(result['capture']['state'], 'cancelled')
                    self.assertEqual(result['input_state'], 'reports_sent')
                    self.assertEqual(sum(op=='mouse.down' for op,_ in fake.calls), 1)
                    self.assertEqual(len(fake.thread_ids), 1)

    def test_failure_and_skipped_mapping(self):
        for kind in ('disabled', 'action', 'release', 'legacy', 'settle_cancel', 'settle_deadline'):
            with self.subTest(kind=kind), tempfile.TemporaryDirectory() as folder, source('legacy') as (url, _):
                fake = FakeBridge()
                original = fake.request
                def request(op, **kw):
                    if (kind=='action' and op=='mouse.click') or (kind=='release' and op=='release_all'):
                        raise RuntimeError('injected failure')
                    return original(op, **kw)
                fake.request = request
                fake.close = lambda: None
                worker = SerialWorker(lambda **_:fake, runtime=Path(folder), frame_url=url)
                worker.start()
                try:
                    action = Action(request_id=kind, steps=[{'op':'click'}], capture=kind!='disabled',
                                    settle_ms=1500 if kind.startswith('settle') else 0,
                                    timeout_seconds=1 if kind=='settle_deadline' else 3)
                    future, cancel = worker.submit(action)
                    if kind=='settle_cancel':
                        end = time.perf_counter()+1
                        while not any(op=='release_all' for op,_ in fake.calls) and time.perf_counter()<end:
                            time.sleep(.01)
                        cancel.set()
                    result = future.result(3)
                    self.assertEqual(result['action']['state'], result['input_state'])
                    self.assertEqual(result['release']['state'], 'released' if result['released'] else 'unknown')
                    expected = {'disabled':'skipped', 'action':'skipped', 'release':'skipped',
                                'legacy':'invalid', 'settle_cancel':'cancelled', 'settle_deadline':'unavailable'}[kind]
                    self.assertEqual(result['capture']['state'], expected)
                    self.assertNotIn('frame', result)
                    if kind not in ('disabled','action','release'):
                        self.assertIn('capture_error', result)
                        self.assertEqual(result['input_state'], 'reports_sent')
                finally:
                    worker.stop()

    def test_persistence_failure_keeps_action_and_updates_capture_state(self):
        with tempfile.TemporaryDirectory() as folder, source() as (url, _):
            fake = FakeBridge()
            worker = SerialWorker(lambda **_:fake, runtime=Path(folder), frame_url=url)
            worker.start()
            try:
                action = Action(request_id='disk', steps=[{'op':'click'}], settle_ms=0)
                with patch.object(Path, 'write_bytes', side_effect=OSError('disk full')):
                    future, _ = worker.submit(action)
                    result = future.result(3)
                self.assertEqual(result['input_state'], 'reports_sent')
                self.assertEqual(result['capture']['state'], 'unavailable')
                self.assertNotIn('frame', result)
                self.assertIn('disk full', result['capture_error'])
                self.assertIs(worker.submit(action)[0], future)
                self.assertEqual(sum(op=='mouse.click' for op,_ in fake.calls), 1)
            finally:
                worker.stop()


if __name__ == '__main__':
    unittest.main()

class ObservationFallbackTests(unittest.IsolatedAsyncioTestCase):
    async def test_pre_settle_frame_is_observation_only_never_confirmation(self):
        stamp=time.time()
        async def read(client):return Frame(JPEG,stamp,'static:1')
        capture=FrameCapture(reader=read)
        state,frame=await capture.capture(stamp+1,time.perf_counter()+.1,lambda:False,observation_after=stamp-.1)
        self.assertEqual(state['state'],'observation_only')
        self.assertFalse(frame['after_action'])
        self.assertFalse(frame['effect_verified'])
        self.assertEqual(frame['frame_time'],stamp)
        state,frame=await capture.capture(stamp+1,time.perf_counter()+.1,lambda:False,observation_after=stamp+.1)
        self.assertIsNone(frame)
    async def test_cancelled_observation_is_not_returned(self):
        stamp=time.time()
        async def read(client):return Frame(JPEG,stamp,'static:1')
        state,frame=await FrameCapture(reader=read).capture(stamp+1,time.perf_counter()+.1,lambda:True,observation_after=stamp-.1)
        self.assertEqual(state['state'],'cancelled');self.assertIsNone(frame)
