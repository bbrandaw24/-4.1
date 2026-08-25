# 项目证据索引

本目录只保存与项目验收相关的非敏感截图和文本。未复制 GitHub Token、密码、SSH 私钥、`.env` 或含凭据的日志；桌面原始目录中的凭据文件也不属于项目证据。

## 文件说明

| 文件 | 对应工作 |
| --- | --- |
| `smartagri-dashboard.png` | Web 看板总体页面 |
| `smartagri-dashboard-day08-10h.png` | 10 小时趋势窗口、时间横坐标和数值纵坐标 |
| `smartagri-dashboard-day08-control.png` | 灌溉控制、告警和状态区域 |
| `smartagri-dashboard-day08-pump-control.png` | 水泵控制确认、命令和延迟显示 |
| `smartagri-dashboard-day08-pump.png` | 水泵状态变化页面 |
| `smartagri-dashboard-day06-ai.png` | AI 服务状态边界页面；不代表真实模型已经训练 |
| `mqtt_cloud_evidence.png` | 云端 MQTT 发布/订阅结果排版图 |
| `mqtt_cloud_raw_terminal.png` | 云端 SSH 输出的终端样式转录图 |
| `mqtt_cloud_raw_terminal.txt` | 上述云端 MQTT 实际输出文本 |
| `yinsiyuan_local_db_evidence.png` | 本地虚拟机 MySQL 数据库和五字段表验证图 |
| `yinsiyuan_local_db_evidence.txt` | 本地数据库验证文本 |

`mqtt_cloud_raw_terminal.png` 是根据实际 SSH 命令输出制作的终端样式证据图，不应描述为 MobaXterm 窗口的直接屏幕截图；同目录的 `.txt` 文件保留了可检索的原始输出。

## 证据与结论边界

- MQTT 证据证明消息发布、订阅和水泵控制主题可用，不证明真实硬件已接入。
- Web 截图证明当前模拟数据和控制流程可视化，不证明原生 HarmonyOS HAP/APK 已完成。
- AI 页面只证明服务边界和状态可查看，真实训练、权重导出和生产推理仍是后续工作。
