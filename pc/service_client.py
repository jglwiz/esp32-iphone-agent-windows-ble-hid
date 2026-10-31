"""HTTP client; does not open the serial port. Saves combined action/frame evidence."""
import argparse
import base64
import json
from pathlib import Path
import urllib.request
import uuid


def act(steps, output, *, request_id=None, settle_ms=700, timeout_seconds=20,
        base_url='http://127.0.0.1:8892'):
    payload = {'request_id': request_id or uuid.uuid4().hex, 'steps': steps,
               'capture': True, 'settle_ms': settle_ms, 'timeout_seconds': timeout_seconds}
    path = Path(output).resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    # Persist ID before transmission so uncertain HTTP outcomes can be investigated.
    path.with_suffix('.request.json').write_text(json.dumps(payload, indent=2), encoding='utf-8')
    request = urllib.request.Request(base_url.rstrip('/')+'/action',
        data=json.dumps(payload).encode(), headers={'Content-Type': 'application/json'}, method='POST')
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    with opener.open(request, timeout=timeout_seconds+5) as response:
        result = json.load(response)
    frame = result.get('frame')
    if frame:
        path.write_bytes(base64.b64decode(frame.pop('jpeg_base64')))
        path.with_suffix('.json').write_text(json.dumps({**frame, 'path': str(path)}, indent=2), encoding='utf-8')
    path.with_suffix('.result.json').write_text(json.dumps(result, indent=2), encoding='utf-8')
    return result


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--steps', required=True, help='JSON array; [] observes without input')
    parser.add_argument('--output', required=True)
    parser.add_argument('--request-id')
    args = parser.parse_args()
    print(json.dumps(act(json.loads(args.steps), args.output, request_id=args.request_id)))
