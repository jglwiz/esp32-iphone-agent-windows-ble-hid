"""Bounded movement-only live regression. Never clicks or retries unknown input.

Run only with exclusive phone access. Reset invalidates calibration; perform a
reviewed scope validation afterwards. Evidence stays in the supplied directory.
"""
import argparse
import json
from pathlib import Path
import statistics
import time
import urllib.request
import uuid

from iphone_backend.service_client import health, move_to


POINTS = [(320, 650)] * 4 + [
    (24, 60), (471, 60), (24, 1010), (471, 1010),
    (248, 0), (0, 540), (495, 540), (248, 1079),
]


def save(path, value):
    path.write_text(json.dumps(value, indent=2), encoding='utf-8')


def idle_health():
    value = health()
    if value['fault'] or value['active_id'] or value['queue_size']:
        raise RuntimeError('Controller must be healthy and idle')
    if not value['status'].get('absolute_ready'):
        raise RuntimeError('Absolute pointer not ready')
    return value


def reset(folder):
    before = idle_health()
    token = json.loads((Path(__file__).resolve().parents[1] / '.deployment.json')
                       .read_text(encoding='utf-8'))['BLE_HID_RESTART_TOKEN']
    body = dict(request_id=uuid.uuid4().hex,
                expected_instance_id=before['instance_id'],
                expected_device_id=before['status']['device_id'],
                expected_boot_id=before['status']['boot_id'])
    save(folder / 'reset-before.json', before)
    save(folder / 'reset-request.json', body)
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))

    def call(suffix='', payload=None):
        request = urllib.request.Request('http://127.0.0.1:8892/device/reset' + suffix,
            data=None if payload is None else json.dumps(payload).encode(),
            headers={'Authorization': 'Bearer ' + token, 'Content-Type': 'application/json'})
        with opener.open(request, timeout=5) as response:
            return json.load(response)

    # Request ID is persisted before submission. Exceptions stop, never resubmit.
    call(payload=body)
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        time.sleep(.5)
        result = call('/' + body['request_id'])
        save(folder / 'reset-result.json', result)
        if result['state'] == 'unknown':
            raise RuntimeError('Reset outcome unknown; inspect saved request')
        current = health()
        if (result['state'] == 'device_rebooted' and current['status'].get('absolute_ready')
                and current['status']['boot_id'] != body['expected_boot_id']):
            save(folder / 'reset-after.json', current)
            if current['profile']['verification'] != 'needs_verification':
                raise RuntimeError('Reset did not invalidate calibration')
            return
    raise RuntimeError('Reset readiness deadline expired; do not replay')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', required=True, type=Path)
    parser.add_argument('--rounds', type=int, default=3, choices=range(1, 11))
    parser.add_argument('--idle-seconds', type=int, default=60, choices=range(301))
    parser.add_argument('--reset-between-rounds', action='store_true')
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=False)
    report = dict(started_at=time.time(), rounds=args.rounds,
                  idle_seconds=args.idle_seconds, samples=[], state='running')
    try:
        initial = idle_health()
        save(args.output / 'initial-health.json', initial)
        if initial['profile']['verification'] != 'verified':
            raise RuntimeError('Start with a reviewed, verified profile')
        for cycle in range(args.rounds):
            folder = args.output / f'round-{cycle}'
            folder.mkdir()
            if cycle and args.reset_between_rounds:
                reset(folder)
            print(f'round {cycle}: idle {args.idle_seconds}s', flush=True)
            time.sleep(args.idle_seconds)
            idle_health()
            for index, (x, y) in enumerate(POINTS):
                result = move_to(x, y, folder / f'move-{index:02}.jpg',
                    diagnostic_uncalibrated=bool(cycle and args.reset_between_rounds))
                check = (result.get('absolute') or [{}])[-1].get('pointer_check', {})
                passed = (not result.get('error') and not result.get('capture_error')
                          and result.get('released') and check.get('state') == 'passed')
                record = dict(round=cycle, index=index, target=[x, y], passed=bool(passed),
                    duration_seconds=result['finished_at'] - result['started_at'],
                    center=check.get('center'), evidence=check.get('evidence_directory'))
                report['samples'].append(record)
                save(args.output / 'summary.json', report)
                print(json.dumps(record), flush=True)
                if not passed:
                    raise RuntimeError('Movement failed; no automatic action replay')
        report['state'] = 'passed'
    except Exception as exc:
        report.update(state='failed', error=str(exc))
        raise
    finally:
        report['finished_at'] = time.time()
        durations = sorted(row['duration_seconds'] for row in report['samples'])
        if durations:
            report.update(median_seconds=statistics.median(durations),
                          max_seconds=max(durations))
        save(args.output / 'summary.json', report)


if __name__ == '__main__':
    main()
