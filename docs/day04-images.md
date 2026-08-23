# Day 4 - 图片上传与存储

## 接口

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| `POST` | `/api/v1/images` | multipart 字段 `file` 上传 JPEG/PNG，可选 `device_id` |
| `GET` | `/api/v1/images/{image_id}` | 查询图片元数据 |
| `GET` | `/api/v1/images/{image_id}/file` | 获取标准化 JPEG |
| `GET` | `/api/v1/images/{image_id}/thumbnail` | 获取 512px 内缩略图 |

默认单文件上限为 5 MiB，可由 `MAX_UPLOAD_BYTES` 调整。服务会读取图片内容并用 Pillow 解码，拒绝伪造扩展名或无效图片；成功后统一保存为随机 ID 命名的 JPEG，避免路径穿越和原文件名泄露。

## 存储

容器内目录为 `/data/uploads`，Compose 映射到项目的 `data/uploads/`。当前元数据存储在 API 进程内存中，文件通过随机 ID 访问；第 6 天接入数据库后再持久化图片记录和 AI 结果。

## 验收命令

```bash
curl -X POST http://127.0.0.1:8000/api/v1/images \
  -F 'file=@sample.jpg' -F 'device_id=sim-greenhouse-001'
curl http://127.0.0.1:8000/api/v1/images/{image_id}
```

