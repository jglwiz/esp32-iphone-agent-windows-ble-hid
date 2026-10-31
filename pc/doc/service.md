# 常驻串口控制服务

本服务保留现有固件协议，在 PC 上复用单一串口会话，并将动作、界面等待、新鲜截图合并到一个 HTTP 请求。模型不再为每次操作启动串口客户端、握手、关闭端口，再单独请求截图。API 监听 `127.0.0.1:8892`；现有 AirPlay 取帧仍使用 8891，旧 USB 后端 8890 不参与。

## 运行

在项目根执行：

```powershell
python -m pip install -r pc/requirements-service.txt
python pc/service.py
```

使用一个服务进程、一个 worker，不开启 reload 或多 worker。服务占用串口期间，不运行 bridge.py 或其他直接访问串口的程序。退出服务释放输入并关闭连接；不关闭 AirPlay。串口故障会显示在 `/health`，不自动重放或重连执行未知动作，确认现场状态后显式重启服务。

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

步骤支持 move、click、tap（相对移动后点击）、drag（当前位置按住并相对拖动）、scroll、text、key、key_down/key_up、mouse_down/mouse_up、wait。dx/dy 为 HID 相对单位，不是截图像素；本轮未实现绝对坐标校准，不能声称 tap(x,y) 已可靠定位。按钮编号 1–3；按键 usage、modifiers 与现有串口客户端一致。

所有步骤先整体校验再执行。每次最多32步、256个可打印ASCII字符；text不含回车，发送消息必须显式提供 Enter 操作。临时按住只允许在同一请求内组合，整个请求结束或失败都释放输入，不支持跨HTTP请求维持按键。可把移动、按下、拖动、释放作为一次请求提交，但跨页面仍需先看结果。

`service_client.act(steps, output)` 调用服务并将返回的 JPEG 保存为 output，同时写入 `.json`（兼容 live_case.py）、`.request.json`、`.result.json`。客户端不访问串口，不自动重试。Agent 应在同一次工具调用里执行 act 并用 view_image 展示 output，避免操作与看图拆成两轮。

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

## 验证

`python -m unittest discover -s tests -v` 覆盖旧客户端及服务的取消释放、去重、整批校验、过期队列任务不执行、截图失败不重放。真实输入仍用微信用例由操作子代理执行、主代理看图验收，且记录实际模型和推理强度。性能比较区分服务请求耗时、操作起止帧和总交付时间，不以单轮差异宣称严格模型或API提速倍数。

实机发现：仅时间戳新鲜仍可能出现编辑状态尚未稳定。关键草稿编辑建议 settle_ms=1500，发送前再做一次不修改文字的观察（必要时明确微移刷新画面），两次均精确匹配再提交。禁止将旧草稿截图触发的修正盲目重复。首轮服务测试缺失首字母，作为失败记录保留。

## 实机验收（2026-10-31）

6.1 Terra / Medium 通过本服务完整执行微信用例，主代理独立验收通过。修正后成功轮次 20261031_092538_7cee 耗时119.46秒；此前直接串口轮次273.13秒，单轮观察减少约56.3%，串口会话40次降为1次，截图24张降为14张。起始状态与恢复步骤不同，不作严格因果对照。首轮服务测试少一个t判失败，未计入成功提速。

实际报告在 `tests/case1_wechat_send_hello/runs/20261031_092538_7cee/evaluation.md`（相对项目根）。13项单元测试通过，固件未修改。保持同一串口会话、合并动作与截图已经实测；坐标校准、自动重连和长期运行仍待完善。

去重返回原请求的原始截图与时间戳，不是重新观察；需要当前画面时用新的只观察请求。日志/截图写入失败会使服务进入fault并保留输入结果，不能据此重新执行原动作。
