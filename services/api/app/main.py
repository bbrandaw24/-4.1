from datetime import datetime, timezone
from io import BytesIO
import json
import logging
import os
import sqlite3
import time
from pathlib import Path
from threading import Lock, Thread
from uuid import uuid4

from flask import Flask, jsonify, request, send_file
from PIL import Image, UnidentifiedImageError
import paho.mqtt.client as mqtt

try:
    from .auth import init_db, register_auth_routes, require_auth
except ImportError:  # allow running main.py directly without the package context
    from auth import init_db, register_auth_routes, require_auth

app = Flask(__name__)
init_db()
register_auth_routes(app)
logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"))
LOGGER = logging.getLogger("smart-agriculture-api")


@app.after_request
def add_cors_headers(response):
    """Allow the dashboard and local development hosts to call the API."""
    response.headers["Access-Control-Allow-Origin"] = os.getenv("CORS_ORIGIN", "*")
    response.headers["Access-Control-Allow-Headers"] = "Content-Type, Authorization"
    response.headers["Access-Control-Allow-Methods"] = "GET, POST, PUT, OPTIONS"
    return response


@app.before_request
def handle_cors_preflight():
    """Return 204 for CORS preflight so cross-origin clients (e.g. GitHub Pages) succeed."""
    if request.method == "OPTIONS":
        return app.make_default_options_response()

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

# --- Persistent telemetry history (survives API restarts) -------------------
# The last ~10h of sensor samples are stored in SQLite under the /data volume so
# the trends view always reflects the cloud server's history, not data observed
# since a user logged in or since the last restart.
TELEMETRY_DB = os.getenv("TELEMETRY_DB", "/data/telemetry.db")
TELEMETRY_RETENTION_SECONDS = int(os.getenv("TELEMETRY_RETENTION_SECONDS", str(12 * 3600)))
_last_prune_at = {"at": 0.0}


def _telemetry_connect():
    conn = sqlite3.connect(TELEMETRY_DB, timeout=10)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=5000")
    return conn


def init_telemetry_db():
    Path(TELEMETRY_DB).parent.mkdir(parents=True, exist_ok=True)
    conn = _telemetry_connect()
    try:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS telemetry_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                device_id TEXT NOT NULL,
                kind TEXT NOT NULL,
                payload_json TEXT NOT NULL,
                ts TEXT NOT NULL,
                received_at TEXT NOT NULL
            )
            """
        )
        conn.execute("CREATE INDEX IF NOT EXISTS idx_th_device_ts ON telemetry_history(device_id, ts)")
        conn.commit()
    finally:
        conn.close()


def _prune_telemetry_if_due():
    now = time.time()
    if now - _last_prune_at["at"] < 60:
        return
    _last_prune_at["at"] = now
    try:
        cutoff = datetime.fromtimestamp(now - TELEMETRY_RETENTION_SECONDS, tz=timezone.utc).isoformat()
        conn = _telemetry_connect()
        try:
            conn.execute("DELETE FROM telemetry_history WHERE ts < ?", (cutoff,))
            conn.commit()
        finally:
            conn.close()
    except Exception as exc:  # never let pruning break the ingest path
        LOGGER.warning("telemetry prune failed: %s", exc)


def _store_telemetry(device_id, kind, payload, timestamp):
    conn = _telemetry_connect()
    try:
        conn.execute(
            "INSERT INTO telemetry_history (device_id, kind, payload_json, ts, received_at) VALUES (?,?,?,?,?)",
            (device_id, kind, json.dumps(payload, ensure_ascii=False), timestamp, utc_now()),
        )
        conn.commit()
    finally:
        conn.close()
    _prune_telemetry_if_due()


init_telemetry_db()

# --- Day 10: automatic irrigation rules -------------------------------------
IRRIGATION_RULE_LIMITS = {"min_pct": 5.0, "max_pct": 95.0}
DEFAULT_IRRIGATION_RULE = {
    "auto_enabled": False,
    "start_threshold_pct": 40.0,
    "stop_threshold_pct": 55.0,
    "cooldown_seconds": 60,
    "updated_at": None,
}
AUTO_EVAL_INTERVAL_SECONDS = float(os.getenv("IRRIGATION_RULE_INTERVAL", "5"))
irrigation_rules = {}
irrigation_events = []
irrigation_rules_lock = Lock()
last_auto_action_at = {}


def evaluate_irrigation_rule(rule, moisture, pump_running, pending_command):
    """Pure decision function: return "start", "stop" or None.

    Hysteresis: start below start threshold, stop at or above stop threshold.
    """
    if not rule.get("auto_enabled"):
        return None
    if pending_command:
        return None
    if not isinstance(moisture, (int, float)) or isinstance(moisture, bool):
        return None
    if pump_running:
        if moisture >= rule["stop_threshold_pct"]:
            return "stop"
        return None
    if moisture < rule["start_threshold_pct"]:
        return "start"
    return None


def _record_irrigation_event(device_id, action, rule, moisture):
    event = {
        "device_id": device_id,
        "action": action,
        "source": "auto",
        "trigger_moisture_pct": round(moisture, 2) if isinstance(moisture, (int, float)) else None,
        "start_threshold_pct": rule["start_threshold_pct"],
        "stop_threshold_pct": rule["stop_threshold_pct"],
        "timestamp": utc_now(),
    }
    with irrigation_rules_lock:
        irrigation_events.append(event)
        del irrigation_events[:-200]
    LOGGER.info("auto irrigation %s on %s (moisture %.1f%%)", action, device_id, moisture)
    return event


def _publish_pump_command(device_id, action, source="manual"):
    """Create a command record and publish it over MQTT. Returns (command, error_response)."""
    with registry_lock:
        previous = pending_commands.get(device_id)
        if previous and previous["status"] == "pending" and previous["action"] == action:
            return None, (jsonify({"error": "same_command_pending", "command": previous}), 409)
    command_id = uuid4().hex
    requested_at = utc_now()
    command = {"command_id": command_id, "device_id": device_id, "action": action, "source": source,
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
        return None, (jsonify({"error": "mqtt_unavailable"}), 503)
    return command, None


def evaluate_all_irrigation_rules(publish=None):
    """One evaluation pass over every auto-enabled device using latest soil moisture."""
    publish = publish or _publish_pump_command
    decisions = []
    now = time.time()
    with registry_lock:
        auto_device_ids = [device_id for device_id, rule in irrigation_rules.items() if rule.get("auto_enabled")]
        for device_id in auto_device_ids:
            device = registry.get(device_id)
            if not device or not device.get("last_seen"):
                continue
            moisture = (device.get("telemetry", {}).get("soil", {}).get("payload", {}) or {}).get("moisture_pct")
            pump_state = device.get("pump") or {}
            pump_running = bool(pump_state.get("running")) and pump_state.get("status") == "running"
            pending = pending_commands.get(device_id)
            rule = _get_irrigation_rule(device_id)
            cooldown = max(float(rule.get("cooldown_seconds") or 0), 0)
            last_action = last_auto_action_at.get(device_id, 0)
            if now - last_action < cooldown:
                continue
            action = evaluate_irrigation_rule(rule, moisture, pump_running, pending)
            if action:
                decisions.append((device_id, action, dict(rule), moisture))
    for device_id, action, rule, moisture in decisions:
        command, error = publish(device_id, action, source="auto")
        if error is None:
            last_auto_action_at[device_id] = time.time()
            event = _record_irrigation_event(device_id, action, rule, moisture)
            event["command_id"] = command["command_id"]
    return decisions


def irrigation_rule_loop():
    while True:
        try:
            evaluate_all_irrigation_rules()
        except Exception as exc:
            LOGGER.warning("irrigation rule pass failed: %s", exc)
        time.sleep(AUTO_EVAL_INTERVAL_SECONDS)


if os.getenv("IRRIGATION_RULES_ENABLED", "true").lower() == "true":
    Thread(target=irrigation_rule_loop, name="irrigation-rules", daemon=True).start()


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
            try:
                _store_telemetry(device_id, parts[3], payload, timestamp)
            except Exception as exc:
                LOGGER.warning("telemetry persist failed: %s", exc)
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
@require_auth()
def devices():
    with registry_lock:
        return jsonify({"items": list(registry.values()), "count": len(registry)})


@app.get("/api/v1/devices/<device_id>/telemetry/latest")
@require_auth()
def latest_telemetry(device_id):
    with registry_lock:
        device = registry.get(device_id)
        if device is None:
            return jsonify({"error": "device_not_found", "device_id": device_id}), 404
        return jsonify(device)


@app.get("/api/v1/devices/<device_id>/pump")
@require_auth()
def pump_status(device_id):
    return jsonify(_pump_snapshot(device_id))


@app.get("/api/v1/devices/<device_id>/telemetry/history")
@require_auth()
def telemetry_history(device_id):
    try:
        hours = min(max(float(request.args.get("hours", "10")), 0.25), 24)
    except ValueError:
        return jsonify({"error": "hours_must_be_number"}), 400
    cutoff = datetime.fromtimestamp(datetime.now(timezone.utc).timestamp() - hours * 3600, tz=timezone.utc).isoformat()
    items = []
    try:
        conn = _telemetry_connect()
        try:
            rows = conn.execute(
                "SELECT kind, payload_json, ts FROM telemetry_history "
                "WHERE device_id=? AND ts >= ? ORDER BY id DESC LIMIT ?",
                (device_id, cutoff, HISTORY_LIMIT * 2),
            ).fetchall()
        finally:
            conn.close()
        items = [
            {"timestamp": row[2], "kind": row[0], "payload": json.loads(row[1])}
            for row in reversed(rows)
        ]
    except Exception as exc:
        LOGGER.warning("history read failed: %s", exc)
    return jsonify({"device_id": device_id, "hours": hours, "count": len(items), "items": items})


@app.get("/api/v1/devices/<device_id>/alerts")
@require_auth()
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
@require_auth("control_pump")
def pump(device_id):
    action = (request.get_json(silent=True) or {}).get("action")
    if action not in {"start", "stop"}:
        return jsonify({"error": "action_must_be_start_or_stop"}), 400
    command, error = _publish_pump_command(device_id, action, source="manual")
    if error is not None:
        return error
    return jsonify({"device_id": device_id, "action": action, "status": "pending", "command_id": command["command_id"],
                    "requested_at": command["requested_at"], "confirm_timeout_seconds": PUMP_CONFIRM_TIMEOUT_SECONDS}), 202


def _get_irrigation_rule(device_id):
    """Return a merged copy of stored rule and defaults."""
    with irrigation_rules_lock:
        rule = dict(DEFAULT_IRRIGATION_RULE)
        rule.update(irrigation_rules.get(device_id, {}))
        rule["device_id"] = device_id
        return rule


@app.get("/api/v1/devices/<device_id>/irrigation-rules")
@require_auth()
def get_irrigation_rule(device_id):
    return jsonify(_get_irrigation_rule(device_id))


@app.put("/api/v1/devices/<device_id>/irrigation-rules")
@require_auth("manage_rules")
def put_irrigation_rule(device_id):
    body = request.get_json(silent=True)
    if not isinstance(body, dict):
        return jsonify({"error": "json_object_required"}), 400
    updates = {}
    if "auto_enabled" in body:
        if not isinstance(body["auto_enabled"], bool):
            return jsonify({"error": "auto_enabled_must_be_boolean"}), 400
        updates["auto_enabled"] = body["auto_enabled"]
    for key in ("start_threshold_pct", "stop_threshold_pct"):
        if key in body:
            value = body[key]
            if not isinstance(value, (int, float)) or isinstance(value, bool):
                return jsonify({"error": f"{key}_must_be_number"}), 400
            if not IRRIGATION_RULE_LIMITS["min_pct"] <= value <= IRRIGATION_RULE_LIMITS["max_pct"]:
                return jsonify({"error": f"{key}_out_of_range",
                                "allowed": [IRRIGATION_RULE_LIMITS["min_pct"], IRRIGATION_RULE_LIMITS["max_pct"]]}), 400
            updates[key] = float(value)
    if "cooldown_seconds" in body:
        value = body["cooldown_seconds"]
        if not isinstance(value, (int, float)) or isinstance(value, bool) or value < 0:
            return jsonify({"error": "cooldown_seconds_must_be_non_negative_number"}), 400
        updates["cooldown_seconds"] = min(float(value), 3600)
    current = _get_irrigation_rule(device_id)
    merged = {**current, **updates}
    if merged["stop_threshold_pct"] <= merged["start_threshold_pct"]:
        return jsonify({"error": "stop_threshold_must_exceed_start_threshold"}), 400
    updates["updated_at"] = utc_now()
    with irrigation_rules_lock:
        irrigation_rules.setdefault(device_id, {}).update(updates)
    return jsonify(_get_irrigation_rule(device_id))


@app.get("/api/v1/devices/<device_id>/irrigation-events")
@require_auth()
def irrigation_event_history(device_id):
    try:
        limit = min(max(int(request.args.get("limit", "20")), 1), 200)
    except ValueError:
        return jsonify({"error": "limit_must_be_integer"}), 400
    with irrigation_rules_lock:
        items = [dict(event) for event in irrigation_events if event["device_id"] == device_id]
    return jsonify({"device_id": device_id, "count": len(items[-limit:]), "items": items[-limit:]})


def _image_record(image_id):
    with image_registry_lock:
        return image_registry.get(image_id)


@app.post("/api/v1/images")
@require_auth("upload_image")
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
