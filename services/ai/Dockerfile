FROM python:3.11-slim
WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app
COPY train_numpy.py prepare_riseholme.py ./
RUN mkdir -p /models

ENV AI_MODEL_PATH=/models/riseholme_mlp_v1.npz \
    AI_LABELS_PATH=/models/labels.json

EXPOSE 8001
CMD ["gunicorn", "--bind", "0.0.0.0:8001", "--workers", "1", "app.main:app"]
