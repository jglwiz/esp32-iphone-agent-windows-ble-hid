"""Local authenticated restart admission. No process control in this module."""
import secrets
from fastapi import BackgroundTasks, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field


class RestartRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')
    request_id: str = Field(min_length=1, max_length=80)
    expected_instance_id: str = Field(min_length=1, max_length=80)


def install_restart(app, owner, callback=None, token='', manager_probe=None):
    enabled = callback is not None and len(token) >= 32

    @app.post('/admin/restart', status_code=202)
    async def restart(body: RestartRequest, request: Request, tasks: BackgroundTasks):
        if not enabled: raise HTTPException(503, 'Restart requires service_manager.py and restart token')
        if not request.client or request.client.host not in ('127.0.0.1', '::1'):
            raise HTTPException(403, 'Loopback client required')
        if not secrets.compare_digest(request.headers.get('authorization', ''), 'Bearer '+token):
            raise HTTPException(401, 'Invalid restart token')
        if manager_probe is not None:
            try:
                manager = await manager_probe()
            except Exception as exc:
                raise HTTPException(503,'Backend manager unavailable; API retained') from exc
            if manager.get('api_state') != 'ready':
                raise HTTPException(503,'Backend manager not supervising this API')
            if manager.get('capture_pending'):
                raise HTTPException(409,'Capture startup in progress')
        with owner.lock:
            if body.expected_instance_id != owner.instance_id:
                raise HTTPException(409, 'Service instance changed; do not replay restart')
            if owner.restart_request:
                if owner.restart_request != body.request_id: raise HTTPException(409, 'Restart already pending')
                return {'state':'restart_requested','instance_id':owner.instance_id,'request_id':body.request_id}
            busy = (getattr(owner, "reset_pending", None) or owner.active_id is not None or not owner.jobs.empty() or owner.calibration_reserved or
                    any(not f.done() for _, f, _ in owner.entries.values()) or
                    any(j.result['state'] not in ('passed','failed','cancelled') for j in owner.calibration_jobs.values()))
            if busy: raise HTTPException(409, 'Controller busy; wait for completion before requesting restart')
            owner.restart_request = body.request_id
            tasks.add_task(callback)  # Response first, then request graceful Uvicorn shutdown.
            return {'state':'restart_requested','instance_id':owner.instance_id,'request_id':body.request_id}

    return enabled
