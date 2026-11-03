"""Fresh-checkout configuration and read-only deployment acceptance. Never opens serial."""
import argparse
import json
import secrets
import sys
import time
import urllib.request
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
PROJECT = BACKEND.parent
CONFIG = BACKEND / '.deployment.json'
OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def initialize(uxplay_dir, phone_id=''):
    receiver = Path(uxplay_dir).resolve()
    if not (receiver / 'uxplay-windows.exe').is_file():
        raise ValueError('UXPLAY directory must contain uxplay-windows.exe and its runtime dependencies')
    if CONFIG.exists():
        raise ValueError('Configuration already exists; retained unchanged. Use check or review it locally.')
    config = {
        'IPHONE_PROJECT_ROOT': str(PROJECT),
        'IPHONE_RUNTIME_DIR': str(BACKEND / '.runtime'),
        'UXPLAY_DIR': str(receiver),
        'HIDAGENT_DATA': str(BACKEND / '.runtime' / 'airplay-data'),
        'AIRPLAY_NAME': 'iPhone-Agent',
        'BLE_HID_RESTART_TOKEN': secrets.token_urlsafe(48),
        'BLE_HID_COORDINATE_BACKEND': 'native_hid',
        'BLE_HID_PHONE_ID': phone_id,
    }
    # Exclusive creation also protects against concurrent bootstrap runs.
    with CONFIG.open('x', encoding='utf-8') as stream:
        json.dump(config, stream, indent=2)
    print('Created local deployment configuration; token is not printed. No service started.')


def read_config():
    config = json.loads(CONFIG.read_text(encoding='utf-8'))
    if Path(config['IPHONE_PROJECT_ROOT']).resolve() != PROJECT:
        raise ValueError('Configuration belongs to another checkout')
    for key in ('IPHONE_RUNTIME_DIR', 'UXPLAY_DIR', 'HIDAGENT_DATA'):
        if not Path(config[key]).is_absolute():
            raise ValueError(key + ' must be absolute')
    if len(config.get('BLE_HID_RESTART_TOKEN', '')) < 32:
        raise ValueError('Management token missing or too short')
    return config


def check():
    config = read_config()
    headers = {'Authorization': 'Bearer ' + config['BLE_HID_RESTART_TOKEN']}
    def fetch(port, path):
        return OPENER.open(urllib.request.Request(
            f'http://127.0.0.1:{port}{path}', headers=headers), timeout=5)
    with fetch(8894, '/health') as response:
        manager = json.load(response)
    with fetch(8892, '/admin/identity') as response:
        identity = json.load(response)
    if Path(identity['runtime_dir']).resolve() != Path(config['IPHONE_RUNTIME_DIR']).resolve():
        raise ValueError('Running API belongs to another deployment')
    with fetch(8892, '/health') as response:
        health = json.load(response)
    if health.get('instance_id') != identity.get('instance_id'):
        raise ValueError('API instance changed during acceptance; retry read-only check')
    status = health.get('status') or {}
    age, geometry, frame_ok = None, None, False
    with fetch(8892, '/frame') as response:
        if response.status == 200:
            jpeg = response.read(8 * 1024 * 1024 + 1)
            age = time.time() - float(response.headers.get('X-Frame-Time', 'nan'))
            frame_id = response.headers.get('X-Frame-Id')
            from PIL import Image
            import io
            image = Image.open(io.BytesIO(jpeg))
            geometry = list(image.size)
            image.verify()
            frame_ok = bool(image.format == 'JPEG' and frame_id and -1 <= age <= 5
                            and len(jpeg) <= 8 * 1024 * 1024 and geometry == [496, 1080])
    checks = {
        'manager_ready': manager.get('api_state') == 'ready',
        'backend_ready': health.get('backend_ready') is True and not health.get('fault'),
        'ble_keyboard_ready': status.get('keyboard_ready') is True,
        'ble_mouse_ready': status.get('mouse_ready') is True,
        'fresh_portrait_frame': frame_ok,
    }
    print(json.dumps({'checks': checks, 'device_id': status.get('device_id'),
                      'frame_age_seconds': round(age, 3) if frame_ok else None,
                      'geometry': geometry, 'input_sent': False}, indent=2))
    return 0 if all(checks.values()) else 1


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='operation', required=True)
    init = commands.add_parser('init')
    init.add_argument('--uxplay-dir', required=True)
    init.add_argument('--phone-id', default='')
    commands.add_parser('check')
    args = parser.parse_args()
    try:
        if args.operation == 'init':
            initialize(args.uxplay_dir, args.phone_id)
            return 0
        return check()
    except Exception as exc:
        # Do not emit credentials from malformed config or authenticated request errors.
        print('Deployment incomplete: ' + type(exc).__name__
              + '. Check local configuration, service logs, BLE pairing and mirroring.', file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
