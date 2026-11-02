# 新后端实施记录

2026-11-02：统一 Backend 迁移完成。按“新目录开发 → 验收切换 → 备份后清理旧代码”执行；未刷固件、未清配对、未发送手机消息。

## 最终架构与部署

- 顶层 Backend 下只有 esp32、airplay 两个核心子模块；公共 API、完整任务编排、配置和主控管理位于 Backend。
- 8892 是唯一客户端控制与截图入口。8891 为回环内部快照，8894 为认证主控 IPC；8893 保留手机令牌校准页。8890 无监听，也未发现相关计划任务、启动文件夹或 HKCU/HKLM Run 启动项。
- 三个 Python 工作进程：主控、API、取帧，外加 UxPlay。六个主要线程：主控事件循环；API 事件循环、固定编排、串口所有者；取帧 HTTP 与管道读取。Windows venv 启动器和库线程另计。
- 串口为进程内服务，唯一 actor 线程执行打开、命令、心跳、复位、关闭。完整输入任务串行，GET /frame 可同时读取；未知结果不重放。
- 主控负责统一启动、取帧复用、API 监督及认证接管；API 重启保留取帧与 UxPlay。新包已不依赖旧 pc/ 或 upstream-windows 模块。独立 upstream-windows 项目保留，不属于清理范围。
- 主项目 .runtime 数据路径保持；本机配置 backend/.deployment.json 含凭据且忽略 Git。用户登录启动项 iPhone Backend.lnk 只启动 backend/tools/deploy.py start。全新系统登录未另行执行；已验证快捷方式目标、同入口启动及真实服务切换。

## 验收证据

| 要求 | 证据与结果 |
|---|---|
| 包组织、入口和资源 | test-wheel.log：构建 wheel、隔离安装、从独立目录导入，校准网页资源存在，配置路径显式指定。test-postcleanup.log：删除旧文件后异 cwd 的模块入口、导入及资源仍通过 |
| API、快照语义、心跳和互斥 | test-postcleanup.log：125/125 测试通过、无跳过；涵盖原控制回归、串口 actor、快照、并发、校准、管理、无设备状态和日志认证/脱敏 |
| 并行取图 | .runtime/acceptance/final/：动作期间 12 次 GET /frame 均 200；画面真实时间和 ID 保留，过期帧不冒充动作结果 |
| 新取帧独立启动 | .runtime/capture-cutover/：旧 capture 退出后，由新主控从 backend 包启动取帧；8890 不参与。用户重新投屏后新 owner 收到画面 |
| 主控恢复与 API 交接 | test-manager-recovery/test-recovery 系列历史记录、test-manager-identity.log、test-manager-reload.log；主控核验 PID/创建时间/实例/配置/认证，接管时保持 API，随后干净重启 API |
| 实机设备重置 | test-runtime.log 与 .runtime/acceptance/reset-*.json：新 boot_id、HID 恢复，同请求不重复复位；校准运行态失效，投屏进程保持 |
| 复位状态并发修复 | test-reset-consistency.log：专项通过、原失败用例连续 10 次通过；test-reset-consistency-full.log：125 项通过 |
| 新后端真实点击闭环 | .runtime/acceptance/home-final/combined-click.jpg 及结果：人工确认指针后，以真实新帧执行一次 reviewed click，动作后新帧显示搜索界面和键盘；主代理独立审图确认，无输入文字 |
| 清理后运行 | test-postcleanup.log：API 重启通过，capture/UxPlay PID 不变，重启后 /frame 200；无旧 pc 运行引用；8890 零监听 |
| 客户端、技能、启动和文档 | README 中英文、用户使用说明、AGENTS 导航、iphone 技能已更新；正式技能校验通过，启动快捷方式目标检查通过，15 份目标文档本地链接无缺失 |

所有测试执行及 iPhone UI 操作由 GPT-6.1 Terra / Medium 完成，主代理实现代码、审查日志和关键截图。上述日志位于本 doc/，运行截图和含私人画面的证据位于 backend/.runtime/，不随 Git 提交。

## 迁移与清理

1. 在 backend/ 内复制、重构、测试，原始源码哈希记录在 migration-manifest.json 和 migration-extras.json。
2. 在旧文件保留期间完成真实 API/串口交接、设备重置、新取帧启动、用户重连投屏及点击闭环。
3. 测试通过后核对源哈希，备份并删除 79 个已替代文件（旧源码、旧测试、迁入文档、资源、依赖清单和缓存）。旧 pc/ 已删除；固件、独立实验、其他项目和历史证据保留。
4. 清理后再次完成 125 项回归及实机 API 重启/取图冒烟。

源码备份：backend/.runtime/migration-backup/20261102-210651/legacy-files.zip，逐文件比对原始内容后才删除；deleted.json 记录精确清单，包括迁移前未提交内容。文档与旧技能备份：backend/.runtime/migration-doc-backup/20261102-210649/。

## 已知边界与失败记录

- 本次替换 capture 时 UxPlay PID 发生变化，新组件在旧接收器缺失后启动了接收器；旧进程退出原因没有足够证据确认。切换后需要用户重新投屏，不能声称 capture 升级无中断。普通 API/主控重启保持投屏已分别验证。
- 人工原生定位仍需实际看图；诊断坐标不能代替校准，且 5 秒几何新鲜度门禁和 120 秒审查证据期限保持。静止画面可能使点击明确 not_started；不能通过篡改时间或重放未知输入解决。本轮最后一次使用按钮内部已确认的极小往返生成真实新帧，随后在同一次工具调用中提交经审查点击，通过闭环验收。
- 完整屏幕校准、跨应用覆盖、锁屏恢复及长期压力测试不因本次架构迁移自动视为通过。相关历史证据继续保留。
- 不安装或重新刷写固件；历史 serial_smoke 工具迁至 backend/tools/，仅做语法检查，常驻 API 占有串口时不得运行。

当前使用与恢复操作见 [使用说明](usage.md)，设计见 [架构说明](architecture.md)，原始要求及实施约束见 [实施计划](unified-backend-plan.md)。
