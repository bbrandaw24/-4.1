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
MOISTURE_GAIN_PER_TICK = float(os.getenv("MOISTURE_GAIN_PER_TICK", "1.8"))
MOISTURE_DECAY_PER_TICK = float(os.getenv("MOISTURE_DECAY_PER_TICK", "0.12"))

SIM_STATE = {
    "moisture_pct": 50.0,
    "temperature_c": 23.0,
    "air_temperature_c": 24.0,
    "air_humidity_pct": 68.0,
    "light_lux": 30000.0,
    "pump_running": False,
}


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def topic(kind: str) -> str:
    return f"farm/{DEVICE_ID}/{kind}"


def envelope(payload: dict) -> str:
    return json.dumps({"device_id": DEVICE_ID, "timestamp": now(), "payload": payload}, separators=(",", ":"))


def publish_sensor(client: mqtt.Client) -> None:
    SIM_STATE["moisture_pct"] += (MOISTURE_GAIN_PER_TICK if SIM_STATE["pump_running"] else -MOISTURE_DECAY_PER_TICK) + random.gauss(0, 0.08)
    SIM_STATE["moisture_pct"] = max(25.0, min(75.0, SIM_STATE["moisture_pct"]))
    SIM_STATE["temperature_c"] = max(18.0, min(28.0, SIM_STATE["temperature_c"] + random.gauss(0, 0.12)))
    SIM_STATE["air_temperature_c"] = max(18.0, min(30.0, SIM_STATE["air_temperature_c"] + random.gauss(0, 0.16)))
    SIM_STATE["air_humidity_pct"] = max(55.0, min(80.0, SIM_STATE["air_humidity_pct"] + random.gauss(0, 0.35)))
    SIM_STATE["light_lux"] = max(18000.0, min(42000.0, SIM_STATE["light_lux"] + random.gauss(0, 900)))
    soil = {
        "moisture_pct": round(SIM_STATE["moisture_pct"], 2),
        "temperature_c": round(SIM_STATE["temperature_c"], 2),
        "ph": round(6.35 + random.gauss(0, 0.03), 2),
        "nitrogen_mg_kg": round(135 + random.gauss(0, 3), 2),
        "phosphorus_mg_kg": round(52 + random.gauss(0, 2), 2),
        "potassium_mg_kg": round(180 + random.gauss(0, 4), 2),
        "conductivity_ms_cm": round(1.05 + random.gauss(0, 0.025), 3),
        "salinity_g_l": round(0.42 + random.gauss(0, 0.012), 3),
    }
    climate = {
        "light_lux": round(SIM_STATE["light_lux"], 1),
        "air_temperature_c": round(SIM_STATE["air_temperature_c"], 2),
        "air_humidity_pct": round(SIM_STATE["air_humidity_pct"], 2),
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
        command_id = command.get("command_id") or payload.get("command_id")
        state = {"action": payload["action"], "running": payload["action"] == "start", "command_id": command_id}
        SIM_STATE["pump_running"] = state["running"]
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

