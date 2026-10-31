"""Save a fresh AirPlay frame from the existing capture worker without restarting it."""
import argparse
import json
import time
import urllib.request
from pathlib import Path


def snapshot(path: str):
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    with opener.open('http://127.0.0.1:8891/frame', timeout=3) as response:
        if response.status != 200:
            raise RuntimeError(f'No live frame: HTTP {response.status}')
        timestamp = float(response.headers['X-Frame-Time'])
        age = time.time() - timestamp
        if not -1 <= age <= 5:
            raise RuntimeError(f'Stale frame: {age:.1f}s; request a new phone frame')
        data = response.read()
    target = Path(path).resolve()
    target.write_bytes(data)
    evidence = {'path': str(target), 'frame_time': timestamp, 'age_seconds': age}
    target.with_suffix('.json').write_text(json.dumps(evidence, indent=2), encoding='utf-8')
    return evidence


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('path')
    print(json.dumps(snapshot(parser.parse_args().path)))
