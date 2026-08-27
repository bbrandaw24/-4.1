# 智慧农业大棚监控系统

本仓库是《中国地质大学 12 天实训计划》的智慧农业项目实现，主线为“设备模拟器 -> MQTT -> Flask API -> 数据库 -> Web 看板 -> 智能体问答 -> 云端部署”。过时的“堵桥”任务不属于本项目。

## 项目当前状态

当前已形成可运行、可验证的端云一体化演示系统：腾讯云部署 Web 看板 + 多地块模拟器 + 云端知识库智能体，GitHub 用于源码、文档和非敏感证据管理。

- 已完成：多地块（苹果/梨/橘）模拟器、MQTT 消息链路、Flask API、图片上传与缩略图、Web 看板（概览/趋势/设备/智能体问答 4 个板块）、地块切换与多地块总览、10 小时趋势（SQLite 持久化）、水泵控制闭环、告警记录（SQLite 持久化）、自动灌溉规则、云端知识库 + Luna 双模式智能体问答、**传感器注册表（每块地 5 类虚拟硬件，可连接/断开/删除/创建，MQTT 实时上报）**、腾讯云部署。
- 已验证：云端 MQTT 发布/订阅、三地块遥测、水泵控制确认、告警日志接口、知识库问答与 Luna 大模型回答、角色权限（游客仅知识库、农户/管理者可用 Luna）、**15 个传感器（3 地块 × 5 类）实时上报与 CRUD**、Web 访问与主要 API。
- 待继续：HTTPS/MQTT TLS/认证、备份监控、原生鸿蒙 APK/HAP、真实 BearPi-HM Nano 硬件接入。

完整阶段交付说明见 [`docs/project-completion.md`](docs/project-completion.md)，按天记录见 [`docs/plan-12-day.md`](docs/plan-12-day.md) 和 [`docs/task-log.md`](docs/task-log.md)。远程部署和 Web 运行说明见 [`docs/cloud-deployment.md`](docs/cloud-deployment.md)、[`docs/web-dashboard.md`](docs/web-dashboard.md) 和 [`docs/runtime-and-apk.md`](docs/runtime-and-apk.md)。

## 系统组成

```text
多地块模拟器（苹果园/梨园/橘园）
        | MQTT sensor/control/status
        v
Mosquitto -> Flask API -> SQLite/MySQL
                |              |
                |              v
                |       知识库 + Luna 模型
                v
            Web 看板（概览/趋势/设备/智能体问答）
```

本地虚拟机是 Linux/Docker/MQTT/API 的开发环境，不是 BearPi 硬件本身，也不是公网服务器。Windows 端主要用于 MobaXterm SSH、浏览器、截图和 GitHub 操作。

## 地址与环境

| 环境 | 地址/入口 | 用途 |
| --- | --- | --- |
| 本地虚拟机 | `192.168.128.130` | Docker、模拟器、联调 |
| 腾讯云公网网页 | [http://43.156.230.129:8080/login.html](http://43.156.230.129:8080/login.html) | 对外演示（登录入口） |
| 腾讯云 API | `http://43.156.230.129:8010/api/v1/` | 遥测/告警/智能体接口 |
| GitHub 仓库 | [bbrandaw24/-4.1](https://github.com/bbrandaw24/-4.1) | 源码与文档 |

公网入口目前是 HTTP IP 地址，不代表已经配置 HTTPS 域名。GitHub Pages 只适合静态页面，动态 API、MQTT、SQLite 和模拟器仍运行在云服务器 Docker 中。

## 功能特性

| 板块 | 功能 |
| --- | --- |
| 概览 | 多地块总览（3 卡：名称/作物/湿度/温度/在线态）、「当前地块」下拉切换（全局生效）、关键指标、灌溉控制（水泵启停 + 自动规则）、现场图像上传、AI 识别状态 |
| 趋势 | 最近 10 小时土壤湿度/温度/光照曲线（SQLite 持久化，重启不丢） |
| 设备 | **传感器管理（Day 16）**：每块地 5 类虚拟硬件（土壤温度/pH/氮磷钾/空气湿度/电导率），实时值 + 状态徽标，可连接/断开/删除/添加；断开即停止推送，添加即在云端创建并开始上报；仅农户/管理者可操作 |
| 告警记录 | 系统告警历史（低湿/高湿/高温，SQLite 持久化，触发/恢复去重），30s 自动刷新 |
| 智能体问答 | 双模式：知识库问答（云端 53 篇灌溉/农事文档 + 实时遥测合成，全员可用）/ Luna 模式（接入 Luna 模型，农户/管理者可用）；可开关思考模式、选思考强度（中/低）、折叠展示思维链；Luna 小猫娘动态形象随状态切换 |

登录账号（本地演示种子）：`admin/admin123`（管理者）、`farmer/farmer123`（农户）、`guest`（游客一键进入，仅知识库问答）。

## 快速启动

1. 复制 `.env.example` 为 `.env`，填写密码等配置；**不要提交 `.env`**。
2. 执行 `docker compose config` 检查 Compose 配置。
3. 执行 `docker compose -p smartagri up -d --build` 启动服务；如已有同端口项目，请使用项目专用名称并先确认端口占用。
4. 可选环境变量：
   - `SIM_DEVICES`：覆盖模拟地块列表（默认苹果园/梨园/橘园，JSON 数组，含 id/label/crop/moisture/temp 基线）
   - `LUNA_API_KEY` / `LUNA_BASE_URL` / `LUNA_MODEL`：启用智能体 Luna 模式（OpenAI 兼容；思考强度由前端选择，固定不开放 high/xhigh）
5. 检查 API `/healthz`，再检查设备列表、最新遥测和水泵控制。

也可以只启动静态页面进行接口联调：`python3 -m http.server 8080 --bind 0.0.0.0 --directory web`。主要协议与验收命令见 [`docs/day02-mqtt.md`](docs/day02-mqtt.md)、[`docs/day03-api.md`](docs/day03-api.md)、[`docs/day04-images.md`](docs/day04-images.md) 和 [`docs/day08-mqtt-control.md`](docs/day08-mqtt-control.md)。非敏感截图和原始文本见 [`evidence/README.md`](evidence/README.md)。

## 全局 MQTT Broker（Day 16 传感器注册表）

系统使用**单一全局 MQTT Broker**（Eclipse Mosquitto），所有地块、所有传感器的遥测统一经它中转，**不做按传感器拆 broker**。Broker 部署形态与配置如下。

### 部署形态与地址

| 项 | 值 | 说明 |
| --- | --- | --- |
| Broker 软件 | `eclipse-mosquitto:2` | Docker Compose 服务名 `mosquitto` |
| 容器内地址 | `mosquitto:1883` | Compose 服务发现，API/模拟器经此访问 |
| 宿主机绑定 | `127.0.0.1:1883`（仅内网） | **不直接公网暴露**，公网只能通过 Web 网关 |
| 公网 MQTT 端口 | 不开放 | MQTT over TLS 与公网接入列为后续待办 |
| 认证 | 匿名（本地/演示阶段） | 生产上线前应启用账号认证 + TLS |
| 配置挂载 | `infra/mosquitto/mosquitto.conf` | 只读挂载进容器 |

Broker 地址由环境变量注入：`MQTT_HOST`（默认 `mosquitto`）、`MQTT_PORT`（默认 `1883`）。API 与模拟器容器都读取这两个变量；如需切换 Broker，修改 `.env` 后 `docker compose -p <项目名> up -d --force-recreate api simulator` 即可。

### Topic 规范（传感器注册表，Day 16 起）

**`farm/{device_id}/{sensor_id}/telemetry`** —— 每类传感器一个独立 topic，`sensor_id` 为云端 `sensors` 表中的 UUID：

```json
{
  "sensor_id": "7b9b6028217f477da63568be1aa61616",
  "device_id": "sim-plot-apple",
  "type": "soil_temperature",
  "value": {"temperature_c": 23.05},
  "unit": "°C",
  "timestamp": "2026-08-27T02:51:46.166525+00:00"
}
```

- `value` 是**对象**（不是标量），不同传感器类型字段不同（见下表）
- 订阅端可一条通配符订阅全部：`farm/+/+/telemetry`
- API 收到后校验 `sensor_id` 存在且 `status=connected`，否则丢弃；同时写 SQLite（`sensors` 表更新最新值 + `telemetry_history` 留痕）

### 5 类传感器与发布周期

| type | 名称 | value 字段 | 单位 | 默认发布周期 |
| --- | --- | --- | --- | --- |
| `soil_temperature` | 土壤温度 | `temperature_c` | °C | 30 s |
| `soil_ph` | pH | `ph` | — | 30 s |
| `soil_npk` | 氮/磷/钾 | `nitrogen_mg_kg` / `phosphorus_mg_kg` / `potassium_mg_kg` | mg/kg | 60 s |
| `air_humidity` | 空气湿度 | `air_humidity_pct` | % | 15 s |
| `soil_conductivity` | 电导率 | `conductivity_ms_cm` | mS/cm | 30 s |

每个地块（`sim-plot-apple` / `sim-plot-pear` / `sim-plot-orange`）默认自动 seed 全部 5 类传感器，共 **15 个传感器**。断开（`status=disconnected`）后模拟器停止发布该 topic；连接后恢复。删除后重新创建即重新开始上报。

### 传感器管理 API

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| GET | `/api/v1/devices` | 每个设备附带 `sensors` 数组（类型/状态/最新值/最近上报时间） |
| GET | `/api/v1/devices/{id}/sensors` | 列某地块传感器 |
| POST | `/api/v1/devices/{id}/sensors` | 创建传感器（`{"type":"soil_ph"}`），201；重复返回 409 |
| PATCH | `/api/v1/sensors/{id}` | 切换 `{"status":"connected"\|"disconnected"}` |
| DELETE | `/api/v1/sensors/{id}` | 删除传感器 |

权限：游客只读（操作返回 403），农户/管理者拥有 `manage_sensors`。

### 旧 Topic（兼容保留）

| 主题 | 方向 | 说明 |
| --- | --- | --- |
| `farm/{device_id}/sensor/soil` | 设备 -> 服务端 | 土壤湿度、温度、pH、氮磷钾、电导率、盐度（自动灌溉规则依赖 `moisture_pct`） |
| `farm/{device_id}/sensor/climate` | 设备 -> 服务端 | 光照、空气温度、空气湿度 |
| `farm/{device_id}/control/pump` | 服务端 -> 设备 | 水泵 `start`/`stop` |
| `farm/{device_id}/status/pump` | 设备 -> 服务端 | 水泵执行确认 |

旧 `sensor/soil`、`sensor/climate` 与传感器注册表**双轨并存**：模拟器同时发布两套 payload（可用 `SIM_KEEP_LEGACY_PAYLOADS=false` 关闭旧格式），保证自动灌溉与趋势图不中断。

### 订阅调试示例

```bash
# 订阅全部传感器遥测（宿主机本地，或任一能访问 1883 的内网主机）
mosquitto_sub -h 127.0.0.1 -p 1883 -t 'farm/+/+/telemetry' -v

# 只看苹果园的土壤温度
mosquitto_sub -h 127.0.0.1 -p 1883 -t 'farm/sim-plot-apple/+/telemetry' -v

# 观察水泵控制回执
mosquitto_sub -h 127.0.0.1 -p 1883 -t 'farm/+/status/pump' -v
```

默认模拟设备为三个地块：`sim-plot-apple`（苹果园，湿度基线 50）、`sim-plot-pear`（梨园，基线 60）、`sim-plot-orange`（橘园，基线 44），各地块温湿度画像不同。灌溉状态使用连续状态模型，启动后湿度逐步上升，停止后逐步回落。

## 持久化

- `/data/telemetry.db`（SQLite，挂载卷）：遥测历史（最近 10 小时窗口，12 小时保留）+ 告警日志（保留最近 500 条）+ **传感器注册表 `sensors` 表**（设备/类型/连接状态/最新值/最近上报时间，`UNIQUE(device_id, type)`）
- `/data/users.db`（SQLite）：用户与登录态
- MySQL：图片等业务数据（保留）

## 安全边界

- 不提交 Token、密码、私钥、`.env` 或含凭据的日志。
- `LUNA_API_KEY` 只写入云端 `.env`，通过 Compose 环境变量透传，不进入源码仓库。
- 本地开发阶段可使用匿名 MQTT；上线前应启用账号认证、TLS 和最小权限。
- MySQL、MQTT、API 不直接公网暴露，公网只开放 Web 网关端口。
- 智能体 Luna 模式仅对农户/管理者开放（游客仅知识库问答），后端 403 拦截 + 前端禁用双保险。
