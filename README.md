# 智慧农业大棚监控系统

本仓库按《中国地质大学 12 天实训计划》推进，目标是完成“设备模拟器 -> MQTT -> Flask -> MySQL -> AI -> 鸿蒙 APP”的端云智端闭环。

## 当前进度

- [x] 第 1 天：需求基线、架构基线、Docker Compose 骨架、Git 规范
- [x] 第 2 天：设备模拟器与 MQTT 对接
- [x] 第 3 天：Flask 服务端与设备管理
- [x] 第 4 天：图片上传与存储
- [x] 第 5 天：AI 图像分类需求、数据集结构与标注规范
- [ ] 第 6 天：模型导出与业务集成（推理接口契约已完成，待真实数据训练）
- [ ] 第 7-8 天：鸿蒙 APP 与灌溉控制
- [ ] 第 9-10 天：端云 APP 联调与自动化规则
- [ ] 第 11 天：安全、性能与运维
- [ ] 第 12 天：工程文档、代码规范与交付检查

## Day 2 验证

设备模拟器会通过 MQTT 发布土壤和气候数据，并接收水泵控制指令。协议、字段和验收命令见 [`docs/day02-mqtt.md`](docs/day02-mqtt.md)。

## Day 3 验证

Flask API 会订阅 MQTT 传感器消息并提供设备查询、最新遥测和水泵控制接口，详见 [`docs/day03-api.md`](docs/day03-api.md)。

Day 4 图片接口、存储约定和验收命令见 [`docs/day04-images.md`](docs/day04-images.md)。

## Web 可视化看板

已提供无需构建的静态看板，展示实时遥测、土壤湿度趋势、灌溉控制和现场图片上传。运行 `python3 -m http.server 8080 --bind 0.0.0.0 --directory web` 后访问 `http://<虚拟机 IP>:8080/`。详细说明和验收记录见 [`docs/web-dashboard.md`](docs/web-dashboard.md)。

## 启动 Day 1 环境

1. 复制 `.env.example` 为 `.env`，修改本机密码。
2. 执行 `docker compose config` 检查配置。
3. 执行 `docker compose up -d --build` 启动服务。
4. 验证 `http://127.0.0.1:8000/healthz` 和 `http://127.0.0.1:8001/healthz`。

默认端口：API `8000`、AI `8001`、MySQL `3306`、MQTT `1883`、MQTT WebSocket `9001`。

## 安全边界

- `.env`、token、密码文件不会提交。
- MQTT 的匿名监听仅用于 Day 1 本地开发，进入第 11 天必须改为账号认证和 TLS。
- AI 接口当前是明确的 501 占位实现，不伪装成已完成模型能力。
