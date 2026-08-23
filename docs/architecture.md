# Day 1 - 系统架构基线

```text
虚拟设备模拟器
  ├─ farm/{id}/sensor/soil ------┐
  ├─ farm/{id}/sensor/climate ---┼--> Mosquitto --> Flask API --> MySQL
  ├─ farm/{id}/camera/command ---┘                    │
  └─ farm/{id}/control/pump <--------------------------┘
                                                     │
                                  图片上传 --> AI 推理服务 --> 生长状态
                                                     │
                                  HarmonyOS APP <-----┘
```

## 边界

- API 负责鉴权、设备状态、业务路由和数据库访问。
- Mosquitto 负责消息转发；第 1 天仅用于本地开发，安全加固在第 11 天。
- AI 服务负责模型推理，不在第 1 天伪造识别结果。
- APP 只通过 API 和 MQTT 访问数据，不直接连接 MySQL。

## 主题约定

| Topic | 方向 | 用途 |
| --- | --- | --- |
| `farm/{device_id}/sensor/soil` | 设备 -> 云 | 土壤 8 项参数 |
| `farm/{device_id}/sensor/climate` | 设备 -> 云 | 光照、空气温湿度 |
| `farm/{device_id}/control/pump` | 云 -> 设备 | 水泵控制 |
| `farm/{device_id}/camera/command` | 云 -> 设备 | 摄像头拍照指令 |
