"""Single-owner serial service. Run with python pc/service.py (one worker only)."""
from __future__ import annotations

import asyncio
import base64
from concurrent.futures import Future
from contextlib import asynccontextmanager
import json
from pathlib import Path
import queue
import threading
import time
from typing import Literal
import urllib.request
import uuid

from fastapi import FastAPI, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field, model_validator

from bridge import Bridge, ascii_key

ROOT = Path(__file__).resolve().parents[1]


class Step(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    op: Literal['move', 'click', 'tap', 'drag', 'scroll', 'text', 'key',
                'key_down', 'key_up', 'mouse_down', 'mouse_up', 'wait']
    dx: int = Field(default=0, ge=-2048, le=2048)
    dy: int = Field(default=0, ge=-2048, le=2048)
    button: int = Field(default=1, ge=1, le=3)
    wheel: int = Field(default=0, ge=-127, le=127)
    usage: int = Field(default=4, ge=4, le=101)
    modifiers: int = Field(default=0, ge=0, le=255)
    text: str = Field(default='', max_length=256)
    seconds: float = Field(default=0.0, ge=0, le=1.5)

    @model_validator(mode='after')
    def validate_text(self):
        if self.op == 'text':
            # Submission/newlines must be an explicit key action, never implicit text.
            if any(ord(c) < 32 or ord(c) > 126 for c in self.text):
                raise ValueError('text accepts printable US ASCII only; send Enter explicitly')
            for c in self.text:
                ascii_key(c)
        return self


class Action(BaseModel):
    model_config = ConfigDict(extra='forbid')
    request_id: str = Field(min_length=1, max_length=80)
    steps: list[Step] = Field(default_factory=list, max_length=32)
    capture: bool = True
    settle_ms: int = Field(default=700, ge=0, le=2000)
    timeout_seconds: float = Field(default=20, ge=1, le=45)

    @model_validator(mode='after')
    def bound_total(self):
        if sum(len(s.text) for s in self.steps if s.op == 'text') > 256:
            raise ValueError('At most 256 characters per action')
        return self


class Cancelled(Exception):
    pass


class SerialWorker:
    def __init__(self, bridge_factory=Bridge, frame_reader=None, runtime=None):
        self.bridge_factory = bridge_factory
        self.frame_reader = frame_reader or self.read_frame
        self.runtime = runtime or ROOT / '.runtime' / 'service'
        self.runtime.mkdir(parents=True, exist_ok=True)
        self.jobs = queue.Queue(maxsize=8)
        self.entries = {}  # bounded lifetime: refuse new IDs when full, never silently evict/replay
        self.lock = threading.Lock()
        self.stop_event = threading.Event()
        self.ready = threading.Event()
        self.bridge = None
        self.fault = None
        self.status = None
        self.active_id = None
        self.instance_id = uuid.uuid4().hex
        self.thread = threading.Thread(target=self.run, daemon=True, name='serial-owner')

    def start(self):
        self.thread.start()
        if not self.ready.wait(8):
            raise RuntimeError('Serial worker startup timed out')

    def stop(self):
        self.stop_event.set()
        with self.lock:
            for _, _, cancel in self.entries.values():
                cancel.set()
        self.thread.join(12)
        if self.thread.is_alive():
            raise RuntimeError('Serial worker did not stop')

    def submit(self, action):
        encoded = action.model_dump_json()
        with self.lock:
            old = self.entries.get(action.request_id)
            if old:
                if old[0] != encoded:
                    raise ValueError('request_id conflict')
                return old[1], old[2]
            if len(self.entries) >= 1000:
                raise OverflowError('Request history full; restart explicitly when idle')
            future, cancel = Future(), threading.Event()
            deadline = time.monotonic() + action.timeout_seconds
            self.jobs.put_nowait((action, future, cancel, deadline))
            self.entries[action.request_id] = (encoded, future, cancel)
            return future, cancel

    def check(self, cancel, deadline):
        if cancel.is_set() or self.stop_event.is_set() or time.monotonic() > deadline:
            raise Cancelled('Cancelled/deadline exceeded; no replay; partial execution possible')

    def send(self, cancel, deadline, op, **args):
        self.check(cancel, deadline)
        return self.bridge.request(op, timeout=min(3, max(.1, deadline-time.monotonic())), **args)

    def pause(self, seconds, cancel, deadline):
        until = time.monotonic() + seconds
        while time.monotonic() < until:
            self.check(cancel, deadline)
            time.sleep(min(.1, max(0, until-time.monotonic())))
            self.send(cancel, deadline, 'ping')

    def step(self, s, cancel, deadline):
        send = lambda op, **kw: self.send(cancel, deadline, op, **kw)
        if s.op in ('move', 'tap', 'drag'):
            if s.op == 'drag':
                send('mouse.down', button=s.button)
            # Bound cancellation latency while preserving firmware's 8-unit report cadence.
            dx, dy = s.dx, s.dy
            while dx or dy:
                x, y = max(-64, min(64, dx)), max(-64, min(64, dy))
                send('mouse.move', dx=x, dy=y)
                dx -= x; dy -= y
            if s.op == 'tap':
                send('mouse.click', button=s.button)
            if s.op == 'drag':
                send('mouse.up', button=s.button)
        elif s.op == 'click': send('mouse.click', button=s.button)
        elif s.op == 'scroll': send('mouse.scroll', wheel=s.wheel)
        elif s.op == 'text':
            for char in s.text:
                usage, modifiers = ascii_key(char)
                send('keyboard.tap', usage=usage, modifiers=modifiers)
        elif s.op in ('key', 'key_down'):
            send('keyboard.tap' if s.op == 'key' else 'keyboard.down', usage=s.usage, modifiers=s.modifiers)
        elif s.op == 'key_up': send('keyboard.up')
        elif s.op in ('mouse_down', 'mouse_up'): send('mouse.' + s.op.split('_')[1], button=s.button)
        elif s.op == 'wait': self.pause(s.seconds, cancel, deadline)

    @staticmethod
    def read_frame():
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        with opener.open('http://127.0.0.1:8891/frame', timeout=.7) as response:
            if response.status != 200:
                raise RuntimeError(f'No frame: HTTP {response.status}')
            return response.read(), float(response.headers['X-Frame-Time'])

    def capture(self, after, cancel, deadline):
        end = min(deadline, time.monotonic()+2.5)
        error = 'No fresh frame'
        while time.monotonic() < end:
            self.check(cancel, deadline)
            try:
                data, timestamp = self.frame_reader()
                age = time.time()-timestamp
                if -1 <= age <= 5 and (after is None or timestamp >= after):
                    return {'jpeg_base64': base64.b64encode(data).decode(),
                            'frame_time': timestamp, 'age_seconds': age}
                error = 'Frame predates action completion or is stale'
            except Exception as exc:
                error = str(exc)
            self.send(cancel, deadline, 'ping')
            time.sleep(.08)
        raise RuntimeError(error)

    def execute(self, action, cancel, deadline):
        start = time.time()
        result = {'request_id': action.request_id, 'instance_id': self.instance_id,
                  'started_at': start, 'input_state': 'not_started', 'completed_steps': 0}
        release_ok = False
        try:
            if self.fault or not self.bridge:
                raise RuntimeError(self.fault or 'Serial unavailable; restart service explicitly')
            for s in action.steps:
                result['input_state'] = 'partial_unknown'
                self.step(s, cancel, deadline)
                result['completed_steps'] += 1
            result['input_state'] = 'reports_sent' if action.steps else 'no_input'
        except Exception as exc:
            result['error'] = str(exc)
        finally:
            if self.bridge:
                try:
                    self.bridge.request('release_all', timeout=1)
                    release_ok = True
                except Exception as exc:
                    self.fault = f'Release failed: {exc}; restart explicitly'
                    result['release_error'] = self.fault
        result['released'] = release_ok
        result['input_finished_at'] = time.time()
        if action.capture and release_ok and 'error' not in result:
            try:
                self.pause(action.settle_ms/1000, cancel, deadline)
                # A newly published frame can still precede UI settlement. Require a frame
                # produced AFTER the settle interval, not merely after HID submission.
                result['settled_at'] = time.time()
                result['frame'] = self.capture(result['settled_at'] if action.steps else None,
                                               cancel, deadline)
            except Exception as exc:
                result['capture_error'] = str(exc)  # Do not retry input when only capture fails.
        result['finished_at'] = time.time()
        return result

    def run(self):
        try:
            try:
                self.bridge = self.bridge_factory(log=str(self.runtime/'serial.jsonl'))
                self.status = self.bridge.request('status')
            except Exception as exc:
                self.fault = str(exc)
            finally:
                self.ready.set()
            while not self.stop_event.is_set():
                try:
                    action, future, cancel, deadline = self.jobs.get(timeout=.4)
                except queue.Empty:
                    if self.bridge and not self.fault:
                        try: self.status = self.bridge.request('ping', timeout=1)
                        except Exception as exc: self.fault = str(exc)
                    continue
                self.active_id = action.request_id
                result = self.execute(action, cancel, deadline)
                try:
                    # Persist images separately; cached results contain paths, not unbounded base64.
                    frame = result.get('frame')
                    if frame:
                        filename = self.runtime / (uuid.uuid4().hex + '.jpg')
                        filename.write_bytes(base64.b64decode(frame.pop('jpeg_base64')))
                        frame['path'] = str(filename)
                    with (self.runtime/'actions.jsonl').open('a', encoding='utf-8') as out:
                        out.write(json.dumps({'action': action.model_dump(), 'result': result})+'\n')
                except OSError as exc:
                    result.pop('frame', None)
                    result['capture_error'] = f'Evidence persistence failed: {exc}'
                    self.fault = str(exc)
                future.set_result(result)
                self.active_id = None
        finally:
            if self.bridge: self.bridge.close()
            while not self.jobs.empty():
                _, future, _, _ = self.jobs.get_nowait()
                future.set_result({'error': 'Service stopped before execution', 'input_state': 'not_started'})


def create_app(worker=None):
    owner = worker or SerialWorker()

    @asynccontextmanager
    async def lifespan(app):
        await asyncio.to_thread(owner.start)
        yield
        await asyncio.to_thread(owner.stop)

    app = FastAPI(title='BLE HID local controller', lifespan=lifespan)

    @app.get('/health')
    def health():
        return {'service': 'ble-hid-controller', 'instance_id': owner.instance_id,
                'fault': owner.fault, 'status': owner.status, 'active_id': owner.active_id,
                'queue_size': owner.jobs.qsize()}

    @app.post('/action')
    async def action(body: Action, request: Request):
        try: future, cancel = owner.submit(body)
        except ValueError as exc: raise HTTPException(409, str(exc))
        except (queue.Full, OverflowError) as exc: raise HTTPException(429, str(exc))
        try:
            while not future.done():
                if await request.is_disconnected():
                    cancel.set()
                    raise HTTPException(499, 'Client disconnected; cancel requested')
                await asyncio.sleep(.05)
            result = dict(future.result())
            if 'frame' in result:
                result['frame'] = dict(result['frame'])
                result['frame']['jpeg_base64'] = base64.b64encode(Path(result['frame']['path']).read_bytes()).decode()
            return result
        except asyncio.CancelledError:
            cancel.set()
            raise

    @app.post('/cancel/{request_id}')
    def cancel(request_id: str):
        with owner.lock:
            entry = owner.entries.get(request_id)
            if not entry: raise HTTPException(404, 'Unknown request')
            entry[2].set()
        return {'cancel_requested': True}

    return app


if __name__ == '__main__':
    import uvicorn
    uvicorn.run(create_app(), host='127.0.0.1', port=8892, workers=1)
