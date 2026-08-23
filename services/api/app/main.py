from datetime import datetime, timezone
import json
import logging
import os
from threading import Lock, Thread

from flask import Flask, jsonify
import paho.mqtt.client as mqtt

app = Flask(__name__)
logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"))
LOGGER = logging.getLogger("smart-agriculture-api")

MQTT_HOST = os.getenv("MQTT_HOST", "localhost")
MQTT_PORT = int(os.getenv("MQTT_PORT", "1883"))
registry = {}
registry_lock = Lock()


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
    from flask import request

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
