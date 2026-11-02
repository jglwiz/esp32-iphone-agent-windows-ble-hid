"""Explicit supervisor: restart only after a clean, API-requested child exit."""
from contextlib import contextmanager
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import time
import urllib.request

from iphone_backend.config import PROJECT_ROOT as ROOT
from iphone_backend.config import RUNTIME_DIR
RESTART_EXIT = 75


@contextmanager
def manager_lock(path):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('a+b') as handle:
        handle.seek(0); handle.write(b'0'); handle.flush(); handle.seek(0)
        if os.name == 'nt':
            import msvcrt
            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        try:
            yield
        finally:
            handle.seek(0)
            if os.name == 'nt': msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else: fcntl.flock(handle, fcntl.LOCK_UN)


def port_available():
    with socket.socket() as sock:
        if os.name == 'nt': sock.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        sock.bind(('127.0.0.1', 8892))


def wait_ready(child, previous=None, timeout=20):
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if child.poll() is not None: raise RuntimeError('Controller exited during startup')
        try:
            with opener.open('http://127.0.0.1:8892/health', timeout=1) as response:
                health = json.load(response)
            if health.get('pid') == child.pid and health.get('instance_id') != previous:
                return health['instance_id']
        except (OSError, ValueError):
            pass
        time.sleep(.2)
    raise RuntimeError('Controller health timeout; no replacement child started')


def supervise(spawn, ready):
    previous = None
    while True:
        child = spawn()
        # A health failure stops supervision. Never spawn a second serial owner.
        try:
            previous = ready(child, previous)
        except Exception as exc:
            print(json.dumps({'event':'health_failed', 'pid':child.pid, 'error':str(exc)}), flush=True)
            # Retain the manager lock until this child actually exits; no forced kill.
            child.wait()
            return 1
        print(json.dumps({'event':'ready', 'pid':child.pid, 'instance_id':previous}), flush=True)
        code = child.wait()
        if code != RESTART_EXIT: return code


def main():
    # Older imports retain the lock/restart utilities, but there is only one
    # production startup implementation, including capture supervision.
    from .manager import main as run_manager
    return run_manager()


if __name__ == '__main__':
    raise SystemExit(main())
