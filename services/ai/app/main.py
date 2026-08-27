"""AI 推理服务：草莓成熟度/状态识别。

推理内核：TorchScript（strawberry-resnet50-tl，resnet50 全量微调，test 87.7%）。
预处理与训练一致：Resize(224) -> ToTensor -> ImageNet Normalize。
端点契约与历史版本兼容：/api/v1/model/status、/api/v1/predict 结构保持不变。
权重文件(AI_MODEL_PATH)存在时自动加载并启用真实推理；不存在时保持
not_ready / 501 占位，绝不伪造推理结果。
"""

import io
import json
import os
import time

import numpy as np
import torch
from flask import Flask, jsonify, request
from PIL import Image, UnidentifiedImageError
from torchvision import transforms

app = Flask(__name__)

MODEL_VERSION = os.getenv("AI_MODEL_VERSION", "strawberry-resnet50-tl-v1")
CONFIDENCE_THRESHOLD = float(os.getenv("AI_CONFIDENCE_THRESHOLD", "0.60"))
DEFAULT_CLASSES = ["anomalous", "occluded", "ripe", "unripe"]
CLASS_LABELS_ZH = {
    "anomalous": "异常果",
    "occluded": "遮挡",
    "ripe": "成熟",
    "unripe": "未成熟",
}

MODEL_PATH = os.getenv("AI_MODEL_PATH", "models/strawberry_resnet50_tl.ts")
LABELS_PATH = os.getenv("AI_LABELS_PATH", "models/classes.json")
MAX_UPLOAD_BYTES = int(os.getenv("AI_MAX_UPLOAD_BYTES", str(5 * 1024 * 1024)))
ALLOWED_EXTENSIONS = {"jpg", "jpeg", "png"}

IMAGENET_MEAN = [0.485, 0.456, 0.406]
IMAGENET_STD = [0.229, 0.224, 0.225]

_model = None
_classes = list(DEFAULT_CLASSES)
_input_size = 224
_load_error = None


def load_model():
    """Lazy-load TorchScript model. Returns the model or None."""
    global _model, _classes, _input_size, _load_error
    if _model is not None or _load_error is not None:
        return _model

    if os.path.exists(LABELS_PATH):
        try:
            with open(LABELS_PATH, encoding="utf-8") as f:
                meta = json.load(f)
                if isinstance(meta.get("classes"), list) and meta["classes"]:
                    _classes = [str(c) for c in meta["classes"]]
                if isinstance(meta.get("input_size"), list) and len(meta["input_size"]) == 2:
                    _input_size = int(meta["input_size"][0])
        except (OSError, KeyError, json.JSONDecodeError) as exc:
            app.logger.warning("classes.json 读取失败，使用内置类别: %s", exc)

    if not os.path.exists(MODEL_PATH):
        _load_error = f"模型权重不存在: {MODEL_PATH}，请先部署 TorchScript 模型。"
        app.logger.warning(_load_error)
        return None
    try:
        model = torch.jit.load(MODEL_PATH, map_location="cpu")
        model.eval()
        _model = model
        app.logger.info("模型已加载: %s (类别 %d, 输入 %dx%d)",
                        MODEL_PATH, len(_classes), _input_size, _input_size)
    except Exception as exc:
        _load_error = f"模型加载失败: {exc}"
        app.logger.error(_load_error, exc_info=True)
        _model = None
    return _model


def is_ready():
    return load_model() is not None


def predict_proba(pil_image):
    """PIL RGB 图 -> 类别概率数组（与训练预处理一致）。"""
    tf = transforms.Compose([
        transforms.Resize((_input_size, _input_size)),
        transforms.ToTensor(),
        transforms.Normalize(IMAGENET_MEAN, IMAGENET_STD),
    ])
    tensor = tf(pil_image.convert("RGB")).unsqueeze(0)
    with torch.no_grad():
        logits = _model(tensor)
        probs = torch.softmax(logits, dim=1)[0].numpy()
    return probs


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
        "class_labels": {c: CLASS_LABELS_ZH.get(c, c) for c in _classes},
        "confidence_threshold": CONFIDENCE_THRESHOLD,
        "input_size": _input_size,
        "model_path": MODEL_PATH if ready else None,
        "message": None if ready else (_load_error or "模型尚未就绪。"),
    })


@app.post("/api/v1/predict")
def predict():
    """图像推理:返回各类概率;最高概率低于阈值时 predicted_class 为 None。"""
    if not is_ready():
        return jsonify({
            "status": "not_ready",
            "message": _load_error or "模型尚未就绪，请先部署 TorchScript 模型。",
        }), 501

    file = request.files.get("file")
    if file is None or not file.filename:
        return jsonify({"status": "error", "message": "缺少图片字段 file。"}), 400

    ext = file.filename.rsplit(".", 1)[-1].lower() if "." in file.filename else ""
    if ext not in ALLOWED_EXTENSIONS:
        return jsonify({"status": "error", "message": f"仅支持 JPEG/PNG 图片，收到 .{ext or '?'}"}), 415

    data = file.read(MAX_UPLOAD_BYTES + 1)
    if len(data) > MAX_UPLOAD_BYTES:
        return jsonify({"status": "error", "message": "图片超过 5 MiB 上限。"}), 413

    try:
        image = Image.open(io.BytesIO(data))
        image.load()
    except (UnidentifiedImageError, OSError, ValueError):
        return jsonify({"status": "error", "message": "无法解析图片内容。"}), 415

    try:
        start = time.perf_counter()
        probs = predict_proba(image)
        latency_ms = round((time.perf_counter() - start) * 1000, 1)
    except Exception as exc:
        app.logger.error("inference failed: %s", exc, exc_info=True)
        return jsonify({"status": "error", "message": f"推理失败: {exc}"}), 500

    probabilities = {c: round(float(p), 4) for c, p in zip(_classes, probs)}
    top_idx = int(np.argmax(probs))
    confidence = float(probs[top_idx])
    predicted = _classes[top_idx] if confidence >= CONFIDENCE_THRESHOLD else None

    return jsonify({
        "status": "ok" if predicted else "uncertain",
        "predicted_class": predicted,
        "predicted_label": CLASS_LABELS_ZH.get(predicted, predicted) if predicted else None,
        "confidence": round(confidence, 4),
        "model_version": MODEL_VERSION,
        "classes": _classes,
        "probabilities": probabilities,
        "latency_ms": latency_ms,
    })
