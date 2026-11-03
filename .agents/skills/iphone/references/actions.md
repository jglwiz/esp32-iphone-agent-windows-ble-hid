# 动作与客户端参考

本文件随仓库版本维护。精确定义以部署 checkout 的 `backend/src/iphone_backend/orchestration.py` 中 `Action`、`Step` 和执行逻辑为准；全局复制的 skill 若与后端版本不同，先核对定义。

本参考对应固件 0.4.2、guard v9 的仅原生绝对定位 API。固件已删除 mouse.move，保留原地按钮和滚轮；Report 2 的 X/Y 恒零。三次复位重连及重复定位已实测；屏幕四角存在实际内缩，物理旋转未测。

## 请求结构

业务入口为 `POST /action`。下例仅展示格式，不授权执行输入：

```json
{
  "request_id": "unique-request-id",
  "coordinate_backend": "native_hid",
  "steps": [{"op": "move_to", "x": 250, "y": 540}],
  "capture": true,
  "settle_ms": 700,
  "timeout_seconds": 20
}
```

| 字段 | 约束及默认值 |
| --- | --- |
| `request_id` | 必填，1–80 字符；发送前持久化，每个新动作使用新 ID |
| `steps` | 默认空列表，最多 32 步；空列表不注入输入，但 `/frame` 更适合单纯观察 |
| `capture` | 默认 true |
| `settle_ms` | 0–2000，默认 700 |
| `timeout_seconds` | 1–45，默认 20 |
| `coordinate_backend` | 默认且唯一模式 `native_hid`；不接受 `visual_relative` |
| `diagnostic_uncalibrated` | 默认 false，诊断标志不是定位成功的证明 |
| `manual_visual` | 默认 false；约束见下文 |
| `reviewed_action_id` | 人工审查点击所引用的移动动作 ID |

同一个 request_id 的相同请求由服务历史去重；不同请求体复用 ID 会冲突。未知结果不通过换 ID 再发送来恢复。取消入口为 `POST /cancel/{request_id}`，返回取消请求已提交不代表此前没有输入。

## 步骤

| `op` | 参数 | 行为 |
| --- | --- | --- |
| `click` | `button` | 当前指针处点击，不定位 |
| `move_to` | `x`, `y` | 原生绝对定位到截图像素，检查实际指针响应 |
| `click_at` | `x`, `y` | 经定位验证后点击，仅主按钮 |
| `scroll` | `wheel` | 滚轮，方向需观察手机确认 |
| `text` | `text` | 可打印 US ASCII，不含换行 |
| `key` / `key_down` | `usage`, `modifiers` | HID 键盘 usage，按一下或按住 |
| `key_up` | 无 | 释放键盘 |
| `mouse_down` / `mouse_up` | `button` | 按住或释放按钮 |
| `wait` | `seconds` | 等待 |

- `move`、`tap`、`drag` 和 `dx/dy` 已删除；整份请求在输入前拒绝，没有相对移动、拖拽或兜底接口。
- `x`、`y`：整数截图像素，当前 `x=0–495`、`y=0–1079`，绝对操作必填；不能传归一化坐标。
- `button`：1–3，默认 1；额外按钮在 iOS 的实际映射需单独验证。
- `wheel`：整数 −127–127，默认 0。
- `usage`：整数 0–101，默认 4；`key`、`key_down` 禁止保留错误码 1、2、3。Enter 的 usage 为 40，只有对应提交已授权才发送。
- `modifiers`：整数 0–255，默认 0。
- `seconds`：0–1.5，默认 0。
- 每个 action 的所有 `text` 总长度最多 256 字符，字符范围 U+0020–U+007E。
- 绝对操作不能与 `mouse_down` 或 `key_down` 混在同一个 action 中。不要假定 held 输入可跨 action 保持，服务有释放清理。

## 复用现有客户端

从任意工作目录调用仓库客户端，无需新建 skill 专用客户端。下例是只读健康查询，`$repo` 与 `$baseUrl` 先按 SKILL.md 的配置优先级解析；Python 使用仓库虚拟环境，未部署时可使用已有 Python 3.11+：

```powershell
$python = Join-Path $repo 'backend/.venv/Scripts/python.exe'
$client = Join-Path $repo 'backend/src/iphone_backend/service_client.py'
& $python $client --operation health --base-url $baseUrl
```

动作可使用该脚本的 `--steps`、`--output`、`--request-id`、`--base-url` 参数。使用 `--help` 查看 CLI 支持项；CLI 没有开放全部 Action 字段，高级操作通过现有 Python 函数 `act` 的关键字参数实现，或直接构造 HTTP 请求。

Python 调用可使用已安装包；尚未安装包但需要独立调用客户端时，可仅将已确认的 `<repo>/backend/src` 加入该 Python 进程的 `sys.path`，再导入 `iphone_backend.service_client`。不要全局更改 PYTHONPATH。客户端模块自身仅使用标准库。

`act(steps, output, ...)` 默认生成 request_id，并在发送前保存 `<output stem>.request.json`。成功响应后保存 `.result.json`，有 frame 时将 base64 解码到指定图片路径，并保存帧元数据 `.json`。传输失败时保留请求文件用于调查，不自动重试。为每次动作选择独立文件名，避免覆盖旧证据；读取结果时同时检查 `input_state`、`error`、`capture` 和实际截图。

观察帧使用 GET `/frame` 返回的原始 JPEG，不作为 JSON 解码；同时记录响应时间头和状态码。可用 GET `/screen/status` 辅助判断新鲜度，但最终证据应绑定实际保存的帧。

## 人工审查的原生定位

仅在任务需要且符合项目流程时使用：

1. 单步 `move_to`，设置 `coordinate_backend='native_hid'`、`diagnostic_uncalibrated=True`、`manual_visual=True`、`capture=True`。
2. 实际查看返回截图，确认指针位于期望目标。`manual_review_required` 不表示已到位。
3. 需要点击且已获授权时，以同坐标单步 `click_at`，使用相同模式并设置 `reviewed_action_id` 为前一步 ID。

服务会核对同实例、连接绑定、截图来源、目标与时效等条件。审查过期或连接改变时重新观察未执行的动作，不绕过检查。普通定位模式使用其校准与指针验证流程，不默认开启诊断标志。

## 校准与已知陷阱

自动事件校准、视觉校准采样和独立留出点都使用原生绝对报告，不夹杂相对移动。校准文件为 `<IPHONE_RUNTIME_DIR>/service/calibration-profile.json`；检查运行态 profile 与当前设备描述符是否匹配，不能手改版本或 verified 字段冒充验收。

曾出现相对移动后重发相同绝对目标仍不归位；改用两个不同绝对点逐步审图恢复，但底层原因尚未确定。若需无点击诊断，可先移至另一屏内绝对点审图，再移至目标审图，不能以相对 nudge 唤醒。

命令坐标、报告已发送和 PC 新帧时间均不是到位证明。校准只能修正稳定偏差，不能修复偶发不响应、方向反转或点击不生效。详见仓库 `backend/doc/direction-root-cause.md`；单一控件不响应时，可在授权范围内观察或使用其他可逆入口，不重复提交未知的发送等动作。

## 静止截图与重连

`capture.state=observation_only` 表示得到输入结束后、settle 之前的有效图，但无法确认后续画面。返回原始 `frame_time`、实际 `age_seconds`、`after_action=false`、`effect_verified=false`；不带 capture_error 不代表效果已确认。超过 5 秒、输入前的帧、取消和不可用源不走此返回。普通 guard 移动的 `pointer_response_verified=true` 是另一种明确的位置证据。`reports_sent`、`available` 和 `after_action` 本身都不证明业务成功。

重连后 profile 失效；用正式范围复验恢复，不能手改验证状态。无按钮 guard 可最多更换一次绝对 anchor 来恢复首报告未响应，失败继续拦截点击。scope 模板为 usable-area-and-edge-midpoints-v2，36 点含三轮屏内网格及四边中点，矩形范围 [24,60,471,1010]，不代表四角可达。
