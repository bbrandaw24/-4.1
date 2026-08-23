# Day 1 - 项目需求规格说明书（SOW）

## 目标

构建一个可本地部署、可演示的智慧农业大棚监控系统，覆盖环境采集、远程灌溉、图片上传、草莓生长阶段识别和鸿蒙端可视化。

## 范围

- 设备端：软件模拟土壤 8 项参数、光照、空气温湿度、继电器和摄像头。
- 传输端：MQTT 主题、QoS、设备在线状态和控制指令。
- 服务端：Flask HTTP/MQTT 双协议、MySQL 持久化、图片上传。
- AI 端：草莓生长阶段模型训练与推理，安排在第 5-6 天。
- 应用端：HarmonyOS ArkTS 看板、趋势、图片与灌溉控制，安排在第 7-10 天。

## 验收目标

1. `docker compose up -d --build` 能启动本地 API、AI、MySQL、Mosquitto。
2. API 和 AI 健康检查返回 200。
3. 设备数据按 `farm/{device_id}/sensor/soil` 和 `farm/{device_id}/sensor/climate` 上报。
4. 灌溉控制按 `farm/{device_id}/control/pump` 下发并能回传状态。
5. 图片上传、AI 推理和 APP 展示在后续阶段形成闭环。

## 非目标

本阶段不实现真实硬件烧录，不将任何本地 token、密码或外部账号信息提交到仓库。
