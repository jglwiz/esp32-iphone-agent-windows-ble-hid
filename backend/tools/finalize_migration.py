"""Final manifest cleanup. Run only after main-agent acceptance; backup first."""
from pathlib import Path
import argparse, hashlib, json, re, zipfile, time
ROOT=Path(__file__).resolve().parents[2]
BACKEND=ROOT/'backend'

def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--apply',action='store_true')
    args=parser.parse_args()
    entries=[]
    for name in ('migration-manifest.json','migration-extras.json'):
        entries+=json.loads((BACKEND/'doc'/name).read_text(encoding='utf-8-sig'))
    targets=[]
    for entry in entries:
        path=Path(entry['source']).resolve()
        # upstream-windows is an independent project; never delete it here.
        if not path.is_relative_to(ROOT):continue
        if path.is_symlink() or not path.is_file():raise RuntimeError(f'Unexpected source: {path}')
        if hashlib.sha256(path.read_bytes()).hexdigest()!=entry['sha256']:raise RuntimeError(f'Source changed: {path}')
        if path.parts[len(ROOT.parts)] not in ('pc','tests'):raise RuntimeError(f'Outside approved source area: {path}')
        targets.append(path)
    legacy_plan=ROOT/'pc'/'doc'/'unified-backend-plan.md'
    if legacy_plan.exists():targets.append(legacy_plan)
    for cache in (ROOT/'pc'/'__pycache__',ROOT/'tests'/'__pycache__'):
        for path in cache.glob('*.pyc'):
            if not path.resolve().is_relative_to(ROOT) or path.is_symlink():raise RuntimeError('Cache path escaped repository')
            targets.append(path.resolve())
    print(json.dumps({'delete_count':len(targets),'paths':[str(p.relative_to(ROOT)) for p in targets]},indent=2))
    if not args.apply:return
    folder=BACKEND/'.runtime'/'migration-backup'/time.strftime('%Y%m%d-%H%M%S')
    folder.mkdir(parents=True)
    archive=folder/'legacy-files.zip'
    with zipfile.ZipFile(archive,'w',zipfile.ZIP_DEFLATED) as out:
        for path in targets:out.write(path,str(path.relative_to(ROOT)))
    with zipfile.ZipFile(archive) as saved:
        if saved.testzip():raise RuntimeError('Backup integrity failure')
        for path in targets:
            if saved.read(str(path.relative_to(ROOT)).replace('\\','/'))!=path.read_bytes():raise RuntimeError('Backup bytes differ')
    # Check all resolved targets before any deletion, including uncommitted sources.
    for path in targets:
        if not path.resolve().is_relative_to(ROOT):raise RuntimeError('Resolved target escaped repository')
    for path in targets:path.unlink()
    for directory in (ROOT/'pc'/'calibration_page',ROOT/'pc'/'doc',ROOT/'pc'/'__pycache__',ROOT/'tests'/'__pycache__',ROOT/'pc'):
        if directory.exists() and not any(directory.iterdir()):directory.rmdir()
    (folder/'deleted.json').write_text(json.dumps([str(p) for p in targets],indent=2),encoding='utf-8')
    print('Verified backup: '+str(archive))

if __name__=='__main__':main()
