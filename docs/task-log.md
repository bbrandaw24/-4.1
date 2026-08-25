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

## 2026-08-23 至 2026-08-25 - Day 5 至 Day 12 阶段同步

- 完成 Web 可视化看板，包含 Overview、Trends、Devices 和 Control 页面；趋势页支持最长 10 小时窗口以及时间横坐标、数值纵坐标。
- 调整模拟器为连续状态模型：灌溉启动后土壤湿度逐步上升，停止后逐步回落；温度、光照围绕基线小幅波动，避免不合理的大范围随机跳变。
- 完成水泵 Web 控制链路：页面请求 API，API 产生 `command_id` 并发布 `control/pump`，模拟器回传 `status/pump`，页面显示 `confirmed` 和 `latency_ms`。
- 部署同一套服务到腾讯云 Ubuntu 24.04，Compose 项目名为 `smartagri-cloud`；Web 网关通过 `http://43.156.230.129:8080/` 对外提供演示入口。
- 云端仅对外提供 Web 网关；API、AI、Mosquitto 和 MySQL 保持在服务器本机或 Docker 内部网络，未作为公网数据库或消息代理开放。
- 在本地虚拟机 MySQL 容器 `mysql8` 中创建 `yinsiyuan.yinsiyuan_data`，验证 `INT UNSIGNED`、`VARCHAR(64)`、`DECIMAL(10,2)`、`DATETIME`、`BOOLEAN` 五种字段类型。
- 在云端 Mosquitto 容器完成实际 `mosquitto_sub`/`mosquitto_pub` 验证，包含气候主题、水泵控制主题和模拟器遥测；最终输出为 `MQTT_RESULT=PASS`。
- 整理阶段交付说明、12 天计划实际状态和证据索引，明确 AI 真实模型、原生鸿蒙 HAP/APK、真实 BearPi 硬件和安全加固仍为后续任务。

### 阶段验证记录

| 检查项 | 结果 | 证据 |
| --- | --- | --- |
| Web 看板和 10 小时趋势 | 通过页面截图核对 | `evidence/smartagri-dashboard-day08-10h.png` |
| 水泵控制、确认和延迟显示 | 通过页面截图核对 | `evidence/smartagri-dashboard-day08-pump-control.png` |
| 本地虚拟机数据库建表 | 通过 | `evidence/yinsiyuan_local_db_evidence.png` |
| 云端 MQTT 发布/订阅 | 通过，`MQTT_RESULT=PASS` | `evidence/mqtt_cloud_evidence.png`、`evidence/mqtt_cloud_raw_terminal.txt` |
| AI 真实模型推理 | 未完成，不作为已验收功能 | 见 `docs/project-completion.md` |
| 原生 HarmonyOS HAP/APK | 未完成，不作为已验收功能 | 见 `docs/project-completion.md` |
