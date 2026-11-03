"""Single-owner serial service. Run with python pc/service.py (one worker only)."""
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
from iphone_backend.native_pointer import map_target, validate_profile
from iphone_backend.pointer_guard import guard as verify_pointer_response
from iphone_backend.calibration import load_unverified
from iphone_backend.calibration_jobs import CalibrationJob
from iphone_backend.visual_calibration import VisualReviewJob, VisualReviewRequest
from iphone_backend.scope import ScopeJob, ScopeApproval

from iphone_backend.config import PROJECT_ROOT as ROOT
from iphone_backend.device_reset import ResetJob


from iphone_backend.orchestration import *

def create_app(worker=None, *, restart_callback=None, restart_token='', manager_probe=None):
    owner = worker or SerialWorker()

    @asynccontextmanager
    async def lifespan(app):
        owner.event_loop = asyncio.get_running_loop()
        await asyncio.to_thread(owner.start)
        yield
        await asyncio.to_thread(owner.stop)

    app = FastAPI(title='BLE HID local controller', lifespan=lifespan)
    from iphone_backend.service_restart import install_restart
    restart_enabled = install_restart(app, owner, restart_callback, restart_token, manager_probe)

    @app.get('/health')
    def health():
        return {'service': 'ble-hid-controller', 'instance_id': owner.instance_id,
                'backend_ready': True,
                'manager': getattr(owner, 'manager_status', {'reachable':None}),
                'components': {'esp32': {'ready': bool(owner.status and not owner.fault), 'fault': owner.fault},
                               'airplay': getattr(owner.frames, 'last_observation', {'freshness':'unknown'})},
                'pid': os.getpid(), 'restart': {'enabled': restart_enabled, 'pending': bool(owner.restart_request)},
                'visual_calibration_version': 3, 'verified_region_blocks_clicks': False, 'manual_visual_move_supported': True, 'signed_native_mapping': False, 'pointer_response_guard_version': 9, 'capture_observation_fallback_version': 1, 'scope_validation_guard_version': 1,
                'allow_legacy_frames': owner.frames.allow_legacy, 'hid_diagnostics':copy.deepcopy(owner.hid_diagnostics),
                'fault': owner.fault, 'status': owner.status, 'status_stale': bool(owner.fault), 'active_id': owner.active_id,
                'queue_size': owner.jobs.qsize(), 'coordinate_backend': owner.coordinate_backend,
                'relative_pointer_supported': False,
                'geometry': owner.geometry, 'profile': owner.profile,
                'calibration_jobs': [copy.deepcopy(j.result) for j in owner.calibration_jobs.values()],
                'absolute_capability': {'supported': bool(owner.status and owner.status.get('supports_absolute_pointer')),
                    'descriptor_version': (owner.status or {}).get('absolute_descriptor_version'),
                    'ready': bool(owner.status and owner.status.get('absolute_ready'))}}

    @app.post('/action')
    async def action(body: Action, request: Request):
        try: future, cancel = owner.submit(body)
        except (ValueError, RuntimeError) as exc: raise HTTPException(409, str(exc))
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

    @app.post('/visual-calibrations/review', status_code=202)
    def visual_review(body: VisualReviewRequest):
        encoded = body.model_dump_json()
        with owner.lock:
            if owner.restart_request or owner.reset_pending: raise HTTPException(503, 'Service draining for restart')
            old = owner.visual_reviews.get(body.request_id)
            if old:
                if old.encoded != encoded: raise HTTPException(409, 'Review request conflict')
                result = copy.deepcopy(old.result)
                if not old.completed and result['state'] in ('passed','failed'): result['state']='reviewing'
                return result
            active_entry = owner.entries.get(owner.active_id)
            active_pending = owner.active_id is not None and (active_entry is None or not active_entry[1].done())
            if owner.calibration_reserved or active_pending or not owner.jobs.empty() or any(j.result['state'] not in ('passed','failed','cancelled') for j in owner.calibration_jobs.values()):
                raise HTTPException(409, 'Controller must be idle for review')
            if len(owner.visual_reviews) >= 100: raise HTTPException(429, 'Review history full')
            job = VisualReviewJob(owner, body, encoded)
            owner.calibration_reserved = job.job_id
            try: owner.jobs.put_nowait((job, None, job.cancel, job.deadline))
            except queue.Full:
                owner.calibration_reserved = None
                raise HTTPException(429, 'Queue full')
            owner.visual_reviews[body.request_id] = job
            return copy.deepcopy(job.result)

    @app.get('/visual-calibrations/{request_id}')
    def get_visual_review(request_id: str):
        with owner.lock:
            job = owner.visual_reviews.get(request_id)
            if job is None: raise HTTPException(404, 'Unknown review')
            result = copy.deepcopy(job.result)
            if not job.completed and result['state'] in ('passed','failed'): result['state']='reviewing'
            return result

    @app.post('/calibrations',status_code=202)
    def start_calibration(body: CalibrationRequest):
        with owner.lock:
            if owner.restart_request or owner.reset_pending: raise HTTPException(503, 'Service draining for restart')
            old=owner.calibration_requests.get(body.request_id)
            if old:
                if old.mode != body.mode: raise HTTPException(409,'request_id conflict')
                return dict(old.result)
            if owner.calibration_reserved or any(j.result['state'] not in ('passed','failed','cancelled') for j in owner.calibration_jobs.values()):
                raise HTTPException(409,'Calibration already active')
            if len(owner.calibration_requests) >= 1000: raise HTTPException(429,'Calibration history full')
            try: job=CalibrationJob(body.request_id,body.mode,owner)
            except (ValueError,OSError) as exc: raise HTTPException(503,str(exc))
            owner.calibration_requests[body.request_id]=job
            owner.calibration_jobs[job.job_id]=job
            job.start()
            return dict(job.result)

    @app.get('/calibrations/{job_id}')
    def get_calibration(job_id: str):
        with owner.lock:
            job=owner.calibration_jobs.get(job_id)
            if not job: raise HTTPException(404,'Unknown calibration')
            return copy.deepcopy(job.result)

    @app.post('/calibrations/{job_id}/cancel')
    def cancel_calibration(job_id: str):
        with owner.lock:
            job=owner.calibration_jobs.get(job_id)
            if not job: raise HTTPException(404,'Unknown calibration')
            job.cancel.set()
        return {'cancel_requested':True}

    @app.post('/scope-validations',status_code=202)
    def start_scope(body: ScopeRequest):
        with owner.lock:
            if owner.restart_request or owner.reset_pending: raise HTTPException(503, 'Service draining for restart')
            old=owner.scope_requests.get(body.request_id)
            if old: return copy.deepcopy(old.result)
            if owner.calibration_reserved or any(j.result['state'] not in ('passed','failed','cancelled') for j in owner.calibration_jobs.values()):
                raise HTTPException(409,'Another validation is active')
            if owner.fault or not owner.profile:
                raise HTTPException(409,'Existing positive profile required; scope never fits a mapping')
            if len(owner.scope_requests)>=1000:raise HTTPException(429,'Scope history full')
            try:
                job=ScopeJob(owner,body.request_id)
                owner.jobs.put_nowait((job,None,job.cancel,job.deadline))
            except (OSError,queue.Full) as exc:raise HTTPException(503,str(exc))
            owner.calibration_reserved=job.job_id
            owner.scope_jobs[job.job_id]=job;owner.scope_requests[body.request_id]=job
            return copy.deepcopy(job.result)

    @app.get('/scope-validations/{job_id}')
    def get_scope(job_id: str):
        with owner.lock:
            job=owner.scope_jobs.get(job_id)
            if not job:raise HTTPException(404,'Unknown scope job')
            return copy.deepcopy(job.result)

    @app.post('/scope-validations/{job_id}/approve',status_code=202)
    def approve_scope(job_id: str, body: ScopeReview):
        with owner.lock:
            if owner.restart_request or owner.reset_pending: raise HTTPException(503, 'Service draining for restart')
            job=owner.scope_jobs.get(job_id)
            if not job:raise HTTPException(404,'Unknown scope job')
            review=body.model_dump();encoded=json.dumps(review,sort_keys=True)
            if hasattr(job,'review_payload'):
                if job.review_payload!=encoded:raise HTTPException(409,'Review conflict')
                return copy.deepcopy(job.result)
            if job.result['state']!='awaiting_review' or owner.calibration_reserved:raise HTTPException(409,'Scope review unavailable')
            if job.generation!=owner.profile_generation or job.result['instance_id']!=owner.instance_id:raise HTTPException(409,'Scope evidence invalidated')
            if not hasattr(job,'review_deadline') or time.monotonic()>job.review_deadline:raise HTTPException(409,'Scope review window expired')
            job.deadline=time.perf_counter()+20
            try:owner.jobs.put_nowait((ScopeApproval(job,review),None,job.cancel,job.deadline))
            except queue.Full as exc:raise HTTPException(429,str(exc))
            job.review_payload=encoded;owner.calibration_reserved=job.job_id
            return copy.deepcopy(job.result)

    @app.post('/scope-validations/{job_id}/cancel')
    def cancel_scope(job_id: str):
        with owner.lock:
            job=owner.scope_jobs.get(job_id)
            if not job:raise HTTPException(404,'Unknown scope job')
            job.cancel.set()
        return {'cancel_requested':True}

    @app.post('/cancel/{request_id}')
    def cancel(request_id: str):
        with owner.lock:
            entry = owner.entries.get(request_id)
            if not entry: raise HTTPException(404, 'Unknown request')
            entry[2].set()
        return {'cancel_requested': True}

    return app
