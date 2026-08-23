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
MQTT_CLIENT_ID = os.getenv("MQTT_CLIENT_ID", "smart-agriculture-api")
UPLOAD_DIR = Path(os.getenv("UPLOAD_DIR", "/data/uploads"))
MAX_UPLOAD_BYTES = int(os.getenv("MAX_UPLOAD_BYTES", str(5 * 1024 * 1024)))
registry = {}
registry_lock = Lock()
PUMP_CONFIRM_TIMEOUT_SECONDS = float(os.getenv("PUMP_CONFIRM_TIMEOUT_SECONDS", "5"))
HISTORY_LIMIT = int(os.getenv("TELEMETRY_HISTORY_LIMIT", "7200"))
pending_commands = {}
image_registry = {}
image_registry_lock = Lock()
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)


def utc_now():
    return datetime.now(timezone.utc).isoformat()


def _parse_timestamp(value):
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (AttributeError, TypeError, ValueError):
        return None


def _pump_snapshot(device_id):
    with registry_lock:
        device = registry.get(device_id)
        pump_state = dict((device or {}).get("pump") or {
            "action": "stop", "running": False, "status": "standby",
            "timestamp": None, "command_id": None,
        })
        command = pending_commands.get(device_id)
        if command and command["status"] == "pending":
            requested = _parse_timestamp(command["requested_at"])
            if requested and (datetime.now(timezone.utc) - requested).total_seconds() > PUMP_CONFIRM_TIMEOUT_SECONDS:
                command["status"] = "timeout"
                if pump_state.get("status") == "pending":
                    pump_state["status"] = "timeout"
        return {"device_id": device_id, "pump": pump_state, "command": dict(command) if command else None}


def on_mqtt_message(_client, _userdata, message):
    """Validate sensor and actuator envelopes and update the latest snapshot."""
    parts = message.topic.split("/")
    if len(parts) != 4 or parts[0] != "farm" or parts[1] == "":
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
        device = registry.setdefault(device_id, {"device_id": device_id, "telemetry": {}, "last_seen": None,
                                                 "pump": {"action": "stop", "running": False, "status": "standby",
                                                          "timestamp": None, "command_id": None}})
        if parts[2] == "sensor":
            device["last_seen"] = timestamp
            device["telemetry"][parts[3]] = {"timestamp": timestamp, "payload": payload}
            history = device.setdefault("history", [])
            history.append({"timestamp": timestamp, "kind": parts[3], "payload": payload})
            if len(history) > HISTORY_LIMIT * 2:
                del history[:-HISTORY_LIMIT * 2]
            return
        if parts[2] != "status" or parts[3] != "pump":
            return
        command_id = envelope.get("command_id") or payload.get("command_id")
        device["pump"] = {
            "action": payload.get("action"),
            "running": bool(payload.get("running")),
            "status": "running" if payload.get("running") else "standby",
            "timestamp": timestamp,
            "command_id": command_id,
        }
        command = pending_commands.get(device_id)
        if command and (not command_id or command["command_id"] == command_id):
            command["status"] = "confirmed"
            command["confirmed_at"] = timestamp
            sent = _parse_timestamp(command["requested_at"])
            received = _parse_timestamp(timestamp)
            command["latency_ms"] = round(max(0, (received - sent).total_seconds() * 1000), 1) if sent and received else None


def mqtt_listener():
    client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id=MQTT_CLIENT_ID)
    client.on_message = on_mqtt_message
    try:
        client.connect(MQTT_HOST, MQTT_PORT, keepalive=60)
        client.subscribe("farm/+/sensor/+", qos=1)
        client.subscribe("farm/+/status/pump", qos=1)
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


@app.get("/api/v1/devices/<device_id>/pump")
def pump_status(device_id):
    return jsonify(_pump_snapshot(device_id))


@app.get("/api/v1/devices/<device_id>/telemetry/history")
def telemetry_history(device_id):
    try:
        hours = min(max(float(request.args.get("hours", "10")), 0.25), 24)
    except ValueError:
        return jsonify({"error": "hours_must_be_number"}), 400
    cutoff = datetime.now(timezone.utc).timestamp() - hours * 3600
    with registry_lock:
        device = registry.get(device_id)
        history = list((device or {}).get("history", []))
    items = []
    for item in history:
        parsed = _parse_timestamp(item.get("timestamp"))
        if parsed and parsed.timestamp() >= cutoff:
            items.append(item)
    return jsonify({"device_id": device_id, "hours": hours, "count": len(items), "items": items})


@app.get("/api/v1/devices/<device_id>/alerts")
def device_alerts(device_id):
    with registry_lock:
        device = registry.get(device_id)
        if device is None:
            return jsonify({"device_id": device_id, "items": [], "count": 0})
        soil = device.get("telemetry", {}).get("soil", {}).get("payload", {})
        climate = device.get("telemetry", {}).get("climate", {}).get("payload", {})
    items = []
    moisture = soil.get("moisture_pct")
    temperature = climate.get("air_temperature_c")
    if isinstance(moisture, (int, float)) and moisture < 40:
        items.append({"level": "warning", "code": "low_moisture", "message": f"土壤湿度 {moisture:.1f}% 低于 40%，建议灌溉"})
    if isinstance(temperature, (int, float)) and temperature > 30:
        items.append({"level": "warning", "code": "high_temperature", "message": f"空气温度 {temperature:.1f}°C 偏高，请检查通风"})
    return jsonify({"device_id": device_id, "items": items, "count": len(items)})


@app.post("/api/v1/devices/<device_id>/pump")
def pump(device_id):
    action = (request.get_json(silent=True) or {}).get("action")
    if action not in {"start", "stop"}:
        return jsonify({"error": "action_must_be_start_or_stop"}), 400
    with registry_lock:
        previous = pending_commands.get(device_id)
        if previous and previous["status"] == "pending" and previous["action"] == action:
            return jsonify({"error": "same_command_pending", "command": previous}), 409
    command_id = uuid4().hex
    requested_at = utc_now()
    command = {"command_id": command_id, "device_id": device_id, "action": action,
               "status": "pending", "requested_at": requested_at, "confirmed_at": None, "latency_ms": None}
    with registry_lock:
        pending_commands[device_id] = command
        device = registry.setdefault(device_id, {"device_id": device_id, "telemetry": {}, "last_seen": None})
        device["pump"] = {"action": action, "running": action == "start", "status": "pending",
                           "timestamp": requested_at, "command_id": command_id}
    client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id=f"smart-agriculture-api-control-{command_id[:8]}")
    try:
        client.connect(MQTT_HOST, MQTT_PORT, keepalive=60)
        client.loop_start()
        info = client.publish(
            f"farm/{device_id}/control/pump",
            json.dumps({"device_id": device_id, "command_id": command_id, "timestamp": requested_at,
                        "payload": {"action": action, "command_id": command_id}}),
            qos=1,
        )
        info.wait_for_publish(timeout=5)
        client.loop_stop()
        client.disconnect()
    except Exception as exc:
        LOGGER.warning("pump command failed: %s", exc)
        with registry_lock:
            command["status"] = "failed"
        return jsonify({"error": "mqtt_unavailable"}), 503
    return jsonify({"device_id": device_id, "action": action, "status": "pending", "command_id": command_id,
                    "requested_at": requested_at, "confirm_timeout_seconds": PUMP_CONFIRM_TIMEOUT_SECONDS}), 202


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
