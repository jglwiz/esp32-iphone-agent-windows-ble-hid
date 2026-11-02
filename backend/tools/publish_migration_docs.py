"""Publish reviewed migration docs and client skill after acceptance."""
from pathlib import Path
import re, shutil, time
ROOT=Path(__file__).resolve().parents[2]
BACKEND=ROOT/'backend'
BACKUP=BACKEND/'.runtime'/'migration-doc-backup'/time.strftime('%Y%m%d-%H%M%S')
BACKUP.mkdir(parents=True)
files=[ROOT/'README.md',ROOT/'README_CN.md',ROOT/'AGENTS.md',ROOT/'doc'/'user'/'usage.md']
# Rewrite actual file links throughout maintained docs; historical prose remains history.
for path in list(ROOT.glob('*.md'))+list((ROOT/'doc').rglob('*.md'))+list((ROOT/'tests').rglob('*.md')):
    text=path.read_text(encoding='utf-8-sig')
    text=re.sub(r'(\]\([^\n)]*)pc/doc/',r'\1backend/doc/',text)
    for old,new in {'bridge':'esp32/serial_transport','frame_capture':'airplay/frames'}.items():
        text=text.replace('pc/'+old+'.py','backend/src/iphone_backend/'+new+'.py')
    if path in files:
        dest=BACKUP/path.relative_to(ROOT)
        dest.parent.mkdir(parents=True,exist_ok=True)
        shutil.copy2(path,dest)
    if path.name in ('README.md','README_CN.md') or path==ROOT/'doc'/'user'/'usage.md':
        text=text.replace('pc\\bridge.py','-m iphone_backend.esp32.serial_transport')
        text=text.replace('python -m venv .venv','python -m venv backend/.venv')
        text=text.replace('.\\.venv\\Scripts', '.\\backend\\.venv\\Scripts')
        text=text.replace('pip install -r pc\\requirements.txt','pip install -e backend')
        text=text.replace('pc/requirements.txt','backend/pyproject.toml')
        text=text.replace('Python 3.10','Python 3.11')
        text=text.replace('-m unittest discover -s tests -v','-m unittest discover -s backend/tests -v')
        text=text.replace('tests\\serial_smoke.py','backend\\tools\\serial_smoke.py')
        text=text.replace('`pc/requirements-service.txt`','`backend/pyproject.toml`').replace('`python pc/service.py`','`python -m iphone_backend`')
        text=text.replace('| pc/ |','| backend/ |')
    if path.name=='README.md':
        text=text.replace('visual feedback uses a separately provided AirPlay capture service.','the backend includes AirPlay capture and process management.')
        text=text.replace('Python serial CLI, dependencies, and optional screenshot client','Unified API, ESP32/AirPlay modules, package, tests and docs')
        text=text.replace('The optional screenshot service must still be provided separately.','The backend includes the capture worker; UxPlay remains an external runtime configured with UXPLAY_DIR.')
        text=text.replace('Install `backend/pyproject.toml` and run','Install with `python -m pip install -e backend` and run')
        text += '\n## Unified backend\n\nUse [backend setup](backend/doc/usage.md) to configure absolute runtime paths and start `python -m iphone_backend`. Port 8892 is the only control/frame API; 8891 and 8894 are internal. The manager starts capture and supervises the API. Do not run the direct serial CLI while the API owns the device. See [migration evidence](backend/doc/implementation-status.md).\n'
    elif path.name=='README_CN.md':
        text=text.replace('Python 串口 CLI、依赖和可选截图客户端','统一 API、ESP32/AirPlay 子模块、包、测试与文档')
        text=text.replace('安装 `backend/pyproject.toml` 后运行','通过 `python -m pip install -e backend` 安装后运行')
        text += '\n## 统一后端\n\n按 [Backend 使用说明](backend/doc/usage.md) 配置绝对运行路径，通过 `python -m iphone_backend` 启动。8892 是唯一控制和截图 API；8891/8894 仅供内部使用。主控负责取帧启动和 API 监督，UxPlay 为外部运行时。API 占有设备时不要运行直接串口 CLI。迁移证据见 [实施记录](backend/doc/implementation-status.md)。\n'
    elif path==ROOT/'doc'/'user'/'usage.md':
        text=text.replace('先按根目录 README 安装 backend/pyproject.toml，再从项目根目录执行：','当前日常入口为 [统一 Backend](../../backend/doc/usage.md)，通过 8892 获取控制与画面。下列直接串口 CLI 仅在 API 已释放串口后用于诊断；不能与常驻服务并行。先按根目录 README 安装 backend 包，再从项目根目录执行：')
        text=re.sub(r'## 可选的截图服务[\s\S]*$', '## 统一截图服务\n\n`python -m iphone_backend.snapshot <文件路径>` 从 8892/frame 获取图像并检查原始帧时间；人工看图可直接请求最新快照，动作证据仍要求新鲜帧。取帧实现随 backend 包安装，由主控统一启动；UxPlay 路径通过 UXPLAY_DIR 指定。不要把 HTTP 成功或串口 sent 当作手机操作成功。\n',text)
    elif path.name=='AGENTS.md':
        text=text.replace('## 本子项目导航','## 本子项目导航\n\n当前统一后端位于 backend/：[使用说明](backend/doc/usage.md)、[架构](backend/doc/architecture.md)、[迁移实施记录](backend/doc/implementation-status.md)。唯一启动入口为 python -m iphone_backend；8892 提供控制和截图。旧 pc/ 源码已按清单迁移，下面的历史文档以当前说明为准。')
    if text!=path.read_text(encoding='utf-8-sig'):
        dest=BACKUP/path.relative_to(ROOT)
        dest.parent.mkdir(parents=True,exist_ok=True)
        if not dest.exists():shutil.copy2(path,dest)
        path.write_text(text,encoding='utf-8')
skill=Path('C:/Users/J/.codex/skills/iphone/SKILL.md')
shutil.copy2(skill,BACKUP/'iphone-SKILL.md')
skill.write_text((BACKEND/'doc'/'iphone-skill.md').read_text(encoding='utf-8-sig'),encoding='utf-8')
print('Documentation and skill published; backups: '+str(BACKUP))
