# Day 5 AI 图像识别基础

## 目标与边界

识别草莓图像主类别，任务是**单标签图像分类**（非目标检测）。每张图片只保留一个主标签。

## 实际落地方案（Riseholme-2021）

原计划使用自有草莓生长阶段数据集（`germination/flowering/fruit_set/ripening`），
因仓库无真实标注图片，改为使用公开数据集 **Riseholme-2021**
（github.com/ctyeong/Riseholme-2021，林肯大学，3520 张）。

### 类别定义（数据集原始标注）

| 类别 | 含义 |
| --- | --- |
| `Ripe` | 成熟果实 |
| `Unripe` | 未成熟果实 |
| `Occluded` | 被遮挡的果实 |
| `Anomalous` | 异常果实（病虫害/畸形） |

### 技术路线（相较原计划变更）

原计划 ImageNet 预训练 ResNet18 + PyTorch。因开发环境 torch 与 numpy 2 不兼容、
ImageNet 权重下载不可用，改为 **纯 NumPy + Pillow 轻量 MLP**：

- 架构：48x48 RGB 展平（6912）→ 512 → 128 → 4，ReLU + Dropout(0.3) + softmax
- 优化：Adam（lr 5e-4，20 epoch），频率倒数加权交叉熵（Unripe 占 68%）
- 增强：水平翻转 / 平移 ±2px / 亮度 ±20%（向量化实现）
- 推理：`app/main.py` 同步实现同一 MLP 前向，无 torch 依赖

### 数据划分（如实记录）

- `Ripe/Unripe/Occluded`：官方 `Splits/Split1-RUO-*` 划分
- `Anomalous`：官方仅测试（one-class），本项目多分类需训练样本，按 70/15/15
  随机划分（seed=42），见 `datasets/riseholme-classification/meta.json`

## 训练结果

测试集准确率 **0.8123**，宏平均 F1 **0.5202**；Ripe/Unripe 良好（≈0.95），
Occluded/Anomalous 偏弱（0.27 / 0.00），详见 `docs/day06-ai-inference.md`。
结果为真实水平，不虚构。

## 复用/扩展

- 重新训练：`python services/ai/train_numpy.py --data-dir datasets/riseholme-classification ...`
- 换新数据集：整理为 `train|val|test/<class>/` 标准目录即可直接训练（类别自动发现）
- 每类训练图不足 50 张、验证不足 10 张时脚本中止并提示

## API 状态

`POST /api/v1/predict` 契约见 `docs/day06-ai-inference.md`。权重存在时正常推理；
权重缺失时返回 HTTP 501 和 `status=not_ready`，这是预期状态。
