"""Hardware protocol checks. Only status/release commands; no key or pointer input."""
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'pc'))
from bridge import Bridge


def receive(bridge, request_id, timeout=2):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        data = bridge._read()
        if data and data.get('id') == request_id and data.get('state') != 'event':
            return data
    raise AssertionError(f'No response for id={request_id}')


def main():
    project = Path(__file__).resolve().parents[1]
    boot_ids = []
    for _ in range(3):
        with Bridge(log=str(project / '.runtime' / 'protocol-smoke.jsonl')) as b:
            boot_ids.append(b.boot_id)
            status = b.request('status')
            assert status['firmware'].startswith(('0.1.', '0.2.'))
            assert 0 < status['keyboard_handle'] < 65535
            assert 0 < status['mouse_handle'] < 65535
            assert status['keyboard_handle'] != status['mouse_handle']
            b.io.write(b'{bad json}\n')
            assert receive(b, 0)['error'] == 'invalid_json'
            b.io.write(b'x' * 513 + b'\n')
            assert receive(b, 0)['error'] == 'frame_too_long'
            b.counter += 1
            payload = {'v': 1, 'session': b.session, 'id': b.counter, 'op': 'release_all'}
            wire = json.dumps(payload).encode() + b'\n'
            b.io.write(wire[:11]); time.sleep(0.02); b.io.write(wire[11:])
            response = receive(b, b.counter)
            assert response['state'] == 'ok'
            b.io.write(wire)
            assert receive(b, b.counter) == response
            payload['op'] = 'cancel'
            b.io.write(json.dumps(payload).encode() + b'\n')
            assert receive(b, b.counter)['error'] == 'id_conflict'
            b.request('status')
    assert len(set(boot_ids)) == 1, boot_ids
    print(json.dumps({'passed': True, 'open_close_cycles': 3, 'boot_id': boot_ids[0],
                      'checks': ['malformed_json', 'oversized_frame', 'fragmentation',
                                 'duplicate_release', 'conflicting_id', 'no_dtr_reset']}))


if __name__ == '__main__':
    main()
