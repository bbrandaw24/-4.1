# Day 2 - 设备模拟器与 MQTT 协议

## 运行方式

在 Docker 环境中执行：

```bash
docker compose up -d --build mosquitto simulator
docker compose logs -f simulator
```

如果虚拟机已有 EMQX 或其他 MQTT 服务占用宿主机 `1883`，可在 `.env` 中将 `MQTT_PORT_HOST` 改为未占用端口（例如 `1884`）。Compose 网络内的模拟器仍使用 `mosquitto:1883`，不需要修改 `MQTT_PORT`。

模拟器默认设备 ID 为 `sim-greenhouse-001`，每 5 秒发布一次土壤和气候数据。可通过 `DEVICE_ID`、`PUBLISH_INTERVAL_SECONDS` 调整。

## 消息格式

所有消息使用 UTF-8 JSON 信封：

```json
{
  "device_id": "sim-greenhouse-001",
  "timestamp": "2026-08-23T00:00:00+00:00",
  "payload": {}
}
```

### 上行主题

| 主题 | QoS | payload 字段 |
| --- | --- | --- |
| `farm/{device_id}/sensor/soil` | 1 | `moisture_pct`, `temperature_c`, `ph`, `nitrogen_mg_kg`, `phosphorus_mg_kg`, `potassium_mg_kg`, `conductivity_ms_cm`, `salinity_g_l` |
| `farm/{device_id}/sensor/climate` | 1 | `light_lux`, `air_temperature_c`, `air_humidity_pct` |
| `farm/{device_id}/status/pump` | 1 | `action`, `running` |

### 下行主题

向 `farm/{device_id}/control/pump` 发布以下 JSON 即可模拟控制水泵：

```json
{"device_id":"sim-greenhouse-001","payload":{"action":"start"}}
```

支持的 `action` 为 `start` 和 `stop`。模拟器会在 `status/pump` 回传状态。

## 验收命令

```bash
mosquitto_sub -h 127.0.0.1 -t 'farm/+/sensor/#' -v
mosquitto_pub -h 127.0.0.1 -t farm/sim-greenhouse-001/control/pump -m '{"payload":{"action":"start"}}' -q 1
```

第 2 天只验证协议和模拟设备；消息持久化与 API 路由安排在第 3 天。
