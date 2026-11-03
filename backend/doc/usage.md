# 新 Backend 使用说明

## 绝对定位与迁移状态

2026-11-03 已部署固件 0.4.2、guard v9；143 项后端测试通过，固件沿用先前 3 项布局测试及构建/刷写/读回校验。九点校准、五点留出、三轮复位重连及 36 点范围复验完成；verified_region 为 [24,60,471,1010]，另验证四边中点。四角实际内缩未通过，物理旋转和长期稳定性未测。静止截图 observation_only 不等于效果确认。详见 [验收与边界](direction-root-cause.md)。

- 业务接口仅接受 `native_hid`，`move_to` / `click_at` 使用截图整数坐标；视觉用于核对，不再相对步进纠偏。
- 删除 `move`、`tap`、`drag`、dx/dy、`visual_relative` 请求及 CLI 相对移动；Bridge 和固件均拒绝 `mouse.move`。没有相对诊断或兜底入口。
- 保留原地 `click`、滚轮和键盘。滚轮只滚动内容，Report 2 的 X/Y 固定为零；保留已有报告布局以避免本轮额外改变配对描述符。
- 校准全程使用绝对指令。校准文件在 `IPHONE_RUNTIME_DIR/service/calibration-profile.json`；历史文件不会自动变成当前连接已验证。位移方向异常、重复坐标不响应等可靠性问题不能通过改参数掩盖。
- 旧环境设置 `BLE_HID_COORDINATE_BACKEND=visual_relative` 仅在启动时告警并归一为 `native_hid`；新配置写 `native_hid`。旧客户端请求相对动作会在输入前拒绝。

后续先验证 API 拒绝旧动作、默认原生定位、无效校准停止且无兜底、按钮释放与请求去重，再执行绝对校准、同点重复、重连、方向变化及实际点击回归。不要把本次改代码记为测试通过。原因与实机证据见 [踩坑记录](direction-root-cause.md)。

## 安装与启动

全新电脑请先按 [首次部署流程](../../doc/user/deployment.md) 使用 `scripts/setup.ps1` 创建环境与配置，再通过 `deploy.py start` 启动。下面的 prepare/handover 是历史迁移流程，不是首次安装要求。`bootstrap.py check` 提供不发送输入的部署验收。

使用 Python 3.11 或更高版本安装 backend/ 中的包。当前本机环境为 backend/.venv，使用已安装的系统依赖；可编辑包已安装，正式部署入口统一为 `python -m iphone_backend`。

启动前设置绝对 IPHONE_PROJECT_ROOT、IPHONE_RUNTIME_DIR、UXPLAY_DIR、HIDAGENT_DATA；迁移时沿用 BLE_HID_DEVICE_ID、BLE_HID_PHONE_ID、BLE_HID_CALIBRATION_HOST，新部署仅在启用相应坐标校准功能时配置这些值。BLE_HID_RESTART_TOKEN 至少 32 字符。不要把 token 写入命令记录或提交 Git。8892/8891/8894 的运行服务必须属于同一已识别部署，不能平行启动第二份。

本机 tools/deploy.py prepare 从已核实的旧进程提取配置到忽略 Git 的 .deployment.json；handover 仅用于一次旧 API 到新版的受控交接；start 从本地配置启动统一主控。不要在未知状态下反复执行 start。使用前核对 implementation-status.md 中的部署状态。

## 接口

| 接口 | 行为 |
|---|---|
| POST /action | 现有动作请求与结果格式，支持原生绝对定位、原地点击、滚轮、键盘及可选结果截图 |
| GET /frame | 最新 JPEG；保留 X-Frame-Id、X-Frame-Time，204 表示无帧 |
| GET /screen/status | 可达性、年龄、新鲜度；静止/卡住无法区分时返回 stale_or_unknown |
| POST /screen/start | 认证；request_id 去重，启动或复用取帧，202 后查询任务 |
| GET /screen/start/{request_id} | 认证；取帧启动任务状态 |
| GET /device/status | ESP32 状态和 fault；不执行输入 |
| POST /device/reset | 认证；request_id、expected_instance_id、expected_device_id、expected_boot_id；202 不代表已重启 |
| GET /device/reset/{request_id} | 认证；重启确认、当前 BLE readiness 或未知结果 |
| GET /health | Backend 存活、分模块状态、主控状态及原有控制健康字段 |
| GET /system/logs?source=backend&limit=100 | 认证；来源固定为 backend、esp32、airplay，最多 500 行/256 KiB |
| POST /admin/restart | 认证；request_id、expected_instance_id；忙碌或主控不可用时拒绝 |

认证使用 `Authorization: Bearer <token>`，管理入口只接受回环来源。错误截图或超时不会重新点击。/frame 可在动作期间读取，但确认动作效果必须使用满足动作完成时间的新帧。

## 原地点击

固件 0.4.1 的设备名为 `S3-Click-Buttons`。手机需开启辅助触控，并为主按钮保留单点操作；设备页“自定义按钮需要启用辅助触控”是通用说明，不能据此判断主开关状态。固件以独立 Report 5 发送按钮，不带 X/Y，`click` 不会重新定位到上一次坐标。

确认可见指针位于目标后，通过现有 `POST /action` 提交 `steps: [{"op":"click","button":1}]`，或在项目根目录调用客户端：

```python
from iphone_backend.service_client import click

result = click('.runtime/click.jpg')
```

`click()` 不需要坐标或校准 profile，也不执行定位验证，调用方应先确认当前位置。`click_at(x,y)` 是有定位门禁的另一种操作，不能用未知位置原地点击替代。reports_sent/released 仅证明本地提交与释放，仍需新鲜画面或可信事件；未知结果先检查保存的 request_id，不自动再次点击。

本轮仅验收主按钮。移动仍观察到方向旋转，不能据原地点击通过宣称坐标 API 稳定；正向校验保持，异常不放行。设计与证据边界见 [按钮报告说明](../../firmware/serial_ble_hid/doc/mouse-buttons.md)。

## 故障与恢复

设备重置会使当前定位校准运行态失效；恢复 BLE 后仍需按原有规则验证定位。reset 不刷固件、不清配对，结果未知时先查询状态，不能自动重发。

主控在 8894 的认证 /health 报 attention_required 时查看 api_error；它不会因未知 API 占用端口而强杀或另开串口。重启主控可以接管注册过且身份一致的存活 API。未知旧实例需明确交接。

本机更新主控代码或部署配置后，使用 `backend/.venv/Scripts/python.exe backend/tools/deploy.py restart-manager`。该工具要求主控/API 空闲就绪，并核对认证身份与持久登记，只替换主控自身，保留 API、串口、取帧和 UxPlay；身份不符时停止。输出证据位于 backend/.runtime/deployment/manager-reload.json。此操作与 `/admin/restart`（只更新 API）分开。

取帧换版本可能需要手机重新投屏。API 普通重启应保持取帧与 UxPlay PID 不变。源码清理必须等完整验收及运行实例切换后进行。

## 有界实机回归

独占手机操作时，在仓库根用 `backend/.venv/Scripts/python.exe backend/tools/reliability_soak.py --output .runtime/soak-unique --rounds 3 --idle-seconds 60 --reset-between-rounds` 复现。必须从 verified profile 开始，输出目录必须是新的；每轮静止等待后测试 12 个绝对目标，包含同点重复、近角和四边中点。工具不点击或输入文本，失败立即停止，重连结果未知不再次提交复位。截图、请求、绑定、耗时及错误都保存在输出目录。

复位后旧 profile 失效，后续无按钮采样明确使用 diagnostic_uncalibrated；工具不把诊断成功写成正式校准。结束需通过 `/scope-validations` 和逐图审核恢复 verified 状态。当前四个最外角未支持，轮数和时长有限的成功不能代替长期稳定性保证。
