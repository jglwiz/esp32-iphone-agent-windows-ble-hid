# 项目工作约定

项目名称：**ESP32 iPhone Agent for Windows — BLE HID**；目录及 Git 仓库根：`esp32-iphone-agent-windows-ble-hid/`。本项目独立于父目录中的旧 USB HID 方案。

<!-- development-doc:start -->
## 开发文档

公共开发资料放根 `doc/dev/`，项目级用户文档放 `doc/user/`，模块资料放 `<module>/doc/`。局部设计按主要维护归属靠近模块，不因有多个调用方就上移到项目级。专题文件用具体主题名；`design.md` 仅用于整体设计及导航，有实际需要时才创建，入口位置为 `doc/dev/design.md` 或模块 `doc/design.md`。

路径以所属项目根为基准；嵌套独立项目以自身目录为根，内部子模块仍用 `<module>/doc/`。源码直接位于项目根时，项目设计仍放 `doc/dev/design.md`。

严格执行上述文档结构，不设置其他目录布局或旧路径兼容入口。

临时任务计划、执行清单和进度记录统一放在 `tasks/<任务名>/`，不提交 Git。`doc/dev/` 仅保留需要长期维护的设计、决策及项目路线图。任务完成后，将有长期价值的结论整理到对应正式文档。

已有文档可随开发、需求纠正和排查结论更新；Agent 也可有节制地按需新建或拆分文档，无需用户点名或再次确认。优先复用已有资料，不按类型清单生成整套文件。初始化只配置 Agent 指令与已有资料导航，不自动补文档或空目录。

按任务读取已有入口与相关资料；入口不存在时直接搜索，不添加悬空链接。设计重点记录原因、取舍和踩坑经验，实现细节查代码与测试。理由未知时不编造，预期与实现不符时指出差异。

只更新受影响的内容和引用；无变化时不改文档。新文件须有实际内容并服务当前任务，遵守用户明确限定的操作范围。
<!-- development-doc:end -->


## 本子项目导航

全新电脑或干净克隆的部署入口：[AI 部署流程](doc/user/deployment.md)。先使用 `scripts/setup.ps1` 安装和初始化，再启动并只读验收；`deploy.py prepare/handover` 仅用于旧服务迁移。

当前统一后端位于 backend/：[使用说明](backend/doc/usage.md)、[架构](backend/doc/architecture.md)、[迁移实施记录](backend/doc/implementation-status.md)。唯一启动入口为 python -m iphone_backend；8892 提供控制和截图。旧 pc/ 源码已按清单迁移，下面的历史文档以当前说明为准。

本目录是独立的串口 → ESP32-S3 → BLE HID 项目，以此目录为文档根。

- 常驻串口服务：[PC 服务设计与使用](backend/doc/service.md)。维护 FastAPI、组合动作与截图返回时读取。
- 服务生命周期：[管理进程与 restart API](backend/doc/service-management.md)。启动、停止接单、释放串口及版本交接时读取。
- 原地点击与按钮报告：[0.4.1 按钮设计及验收](firmware/serial_ble_hid/doc/mouse-buttons.md)。维护 `click()`、HID 报告、按钮释放或升级配对时读取；当前位置点击通过不代表坐标定位稳定。
- 正向定位与实机边界：[连续指针响应验收](backend/doc/pointer-response-validation.md)；姿态相关踩坑：[方向异常调查](backend/doc/direction-root-cause.md)。区分当前局部验收、历史失败与未确认原因。
- 微信完整实机用例：[测试流程与主代理验收](tests/case1_wechat_send_hello/doc/test-case.md)。运行子代理操作测试、比较耗时时读取。
- 项目概览：[英文 README](README.md) / [中文 README](README_CN.md)（更新时保持内容一致）；使用、构建与恢复：[使用说明](doc/user/usage.md)。
- 设计取舍、协议候选与后续验收：[实施计划](doc/dev/plan.md)。修改架构或规划工作时读取；其中候选内容不代表已实现。
- 实机证据、库切换原因与未验证边界：[验证记录](doc/dev/validation.md)。判断支持范围、排查输入问题时读取。

文档已按用途归入 doc/dev/ 与 doc/user/；测试用例的文档保留在所属用例目录的 doc/。历史计划和验证记录引用的父工作区资料、运行截图及备份不随仓库分发。

本项目 BLE 控制与父项目旧 Wi-Fi/TCP → USB HID 控制不同。板子 USB 接 PC；不要为本项目测试启动旧 USB 控制服务。复用现有 AirPlay 取帧，不因调试重启 UxPlay 或取帧进程。串口 sent 仅表示本地报告提交，必须观察手机确认输入效果。

## iPhone 实机操作分工

用户指定：代码开发由主代理负责；**6.1 Terra / Medium 子代理只负责测试执行和 iPhone UI 操作，不参与开发**。最终 iPhone UI 操作使用该模型，因此所有测试执行统一交给该配置的子代理，包括原型实验、自动化测试和实机回归；主代理负责设计测试、审查证据及最终验收。

主代理负责方案设计、固件/API/客户端实现、原型脚本和测试代码编写、缺陷修复、文档维护，以及测试环境和工具准备。不得委派 Terra 子代理编写或修改这些代码。Terra 使用主代理提供的工具准备手机测试起点、执行用例、采集截图和日志、记录结果；遇到缺陷时返回复现步骤与证据，由主代理修复后再交给 Terra 重测，不在测试过程中自行改代码。

创建测试子代理时显式设置 `model="gpt-6.1-terra"`、`reasoning_effort="medium"`、`fork_turns="none"`，并传入测试范围、授权边界、工具用法和证据目录；不要仅在提示词中写模型名称。可用时复用同一测试子代理。模型不可用或无法设置时如实说明，不静默换模型，也不将未指定推理强度的历史测试标成 Medium。用户针对某次任务另有指定时按该次要求执行。

主代理监控阶段进展、独立查看关键截图和日志并验收；操作期间不与子代理同时占用串口或注入输入。委派不扩大操作授权，发送消息等动作仍须属于用户已授权范围。测试流程按所属用例文档执行，模型与分工约定以本节为准。
