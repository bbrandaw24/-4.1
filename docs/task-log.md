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

### Day 8 趋势坐标补齐

- 趋势图保留每个样本的实际时间戳，横轴按 10 小时窗口显示采样时间刻度。
- 土壤湿度图增加带 `%` 单位的纵轴；温度/光照图增加左右双纵轴，分别标注 `°C` 和 `kLux`。
- 浏览器强制加载 `app.js?v=day08-axes`，避免旧缓存遮挡坐标更新。
- 虚拟机页面验证地址：`http://192.168.128.129:8080/?view=trends&device=sim-greenhouse-day08`。

## 2026-08-24 至 2026-08-25 - 云端、数据库与阶段交付同步

- 部署同一套服务到腾讯云 Ubuntu 24.04，Compose 项目名为 `smartagri-cloud`；Web 网关通过 `http://43.156.230.129:8080/` 对外提供演示入口。
- 云端仅对外提供 Web 网关；API、AI、Mosquitto 和 MySQL 保持在服务器本机或 Docker 内部网络，未作为公网数据库或消息代理开放。
- 在本地虚拟机 MySQL 容器 `mysql8` 中创建 `yinsiyuan.yinsiyuan_data`，验证 `INT UNSIGNED`、`VARCHAR(64)`、`DECIMAL(10,2)`、`DATETIME`、`BOOLEAN` 五种字段类型。
- 在云端 Mosquitto 容器完成实际 `mosquitto_sub`/`mosquitto_pub` 验证，包含气候主题、水泵控制主题和模拟器遥测；最终输出为 `MQTT_RESULT=PASS`。
- 阶段文档明确 AI 真实模型、原生鸿蒙 HAP/APK、真实 BearPi 硬件和 HTTPS/MQTT TLS 等安全加固仍为后续任务。

### 阶段验证记录

| 检查项 | 结果 | 证据 |
| --- | --- | --- |
| Web 看板和 10 小时趋势 | 通过页面截图核对 | `evidence/smartagri-dashboard-day08-10h.png` |
| 水泵控制、确认和延迟显示 | 通过页面截图核对 | `evidence/smartagri-dashboard-day08-pump-control.png` |
| 本地虚拟机数据库建表 | 通过 | `evidence/yinsiyuan_local_db_evidence.png` |
| 云端 MQTT 发布/订阅 | 通过，`MQTT_RESULT=PASS` | `evidence/mqtt_cloud_evidence.png`、`evidence/mqtt_cloud_raw_terminal.txt` |
| AI 真实模型推理 | 未完成，不作为已验收功能 | 见 `docs/project-completion.md` |
| 原生 HarmonyOS HAP/APK | 未完成，不作为已验收功能 | 见 `docs/project-completion.md` |

## 2026-08-26 - Day 10 自动灌溉规则引擎

- 修复 `services/api/app/main.py` 中重复的两个 CORS `after_request` 处理器，统一为单个函数并支持 `PUT` 预检。
- 新增服务端自动灌溉规则引擎（此前 Web 页面的“自动模式”仅是前端提示，无后端逻辑）：
  - 纯函数 `evaluate_irrigation_rule(rule, moisture, pump_running, pending)`：低湿度（`< start_threshold`）触发启动，回差到 `>= stop_threshold` 触发停止；非法湿度、未启用或存在待确认指令时不触发。
  - 后台评估线程 `irrigation_rule_loop`，每 5 秒（可配 `IRRIGATION_RULE_INTERVAL`）对开启自动的设备做一次规则评估，复用统一指令发布函数 `_publish_pump_command`，指令来源标记为 `auto`，并写入事件日志 `irrigation_events`（最多 200 条）。
  - 冷却时间 `cooldown_seconds` 防止短时间内重复下发。
  - 接口：`GET/PUT /api/v1/devices/<id>/irrigation-rules`、`GET /api/v1/devices/<id>/irrigation-events`。
  - 校验：阈值范围 5–95%、停止阈值必须高于启动阈值、冷却时间非负、类型严格校验，全部返回明确错误码。
- Web 看板（`web/app.js`、`index.html`、`day08.css`）：
  - 模式按钮和阈值输入改为调用真实规则 API（PUT 保存、GET 回显）。
  - 新增 `#rule-status` 实时显示自动规则状态与最近一次自动动作时间。
  - 缓存版本号更新为 `app.js?v=day10-rules`。
- 新增 `services/api/tests/test_irrigation.py`，覆盖决策逻辑、接口校验与评估下发的正常/异常用例，共 16 个用例。

### Day 10 验证记录

| 检查项 | 结果 |
| --- | --- |
| API/模拟器/AI Python 静态编译 | 已通过 |
| Web 脚本 `app.js` 语法 | 已通过 `node --check` |
| 自动灌溉规则 pytest | 已通过，16 passed |
| 规则服务端逻辑联调 | 待在虚拟机/云端容器复测（需 API 与 MQTT 在线） |
| 自动动作在模拟器上的端到端确认 | 待在虚拟机/云端复测 |

### Day 11 登录与多角色权限（认证模块）

需求：登录页支持身份选择（农户 / 管理者 / 游客），注册数据保存在后端，不同身份有不同操作权限。

交付：

- 后端 `services/api/app/auth.py`（新增）：
  - 用户持久化到 SQLite（`DB_PATH`，云端默认 `/data/users.db`，随 `./data` 卷持久化），密码使用 werkzeug 哈希。
  - Token 为 itsdangerous 签名的无状态 Bearer（默认 12 小时，可配 `AUTH_TOKEN_MAX_AGE`），密钥来自 `AUTH_SECRET`。
  - 角色权限矩阵：`guest`（仅查看）、`farmer`（查看 + 灌溉控制）、`manager`（全部 + 规则配置 + 图像上传 + 用户管理）。
  - 接口：`POST /api/v1/auth/register`、`/login`、`/guest`，`GET /auth/me`、`/auth/users`。
  - 可选演示账号种子（`AUTH_SEED_DEMO`，默认开启）：`admin/admin123`（管理者）、`farmer/farmer123`（农户）。
- `services/api/app/main.py`：启动时 `init_db()` 并注册鉴权路由；读接口要求有效 token，写操作按权限分级（`control_pump` / `manage_rules` / `upload_image` / `list_users`）；CORS 允许 `Authorization` 头。
- 前端：
  - `web/login.html` + `login.css` + `login.js` + 共享 `auth.js`：身份选择、登录/注册切换、游客一键进入，注册落库。
  - `web/index.html` / `app.js`：加载即校验 token（无则跳登录），所有请求走 `Auth.request` 注入 token，按角色禁用/隐藏水泵控制（control_pump）、规则编辑（manage_rules）、图像上传（upload_image）；顶部显示角色徽标与登出。
  - `web/auth.css`：角色徽标、锁定态样式。
- 部署：`requirements.txt` 增加 werkzeug/itsdangerous；`docker-compose.yml` 的 api 挂载 `./data:/data` 并加 `DB_PATH`/`AUTH_SECRET` 等环境变量；`.env.example` 补充认证相关变量。

### Day 11 验证记录

| 检查项 | 结果 |
| --- | --- |
| API/认证 Python 静态编译 | 已通过 |
| Web 脚本 `app.js`/`login.js`/`auth.js` 语法 | 已通过 `node --check` |
| 鉴权与权限 pytest（test_auth.py，9 项） | 已通过，9 passed |
| 既有灌溉规则测试（test_irrigation.py，16 项，已补 token） | 已通过，16 passed |
| 登录页与看板联调 | 待在云端容器复测（需 API 在线） |
