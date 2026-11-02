"""Lifecycle operations invoked exclusively by the backend manager."""
import asyncio
import json
import os
from pathlib import Path
import subprocess
import socket
import sys
import httpx
from iphone_backend.config import RUNTIME_DIR

class CaptureService:
    def __init__(self):
        self.lock = asyncio.Lock()
        self.child = None
        self.url = 'http://127.0.0.1:'+os.environ.get('HIDAGENT_CAPTURE_PORT','8891')

    async def health(self):
        async with httpx.AsyncClient(trust_env=False, timeout=1) as client:
            response = await client.get(self.url+'/health')
            response.raise_for_status()
            health = response.json()
        expected = str(Path(__file__).resolve().parent)
        allowed = [os.path.normcase(expected)]
        legacy = os.environ.get('IPHONE_LEGACY_CAPTURE_OWNER')
        if legacy: allowed.append(os.path.normcase(str(Path(legacy).resolve())))
        if health.get('service') != 'hidagent-capture' or os.path.normcase(health.get('owner','')) not in allowed:
            raise RuntimeError('Capture port belongs to an unrecognized instance')
        return health

    async def ensure_started(self):
        async with self.lock:
            try:
                return {'state':'ready','reused':True,'health':await self.health()}
            except httpx.TransportError:
                with socket.socket() as probe:
                    if os.name=='nt':probe.setsockopt(socket.SOL_SOCKET,socket.SO_EXCLUSIVEADDRUSE,1)
                    probe.bind(('127.0.0.1',int(os.environ.get('HIDAGENT_CAPTURE_PORT','8891'))))
            RUNTIME_DIR.mkdir(parents=True, exist_ok=True)
            if not os.environ.get('UXPLAY_DIR'):
                raise RuntimeError('Set UXPLAY_DIR before starting a new capture worker')
            with (RUNTIME_DIR/'airplay.log').open('ab') as log:
                self.child = subprocess.Popen([sys.executable,'-m','iphone_backend','--role','capture'],
                    env=os.environ.copy(), stdin=subprocess.DEVNULL, stdout=log, stderr=log,
                    creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
            for _ in range(40):
                if self.child.poll() is not None: raise RuntimeError('Capture worker exited before readiness')
                try:
                    return {'state':'ready','reused':False,'health':await self.health()}
                except (httpx.ConnectError,httpx.ConnectTimeout):
                    await asyncio.sleep(.1)
            raise TimeoutError('Capture startup timed out; inspect worker before retry')
