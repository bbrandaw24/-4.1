# Day 7 鸿蒙 APP 基础与可视化

## 页面结构

现有 Web 看板先作为可运行的跨端交互基线，页面路由与 HarmonyOS ArkTS 对应如下：

| 路由 | 页面职责 | ArkTS 映射 |
| --- | --- | --- |
| `overview` | 实时指标、灌溉入口、现场图片和 AI 状态 | `pages/OverviewPage.ets` |
| `trends` | 土壤湿度、空气温度和光照趋势 | `pages/TrendsPage.ets` |
| `devices` | 设备注册信息、API/MQTT/AI 状态 | `pages/DevicesPage.ets` |

## 状态模型

移动端状态最小集合为 `device`、`samples`、`connection` 和 `aiStatus`。对应 ArkTS 可使用 `@State` 保存页面状态，子组件通过 `@Prop` 接收只读数据；网络层统一封装 API 请求和 JSON 解析，页面不直接访问数据库。

## 数据刷新与图表

- API 设备数据每 5 秒刷新一次。
- AI 模型状态每 15 秒刷新一次。
- 趋势页缓存最近 7200 个样本，按 5 秒刷新形成 10 小时滚动窗口；API 同时提供历史查询接口。
- Web 基线使用 Canvas 绘制曲线；ArkTS 端可替换为 Canvas 或 ECharts 组件，保持同一数据模型。
- API 不可用时保留页面结构并显示离线状态，不清空最后一次有效数据。

## 本日交付范围

已完成 Web 基线的页面路由、移动端响应式布局、实时数据状态和三类趋势图。DevEco Studio 工程和真机安装包需要在具备 HarmonyOS SDK/设备的环境中继续生成，当前不虚构 APK 产物。
