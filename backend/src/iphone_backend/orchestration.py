"""Complete control-task orchestration; start via python -m iphone_backend."""
from __future__ import annotations

import asyncio
import base64
import copy
import hashlib
from concurrent.futures import Future
from contextlib import asynccontextmanager
import json
import os
from pathlib import Path
import queue
import threading
import time
from typing import Literal
import uuid

from fastapi import FastAPI, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field, model_validator

from iphone_backend.esp32.serial_transport import Bridge, ascii_key
from iphone_backend.esp32.service import SerialService
from iphone_backend.airplay.frames import FrameCapture
from iphone_backend.absolute_pointer import run as run_absolute
from iphone_backend.native_pointer import map_target, validate_profile, SUPPORTED_DESCRIPTOR_VERSIONS
from iphone_backend.pointer_guard import guard as verify_pointer_response
from iphone_backend.calibration import load_unverified
from iphone_backend.calibration_jobs import CalibrationJob
from iphone_backend.visual_calibration import VisualReviewJob, VisualReviewRequest
from iphone_backend.scope import ScopeJob, ScopeApproval

from iphone_backend.config import PROJECT_ROOT as ROOT
from iphone_backend.device_reset import ResetJob


class Step(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    op: Literal['move', 'click', 'tap', 'drag', 'scroll', 'text', 'key',
                'key_down', 'key_up', 'mouse_down', 'mouse_up', 'wait', 'move_to', 'click_at']
    x: int | None = Field(default=None, ge=0, le=495)
    y: int | None = Field(default=None, ge=0, le=1079)
    dx: int = Field(default=0, ge=-2048, le=2048)
    dy: int = Field(default=0, ge=-2048, le=2048)
    button: int = Field(default=1, ge=1, le=3)
    wheel: int = Field(default=0, ge=-127, le=127)
    usage: int = Field(default=4, ge=0, le=101)
    modifiers: int = Field(default=0, ge=0, le=255)
    text: str = Field(default='', max_length=256)
    seconds: float = Field(default=0.0, ge=0, le=1.5)

    @model_validator(mode='after')
    def validate_text(self):
        if self.op in ('key', 'key_down') and self.usage in (1, 2, 3):
            raise ValueError('Reserved keyboard error usages are not input keys')
        if self.op in ('move_to', 'click_at'):
            if self.x is None or self.y is None:
                raise ValueError('Absolute operations require x and y screenshot pixels')
            if self.button != 1:
                raise ValueError('Absolute click currently supports left button only')
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
    coordinate_backend: Literal['native_hid', 'visual_relative'] | None = None
    diagnostic_uncalibrated: bool = False
    manual_visual: bool = False
    reviewed_action_id: str | None = None
    settle_ms: int = Field(default=700, ge=0, le=2000)
    timeout_seconds: float = Field(default=20, ge=1, le=45)

    @model_validator(mode='after')
    def bound_total(self):
        if self.manual_visual:
            if not self.diagnostic_uncalibrated or self.coordinate_backend != 'native_hid' or not self.capture or len(self.steps) != 1 or self.steps[0].op not in ('move_to','click_at'):
                raise ValueError('Manual visual mode requires one captured native movement or reviewed click')
            if self.steps[0].op == 'click_at' and not self.reviewed_action_id:
                raise ValueError('Manual click requires the reviewed movement action ID')
        if any(s.op in ('move_to', 'click_at') for s in self.steps):
            if any(s.op in ('mouse_down', 'key_down') for s in self.steps):
                raise ValueError('Absolute operations cannot be combined with held inputs')
        if sum(len(s.text) for s in self.steps if s.op == 'text') > 256:
            raise ValueError('At most 256 characters per action')
        return self


class CalibrationRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')
    request_id: str = Field(min_length=1, max_length=80)
    mode: Literal['verify', 'calibrate']


class ScopeRequest(BaseModel):
    model_config=ConfigDict(extra='forbid')
    request_id: str=Field(min_length=1,max_length=80)

class ScopeReview(BaseModel):
    model_config=ConfigDict(extra='forbid')
    reviewer: str=Field(min_length=1,max_length=200)
    observations: dict[str,dict]


class Cancelled(Exception):
    pass


class SerialWorker:
    def __init__(self, bridge_factory=Bridge, frame_reader=None, runtime=None,
                 frame_url='http://127.0.0.1:8891/frame'):
        self.bridge_factory = lambda **kw: SerialService(bridge_factory, **kw)
        self.frames = FrameCapture(frame_url, frame_reader,
            allow_legacy=os.environ.get('BLE_HID_ALLOW_LEGACY_FRAMES') == '1')
        self.runtime = runtime or __import__('iphone_backend.config', fromlist=['RUNTIME_DIR']).RUNTIME_DIR / 'service'
        self.runtime.mkdir(parents=True, exist_ok=True)
        self.jobs = queue.Queue(maxsize=8)
        self.entries = {}  # bounded lifetime: refuse new IDs when full, never silently evict/replay
        self.lock = threading.Lock()
        self.stop_event = threading.Event()
        self.ready = threading.Event()
        self.bridge = None
        self.fault = None
        self.status = None
        self.hid_diagnostics = None
        self.coordinate_backend = os.environ.get('BLE_HID_COORDINATE_BACKEND', 'visual_relative')
        if self.coordinate_backend not in ('native_hid', 'visual_relative'):
            raise ValueError('Invalid configured coordinate backend')
        self.device_id = os.environ.get('BLE_HID_DEVICE_ID', '')
        self.phone_id = os.environ.get('BLE_HID_PHONE_ID', '')
        self.geometry = None
        self.geometry_last = None
        self.geometry_source = None
        self.profile = load_unverified(self.runtime/'calibration-profile.json')
        self.calibration_reserved = None
        self.calibration_jobs = {}
        self.calibration_requests = {}
        self.scope_jobs = {}
        self.scope_requests = {}
        self.visual_reviews = {}
        self.job_contexts = {}
        self.profile_generation = 0
        self.active_id = None
        self.restart_request = None
        self.reset_pending = None
        self.shutdown_clean = False
        self.startup_cleanup_known = True
        self.instance_id = uuid.uuid4().hex
        self.thread = threading.Thread(target=self.run, daemon=True, name='backend-orchestrator')

    def set_fault(self, reason):
        with self.lock:
            if self.profile is not None:
                self.profile['verification'] = 'needs_verification'
            self.profile_generation += 1
            self.geometry = None
            self.geometry_last = None
            self.fault = str(reason)

    def invalidate_geometry(self):
        with self.lock:
            if self.profile is not None: self.profile['verification'] = 'needs_verification'
            self.profile_generation += 1
            self.geometry = None
            self.geometry_last = None

    def accept_geometry(self, frame, profile):
        import cv2, numpy as np
        try:
            source, seq_text = frame['frame_id'].rsplit(':',1)
            if not source or not seq_text.isdecimal(): raise ValueError('Malformed v1 frame ID')
            seq = int(seq_text)
            if source != profile.get('frame_source_id'): raise ValueError('Frame source instance changed')
            baseline = int(profile['frame_sequence'])
            timestamp = float(frame['frame_time'])
            raw = base64.b64decode(frame['jpeg_base64'])
            digest = hashlib.sha256(raw).hexdigest()
            previous = self.geometry_last if self.geometry_source == source else None
            last = previous or (baseline, float(profile['frame_timestamp']), profile['frame_sha256'])
            if seq < last[0] or timestamp < last[1]: raise ValueError('Frame ID or timestamp regressed')
            if seq == last[0] and (timestamp != last[1] or digest != last[2]): raise ValueError('Frame ID reused with different snapshot')
            image = cv2.imdecode(np.frombuffer(raw,dtype=np.uint8),cv2.IMREAD_COLOR)
            if image is None or image.shape[:2] != (1080,496): raise ValueError('Screenshot geometry changed')
            frame['cached_static'] = bool(frame['age_seconds'] > 5 or (previous and seq == previous[0]))
            self.geometry_source = source
            self.geometry_last = (seq,timestamp,digest)
            self.geometry = (496,1080,'portrait')
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError('Geometry verification failed: '+str(exc)) from exc

    def record_status(self, current):
        previous = self.status or {}
        changed = (bool(previous) and 'connection_generation' in current and
                   (previous.get('boot_id') != current.get('boot_id') or
                    previous.get('connection_generation') != current.get('connection_generation') or
                    (previous.get('connected') is True and current.get('connected') is not True) or
                    (previous.get('absolute_ready') is True and current.get('absolute_ready') is not True) or
                    previous.get('device_id') != current.get('device_id') or
                    previous.get('absolute_descriptor_version') != current.get('absolute_descriptor_version')))
        if changed:
            with self.lock:
                if self.profile is not None: self.profile['verification'] = 'needs_verification'
                self.profile_generation += 1
                self.geometry = None
                self.geometry_last = None
        self.status = current
        return changed

    def start(self):
        self.thread.start()
        if not self.ready.wait(8):
            raise RuntimeError('Serial worker startup timed out')

    def stop(self):
        self.stop_event.set()
        with self.lock:
            for job in self.calibration_jobs.values(): job.cancel.set()
            for job in self.visual_reviews.values(): job.cancel.set()
            for job in self.scope_jobs.values(): job.cancel.set()
            for _, _, cancel in self.entries.values():
                cancel.set()
        self.thread.join(12)
        if self.thread.is_alive():
            raise RuntimeError('Serial worker did not stop')

    def submit(self, action):
        encoded = action.model_dump_json()
        with self.lock:
            if self.restart_request or self.reset_pending:
                raise RuntimeError('Service draining for restart')
            old = self.entries.get(action.request_id)
            if old:
                if old[0] != encoded:
                    raise ValueError('request_id conflict')
                return old[1], old[2]
            if self.calibration_reserved:
                raise RuntimeError('Calibration sampling is exclusive')
            if len(self.entries) >= 1000:
                raise OverflowError('Request history full; restart explicitly when idle')
            future, cancel = Future(), threading.Event()
            deadline = time.perf_counter() + action.timeout_seconds
            backend = action.coordinate_backend or self.coordinate_backend
            context = {'backend': backend, 'profile': copy.deepcopy(self.profile), 'generation': self.profile_generation}
            self.jobs.put_nowait((action, future, cancel, deadline))
            self.job_contexts[action.request_id] = context
            self.entries[action.request_id] = (encoded, future, cancel)
            return future, cancel

    def check(self, cancel, deadline):
        if cancel.is_set() or self.stop_event.is_set() or time.perf_counter() > deadline:
            raise Cancelled('Cancelled/deadline exceeded; no replay; partial execution possible')

    def send(self, cancel, deadline, op, **args):
        self.check(cancel, deadline)
        return self.bridge.request(op, timeout=min(3, max(.1, deadline-time.perf_counter())), **args)

    def pause(self, seconds, cancel, deadline):
        until = time.perf_counter() + seconds
        while time.perf_counter() < until:
            self.check(cancel, deadline)
            time.sleep(min(.1, max(0, until-time.perf_counter())))
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

    def capture(self, after, cancel, deadline, geometry_only=False):
        # Dedicated serial actor maintains heartbeats while orchestration awaits frames.
        end = min(deadline, time.perf_counter()+2.5)
        cancelled = lambda: cancel.is_set() or self.stop_event.is_set() or bool(self.bridge and self.bridge.fault)
        result = asyncio.run(self.frames.geometry(end, cancelled) if geometry_only
                             else self.frames.capture(after, end, cancelled))
        if self.bridge and self.bridge.fault:
            self.set_fault(self.bridge.fault)
            return {'state': 'unavailable', 'reason': self.bridge.fault}, None
        return result

    def absolute(self, s, cancel, deadline):
        """Run the entire visual loop inside the serial queue, without HTTP recursion."""
        folder = self.runtime / 'absolute' / uuid.uuid4().hex

        def perform(steps, path, settle_ms=700):
            self.check(cancel, deadline)
            result = {'input_state': 'partial_unknown', 'released': False}
            try:
                self.send(cancel, deadline, 'release_all')
                for raw in steps:
                    self.step(Step(**raw), cancel, deadline)
                result['input_state'] = 'reports_sent' if steps else 'no_input'
            finally:
                try:
                    self.bridge.request('release_all', timeout=1)
                    result['released'] = True
                except Exception as exc:
                    self.set_fault(f'Absolute release failed: {exc}')
                    raise
            self.pause(settle_ms/1000, cancel, deadline)
            # Static AirPlay can stop publishing. A no-input recheck accepts the
            # latest <=5s snapshot; motion still requires a post-settlement frame.
            result['capture'], frame = self.capture(time.time() if steps else None, cancel, deadline)
            if frame is None:
                result['capture_error'] = result['capture']['reason']
            else:
                Path(path).write_bytes(base64.b64decode(frame.pop('jpeg_base64')))
                result['frame'] = frame
            Path(path).with_suffix('.result.json').write_text(json.dumps(result), encoding='utf-8')
            return result

        return run_absolute((s.x, s.y), folder, perform, click=s.op == 'click_at')

    def execute(self, action, cancel, deadline):
        start = time.time()
        result = {'request_id': action.request_id, 'instance_id': self.instance_id,
                  'started_at': start, 'input_state': 'not_started', 'completed_steps': 0,
                  'capture': {'state': 'skipped', 'reason': 'Capture disabled' if not action.capture
                              else 'Action or release did not complete'}}
        release_ok = False
        started_input = False
        context = self.job_contexts.get(action.request_id, {'backend': action.coordinate_backend or self.coordinate_backend, 'profile': copy.deepcopy(self.profile)})
        result['coordinate_backend'] = context['backend']
        native_steps = [s for s in action.steps if s.op in ('move_to', 'click_at')]
        try:
            if self.fault or not self.bridge:
                raise RuntimeError(self.fault or 'Serial unavailable; restart service explicitly')
            self.check(cancel, deadline)
            if native_steps and context['backend'] == 'native_hid':
                if context.get('generation', self.profile_generation) != self.profile_generation:
                    raise RuntimeError('Queued profile invalidated')
                state = self.send(cancel, deadline, 'status')
                if self.record_status(state) or context.get('generation', self.profile_generation) != self.profile_generation:
                    raise RuntimeError('Connection changed; queued profile invalidated')
                if state.get('connected') is not True:
                    raise RuntimeError('Device disconnected')
                if not self.device_id or state.get('device_id') != self.device_id:
                    raise RuntimeError('Device identity unavailable or mismatch')
                if state.get('supports_absolute_pointer') is not True or state.get('absolute_descriptor_version') not in SUPPORTED_DESCRIPTOR_VERSIONS or state.get('absolute_ready') is not True:
                    raise RuntimeError('Native absolute capability unavailable or not ready')
                result['connection_binding'] = {k: state.get(k) for k in ('device_id','boot_id','connection_generation','absolute_descriptor_version')}
                result['profile_generation'] = self.profile_generation
                profile = context['profile']
                diagnostic = action.diagnostic_uncalibrated
                if diagnostic and not action.manual_visual and any(step.op != 'move_to' for step in action.steps):
                    raise RuntimeError('Diagnostic identity permits native movement only')
                if profile is None and not diagnostic:
                    raise RuntimeError('Verified profile required')
                if profile is not None and not diagnostic:
                    validate_profile(profile, device_id=self.device_id, phone_id=self.phone_id, descriptor_version=state['absolute_descriptor_version'], geometry=(496,1080,'portrait'), require_verified=not diagnostic)
                capture_state, geometry_frame = self.capture(None, cancel, deadline, geometry_only=True)
                if geometry_frame is None:
                    self.invalidate_geometry()
                    raise RuntimeError('Geometry snapshot unavailable: '+capture_state.get('reason','unknown'))
                try:
                    geometry_profile = profile
                    if diagnostic:
                        # Movement diagnostics bind to the current fresh source, never
                        # to persisted calibration. This does not verify a profile.
                        if geometry_frame['age_seconds'] > 5:
                            raise ValueError('Diagnostic geometry requires a fresh snapshot')
                        source, sequence = geometry_frame['frame_id'].rsplit(':', 1)
                        geometry_profile = {'frame_source_id':source, 'frame_sequence':int(sequence),
                            'frame_timestamp':geometry_frame['frame_time'],
                            'frame_sha256':hashlib.sha256(base64.b64decode(geometry_frame['jpeg_base64'])).hexdigest()}
                    self.accept_geometry(geometry_frame, geometry_profile)
                except Exception:
                    self.invalidate_geometry()
                    raise
                if context.get('generation') != self.profile_generation:
                    raise RuntimeError('Connection or geometry changed; queued profile invalidated')
                result['geometry_frame'] = {k:v for k,v in geometry_frame.items() if k != 'jpeg_base64'}
                if action.diagnostic_uncalibrated:
                    if not action.manual_visual and any(step.op == 'click_at' for step in native_steps):
                        raise RuntimeError('Diagnostic identity cannot click')
                    profile = None
                    context['profile'] = None
                for step in native_steps:
                    map_target(step.x, step.y, profile, click=step.op == 'click_at' and not action.manual_visual)
                if action.manual_visual and action.steps[0].op == 'click_at':
                    entry = self.entries.get(action.reviewed_action_id)
                    if entry is None or not entry[1].done(): raise RuntimeError('Unknown reviewed movement')
                    prior = entry[1].result()
                    traces = prior.get('absolute') or []
                    if (prior.get('instance_id') != self.instance_id or prior.get('connection_binding') != result['connection_binding'] or prior.get('input_state') != 'reports_sent' or not prior.get('released') or prior.get('capture_error') or prior.get('frame',{}).get('frame_id','').rsplit(':',1)[0] != geometry_frame['frame_id'].rsplit(':',1)[0] or len(traces)!=1 or traces[0].get('target') != [action.steps[0].x,action.steps[0].y] or traces[0].get('pointer_check',{}).get('state') != 'manual_review_required' or not 0 <= time.time()-prior.get('frame',{}).get('frame_time',0) <= 120):
                        raise RuntimeError('Reviewed movement evidence invalid or expired')
            for s in action.steps:
                result['input_state'] = 'partial_unknown'
                started_input = True
                if s.op in ('move_to', 'click_at'):
                    if context['backend'] == 'native_hid':
                        hid = map_target(s.x, s.y, context['profile'], click=s.op == 'click_at' and not action.manual_visual)
                        if action.manual_visual:
                            if s.op == 'move_to':
                                self.send(cancel,deadline,'abs.move',x=hid[0],y=hid[1])
                            check = {'state':'manual_review_required' if s.op == 'move_to' else 'manually_reviewed','automatic_verification':False,'reviewed_action_id':action.reviewed_action_id}
                        else:
                            check = verify_pointer_response(self, (s.x,s.y), context['profile'], cancel, deadline)
                        if s.op == 'click_at':
                            current = self.send(cancel, deadline, 'status')
                            if self.record_status(current) or any(current.get(k) != v for k,v in result['connection_binding'].items()):
                                raise RuntimeError('Connection changed after pointer verification; click blocked')
                            self.send(cancel, deadline, 'abs.click', x=hid[0], y=hid[1])
                        trace = {'backend':'native_hid', 'target':[s.x,s.y], 'hid':hid, 'profile_id':context['profile']['profile_id'] if context['profile'] else None, 'status':'click_submitted' if s.op == 'click_at' else 'report_submitted', 'reports':6 if s.op == 'click_at' else 3, 'pointer_check':check}
                    else:
                        trace = self.absolute(s, cancel, deadline)
                        trace['backend'] = 'visual_relative'
                    result.setdefault('absolute', []).append(trace)
                    if trace['status'] not in ('reached', 'report_submitted', 'click_submitted'):
                        raise RuntimeError('Absolute operation stopped: ' + trace.get('reason', trace['status']))
                else:
                    self.step(s, cancel, deadline)
                result['completed_steps'] += 1
            result['input_state'] = 'reports_sent' if action.steps else 'no_input'
        except Exception as exc:
            result['error'] = str(exc)
        finally:
            if self.bridge and (started_input or not native_steps or context['backend'] != 'native_hid'):
                try:
                    self.bridge.request('release_all', timeout=1)
                    release_ok = True
                except Exception as exc:
                    self.set_fault(f'Release failed: {exc}; restart explicitly')
                    result['release_error'] = self.fault
        if not started_input and native_steps and context['backend'] == 'native_hid':
            release_ok = True  # no report or held state was submitted by this action
        result['released'] = release_ok
        result['action'] = {'state': result['input_state']}
        result['release'] = {'state': 'released' if release_ok else 'unknown'}
        if 'error' in result:
            result['action']['error'] = result['error']
        if 'release_error' in result:
            result['release']['error'] = result['release_error']
        result['input_finished_at'] = time.time()
        if action.capture and release_ok and 'error' not in result:
            try:
                checked = (result.get('absolute') or [{}])[-1].get('pointer_check', {})
                if all(step.op == 'move_to' for step in action.steps) and checked.get('state') == 'passed' and checked.get('frame'):
                    frame = dict(checked['frame'])
                    frame['jpeg_base64'] = base64.b64encode(Path(frame['path']).read_bytes()).decode()
                    frame.update(after_action=False, pointer_response_verified=True)
                    result['frame'] = frame
                    result['capture'] = {'state':'available', 'reason':'Verified continuous pointer response frame; no extra static-frame wait'}
                    result['finished_at'] = time.time()
                    return result
                self.pause(action.settle_ms/1000, cancel, deadline)
                # A newly published frame can still precede UI settlement. Require a frame
                # received AFTER the settle interval; this alone does not prove UI causality.
                result['settled_at'] = time.time()
                result['capture'], frame = self.capture(result['settled_at'] if action.steps else None,
                                                        cancel, deadline)
                if frame is not None:
                    try:
                        import cv2, numpy as np
                        im = cv2.imdecode(np.frombuffer(base64.b64decode(frame['jpeg_base64']), dtype=np.uint8), cv2.IMREAD_COLOR)
                        self.geometry = (im.shape[1], im.shape[0], 'portrait' if im.shape[0] > im.shape[1] else 'landscape') if im is not None else None
                    except Exception:
                        self.geometry = None
                    result['frame'] = frame
                else:
                    result['capture_error'] = result['capture']['reason']
            except Exception as exc:
                result['capture_error'] = str(exc)  # Do not retry input when only capture fails.
                result['capture'] = {'state': 'cancelled' if isinstance(exc, Cancelled)
                                     and (cancel.is_set() or self.stop_event.is_set()) else 'unavailable',
                                     'reason': str(exc)}
        result['finished_at'] = time.time()
        return result

    def run(self):
        try:
            try:
                self.bridge = self.bridge_factory(log=str(self.runtime/'serial.jsonl'))
                self.record_status(self.bridge.request('status'))
                # Read-only descriptor fingerprint, through the sole serial owner.
                # It cannot reveal iOS physical/interface orientation.
                try:
                    diag = self.bridge.request('diag')
                    raw = bytes.fromhex(diag.get('report_map_hex', ''))
                    self.hid_diagnostics = {'captured_at':time.time(), 'response':diag,
                        'report_map_sha256':hashlib.sha256(raw).hexdigest() if raw else None}
                except Exception as exc:
                    self.hid_diagnostics = {'captured_at':time.time(), 'error':str(exc)}
            except Exception as exc:
                self.startup_cleanup_known = getattr(exc, 'cleanup_known', True)
                self.set_fault(str(exc))
            finally:
                self.ready.set()
            while not self.stop_event.is_set():
                try:
                    action, future, cancel, deadline = self.jobs.get(timeout=.4)
                except queue.Empty:
                    if self.bridge and not self.fault:
                        try:
                            self.record_status(self.bridge.request('ping', timeout=1))
                        except Exception as exc: self.set_fault(str(exc))
                    continue
                self.active_id = action.request_id
                if isinstance(action, ResetJob):
                    action.run()
                    self.active_id = None
                    continue
                if isinstance(action, VisualReviewJob):
                    action.run()
                    self.active_id = None
                    continue
                if isinstance(action, (CalibrationJob, ScopeJob, ScopeApproval)):
                    try: action.run()
                    except Exception as exc:
                        self.set_fault('Calibration job failed unexpectedly: ' + str(exc))
                        target_job = action.job if isinstance(action, ScopeApproval) else action
                        target_job.result.update(state='failed', error=self.fault, finished_at=time.time())
                        try:
                            if isinstance(action, CalibrationJob): action.receiver.close()
                        except Exception: pass
                        with self.lock:
                            target_job = action.job if isinstance(action, ScopeApproval) else action
                            if self.calibration_reserved == target_job.job_id: self.calibration_reserved = None
                    self.active_id = None
                    continue
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
                    result['capture'] = {'state': 'unavailable', 'reason': result['capture_error']}
                    self.set_fault(str(exc))
                future.set_result(result)
                self.active_id = None
        finally:
            try:
                if self.bridge:
                    if self.restart_request or self.reset_pending:
                        self.bridge.request('release_all', timeout=2)
                    self.bridge.close()
                    self.shutdown_clean = True
                elif self.startup_cleanup_known:
                    self.shutdown_clean = True
            except Exception as exc:
                self.shutdown_clean = False
                self.set_fault('Shutdown cleanup failed: '+str(exc))
                # Ensure port closure even if the explicit release acknowledgement failed.
                if self.bridge:
                    try: self.bridge.close()
                    except Exception: pass
            while not self.jobs.empty():
                queued, future, _, _ = self.jobs.get_nowait()
                if isinstance(queued, ResetJob):
                    queued.result.update(state="unknown", error="Service stopped before reset execution")
                    queued.save()
                    continue
                if isinstance(queued, VisualReviewJob):
                    queued.fail('Service stopped before review')
                    continue
                if isinstance(queued, CalibrationJob):
                    queued.finish('cancelled', 'Service stopped before execution')
                    queued.receiver.close()
                    continue
                if isinstance(queued, (ScopeJob, ScopeApproval)):
                    (queued.job if isinstance(queued, ScopeApproval) else queued).fail(RuntimeError('Service stopped before execution'))
                    continue
                future.set_result({'error': 'Service stopped before execution', 'input_state': 'not_started',
                                   'action': {'state': 'not_started'}, 'released': False,
                                   'release': {'state': 'unknown'},
                                   'capture': {'state': 'skipped', 'reason': 'Service stopped before execution'}})
