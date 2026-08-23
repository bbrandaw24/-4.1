# Day 3 - Flask 服务端与设备管理

## API

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| `GET` | `/healthz` | 服务健康检查 |
| `GET` | `/api/v1/system/status` | API 与 MQTT 配置状态 |
| `GET` | `/api/v1/devices` | 已接入设备及最新遥测摘要 |
| `GET` | `/api/v1/devices/{device_id}/telemetry/latest` | 指定设备最新土壤/气候数据 |
| `POST` | `/api/v1/devices/{device_id}/pump` | 发布水泵 `start` 或 `stop` 指令 |

水泵请求体：

```json
{"action":"start"}
```

## 本地验收

API 监听器订阅 `farm/+/sensor/+`，收到模拟器消息后更新设备注册表。当前注册表为进程内状态，重启后清空；MySQL 持久化安排在后续数据库接入阶段。

```bash
curl http://127.0.0.1:8000/api/v1/devices
curl http://127.0.0.1:8000/api/v1/devices/sim-greenhouse-001/telemetry/latest
curl -X POST http://127.0.0.1:8000/api/v1/devices/sim-greenhouse-001/pump \
  -H 'Content-Type: application/json' -d '{"action":"start"}'
```

API 容器使用单 Gunicorn worker，避免每个 worker 各自建立一份 MQTT 监听器。

