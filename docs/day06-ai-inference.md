# Day 6 AI 推理接口

## 当前状态

已完成基于真实数据集 **Riseholme-2021** 的训练与推理集成：

- 数据：`datasets/train/Riseholme-2021-main`（3520 张，类别 `Ripe / Unripe / Occluded / Anomalous`）
- 技术路线：**纯 NumPy + Pillow 轻量 MLP**（6912-512-128-4, ReLU + Dropout + Adam），
  不依赖 PyTorch（本环境 torch 与 numpy 2 不兼容且 ImageNet 权重下载不可用，
  且 60x60 小图下 MLP 足够，镜像更小、部署更简单）
- 划分：Normal 三类使用官方 `Splits/Split1-RUO-*`；Anomalous 官方仅测试(one-class)，
  本项目多分类需要训练样本，故按 70/15/15 随机划分（种子 42），见 `meta.json`
- 类别不平衡：Unripe 占 68%，启用频率倒数加权交叉熵

## 训练与结果（真实指标，不虚构）

```bash
# 1) 整理数据 -> datasets/riseholme-classification
python services/ai/prepare_riseholme.py \
    --riseholme-dir datasets/train/Riseholme-2021-main \
    --output-dir datasets/riseholme-classification

# 2) 训练 -> services/ai/models/
python services/ai/train_numpy.py \
    --data-dir datasets/riseholme-classification \
    --output services/ai/models/riseholme_mlp_v1.npz \
    --model-version riseholme-2021-mlp-v1 --epochs 20 --batch-size 64 --lr 5e-4
```

产物（`services/ai/models/`）：`riseholme_mlp_v1.npz`（权重）、`labels.json`（类别映射）、
`training_report.json`（指标报告）。

**测试集结果**：准确率 **0.8123**，宏平均 F1 **0.5202**。

| 类别 | 准确率 | 说明 |
| --- | --- | --- |
| Ripe | 0.944 | 成熟果实识别良好 |
| Unripe | 0.952 | 未熟果实识别良好 |
| Occluded | 0.269 | 遮挡果实难以判断成熟度，多被误判为 Unripe/Ripe |
| Anomalous | 0.000 | 异常果实（病虫害/畸形）外观接近正常果实，样本少(107)，为最难类 |

混淆矩阵中 `Anomalous` 主要被分到 `Unripe`（18/24），属语义上合理的混淆（病果像未熟果）。
此结果为真实水平，如实记录；后续可通过补充 Anomalous 样本、图像增强或更强模型（CNN）提升。

## 查询模型状态

```http
GET /api/v1/model/status
```

返回模型是否就绪、版本、类别和置信度阈值，供 Web 看板展示。权重加载成功才返回
`ready: true`。看板 `refreshAiStatus()` 由响应驱动，自动显示新模型版本与类别。

## 图像推理

```http
POST /api/v1/predict
Content-Type: multipart/form-data
```

字段：`file`（必填，JPEG/PNG，默认最大 5 MiB）、`device_id`（可选）、`image_id`（可选）。

模型就绪时返回四类 softmax 概率、标签、置信度和版本；低于 0.60 时 `predicted_class`
为 `null`、`status` 为 `uncertain`。权重缺失时返回 `501 not_ready`。
图片先缩放为 48x48 再推理（与训练一致）。

## 部署

`docker-compose.yml` 中 `ai` 服务通过 volume 挂载 `./services/ai/models:/models`，
训练产物放入该目录即可生效，无需重建镜像。Dockerfile 仅安装 Flask/gunicorn/Pillow/numpy，
镜像约 200MB（不含 PyTorch）。
