FROM python:3.11-slim
WORKDIR /app

COPY requirements.txt .
# 默认源装常规依赖；torch/torchvision 从 PyTorch 官方 CPU index 拉取（体积小）
RUN pip install --no-cache-dir -r requirements.txt --extra-index-url https://download.pytorch.org/whl/cpu

COPY app ./app
COPY models ./models

ENV AI_MODEL_PATH=/app/models/strawberry_resnet50_tl.ts \
    AI_LABELS_PATH=/app/models/classes.json

EXPOSE 8001
CMD ["gunicorn", "--bind", "0.0.0.0:8001", "--workers", "1", "app.main:app"]
