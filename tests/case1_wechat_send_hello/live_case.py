"""Evidence recorder for agent-operated iPhone tests; never sends HID input."""
import argparse
import json
import time
import uuid
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

CASE = Path(__file__).resolve().parent


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='command', required=True)
    init = sub.add_parser('init')
    init.add_argument('--model', default='gpt-6.1-terra')
    mark = sub.add_parser('mark')
    mark.add_argument('run')
    mark.add_argument('stage', choices=['start', 'home', 'wechat', 'recipient', 'input_method', 'ascii_probe', 'input_ready', 'draft', 'sent', 'stop'])
    mark.add_argument('--image', required=True)
    mark.add_argument('--note', default='')
    args = parser.parse_args()
    if args.command == 'init':
        run_id = datetime.now(ZoneInfo('Asia/Taipei')).strftime('%Y%m%d_%H%M%S') + '_' + uuid.uuid4().hex[:4]
        folder = CASE / 'runs' / run_id
        folder.mkdir(parents=True)
        data = {'id': run_id, 'message': 'test_' + run_id, 'recipient': '文件传输助手',
                'operator_model': args.model, 'case_version': 2, 'input_method': 'hid_keys', 'required_keyboard_layout': 'English (US)', 'created_at': time.time(), 'result': 'pending_main_review'}
        (folder / 'run.json').write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding='utf-8')
        print(json.dumps({'folder': str(folder), **data}, ensure_ascii=False))
        return
    folder = Path(args.run).resolve()
    folder.relative_to((CASE / 'runs').resolve())
    if not (folder / 'run.json').is_file():
        raise ValueError('Not an initialized benchmark run')
    events_file = folder / 'events.jsonl'
    stages = [json.loads(line)['stage'] for line in events_file.read_text(encoding='utf-8').splitlines()] if events_file.exists() else []
    prerequisites = {'input_method': 'recipient', 'ascii_probe': 'input_method',
                     'input_ready': 'ascii_probe', 'draft': 'input_ready', 'sent': 'draft'}
    required = prerequisites.get(args.stage)
    if required and required not in stages:
        raise ValueError(f'Missing required stage: {required}; visual review is still mandatory')
    picture = Path(args.image).resolve()
    picture.relative_to(folder)
    if not picture.is_file():
        raise ValueError('Evidence image missing')
    metadata = json.loads(picture.with_suffix('.json').read_text(encoding='utf-8'))
    event = {'stage': args.stage, 'recorded_at': time.time(), 'frame_time': metadata['frame_time'],
             'image': str(picture), 'note': args.note}
    with (folder / 'events.jsonl').open('a', encoding='utf-8') as stream:
        stream.write(json.dumps(event, ensure_ascii=False) + '\n')
    print(json.dumps(event, ensure_ascii=False))


if __name__ == '__main__':
    main()
