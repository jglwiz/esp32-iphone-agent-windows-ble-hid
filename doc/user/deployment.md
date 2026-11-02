# 从干净克隆部署（供使用者与 AI Agent）

目标是 Windows 上可用的 8892 控制与截图 API。以下命令从仓库根目录执行。用户可以把这个文件交给 AI；AI 应逐项完成并报告结果，不能把进程启动等同于部署成功。

## 给 AI 的任务

> 请按 doc/user/deployment.md 部署本仓库：检查前提、安装独立 Python 环境、配置 UxPlay、生成本机部署配置并启动后端，最后运行只读验收。保留现有配置和运行服务；遇到已有实例先识别，不重复启动或强杀。需要手机配对、投屏时告诉我具体步骤。固件刷写前核对开发板、端口和备份。报告已完成、待人工操作、未验证项，不输出管理 token。

项目 AGENTS.md 对测试模型与分工有要求，AI 执行测试时也须遵守。未安装该模型的工具应明确说明限制。

## 1. 前提与环境

- 64 位 Windows，PowerShell 5.1+，Git，Python 3.11+，网络与数 GB 磁盘空间。
- 本项目已验证的 ESP32-S3 板型为 CH343 串口、16 MB Flash / 8 MB OPI PSRAM。板子 USB 接 PC，不接 iPhone。
- iPhone 与 PC 在可互通的局域网中；访客 Wi-Fi、客户端隔离或防火墙可能阻断 AirPlay 发现与连接。

```powershell
git clone https://github.com/jglwiz/esp32-iphone-agent-windows-ble-hid.git
cd esp32-iphone-agent-windows-ble-hid
python --version
Get-NetTCPConnection -State Listen -ErrorAction SilentlyContinue |
    Where-Object { $_.LocalPort -in 8891,8892,8894 }
```

若端口已占用，先识别已有部署并检查其健康状态；不要继续首次启动。8892 对外提供本机 API，8891/8894 为内部服务，不要为了投屏开放这些 HTTP 端口到局域网。

缺 Python 时，可使用官方 Python 安装程序，或 `winget install --exact --id Python.Python.3.11`，随后重新打开终端。脚本的 `-Python` 参数可指定 Python 可执行文件绝对路径。不需要全局安装 Python 依赖。

## 2. 固件与手机准备

若板子已有匹配固件，跳过刷写。否则先运行只编译命令；工具链自动安装到仓库内：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\build.ps1
```

按 [安装与恢复](usage.md) 核对板型、真实串口并完成该板备份后，再使用 `-Upload -Port COMx`。不能直接照抄他人的 COM4 或备份文件。未显示串口时，从板卡厂商获取相应 CH343 驱动。

需要用户在手机上完成：开启蓝牙并配对 **S3-Click-Buttons**；在“设置 → 辅助功能 → 触控 → 辅助触控”开启辅助触控。手机正向竖直固定、开启竖屏方向锁。更新固件后的蓝牙名称可能变化，详见 [使用说明](usage.md)。

## 3. 准备 UxPlay

从 [UxPlay Windows 官方 Releases](https://github.com/leapbtw/uxplay-windows/releases) 获取适合 Windows x64 的完整安装包或便携包，保留全部 DLL、GStreamer 插件及目录结构，不能只复制 exe。安装步骤以 [上游说明](https://github.com/leapbtw/uxplay-windows) 为准；其 Bonjour/mDNS 服务安装可能需要管理员操作。

找到实际包含 `uxplay-windows.exe` 的目录，例如 `C:\Apps\uxplay-windows`。后端当前集成使用 `%APPDATA%\leapbtw\uxplay-windows\arguments.txt` 指向生成的 rc 文件，并通过 GStreamer 命名管道接收 I420 画面。存在同名 exe 仅通过路径检查，不能证明任意上游版本均兼容。上游新版与本机既有版本的兼容性须通过第 6 步实际投屏确认；当前没有随仓库发布、固定哈希并经过干净部署验收的 UxPlay 二进制包。

若已有 UxPlay 或取帧正在运行，保留会话；记录现有路径及配置，不能为首次部署调试直接重启它。后端取帧会写入上述用户级 arguments 文件及 `HIDAGENT_DATA/uxplayrc.conf`，首次启动前备份已有文件。旧会话未采用命名管道配置时，不能把“复用了进程”视为截图已接通，需要安排用户重新投屏。

## 4. 安装与初始化后端

将下面路径替换为实际 UxPlay 安装目录：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\setup.ps1 -UxPlayDir 'C:\Apps\uxplay-windows'
```

脚本创建 `backend/.venv`，按 `backend/pyproject.toml` 安装依赖，首次生成忽略 Git 的 `backend/.deployment.json`。配置包含仓库绝对路径、`backend/.runtime` 数据路径及随机管理 token；不自动启动、不打开串口、不刷固件。重复运行会复用环境并保留配置；已有配置时传入的新路径不会覆盖旧值。不要在服务正在运行或执行动作时重装依赖。

可通过 `-PhoneId 'my-iphone'` 设置本地手机标识；它不是自动识别出的手机序列号。不默认配置原生坐标校准，也不复制其他手机的 profile。`BLE_HID_DEVICE_ID` 应从实际设备状态核实，仅在启用原生坐标方案时按相关校准文档配置。首次部署无需 `BLE_HID_CALIBRATION_HOST`；自动校准另需手机可达的 PC 地址与对应端口。

移动整个仓库会使已保存的绝对路径失效，应停机后本地更新配置。不要把 token、整个配置或含输入文本的日志提交 Git 或贴入聊天。

## 5. 启动与投屏

```powershell
.\backend\.venv\Scripts\python.exe backend\tools\deploy.py start
```

这是首次启动入口；`prepare/handover` 专用于旧版本迁移，不用于全新电脑。start 发现 8891/8892/8894 已占用会拒绝，不能循环重试。返回 PID 只证明启动器已运行。主控持有单实例锁，监督 API 与取帧；日志位于 `backend/.runtime/deployment/manager.log`、`backend/.runtime/backend.log` 与 `backend/.runtime/airplay.log`。

手机打开“控制中心 → 屏幕镜像”，选择 **iPhone-Agent**。如 Windows 弹出网络访问请求，只为所需的可信网络允许接收器。没有广播或画面时检查局域网、mDNS/Bonjour 服务、防火墙以及上游版本的配置兼容性；不要关闭整个防火墙。

后端自动选择唯一 CH343；多个候选时当前统一后端没有 COM 参数入口，应先排除其他候选，不能随机选择。直接串口 CLI 的 `--port` 不会配置常驻后端。服务持有串口时不要并行运行串口 CLI。

## 6. 只读验收

投屏开始后执行：

```powershell
.\backend\.venv\Scripts\python.exe backend\tools\bootstrap.py check
```

脚本认证主控与 API，核对运行目录、后端健康、键盘与鼠标 BLE readiness、5 秒内的 496×1080 JPEG，并报告设备 ID。全部通过返回 0；缺配置、服务、BLE 或新鲜画面返回非零。不会点击、移动、输入或重置设备。HTTP 检查失败可能只显示错误类型；AI 应读取本地日志定位，不打印配置中的 token。

只读检查通过仍不代表输入效果已验证。查看新截图，确认手机画面正确；若用户已授权输入验证，再在安全空白区域做小幅无点击移动，通过动作后截图观察实际方向与落点。未经这一步，不宣称坐标 API 可用。手机朝向限制见 README，原生绝对定位与校准须另行验收。

只有完成基础验收后，如用户需要开机启动，才运行 `backend/tools/install_startup.ps1`。日常恢复及主控更新见 [Backend 使用说明](../../backend/doc/usage.md)，不要用重复 start 代替诊断。

AI 的交付应记录 Python/依赖安装结果、UxPlay 来源与版本、固件版本、服务检查结果及未完成的手机操作。未在新电脑上完成真实投屏前，状态应为“环境已准备，实机验收待完成”。
