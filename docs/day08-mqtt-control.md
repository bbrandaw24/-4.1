# Day 8：APP 灌溉控制与 MQTT

## 交付内容

- 水泵控制接口使用 `farm/{device_id}/control/pump`，只接受 `start` 和 `stop`。
- 每条指令生成 `command_id`，模拟器在 `farm/{device_id}/status/pump` 回传时保留该 ID。
- API 订阅传感器和水泵状态主题，缓存当前状态、确认时间和响应耗时。
- `GET /api/v1/devices/{device_id}/pump` 返回 `pending`、`confirmed` 或 `timeout` 状态；默认确认窗口为 5 秒。
- `GET /api/v1/devices/{device_id}/alerts` 根据低湿度（<40%）和高温（>30°C）生成告警。
- Web/ArkTS 映射基线增加手动/自动模式、低湿度阈值、定时灌溉设置、告警区和浏览器通知/振动入口。

## 控制闭环

```text
页面 POST pump -> API 生成 command_id -> MQTT control/pump
    -> 模拟器执行 -> MQTT status/pump -> API 确认
    -> 页面 GET pump 显示确认状态和响应耗时
```

页面的自动模式和定时字段为 APP 规则配置基线；自动执行规则在第 10 天集成测试阶段接入，当前不会绕过确认机制直接启动水泵。

## 验收命令

```bash
curl -s http://127.0.0.1:8000/api/v1/devices/sim-greenhouse-001/pump
curl -s -X POST http://127.0.0.1:8000/api/v1/devices/sim-greenhouse-001/pump \
  -H 'Content-Type: application/json' -d '{"action":"start"}'
curl -s http://127.0.0.1:8000/api/v1/devices/sim-greenhouse-001/pump
curl -s http://127.0.0.1:8000/api/v1/devices/sim-greenhouse-001/alerts
```

验收重点：POST 返回 `202` 和 `command_id`，随后 GET 的 `command.status` 变为 `confirmed`，并且 `latency_ms` 小于 500 ms；无设备回执时在 5 秒后变为 `timeout`。

## 鸿蒙说明

当前仓库提供可运行的 Web/ArkTS 数据和交互映射基线。真正的 MQTT.js 鸿蒙运行时、系统通知和振动权限需要在具备 DevEco Studio SDK 和真机的环境中完成最终签名验收，本阶段不虚构 APK 结果。
