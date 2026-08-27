"""AI 推理服务(纯 NumPy 后端,Day 6)。

权重文件(AI_MODEL_PATH)存在时自动加载并启用真实推理;
权重不存在时保持 not_ready / 501 占位,绝不伪造推理结果。

训练管线见 train_numpy.py:
    python prepare_riseholme.py --riseholme-dir ../datasets/train/Riseholme-2021-main \
        --output-dir ../datasets/riseholme-classification
    python train_numpy.py --data-dir ../datasets/riseholme-classification \
        --output models/riseholme_mlp_v1.npz --model-version riseholme-2021-mlp-v1

推理实现与训练脚本保持一致:48x48 RGB -> 展平 -> MLP(6912-512-128-4) -> softmax。
"""

import io
import json
import os
import time

import numpy as np
from flask import Flask, jsonify, request
from PIL import Image, UnidentifiedImageError

app = Flask(__name__)

MODEL_VERSION = os.getenv("AI_MODEL_VERSION", "riseholme-2021-mlp-v1")
CONFIDENCE_THRESHOLD = float(os.getenv("AI_CONFIDENCE_THRESHOLD", "0.60"))
DEFAULT_CLASSES = ["Anomalous", "Occluded", "Ripe", "Unripe"]

MODEL_PATH = os.getenv("AI_MODEL_PATH", "models/riseholme_mlp_v1.npz")
LABELS_PATH = os.getenv("AI_LABELS_PATH", "models/labels.json")
MAX_UPLOAD_BYTES = int(os.getenv("AI_MAX_UPLOAD_BYTES", str(5 * 1024 * 1024)))
ALLOWED_EXTENSIONS = {"jpg", "jpeg", "png"}

INPUT_SIZE = 48  # 与 train_numpy.py 保持一致

_model = None
_classes = list(DEFAULT_CLASSES)
_load_error = None


def load_model():
    """加载 .npz 权重;不存在或损坏时保持 _model=None。"""
    global _model, _classes, _load_error
    if _model is not None or _load_error is not None:
        return _model

    if os.path.exists(LABELS_PATH):
        try:
            with open(LABELS_PATH, encoding="utf-8") as f:
                _classes = json.load(f)["classes"]
        except (OSError, KeyError, json.JSONDecodeError) as exc:
            app.logger.warning("labels.json 读取失败,使用内置类别: %s", exc)

    if not os.path.exists(MODEL_PATH):
        _load_error = f"模型权重不存在: {MODEL_PATH},请先运行 train_numpy.py 训练。"
        app.logger.warning(_load_error)
        return None
    try:
        data = np.load(MODEL_PATH, allow_pickle=True)
        params = [data["W1"].astype(np.float32), data["b1"].astype(np.float32),
                  data["W2"].astype(np.float32), data["b2"].astype(np.float32),
                  data["W3"].astype(np.float32), data["b3"].astype(np.float32)]
        saved_classes = data["classes"]
        if saved_classes.size == len(_classes):
            _classes = [str(c) for c in saved_classes.tolist()]
        _model = params
        app.logger.info("模型已加载: %s (类别 %d, 层 %d-%s-%s-%d)",
                        MODEL_PATH, len(_classes), params[0].shape[1],
                        params[0].shape[0], params[2].shape[0], params[4].shape[0])
    except Exception as exc:
        _load_error = f"模型加载失败: {exc}"
        app.logger.error(_load_error, exc_info=True)
        _model = None
    return _model


def is_ready():
    return load_model() is not None


def relu(x):
    return np.maximum(x, 0.0)


def softmax(z):
    z = z - z.max(axis=1, keepdims=True)
    e = np.exp(z)
    return e / e.sum(axis=1, keepdims=True)


def predict_proba(rgb_array):
    """48x48x3 -> 类别概率数组(与 train_numpy.forward 推理路径一致)。"""
    params = _model
    x = rgb_array.reshape(1, -1).astype(np.float32) / 255.0
    W1, b1, W2, b2, W3, b3 = params
    z1 = x @ W1.T + b1
    a1 = relu(z1)
    z2 = a1 @ W2.T + b2
    a2 = relu(z2)
    z3 = a2 @ W3.T + b3
    return softmax(z3)[0]


@app.after_request
def add_cors_headers(response):
    origin = os.getenv("CORS_ORIGIN", "*")
    response.headers["Access-Control-Allow-Origin"] = origin
    response.headers["Access-Control-Allow-Headers"] = "Content-Type"
    response.headers["Access-Control-Allow-Methods"] = "GET, POST, OPTIONS"
    if request.method == "OPTIONS":
        response.status_code = 204
    return response


@app.get("/healthz")
def healthz():
    return jsonify({"status": "ok", "service": "ai"})


@app.get("/api/v1/model/status")
def model_status():
    """暴露真实就绪状态:权重加载成功才置 ready=True。"""
    ready = is_ready()
    return jsonify({
        "status": "ready" if ready else "not_ready",
        "ready": ready,
        "model_version": MODEL_VERSION,
        "classes": _classes,
        "confidence_threshold": CONFIDENCE_THRESHOLD,
        "model_path": MODEL_PATH if ready else None,
        "message": None if ready else (_load_error or "模型尚未就绪。"),
    })


@app.post("/api/v1/predict")
def predict():
    """图像推理:返回各类概率;最高概率低于阈值时标签为 uncertain。"""
    if not is_ready():
        return jsonify({
            "status": "not_ready",
            "message": _load_error or "模型尚未就绪,请先运行 train_numpy.py 生成权重。",
        }), 501

    file = request.files.get("file")
    if file is None or not file.filename:
        return jsonify({"status": "error", "message": "缺少图片字段 file。"}), 400

    ext = file.filename.rsplit(".", 1)[-1].lower() if "." in file.filename else ""
    if ext not in ALLOWED_EXTENSIONS:
        return jsonify({"status": "error", "message": f"仅支持 JPEG/PNG 图片,收到 .{ext or '?'}"}), 415

    data = file.read(MAX_UPLOAD_BYTES + 1)
    if len(data) > MAX_UPLOAD_BYTES:
        return jsonify({"status": "error", "message": "图片超过 5 MiB 上限。"}), 413

    try:
        image = Image.open(io.BytesIO(data))
        image.load()
        image = image.convert("RGB")
        image = image.resize((INPUT_SIZE, INPUT_SIZE), Image.BILINEAR)
        rgb = np.asarray(image, dtype=np.float32)
    except (UnidentifiedImageError, OSError, ValueError):
        return jsonify({"status": "error", "message": "无法解析图片内容。"}), 415

    start = time.perf_counter()
    probs = predict_proba(rgb)
    latency_ms = round((time.perf_counter() - start) * 1000, 1)

    probabilities = {c: round(float(p), 4) for c, p in zip(_classes, probs)}
    top_idx = int(np.argmax(probs))
    confidence = float(probs[top_idx])
    predicted = _classes[top_idx] if confidence >= CONFIDENCE_THRESHOLD else None

    return jsonify({
        "status": "ok" if predicted else "uncertain",
        "predicted_class": predicted,
        "confidence": round(confidence, 4),
        "model_version": MODEL_VERSION,
        "classes": _classes,
        "probabilities": probabilities,
        "latency_ms": latency_ms,
    })
