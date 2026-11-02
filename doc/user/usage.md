# 安装、使用、构建与恢复

以下命令均从项目根目录执行。Python CLI 与固件构建均可在独立克隆中使用，详见根目录 README 的安装步骤。

完整控制与截图环境的首次安装见 [从干净克隆部署](deployment.md)，可交给 AI 执行；本页主要说明硬件、构建和恢复。



0.4.1 固件蓝牙名称为 **S3-Click-Buttons**；旧版名称为 S3-iPhone-HID。板子连接 PC 的 USB，iPhone 开启辅助触控并选择当前设备。新版使用独立身份，升级旧版需配对新名称，不自动删除旧配对。初次配对使用 Just Works + bonding，动作仅在连接已加密且对应报告订阅完成后执行。原地点击使用与边界见 [Backend 说明](../../backend/doc/usage.md#原地点击)。

原生绝对定位测试建议固定竖屏，并让手机保持稳定倾角。曾观察到方向异常，用户反馈此前手机可能水平放置；倾斜放回后多次恢复正向，但尚未通过受控实验确认姿态传感器是根因。AirPlay为主要画面依据，仍须检查实际指针响应，不因摆放倾斜就跳过验证。详见[方向异常踩坑记录](../../backend/doc/direction-root-cause.md)。

当前日常入口为 [统一 Backend](../../backend/doc/usage.md)，通过 8892 获取控制与画面。下列直接串口 CLI 仅在 API 已释放串口后用于诊断；不能与常驻服务并行。先按根目录 README 安装 backend 包，再从项目根目录执行：

```powershell
$python = '.\backend\.venv\Scripts\python.exe'
& $python -m iphone_backend.esp32.serial_transport status
& $python -m iphone_backend.esp32.serial_transport move 20 0
& $python -m iphone_backend.esp32.serial_transport click
# 先确认 iPhone 的测试文本框已获得焦点、硬件键盘布局为英文。
& $python -m iphone_backend.esp32.serial_transport text 'ble123'
& $python -m iphone_backend.esp32.serial_transport key 42  # Backspace
```

默认自动选择唯一的 CH343；存在多个候选时必须指定 `--port COMx`。用 `--log .runtime\commands.jsonl` 留存请求/响应。日志会包含输入内容，仅本地保存。`monitor --seconds 30` 只查询状态，不发送测试输入。

`sent` 表示本地 BLE 栈接受所需报告，不证明 iPhone 已执行；必须通过投屏、摄像头或直接观察手机确认。文本仅支持美式键盘布局的 ASCII；中文输入法、Caps Lock 或其他布局会改变结果。当前手机双语输入法会将字母作为组合文本；组合态下 Cmd+A 未可靠选中全部文字，测试清理使用逐字退格，不把组合键的系统行为视为已完整验证。

## 实现与构建

构建要求 64 位 Windows、PowerShell 5.1 或更新版本、网络连接和数 GB 磁盘空间。脚本会自动下载固定版本 Arduino CLI 1.5.1、ESP32 core 3.3.12 和 NimBLE-Arduino 2.3.8，不要求 Arduino IDE、全局 CLI 或父目录工具链。CLI 与 NimBLE 压缩包使用固定 SHA-256 校验；ESP32 包由 Arduino CLI 按官方索引安装。工具、索引、下载缓存和用户库目录均位于本仓库 `.tools/`，调用后恢复原 Arduino 路径环境变量。

首次安装需要下载大型工具链。下载中断后可重跑原命令；若提示压缩包哈希不匹配，删除错误信息中指定的本地缓存文件后重试。需要访问 GitHub、Arduino 和 Espressif 官方下载服务。脚本不会改写系统执行策略；README 中的 Bypass 仅用于当前 PowerShell 子进程。构建失败会立即停止，不继续刷写。

ESP32 GCC 会拼接包含多层 `../` 的头文件路径，在较深的克隆目录中可能报 `bits/c++config.h` 不存在，即使文件实际存在。脚本通过 Windows `subst` 临时将仓库映射为 D: 至 Z: 中的空闲盘符，安装、编译和刷写均使用此短路径，完成或异常退出时解除映射。文件仍保存在原仓库内，不复制工具链，也不更改系统长路径设置；没有空闲盘符时直接报错。不要同时对同一克隆运行多个构建。

- [固件](../../firmware/serial_ble_hid/serial_ble_hid.ino)：UART0 / Serial0，115200 8N1；Arduino ESP32 3.3.12 + 独立 **NimBLE-Arduino 2.3.8**。不注册 USB HID，不启用 Wi-Fi。
- [报告描述符](../../firmware/serial_ble_hid/hid_reports.h)：键盘 ID 1，鼠标 ID 2；BLE 报告值不额外前置 Report ID。
- [CLI](../../backend/src/iphone_backend/esp32/serial_transport.py)：标准库与 pyserial，显式控制 DTR/RTS；超时或重启后不自动重放动作。
- [构建脚本](../../scripts/build.ps1)：工具链保存在本仓库 .tools/，NimBLE-Arduino 位于 .build-deps/，编译中间文件位于 .build/work/，固件输出位于 .build/。这些目录不提交 Git。`-SetupOnly` 只安装依赖；默认只编译；`-Upload -Port COM4` 只在确认目标端口与备份后使用，不允许与 `-SetupOnly` 同用。

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\build.ps1
# 可选：仅安装依赖
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\build.ps1 -SetupOnly
# 已确认端口与备份后：
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\build.ps1 -Upload -Port COM4
& $python -m unittest discover -s backend/tests -v
# 实机串口协议检查：仅状态与释放，不输入字符或移动指针。
& $python backend\tools\serial_smoke.py
```

目前实现 hello/status/ping/diag、mouse.move/click/down/up/scroll、keyboard.tap/down/up、release_all/cancel。一次只执行一个动作，移动拆成每 20 ms 最多 8 单位；单击和按键有 60 ms 按下间隔。CLI 的 text 在 PC 上转换为连续 keyboard.tap。diag 返回实际 Report Map 和 Report Reference；status 包含协议模式与最近报告字节。

协议使用有版本、会话和请求 ID 的按行 JSON，最大 512 字节。保留最近 16 个动作结果；相同 ID/相同请求返回已有结果，不重复执行，相同 ID/不同请求报错。hello 切换会话会取消旧动作、释放输入。动作的 accepted 与最终 sent/error 分开；故障后的 partial_unknown 表示可能已部分执行。缓存仅限当前启动和活跃会话，未实现跨断电去重。

客户端每 500 ms 在长动作期间发送心跳；固件 2 s 无有效会话通信即释放全部按键/按钮并取消动作。CLI 结束时也显式释放。长时间 Python 脚本必须持续请求或自行维持心跳，不能保持按下状态后无限休眠。

## 当前边界

0.3.3包含“先按修饰键、再按字符、先松字符、最后松修饰键”的兼容时序，但曾出现同版本失效。2026-11-02恢复原固件后ASCII输入通过，根因仍未确定；不能把连接ready或切到英文键帽当作输入正确的证明，也不将反复刷写当作保证有效的恢复步骤。诊断顺序及证据边界见[修饰键说明](../../firmware/serial_ble_hid/doc/keyboard-modifiers.md)。

本轮完成计划 P1。尚未实现 bond.clear 命令、完整动作队列、Boot Protocol、媒体键、水平滚动及视觉坐标校准。现有 USB HID 校准不能套用。拖动与双向滚轮已通过单轮实机验证；自动重连、完整压力测试与故障恢复矩阵尚未系统验证，不能从首轮成功推断其已通过。

## 备份与恢复

原开发板在 2026-10-30 刷写前的 16 MB Flash 保存在本地项目 `.backups/pre-ble-20261030.bin`；其元数据在同目录 JSON。SHA-256：`18E81B1BCD8C910A74D0B45D9080D4AC7DC1767408DF6EF3D477CABEFA947F7C`。备份可能含旧网络配置，仅本地保留，不随仓库分发。其他使用者须为自己的板子制作并验证备份，不能套用下面的文件名或校验值。

需要恢复时，先关闭占用串口的新 CLI，重新核对 CH343 与目标 S3，校验备份长度和哈希，再通过 esptool 对该板写入完整备份；恢复会覆盖本次 BLE 固件与配对信息。以下命令仅作恢复说明，不自动执行：

```powershell
& $python -m esptool --chip esp32s3 --port COM4 --baud 921600 write-flash 0 .backups\pre-ble-20261030.bin
```

微信完整操作测试、子代理执行和主代理独立验收见 [case1 测试用例](../../tests/case1_wechat_send_hello/doc/test-case.md)。工具、文档和忽略提交的运行证据集中在该测试目录。

## 统一截图服务

`python -m iphone_backend.snapshot <文件路径>` 从 8892/frame 获取图像并检查原始帧时间；人工看图可直接请求最新快照，动作证据仍要求新鲜帧。取帧实现随 backend 包安装，由主控统一启动；UxPlay 路径通过 UXPLAY_DIR 指定。不要把 HTTP 成功或串口 sent 当作手机操作成功。
