> 迁移说明：本文件保留原设计与历史证据；当前启动、路径及管理接口以 [Backend 使用说明](usage.md) 为准。

# 常驻串口控制服务

[原生绝对 HID 与自动校准集成计划](native-hid-integration-plan.md) 已部分实施；全屏、跨应用与性能验收尚未完成，默认仍为视觉后端。

## 原生绝对 HID（局部验证）

正式固件 0.3.0 增加独立绝对报告。`coordinate_backend="native_hid"` 可显式选择；默认 `visual_relative` 保持不变。原生动作在单串口 worker 内提交，不进行视觉纠偏；每次预检仍读取一张近期 snapshot v1 检查截图几何，即使 `capture=false` 也不跳过。`report_submitted` / `click_submitted` 只代表提交，业务效果须另外核验。请求前预检失败返回 `not_started`；输入结果未知时不得自动重放。

启动前需设置 `BLE_HID_DEVICE_ID`（固件 status 返回的稳定设备 ID）、`BLE_HID_PHONE_ID`（明确绑定的手机标识）和 `BLE_HID_CALIBRATION_HOST`（手机可访问的本机地址）；可选 `BLE_HID_CALIBRATION_PORT`（默认 8893）和 `BLE_HID_COORDINATE_BACKEND`（默认 `visual_relative`）。控制 API 只监听本机；校准页接收器独立监听局域网、只服务令牌页面与事件，不暴露控制路由。不要将令牌网址分享给其他人。确保指定端口空闲；服务不修改防火墙。

使用 `service_client.start_calibration(request_id, "calibrate")` 创建异步任务，打开结果中的 `page_url`，用 `get_calibration(job_id)` 查看阶段，用 `cancel_calibration(job_id)` 取消。`verify` 用于重启或断连后的既有配置复验。页面和待测目标通过随机会话、探针和确认关联，必须收到完整 down/up/click 链；失败不重试。校准的候选证据先保存为 `validation_passed` / `not_committed`，`calibration-profile.json` 原子替换是唯一激活提交点。重启后配置为 `needs_verification`。任务采样期间普通动作返回冲突；等待打开页面期间仍可导航。

原生点击允许使用现有映射外推到已验证范围之外；verified_region 仅记录实测覆盖范围，不作为点击边界。配置未验证、目标或换算后坐标超出屏幕仍拒绝，连续指针响应检查仍执行。仅为诊断移动，可显式设置 `diagnostic_uncalibrated=true` 使用未校准恒等映射；该选项不能用于点击，也不扩大配置范围。`move_to` / `click_at` 客户端包装和 `act(..., coordinate_backend="native_hid", capture=False)` 保留现有调用形状。CLI 的 `--operation` 支持 `action`、`health`、`start-calibration`、`get-calibration`、`cancel-calibration`，旧 `--steps` / `--output` 用法仍可用。

2026-10-31 实机：正式局部校准完成 6 训练、5 留出和 5 验证点击；主代理审计 16 组完整事件。重启后 `needs_verification` 门禁拒绝点击，复验 10 次通过；范围外点击和等待页取消均无输入。当前验证范围仅 `[186,455,310,651]` 截图像素，不能用于主屏和微信全流程。范围内 click_at 的同 ID 重试只产生一次绝对点击命令和三份报告；最终截图显示指针，但该测试页面接收器已关闭，不能据此声称页面收到点击。全屏、跨应用、断连、三轮同条件性能及正式原生微信尚未验收。

## 视觉相对后端

`POST /action` 的 steps 新增 `move_to`、`click_at`，必须提供 `x`、`y`，单位为当前 496×1080 AirPlay 截图像素（x=0..495、y=0..1079）。示例：`{"op":"move_to","x":250,"y":540}`；`{"op":"click_at","x":459,"y":578}`。旧 `move`/`tap` 仍使用相对 HID 单位。

算法位于 `backend/src/iphone_backend/absolute_pointer.py`、`backend/src/iphone_backend/pointer_vision.py`，正式服务不依赖 task/lab。整个视觉循环在单一串口队列任务中运行，不递归调用 HTTP，不允许与其他请求交错。每次启动重新定位，必要时有限换方向探测和重建参考；建议先 move_to(250,540) 验证居中，但不能靠未知相对位移假定已到中央。动作中始终释放按钮，不支持与 key_down/mouse_down 同请求组合。

按距离分配最多16次移动；连续两次实际位移小于1.5 px时返回 no_progress，阻止点击和后续步骤。误差≤6 px才到位；click_at再次读取快照检查位置及参考图，然后单次左键点击。无输入复查允许使用最近5秒内的已有v1快照（静止屏幕可能不产新帧），不能把两次读取同一frame_id称为两帧独立证据；真实移动仍要求动作等待之后的帧。页面变化、截图失败、取消、截止时间、未知位置均停止，不自动重放。视觉循环即使capture=false也必须取帧；该选项只控制最终返回图。整体沿用请求截止时间，绝对动作建议 timeout_seconds=45，不在一个请求里塞入许多跨页目标。

返回 `absolute` 数组包含每步定位轨迹、状态、内部请求数和 evidence_directory；其 requests 是内部动作/截图轮数，不是 HTTP 请求数。中间图片和元数据保存在 `.runtime/service/absolute/`。失败时保留 partial_unknown 和 released；HTTP 200 本身不代表动作成功。click_submitted也不代表 UI 已命中，仍需检查返回画面。capture_error只表示最终截图失败，不重新点击。

当前支持当前截图尺寸及约14 px半径指针；极限圆角可能不可达，不能保证任意坐标。旧参考图不跨页面复用。点击前检查与真正点击仍有时间间隔，不保证动态页面原子命中。固件不变。

整合实机验收（2026-10-31）：同一 6.1 Terra / Medium 子代理通过正式接口先居中，再完成微信文件传输助手测试，主代理看图通过；消息 `test_20261031_104014_b3e5` 单次发送。耗时274.258秒，19次HTTP、39轮内部绝对定位动作/取帧；旧相对服务成功轮119.455秒，本轮没有提速。包含一次定位门禁失败后居中恢复及75.191秒诊断协调间隔，不作严格同条件性能结论。28项自动化测试通过，静止复查修正后4项绝对动作回归测试再次通过。完整证据见 `tests/case1_wechat_send_hello/runs/20261031_104014_b3e5/evaluation.md`。

本服务保留现有固件协议，在 PC 上复用单一串口会话，并将动作、界面等待、新鲜截图合并到一个 HTTP 请求。模型不再为每次操作启动串口客户端、握手、关闭端口，再单独请求截图。API 监听 `127.0.0.1:8892`；现有 AirPlay 取帧仍使用 8891，旧 USB 后端 8890 不参与。

## 运行

在项目根执行：

```powershell
python -m pip install -e backend
# 按 usage.md 配置绝对路径和管理 token 后：
python -m iphone_backend
```

使用一个服务进程、一个 worker，不开启 reload 或多 worker。服务占用串口期间，不运行 bridge.py 或其他直接访问串口的程序。退出服务释放输入并关闭连接；不关闭 AirPlay。串口故障会显示在 `/health`，不自动重放或重连执行未知动作，确认现场状态后显式重启服务。

取帧适配器依赖 httpx 0.28.1（已列入 backend/pyproject.toml）。帧源需支持原子快照 v1：`/frame` 保留 JPEG 正文与 `X-Frame-Time`，并提供 `X-Frame-Snapshot-Version: 1` 和非空 `X-Frame-Id`；204 无帧响应也带版本标识。当前源码位于 backend/src/iphone_backend/airplay/，主控统一启动独立取帧进程。旧帧源缺少版本时返回 capture.state=invalid 和明确的升级提示，不自动重启或替换现有服务。2026-10-31 已部署快照 v1 帧源并验证；当前控制服务以 allow_legacy_frames=false 统一使用 v1，不启用旧接口兼容。

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
from iphone_backend.service_client import act
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

当前 airplay/frames.py 使用异步 HTTP。固定编排线程调用取帧，串口 actor 独立心跳，不为每次截图创建线程。读取总预算取请求剩余时间与 2.5 秒的较小值，单次读取最多 0.7 秒，正文最多 8 MiB。完整任务独占输入但允许独立 GET /frame；取消、超时和截图失败不重放输入。线程职责见 architecture.md。

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

## 无点击校准的启动检查（2026-11-02）

`native_hid` 的 `diagnostic_uncalibrated=true` 现在只允许纯 `move_to` 批次；不允许混入相对点击、键盘或其他动作。它以当前设备身份、连接状态、绝对报告就绪状态和五秒内 snapshot v1 画面检查尺寸与帧序列，不依赖旧 profile 的截图源或坐标映射。不同截图源的帧序列分别建立基线，同源倒退仍拒绝。此路径不生成或升级 profile，正式 `click_at` 保持原有校准和范围检查。

原因：AirPlay 重建会话后，旧 needs_verification profile 的 frame_source_id 曾让“重新校准所需的无点击移动”在输入前被拒绝，形成启动依赖。修复仅解除诊断移动的旧记录依赖；不据此宣称定位精度或点击已验证。测试子代理运行完整 68 项测试通过；实机结果见 [主屏原型记录](../../task/relative-click/doc/api-prototype-result.md)。

## 主屏视觉校准与固定方向约束

原生映射沿用 `screen = slope * command + offset`，`move_to` 与 `click_at` 均调用 `native_pointer.map_target` 反算，再转为 HID 0..32767。当前固定竖屏只接受正斜率 0.8..1.2 和接近原点的偏移。负斜率视为方向异常并拒绝，不自动重新拟合来适配。历史版本曾接受负斜率，该行为已撤回。

服务生命周期与重启接口见[服务管理](service-management.md)：独立管理进程、空闲时接受重启、优雅释放串口、旧实例退出后启动新实例。已完成一次真实API重启和版本加载；故障分支仅离线覆盖。

坐标约定固定为左上原点、X向右、Y向下。正向轴约束同时在 profile 验证和底层 map_target 执行；直接调用转换函数也不能绕过限制。move_to 与 click_at 共用该检查，方向异常仍由连续响应保护停止操作并保存证据，不提供自动翻转模式。

指针响应检查当前为v4：图标重叠时由完整足迹测得半径，以可见圆弧定位；不再把残缺差分区域的质心当成可靠指针中心。8px位置误差、2px稳定性及80px外部变化预算保持不变。当前完成局部6+5点验证与单次相机打开测试，范围和限制见[连续指针响应验收](pointer-response-validation.md)。

正式采集器 `python -m iphone_backend.visual_calibration_capture --scene home --command X Y --output 新前缀` 通过纯诊断移动取证，默认100ms等待。主代理审图后记录观测中心和不确定度。6个训练点（第3和第6相同）拟合；以 `--target X Y --training 训练JSON` 采5个独立留出点：一个矩形四角和中心。新采集记录包含服务 action_id。

`POST /visual-calibrations/review` 接收 request_id、reviewer、scene、training[6]、holdout[5]；每个观测为 `{action_id, center:[x,y], uncertainty_px, image_sha256}`，留出另含 `target:[x,y]`。不接收客户端自报映射、图片路径或verified状态。服务在串口工作线程排他核对本实例已完成的单步无点击诊断动作、连接代次、服务校准代次、动作后截图、图片哈希与15分钟有效期；拟合、重复点、留出及观测不确定度都必须满足8px容差。

通过 `GET /visual-calibrations/{request_id}` 读取审核状态。成功后发布 `evidence_type=visual_pointer`、`click_delivery_verified=false` 的坐标校准profile；可点击范围仅为通过留出验证且位于训练覆盖内的矩形。该verified仅代表坐标审核通过，按钮生效仍须实机验证。正式原生移动与点击随后使用相同profile转换；不需要专用页面或另占串口。重启服务、BLE连接变化或截图几何失效后仍需复验。HTTP重复审核ID只返回已有结果，不重复发布；不同内容复用ID拒绝。

测试与手机操作仍由6.1 Terra Medium执行，主代理开发和验收。不可将旧服务实例的样本直接提交给重启后的服务；正式代码不依赖task/lab。

实机整合验收（2026-11-02）：主代理恢复新版8892，服务PID51136；同一Terra Medium使用正式采集器在主屏完成6训练、5独立留出。纯native连续采样本轮得到正向identity，审核profile `246d00b6da8a4c21a33e577a844a813a`，范围 `[200,450,300,650]`。主代理独立审核原图及5px观测不确定度，服务审核通过。正式move_to(205,625)与click_at共用同一profile/HID坐标；两次单独原生点击均实际打开闲鱼，最终返回主屏。71项回归通过，最新review终态修复后visual两项重复两轮、HTTP两项通过。证据 `.runtime/signed-mapping-integration/live/main-evaluation.md`。

此前原型的反向关系不能作为固定方向；本轮早期插入relative nudge后的观测不一致，而纯native连续采样稳定。混合通道影响仅为待验证原因，当前已验收流程保持native，不插入relative刷新。主屏静态时可等时钟自然更新取得新鲜帧；不要用旧帧假装新帧。当前仅为局部当前连接通过，当时负斜率代码仅由回归验证，实机点击使用正向映射；现已撤回负斜率支持。

方向及时序限制补充（2026-11-02）：后续36次A/B/A受控复验最终全部正向，相对插入未复现方向切换。已实测两次frame_time晚于HID完成但画面仍显示之前位置，约150ms后才出现正确目标。现after_action/after_settle是PC接收发布时序，不保证手机画面因果归属；该发现是下述连续响应检查的设计依据。不得以负斜率拟合掩盖未经解释的方向变化。详见[诊断报告](../../task/relative-click/doc/direction-investigation.md)。


## 连续画面响应与点击门禁（2026-11-02）

`pointer_guard.py` 对正式 native `move_to`、`click_at` 和无点击诊断移动统一执行三个不同位置的无按钮挑战：锚点、探测点、目标。以相邻画面的两个紧凑指针差分区域验证两段按顺序的运动，避免将旧指针消失误作目标指针。每段要求至少三个不同 frame_id、持续至少120ms、中心抖动不超过2px；实际位置与预期相差超过8px、明显场景变化、截图源改变、缺帧或超时均不能通过。每段响应最多等待2.5秒；不会自动反向、重新拟合或重复点击。

这是一项保守的静态界面视觉检查，不是iOS触点回读；动画、图标吸附、裁切和指针形态变化可能导致拒绝。它会增加动作耗时。顶部70px忽略状态栏变化，但保留本段被测指针周围的30px邻域，允许上边缘接受与其他点相同的证据检查。需要先在主屏验收，再扩大适用界面，不能称为任意页面通用。

`click_at` 只有在目标连续响应检查通过、连接身份再次核对一致后才提交原生点击；所有尝试保存 `.runtime/service/pointer-checks/<id>/` 原始连续帧及result.json。失败会将内存profile标为needs_verification并增加代次，随后点击被阻止。服务重启同样不会自动信任旧profile。返回trace含pointer_check证据目录；审核校准只接受已通过此检查的本实例诊断动作。旧相对通道不在此门禁的覆盖范围内。

时间戳仍是PC接收时间，不宣称已取得手机源PTS。三点画面挑战提供可观测响应证据，不把接收时间直接等同于动作因果。已有正向校准与点击成功记录是历史结果；新版实机验收另行记录。


纯 `move_to` 的截图现在直接返回最后通过连续检查的稳定证据帧，标记 `pointer_response_verified=true`、`after_action=false`；不会为了等待静止页面的新帧重复移动，也不伪造settled_at。该帧早于后续release完成，但属于已确认的移动响应。`click_at` 仍单独抓取点击后画面，移动检查通过不等同按钮生效。

当前诊断检查按原始正向坐标允许8px误差，属于保守验证门禁；未解决“未知较大正向偏移/缩放如何无先验校准”，不能宣称支持任意初始映射。已有正向profile使用共用逆变换后验证屏幕目标。

新版实机结果、时间比较和已知边界见[连续指针响应验收](pointer-response-validation.md)。


## 固定映射范围复验

`POST /scope-validations` 使用现有正向profile，不拟合或改变axes/mapping。可对重启后的needs_verification记录做固定映射复验；需身份、几何、截图源和连续指针证据全部通过。当前使用下述usable-area-v1模板，验证明确的可操作区域。历史40点全像素模板在(0,0)失败，证据保留于验收记录，不能宣称极限角落已支持。

每点走与move_to/click_at相同的pointer_guard并保存稳定证据帧，单帧时间戳不再代替连续响应。任一失败立即结束，不跳过、不自动重试、也不收缩或重新拟合来宣称通过。全部样本经独立审核后才能扩大verified_region；审核保留既有axes，且不把坐标成功当作按钮送达成功。范围验证失败应如实记录；区域外目标不再仅因覆盖范围不足而禁止点击，实际定位仍须通过即时响应检查。


指针响应检查v2：先对差分掩码做3×3开运算，切断细长连通尾部后提取紧凑候选；保留原始差分掩码计算outside变化，移除的像素仍消耗原有80px预算。候选尺寸48px、位置8px、稳定2px/3帧/120ms门槛均未放宽。版本号记录在health和单动作证据中。此修复针对已保存失败帧验证，不代表全屏复验已通过。


当前scope模板改为显式可操作区域复验 `usable-area-v1`：`[24,60,471,1010]`，5个内部点加9宫格三次随机顺序重复，共32点。此范围变更在新一轮开始前明确选择，不是某轮失败后自动缩域继续，也不宣称覆盖极限角落。scope结果含template_version/requested_region，成功profile含scope_template；保持原axes/mapping和既有误差/稳定性门槛。先前40点全像素模板的失败证据保留，不能改写为通过。


pointer guard v3区分“指针响应异常”和“远处局部场景变化”。仅无点击scope采样可以把远离两个指针至少50px、最多两个48×48以内且合计不超过1600px的变化区域，记录为scene_changes等待独立人工审图；未归类噪声仍须≤80px，位置/稳定门槛不变，大场景变化仍拒绝。审核这类样本必须显式提供scene_reviewed=true，否则不发布profile。普通move_to/click_at不开放此选项，场景变化仍阻止点击；API客户端不能自行选择宽松模式。此项为避免主屏通知角标刷新被误报为坐标异常，不自动声称变化无关，也不忽略原始证据。


## 全屏九点校准（2026-11-02）

视觉校准 v3 支持九点3×3网格与五个独立留出点，继续支持历史六点格式。当前训练点为X=24/248/471、Y=60/535/1010的九种组合；独立留出点为(40,80)、(456,80)、(40,980)、(456,980)、(248,530)。各点仍需要当前服务实例、连接身份、连续指针响应、原图哈希及独立看图审核。五点均不复用训练命令。全屏分布采样不声称极限圆角可达。

verified_region记录独立验证矩形，已经取消用该矩形拒绝区域外点击。映射用于全屏，屏幕外/换算越界和实时响应异常仍拒绝。上线及实机结果单独记录，不以离线测试代替手机验证。

指针响应检查v5在当前画面内部网格中选择低纹理起点，并与探测点和目标保持至少80px距离，减少起始指针被图标遮挡导致的识别失败。该选择不证明指针已到位；后续双足迹、8px误差、3帧/120ms和场景变化检查保持不变。采集器在静态画面时最多只读等待65秒，获得3秒内新帧后再提交动作。

指针响应检查v6区分坐标异常与远离指针的局部场景动画：若双足迹已连续稳定，只有符合既有局部变化分类的远处像素变化导致超时，当次点击仍未提交，但保留校准。方向/位置未确认、源改变、几何变化或其他异常继续使校准失效。该修复针对微信顶部收取中spinner导致整个profile失效的问题；调用方须观察页面稳定后再决定新的动作，不自动重放。

### 人工看图移动

用户要求点击不受图标遮挡、光标闪烁等自动识别误判阻塞。`service_client.act`新增`manual_visual=True`，必须同时指定`coordinate_backend="native_hid"`、`diagnostic_uncalibrated=True`、`capture=True`且只有一个`move_to`或带复核依据的`click_at`步骤。该模式使用正向identity诊断移动，返回动作后截图和`pointer_check.state=manual_review_required`，不声称自动验证或校准通过。设备身份、连接、几何和屏幕边界仍检查。调用者必须查看新图、确认实际位置后，另发同坐标的manual_visual click_at，并提供reviewed_action_id指向已查看的移动请求。服务校验该移动同实例、同连接、同图源、目标一致、已释放、截图不超过120秒，再发送abs.click。无复核ID、关闭截图或混合步骤拒绝。

自动校准和默认move_to/click_at继续使用原有guard。人工移动证据不可用于正式视觉校准审批，因为它没有passed连续响应证据。移动请求不自动点击；独立复核点击不自动重试，也不静默把旧profile改成verified。人工点击必须走原生abs.click，不混用相对mouse.click。
