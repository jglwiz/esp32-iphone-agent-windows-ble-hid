# iPhone 修饰键时序（0.3.3）

2026-11-01，ESP32-S3 经 BLE 控制当前 iPhone。普通字符可输入，但同时报告 Shift 与字符时，`a_A-1` 实际变为 `a-a-1`；Ctrl/Command 的局部快捷键也曾无可见效果。串口 sent 不能证明手机正确解释了组合键。

## 对照与修复

同一 6.1 Terra / Medium 操作，主代理审查图片与串口记录：

- 0.3.0 左右 Shift+A、Shift+- 都失败；保持 0.5 秒仍失败。保持期间多次状态返回 `0200040000000000`，证明固件保留 Shift+A 字节，不是 PC 字符映射丢失 Shift。
- 0.3.1 将 GATT 输入特征改为键盘、相对鼠标、绝对鼠标顺序，报告描述符与键盘 appearance 不变；单独这一改动没有修复。通过 Service Changed 通知已有连接刷新 GATT，未删除 bond。
- 0.3.2 增加 usage=0 的仅修饰键诊断：先报告 Shift，等待 200ms，再报告 Shift+A，结果为 A；Shift+- 得到下划线，完整探测得到精确 `a_A-1`。
- 0.3.3 将上述策略整合至正常键盘命令。含修饰键的 tap 依次发送：仅修饰键 → 等待 200ms → 修饰键与字符 → 等待 60ms → 仅修饰键 → 等待 60ms → 全部释放。含修饰键的 down 先建立修饰键，再保持字符。无修饰键 tap 保持原来的 60ms 行为。

PC 的 `text` 与 `ascii_key` 调用和映射不变。usage=0 可用于显式 modifier-only；1–3 是键盘错误用途码，服务与固件均拒绝。取消、租约超时、断连和 release_all 仍释放全部按键。

## 验证边界

有效差异是“先建立修饰键”，不是“把同一报告按更久”。这是当前 iPhone 的实机兼容策略，不能据此证明所有 iOS 版本或 BLE 键盘都有同一问题。GATT 顺序单独失败的证据保留，不把它描述成已证实的根因。

实验原图及请求位于项目 `.runtime/modifier-fix-20261101/`；旧版应用镜像及源码位于 `.backups/modifier-fix-20261101/`。0.3.3 构建、应用区写入 hash 校验通过，PC 65 项隔离测试通过（27.482 秒）。整合后的普通 text 已实测得到精确 `a_A-1`（`v033-ascii.jpg`），Ctrl+Space 已显示 English (US) 高亮（`v033-layout-english.jpg`），主代理独立查看通过。为排除首字母自动大写，输入先加数字前缀，再用硬件快捷键删除；不是屏幕键盘替代输入。微信完整测试见[用例记录](../../../tests/case1_wechat_send_hello/doc/test-case.md)。

升级只写 app0（0x10000），保留 NVS 与配对。重启后原生指针配置必须复验，不复用旧连接的已验证状态。新增时序会增加带修饰字符的发送时长，不能仅凭串口命令数宣称性能提升。

参考线索：[复合 HID 设备在 iOS 忽略 Shift 的上游讨论](https://github.com/adafruit/Adafruit_nRF52_Arduino/issues/870)。该讨论中的 USB 顺序修复不是本 BLE 实验的成功证据。
