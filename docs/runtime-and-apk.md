# 运行环境与鸿蒙 APK 说明

## 为什么现在使用虚拟机

当前 Windows 主机上的浏览器只负责打开看板；Linux 虚拟机 `192.168.128.129` 承载 Docker、MQTT Broker、Flask API、设备模拟器和静态网页。这样可以让 API、MQTT 和后续鸿蒙客户端通过稳定的局域网地址联调。

虚拟机不是智慧农业系统的硬性要求。生产部署可以换成 Linux 服务器、云主机或开发板；本项目当前选择它，是因为已有 Kali/Ubuntu 虚拟机和 Docker 环境，并且不需要改变 Windows 主机环境。现有旧服务继续在 `8000`，Day 8 闭环服务使用 `8010`，用于并行验收。

## 当前网页与 APK 的关系

`web/` 是可运行的 Web/ArkTS 交互基线，不是已经编译好的鸿蒙工程。它验证了页面布局、遥测数据模型、HTTP API、MQTT 控制闭环和告警逻辑；浏览器可以直接访问：

```text
http://192.168.128.129:8080/?view=overview&device=sim-greenhouse-day08
```

## 生成 APK 还缺什么

1. DevEco Studio 和匹配的 HarmonyOS SDK/API level。
2. 原生 ArkTS 工程（Stage 模型、EntryAbility、页面 `.ets` 文件和 `module.json5`）。当前仓库只有 Web 页面映射文档，还没有可直接导入 DevEco 的工程目录。
3. 鸿蒙端网络层：使用兼容 HarmonyOS 的 MQTT 客户端（或系统原生 MQTT 实现），连接 `1883`/WebSocket `9001`，并调用 API `8010`。
4. 权限和配置：网络权限、通知权限、振动权限、API/MQTT 地址，以及设备离线和确认超时处理。
5. 签名材料：DevEco 的调试签名可生成本地测试包；发布/安装到指定真机还需要对应的签名配置、设备授权和 USB/无线调试。
6. 真机或模拟器验收：检查 MQTT.js 兼容性、页面布局、通知、振动、后台网络和水泵控制闭环。

所以目前不能诚实地说已经有 APK：端云协议和可视化基线已完成，但缺少 DevEco 工程、SDK、签名和真机验收条件。补齐这些条件后，才执行 `Build > Build Hap(s)/APP(s)` 生成 `.hap`/`.app` 产物。
