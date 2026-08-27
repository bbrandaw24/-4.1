FROM python:3.11-slim
WORKDIR /app

COPY requirements.txt .
# 常规依赖走腾讯云镜像（容器内默认源 files.pythonhosted.org 国内超时）；
# torch/torchvision 单独从 PyTorch 官方 CPU index 拉取（--index-url 单源）
RUN pip install --no-cache-dir -r requirements.txt -i https://mirrors.tencent.com/pypi/simple \
 && pip install --no-cache-dir torch==2.2.2 torchvision==0.17.2 --index-url https://download.pytorch.org/whl/cpu

COPY app ./app
COPY models ./models

ENV AI_MODEL_PATH=/app/models/strawberry_resnet50.ts \
    AI_LABELS_PATH=/app/models/classes.json

EXPOSE 8001
CMD ["gunicorn", "--bind", "0.0.0.0:8001", "--workers", "1", "app.main:app"]
