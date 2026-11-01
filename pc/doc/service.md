# 常驻串口控制服务

[原生绝对 HID 与自动校准集成计划](native-hid-integration-plan.md) 已部分实施；全屏、跨应用与性能验收尚未完成，默认仍为视觉后端。

## 原生绝对 HID（局部验证）

正式固件 0.3.0 增加独立绝对报告。`coordinate_backend="native_hid"` 可显式选择；默认 `visual_relative` 保持不变。原生动作在单串口 worker 内提交，不进行视觉纠偏；每次预检仍读取一张近期 snapshot v1 检查截图几何，即使 `capture=false` 也不跳过。`report_submitted` / `click_submitted` 只代表提交，业务效果须另外核验。请求前预检失败返回 `not_started`；输入结果未知时不得自动重放。

启动前需设置 `BLE_HID_DEVICE_ID`（固件 status 返回的稳定设备 ID）、`BLE_HID_PHONE_ID`（明确绑定的手机标识）和 `BLE_HID_CALIBRATION_HOST`（手机可访问的本机地址）；可选 `BLE_HID_CALIBRATION_PORT`（默认 8893）和 `BLE_HID_COORDINATE_BACKEND`（默认 `visual_relative`）。控制 API 只监听本机；校准页接收器独立监听局域网、只服务令牌页面与事件，不暴露控制路由。不要将令牌网址分享给其他人。确保指定端口空闲；服务不修改防火墙。

使用 `service_client.start_calibration(request_id, "calibrate")` 创建异步任务，打开结果中的 `page_url`，用 `get_calibration(job_id)` 查看阶段，用 `cancel_calibration(job_id)` 取消。`verify` 用于重启或断连后的既有配置复验。页面和待测目标通过随机会话、探针和确认关联，必须收到完整 down/up/click 链；失败不重试。校准的候选证据先保存为 `validation_passed` / `not_committed`，`calibration-profile.json` 原子替换是唯一激活提交点。重启后配置为 `needs_verification`。任务采样期间普通动作返回冲突；等待打开页面期间仍可导航。

原生点击仅在当前已验证范围内允许，超范围或配置未验证均在输入前拒绝。仅为诊断移动，可显式设置 `diagnostic_uncalibrated=true` 使用未校准恒等映射；该选项不能用于点击，也不扩大配置范围。`move_to` / `click_at` 客户端包装和 `act(..., coordinate_backend="native_hid", capture=False)` 保留现有调用形状。CLI 的 `--operation` 支持 `action`、`health`、`start-calibration`、`get-calibration`、`cancel-calibration`，旧 `--steps` / `--output` 用法仍可用。

2026-10-31 实机：正式局部校准完成 6 训练、5 留出和 5 验证点击；主代理审计 16 组完整事件。重启后 `needs_verification` 门禁拒绝点击，复验 10 次通过；范围外点击和等待页取消均无输入。当前验证范围仅 `[186,455,310,651]` 截图像素，不能用于主屏和微信全流程。范围内 click_at 的同 ID 重试只产生一次绝对点击命令和三份报告；最终截图显示指针，但该测试页面接收器已关闭，不能据此声称页面收到点击。全屏、跨应用、断连、三轮同条件性能及正式原生微信尚未验收。

## 视觉相对后端

`POST /action` 的 steps 新增 `move_to`、`click_at`，必须提供 `x`、`y`，单位为当前 496×1080 AirPlay 截图像素（x=0..495、y=0..1079）。示例：`{"op":"move_to","x":250,"y":540}`；`{"op":"click_at","x":459,"y":578}`。旧 `move`/`tap` 仍使用相对 HID 单位。

算法位于 `pc/absolute_pointer.py`、`pc/pointer_vision.py`，正式服务不依赖 task/lab。整个视觉循环在单一串口队列任务中运行，不递归调用 HTTP，不允许与其他请求交错。每次启动重新定位，必要时有限换方向探测和重建参考；建议先 move_to(250,540) 验证居中，但不能靠未知相对位移假定已到中央。动作中始终释放按钮，不支持与 key_down/mouse_down 同请求组合。

按距离分配最多16次移动；连续两次实际位移小于1.5 px时返回 no_progress，阻止点击和后续步骤。误差≤6 px才到位；click_at再次读取快照检查位置及参考图，然后单次左键点击。无输入复查允许使用最近5秒内的已有v1快照（静止屏幕可能不产新帧），不能把两次读取同一frame_id称为两帧独立证据；真实移动仍要求动作等待之后的帧。页面变化、截图失败、取消、截止时间、未知位置均停止，不自动重放。视觉循环即使capture=false也必须取帧；该选项只控制最终返回图。整体沿用请求截止时间，绝对动作建议 timeout_seconds=45，不在一个请求里塞入许多跨页目标。

返回 `absolute` 数组包含每步定位轨迹、状态、内部请求数和 evidence_directory；其 requests 是内部动作/截图轮数，不是 HTTP 请求数。中间图片和元数据保存在 `.runtime/service/absolute/`。失败时保留 partial_unknown 和 released；HTTP 200 本身不代表动作成功。click_submitted也不代表 UI 已命中，仍需检查返回画面。capture_error只表示最终截图失败，不重新点击。

当前支持当前截图尺寸及约14 px半径指针；极限圆角可能不可达，不能保证任意坐标。旧参考图不跨页面复用。点击前检查与真正点击仍有时间间隔，不保证动态页面原子命中。固件不变。

整合实机验收（2026-10-31）：同一 6.1 Terra / Medium 子代理通过正式接口先居中，再完成微信文件传输助手测试，主代理看图通过；消息 `test_20261031_104014_b3e5` 单次发送。耗时274.258秒，19次HTTP、39轮内部绝对定位动作/取帧；旧相对服务成功轮119.455秒，本轮没有提速。包含一次定位门禁失败后居中恢复及75.191秒诊断协调间隔，不作严格同条件性能结论。28项自动化测试通过，静止复查修正后4项绝对动作回归测试再次通过。完整证据见 `tests/case1_wechat_send_hello/runs/20261031_104014_b3e5/evaluation.md`。

本服务保留现有固件协议，在 PC 上复用单一串口会话，并将动作、界面等待、新鲜截图合并到一个 HTTP 请求。模型不再为每次操作启动串口客户端、握手、关闭端口，再单独请求截图。API 监听 `127.0.0.1:8892`；现有 AirPlay 取帧仍使用 8891，旧 USB 后端 8890 不参与。

## 运行

在项目根执行：

```powershell
python -m pip install -r pc/requirements-service.txt
python pc/service.py
```

使用一个服务进程、一个 worker，不开启 reload 或多 worker。服务占用串口期间，不运行 bridge.py 或其他直接访问串口的程序。退出服务释放输入并关闭连接；不关闭 AirPlay。串口故障会显示在 `/health`，不自动重放或重连执行未知动作，确认现场状态后显式重启服务。

取帧适配器依赖 httpx 0.28.1（已列入 requirements-service.txt）。帧源需支持原子快照 v1：`/frame` 保留 JPEG 正文与 `X-Frame-Time`，并提供 `X-Frame-Snapshot-Version: 1` 和非空 `X-Frame-Id`；204 无帧响应也带版本标识。对应源码位于既有帧源项目的 `server/hidagent/frame_snapshot.py`、`screen_pipe.py` 和 `capture_worker.py`。旧帧源缺少版本时返回 capture.state=invalid 和明确的升级提示，不自动重启或替换现有服务。2026-10-31 已部署快照 v1 帧源并验证；当前控制服务以 allow_legacy_frames=false 统一使用 v1，不启用旧接口兼容。

## Agent 使用

`GET /health` 返回最近一次串口状态、故障、活动请求、服务 instance_id。`POST /action` 提交以下结构：

```json
{
  "request_id": "unique-id",
  "steps": [{"op":"tap","dx":20,"dy":-10}],
  "capture": true,
  "settle_ms": 700,
  "timeout_seconds": 20
}
```

步骤支持 move、click、tap（相对移动后点击）、drag（当前位置按住并相对拖动）、scroll、text、key、key_down/key_up、mouse_down/mouse_up、wait。dx/dy 为 HID 相对单位，不是截图像素；tap 的 dx/dy 仍为相对单位；绝对目标须用 move_to/click_at 的 x/y。按钮编号 1–3；按键 usage、modifiers 与现有串口客户端一致。

所有步骤先整体校验再执行。每次最多32步、256个可打印ASCII字符；text不含回车，发送消息必须显式提供 Enter 操作。临时按住只允许在同一请求内组合，整个请求结束或失败都释放输入，不支持跨HTTP请求维持按键。可把移动、按下、拖动、释放作为一次请求提交，但跨页面仍需先看结果。

`service_client.act(steps, output)` 调用服务并将返回的 JPEG 保存为 output，同时写入 `.json`（兼容 live_case.py）、`.request.json`、`.result.json`。客户端不访问串口，不自动重试。Agent 应在同一次工具调用里执行 act 并用 view_image 展示 output，避免操作与看图拆成两轮。

客户端新增可选 `base_url`，默认仍为 `http://127.0.0.1:8892`，用于隔离实例和测试；原调用方式不变。

```python
from service_client import act
result = act([{"op": "tap", "dx": 20, "dy": -10}], "run/after.jpg")
# text不自动提交
result = act([{"op": "text", "text": "hello"}], "run/draft.jpg")
```

steps=[] 为只观察；静止页面超过5秒无新帧时可能报截图过期，需要明确的小幅移动再观察。服务不会偷偷移动指针或重做输入以刷新截图。输入后默认等待700ms，只接受晚于等待结束时间的新帧；等待最多2.5秒，截图失败时仍保留 input_state=reports_sent，绝不将其等同于输入失败并重放。

## 执行与故障语义

专用线程拥有全部 Bridge 读写；HTTP层不直接接触串口。队列最多8项，超限429。等待队列时间计入请求截止时间；过期任务不开始输入。空闲每约0.4秒心跳，正常动作按现有固件节奏执行，长移动拆分为最多64单位以缩短取消响应。

客户端断开、显式 `POST /cancel/{request_id}`、截止时间或服务退出都会请求取消；在串口请求边界检查，不承诺中断已提交的BLE报告。单次串口等待最长3秒；固件自身2秒租约仍作为异常兜底。每个动作结束强制 release_all，避免常驻心跳维持遗留按下状态。

相同服务进程内，同 request_id 和相同负载返回原结果，不重复执行；负载冲突409。保留最多1000条请求历史，满后拒绝新请求，不静默淘汰后重新执行旧ID。服务重启会改变 instance_id 并丢失去重记录，因此不跨重启重试未知动作。

返回 input_state、completed_steps、released、时间戳与 frame。partial_unknown 表示可能执行部分动作；capture_error 单独描述截图失败。reports_sent只说明报告提交，不表示手机效果通过。日志保存在项目 `.runtime/service/serial.jsonl` 和 actions.jsonl；含输入文字和截图，仅本地保存。

增量结构保留上述全部旧字段：action.state 与 input_state 一致；release.state 为 released 或 unknown；capture.state 为 available、stale_or_unknown、unavailable、cancelled、invalid 或 skipped。capture=false、动作或释放失败时为 skipped；取帧失败继续返回 capture_error，无成功 frame。frame 内保留 jpeg_base64、frame_time、age_seconds，新增 frame_id、snapshot_version、received_at、after_action 和有动作时的 settled_at。JPEG 不在 capture 内重复存储。after_action 只说明时间关系，不证明手机上的业务成功。

pc/frame_capture.py 使用独立异步 HTTP 读取线程，总预算取请求剩余时间与 2.5 秒的较小值，单次读取最多 0.7 秒，正文最多 8 MiB；整体截止覆盖慢速正文及分块响应。读取结束会取消并回收异步任务和线程。串口所有者继续每约 0.4 秒续租，取帧线程不调用 Bridge；否则截图等待可能超过固件 2 秒租约。心跳失败会置 fault、终止截图并保留输入结果，不重连重放。取消可以独立终止网络读取，但已在途的串口心跳仍可能等待其最长 1 秒的回执；隔离网络测试的 200 ms 返回余量不代表真实串口端到端上限。

## 验证

`python -m unittest discover -s tests -v` 覆盖旧客户端及服务的取消释放、去重、整批校验、过期队列任务不执行、截图失败不重放。真实输入仍用微信用例由操作子代理执行、主代理看图验收，且记录实际模型和推理强度。性能比较区分服务请求耗时、操作起止帧和总交付时间，不以单轮差异宣称严格模型或API提速倍数。

隔离测试依赖使用 `python -m pip install -r tests/requirements.txt`；测试仅使用模拟串口、合成 JPEG 和随机 loopback 端口。新增覆盖真实 HTTP 客户端与输出文件、原始帧去重、settled_at 边界、旧帧源能力拒绝、慢速读取取消、关闭及断开请求、证据写盘失败、截图期间租约续期。此前微信输入用例属于旧服务版本；快照 v1 已部署，并通过只读取帧和空步骤请求验证。

实机发现：仅时间戳新鲜仍可能出现编辑状态尚未稳定。关键草稿编辑建议 settle_ms=1500，发送前再做一次不修改文字的观察（必要时明确微移刷新画面），两次均精确匹配再提交。禁止将旧草稿截图触发的修正盲目重复。首轮服务测试缺失首字母，作为失败记录保留。

## 实机验收（2026-10-31）

6.1 Terra / Medium 通过本服务完整执行微信用例，主代理独立验收通过。修正后成功轮次 20261031_092538_7cee 耗时119.46秒；此前直接串口轮次273.13秒，单轮观察减少约56.3%，串口会话40次降为1次，截图24张降为14张。起始状态与恢复步骤不同，不作严格因果对照。首轮服务测试少一个t判失败，未计入成功提速。

实际报告在 `tests/case1_wechat_send_hello/runs/20261031_092538_7cee/evaluation.md`（相对项目根）。13项单元测试通过，固件未修改。保持同一串口会话、合并动作与截图已经实测；坐标校准、自动重连和长期运行仍待完善。

去重返回原请求的原始截图与时间戳，不是重新观察；需要当前画面时用新的只观察请求。日志/截图写入失败会使服务进入fault并保留输入结果，不能据此重新执行原动作。

## 固件0.3.3修饰键兼容

0.3.3在固件内部先建立修饰键（200ms），再按下字符并分阶段释放，修复本机iPhone上同时报告Shift与字符时大写/下划线丢失的问题。PC `text`、`key`、`key_down`调用不变；仅含修饰键的操作增加等待。高级诊断可用usage=0表示modifier-only，usage1–3禁止。正式接口已实机输入精确a_A-1，Ctrl+Space显示English (US)选中；设计、失败对照和升级边界见[修饰键时序](../../firmware/serial_ble_hid/doc/keyboard-modifiers.md)。

当前原生动作的几何预检对有同源绑定的校准配置可读取静态旧帧（仅确认几何），会保留真实时间并标记cached_static；动作后的结果截图仍要求新鲜。未校准诊断只允许move_to，不允许click_at；旧配置没有帧绑定时诊断移动要求5秒内新鲜几何帧。不能据cached_static声称界面当前状态已确认。
