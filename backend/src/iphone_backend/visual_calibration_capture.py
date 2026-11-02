"""Capture one native movement-only sample after strict preflight; never clicks.

Run once per sample, inspect the images before choosing another. Training uses
--command X Y. Holdout uses --target X Y --training samples.json, where the
JSON has six legacy or nine grid manually observed training entries fitted by the formal calibration module.
No profile is written; observed pointer coordinates are deliberately null.
"""
import hashlib
import cv2
import numpy as np
import argparse
import json
import time
import urllib.request
from pathlib import Path
from iphone_backend.calibration import fit_samples
from iphone_backend.service_client import act, health

def fit_training(samples):
    return fit_samples([{'command': s['command'], 'actual': s['observed']} for s in samples])


def connection_context(state):
    status = state.get('status') or {}
    keys = ('device_id', 'boot_id', 'connection_generation', 'absolute_descriptor_version')
    if any(status.get(k) is None for k in keys) or not state.get('instance_id'):
        raise RuntimeError('missing connection identity')
    return {'instance_id': state['instance_id'], **{k: status[k] for k in keys}}


def preflight(output):
    state = health()
    status = state.get('status') or {}
    if state.get('fault') or state.get('status_stale') or not all(status.get(k) is True for k in ('connected', 'encrypted', 'absolute_ready')):
        raise RuntimeError('BLE not ready; no action sent')
    request = urllib.request.Request('http://127.0.0.1:8892/frame')
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    deadline = time.monotonic() + 65
    while True:
        with opener.open(request, timeout=5) as response:
            data = response.read()
            frame_id = response.headers.get('X-Frame-Id')
            frame_time = float(response.headers.get('X-Frame-Time', '0'))
            valid = response.status == 200 and bool(data) and bool(frame_id) and response.headers.get('X-Frame-Snapshot-Version') == '1'
            if valid and 0 <= time.time() - frame_time <= 3:
                break
        if time.monotonic() >= deadline:
            raise RuntimeError('no recent snapshot v1 frame within 65 seconds; no action sent')
        time.sleep(.2)
    state = health()
    status = state.get('status') or {}
    if state.get('fault') or state.get('status_stale') or not all(status.get(k) is True for k in ('connected', 'encrypted', 'absolute_ready')):
        raise RuntimeError('BLE not ready after frame wait; no action sent')
    image = cv2.imdecode(np.frombuffer(data, dtype=np.uint8), cv2.IMREAD_COLOR)
    if image is None or image.shape[:2] != (1080, 496):
        raise RuntimeError('unexpected frame geometry; no action sent')
    connection_context(state)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.with_suffix('.before.jpg').write_bytes(data)
    output.with_suffix('.before.json').write_text(json.dumps({'frame_id': frame_id, 'frame_time': frame_time, 'health': state}, indent=2), encoding='utf-8')
    return frame_id


def validate_result(result, before_id):
    frame = result.get('frame') or {}
    if (result.get('error') or result.get('capture_error') or
            result.get('input_state') != 'reports_sent' or result.get('released') is not True or
            result.get('completed_steps') != 1):
        raise RuntimeError('movement or release unconfirmed; do not replay')
    before_source, before_sequence = before_id.rsplit(':', 1)
    after_source, after_sequence = frame.get('frame_id', '').rsplit(':', 1)
    if before_source != after_source or int(after_sequence) <= int(before_sequence):
        raise RuntimeError('capture source changed or frame did not advance')
    if not frame.get('pointer_response_verified') and not frame.get('frame_time', 0) > result.get('settled_at', float('inf')):
        raise RuntimeError('post-settlement frame required')
    return frame


def main():
    p = argparse.ArgumentParser(description=__doc__)
    group = p.add_mutually_exclusive_group(required=True)
    group.add_argument('--command', nargs=2, type=int)
    group.add_argument('--target', nargs=2, type=int)
    p.add_argument('--training', type=Path)
    p.add_argument('--settle-ms', type=int, choices=[0,100,300,500,1000], default=100)
    p.add_argument('--scene', required=True, choices=['home', 'notifications', 'control_center'])
    p.add_argument('--output', type=Path, required=True, help='new output prefix; files must not exist')
    args = p.parse_args()
    if args.target:
        if not args.training: p.error('--target requires --training')
        data = json.loads(args.training.read_text(encoding='utf-8'))
        _, axes, _ = fit_training(data['training'])
        command = [round((args.target[i]-axes[i]['offset'])/axes[i]['slope']) for i in (0,1)]
    else:
        if args.training: p.error('--training only applies to --target')
        command = args.command
    if not (0 <= command[0] <= 495 and 0 <= command[1] <= 1079):
        p.error('command must be within the screenshot')
    output = args.output.resolve()
    if any(output.parent.glob(output.name + '.*')):
        p.error('output prefix already exists')
    if output.suffix:
        p.error('output must be a suffix-free prefix')
    before_id = preflight(output)
    before = json.loads(output.with_suffix('.before.json').read_text(encoding='utf-8'))
    image_path = output.with_suffix('.jpg')
    result = act([{'op': 'move_to', 'x': command[0], 'y': command[1]}], image_path,
                 coordinate_backend='native_hid', diagnostic_uncalibrated=True,
                 settle_ms=args.settle_ms, timeout_seconds=20)
    frame = validate_result(result, before_id)
    if not image_path.exists():
        raise RuntimeError('missing post-action image')
    after_health = health()
    if connection_context(after_health) != connection_context(before['health']):
        raise RuntimeError('connection changed during sample; discard sample')
    image_bytes = image_path.read_bytes()
    decoded = cv2.imdecode(np.frombuffer(image_bytes, dtype=np.uint8), cv2.IMREAD_COLOR)
    if decoded is None or decoded.shape[:2] != (1080, 496):
        raise RuntimeError('post-action geometry changed')
    if frame['frame_id'].rsplit(':', 1)[0] != before_id.rsplit(':', 1)[0]:
        raise RuntimeError('capture source changed')
    sample = {'action_id': result['request_id'], 'settle_ms': args.settle_ms, 'scene': args.scene, 'connection': connection_context(after_health),
              'geometry': [496, 1080], 'image_sha256': hashlib.sha256(image_bytes).hexdigest(),
              'command': command, 'observed': None, 'action_time': result['input_finished_at'],
              'frame_time': frame['frame_time'], 'frame_id': frame['frame_id'],
              'before_image': str(output.with_suffix('.before.jpg')), 'after_image': str(image_path)}
    if args.target: sample['target'] = args.target
    output.with_suffix('.sample.json').write_text(json.dumps(sample, indent=2), encoding='utf-8')
    print(json.dumps(sample, indent=2))


if __name__ == '__main__':
    main()
