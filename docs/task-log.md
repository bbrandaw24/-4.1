# 任务日志

## 2026-08-23 - Day 1 基线

- 依据《中国地质大学12天.docx》确认主线为智慧农业，未采用过时的“堵桥”任务。
- 确认仓库初始为空 Git 仓库，没有可继承的业务代码或远端提交。
- 完成 SOW、端云智端架构、12 天执行计划、Git 规范。
- 建立 Docker Compose 开发骨架：MySQL、Mosquitto、Flask API、AI 服务。
- API/AI 增加健康检查；AI 预测接口明确返回 501，避免虚报完成。
- 安全检查：token 文件未读取、未复制、未加入 Git；`.gitignore` 已屏蔽敏感文件。

### Day 1 验证记录

| 检查项 | 结果 |
| --- | --- |
| `docker compose config` | 待在 Docker 环境执行 |
| API `/healthz` | 待容器启动后执行 |
| AI `/healthz` | 待容器启动后执行 |
| Git 工作区无敏感文件 | 已通过忽略规则检查 |

## 2026-08-23 - Day 2 设备与 MQTT

- 增加可运行的 Python 虚拟设备模拟器，默认每 5 秒发布土壤、气候数据。
- 固化 MQTT 主题、QoS、JSON 信封和传感器字段规格。
- 实现水泵 `start`/`stop` 控制指令及状态回传。
- Compose 增加 `simulator` 服务；Docker 实机验收仍待执行。

### Day 2 验证记录

| 检查项 | 结果 |
| --- | --- |
| Python 模块静态编译 | 待执行 |
| `docker compose config` | 待在 Docker 环境执行 |
| MQTT 发布/订阅 | 待容器启动后执行 |

### Day 2 虚拟机实测（2026-08-23）

| 检查项 | 结果 |
| --- | --- |
| SSH 公钥免密连接 | 通过，主机 `bearpi-virtual-machine` |
| Docker / Compose 版本 | Docker 24.0.2 / Compose v2.18.1 |
| 模拟器构建 | 通过，镜像 `smartagri-simulator` |
| 土壤 MQTT 上报 | 通过，收到 `farm/sim-greenhouse-001/sensor/soil` JSON |
| 水泵 start 控制 | 通过，收到 `status/pump` 且 `running=true` |
| MQTT Broker | 使用虚拟机已有 EMQX（宿主机 1883 已被占用）；未停止或删除原容器 |

备注：由于项目目录含中文，Compose 命令需显式指定项目名，例如 `docker compose -p smartagri ...`。

## 2026-08-23 - Day 3 Flask 服务端与设备管理

- API 增加 MQTT 监听器，订阅 `farm/+/sensor/+` 并校验 JSON 信封。
- 增加设备列表、最新遥测和水泵控制 API。
- API 改为单 Gunicorn worker，避免重复订阅 MQTT。
- 当前设备注册表为进程内状态，MySQL 持久化留待后续阶段真实接入。
- 新增 `docs/day03-api.md` API 文档。

### Day 3 验证记录

| 检查项 | 结果 |
| --- | --- |
| Python 静态编译 | 待执行 |
| Flask API 测试 | 已通过静态测试；水泵发布修复后待容器复测 |

### Day 3 虚拟机实测（2026-08-23）

| 检查项 | 结果 |
| --- | --- |
| API 镜像构建 | 通过，`smartagri-api:day03` |
| API 健康检查 | 通过，`GET /healthz` 返回 200 |
| MQTT -> Flask 消息路由 | 通过，`GET /api/v1/devices` 返回 `sim-greenhouse-001` 最新 soil/climate 数据 |
| Flask -> MQTT 水泵控制 | 通过，`POST /api/v1/devices/sim-greenhouse-001/pump` 返回 202；模拟器日志确认 `pump state changed: True` |
| 运行容器 | `smartagri-api-vm`、`smartagri-simulator-vm` 正常运行 |

## 2026-08-23 - Day 4 图片上传与存储

- 增加 multipart 图片上传接口和图片元数据查询接口。
- 增加 JPEG/PNG 内容校验、5 MiB 默认大小限制和 Pillow 解码。
- 上传图片统一转换为 JPEG，并生成 512px 内缩略图。
- Compose 将 `/data/uploads` 映射到项目 `data/uploads/`，不保存原始文件名。

### Day 4 验证记录

| 检查项 | 结果 |
| --- | --- |
| Python 静态编译 | 已通过 |
| 图片上传与缩略图生成 | 已通过，上传返回 201，原图/缩略图均返回 200 image/jpeg |
| 非图片/超大文件拒绝 | 已通过，文本伪装文件返回 415 invalid_image |

### Day 4 虚拟机实测（2026-08-23）

| 检查项 | 结果 |
| --- | --- |
| API 镜像构建 | 通过，`smartagri-api:day04` |
| 图片元数据 | 通过，返回随机 `image_id`、尺寸、设备 ID 和访问 URL |
| 文件存储 | 通过，`data/uploads/` 生成原图 JPEG 和 `_thumb.jpg` |
| 非法图片拒绝 | 通过，返回 HTTP 415 |
| API 容器与模拟器联动 | 待在虚拟机执行 |

## 2026-08-23 - Web 可视化验收

- 增加 `web/` 静态温室运行看板，支持实时遥测、趋势图、灌溉控制和图片上传。
- API 增加跨域响应头，允许本地静态页面调用；生产环境可通过 `CORS_ORIGIN` 限制来源。

### 验证记录

| 检查项 | 结果 |
| --- | --- |
| 页面加载 | 通过，`http://192.168.128.129:8080/` |
| API 连接 | 通过，页面显示 `API 已连接` 和 `sim-greenhouse-001` |
| 实时遥测 | 通过，土壤湿度、空气温度、光照、土壤明细和趋势图正常显示 |
| 水泵启动 | 通过，API 返回 202，模拟器日志确认 `pump state changed: True` |
| 水泵停止 | 通过，API 返回 202，模拟器日志确认 `pump state changed: False` |
| 截图留档 | `C:\Users\DELL\Desktop\测试实习\smartagri-dashboard.png` |

## 2026-08-23 - Day 5/6 AI 基线

- 完成草莓萌芽、开花、坐果、成熟四分类定义和 `train/val/test/reject` 数据集目录。
- 固化 `labels.json`、标注审核规则、数据划分原则和 ResNet18 训练契约。
- 增加 `GET /api/v1/model/status`，明确返回模型未就绪、版本、类别和置信度阈值。
- 保留 `POST /api/v1/predict` 的 501 响应，直到真实图片完成审核、训练并生成权重。

### Day 5/6 验证记录

| 检查项 | 结果 |
| --- | --- |
| 数据集目录和标签 JSON | 已完成 |
| AI 模块静态编译 | 待执行 |
| 模型状态接口 | 已实现，待容器验收 |
| 真实模型训练 | 待收集并审核真实图片 |

### Day 6 虚拟机实测（2026-08-23）

| 检查项 | 结果 |
| --- | --- |
| AI 镜像构建 | 通过，`smartagri-ai:day06` |
| AI 容器 | `smartagri-ai-vm` 正常运行，端口 `8001` |
| `GET /healthz` | 通过，返回 200 |
| `GET /api/v1/model/status` | 通过，返回 `not_ready`、四分类和阈值 `0.60` |
| Web 看板 AI 状态 | 通过，显示 `MODEL PENDING`，不虚构预测结果 |
| 截图留档 | `C:\Users\DELL\Desktop\测试实习\smartagri-dashboard-day06-ai.png` |

## 2026-08-23 - Day 7 APP 基础与可视化

- 在现有 Web 看板上增加 `overview`、`trends`、`devices` 三个页面路由，作为 ArkTS 页面映射基线。
- 增加移动端响应式导航和设备注册页，展示 API、MQTT、AI 三条连接状态。
- 趋势页增加土壤湿度、空气温度/光照 Canvas 曲线，缓存最近 18 个样本并按 5 秒刷新。
- 新增 `docs/day07-harmony-app.md`，记录 ArkTS 页面结构、`@State`/`@Prop` 状态模型和网络层约定。

### Day 7 验证记录

| 检查项 | 结果 |
| --- | --- |
| 概览/趋势/设备路由 | 已实现 |
| 移动端布局 | 已实现，待 HarmonyOS 真机验收 |
| 实时数据和趋势缓存 | 已实现，使用虚拟机 API 验收 |
| DevEco Studio APK | 待具备 HarmonyOS SDK 的开发机生成 |

## 2026-08-24 - Day 8 灌溉控制与 MQTT

- API 水泵指令增加 `command_id`、发送时间、确认时间和响应耗时。
- API 订阅 `farm/+/status/pump`，只有收到设备回执后才将页面状态标为运行中/待机。
- 增加水泵状态查询和低湿度/高温告警接口；相同动作的未确认指令会被拒绝，避免重复发送。
- 模拟器回传 `command_id`，形成控制指令闭环。
- Web 看板控制区增加确认状态、手动/自动模式、低湿度阈值、定时配置、告警列表和浏览器通知入口。
- 模拟器改为连续状态模型：灌溉运行时土壤湿度按每 5 秒约 1.8 个百分点上升，停止后缓慢回落；温度、光照和空气湿度改为围绕基线的小幅波动。
- 趋势缓存从 90 秒扩展为 10 小时（最多 7200 个 5 秒样本），API 增加历史查询接口。

### Day 8 验证记录

| 检查项 | 结果 |
| --- | --- |
| API/模拟器 Python 静态编译 | 已通过 |
| 指令 ID 和状态主题回传 | 已通过，`start`/`stop` 均收到确认回执 |
| 页面确认状态和响应耗时 | 已通过，页面显示 `MQTT 已确认`，实测 2.9–8.2 ms |
| 告警接口与页面告警区 | 已通过，接口返回 0 条当前告警，页面正常展示 |
| 虚拟机 Day 8 服务 | 已通过，新服务使用 API `8010`，未停止旧容器 |
| 截图留档 | `C:\Users\DELL\Desktop\测试实习\smartagri-dashboard-day08-pump-control.png` |
| HarmonyOS MQTT.js 真机 | 待具备 DevEco SDK/真机的开发机验收 |

### Day 8 复测（连续传感器模型）

| 检查项 | 结果 |
| --- | --- |
| 灌溉对湿度的影响 | 通过，49.79% -> 56.95%，16 秒增加 7.16 个百分点 |
| 水泵停止确认 | 通过，MQTT 回执延迟 2.6 ms |
| 10 小时历史接口 | 通过，`/telemetry/history?hours=10` 正常返回 |
