from datetime import datetime, timezone

from flask import Flask, jsonify

app = Flask(__name__)


@app.get("/healthz")
def healthz():
    return jsonify({"status": "ok", "service": "api"})


@app.get("/api/v1/system/status")
def system_status():
    return jsonify({
        "service": "smart-agriculture-api",
        "status": "scaffold",
        "timestamp": datetime.now(timezone.utc).isoformat(),
    })
