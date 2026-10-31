# 安装、使用、构建与恢复

以下命令均从项目根目录执行。Python CLI 可独立使用；固件构建脚本目前仍依赖原工作区工具链布局，详见构建章节。



固件蓝牙名称为 **S3-iPhone-HID**。板子连接 PC 的 USB，iPhone 开启辅助触控并在“设备 → 蓝牙设备”中选择它。初次配对使用 Just Works + bonding，动作仅在连接已加密且对应报告订阅完成后执行。

先按根目录 README 安装 pc/requirements.txt，再从项目根目录执行：

```powershell
$python = '.\.venv\Scripts\python.exe'
& $python pc\bridge.py status
& $python pc\bridge.py move 20 0
& $python pc\bridge.py click
# 先确认 iPhone 的测试文本框已获得焦点、硬件键盘布局为英文。
& $python pc\bridge.py text 'ble123'
& $python pc\bridge.py key 42  # Backspace
```

默认自动选择唯一的 CH343；存在多个候选时必须指定 `--port COMx`。用 `--log .runtime\commands.jsonl` 留存请求/响应。日志会包含输入内容，仅本地保存。`monitor --seconds 30` 只查询状态，不发送测试输入。

`sent` 表示本地 BLE 栈接受所需报告，不证明 iPhone 已执行；必须通过投屏、摄像头或直接观察手机确认。文本仅支持美式键盘布局的 ASCII；中文输入法、Caps Lock 或其他布局会改变结果。当前手机双语输入法会将字母作为组合文本；组合态下 Cmd+A 未可靠选中全部文字，测试清理使用逐字退格，不把组合键的系统行为视为已完整验证。

## 实现与构建

构建脚本要求父目录已有 `.tools/arduino-cli/arduino-cli.exe` 和安装了 ESP32 core 3.3.12 的 `.tools/arduino-data/`。此布局来自原开发工作区，不随仓库分发，目前尚无独立工具链安装脚本。不要把以下命令理解为干净克隆后的完整安装步骤。

- [固件](../../firmware/serial_ble_hid/serial_ble_hid.ino)：UART0 / Serial0，115200 8N1；Arduino ESP32 3.3.12 + 独立 **NimBLE-Arduino 2.3.8**。不注册 USB HID，不启用 Wi-Fi。
- [报告描述符](../../firmware/serial_ble_hid/hid_reports.h)：键盘 ID 1，鼠标 ID 2；BLE 报告值不额外前置 Report ID。
- [CLI](../../pc/bridge.py)：标准库与 pyserial，显式控制 DTR/RTS；超时或重启后不自动重放动作。
- [构建脚本](../../scripts/build.ps1)：显式使用父目录 .tools 下的工具链，输出 .build/；下载固定版本 NimBLE-Arduino 到 .build-deps/ 并校验 SHA-256，原工具链源码不修改。`-Upload -Port COM4` 只在确认目标端口与备份后使用。

```powershell
.\scripts\build.ps1
# 已确认端口与备份后：
.\scripts\build.ps1 -Upload -Port COM4
& $python -m unittest discover -s tests -v
# 实机串口协议检查：仅状态与释放，不输入字符或移动指针。
& $python tests\serial_smoke.py
```

目前实现 hello/status/ping/diag、mouse.move/click/down/up/scroll、keyboard.tap/down/up、release_all/cancel。一次只执行一个动作，移动拆成每 20 ms 最多 8 单位；单击和按键有 60 ms 按下间隔。CLI 的 text 在 PC 上转换为连续 keyboard.tap。diag 返回实际 Report Map 和 Report Reference；status 包含协议模式与最近报告字节。

协议使用有版本、会话和请求 ID 的按行 JSON，最大 512 字节。保留最近 16 个动作结果；相同 ID/相同请求返回已有结果，不重复执行，相同 ID/不同请求报错。hello 切换会话会取消旧动作、释放输入。动作的 accepted 与最终 sent/error 分开；故障后的 partial_unknown 表示可能已部分执行。缓存仅限当前启动和活跃会话，未实现跨断电去重。

客户端每 500 ms 在长动作期间发送心跳；固件 2 s 无有效会话通信即释放全部按键/按钮并取消动作。CLI 结束时也显式释放。长时间 Python 脚本必须持续请求或自行维持心跳，不能保持按下状态后无限休眠。

## 当前边界

本轮完成计划 P1。尚未实现 bond.clear 命令、完整动作队列、Boot Protocol、媒体键、水平滚动及视觉坐标校准。现有 USB HID 校准不能套用。拖动与双向滚轮已通过单轮实机验证；自动重连、完整压力测试与故障恢复矩阵尚未系统验证，不能从首轮成功推断其已通过。

## 备份与恢复

原开发板在 2026-10-30 刷写前的 16 MB Flash 保存在本地项目 `.backups/pre-ble-20261030.bin`；其元数据在同目录 JSON。SHA-256：`18E81B1BCD8C910A74D0B45D9080D4AC7DC1767408DF6EF3D477CABEFA947F7C`。备份可能含旧网络配置，仅本地保留，不随仓库分发。其他使用者须为自己的板子制作并验证备份，不能套用下面的文件名或校验值。

需要恢复时，先关闭占用串口的新 CLI，重新核对 CH343 与目标 S3，校验备份长度和哈希，再通过 esptool 对该板写入完整备份；恢复会覆盖本次 BLE 固件与配对信息。以下命令仅作恢复说明，不自动执行：

```powershell
& $python -m esptool --chip esp32s3 --port COM4 --baud 921600 write-flash 0 .backups\pre-ble-20261030.bin
```

微信完整操作测试、子代理执行和主代理独立验收见 [case1 测试用例](../../tests/case1_wechat_send_hello/doc/test-case.md)。工具、文档和忽略提交的运行证据集中在该测试目录。

## 可选的截图服务

pc/snapshot.py 从 `http://127.0.0.1:8891/frame` 获取图像，并检查 `X-Frame-Time`。服务来自原工作区的 AirPlay 取帧组件，不包含在此仓库。运行纯键鼠 CLI 不需要该服务；需要视觉自动化测试时必须先提供兼容的服务。不要把端口存在或串口 sent 回执视为手机操作成功。
