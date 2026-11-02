"""Deployment paths do not depend on the caller's working directory."""
import os
from pathlib import Path

def absolute_setting(name, default):
    value = Path(os.environ.get(name, default))
    if not value.is_absolute():
        raise ValueError(name+' must be an absolute path')
    return value.resolve()

_checkout = Path(__file__).resolve().parents[3]
if not (_checkout/'backend'/'pyproject.toml').exists() and not os.environ.get('IPHONE_PROJECT_ROOT'):
    raise RuntimeError('Installed deployment requires IPHONE_PROJECT_ROOT; refusing an implicit data directory')
PROJECT_ROOT = absolute_setting('IPHONE_PROJECT_ROOT', _checkout)
RUNTIME_DIR = absolute_setting('IPHONE_RUNTIME_DIR', PROJECT_ROOT / '.runtime')

def runtime_dir():
    RUNTIME_DIR.mkdir(parents=True, exist_ok=True)
    return RUNTIME_DIR
