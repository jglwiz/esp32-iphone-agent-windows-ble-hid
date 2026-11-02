"""Bounded authenticated diagnostics; never accepts arbitrary file paths."""
import secrets
from collections import deque
from pathlib import Path
from fastapi import HTTPException, Request, Query

def install(app, owner, token):
    @app.get('/admin/identity')
    def identity(request: Request):
        if not token or not secrets.compare_digest(request.headers.get('authorization',''),'Bearer '+token):
            raise HTTPException(401,'Invalid management token')
        from .config import RUNTIME_DIR
        import os
        import psutil
        return {'pid':os.getpid(), 'created_at':psutil.Process().create_time(),
                'instance_id':owner.instance_id,'runtime_dir':str(RUNTIME_DIR),'protocol':1}

    @app.get('/device/status')
    def device():
        import copy
        return {'status': copy.deepcopy(owner.status), 'fault': owner.fault,
                'instance_id': owner.instance_id, 'reset_supported': True}

    @app.get('/system/logs')
    def logs(request: Request, source: str = 'esp32', limit: int = Query(100, ge=1, le=500)):
        if not request.client or request.client.host not in ('127.0.0.1', '::1'):
            raise HTTPException(403, 'Loopback client required')
        if not token or not secrets.compare_digest(request.headers.get('authorization', ''), 'Bearer '+token):
            raise HTTPException(401, 'Invalid management token')
        from .config import RUNTIME_DIR
        sources = {'esp32': Path(owner.runtime)/'serial.jsonl',
                   'backend': RUNTIME_DIR/'backend.log', 'airplay': RUNTIME_DIR/'airplay.log'}
        if source not in sources:
            raise HTTPException(400, 'Unknown log source')
        path = sources[source]
        try:
            with path.open('rb') as stream:
                stream.seek(0, 2)
                offset = max(0, stream.tell() - 256*1024)
                stream.seek(offset)
                if offset:
                    stream.readline()
                lines = deque(stream.read(256*1024).decode('utf-8', errors='replace').splitlines(), maxlen=limit)
            return {'source': source, 'lines': [line.replace(token, '[redacted]') for line in lines]}
        except FileNotFoundError:
            return {'source': source, 'lines': []}
        except OSError as exc:
            raise HTTPException(503, 'Log source unavailable') from exc
