"""Virtual greenhouse device for local MQTT integration tests."""

from __future__ import annotations

import json
import logging
import os
import random
import signal
import time
from datetime import datetime, timezone

import paho.mqtt.client as mqtt

logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"), format="%(asctime)s %(levelname)s %(message)s")
LOGGER = logging.getLogger("device-simulator")

MQTT_HOST = os.getenv("MQTT_HOST", "localhost")
MQTT_PORT = int(os.getenv("MQTT_PORT", "1883"))
DEVICE_ID = os.getenv("DEVICE_ID", "sim-greenhouse-001")
PUBLISH_INTERVAL = float(os.getenv("PUBLISH_INTERVAL_SECONDS", "5"))


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def topic(kind: str) -> str:
    return f"farm/{DEVICE_ID}/{kind}"


def envelope(payload: dict) -> str:
    return json.dumps({"device_id": DEVICE_ID, "timestamp": now(), "payload": payload}, separators=(",", ":"))


def publish_sensor(client: mqtt.Client) -> None:
    soil = {
        "moisture_pct": round(random.uniform(38, 62), 2),
        "temperature_c": round(random.uniform(18, 28), 2),
        "ph": round(random.uniform(5.8, 6.8), 2),
        "nitrogen_mg_kg": round(random.uniform(80, 180), 2),
        "phosphorus_mg_kg": round(random.uniform(25, 80), 2),
        "potassium_mg_kg": round(random.uniform(100, 240), 2),
        "conductivity_ms_cm": round(random.uniform(0.4, 1.8), 3),
        "salinity_g_l": round(random.uniform(0.1, 0.8), 3),
    }
    climate = {
        "light_lux": round(random.uniform(9000, 42000), 1),
        "air_temperature_c": round(random.uniform(16, 31), 2),
        "air_humidity_pct": round(random.uniform(45, 90), 2),
    }
    for kind, payload in (("sensor/soil", soil), ("sensor/climate", climate)):
        info = client.publish(topic(kind), envelope(payload), qos=1, retain=False)
        info.wait_for_publish()
        LOGGER.info("published %s", topic(kind))


def on_message(client: mqtt.Client, _userdata: object, message: mqtt.MQTTMessage) -> None:
    try:
        command = json.loads(message.payload.decode("utf-8"))
        payload = command.get("payload", command)
        if payload.get("action") not in {"start", "stop"}:
            LOGGER.warning("ignored unknown pump action: %s", payload)
            return
        state = {"action": payload["action"], "running": payload["action"] == "start"}
        client.publish(topic("status/pump"), envelope(state), qos=1)
        LOGGER.info("pump state changed: %s", state["running"])
    except (UnicodeDecodeError, json.JSONDecodeError, AttributeError) as exc:
        LOGGER.warning("ignored invalid command: %s", exc)


def main() -> None:
    client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id=DEVICE_ID)
    client.on_message = on_message
    client.connect(MQTT_HOST, MQTT_PORT, keepalive=60)
    client.subscribe(topic("control/pump"), qos=1)
    client.loop_start()
    LOGGER.info("device %s connected to %s:%s", DEVICE_ID, MQTT_HOST, MQTT_PORT)
    stopping = False

    def stop(_signum: int, _frame: object) -> None:
        nonlocal stopping
        stopping = True

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    try:
        while not stopping:
            if client.is_connected():
                publish_sensor(client)
            time.sleep(PUBLISH_INTERVAL)
    finally:
        client.loop_stop()
        client.disconnect()


if __name__ == "__main__":
    main()

