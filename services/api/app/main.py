from datetime import datetime, timezone
from io import BytesIO
import json
import logging
import os
from pathlib import Path
from threading import Lock, Thread
from uuid import uuid4

from flask import Flask, jsonify, request, send_file
from PIL import Image, UnidentifiedImageError
import paho.mqtt.client as mqtt

app = Flask(__name__)
logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"))
LOGGER = logging.getLogger("smart-agriculture-api")


@app.after_request
def add_cors_headers(response):
    """Allow the read-only dashboard and local development hosts to call the API."""
    response.headers["Access-Control-Allow-Origin"] = os.getenv("CORS_ORIGIN", "*")
    response.headers["Access-Control-Allow-Headers"] = "Content-Type"
    response.headers["Access-Control-Allow-Methods"] = "GET, POST, OPTIONS"
    return response

MQTT_HOST = os.getenv("MQTT_HOST", "localhost")
MQTT_PORT = int(os.getenv("MQTT_PORT", "1883"))
UPLOAD_DIR = Path(os.getenv("UPLOAD_DIR", "/data/uploads"))
MAX_UPLOAD_BYTES = int(os.getenv("MAX_UPLOAD_BYTES", str(5 * 1024 * 1024)))
registry = {}
registry_lock = Lock()
image_registry = {}
image_registry_lock = Lock()
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)


def utc_now():
    return datetime.now(timezone.utc).isoformat()


def on_mqtt_message(_client, _userdata, message):
    """Validate sensor envelopes and update the latest device snapshot."""
    parts = message.topic.split("/")
    if len(parts) != 4 or parts[0] != "farm" or parts[2] != "sensor":
        return
    try:
        envelope = json.loads(message.payload.decode("utf-8"))
        device_id = envelope["device_id"]
        payload = envelope["payload"]
        timestamp = envelope["timestamp"]
        if not isinstance(device_id, str) or not isinstance(payload, dict):
            raise ValueError("invalid envelope types")
    except (UnicodeDecodeError, json.JSONDecodeError, KeyError, TypeError, ValueError) as exc:
        LOGGER.warning("ignored invalid MQTT message on %s: %s", message.topic, exc)
        return
    with registry_lock:
        device = registry.setdefault(device_id, {"device_id": device_id, "telemetry": {}, "last_seen": None})
        device["last_seen"] = timestamp
        device["telemetry"][parts[3]] = {"timestamp": timestamp, "payload": payload}


def mqtt_listener():
    client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id="smart-agriculture-api")
    client.on_message = on_mqtt_message
    try:
        client.connect(MQTT_HOST, MQTT_PORT, keepalive=60)
        client.subscribe("farm/+/sensor/+", qos=1)
        LOGGER.info("MQTT listener connected to %s:%s", MQTT_HOST, MQTT_PORT)
        client.loop_forever()
    except Exception as exc:  # API remains available when broker is temporarily offline.
        LOGGER.warning("MQTT listener unavailable: %s", exc)


if os.getenv("MQTT_LISTENER_ENABLED", "true").lower() == "true":
    Thread(target=mqtt_listener, name="mqtt-listener", daemon=True).start()


@app.get("/healthz")
def healthz():
    return jsonify({"status": "ok", "service": "api"})


@app.get("/api/v1/system/status")
def system_status():
    return jsonify({
        "service": "smart-agriculture-api",
        "status": "ready",
        "mqtt": {"host": MQTT_HOST, "port": MQTT_PORT},
        "timestamp": datetime.now(timezone.utc).isoformat(),
    })


@app.get("/api/v1/devices")
def devices():
    with registry_lock:
        return jsonify({"items": list(registry.values()), "count": len(registry)})


@app.get("/api/v1/devices/<device_id>/telemetry/latest")
def latest_telemetry(device_id):
    with registry_lock:
        device = registry.get(device_id)
        if device is None:
            return jsonify({"error": "device_not_found", "device_id": device_id}), 404
        return jsonify(device)


@app.post("/api/v1/devices/<device_id>/pump")
def pump(device_id):
    action = (request.get_json(silent=True) or {}).get("action")
    if action not in {"start", "stop"}:
        return jsonify({"error": "action_must_be_start_or_stop"}), 400
    client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id="smart-agriculture-api-control")
    try:
        client.connect(MQTT_HOST, MQTT_PORT, keepalive=60)
        client.loop_start()
        info = client.publish(
            f"farm/{device_id}/control/pump",
            json.dumps({"device_id": device_id, "timestamp": utc_now(), "payload": {"action": action}}),
            qos=1,
        )
        info.wait_for_publish(timeout=5)
        client.loop_stop()
        client.disconnect()
    except Exception as exc:
        LOGGER.warning("pump command failed: %s", exc)
        return jsonify({"error": "mqtt_unavailable"}), 503
    return jsonify({"device_id": device_id, "action": action, "status": "published"}), 202


def _image_record(image_id):
    with image_registry_lock:
        return image_registry.get(image_id)


@app.post("/api/v1/images")
def upload_image():
    upload = request.files.get("file")
    if upload is None or not upload.filename:
        return jsonify({"error": "file_field_required"}), 400
    if request.content_length and request.content_length > MAX_UPLOAD_BYTES + 1024 * 256:
        return jsonify({"error": "file_too_large", "max_bytes": MAX_UPLOAD_BYTES}), 413
    data = upload.stream.read(MAX_UPLOAD_BYTES + 1)
    if len(data) > MAX_UPLOAD_BYTES:
        return jsonify({"error": "file_too_large", "max_bytes": MAX_UPLOAD_BYTES}), 413
    if not data:
        return jsonify({"error": "empty_file"}), 400
    try:
        with Image.open(BytesIO(data)) as source:
            source.verify()
        with Image.open(BytesIO(data)) as source:
            image = source.convert("RGB")
            width, height = image.size
            image_id = uuid4().hex
            image_path = UPLOAD_DIR / f"{image_id}.jpg"
            thumb_path = UPLOAD_DIR / f"{image_id}_thumb.jpg"
            image.save(image_path, format="JPEG", quality=90, optimize=True)
            thumbnail = image.copy()
            thumbnail.thumbnail((512, 512))
            thumbnail.save(thumb_path, format="JPEG", quality=85, optimize=True)
    except (UnidentifiedImageError, OSError, ValueError) as exc:
        LOGGER.info("rejected invalid image %s: %s", upload.filename, exc)
        return jsonify({"error": "invalid_image"}), 415
    record = {
        "image_id": image_id,
        "device_id": request.form.get("device_id"),
        "width": width,
        "height": height,
        "content_type": "image/jpeg",
        "size_bytes": image_path.stat().st_size,
        "file_url": f"/api/v1/images/{image_id}/file",
        "thumbnail_url": f"/api/v1/images/{image_id}/thumbnail",
        "created_at": utc_now(),
    }
    with image_registry_lock:
        image_registry[image_id] = record
    return jsonify(record), 201


@app.get("/api/v1/images/<image_id>")
def image_metadata(image_id):
    record = _image_record(image_id)
    if record is None:
        return jsonify({"error": "image_not_found", "image_id": image_id}), 404
    return jsonify(record)


@app.get("/api/v1/images/<image_id>/file")
def image_file(image_id):
    record = _image_record(image_id)
    if record is None:
        return jsonify({"error": "image_not_found", "image_id": image_id}), 404
    return send_file(UPLOAD_DIR / f"{image_id}.jpg", mimetype="image/jpeg", max_age=3600)


@app.get("/api/v1/images/<image_id>/thumbnail")
def image_thumbnail(image_id):
    record = _image_record(image_id)
    if record is None:
        return jsonify({"error": "image_not_found", "image_id": image_id}), 404
    return send_file(UPLOAD_DIR / f"{image_id}_thumb.jpg", mimetype="image/jpeg", max_age=3600)
