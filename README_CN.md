# ESP32 iPhone Agent for Windows — BLE HID

[English](README.md) | 简体中文

通过 ESP32-S3 BLE HID 和 AirPlay，让 Windows 上的 AI Agent 观察并操作 iPhone。

项目标识：`esp32-iphone-agent-windows-ble-hid`。当前实现提供串口到 BLE 的键鼠控制桥接和 Agent 实机测试；统一 Backend 已包含 AirPlay 取帧和进程管理。

**控制链路：PC → USB 串口 → ESP32-S3 → BLE HID → iPhone。** 板子 USB 接 PC；iPhone 通过蓝牙接收输入。视觉反馈可另接 AirPlay，BLE HID 本身不返回屏幕画面。

## 当前状态

固件 **0.4.1** 增加独立按钮报告，Backend 提供原地 `click()`，不发送坐标；设备名为 `S3-Click-Buttons`。当前实机点击事件通过，移动仍有方向旋转，不能宣称坐标 API 稳定。使用与边界见 [Backend 说明](backend/doc/usage.md#原地点击) 和 [按钮设计](firmware/serial_ble_hid/doc/mouse-buttons.md)。下面的旧版本验收保留为历史记录。

固件 0.2.0 已在现有微雪 ESP32-S3 与 iPhone 17e 上验证键盘输入、退格、鼠标移动、点击、拖动和双向滚轮。完整自动重连、锁屏恢复、长期稳定性和坐标校准尚未完成。文本受手机硬件输入法影响，ASCII 下划线曾被中文输入法转换为长横线。完整证据与边界见 [验证记录](doc/dev/validation.md)。

当前主要验证环境为 Windows、CH343 串口、16 MB Flash / 8 MB PSRAM 开发板。未验证所有 ESP32 型号、其他操作系统或全部 iOS 版本。

## 从干净克隆构建固件

需要完整部署控制 API 与 AirPlay 截图时，先阅读 [从干净克隆部署（供 AI Agent）](doc/user/deployment.md)。其中包含后端安装脚本、首次配置、手机操作和只读验收；可直接交给 AI 按步骤执行。

前提：64 位 Windows、PowerShell 5.1 或更新版本、Git、网络连接及数 GB 可用磁盘空间。无需安装 Arduino IDE 或全局 Arduino CLI。Python 仅供下文的 PC 客户端使用。

```powershell
git clone https://github.com/jglwiz/esp32-iphone-agent-windows-ble-hid.git
cd esp32-iphone-agent-windows-ble-hid
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\build.ps1
```

首次运行会在仓库 `.tools/` 内安装 Arduino CLI **1.5.1** 和 ESP32 core **3.3.12**，将 NimBLE-Arduino **2.3.8** 下载到 `.build-deps/`，并编译到 `.build/`。CLI 与 NimBLE 压缩包均校验 SHA-256。后续运行复用已安装工具和下载缓存，不依赖父工作区或全局 Arduino 库。默认板型配置为 ESP32-S3、16 MB Flash、8 MB OPI PSRAM。

脚本会临时将仓库映射为一个空闲盘符，规避 Windows 编译器路径长度限制；完成或报错时解除映射。请在 D: 至 Z: 中保留至少一个空闲盘符。

仅安装依赖、不编译时，在命令后添加 `-SetupOnly`。默认命令只编译；核对开发板、串口和备份后，显式构建并刷写：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\build.ps1 -Upload -Port COM4
```

将 `COM4` 替换为实际开发板串口；Windows 未显示串口时需安装相应串口驱动。恢复和故障排查见 [使用说明](doc/user/usage.md)。

## 快速开始：PC 客户端

前提：开发板已刷入本项目固件；iPhone 已配对 `S3-Click-Buttons`，开启辅助触控。固件构建前提见 [使用说明](doc/user/usage.md)。

在项目根目录运行（Python 3.11 或更新版本；已使用 Python 3.11 环境测试）：

```powershell
python -m venv backend/.venv
.\backend\.venv\Scripts\python.exe -m pip install -e backend
.\backend\.venv\Scripts\python.exe -m iphone_backend.esp32.serial_transport status
```

默认识别唯一 CH343；需要时显式指定端口：

```powershell
.\backend\.venv\Scripts\python.exe -m iphone_backend.esp32.serial_transport --port COM4 status
```

在确认手机输入目标后，按 [操作说明](doc/user/usage.md)执行键鼠动作。`sent` 仅代表本地 BLE 报告提交；必须观察手机验证结果。

## 项目结构

| 路径 | 内容 |
| --- | --- |
| firmware/serial_ble_hid/ | Arduino 固件及 HID 报告描述符 |
| backend/ | 统一 API、ESP32/AirPlay 子模块、包、测试与文档 |
| scripts/ | 固件构建、刷写脚本 |
| tests/ | 客户端单元测试、实机串口检查与诊断工具 |
| tests/case1_wechat_send_hello/ | 独立实机用例，包含工具、doc/ 和忽略提交的 runs/ |
| doc/user/ | 安装、使用、构建与恢复 |
| doc/dev/ | 设计取舍、实施计划、实机验证记录 |
| AGENTS.md | Agent 工作约定和文档导航 |

本地 `.tools/`、`.build/`、`.build-deps/`、`.runtime/`、`.backups/` 和测试 `runs/` 不纳入版本控制。

## 开发与测试

无需连接硬件的客户端测试：

```powershell
.\backend\.venv\Scripts\python.exe -m unittest discover -s backend/tests -v
```

实机测试与普通单元测试分开运行。微信用例涉及真实手机操作与消息发送，见 [测试流程](tests/case1_wechat_send_hello/doc/test-case.md)。设计方案及后续工作见 [实施计划](doc/dev/plan.md)。

## 发布状态

源码已采用 MIT 许可证公开发布，支持独立安装固件依赖与编译。Backend 已包含取帧进程，UxPlay 作为外部运行时通过 UXPLAY_DIR 配置。具体事项见 [发布完善待办](doc/dev/plan.md#独立发布前待办)。

## 许可证

[MIT](LICENSE)。允许修改、再分发和商业使用，须保留版权及许可声明。第三方依赖仍遵循各自的许可证。

可选的 Python FastAPI 常驻控制服务复用串口会话，将动作结果和 AirPlay 截图合并返回。默认使用视觉相对后端；原生绝对 HID 与自动校准已完成局部实机验证，点击受验证范围约束，全屏与跨应用尚未验收。通过 `python -m pip install -e backend` 安装后运行 `python -m iphone_backend`；服务占用串口时不要并行运行直接串口客户端。详见 [服务说明](backend/doc/service.md)。

固件 **0.3.3** 为当前实测iPhone增加修饰键先行报告：普通HID文本已正确输入`a_A-1`，Ctrl+Space再次显示选中的输入语言，客户端text调用不变。设计与证据见[修饰键时序](firmware/serial_ble_hid/doc/keyboard-modifiers.md)，最新范围与限制见[原生HID验收](backend/doc/native-hid-validation.md)；上文旧版本结果为历史记录。

## 统一后端

按 [Backend 使用说明](backend/doc/usage.md) 配置绝对运行路径，通过 `python -m iphone_backend` 启动。8892 是唯一控制和截图 API；8891/8894 仅供内部使用。主控负责取帧启动和 API 监督，UxPlay 为外部运行时。API 占有设备时不要运行直接串口 CLI。迁移证据见 [实施记录](backend/doc/implementation-status.md)。
