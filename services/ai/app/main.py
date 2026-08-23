from flask import Flask, jsonify

app = Flask(__name__)


@app.get("/healthz")
def healthz():
    return jsonify({"status": "ok", "service": "ai"})


@app.post("/api/v1/predict")
def predict():
    return jsonify({
        "status": "not_ready",
        "message": "AI model integration is scheduled for Day 5-6.",
    }), 501
