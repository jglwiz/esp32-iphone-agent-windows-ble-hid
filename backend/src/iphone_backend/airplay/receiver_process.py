"""Receiver discovery only; the capture reader owns startup decisions."""
import os
from pathlib import Path
import psutil

PROC_NAME = 'uxplay-windows.exe'
EXE_DIR = Path(os.environ['UXPLAY_DIR'])
if not EXE_DIR.is_absolute():
    raise ValueError('UXPLAY_DIR must be absolute')

def _pids_of(exe_name):
    result=set()
    for process in psutil.process_iter(['name']):
        try:
            if (process.info['name'] or '').casefold()==exe_name.casefold():
                result.add(process.pid)
        except psutil.Error:
            pass
    return result
