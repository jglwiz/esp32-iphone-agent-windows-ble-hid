# iPhone 修饰键时序（0.3.3）

**当前结论（2026-11-02，Asia/Taipei）：硬件输入已恢复，根因未找到。**最终运行原版0.3.3，普通`text`的`1zAz1`和`1a_A-1`均由实机原图确认，短复位后仍通过。恢复发生在临时固件刷写/重连之后，但不能认定是时序改动、刷写或某种连接状态单独修复了问题；不保证不会复发。本轮只完成输入原型，没有重新发送微信消息，也没有通过新的完整微信用例。

2026-11-01，ESP32-S3 经 BLE 控制当前 iPhone。普通字符可输入，但同时报告 Shift 与字符时，`a_A-1` 实际变为 `a-a-1`；Ctrl/Command 的局部快捷键也曾无可见效果。串口 sent 不能证明手机正确解释了组合键。

## 对照与修复

同一 6.1 Terra / Medium 操作，主代理审查图片与串口记录：

- 0.3.0 左右 Shift+A、Shift+- 都失败；保持 0.5 秒仍失败。保持期间多次状态返回 `0200040000000000`，证明固件保留 Shift+A 字节，不是 PC 字符映射丢失 Shift。
- 0.3.1 将 GATT 输入特征改为键盘、相对鼠标、绝对鼠标顺序，报告描述符与键盘 appearance 不变；单独这一改动没有修复。通过 Service Changed 通知已有连接刷新 GATT，未删除 bond。
- 0.3.2 增加 usage=0 的仅修饰键诊断：先报告 Shift，等待 200ms，再报告 Shift+A，结果为 A；Shift+- 得到下划线，完整探测得到精确 `a_A-1`。
- 0.3.3 将上述策略整合至正常键盘命令。含修饰键的 tap 依次发送：仅修饰键 → 等待 200ms → 修饰键与字符 → 等待 60ms → 仅修饰键 → 等待 60ms → 全部释放。含修饰键的 down 先建立修饰键，再保持字符。无修饰键 tap 保持原来的 60ms 行为。

PC 的 `text` 与 `ascii_key` 调用和映射不变。usage=0 可用于显式 modifier-only；1–3 是键盘错误用途码，服务与固件均拒绝。取消、租约超时、断连和 release_all 仍释放全部按键。

## 固定按键顺序与可复用回归

用户确认的方案是：**先按住 Shift → 按下 A → 松开 A、继续按住 Shift → 最后松开 Shift**。不能退化为第一次报告就同时按下 Shift+A，也不能把按住 Shift 和按 A 拆成不同 HTTP 请求：服务在每个请求结束时会释放全部输入。

固件实现位于 [serial_ble_hid.ino](../serial_ble_hid.ino) 的 `advanceAction()`，`action.modifiers && action.usage` 分支。正常 `text`/`key` 的修饰键 tap 已由固件执行四阶段；不用调用方为每个大写字符另写一套规则。对于 Shift+A，键盘8字节报告应依次为：

| 阶段 | 报告 | 当前固件等待 |
| --- | --- | --- |
| 仅 Shift 按下 | `0200000000000000` | 200ms |
| Shift 保持，A 按下 | `0200040000000000` | 60ms |
| A 松开，Shift 保持 | `0200000000000000` | 60ms |
| Shift 松开 | `0000000000000000` | 完成 |

2026-11-01已成功的显式原始请求为本地 `.runtime/modifier-fix-20261101/v032-leading-shift-a.request.json`，当时先等待200ms，A按下及松开后各等待100ms。其完整步骤已固化为可提交的 [shift_a_staged.json](../../../tests/case1_wechat_send_hello/fixtures/shift_a_staged.json)，不依赖被忽略的运行目录。数字前缀用于排除输入框自动首字母大写；期待结果是精确 `1A`，不是 `1a`。该请求只写诊断草稿，不发送。

`key_down`表达完整键盘状态，不是自动累加按键：A按下时必须仍传`modifiers=2`。`key_up`在当前API中会释放全部按键，即使写了`usage=4`也不是只松开A。因此只松开A、保持Shift要用`key_down usage=0 modifiers=2`，然后再`key_up`。

从项目根通过已有服务调用（先确认聚焦可清理的空输入框和系统英文布局）：

```python
import json
import sys
from pathlib import Path
sys.path.insert(0, str(Path("pc").resolve()))
from service_client import act

payload = json.loads(Path(
    "tests/case1_wechat_send_hello/fixtures/shift_a_staged.json"
).read_text(encoding="utf-8"))
result = act(output=".runtime/shift-a-staged.jpg", **payload)
```

必须看实际截图确认`1A`并清空；`reports_sent`不等于通过。当前0.3.3的`key_down`还会自行预按修饰键200ms，因此该显式回归与旧0.3.2的总耗时并非完全一致，但按键保持/释放顺序保持一致。发生回归时优先复现此固定请求，再与普通`text`对照，不以缺少“A先松开”阶段的长按实验代替原成功方案。

2026-11-02 17:17，Terra Medium按此固定文件在系统英文键盘的设置搜索框实测一次，8步完成并释放，实际仍为`1a`；主代理独立看图确认。因此本次固化的是曾成功的操作顺序与可复现用例，不宣称当前故障已修复。证据`.runtime/modifier-regression-20261102/exact-staged-shift.jpg`、同名请求/结果及`exact-staged-clear.jpg`。步骤和时长核验、`git diff --check`通过，日志`fixture-check.txt`；未发送消息。

## 验证边界

2026-11-01实验中观察到的有效差异是“先建立修饰键”，不是“把同一报告按更久”；0.3.3保留这一兼容策略。2026-11-02同策略曾失败、随后恢复，故不能据此解释全部复发原因，也不能推广到所有iOS版本或BLE键盘。GATT顺序单独失败的证据保留，不把它描述成已证实的根因。

实验原图及请求位于项目 `.runtime/modifier-fix-20261101/`；旧版应用镜像及源码位于 `.backups/modifier-fix-20261101/`。0.3.3 构建、应用区写入 hash 校验通过，PC 65 项隔离测试通过（27.482 秒）。整合后的普通 text 已实测得到精确 `a_A-1`（`v033-ascii.jpg`），Ctrl+Space 已显示 English (US) 高亮（`v033-layout-english.jpg`），主代理独立查看通过。为排除首字母自动大写，输入先加数字前缀，再用硬件快捷键删除；不是屏幕键盘替代输入。微信完整测试见[用例记录](../../../tests/case1_wechat_send_hello/doc/test-case.md)。

升级只写 app0（0x10000），保留 NVS 与配对。重启后原生指针配置必须复验，不复用旧连接的已验证状态。新增时序会增加带修饰字符的发送时长，不能仅凭串口命令数宣称性能提升。

参考线索：[复合 HID 设备在 iOS 忽略 Shift 的上游讨论](https://github.com/adafruit/Adafruit_nRF52_Arduino/issues/870)。该讨论中的 USB 顺序修复不是本 BLE 实验的成功证据。

## 2026-11-02 再现：旧修复仍存在，跨连接稳定性未证实

昨日三个完整微信硬件用例通过是真实的局部验证；今日同版本再次出现下划线、大写和快捷键失效，不能再将 0.3.3 时序描述为已解决所有连接状态下的问题。

对照成功 run `20261101_103405_9fbc` 和失败 run `20261102_140949_85ef` 的 `ascii_probe.request.json`，两者都是输入 `1a_A-1`，再以 Cmd+Left 和 Delete 删除前缀。串口记录中的 underscore、A 分别仍为 usage=45/4、modifiers=2，Command 为 modifiers=8；修饰键 tap 均约 0.34 秒，符合 200+60+60ms 阶段策略。因此没有发现 PC 漏传修饰键、运行旧版固件或预按时序被删除的证据。

成功时 boot_id=`91f04e495ea33dbe`、connection_generation=1；失败时 boot_id=`fc756ce710d2cd31`、connection_generation=38。两者 firmware 均为 0.3.3，键盘输入句柄29、protocol_mode=1。启动及连接状态不同只是调查变量，不足以确定因果。

失败连接的显式 Ctrl-only 及 Ctrl+Space 诊断中，固件状态分别持续回读 `0100000000000000`、`01002c0000000000`，keyboard_ready=true。这只能证明本地报告值保持正确，不证明 BLE 空中交付或 iPhone 对修饰键的解释。当前调查证据保存在 `.runtime/modifier-regression-20261102/`，尚无本轮修复后的硬件用例通过结论。

外部调查线索：[Aiden 开发者的键鼠复合设备对照实验](https://huggingface.co/blog/NatalieY/debugging-aiden)报告 iOS 在部分键鼠并存场景将修饰命令路由到 SpringBoard。其 USB profile 切换措施不能直接作为本 BLE 项目的修复；本项目尚未采集 iPhone 键盘焦点日志或完成键盘单独/复合对照，不将该现象认定为本案根因。

本轮早期无鼠标对照：左 Cmd+Space 确实打开 Spotlight（`left-cmd-space.jpg`），故不能称所有修饰键均被忽略；随后一次 `1a_A-1` 硬件输入没有可见字符，Ctrl+Space 没有显示菜单，Esc 没有收起搜索。主代理复核原图与返回的新帧元数据；不把空搜索框判为 ASCII 通过或 Shift 失败。输入均已释放，未发送消息。另有正向目标(82,745)落在约(414,335)的指针反向，当时先恢复可验证的手机操作状态，再继续输入法及连接状态对照。

17:00串口RTS脉冲EN复位后，固件仍0.3.3、boot改为`2c94103bf3d0a53c`、BLE generation=1，指针连续检查恢复正向；键盘没有随之恢复。文件传输助手在豆包输入法下，硬件`1a_A-1`得到带组合态的`1a-a-1`。用户指出当前是豆包后，操作子代理通过地球键切到系统English (US)，`english-layout.jpg`明确显示名称；再以相同硬件text输入，`english-hid-ascii.jpg`仍为`1a-a-1`，但已没有组合态。主代理独立复核两张图。因此需要区分第三方输入法的组合行为和英文布局下仍存在的Shift丢失，不能将屏幕英文布局当作硬件输入法列表验收。两次草稿均已清空，没有发送。

## 原型对照与最终恢复

所有实机操作由同一GPT-6.1 Terra / Medium执行，主代理独立审图。证据根目录为`.runtime/modifier-regression-20261102/`，汇总为`experiment-summary.json`；这些运行原图只在本地保存，不随仓库分发。

| 对照 | 结果及意义 | 关键证据（相对证据根目录） |
| --- | --- | --- |
| 系统英文、左右Shift、Caps Lock | Shift得到`1aa`，Caps Lock对照仍为`1a`；不能只归因于豆包 | `shift-left-right.jpg`、`caps-text.jpg` |
| Shift先保持3秒，A按下/松开后再松Shift | 持有期间键帽仍小写，最终`1a`；串口回读确认Shift状态保持，但不证明手机解释正确 | `phase-prototype/timeline.json`、`phase-prototype/final.jpg`及串口日志 |
| 临时`0.3.3-proto-directdown` | 仅让`keyboard.down`直接发完整状态，去掉该路径额外的仅Shift报告和200ms；tap路径不变。未修饰`1zaz1`和分步Shift的`1zAz1`可区分 | `nonword-lower-observe.jpg`、`nonword-shift-observe.jpg` |
| 回刷原版0.3.3 | 普通text分别精确显示`1zaz1`和`1zAz1`，无候选/选中态，说明恢复不依赖临时改动 | `restore-lower-observe.jpg`、`restore-upper-observe.jpg` |
| 原版0.3.3再次短EN复位 | 普通text仍精确显示`1zAz1`，最终`1a_A-1`亦通过，未复发 | `short-reset-observe.jpg`、`final-ascii-observe.jpg`、`final-clear.jpg` |

最初0.3.2成功请求与当前固定请求的显式步骤相同，但0.3.3的`keyboard.down`内部多一个预按阶段，不能仅比较JSON就声称底层时序完全一致。临时固件用于隔离这项差异；恢复原固件也成功后，不能把去掉200ms写成根因修复。刷写同时改变启动和连接状态，没有只改变该状态的充分对照，无法在ESP32运行态、BLE重连和iPhone输入处理之间唯一定位。

短串`1A`曾伴随`1a`候选和选中态，不单独作为成功证据；改用未修饰/修饰非词串对照，再观察恢复原固件后的干净结果。不能把第一次看见大写、`ready=true`或固件本地报告正确当作完整验收。

最终在板版本0.3.3，boot_id=`ea4318e6d7df99a2`；`final-health.json`记录keyboard/absolute ready，`final-clear.jpg`确认诊断草稿已清空。原app0备份SHA-256为`c07d9849f75a8e1a2163ebaa47a1c628cc78161e917d94e4f3945ae4f52503ac`，恢复写入校验通过。实验只写app0，保留NVS/配对及投屏；临时代码未整合到正式实现。5秒EN复位脚本仅准备、未执行，不列为已测试恢复方法。

再次复发时，先保存版本、boot/连接代次、布局、原始请求和按键各阶段证据；使用上述非词串与原成功请求作对照。优先在同一连接中只改变一个变量。刷写后恢复不等于刷写是必要措施，当前没有可保证成功的恢复处方，也没有跨长期运行或多设备稳定性结论。
