# Day 5 AI 图像识别基础

## 目标与边界

本阶段识别的是草莓主生长阶段，任务类型是**单标签图像分类**，不是目标检测。每张图片只保留一个主阶段标签：萌芽、开花、坐果或成熟。

当前仓库完成了数据规范和训练输入契约，但没有提交未经授权的真实农场图片，也没有虚构训练指标。收集并审核标注图片后，才能运行训练并产生模型权重。

## 类别定义

| 类别 ID | 标签 | 判定规则 |
| --- | --- | --- |
| `germination` | 萌芽 | 可见新芽或幼叶，尚未出现开放花朵 |
| `flowering` | 开花 | 可见开放花朵或明显花蕾，尚未形成清晰果实 |
| `fruit_set` | 坐果 | 花后已形成幼果，果实仍为小型绿色或浅色 |
| `ripening` | 成熟 | 果实已膨大并出现成熟色泽，达到采收判断阶段 |

标签不确定、多个阶段无法确定主阶段、严重遮挡或图片质量不足时，放入 `reject`，不进入训练集。

## 数据集布局

```text
datasets/strawberry-growth/
  labels.json
  README.md
  train/{germination,flowering,fruit_set,ripening}/
  val/{germination,flowering,fruit_set,ripening}/
  test/{germination,flowering,fruit_set,ripening}/
  reject/
```

建议按植株或采集日期分组后再划分数据集，避免同一植株的近似连续帧同时出现在训练和测试中。初始比例为 train 70%、val 15%、test 15%。

## 标注工具与质量门槛

- 分类任务可直接使用文件夹分类；需要记录来源和审核信息时使用 Label Studio 或自定义 CSV。
- 图片建议保留原始 JPEG/PNG，训练阶段统一缩放到 `224 x 224`。
- 每类至少 200 张审核通过的图片后再进行第一轮训练；四类数量差异不超过 2:1。
- 标注记录应包含 `image_id`、`label`、`source`、`captured_at`、`annotator`、`review_status`。
- 验证集和测试集只允许使用审核通过且来源分组隔离的图片。

## 训练契约

- 基线模型：ImageNet 预训练 ResNet18，替换最后全连接层为 4 类。
- 输入：RGB 图片，`224 x 224`，训练阶段使用水平翻转、轻微旋转、亮度/对比度扰动。
- 输出：四类 softmax 概率、`label`、`confidence` 和模型版本。
- 训练报告必须记录数据集版本、类别分布、随机种子、学习率、batch size、epoch、准确率、宏平均 F1 和混淆矩阵。
- 置信度低于 0.60 时返回 `uncertain`，不得强行展示阶段结论。

## API 状态

`POST /api/v1/predict` 的正式请求/响应契约见 `docs/day06-ai-inference.md`。在模型文件生成前，服务返回 HTTP 501 和 `status=not_ready`，这是预期状态。
