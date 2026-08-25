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

## 当前部署边界

- 本地 VMware 虚拟机 `bearipi`（NAT 地址 `192.168.128.130`）用于 Linux、Docker、模拟器和端到端联调；Windows 端通过 MobaXterm SSH 进入虚拟机，并负责浏览器、截图和 GitHub 操作。
- 腾讯云 Ubuntu 24.04 运行 Compose 项目 `smartagri-cloud`，公网入口为 `http://43.156.230.129:8080/`。公网只开放 Web 网关，API、AI、Mosquitto 和 MySQL 通过内部网络或本机绑定访问。
- Web 页面调用 API，不直接访问 MySQL 或 MQTT。水泵控制通过 `POST /api/v1/devices/{device_id}/pump` 生成命令并等待 `status/pump` 确认。
- 当前设备数据由模拟器提供；模拟器通过不代表真实 BearPi-HM Nano 已经烧录或通过硬件验收。
- AI 服务当前提供健康检查和接口边界，真实训练权重与生产推理仍待补齐；原生鸿蒙 ArkTS/HAP 工程也尚未纳入当前运行栈。
