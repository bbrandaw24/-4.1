"""Multi-plot virtual greenhouse simulator: publishes distinct sensor data for
three plots (apple / pear / orange orchards) over MQTT, each with its own state
and pump control channel (farm/<device_id>/control/pump)."""

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
PUBLISH_INTERVAL = float(os.getenv("PUBLISH_INTERVAL_SECONDS", "5"))

# Each plot has a distinct crop, soil-moisture baseline and climate profile.
DEFAULT_PROFILES = [
    {"id": "sim-plot-apple", "label": "苹果园", "crop": "苹果",
     "moisture": 50.0, "soil_temp": 23.0, "air_temp": 24.0, "humidity": 66.0, "light": 30000.0,
     "decay": 0.12, "gain": 1.8, "moist_min": 38.0, "moist_max": 68.0},
    {"id": "sim-plot-pear", "label": "梨园", "crop": "梨",
     "moisture": 60.0, "soil_temp": 25.0, "air_temp": 26.0, "humidity": 70.0, "light": 34000.0,
     "decay": 0.10, "gain": 1.5, "moist_min": 45.0, "moist_max": 75.0},
    {"id": "sim-plot-orange", "label": "橘园", "crop": "橘子",
     "moisture": 44.0, "soil_temp": 27.0, "air_temp": 28.0, "humidity": 62.0, "light": 36000.0,
     "decay": 0.16, "gain": 2.0, "moist_min": 35.0, "moist_max": 62.0},
]


def _load_profiles() -> list[dict]:
    raw = os.getenv("SIM_DEVICES")
    if not raw:
        return DEFAULT_PROFILES
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        LOGGER.warning("invalid SIM_DEVICES JSON, using defaults")
        return DEFAULT_PROFILES


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def envelope(device_id: str, payload: dict) -> str:
    return json.dumps({"device_id": device_id, "timestamp": now(), "payload": payload}, separators=(",", ":"))


def publish_sensor(client: mqtt.Client, profile: dict, state: dict) -> None:
    device_id = profile["id"]
    state["moisture"] += (profile["gain"] if state["pump_running"] else -profile["decay"]) + random.gauss(0, 0.08)
    state["moisture"] = max(profile["moist_min"], min(profile["moist_max"], state["moisture"]))
    state["soil_temp"] = max(18.0, min(30.0, state["soil_temp"] + random.gauss(0, 0.12)))
    state["air_temp"] = max(18.0, min(34.0, state["air_temp"] + random.gauss(0, 0.16)))
    state["humidity"] = max(50.0, min(85.0, state["humidity"] + random.gauss(0, 0.35)))
    state["light"] = max(16000.0, min(46000.0, state["light"] + random.gauss(0, 900)))
    soil = {
        "moisture_pct": round(state["moisture"], 2),
        "temperature_c": round(state["soil_temp"], 2),
        "ph": round(6.2 + profile["crop"] * 0.0 + random.gauss(0, 0.03), 2),
        "nitrogen_mg_kg": round(130 + random.gauss(0, 3), 2),
        "phosphorus_mg_kg": round(50 + random.gauss(0, 2), 2),
        "potassium_mg_kg": round(178 + random.gauss(0, 4), 2),
        "conductivity_ms_cm": round(1.02 + random.gauss(0, 0.025), 3),
        "salinity_g_l": round(0.40 + random.gauss(0, 0.012), 3),
    }
    climate = {
        "light_lux": round(state["light"], 1),
        "air_temperature_c": round(state["air_temp"], 2),
        "air_humidity_pct": round(state["humidity"], 2),
    }
    for kind, payload in (("sensor/soil", soil), ("sensor/climate", climate)):
        topic = f"farm/{device_id}/{kind}"
        info = client.publish(topic, envelope(device_id, payload), qos=1, retain=False)
        info.wait_for_publish()
        LOGGER.info("published %s", topic)


def on_message(states: dict, profiles: dict, _client: mqtt.Client, _userdata: object, message: mqtt.MQTTMessage) -> None:
    parts = message.topic.split("/")
    if len(parts) != 4 or parts[0] != "farm" or parts[2] != "control" or parts[3] != "pump":
        return
    device_id = parts[1]
    state = states.get(device_id)
    profile = profiles.get(device_id)
    if state is None or profile is None:
        LOGGER.warning("ignored pump command for unknown device %s", device_id)
        return
    try:
        command = json.loads(message.payload.decode("utf-8"))
        payload = command.get("payload", command)
        if payload.get("action") not in {"start", "stop"}:
            LOGGER.warning("ignored unknown pump action: %s", payload)
            return
        command_id = command.get("command_id") or payload.get("command_id")
        state["pump_running"] = payload["action"] == "start"
        reply = {"action": payload["action"], "running": state["pump_running"], "command_id": command_id}
        client.publish(f"farm/{device_id}/status/pump", envelope(device_id, reply), qos=1)
        LOGGER.info("%s pump state changed: running=%s", device_id, state["pump_running"])
    except (UnicodeDecodeError, json.JSONDecodeError, AttributeError) as exc:
        LOGGER.warning("ignored invalid command: %s", exc)


def main() -> None:
    profiles = _load_profiles()
    states = {
        profile["id"]: {
            "moisture": profile["moisture"],
            "soil_temp": profile["soil_temp"],
            "air_temp": profile["air_temp"],
            "humidity": profile["humidity"],
            "light": profile["light"],
            "pump_running": False,
        }
        for profile in profiles
    }
    by_id = {profile["id"]: profile for profile in profiles}
    LOGGER.info("simulating %d plots: %s", len(profiles), ", ".join(f"{p['id']}({p['crop']})" for p in profiles))

    client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id="multi-plot-simulator")
    client.on_message = lambda c, u, m: on_message(states, by_id, c, u, m)
    client.connect(MQTT_HOST, MQTT_PORT, keepalive=60)
    for profile in profiles:
        client.subscribe(f"farm/{profile['id']}/control/pump", qos=1)
    client.loop_start()
    LOGGER.info("connected to %s:%s", MQTT_HOST, MQTT_PORT)

    stopping = False

    def stop(_signum: int, _frame: object) -> None:
        nonlocal stopping
        stopping = True

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    try:
        while not stopping:
            if client.is_connected():
                for profile in profiles:
                    publish_sensor(client, profile, states[profile["id"]])
            time.sleep(PUBLISH_INTERVAL)
    finally:
        client.loop_stop()
        client.disconnect()


if __name__ == "__main__":
    main()
