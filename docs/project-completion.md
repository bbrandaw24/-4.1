# 智慧农业项目阶段交付说明

更新时间：2026-08-25

本文件把本地虚拟机、设备模拟器、MQTT、Flask API、MySQL、AI 服务、Web 看板、GitHub 和腾讯云部署等工作统一记录到项目仓库中。项目主线来自《中国地质大学 12 天实训计划》，不包含已废弃的“堵桥”任务。

## 1. 项目目标

构建一个可演示、可验证、可继续扩展的智慧农业大棚监控系统：设备产生土壤和气候数据，经 MQTT 上报到服务端，再由 API 提供设备查询、历史趋势和灌溉控制，最后由 Web 看板展示。图片上传和 AI 识别保留了明确的接口边界，后续可替换为真实模型和 BearPi-HM Nano 硬件。

## 2. 已完成工作总览

### 2.1 本地虚拟机环境

- VMware 虚拟机：`bearipi`
- 系统用户：`bearpi`
- 当前 NAT 地址：`192.168.128.130`
- 主机名：`bearpi-virtual-machine`
- Docker：`24.0.2`
- Docker Compose：`v2.18.1`
- MobaXterm 通过 SSH 连接虚拟机，作为本地开发和验证入口。
- 虚拟机内已有 EMQX、MySQL、API、AI、模拟器和 Web 服务容器。

虚拟机的作用是提供稳定的 Linux、Docker、MQTT 和 API 运行环境；Windows 主要用于编辑代码、查看网页、保存截图和管理 GitHub。模拟器通过虚拟机产生数据，不等价于真实 BearPi 硬件已经烧录。

### 2.2 设备模拟器与 MQTT

模拟器默认设备 ID 为 `sim-greenhouse-001`，约每 5 秒发布一次数据。协议采用 JSON 信封：

```json
{
  "device_id": "sim-greenhouse-001",
  "timestamp": "2026-08-25T00:31:23Z",
  "payload": {}
}
```

主要主题：

| 主题 | 方向 | 内容 | QoS |
| --- | --- | --- | --- |
| `farm/{device_id}/sensor/soil` | 设备 -> 服务端 | 土壤湿度、温度、pH、氮磷钾、电导率、盐度 | 1 |
| `farm/{device_id}/sensor/climate` | 设备 -> 服务端 | 光照、空气温度、空气湿度 | 1 |
| `farm/{device_id}/control/pump` | 服务端 -> 设备 | 水泵 `start` / `stop` | 1 |
| `farm/{device_id}/status/pump` | 设备 -> 服务端 | 水泵执行确认 | 1 |

云端 Mosquitto 容器中已实际完成：

- `mosquitto_pub` 和 `mosquitto_sub` 版本检查；
- 传感器气候消息发布和订阅；
- 模拟器土壤、气候消息接收；
- 水泵控制消息发布和订阅；
- 最终结果 `MQTT_RESULT=PASS`。

### 2.3 Flask API 与控制闭环

API 负责设备注册状态、最新遥测、历史趋势和水泵控制。水泵控制流程如下：

```text
Web 看板点击 start/stop
    -> POST /api/v1/devices/{device_id}/pump
    -> API 生成 command_id
    -> MQTT control/pump
    -> 模拟器改变水泵状态
    -> MQTT status/pump 回传
    -> API 返回 confirmed 和 latency_ms
    -> 页面显示执行结果
```

已验证内容包括健康检查、设备列表、最新 soil/climate 数据、水泵 `start`/`stop`、确认状态和延迟显示。灌溉后湿度按照连续状态模型逐步上升，停止后逐步回落，温度和光照围绕基线小幅波动，而不是每次完全随机跳变。

### 2.4 Web 可视化

当前 Web 看板包含：

- Overview：API、MQTT、AI 状态、当前遥测和水泵状态；
- Trends：最长 10 小时窗口、时间横坐标、数值纵坐标、湿度/温度/光照曲线；
- Devices：设备 ID、在线状态和设备信息；
- Control：手动/自动模式、灌溉阈值、水泵控制、告警和确认状态；
- 响应式布局，支持桌面和移动视口。

云端入口：`http://43.156.230.129:8080/`

当前入口是 HTTP 公网 IP，不是 HTTPS 域名。GitHub Pages 只能托管静态 HTML/CSS/JavaScript，不能替代 Docker、MQTT、MySQL 或 Python API，因此动态系统使用腾讯云容器服务，GitHub 用于源码和文档管理。

### 2.5 图片和 AI 边界

图片接口已支持 JPEG/PNG 校验、大小限制、统一 JPEG 转换、缩略图和元数据查询。AI 服务健康检查和接口边界已建立，但实际图像训练、权重导出和生产推理仍未完成；当前 501 状态是明确的占位，不代表模型已经训练完成。

### 2.6 数据库验证

在本地虚拟机 MySQL 容器 `mysql8` 中创建并验证了自定义数据库：

- 数据库：`yinsiyuan`
- 表：`yinsiyuan_data`
- `record_id`：`INT UNSIGNED`
- `display_name`：`VARCHAR(64)`
- `reading_value`：`DECIMAL(10,2)`
- `event_time`：`DATETIME`
- `enabled`：`BOOLEAN`

云端应用 MySQL 容器为 `smartagri-cloud-mysql-1`。两个环境相互独立，不能把本地验证数据库误认为云端业务数据库迁移已经完成。

## 3. 腾讯云部署

云服务器：

- 公网 IP：`43.156.230.129`
- Ubuntu Server 24.04 LTS
- Docker Compose 项目：`smartagri-cloud`

已运行服务：Web、API、模拟器、Mosquitto、MySQL、AI。公网仅开放 Web 网关 `8080`；API、AI、MQTT 和 MySQL 绑定在服务器本机或 Docker 内部网络，避免直接暴露数据库和消息代理。

## 4. 12 天计划实际状态

| 天数 | 目标 | 当前状态 |
| --- | --- | --- |
| 第 1 天 | 需求、架构、Docker、Git 规范 | 已完成 |
| 第 2 天 | 设备模拟器与 MQTT | 已完成并在本地、云端验证 |
| 第 3 天 | Flask API 与设备管理 | 已完成并验证健康、设备、控制接口 |
| 第 4 天 | 图片上传与存储 | 已完成并验证图片校验、缩略图 |
| 第 5 天 | AI 数据与识别基础 | 接口和状态边界完成，模型训练待继续 |
| 第 6 天 | 推理服务与业务集成 | AI 服务容器已部署，真实权重和推理待继续 |
| 第 7 天 | Web/APP 可视化基础 | Web 看板已完成；原生鸿蒙 APP 待 DevEco 工程 |
| 第 8 天 | 灌溉控制、趋势和告警 | Web 控制、10 小时趋势、确认状态已完成 |
| 第 9 天 | 端云联调 | Web 与云端联调完成，原生 ArkTS 联调待继续 |
| 第 10 天 | 集成测试与规则 | 主要链路已验证，自动规则和异常用例待补充 |
| 第 11 天 | 安全、性能、运维 | 基本边界完成；HTTPS、MQTT TLS、CORS、备份待加固 |
| 第 12 天 | 文档和交付 | 本文件、任务日志、截图索引正在补齐 |

## 5. 原生鸿蒙 APK/HAP 还缺什么

当前 Web 页面不能直接等同于鸿蒙 APK。还需要：

1. DevEco Studio 与 HarmonyOS SDK；
2. ArkTS Stage 工程、`EntryAbility`、页面 `.ets` 和 `module.json5`；
3. 网络、通知和设备访问权限配置；
4. 将 Web API 调用迁移为 ArkTS 网络层，或封装 MQTT 客户端；
5. 签名证书、Profile、设备调试和 HAP 构建；
6. 真机/模拟器安装测试；
7. 后续再连接真实 BearPi-HM Nano 的 GPIO、串口或 MQTT 上报。

## 6. 证据文件

仓库 `evidence/` 保存不含密码、Token、私钥和 `.env` 的验证截图与文本。桌面原始证据仍保留在 `C:\Users\DELL\Desktop\测试实习`。

| 文件 | 说明 |
| --- | --- |
| `mqtt_cloud_evidence.png` | 云端 MQTT 排版验证图 |
| `mqtt_cloud_raw_terminal.png` | 云端 SSH 原始输出转录图 |
| `mqtt_cloud_raw_terminal.txt` | 云端实际 SSH 输出文本 |
| `yinsiyuan_local_db_evidence.png` | 本地虚拟机数据库验证图 |
| `smartagri-dashboard-day08-10h.png` | 10 小时趋势图 |
| `smartagri-dashboard-day08-pump-control.png` | 水泵确认和延迟图 |
| `smartagri-dashboard-day08-control.png` | 灌溉、告警、控制图 |

## 7. 安全和未完成项

- 不提交 Token、密码、私钥、`.env` 或含凭据的日志；
- 公网入口目前为 HTTP IP，尚未配置域名 HTTPS；
- MQTT 账号认证、TLS、CORS 白名单和安全组最小化仍需加固；
- MySQL 备份、监控、日志轮转和压力测试仍需补齐；
- 模拟器通过不代表真实 BearPi 硬件已经通过；
- 原生鸿蒙 APK/HAP 和真实硬件烧录是下一阶段任务。
