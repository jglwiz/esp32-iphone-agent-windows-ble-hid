"""HTTP client; does not open the serial port. Saves combined action/frame evidence."""
import argparse
import base64
import json
from pathlib import Path
import urllib.request
import uuid


def act(steps, output, *, request_id=None, settle_ms=700, timeout_seconds=20,
        base_url='http://127.0.0.1:8892', coordinate_backend=None, capture=True, diagnostic_uncalibrated=False, manual_visual=False, reviewed_action_id=None):
    payload = {'request_id': request_id or uuid.uuid4().hex, 'steps': steps,
               'capture': capture, 'settle_ms': settle_ms, 'timeout_seconds': timeout_seconds}
    if coordinate_backend is not None:
        payload['coordinate_backend'] = coordinate_backend
    if diagnostic_uncalibrated:
        payload['diagnostic_uncalibrated'] = True
    if manual_visual:
        payload['manual_visual'] = True
    if reviewed_action_id is not None:
        payload['reviewed_action_id'] = reviewed_action_id
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


def move_to(x, y, output, **kwargs):
    return act([{'op':'move_to','x':x,'y':y}], output, **kwargs)


def click_at(x, y, output, **kwargs):
    return act([{'op':'click_at','x':x,'y':y}], output, **kwargs)


def _json_request(path, *, base_url='http://127.0.0.1:8892', payload=None):
    request = urllib.request.Request(base_url.rstrip('/') + path,
        data=None if payload is None else json.dumps(payload).encode(),
        headers={'Content-Type':'application/json'}, method='GET' if payload is None else 'POST')
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    with opener.open(request, timeout=10) as response:
        return json.load(response)


def health(*, base_url='http://127.0.0.1:8892'):
    return _json_request('/health', base_url=base_url)


def start_calibration(request_id, mode, *, base_url='http://127.0.0.1:8892'):
    return _json_request('/calibrations', base_url=base_url, payload={'request_id':request_id,'mode':mode})


def get_calibration(job_id, *, base_url='http://127.0.0.1:8892'):
    return _json_request('/calibrations/'+job_id, base_url=base_url)


def cancel_calibration(job_id, *, base_url='http://127.0.0.1:8892'):
    return _json_request('/calibrations/'+job_id+'/cancel', base_url=base_url, payload={})


def review_visual_calibration(review, *, base_url='http://127.0.0.1:8892'):
    """Submit explicit observations, never client-provided mapping coefficients."""
    return _json_request('/visual-calibrations/review',base_url=base_url,payload=review)


def get_visual_calibration(request_id, *, base_url='http://127.0.0.1:8892'):
    from urllib.parse import quote
    return _json_request('/visual-calibrations/'+quote(request_id,safe=''),base_url=base_url)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--steps', help='JSON array; [] observes without input')
    parser.add_argument('--output')
    parser.add_argument('--request-id')
    parser.add_argument('--operation', choices=['action','health','start-calibration','get-calibration','cancel-calibration'], default='action')
    parser.add_argument('--coordinate-backend', choices=['native_hid','visual_relative'])
    parser.add_argument('--no-capture', action='store_true')
    parser.add_argument('--diagnostic-uncalibrated', action='store_true')
    parser.add_argument('--mode', choices=['verify','calibrate'])
    parser.add_argument('--job-id')
    parser.add_argument('--base-url', default='http://127.0.0.1:8892')
    args=parser.parse_args()
    if args.operation=='action':
        if args.steps is None or not args.output:parser.error('action requires --steps and --output')
        result=act(json.loads(args.steps),args.output,request_id=args.request_id,coordinate_backend=args.coordinate_backend,capture=not args.no_capture,diagnostic_uncalibrated=args.diagnostic_uncalibrated,base_url=args.base_url)
    elif args.operation=='health':result=health(base_url=args.base_url)
    elif args.operation=='start-calibration':
        if not args.request_id or not args.mode:parser.error('start-calibration requires --request-id and --mode')
        result=start_calibration(args.request_id,args.mode,base_url=args.base_url)
    else:
        if not args.job_id:parser.error('operation requires --job-id')
        result=(get_calibration if args.operation=='get-calibration' else cancel_calibration)(args.job_id,base_url=args.base_url)
    print(json.dumps(result))
