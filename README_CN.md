# ESP32 iPhone Agent for Windows — BLE HID

[English](README.md) | 简体中文

通过 ESP32-S3 BLE HID 和 AirPlay，让 Windows 上的 AI Agent 观察并操作 iPhone。

项目标识：`esp32-iphone-agent-windows-ble-hid`。当前实现提供串口到 BLE 的键鼠控制桥接和 Agent 实机测试；视觉反馈使用另行提供的 AirPlay 取帧服务。

**控制链路：PC → USB 串口 → ESP32-S3 → BLE HID → iPhone。** 板子 USB 接 PC；iPhone 通过蓝牙接收输入。视觉反馈可另接 AirPlay，BLE HID 本身不返回屏幕画面。

## 当前状态

固件 0.2.0 已在现有微雪 ESP32-S3 与 iPhone 17e 上验证键盘输入、退格、鼠标移动、点击、拖动和双向滚轮。完整自动重连、锁屏恢复、长期稳定性和坐标校准尚未完成。文本受手机硬件输入法影响，ASCII 下划线曾被中文输入法转换为长横线。完整证据与边界见 [验证记录](doc/dev/validation.md)。

当前主要验证环境为 Windows、CH343 串口、16 MB Flash / 8 MB PSRAM 开发板。未验证所有 ESP32 型号、其他操作系统或全部 iOS 版本。

## 从干净克隆构建固件

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

前提：开发板已刷入本项目固件；iPhone 已配对 `S3-iPhone-HID`，开启辅助触控。固件构建前提见 [使用说明](doc/user/usage.md)。

在项目根目录运行（Python 3.10 或更新版本；已使用 Python 3.11 环境测试）：

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r pc\requirements.txt
.\.venv\Scripts\python.exe pc\bridge.py status
```

默认识别唯一 CH343；需要时显式指定端口：

```powershell
.\.venv\Scripts\python.exe pc\bridge.py --port COM4 status
```

在确认手机输入目标后，按 [操作说明](doc/user/usage.md)执行键鼠动作。`sent` 仅代表本地 BLE 报告提交；必须观察手机验证结果。

## 项目结构

| 路径 | 内容 |
| --- | --- |
| firmware/serial_ble_hid/ | Arduino 固件及 HID 报告描述符 |
| pc/ | Python 串口 CLI、依赖和可选截图客户端 |
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
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
```

实机测试与普通单元测试分开运行。微信用例涉及真实手机操作与消息发送，见 [测试流程](tests/case1_wechat_send_hello/doc/test-case.md)。设计方案及后续工作见 [实施计划](doc/dev/plan.md)。

## 发布状态

源码已采用 MIT 许可证公开发布，支持独立安装固件依赖与编译。可选的截图服务仍需另行提供。具体事项见 [发布完善待办](doc/dev/plan.md#独立发布前待办)。

## 许可证

[MIT](LICENSE)。允许修改、再分发和商业使用，须保留版权及许可声明。第三方依赖仍遵循各自的许可证。
