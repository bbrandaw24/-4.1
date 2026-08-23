# Day 6 AI 推理接口

## 当前状态

接口契约已固定，模型权重尚未生成。服务会明确返回 `501 not_ready`，不会把占位结果写入业务数据。

## 查询模型状态

```http
GET /api/v1/model/status
```

返回模型是否就绪、版本、类别和置信度阈值，供 Web 看板展示。

## 图像推理

```http
POST /api/v1/predict
Content-Type: multipart/form-data
```

字段：`file`（必填，JPEG/PNG，默认最大 5 MiB）、`device_id`（可选）、`image_id`（可选）。模型就绪后返回四类 softmax 概率、标签、置信度和版本；低于 0.60 时标签为 `uncertain`。
