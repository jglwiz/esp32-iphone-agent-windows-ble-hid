"""Copy the current source snapshot without modifying any original file."""
from pathlib import Path
import hashlib
import json
import re
import shutil

ROOT = Path(__file__).resolve().parents[2]
DEST = ROOT / 'backend'
if (DEST / 'doc' / 'migration-manifest.json').exists():
    raise SystemExit('Snapshot already exists; refusing to overwrite migrated implementation')
PKG = DEST / 'src' / 'iphone_backend'
mapping = {p.stem: p.stem for p in (ROOT / 'pc').glob('*.py')}
mapping.update(bridge='esp32.serial_transport', frame_capture='airplay.frames')

def rewrite(text):
    for old, new in mapping.items():
        text = re.sub(r'(?m)^(\s*)from ' + old + r' import ',
                      lambda m: m[1] + 'from iphone_backend.' + new + ' import ', text)
        text = re.sub(r'(?m)^(\s*)import ' + old + r'\s*$',
                      lambda m: m[1] + 'import iphone_backend.' + new + ' as ' + old, text)
    return text

manifest = []
def record(p):
    manifest.append({'source': str(p), 'sha256': hashlib.sha256(p.read_bytes()).hexdigest()})

for sub in ('', 'esp32', 'airplay'):
    folder = PKG / sub
    folder.mkdir(parents=True, exist_ok=True)
    (folder / '__init__.py').touch()
for source in (ROOT / 'pc').glob('*.py'):
    record(source)
    target = PKG / (mapping[source.stem].replace('.', '/') + '.py')
    text = rewrite(source.read_text(encoding='utf-8-sig'))
    text = text.replace("ROOT = Path(__file__).resolve().parents[1]", 'from iphone_backend.config import PROJECT_ROOT as ROOT')
    if source.stem == 'service':
        text = text.replace("runtime or ROOT / '.runtime' / 'service'", "runtime or __import__('iphone_backend.config', fromlist=['RUNTIME_DIR']).RUNTIME_DIR / 'service'")
    target.write_text(text, encoding='utf-8')
shutil.copytree(ROOT/'pc'/'calibration_page', PKG/'calibration_page', dirs_exist_ok=True)
upstream = ROOT.parent/'upstream-windows'/'server'/'hidagent'
for name in ('capture_worker', 'frame_snapshot', 'screen_pipe', 'screen_win', 'screen'):
    source = upstream/(name+'.py')
    record(source)
    (PKG/'airplay'/(name+'.py')).write_bytes(source.read_bytes())
(DEST/'tests').mkdir(exist_ok=True)
for source in (ROOT/'tests').glob('test_*.py'):
    record(source)
    text = rewrite(source.read_text(encoding='utf-8-sig'))
    text = re.sub(r'(?m)^sys.path.insert\(.*\)\s*$', '', text)
    (DEST/'tests'/source.name).write_text(text, encoding='utf-8')
(DEST/'doc').mkdir(exist_ok=True)
shutil.copy2(ROOT/'pc'/'doc'/'unified-backend-plan.md', DEST/'doc'/'unified-backend-plan.md')
for parent in (ROOT, ROOT.parent/'upstream-windows'):
    for source in parent.glob('LICENSE*'):
        shutil.copy2(source, DEST/(parent.name+'-'+source.name))
(DEST/'doc'/'migration-manifest.json').write_text(json.dumps(manifest, indent=2), encoding='utf-8')
print(f'Copied source snapshot: {len(manifest)} files; originals unchanged')
