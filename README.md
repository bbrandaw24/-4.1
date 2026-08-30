# 智慧农业大棚监控系统

本仓库是《中国地质大学 12 天实训计划》的智慧农业项目实现，主线为“设备模拟器 -> MQTT -> Flask API -> 数据库 -> Web 看板 -> 智能体问答 -> 云端部署”。


## 项目当前状态

当前已形成可运行、可验证的端云一体化演示系统：腾讯云部署 Web 看板 + 多地块模拟器 + 云端知识库智能体，GitHub 用于源码、文档和非敏感证据管理。

- 已完成：多地块（苹果/梨/橘）模拟器、MQTT 消息链路、Flask API、图片上传与缩略图、Web 看板（概览/趋势/设备/智能体问答 4 个板块）、地块切换与多地块总览、10 小时趋势（SQLite 持久化）、水泵控制闭环、告警记录（SQLite 持久化）、自动灌溉规则、云端知识库 + Luna 双模式智能体问答、**传感器注册表（每块地 5 类虚拟硬件，可连接/断开/删除/创建，MQTT 实时上报）**、腾讯云部署。
- 已验证：云端 MQTT 发布/订阅、三地块遥测、水泵控制确认、告警日志接口、知识库问答与 Luna 大模型回答、角色权限（游客仅知识库、农户/管理者可用 Luna）、**15 个传感器（3 地块 × 5 类）实时上报与 CRUD**、Web 访问与主要 API。
- 待继续：HTTPS/MQTT TLS/认证、备份监控、原生鸿蒙 APK/HAP、真实 BearPi-HM Nano 硬件接入。

完整阶段交付说明见 [`docs/project-completion.md`](docs/project-completion.md)，按天记录见 [`docs/plan-12-day.md`](docs/plan-12-day.md) 和 [`docs/task-log.md`](docs/task-log.md)。远程部署和 Web 运行说明见 [`docs/cloud-deployment.md`](docs/cloud-deployment.md)、[`docs/web-dashboard.md`](docs/web-dashboard.md) 和 [`docs/runtime-and-apk.md`](docs/runtime-and-apk.md)。

## 版本更新记录

每次推送（提交）对应一个版本号，从 **v1.1** 开始，按 **v1.1 → v1.2 → … → v1.5 → v2.1 → v2.2 → …** 递增：即每 5 个版本进位一次（1.5 之后进入 2.1，依此类推）。此记录随仓库更新持续追加。

| 版本 | 日期 | 提交 | 改动内容 |
| --- | --- | --- | --- |
| v1.1 | 2026-08-23 | `1024241` | day01：智慧农业基础工程，仓库初始化（API/模拟器/看板骨架 + 实训计划文档） |
| v1.2 | 2026-08-23 | `46c3899` | day02：新增 MQTT 设备模拟器（模拟环境传感器数据上报） |
| v1.3 | 2026-08-23 | `c4b599c` | day02：记录虚拟机 MQTT 发布/订阅链路验证 |
| v1.4 | 2026-08-23 | `c1a4979` | day03：API 运行时与模拟器对齐（数据格式与接口匹配） |
| v1.5 | 2026-08-23 | `2f77d2d` | day03：修复 MQTT 水泵控制指令发布 |
| v2.1 | 2026-08-23 | `3bebb67` | day03：处理 paho 发布完成回调（确认指令已发出） |
| v2.2 | 2026-08-23 | `486f788` | day03：标记 API 集成验证通过 |
| v2.3 | 2026-08-23 | `d86f930` | docs：更新 day02/day03 进度文档 |
| v2.4 | 2026-08-23 | `b18992a` | day03：补充 API 依赖与接口文档 |
| v2.5 | 2026-08-23 | `30d2b5b` | day04：图片上传与缩略图存储 |
| v3.1 | 2026-08-23 | `f4f8cdc` | day04：记录图片上传验证 |
| v3.2 | 2026-08-23 | `a9a5d84` | feat：交付 Web 温室看板（概览页面） |
| v3.3 | 2026-08-23 | `917af78` | ci：GitHub Pages 发布 Web 看板（CI 工作流） |
| v3.4 | 2026-08-23 | `8c9d159` | chore：触发公网 Pages 部署 |
| v3.5 | 2026-08-23 | `f96c8a1` | ci：允许从 dashboard 分支发布 GitHub Pages |
| v4.1 | 2026-08-23 | `62d5389` | feat：定义草莓 AI 数据集与模型状态 |
| v4.2 | 2026-08-23 | `e0780a6` | feat：看板展示 AI 模型状态 |
| v4.3 | 2026-08-23 | `9966714` | fix：允许看板读取 AI 状态 |
| v4.4 | 2026-08-23 | `65a3f6c` | docs：记录 day06 AI 容器验证 |
| v4.5 | 2026-08-23 | `2a6c6b8` | feat：day07 应用路由与趋势视图（趋势页初版） |
| v5.1 | 2026-08-23 | `2fefb29` | feat：day08 水泵控制闭环 + 灌溉告警 |
| v5.2 | 2026-08-23 | `e8bf31b` | docs：记录 day08 虚拟机验收 |
| v5.3 | 2026-08-23 | `032ff71` | feat：10 小时遥测历史 + 有状态灌溉规则 |
| v5.4 | 2026-08-24 | `46f4f27` | feat：趋势图坐标轴标注 |
| v5.5 | 2026-08-24 | `0cf4bc3` | feat：Web 网关代理看板服务（/api、/ai） |
| v6.1 | 2026-08-24 | `f23dcba` | feat：网关容器化（Docker 部署 Web 网关） |
| v6.2 | 2026-08-24 | `7889e61` | security：内部服务仅绑定 localhost |
| v6.3 | 2026-08-24 | `2c10870` | docs：新增智慧农业实习报告 |
| v6.4 | 2026-08-24 | `a9b88d7` | docs：打磨详细版实习报告 |
| v6.5 | 2026-08-25 | `10712f0` | docs：同步完整交付证据 |
| v7.1 | 2026-08-25 | `258b6ef` | merge：同步远端项目进度与文档 |
| v7.2 | 2026-08-26 | `d11bed8` | feat(day10)：自动灌溉规则引擎 + 测试 |
| v7.3 | 2026-08-26 | `b5cd116` | feat(day11)：登录页（角色选择）+ 后端认证/RBAC 权限 |
| v7.4 | 2026-08-26 | `a740492` | fix(deploy)：API 宿主机端口对齐 8010（匹配前端/Pages 预期） |
| v7.5 | 2026-08-26 | `1b52f32` | deploy(cloud)：云端暴露 API/AI 公网 8010/8001，前端默认指向腾讯云 |
| v8.1 | 2026-08-26 | `928398e` | fix(day12)：修复登录后看板无数据 |
| v8.2 | 2026-08-26 | `895b36a` | feat(day12)：退出登录清除浏览器缓存并返回登录页 |
| v8.3 | 2026-08-26 | `12c3bcb` | feat(day12)：云端持久化遥测历史（趋势读最近 10 小时） |
| v8.4 | 2026-08-26 | `29f2bf1` | feat(day13)：云端 RAG 灌溉顾问（第 4 个板块：智能体问答） |
| v8.5 | 2026-08-26 | `81fc5e4` | feat(day13b)：多地块模拟器 + 地块切换 + 告警记录 |
| v9.1 | 2026-08-26 | `eb7a8eb` | fix(day13b)：模拟器 pH 崩溃 + 告警线程 NameError |
| v9.2 | 2026-08-26 | `f452917` | feat(day14)：双模式顾问——知识库 vs Luna（中等思考、角色门控） |
| v9.3 | 2026-08-26 | `9343078` | fix(day14)：Luna 回答被网关 20s 代理超时截断 |
| v9.4 | 2026-08-26 | `963a698` | feat(day14)：Luna 人设 = 主人的猫儿女仆（口癖 + 亲切感） |
| v9.5 | 2026-08-27 | `b0e32fb` | feat(day15)：可配置思考（开关/低中强度）+ 展示思维链 |
| v10.1 | 2026-08-27 | `945e779` | fix(simulator)：水泵控制 NameError（改用 _client 参数） |
| v10.2 | 2026-08-27 | `7f5ede5` | docs(readme)：README 更新至 day15 状态（多地块/告警/双模式顾问） |
| v10.3 | 2026-08-27 | `d8a3d5c` | feat(kb)：知识库扩充 10 → 53 篇灌溉/农事文档 |
| v10.4 | 2026-08-27 | `bac18ea` | test(agent)：知识库扩充后放宽 top-source 断言 |
| v10.5 | 2026-08-27 | `3b9e91c` | feat(api)：/healthz 暴露 kb_docs 数量（部署自验用） |
| v11.1 | 2026-08-27 | `db70210` | feat(web)：Luna 小猫娘吉祥物（待命/思考/回答/失败动态状态）+ 知识库篇数修正 |
| v11.2 | 2026-08-27 | `2f48910` | fix(web)：/agent/ask 返回 HTML 时显示真实错误（mixed content/404/网关） |
| v11.3 | 2026-08-27 | `498e38b` | feat(day16)：传感器注册表——每块地 5 类传感器、MQTT 驱动、CRUD API、模拟器重写 |
| v11.4 | 2026-08-27 | `3aae233` | fix(sim)：恢复旧 profile 字段 + API_UPSTREAM 改用服务名 |
| v11.5 | 2026-08-27 | `ed5e09c` | feat(web)：设备板块重写为传感器管理（连接/断开/删除/添加） |
| v12.1 | 2026-08-27 | `b9a4765` | docs(readme)：全局 MQTT Broker 详细说明（topic 规范、5 类传感器、API、订阅示例） |
| v12.2 | 2026-08-27 | `8af71f9` | feat(day16)：暴露全局 MQTT Broker 配置（DB + API + 前端 UI）；修复传感器面板空态 |
| v12.3 | 2026-08-27 | `7cd06d0` | fix(web)：从 /auth/me 刷新权限，农户免重新登录获得 manage_sensors |
| v12.4 | 2026-08-27 | `685c9c5` | feat(day16)：MQTT Broker 预设选择器（腾讯云/hivemq/emqx/mosquitto-test/自定义） |
| v12.5 | 2026-08-27 | `cd9a6e9` | fix：网关代理传感器 PATCH/DELETE 请求（修复按钮无响应） |
| v13.1 | 2026-08-27 | `06d62ba` | fix：GitHub Pages 自动跳转云端网关（解决 HTTPS 页面调 HTTP API 被拦截） |
| v13.2 | 2026-08-27 | `d9a8dce` | fix：看板 auth.js 缓存版本号刷新 |
| v13.3 | 2026-08-27 | `4130c2d` | fix(agent)：知识库未命中时 Luna 仍激活（修复输入框问题不能激活 Luna） |
| v13.4 | 2026-08-27 | `8095c7c` | docs(readme)：新增版本更新记录（v1.1 起每次推送逐一编号） |
| v13.5 | 2026-08-27 | `2b0bb9b` | fix(web)：app.js 缓存版本号 day13-plots → day16-sensors（修复设备板块操作无响应） |
| v13.6 | 2026-08-27 | `7a1b17d` | fix(web)：右上「API 暂不可用」徽章改为独立 /healthz 轻探针驱动，与 /devices 大响应解耦；setTextContent null 保护 |
| v13.7 | 2026-08-27 | `5f6c4ef` | fix(web)：传感器连接/断开/删除/创建后本地立即更新列表，不再依赖 refresh（修复慢链路下删除看起来无效） |
| v13.8 | 2026-08-27 | `d7992b2` | fix(web)：启动时立即绑定传感器操作事件（不再等首次 refresh 成功）；/auth/me 权限刷新后立即重渲染传感器板 |
| v13.9 | 2026-08-27 | `8ef0773` | feat：添加地块功能（不局限于内置 3 地块）——后端 POST /devices 支持 name/crop + 自动生成 id + 自动 seed 5 传感器 + SQLite 持久化；模拟器每 30s 动态发现新地块并开始模拟上报；前端设备板块新增「+ 添加地块」弹窗 |
| v13.10 | 2026-08-27 | `a791d4d` | feat：删除地块功能——DELETE /api/v1/devices/{id}（自定义地块可删、内置 3 地块保护 403）；模拟器停止模拟已删地块；前端每个自定义地块组显示「删除地块」按钮 |
| v13.11 | 2026-08-27 | `3baba8b` | fix(api)：删除地块墓碑机制——删除后残留 MQTT 遥测被丢弃，防止 setdefault 隐式复活已删地块 |
| v13.12 | 2026-08-27 | `d0a4ab5` | chore：弃用新加坡实例，仅维护北京 + GitHub——auth.js 网关/API 默认地址切到北京 62.234.223.89，README 地址表更新 |
| v13.13 | 2026-08-27 | `996b310` | AI 服务：推理接口骨架与部署（健康检查 / 模型状态 / 预测接口 + Docker 化） |
| v13.14 | 2026-08-27 | `6c67728` | feat(ai)：Riseholme-2021 数据集训练与纯 NumPy 推理集成（4 类 MLP，测试集准确率 0.8123，含数据整理/训练/推理全链路） |
| v14.1 | 2026-08-28 | `99e668b` | feat(ai)：推理内核升级为 TorchScript resnet50-tl（草莓成熟度/状态四分类，test 87.7%，224×224）；前端上传后自动识别 + 概率条展示 |
| v14.2 | 2026-08-28 | `c26e858` | fix(build)：AI 镜像构建修复（容器内 pip 腾讯镜像源、torch 走 PyTorch CPU index、模型文件名对齐） |
| v14.3 | 2026-08-28 | `f0cb07c` | fix(web)：网关模式请求修复——requestAI 补 /ai 前缀、健康探测改 /api/v1/system/status 并兼容 ready 状态、device-page 空元素保护（修复上传/识别无响应与页面报错） |
| v14.4 | 2026-08-28 | `27f5b04` | feat(web)：上传图片完整组件——多选批量（PNG/JPG/JPEG，≤5MiB，≤9 张）、缩略图预览网格、逐张进度条 + 全局 N/M 进度、格式/大小校验与友好提示、失败重试/删除、键盘可操作与 aria 无障碍、每张上传成功自动草莓识别 |
| v14.5 | 2026-08-28 | `bbf8313` | feat(web)：PWA 支持——manifest + Service Worker（静态资源网络优先/离线回退，API 请求不缓存）+ 192/512/maskable 图标，手机浏览器「添加到主屏幕」即可当 App 使用 |
| v14.6 | 2026-08-28 | `20145d3` | feat(ai)：问答 LLM 后端切换智谱 GLM（glm-5.3-flash，open.bigmodel.cn，thinking 参数适配，key 存 .env）；fix(compose)：AI_MODEL_PATH 恢复 resnet50-tl + labels 恢复 classes.json（修 git reset 把服务器 compose 还原成 MLP 路径导致识图 not_ready 501） |
| v15.0 | 2026-08-30 | `d55e862` | feat(web)：**双主题设计系统**——新增 `theme.css`（两套 Design Token：自然清新 / 深色数据大屏）+ `enhance.js`（视觉增强，不改业务逻辑）。顶栏毛玻璃深绿渐变 + 温室叶片 SVG logo + 主题切换按钮（localStorage 记忆 + 跟随系统深色）；指标卡改为图标徽章＋等宽大数字＋数值滚动动画＋迷你 sparkline＋达标/偏低/超标状态标签＋胶囊渐变仪表条；地块卡作物图标与 hover 三项读数；两张趋势图重绘为平滑曲线＋渐变面积＋虚线网格＋hover tooltip＋1h/6h/10h 切换（深色带辉光）；设备页表单分区卡、输入框 focus 光环、传感器小卡（图标+名称+值+状态灯）、弹窗淡入缩放；智能体页气泡带小尾巴、打字三点跳动、建议问题 chip、输入栏胶囊、Luna 外围呼吸光晕（内部动画类名不变）；全局 hover/active、导航下划线滑动、卡片 fade-in-up 交错入场；850/560 断点复核。所有 id/class/data-* 与 DOM 层级保持不变，仅新增装饰子元素；PWA 缓存版本 smartagri-v1 → v2 |
| v15.0.1 | 2026-08-30 | `4a9b427` | fix(theme)：移除 `.sensor-card` 强制 grid 单行布局与 `enhance.js` 强插的 sensor icon/dot，修复设备页数值重叠；chore(sw)：PWA 缓存版本 v2 → v3 |
| v15.0.2 | 2026-08-30 | `11210e5` | fix(theme)：`.panel` 的 `overflow:hidden` / hover `transform` / 深色 `backdrop-filter` 会使嵌在 `.panel.sensors-panel` 内的 `position:fixed` 弹窗（添加地块、添加传感器对话框）被裁剪错位——上述属性一律用 `:not(.sensors-panel)` 排除；`.hidden` 补 `display:none!important` 兜底 |
| v15.0.3 | 2026-08-30 | `3d9f489` | fix(enhance)：**关键修复**——`updateMetrics` 在 MutationObserver 回调里无条件重写 `.ag-spark` 的 innerHTML，再次触发 Observer 形成无限循环，主线程 100% 占用导致页面所有点击失效（即「前端无法删除/创建地块」的根因）；改为内容比对后才写 DOM（`__agSvg` / `__agStatus` 缓存） |
| v15.0.4 | 2026-08-30 | `10c93d6` | perf(theme)：移除逐卡 `backdrop-filter:blur(14px)`（GPU 挂死根因），毛玻璃效果仅保留在顶栏 |
| v15.0.5 | 2026-08-30 | `2b286a7` | perf(theme)：删除全屏 `fractalNoise` 噪点 fixed 层与全站入场动画（软件渲染下每帧整屏重绘造成持续卡顿） |
| v15.0.6 | 2026-08-30 | `c1d49fa` | perf(theme)：地块条卡片（每 5s 重建一次）不再播放入场动画，消除周期性重绘开销 |
| v15.1 | 2026-08-30 | `a082a81` | feat(sim)：**创建地块即上线**——根因是模拟器按「30s 设备发现 + 5s 传感器缓存 + 最长 60s 首帧遥测」轮询，新地块约 95 秒才首次写入 `last_seen`，前端因此显示离线；现 `POST /api/v1/devices` 创建成功后由 API 向 `farm/control/new_plot` 广播事件，模拟器订阅该主题后立即发现该地块并推送首帧遥测，绕过轮询。实测新地块 **0.33 秒上线**（原约 95 秒） |
| v15.2 | 2026-08-30 | `db10b51` | perf(web)：**操作卡顿修复**——「设备页操作明显比原版卡」的根因是 `enhance.js` 用 MutationObserver 监听整个 `.shell` 子树，任何 DOM 变更（每 5 秒的整块 innerHTML 刷新、每秒的水泵状态、以及装饰函数自身写 DOM）都会触发回调，每次回调都跑 4 个全量 `querySelectorAll` 扫描 + 每张指标卡 2 次 `getComputedStyle`（触发样式重算与布局抖动）。修法：① 观察者改为只挂在每 5 秒被重建的 `#plots-strip` / `#telemetry-table` / `#agent-messages` 上、且只看直接子节点（不监听子树），装饰即时跟上且不再级联；② `getComputedStyle` 的 color 结果按元素缓存，只在首次读取；③ sparkline 增加数据签名守卫（`__agSig`），样本数据没变就不重建 SVG 字符串；④ `decorateAgent` 加消息数守卫、空态插画改为一次性装饰（去掉每次全页扫描 `.empty-state`）；⑤ 装饰定时器 2s → 5s 与刷新节奏对齐；⑥ 移除深色主题下 `.chart-panel canvas` 的 CSS `filter`（画布每 5 秒重画都要额外做一次离屏栅格化，辉光改由 canvas 内部 `ctx.shadowBlur` 提供）。PWA 缓存 `smartagri-v3` → `v4`，前端资源版本 `v15-themes` → `v15-perf` |
| v15.3 | 2026-08-30 | `d543c91` | fix(web)：**删除地块无反应 + 添加传感器只对第一个地块生效**。① 删除无反应的根因：`#sensors-board` 的点击委托处理器里有一行 `if (!sensorId) return;` 一刀切守卫，而「删除地块」按钮只带 `data-device` 不带 `data-sensor`，每次点击都被提前拦截，`remove-plot` 分支永远走不到（连 confirm 弹窗都不出现）；改为按 action 分流——传感器动作（toggle/remove）才要求 `sensorId`，地块动作（remove-plot / add-sensor）改用 `deviceId`。② 添加传感器只对第一个地块生效的根因：全局唯一的「+ 添加传感器」按钮写死取 `state.device`（主视图当前选中地块，默认第一个），传感器面板平铺所有地块却没有每地块自己的添加按钮；改为每个地块卡片 header 新增独立的「+ 添加传感器」按钮（`data-action="add-sensor" data-device=...`），并配 `.plot-add-sensor` 样式。PWA 缓存 `smartagri-v4` → `v5`，`app.js` / `sensor.css` 版本串 `day16-sensors` → `v15.3` |

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

## 技术栈

| 类别 | 技术 | 用途 |
| :--- | :--- | :--- |
| **后端框架** | Python 3.12 + Flask | RESTful API |
| **消息协议** | MQTT (Mosquitto) | 设备数据上报与指令下发 |
| **数据库** | SQLite + MySQL | 遥测历史、用户数据、图片存储 |
| **AI 服务** | Luna (OpenAI 兼容) + TorchScript resnet50-tl | 智能体问答 + 草莓成熟度/状态识别（test 87.7%，`POST /api/v1/predict`，torch CPU 推理） |
| **容器化** | Docker + Docker Compose | 服务编排与部署 |
| **前端** | HTML + CSS + JavaScript (原生) | Web 看板 |

## 地址与环境

> 2026-08-27 起仅维护**北京**实例（`lhins-mxnziuro`）与 GitHub，新加坡实例（`43.156.230.129`）已弃用。

| 环境 | 地址/入口 | 用途 |
| --- | --- | --- |
| 腾讯云公网网页（北京） | [http://62.234.223.89:8080/login.html](http://62.234.223.89:8080/login.html) | 对外演示（登录入口） |
| 腾讯云 API（北京） | `http://62.234.223.89:8010/api/v1/` | 遥测/告警/智能体接口 |
| GitHub 仓库 | [bbrandaw24/-4.1](https://github.com/bbrandaw24/-4.1) | 源码与文档（GitHub Pages 会自动跳转到北京网关） |

公网入口目前是 HTTP IP 地址，不代表已经配置 HTTPS 域名。GitHub Pages 只适合静态页面，动态 API、MQTT、SQLite 和模拟器仍运行在云服务器 Docker 中。

## 功能特性

| 板块 | 功能 |
| --- | --- |
| 概览 | 多地块总览（3 卡：名称/作物/湿度/温度/在线态）、「当前地块」下拉切换（全局生效）、关键指标、灌溉控制（水泵启停 + 自动规则）、**现场图像批量上传组件**（多选/预览/进度/校验/重试）、**草莓成熟度识别**（上传后自动，四类概率 + 阈值判定 + AI 状态） |
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
