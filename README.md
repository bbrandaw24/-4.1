# 智慧农业大棚监控系统

本仓库是《中国地质大学 12 天实训计划》的智慧农业项目实现，主线为“设备模拟器 -> MQTT -> Flask API -> 数据库 -> Web 看板 -> 云端部署”。过时的“堵桥”任务不属于本项目。

## 项目当前状态

当前已经形成可运行、可验证的 Web 端云演示系统：本地 VMware Linux 虚拟机用于开发和联调，腾讯云用于公网部署，GitHub 用于源码、文档和非敏感证据管理。

- 已完成：本地 Docker 环境、设备模拟器、MQTT 消息链路、Flask API、图片上传与缩略图、Web 看板、10 小时趋势、水泵控制闭环、腾讯云部署。
- 已验证：本地虚拟机和云端 MQTT 发布/订阅、设备遥测、控制确认、数据库建表、网页访问和主要 API。
- 待继续：真实 AI 权重和推理、自动灌溉规则、HTTPS/MQTT TLS/认证、备份监控、原生鸿蒙 APK/HAP、真实 BearPi-HM Nano 硬件接入。

完整阶段交付说明见 [`docs/project-completion.md`](docs/project-completion.md)，按天记录见 [`docs/plan-12-day.md`](docs/plan-12-day.md) 和 [`docs/task-log.md`](docs/task-log.md)。远程部署和 Web 运行说明见 [`docs/cloud-deployment.md`](docs/cloud-deployment.md)、[`docs/web-dashboard.md`](docs/web-dashboard.md) 和 [`docs/runtime-and-apk.md`](docs/runtime-and-apk.md)。

## 系统组成

```text
设备模拟器或真实设备
        | MQTT sensor/control/status
        v
Mosquitto -> Flask API -> MySQL
                |             |
                v             v
            Web 看板      图片/AI 接口
```

本地虚拟机是 Linux/Docker/MQTT/API 的开发环境，不是 BearPi 硬件本身，也不是公网服务器。Windows 端主要用于 MobaXterm SSH、浏览器、截图和 GitHub 操作。

## 地址与环境

| 环境 | 地址/入口 | 用途 |
| --- | --- | --- |
| 本地虚拟机 | `192.168.128.130` | Docker、模拟器、联调 |
| 本地网页 | `http://192.168.128.129:8080/`（历史验证地址） | 局域网验证 |
| 腾讯云公网网页 | [http://43.156.230.129:8080/](http://43.156.230.129:8080/) | 对外演示 |
| GitHub 仓库 | [bbrandaw24/-4.1](https://github.com/bbrandaw24/-4.1) | 源码与文档 |

公网入口目前是 HTTP IP 地址，不代表已经配置 HTTPS 域名。GitHub Pages 只适合静态页面，动态 API、MQTT、MySQL 和模拟器仍运行在云服务器 Docker 中。

## 快速启动

1. 复制 `.env.example` 为 VM 内部的 `.env`，只在本地填写密码等配置，不要提交 `.env`。
2. 执行 `docker compose config` 检查 Compose 配置。
3. 执行 `docker compose -p smartagri up -d --build` 启动服务；如果已有同端口项目，请使用项目专用名称并先确认端口占用。
4. 检查 API 和 AI 的 `/healthz`，再检查设备列表、最新遥测和水泵控制。

也可以只启动静态页面进行接口联调：`python3 -m http.server 8080 --bind 0.0.0.0 --directory web`。主要协议与验收命令见 [`docs/day02-mqtt.md`](docs/day02-mqtt.md)、[`docs/day03-api.md`](docs/day03-api.md)、[`docs/day04-images.md`](docs/day04-images.md) 和 [`docs/day08-mqtt-control.md`](docs/day08-mqtt-control.md)。非敏感截图和原始文本见 [`evidence/README.md`](evidence/README.md)。

## MQTT 主题

| 主题 | 方向 | 说明 |
| --- | --- | --- |
| `farm/{device_id}/sensor/soil` | 设备 -> 服务端 | 土壤湿度、温度、pH、氮磷钾、电导率、盐度 |
| `farm/{device_id}/sensor/climate` | 设备 -> 服务端 | 光照、空气温度、空气湿度 |
| `farm/{device_id}/control/pump` | 服务端 -> 设备 | 水泵 `start`/`stop` |
| `farm/{device_id}/status/pump` | 设备 -> 服务端 | 水泵执行确认 |

默认模拟设备为 `sim-greenhouse-001`，约每 5 秒发布一次数据。灌溉状态使用连续状态模型，启动后湿度逐步上升，停止后逐步回落，温度和光照围绕基线小幅波动。

## 安全边界

- 不提交 Token、密码、私钥、`.env` 或含凭据的日志。
- 本地开发阶段可使用匿名 MQTT；上线前应启用账号认证、TLS 和最小权限。
- MySQL、MQTT、API、AI 不直接公网暴露，公网只开放 Web 网关端口。
- 当前 AI 501 返回值是未完成模型能力的明确占位，不伪装成已训练模型。
