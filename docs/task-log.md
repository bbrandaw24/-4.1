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
