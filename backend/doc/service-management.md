> 迁移说明：本文件保留原设计与历史证据；当前启动、路径及管理接口以 [Backend 使用说明](usage.md) 为准。

# 控制服务生命周期

2026-11-02：restart API 与管理进程已实现，37项离线测试通过。用户手动退出旧服务后启动新版管理器；随后在指针重叠修复中，真实restart接口和进程交接首次通过，见文末记录。

## 当前启动与更新

统一入口为安装后的 `python -m iphone_backend`，由主控统一启动或复用取帧、管理 API。完整配置和本机 start/restart-manager 工具见 [使用说明](usage.md)。本机已配置唯一的用户登录启动项 iPhone Backend.lnk。

主控重启会认证并接管已登记 API，不重新占串口；/admin/restart 仅替换 API，保持取帧和 UxPlay。设备故障不阻止 Backend readiness，画面和日志仍可查询。主控身份不符或缺少干净退出证据时停止自动交接。

## API

`GET /health` 增加pid和restart.enabled/pending。`POST /admin/restart` 仅允许回环客户端，要求请求头 `Authorization: Bearer <token>`，服务器不信任代理头。请求体：

```json
{"request_id":"restart-unique-id","expected_instance_id":"刚读取的health.instance_id"}
```

- 202是接受请求，不表示已完成重启；同实例同ID幂等，不同ID在pending期间409。
- 409表示实例改变或控制器忙。动作、未完成future、队列、校准/验证正在执行时拒绝；等待工作完成后再决定是否请求，不取消或重放动作。
- 新实例拒绝旧expected_instance_id，防止HTTP响应丢失造成重复重启。
- 401/403表示token错误或非本机来源；503表示未启用管理器，或正在停止接单。

重启调用仍须符合当前执行权限，不作为绕过工具拒绝的途径。

## 进程交接

重启接受和任务提交共用锁。接受后，新动作、校准、视觉复核、范围验证及审批不能再入队。先返回202，再请求Uvicorn优雅退出。等待HTTP请求完成后，由唯一串口线程确认release_all并关闭串口；仅清理成功、线程结束时返回专用退出码75。管理器等待旧子进程退出后才启动新子进程，不强杀，不按进程名查杀。

文件锁阻止同项目重复管理器。旧 API 必须留下持久清理成功证明并真正退出，主控才允许替换；已接管进程不能仅依赖 Popen.wait。身份核对包含 PID、创建时间、instance_id、配置路径和认证协议。输入释放或关闭失败时停止自动交接，不强杀或重放。

HTTP健康不等于BLE可用，仍需检查connected/absolute_ready。重启不保留动作去重缓存、不重放动作、不自动批准校准；profile加载为needs_verification。

## 验证状态

37项离线测试通过，包含9项重启/管理器测试及现有服务回归。测试使用FakeBridge、TestClient、模拟子进程，不监听8892或打开串口。日志位于`.runtime/restart-api-20261102/offline-tests.txt`。

真实空闲重启及进程交接已通过文末实测；故障退出等分支目前仅离线覆盖。原6.1 Terra Medium子代理随后完成正向6+5点验证与单次相机点击，主代理独立审图通过，详见[指针v4验收](pointer-response-validation.md)。异常仍停止，不反向拟合。

## 新版启动及首次实机复验

用户退出旧服务后，普通Start-Process启动管理器成功，PID47736；控制子进程PID52936，instance c91b5dea63324df2a8adda893e5562c0。health显示restart.enabled=true、pending=false、fault=null；S3 boot fc756ce710d2cd31、BLE generation38不变。本次没有调用restart API，不能称为API重启通过。

同一Terra Medium执行主屏无点击采样：前两点(150,300)、(350,300)通过，观测约(150.26,299.39)、(350.38,299.87)。第三点目标(350,700)的anchor(350,540)可见正向；probe(270,600)与相机图标重叠，差分候选中心(261.28,597.09)超过8px阈值，guard拒绝。主代理独立查看两张成功图及失败003/012原图，局部足迹不足以区分实际位置偏差、图标吸附或检测误差，不判为反向，也不算通过。

该轮训练2/6完成，holdout0、点击0；第三点最终目标未执行，没有重试或重新拟合。各动作耗时2.017/1.694/3.298秒。当时服务健康空闲、released=true、profile=needs_verification。证据`.runtime/restart-api-20261102/live/operator_summary.json`；失败连续帧`.runtime/service/pointer-checks/7c6177e285bf44b3b3ce4585d1b1f77a/`。此后已修复圆弧定位并完成新的局部点击验收，原失败记录不改写为通过。

## 首次真实restart API验收

指针guard v4代码通过离线回归后，主代理向已启用管理器的本地接口提交一次重启请求，返回202。日志确认PID52936执行Application shutdown complete并退出，随后才启动PID44500。新instance为60869aa0206e4b4da53ba9b65372f13c，health的guard_version=4、fault=null、pending=false；S3 boot和BLE generation38保持不变。未停止UxPlay或取帧。证据`.runtime/pointer-overlap-20261102/restart.json`及`.runtime/restart-api-20261102/manager.{out,err}.log`。

本次证明正常空闲重启与版本加载可行，不代表所有故障退出场景均已实机覆盖。管理token仅供本机授权调用，未写入证据或日志。
