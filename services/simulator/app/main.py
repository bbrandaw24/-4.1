"""Day 16 sensor-driven simulator.

Reads the canonical SENSOR_TYPES from the API and publishes MQTT telemetry in
the new format ``farm/{device_id}/{sensor_id}/telemetry`` so that the API
sensor registry is the single source of truth. Bootstraps per-device sensors
through the API (admin token) on every start, so a fresh deployment (or new
plot) gets the 5 default sensors automatically.

For backward compatibility with the Day 10 irrigation rule (which still keys
on ``soil.moisture_pct``), the simulator also keeps publishing the legacy
``farm/{device_id}/sensor/soil`` and ``farm/{device_id}/sensor/climate``
payloads with the same moisture baseline. These can be retired later.
"""

from __future__ import annotations

import json
import logging
import os
import random
import signal
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone

import paho.mqtt.client as mqtt

logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"), format="%(asctime)s %(levelname)s %(message)s")
LOGGER = logging.getLogger("device-simulator")

MQTT_HOST = os.getenv("MQTT_HOST", "localhost")
MQTT_PORT = int(os.getenv("MQTT_PORT", "1883"))
PUBLISH_INTERVAL = float(os.getenv("PUBLISH_INTERVAL_SECONDS", "1"))
KEEP_LEGACY_PAYLOADS = os.getenv("SIM_KEEP_LEGACY_PAYLOADS", "true").lower() == "true"
API_UPSTREAM = os.getenv("API_UPSTREAM", "http://127.0.0.1:8010")
ADMIN_USERNAME = os.getenv("ADMIN_USERNAME", "admin")
ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD", "admin123")

DEFAULT_PROFILES = [
    {"id": "sim-plot-apple", "label": "苹果园", "crop": "苹果",
     "moisture": 50.0, "soil_temp": 23.0, "air_temp": 24.0, "humidity": 66.0, "light": 30000.0,
     "ph": 6.20, "nitrogen": 130.0, "phosphorus": 50.0, "potassium": 178.0, "conductivity": 1.02},
    {"id": "sim-plot-pear", "label": "梨园", "crop": "梨",
     "moisture": 60.0, "soil_temp": 25.0, "air_temp": 26.0, "humidity": 70.0, "light": 34000.0,
     "ph": 6.35, "nitrogen": 145.0, "phosphorus": 55.0, "potassium": 190.0, "conductivity": 1.20},
    {"id": "sim-plot-orange", "label": "橘园", "crop": "橘子",
     "moisture": 44.0, "soil_temp": 27.0, "air_temp": 28.0, "humidity": 62.0, "light": 36000.0,
     "ph": 5.95, "nitrogen": 120.0, "phosphorus": 45.0, "potassium": 165.0, "conductivity": 0.95},
]

SENSOR_TYPES = {  # mirror of API SENSOR_TYPES — only the bits the simulator needs
    "soil_temperature": {"unit": "°C", "field": "temperature_c", "interval": 30},
    "soil_ph":          {"unit": "",    "field": "ph",              "interval": 30},
    "soil_npk":         {"unit": "mg/kg", "field": "npk",          "interval": 60},
    "air_humidity":     {"unit": "%",   "field": "air_humidity_pct", "interval": 15},
    "soil_conductivity":{"unit": "mS/cm", "field": "conductivity_ms_cm", "interval": 30},
}


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


def envelope_legacy(device_id: str, payload: dict) -> str:
    return json.dumps({"device_id": device_id, "timestamp": now(), "payload": payload}, separators=(",", ":"))


def envelope_sensor(sensor: dict, value: dict, unit: str, ts: str) -> str:
    return json.dumps({
        "sensor_id": sensor["id"],
        "device_id": sensor["device_id"],
        "type": sensor["type"],
        "value": value,
        "unit": unit,
        "timestamp": ts,
    }, separators=(",", ":"))


# --- API bootstrap (login + register devices + seed sensors) -----------------
class ApiClient:
    def __init__(self, base: str):
        self.base = base.rstrip("/")
        self.token = None

    def login(self, username: str, password: str) -> str:
        body = json.dumps({"username": username, "password": password}).encode("utf-8")
        req = urllib.request.Request(
            f"{self.base}/api/v1/auth/login",
            data=body,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=10) as resp:
                payload = json.loads(resp.read())
                self.token = payload["token"]
                LOGGER.info("simulator logged in as %s", username)
                return self.token
        except (urllib.error.URLError, urllib.error.HTTPError, KeyError, json.JSONDecodeError) as exc:
            LOGGER.warning("simulator login failed (%s); will retry", exc)
            raise

    def _headers(self):
        if not self.token:
            raise RuntimeError("not authenticated")
        return {"Authorization": f"Bearer {self.token}", "Content-Type": "application/json"}

    def ensure_device(self, device_id: str):
        req = urllib.request.Request(
            f"{self.base}/api/v1/devices",
            data=json.dumps({"device_id": device_id}).encode("utf-8"),
            headers=self._headers(),
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=10) as resp:
                payload = json.loads(resp.read())
                LOGGER.info("device %s: %s", device_id, payload.get("status"))
        except urllib.error.HTTPError as exc:
            if exc.code == 409:
                return  # already registered
            body = exc.read().decode("utf-8", errors="replace")
            LOGGER.warning("ensure_device %s failed: %s %s", device_id, exc.code, body)

    def list_sensors(self, device_id: str) -> list[dict]:
        req = urllib.request.Request(
            f"{self.base}/api/v1/devices/{device_id}/sensors",
            headers=self._headers(),
            method="GET",
        )
        try:
            with urllib.request.urlopen(req, timeout=10) as resp:
                return json.loads(resp.read()).get("items", [])
        except (urllib.error.URLError, urllib.error.HTTPError, json.JSONDecodeError) as exc:
            LOGGER.warning("list_sensors(%s) failed: %s", device_id, exc)
            return []

    def create_sensor(self, device_id: str, sensor_type: str):
        req = urllib.request.Request(
            f"{self.base}/api/v1/devices/{device_id}/sensors",
            data=json.dumps({"type": sensor_type}).encode("utf-8"),
            headers=self._headers(),
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=10) as resp:
                LOGGER.info("seed sensor %s on %s", sensor_type, device_id)
        except urllib.error.HTTPError as exc:
            if exc.code in {409, 400}:
                body = exc.read().decode("utf-8", errors="replace")
                LOGGER.debug("create_sensor(%s,%s): %s %s", device_id, sensor_type, exc.code, body)
            else:
                LOGGER.warning("create_sensor(%s,%s): HTTP %s", device_id, sensor_type, exc.code)


def bootstrap(api: ApiClient, profiles: list[dict]):
    """Register each device and create missing default sensors."""
    for profile in profiles:
        device_id = profile["id"]
        api.ensure_device(device_id)
        existing = api.list_sensors(device_id)
        existing_types = {s["type"] for s in existing}
        for sensor_type in SENSOR_TYPES:
            if sensor_type not in existing_types:
                api.create_sensor(device_id, sensor_type)


def refresh_sensor_cache(api: ApiClient, profiles: list[dict]) -> dict:
    """Return {device_id: [sensor, ...]} covering connected sensors only."""
    cache: dict[str, list[dict]] = {}
    for profile in profiles:
        sensors = api.list_sensors(profile["id"])
        cache[profile["id"]] = [s for s in sensors if s.get("status") == "connected"]
    return cache


# --- value simulation -------------------------------------------------------
def _drift(state_key: str, baseline: float, lo: float, hi: float, step: float = 0.05) -> float:
    val = baseline + random.gauss(0, step)
    return max(lo, min(hi, val))


def compute_sensor_value(sensor: dict, state: dict) -> tuple[dict, str]:
    """Return (value_dict, unit) for the given sensor, drifting per type."""
    s_type = sensor["type"]
    if s_type == "soil_temperature":
        state["soil_temp"] = _drift("soil_temp", state["soil_temp"] - 0.02, 18.0, 30.0, 0.12)
        return {"temperature_c": round(state["soil_temp"], 2)}, "°C"
    if s_type == "soil_ph":
        state["ph"] = _drift("ph", state["ph"], 5.5, 7.2, 0.04)
        return {"ph": round(state["ph"], 2)}, ""
    if s_type == "soil_npk":
        state["nitrogen"]   = _drift("n", state["nitrogen"], 60, 180, 3.0)
        state["phosphorus"] = _drift("p", state["phosphorus"], 25, 80, 2.0)
        state["potassium"]  = _drift("k", state["potassium"], 120, 230, 4.0)
        return {
            "nitrogen_mg_kg":   round(state["nitrogen"], 2),
            "phosphorus_mg_kg": round(state["phosphorus"], 2),
            "potassium_mg_kg":  round(state["potassium"], 2),
        }, "mg/kg"
    if s_type == "air_humidity":
        state["humidity"] = _drift("h", state["humidity"], 50.0, 85.0, 0.35)
        return {"air_humidity_pct": round(state["humidity"], 2)}, "%"
    if s_type == "soil_conductivity":
        state["conductivity"] = _drift("c", state["conductivity"], 0.6, 2.8, 0.025)
        return {"conductivity_ms_cm": round(state["conductivity"], 3)}, "mS/cm"
    return {}, ""


def build_legacy_payload(profile: dict, state: dict) -> tuple[dict, dict]:
    """Return (soil, climate) payloads keeping the Day 10 contract."""
    state["moisture"] += (profile["gain"] if state["pump_running"] else -profile["decay"]) + random.gauss(0, 0.08)
    state["moisture"] = max(profile["moist_min"], min(profile["moist_max"], state["moisture"]))
    state["air_temp"] = max(18.0, min(34.0, state["air_temp"] + random.gauss(0, 0.16)))
    state["light"] = max(16000.0, min(46000.0, state["light"] + random.gauss(0, 900)))
    soil = {
        "moisture_pct": round(state["moisture"], 2),
        "temperature_c": round(state["soil_temp"], 2),
        "ph": round(6.2 + random.gauss(0, 0.03), 2),
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
    return soil, climate


# --- MQTT plumbing ----------------------------------------------------------
def on_message(states: dict, profiles_by_id: dict, _client, _userdata, message):
    parts = message.topic.split("/")
    if len(parts) != 4 or parts[0] != "farm" or parts[2] != "control" or parts[3] != "pump":
        return
    device_id = parts[1]
    state = states.get(device_id)
    if state is None:
        LOGGER.warning("ignored pump command for unknown device %s", device_id)
        return
    try:
        command = json.loads(message.payload.decode("utf-8"))
        payload = command.get("payload", command)
        action = payload.get("action")
        if action not in {"start", "stop"}:
            return
        command_id = command.get("command_id") or payload.get("command_id")
        state["pump_running"] = action == "start"
        reply = {"action": action, "running": state["pump_running"], "command_id": command_id}
        _client.publish(f"farm/{device_id}/status/pump", envelope_legacy(device_id, reply), qos=1)
        LOGGER.info("%s pump state changed: running=%s", device_id, state["pump_running"])
    except (UnicodeDecodeError, json.JSONDecodeError, AttributeError) as exc:
        LOGGER.warning("ignored invalid command: %s", exc)


def main() -> None:
    profiles = _load_profiles()
    profiles_by_id = {p["id"]: p for p in profiles}
    states = {
        p["id"]: {
            "moisture": p["moisture"], "soil_temp": p["soil_temp"], "air_temp": p["air_temp"],
            "humidity": p["humidity"], "light": p["light"],
            "ph": p["ph"], "nitrogen": p["nitrogen"], "phosphorus": p["phosphorus"],
            "potassium": p["potassium"], "conductivity": p["conductivity"],
            "pump_running": False,
        }
        for p in profiles
    }

    api = ApiClient(API_UPSTREAM)
    for attempt in range(6):
        try:
            api.login(ADMIN_USERNAME, ADMIN_PASSWORD)
            break
        except Exception:
            time.sleep(min(2 ** attempt, 30))
    else:
        LOGGER.error("simulator cannot log in to %s; sensors will not be seeded", API_UPSTREAM)

    if api.token:
        try:
            bootstrap(api, profiles)
        except Exception as exc:
            LOGGER.warning("bootstrap failed: %s", exc)

    client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id="multi-plot-simulator")
    client.on_message = lambda c, u, m: on_message(states, profiles_by_id, c, u, m)
    client.connect(MQTT_HOST, MQTT_PORT, keepalive=60)
    for p in profiles:
        client.subscribe(f"farm/{p['id']}/control/pump", qos=1)
        if KEEP_LEGACY_PAYLOADS:
            client.subscribe(f"farm/{p['id']}/+/telemetry", qos=1)
    client.loop_start()
    LOGGER.info("connected to %s:%s", MQTT_HOST, MQTT_PORT)

    sensor_last_publish: dict[str, float] = {}
    sensor_cache_refresh_at = 0.0
    sensor_cache: dict[str, list[dict]] = {p["id"]: [] for p in profiles}

    stopping = False

    def stop(_signum, _frame):
        nonlocal stopping
        stopping = True

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    try:
        while not stopping:
            if not client.is_connected():
                time.sleep(PUBLISH_INTERVAL)
                continue
            # Refresh sensor cache every 5s (cheap; reads SQLite via API)
            now_ts = time.time()
            if api.token and (now_ts - sensor_cache_refresh_at) > 5:
                try:
                    sensor_cache = refresh_sensor_cache(api, profiles)
                except Exception as exc:
                    LOGGER.debug("sensor cache refresh failed: %s", exc)
                sensor_cache_refresh_at = now_ts
            for profile in profiles:
                device_id = profile["id"]
                for sensor in sensor_cache.get(device_id, []):
                    interval = SENSOR_TYPES.get(sensor["type"], {}).get("interval", 30)
                    last = sensor_last_publish.get(sensor["id"], 0.0)
                    if now_ts - last < interval:
                        continue
                    value, unit = compute_sensor_value(sensor, states[device_id])
                    if not value:
                        continue
                    topic = f"farm/{device_id}/{sensor['id']}/telemetry"
                    payload = envelope_sensor(sensor, value, unit, now())
                    info = client.publish(topic, payload, qos=1, retain=False)
                    info.wait_for_publish()
                    sensor_last_publish[sensor["id"]] = now_ts
                    LOGGER.info("published %s type=%s", topic, sensor["type"])
                # Legacy payloads keep irrigation rule and historical trend charts working.
                if KEEP_LEGACY_PAYLOADS and (now_ts - sensor_last_publish.get(f"{device_id}:legacy", 0.0)) >= 5:
                    soil, climate = build_legacy_payload(profile, states[device_id])
                    for kind, payload in (("soil", soil), ("climate", climate)):
                        topic = f"farm/{device_id}/sensor/{kind}"
                        client.publish(topic, envelope_legacy(device_id, payload), qos=1).wait_for_publish()
                    sensor_last_publish[f"{device_id}:legacy"] = now_ts
            time.sleep(PUBLISH_INTERVAL)
    finally:
        client.loop_stop()
        client.disconnect()


if __name__ == "__main__":
    main()