import os

from flask import Flask, jsonify

app = Flask(__name__)

MODEL_VERSION = os.getenv("AI_MODEL_VERSION", "strawberry-resnet18-v1")
CONFIDENCE_THRESHOLD = float(os.getenv("AI_CONFIDENCE_THRESHOLD", "0.60"))
CLASSES = ["germination", "flowering", "fruit_set", "ripening"]


@app.get("/healthz")
def healthz():
    return jsonify({"status": "ok", "service": "ai"})


@app.get("/api/v1/model/status")
def model_status():
    """Expose readiness without pretending that weights exist."""
    return jsonify({
        "status": "not_ready",
        "ready": False,
        "model_version": MODEL_VERSION,
        "classes": CLASSES,
        "confidence_threshold": CONFIDENCE_THRESHOLD,
        "message": "训练数据审核和模型权重尚未完成。",
    })


@app.post("/api/v1/predict")
def predict():
    return jsonify({
        "status": "not_ready",
        "message": "AI model integration is scheduled for Day 5-6.",
    }), 501
