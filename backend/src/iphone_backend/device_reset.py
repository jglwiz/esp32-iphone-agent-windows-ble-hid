"""Reset admission and durable results; execution belongs to the serial actor."""
import copy
import hashlib
import json
import os
import secrets
import threading
import time
from fastapi import HTTPException, Request
from pydantic import BaseModel, Field, ConfigDict

STORE_LOCK = threading.RLock()

def read_record(path):
    with STORE_LOCK:
        return json.loads(path.read_text(encoding='utf-8'))

class ResetRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')
    request_id: str = Field(min_length=1, max_length=80)
    expected_instance_id: str
    expected_device_id: str
    expected_boot_id: str

class ResetJob:
    def __init__(self, owner, body, path):
        self.owner, self.body, self.path = owner, body, path
        self.request_id = body.request_id
        self.result = {'request_id':self.request_id, 'state':'accepted', 'request':body.model_dump()}
        self.cancel = threading.Event()
        self.deadline = time.perf_counter()+20
        self.save()

    def save(self):
        with STORE_LOCK:
            temporary = self.path.with_suffix('.tmp')
            with temporary.open('w', encoding='utf-8') as stream:
                json.dump(self.result, stream)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, self.path)

    def run(self):
        owner = self.owner
        try:
            self.result['state'] = 'resetting'
            self.save()
            after = owner.bridge.reset(expected_device_id=self.body.expected_device_id,
                                       expected_boot_id=self.body.expected_boot_id)
            owner.record_status(after)
            owner.invalidate_geometry()
            owner.fault = None
            self.result.update(state='device_rebooted', status=after,
                               hid_ready=bool(after.get('keyboard_ready') and after.get('absolute_ready')))
        except Exception as exc:
            self.result.update(state='unknown', error=str(exc))
            owner.set_fault(str(exc))
        finally:
            self.result['finished_at'] = time.time()
            try: self.save()
            finally:
                with owner.lock: owner.reset_pending = None

def install(app, owner, token):
    owner.reset_pending = None
    folder = owner.runtime/'device-resets'
    folder.mkdir(parents=True, exist_ok=True)
    def path(request_id):
        return folder/(hashlib.sha256(request_id.encode()).hexdigest()+'.json')
    def authenticate(request):
        if not request.client or request.client.host not in ('127.0.0.1','::1'):
            raise HTTPException(403,'Loopback client required')
        if not token or not secrets.compare_digest(request.headers.get('authorization',''), 'Bearer '+token):
            raise HTTPException(401,'Invalid management token')

    @app.post('/device/reset', status_code=202)
    def reset(body: ResetRequest, request: Request):
        authenticate(request)
        with owner.lock:
            file = path(body.request_id)
            if file.exists():
                saved = read_record(file)
                if saved['request'] != body.model_dump(): raise HTTPException(409,'Reset request conflict')
                return saved
            if body.expected_instance_id != owner.instance_id:
                raise HTTPException(409,'Service instance changed')
            status = owner.status or {}
            if status.get('device_id') != body.expected_device_id or status.get('boot_id') != body.expected_boot_id:
                raise HTTPException(409,'Device identity changed')
            if owner.reset_pending or owner.restart_request or owner.active_id or not owner.jobs.empty() or owner.calibration_reserved or any(j.result['state'] not in ('passed','failed','cancelled') for j in owner.calibration_jobs.values()):
                raise HTTPException(409,'Controller busy')
            if owner.bridge is None: raise HTTPException(503,'No bound serial transport')
            job = ResetJob(owner, body, file)
            owner.reset_pending = body.request_id
            owner.jobs.put_nowait((job, None, job.cancel, job.deadline))
            return copy.deepcopy(job.result)

    @app.get('/device/reset/{request_id}')
    def result(request_id: str, request: Request):
        authenticate(request)
        file = path(request_id)
        if not file.exists(): raise HTTPException(404,'Unknown reset task')
        # Snapshot the durable state and its live execution marker together.
        # Otherwise completion can clear the marker after an older file was read,
        # falsely classifying a successfully completed reset as interrupted.
        with owner.lock:
            value = read_record(file)
            pending = owner.reset_pending == request_id
        if value['state'] in ('accepted','resetting') and not pending:
            value.update(state='unknown', error='Execution interrupted; reset will not be replayed')
            current = owner.status or {}
            expected = value['request']
            if current.get('device_id') == expected['expected_device_id'] and current.get('boot_id') != expected['expected_boot_id']:
                value.update(state='device_reboot_observed', status=copy.deepcopy(current),
                             note='Boot changed after interruption; causality not asserted')
        if value['state']=='device_rebooted':
            current=owner.status or {}
            if current.get('boot_id')==value.get('status',{}).get('boot_id'):
                value.update(hid_ready=bool(current.get('keyboard_ready') and current.get('absolute_ready')),
                             current_status=copy.deepcopy(current))
        return value
