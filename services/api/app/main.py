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
    from .auth import current_user, get_user_by_id, init_db, register_auth_routes, require_auth
    from .agent import answer_question, load_knowledge_base
except ImportError:  # allow running main.py directly without the package context
    from auth import current_user, get_user_by_id, init_db, register_auth_routes, require_auth
    from agent import answer_question, load_knowledge_base

app = Flask(__name__)
init_db()
register_auth_routes(app)
load_knowledge_base()
logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"))
LOGGER = logging.getLogger("smart-agriculture-api")


@app.after_request
def add_cors_headers(response):
    """Allow the dashboard and local development hosts to call the API."""
    response.headers["Access-Control-Allow-Origin"] = os.getenv("CORS_ORIGIN", "*")
    response.headers["Access-Control-Allow-Headers"] = "Content-Type, Authorization"
    response.headers["Access-Control-Allow-Methods"] = "GET, POST, PUT, PATCH, DELETE, OPTIONS"
    return response


@app.before_request
def handle_cors_preflight():
    """Return 204 for CORS preflight so cross-origin clients (e.g. GitHub Pages) succeed."""
    if request.method == "OPTIONS":
        return app.make_default_options_response()

MQTT_HOST = os.getenv("MQTT_HOST", "localhost")
MQTT_PORT = int(os.getenv("MQTT_PORT", "1883"))
MQTT_USERNAME = os.getenv("MQTT_USERNAME", "")
MQTT_PASSWORD = os.getenv("MQTT_PASSWORD", "")
MQTT_CLIENT_ID = os.getenv("MQTT_CLIENT_ID", "smart-agriculture-api")
UPLOAD_DIR = Path(os.getenv("UPLOAD_DIR", "/data/uploads"))
MAX_UPLOAD_BYTES = int(os.getenv("MAX_UPLOAD_BYTES", str(5 * 1024 * 1024)))
registry = {}
registry_lock = Lock()
# Tombstones for deleted user plots: while present, MQTT telemetry for that
# device is dropped so a deleted plot cannot be revived by lingering messages.
_deleted_plots: dict[str, float] = {}
PUMP_CONFIRM_TIMEOUT_SECONDS = float(os.getenv("PUMP_CONFIRM_TIMEOUT_SECONDS", "5"))
HISTORY_LIMIT = int(os.getenv("TELEMETRY_HISTORY_LIMIT", "7200"))
pending_commands = {}
image_registry = {}
image_registry_lock = Lock()
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)

# --- Plot metadata (multi-plot deployment: apple / pear / orange orchards) ----
PLOT_META = {
    "sim-plot-apple": {"name": "苹果园", "crop": "苹果"},
    "sim-plot-pear": {"name": "梨园", "crop": "梨"},
    "sim-plot-orange": {"name": "橘园", "crop": "橘子"},
}

# --- Plot ownership (multi-tenant isolation) ---------------------------------
# Every plot belongs to exactly one account. A regular account (farmer/guest)
# only ever sees its own plots; a manager ("管理员") sees every plot.
# The three built-in demo plots are owned by the seeded admin account (id=1).
BUILTIN_PLOT_OWNER_ID = 1
# Plots that appear only via MQTT (never created through the API) have no owner
# and are therefore invisible to non-managers until they are claimed.
ORPHAN_PLOT_OWNER_ID = None


def _current_user():
    return current_user() or {}


def _current_user_id():
    return _current_user().get("user_id")


def _is_manager():
    return _current_user().get("role") == "manager"


def _plot_owner(device_id):
    """Resolve the owning user id for a plot (registry first, then DB, then built-in)."""
    with registry_lock:
        device = registry.get(device_id)
    if device is not None and device.get("owner_user_id") is not None:
        return device["owner_user_id"]
    if device_id in PLOT_META:
        return BUILTIN_PLOT_OWNER_ID
    owner = _load_plot_owner(device_id)
    if owner is not None:
        return owner
    return ORPHAN_PLOT_OWNER_ID


def _accessible_device_ids():
    """Ids the current user may see. Returns None when the user may see all."""
    if _is_manager():
        return None
    uid = _current_user_id()
    with registry_lock:
        return {did for did, device in registry.items() if device.get("owner_user_id") == uid}


def _can_access_plot(device_id):
    """True when the current user owns the plot (or is a manager)."""
    if _is_manager():
        return True
    return _plot_owner(device_id) == _current_user_id()


def _plot_access_error(device_id):
    """Uniform 403 payload for cross-tenant plot access attempts."""
    return jsonify({"error": "plot_forbidden", "device_id": device_id,
                    "message": "该地块不属于当前账户，仅管理员可访问全部地块"}), 403


def _owner_label(owner_user_id):
    if owner_user_id is None:
        return "未分配"
    if owner_user_id == BUILTIN_PLOT_OWNER_ID:
        return "内置"
    try:
        user = get_user_by_id(owner_user_id)
    except Exception:
        return f"#{owner_user_id}"
    if not user:
        return f"#{owner_user_id}"
    return user.get("display_name") or user.get("username") or f"#{owner_user_id}"

# --- Day 16: sensor registry ------------------------------------------------
# Each plot (device) owns a fixed set of virtual hardware (sensors). Sensors
# are first-class persistent entities stored in SQLite so that the dashboard
# can render them, the simulator can drive them, and the alert/agent layers
# can pull their latest values. The MQTT broker is shared globally; the topic
# itself encodes the sensor identity (`farm/{device}/{sensor_id}/telemetry`).
SENSOR_TYPES = {
    "soil_temperature": {
        "name": "土壤温度",
        "unit": "°C",
        "field": "temperature_c",
        "baseline_range": (20.0, 26.0),
        "publish_interval_seconds": 30,
    },
    "soil_ph": {
        "name": "pH",
        "unit": "",
        "field": "ph",
        "baseline_range": (5.8, 6.8),
        "publish_interval_seconds": 30,
    },
    "soil_npk": {
        "name": "氮/磷/钾",
        "unit": "mg/kg",
        "field": "npk",
        "baseline_range": (80, 200),
        "publish_interval_seconds": 60,
    },
    "air_humidity": {
        "name": "空气湿度",
        "unit": "%",
        "field": "air_humidity_pct",
        "baseline_range": (55.0, 75.0),
        "publish_interval_seconds": 15,
    },
    "soil_conductivity": {
        "name": "电导率",
        "unit": "mS/cm",
        "field": "conductivity_ms_cm",
        "baseline_range": (0.8, 2.5),
        "publish_interval_seconds": 30,
    },
}
SENSOR_STATUS_CONNECTED = "connected"
SENSOR_STATUS_DISCONNECTED = "disconnected"
ALERT_LOG_LIMIT = int(os.getenv("ALERT_LOG_LIMIT", "500"))  # keep newest N alert records
ALERT_EVAL_INTERVAL_SECONDS = float(os.getenv("ALERT_EVAL_INTERVAL", "5"))
ALERT_LOGGING_ENABLED = os.getenv("ALERT_LOGGING_ENABLED", "true").lower() == "true"

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
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS alert_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                device_id TEXT NOT NULL,
                level TEXT NOT NULL,
                code TEXT NOT NULL,
                message TEXT NOT NULL,
                status TEXT NOT NULL,
                ts TEXT NOT NULL
            )
            """
        )
        conn.execute("CREATE INDEX IF NOT EXISTS idx_al_device_ts ON alert_log(device_id, ts)")
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS sensors (
                id TEXT PRIMARY KEY,
                device_id TEXT NOT NULL,
                type TEXT NOT NULL,
                status TEXT NOT NULL,
                value_json TEXT,
                unit TEXT,
                last_seen TEXT,
                created_at TEXT NOT NULL,
                UNIQUE(device_id, type)
            )
            """
        )
        conn.execute("CREATE INDEX IF NOT EXISTS idx_sensors_device ON sensors(device_id)")
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS mqtt_broker (
                id INTEGER PRIMARY KEY CHECK (id = 1),
                host TEXT NOT NULL,
                port INTEGER NOT NULL,
                username TEXT,
                password TEXT,
                updated_at TEXT NOT NULL
            )
            """
        )
        # User-created plots (Day 16+ "add plot" feature): survives API restarts.
        # The built-in apple/pear/orange plots stay in PLOT_META (code).
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS custom_plots (
                device_id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                crop TEXT,
                created_at TEXT NOT NULL
            )
            """
        )
        # Multi-tenant migration: every plot belongs to an account. Existing rows
        # pre-date ownership, so they are back-filled to the seeded admin account.
        try:
            conn.execute("ALTER TABLE custom_plots ADD COLUMN owner_user_id INTEGER")
        except sqlite3.OperationalError:
            pass  # column already exists
        conn.execute(
            "UPDATE custom_plots SET owner_user_id=? WHERE owner_user_id IS NULL",
            (BUILTIN_PLOT_OWNER_ID,),
        )
        conn.execute("CREATE INDEX IF NOT EXISTS idx_plots_owner ON custom_plots(owner_user_id)")
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


# --- Persistent alert log (trace historical alerts) -------------------------
def _insert_alert(device_id, level, code, message, status, timestamp):
    conn = _telemetry_connect()
    try:
        conn.execute(
            "INSERT INTO alert_log (device_id, level, code, message, status, ts) VALUES (?,?,?,?,?,?)",
            (device_id, level, code, message, status, timestamp),
        )
        conn.execute(
            "DELETE FROM alert_log WHERE id NOT IN (SELECT id FROM alert_log ORDER BY id DESC LIMIT ?)",
            (ALERT_LOG_LIMIT,),
        )
        conn.commit()
    finally:
        conn.close()


def list_alerts(device_id=None, level=None, limit=50):
    conn = _telemetry_connect()
    try:
        where = []
        params = []
        if device_id:
            where.append("device_id = ?")
            params.append(device_id)
        if level:
            where.append("level = ?")
            params.append(level)
        where_sql = (" WHERE " + " AND ".join(where)) if where else ""
        params.append(max(1, min(int(limit), 500)))
        rows = conn.execute(
            f"SELECT device_id, level, code, message, status, ts FROM alert_log{where_sql} "
            "ORDER BY id DESC LIMIT ?",
            params,
        ).fetchall()
        return [
            {"device_id": r[0], "level": r[1], "code": r[2], "message": r[3], "status": r[4], "timestamp": r[5]}
            for r in rows
        ]
    finally:
        conn.close()


def _evaluate_alert_conditions(device):
    """Return list of (code, level, message, active) for one device snapshot."""
    soil = (device.get("telemetry", {}).get("soil", {}).get("payload", {}) or {})
    climate = (device.get("telemetry", {}).get("climate", {}).get("payload", {}) or {})
    moisture = soil.get("moisture_pct")
    temperature = climate.get("air_temperature_c")
    conditions = []
    if isinstance(moisture, (int, float)):
        if moisture < 40:
            conditions.append(("low_moisture", "warning", f"土壤湿度 {moisture:.1f}% 低于 40%，建议灌溉", True))
        else:
            conditions.append(("low_moisture", "warning", f"土壤湿度 {moisture:.1f}% 已恢复至 40% 以上", False))
        if moisture > 70:
            conditions.append(("high_moisture", "warning", f"土壤湿度 {moisture:.1f}% 高于 70%，注意排水", True))
        else:
            conditions.append(("high_moisture", "warning", f"土壤湿度 {moisture:.1f}% 已回落至 70% 以下", False))
    if isinstance(temperature, (int, float)):
        if temperature > 30:
            conditions.append(("high_temperature", "warning", f"空气温度 {temperature:.1f}°C 偏高，建议通风遮阳", True))
        else:
            conditions.append(("high_temperature", "warning", f"空气温度 {temperature:.1f}°C 已回落至 30°C 以下", False))
    return conditions


def alert_evaluator_loop():
    while True:
        time.sleep(ALERT_EVAL_INTERVAL_SECONDS)  # sleep first: module fully loads before first pass
        try:
            with registry_lock:
                devices = {device_id: dict(device) for device_id, device in registry.items()}
            now_ts = utc_now()
            for device_id, device in devices.items():
                for code, level, message, active in _evaluate_alert_conditions(device):
                    key = (device_id, code)
                    previous = alert_states.get(key)
                    if previous is None:
                        # first observation: only log if currently active, to avoid
                        # flooding the log with "cleared" rows on startup
                        if active:
                            _insert_alert(device_id, level, code, message, "active", now_ts)
                        alert_states[key] = active
                    elif previous != active:
                        _insert_alert(device_id, level, code, message, "active" if active else "cleared", now_ts)
                        alert_states[key] = active
        except Exception as exc:
            LOGGER.warning("alert evaluation pass failed: %s", exc)


alert_states = {}
if ALERT_LOGGING_ENABLED:
    Thread(target=alert_evaluator_loop, name="alert-evaluator", daemon=True).start()


init_telemetry_db()


# --- Day 16: user-created plots (persistent "add plot" feature) -------------
def _save_custom_plot(device_id, name, crop, owner_user_id=None):
    """Persist a user-created plot (and its owning account) so it survives API restarts."""
    conn = _telemetry_connect()
    try:
        conn.execute(
            "INSERT INTO custom_plots (device_id, name, crop, created_at, owner_user_id) VALUES (?,?,?,?,?) "
            "ON CONFLICT(device_id) DO UPDATE SET name=excluded.name, crop=excluded.crop"
            + (", owner_user_id=excluded.owner_user_id" if owner_user_id is not None else ""),
            (device_id, name or device_id, crop or "", utc_now(), owner_user_id),
        )
        conn.commit()
    finally:
        conn.close()


def _load_plot_owner(device_id):
    """Read a plot's owner straight from SQLite (used when it is not in the registry)."""
    conn = _telemetry_connect()
    try:
        row = conn.execute(
            "SELECT owner_user_id FROM custom_plots WHERE device_id=?", (device_id,)
        ).fetchone()
    except sqlite3.OperationalError:
        return None
    finally:
        conn.close()
    return row[0] if row and row[0] is not None else None


def _load_custom_plots_into_registry():
    """Re-register persisted user plots after an API restart."""
    conn = _telemetry_connect()
    try:
        rows = conn.execute("SELECT device_id, name, crop, owner_user_id FROM custom_plots").fetchall()
    finally:
        conn.close()
    for device_id, name, crop, owner_user_id in rows:
        with registry_lock:
            if device_id in registry:
                registry[device_id]["plot"] = {"name": name, "crop": crop or ""}
                registry[device_id]["owner_user_id"] = owner_user_id
                continue
            registry[device_id] = {
                "device_id": device_id,
                "telemetry": {},
                "last_seen": None,
                "pump": {"action": "stop", "running": False, "status": "standby",
                         "timestamp": None, "command_id": None},
                "plot": {"name": name, "crop": crop or ""},
                "owner_user_id": owner_user_id,
            }
        LOGGER.info("restored custom plot %s (%s) owner=%s", device_id, name, owner_user_id)


def _seed_builtin_plots_into_registry():
    """Guarantee the three demo plots exist on every boot and belong to admin.

    They used to live in the in-memory registry only, so an API restart dropped
    them — and since the simulator discovers plots through GET /devices, they
    could never come back (deadlock: not in the list → never simulated → never
    re-registered). Seeding them here with an explicit owner also makes their
    multi-tenant ownership unambiguous.
    """
    for device_id, meta in PLOT_META.items():
        with registry_lock:
            existing = registry.get(device_id)
            if existing is not None:
                if existing.get("owner_user_id") is None:
                    existing["owner_user_id"] = BUILTIN_PLOT_OWNER_ID
                existing["plot"] = {"name": meta["name"], "crop": meta["crop"]}
                continue
            registry[device_id] = {
                "device_id": device_id,
                "telemetry": {},
                "last_seen": None,
                "pump": {"action": "stop", "running": False, "status": "standby",
                         "timestamp": None, "command_id": None},
                "plot": {"name": meta["name"], "crop": meta["crop"]},
                "owner_user_id": BUILTIN_PLOT_OWNER_ID,
            }
        for sensor_type in sorted(SENSOR_TYPES.keys()):
            try:
                create_sensor(device_id, sensor_type)
            except ValueError:
                pass  # already seeded
        LOGGER.info("seeded builtin plot %s (%s) owner=%s", device_id, meta["name"],
                    BUILTIN_PLOT_OWNER_ID)


_load_custom_plots_into_registry()


# --- Day 16: sensor CRUD -----------------------------------------------------
def _sensor_to_dict(row):
    if row is None:
        return None
    sensor_id, device_id, sensor_type, status, value_json, unit, last_seen, created_at = row
    value = None
    if value_json:
        try:
            value = json.loads(value_json)
        except (json.JSONDecodeError, TypeError):
            value = None
    meta = SENSOR_TYPES.get(sensor_type, {})
    return {
        "id": sensor_id,
        "device_id": device_id,
        "type": sensor_type,
        "name": meta.get("name", sensor_type),
        "unit": unit or meta.get("unit", ""),
        "status": status,
        "value": value,
        "last_seen": last_seen,
        "created_at": created_at,
    }


def list_sensors_for_device(device_id):
    conn = _telemetry_connect()
    try:
        rows = conn.execute(
            "SELECT id, device_id, type, status, value_json, unit, last_seen, created_at FROM sensors "
            "WHERE device_id=? ORDER BY created_at ASC",
            (device_id,),
        ).fetchall()
    finally:
        conn.close()
    return [_sensor_to_dict(row) for row in rows]


def get_sensor(sensor_id):
    conn = _telemetry_connect()
    try:
        row = conn.execute(
            "SELECT id, device_id, type, status, value_json, unit, last_seen, created_at FROM sensors WHERE id=?",
            (sensor_id,),
        ).fetchone()
    finally:
        conn.close()
    return _sensor_to_dict(row)


def create_sensor(device_id, sensor_type, status=SENSOR_STATUS_CONNECTED):
    if sensor_type not in SENSOR_TYPES:
        raise ValueError(f"unknown sensor type: {sensor_type}")
    sensor_id = uuid4().hex
    created_at = utc_now()
    meta = SENSOR_TYPES[sensor_type]
    conn = _telemetry_connect()
    try:
        try:
            conn.execute(
                "INSERT INTO sensors (id, device_id, type, status, value_json, unit, last_seen, created_at) "
                "VALUES (?,?,?,?,?,?,?,?)",
                (sensor_id, device_id, sensor_type, status, None, meta.get("unit"), None, created_at),
            )
            conn.commit()
        except sqlite3.IntegrityError as exc:
            raise ValueError(f"sensor already exists for {device_id}/{sensor_type}") from exc
    finally:
        conn.close()
    LOGGER.info("sensor created: %s on %s type=%s", sensor_id[:8], device_id, sensor_type)
    return get_sensor(sensor_id)


def update_sensor_status(sensor_id, status):
    if status not in {SENSOR_STATUS_CONNECTED, SENSOR_STATUS_DISCONNECTED}:
        raise ValueError(f"invalid status: {status}")
    conn = _telemetry_connect()
    try:
        cursor = conn.execute("UPDATE sensors SET status=? WHERE id=?", (status, sensor_id))
        conn.commit()
        return cursor.rowcount > 0
    finally:
        conn.close()


def update_sensor_value(sensor_id, value, unit, last_seen):
    conn = _telemetry_connect()
    try:
        conn.execute(
            "UPDATE sensors SET value_json=?, unit=?, last_seen=? WHERE id=?",
            (json.dumps(value, ensure_ascii=False) if value is not None else None, unit, last_seen, sensor_id),
        )
        conn.commit()
    finally:
        conn.close()


def delete_sensor(sensor_id):
    conn = _telemetry_connect()
    try:
        cursor = conn.execute("DELETE FROM sensors WHERE id=?", (sensor_id,))
        conn.commit()
        return cursor.rowcount > 0
    finally:
        conn.close()


def seed_default_sensors_for_device(device_id):
    """Create the canonical 5 sensors if missing. Idempotent."""
    existing = list_sensors_for_device(device_id)
    existing_types = {row["type"] for row in existing}
    created = []
    for sensor_type in SENSOR_TYPES:
        if sensor_type in existing_types:
            continue
        try:
            created.append(create_sensor(device_id, sensor_type))
        except ValueError:
            pass  # raced with another seed; ignore
    return created


# Seed the three demo plots only after the sensor helpers exist (they are defined
# above, while the seed function itself sits next to the registry bootstrapping).
_seed_builtin_plots_into_registry()


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


def _publish_new_plot(device_id: str) -> None:
    """Notify simulators that a new plot was created so they can adopt it
    immediately instead of waiting for the next 30s discovery cycle."""
    try:
        client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2,
                             client_id=f"smart-agri-newplot-{uuid4().hex[:8]}")
        client.connect(MQTT_HOST, MQTT_PORT, keepalive=60)
        client.loop_start()
        info = client.publish(
            "farm/control/new_plot",
            json.dumps({"device_id": device_id, "timestamp": utc_now()}),
            qos=1,
        )
        info.wait_for_publish(timeout=5)
        client.loop_stop()
        client.disconnect()
    except Exception as exc:
        LOGGER.warning("new_plot broadcast failed: %s", exc)


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


# --- Day 16: global MQTT broker configuration (persistent) ------------------
def get_mqtt_broker():
    """Return broker config from SQLite, seeded from env on first call."""
    conn = _telemetry_connect()
    try:
        row = conn.execute(
            "SELECT host, port, username, password, updated_at FROM mqtt_broker WHERE id=1"
        ).fetchone()
    finally:
        conn.close()
    if row:
        return {
            "host": row[0],
            "port": int(row[1]),
            "username": row[2] or "",
            "password": row[3] or "",
            "updated_at": row[4],
            "source": "database",
        }
    config = {
        "host": MQTT_HOST,
        "port": MQTT_PORT,
        "username": MQTT_USERNAME,
        "password": MQTT_PASSWORD,
        "updated_at": utc_now(),
        "source": "env",
    }
    set_mqtt_broker(config["host"], config["port"], config["username"], config["password"])
    return get_mqtt_broker()


def set_mqtt_broker(host, port, username="", password=""):
    conn = _telemetry_connect()
    try:
        conn.execute(
            "INSERT INTO mqtt_broker (id, host, port, username, password, updated_at) "
            "VALUES (1, ?, ?, ?, ?, ?) "
            "ON CONFLICT(id) DO UPDATE SET host=excluded.host, port=excluded.port, "
            "username=excluded.username, password=excluded.password, updated_at=excluded.updated_at",
            (host, int(port), username or None, password or None, utc_now()),
        )
        conn.commit()
    finally:
        conn.close()


def _load_mqtt_broker_into_module():
    """Read the persisted MQTT broker (saved via the API) and override module
    constants so the listener and pump publisher use the configured broker.
    On first launch the row is seeded from environment variables."""
    global MQTT_HOST, MQTT_PORT, MQTT_USERNAME, MQTT_PASSWORD
    config = get_mqtt_broker()
    if config["source"] == "database":
        MQTT_HOST = config["host"]
        MQTT_PORT = config["port"]
        MQTT_USERNAME = config["username"]
        MQTT_PASSWORD = config["password"]


_load_mqtt_broker_into_module()


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
    """Validate sensor and actuator envelopes and update the latest snapshot.

    Supported topics (backward-compatible):
      farm/{device_id}/sensor/{kind}        → legacy "soil"/"climate" payload
      farm/{device_id}/{sensor_id}/telemetry → Day 16 sensor registry payload
      farm/{device_id}/status/pump          → pump confirmation
    """
    parts = message.topic.split("/")
    if len(parts) != 4 or parts[0] != "farm" or parts[1] == "":
        return
    try:
        envelope = json.loads(message.payload.decode("utf-8"))
        device_id = envelope.get("device_id") or parts[1]
        timestamp = envelope.get("timestamp")
        if not isinstance(device_id, str) or not device_id:
            raise ValueError("device_id missing")
    except (UnicodeDecodeError, json.JSONDecodeError, KeyError, TypeError, ValueError) as exc:
        LOGGER.warning("ignored invalid MQTT message on %s: %s", message.topic, exc)
        return

    # A deleted plot's lingering telemetry must NOT re-register the device
    # (registry.setdefault below); drop it until the tombstone expires.
    if device_id in _deleted_plots:
        LOGGER.debug("ignoring telemetry for deleted plot %s", device_id)
        return

    # Day 16: new sensor-registry payload — topic `farm/{device}/{sensor_id}/telemetry`
    if parts[3] == "telemetry":
        sensor_id = parts[2]
        sensor_type = envelope.get("type")
        value = envelope.get("value")
        unit = envelope.get("unit") or ""
        ts = timestamp or utc_now()
        if not isinstance(sensor_id, str) or not isinstance(value, dict):
            LOGGER.warning("ignored malformed sensor payload on %s", message.topic)
            return
        sensor = get_sensor(sensor_id)
        if sensor is None or sensor["device_id"] != device_id:
            LOGGER.info("ignoring telemetry for unknown sensor %s on %s", sensor_id[:8], device_id)
            return
        if sensor["status"] != SENSOR_STATUS_CONNECTED:
            LOGGER.debug("sensor %s disconnected; payload dropped", sensor_id[:8])
            return
        try:
            resolved_type = sensor_type or sensor["type"]
            update_sensor_value(sensor_id, value, unit, ts)
            _store_telemetry(device_id, resolved_type,
                             {"value": value, "unit": unit, "sensor_id": sensor_id, "type": resolved_type},
                             ts)
        except Exception as exc:
            LOGGER.warning("sensor update failed: %s", exc)
            return
        # Keep registry.last_seen fresh so /devices still reports connectivity.
        with registry_lock:
            device = registry.setdefault(device_id, {"device_id": device_id, "telemetry": {}, "last_seen": None,
                                                     "pump": {"action": "stop", "running": False, "status": "standby",
                                                              "timestamp": None, "command_id": None}})
            device["last_seen"] = ts
        return

    # Legacy + pump topics carry the envelope under a `payload` key.
    payload = envelope.get("payload")
    if not isinstance(payload, dict):
        LOGGER.warning("ignored message without payload on %s", message.topic)
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
        client.subscribe("farm/+/+/telemetry", qos=1)  # Day 16 sensor-registry topic
        client.subscribe("farm/+/status/pump", qos=1)
        LOGGER.info("MQTT listener connected to %s:%s", MQTT_HOST, MQTT_PORT)
        client.loop_forever()
    except Exception as exc:  # API remains available when broker is temporarily offline.
        LOGGER.warning("MQTT listener unavailable: %s", exc)


if os.getenv("MQTT_LISTENER_ENABLED", "true").lower() == "true":
    Thread(target=mqtt_listener, name="mqtt-listener", daemon=True).start()


@app.get("/healthz")
def healthz():
    try:
        docs = load_knowledge_base()
    except Exception:  # knowledge base is best-effort for healthz
        docs = []
    return jsonify({
        "status": "ok",
        "service": "api",
        "kb_docs": len(docs),
    })


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
        items = list(registry.values())
    allowed = _accessible_device_ids()
    if allowed is not None:
        items = [device for device in items if device.get("device_id") in allowed]
    enriched = []
    for device in items:
        device_id = device.get("device_id")
        item = dict(device)
        item["plot"] = device.get("plot") or PLOT_META.get(device_id, {})
        owner = device.get("owner_user_id")
        if owner is None and device_id in PLOT_META:
            owner = BUILTIN_PLOT_OWNER_ID
        item["owner_user_id"] = owner
        item["owner_label"] = _owner_label(owner)
        item["sensors"] = list_sensors_for_device(device_id)
        enriched.append(item)
    return jsonify({"items": enriched, "count": len(enriched),
                    "scope": "all" if allowed is None else "own"})


@app.post("/api/v1/devices")
@require_auth("manage_sensors")
def register_device_endpoint():
    body = request.get_json(silent=True) or {}
    device_id = (body.get("device_id") or "").strip()
    name = (body.get("name") or "").strip()
    crop = (body.get("crop") or "").strip()
    if not device_id:
        # Web UI creates plots without choosing an id; generate one for them.
        device_id = f"sim-plot-{uuid4().hex[:8]}"
    # Ownership: the creator owns the plot. Managers may create a plot on behalf
    # of another account by passing owner_user_id explicitly.
    owner_user_id = _current_user_id()
    if _is_manager() and isinstance(body.get("owner_user_id"), int):
        owner_user_id = body["owner_user_id"]
    if device_id in PLOT_META:
        owner_user_id = BUILTIN_PLOT_OWNER_ID
    if not _is_manager() and owner_user_id != BUILTIN_PLOT_OWNER_ID and _plot_owner(device_id) not in (None, owner_user_id):
        return _plot_access_error(device_id)
    with registry_lock:
        if device_id in registry:
            if not _is_manager() and registry[device_id].get("owner_user_id") not in (owner_user_id, None):
                return _plot_access_error(device_id)
            registry[device_id]["owner_user_id"] = registry[device_id].get("owner_user_id") or owner_user_id
            if name or crop:
                _save_custom_plot(device_id, name, crop, registry[device_id]["owner_user_id"])
                registry[device_id]["plot"] = {
                    "name": name or (registry[device_id].get("plot") or {}).get("name") or device_id,
                    "crop": crop or (registry[device_id].get("plot") or {}).get("crop") or "",
                }
            return jsonify({"device_id": device_id, "status": "exists",
                            "owner_user_id": registry[device_id]["owner_user_id"],
                            "plot": registry[device_id].get("plot", {})}), 200
        registry[device_id] = {"device_id": device_id, "telemetry": {}, "last_seen": None,
                               "pump": {"action": "stop", "running": False, "status": "standby",
                                        "timestamp": None, "command_id": None},
                               "plot": {"name": name or device_id, "crop": crop or ""},
                               "owner_user_id": owner_user_id}
    if name or crop:
        _save_custom_plot(device_id, name, crop, owner_user_id)
    _deleted_plots.pop(device_id, None)  # re-created → clear tombstone
    # Seed the 5 default sensor types so the new plot is immediately usable.
    for sensor_type in sorted(SENSOR_TYPES.keys()):
        try:
            create_sensor(device_id, sensor_type)
        except ValueError:
            pass  # already exists
    LOGGER.info("registered new plot %s (%s / %s)", device_id, name, crop)
    # Best-effort: wake the simulator immediately so the plot goes online
    # within ~1s instead of waiting for the next discovery cycle.
    try:
        _publish_new_plot(device_id)
    except Exception:
        pass
    return jsonify({"device_id": device_id, "status": "registered", "owner_user_id": owner_user_id,
                    "plot": {"name": name or device_id, "crop": crop or ""}}), 201


@app.post("/api/v1/devices/<device_id>/sensors")
@require_auth("manage_sensors")
def create_sensor_endpoint(device_id):
    if not _can_access_plot(device_id):
        return _plot_access_error(device_id)
    body = request.get_json(silent=True) or {}
    sensor_type = (body.get("type") or "").strip()
    if not sensor_type:
        return jsonify({"error": "type_required", "allowed": sorted(SENSOR_TYPES.keys())}), 400
    try:
        sensor = create_sensor(device_id, sensor_type)
    except ValueError as exc:
        msg = str(exc)
        if "unknown sensor type" in msg:
            return jsonify({"error": "unknown_sensor_type", "type": sensor_type,
                            "allowed": sorted(SENSOR_TYPES.keys())}), 400
        if "already exists" in msg:
            return jsonify({"error": "sensor_already_exists", "device_id": device_id, "type": sensor_type}), 409
        return jsonify({"error": "invalid_request", "message": msg}), 400
    # Make sure the device exists in registry so /devices picks it up.
    with registry_lock:
        registry.setdefault(device_id, {"device_id": device_id, "telemetry": {}, "last_seen": None,
                                        "pump": {"action": "stop", "running": False, "status": "standby",
                                                 "timestamp": None, "command_id": None}})
    return jsonify(sensor), 201


@app.patch("/api/v1/sensors/<sensor_id>")
@require_auth("manage_sensors")
def patch_sensor_endpoint(sensor_id):
    sensor = get_sensor(sensor_id)
    if sensor is None:
        return jsonify({"error": "sensor_not_found", "sensor_id": sensor_id}), 404
    if not _can_access_plot(sensor["device_id"]):
        return _plot_access_error(sensor["device_id"])
    body = request.get_json(silent=True) or {}
    if "status" in body:
        try:
            update_sensor_status(sensor_id, body["status"])
        except ValueError:
            return jsonify({"error": "invalid_status",
                            "allowed": [SENSOR_STATUS_CONNECTED, SENSOR_STATUS_DISCONNECTED]}), 400
        sensor = get_sensor(sensor_id)
    if sensor is None:
        return jsonify({"error": "sensor_not_found", "sensor_id": sensor_id}), 404
    return jsonify(sensor)


@app.delete("/api/v1/sensors/<sensor_id>")
@require_auth("manage_sensors")
def delete_sensor_endpoint(sensor_id):
    sensor = get_sensor(sensor_id)
    if sensor is None:
        return jsonify({"error": "sensor_not_found", "sensor_id": sensor_id}), 404
    if not _can_access_plot(sensor["device_id"]):
        return _plot_access_error(sensor["device_id"])
    delete_sensor(sensor_id)
    return jsonify({"deleted": sensor_id, "device_id": sensor["device_id"], "type": sensor["type"]})


@app.delete("/api/v1/devices/<device_id>")
@require_auth("manage_sensors")
def delete_plot_endpoint(device_id):
    """Delete a user-created plot: removes registry entry, all its sensors and
    the persisted custom_plots row. Built-in plots (PLOT_META) are protected so
    the demo baseline always exists. A tombstone is left so telemetry still
    sent by the simulator before its next discovery cycle is dropped instead of
    implicitly re-registering the plot (registry.setdefault in the MQTT path)."""
    if device_id in PLOT_META:
        return jsonify({"error": "builtin_plot_cannot_be_deleted",
                        "message": "内置地块（苹果园/梨园/橘园）不可删除，仅可删除自定义地块"}), 403
    if not _can_access_plot(device_id):
        return _plot_access_error(device_id)
    with registry_lock:
        existed = device_id in registry
        registry.pop(device_id, None)
        _deleted_plots[device_id] = time.time()
        # prune tombstones older than 10 minutes
        now = time.time()
        for stale in [k for k, v in _deleted_plots.items() if now - v > 600]:
            _deleted_plots.pop(stale, None)
    conn = _telemetry_connect()
    try:
        cursor = conn.execute("DELETE FROM sensors WHERE device_id=?", (device_id,))
        conn.execute("DELETE FROM custom_plots WHERE device_id=?", (device_id,))
        conn.commit()
        sensor_rows = cursor.rowcount
    finally:
        conn.close()
    if not existed:
        return jsonify({"error": "plot_not_found", "device_id": device_id}), 404
    LOGGER.info("deleted plot %s (%d sensors removed)", device_id, sensor_rows)
    return jsonify({"deleted": device_id, "sensors_removed": sensor_rows})


@app.get("/api/v1/devices/<device_id>/sensors")
@require_auth()
def list_sensors_endpoint(device_id):
    """Convenience read endpoint; /devices already embeds the sensor list per device."""
    if not _can_access_plot(device_id):
        return _plot_access_error(device_id)
    items = list_sensors_for_device(device_id)
    return jsonify({"device_id": device_id, "items": items, "count": len(items)})


@app.get("/api/v1/system/mqtt-broker-presets")
@require_auth()
def list_mqtt_broker_presets():
    """Return a catalog of well-known public MQTT brokers for the dashboard's
    preset dropdown. Farmers/managers pick one and we pre-fill the editor;
    they can still switch to Custom and enter their own host/port."""
    return jsonify({"presets": [
        {
            "id": "tencent-mosquitto",
            "label": "云端 mosquitto（项目自带）",
            "host": "mosquitto",
            "port": 1883,
            "username": "",
            "description": "项目 compose 内的 mosquitto 服务，容器内可达",
        },
        {
            "id": "hivemq-public",
            "label": "HiveMQ Public Broker（演示）",
            "host": "broker.hivemq.com",
            "port": 1883,
            "username": "",
            "description": "HiveMQ 公网免费 broker，端口 1883 仅开放部分 topic；演示用",
        },
        {
            "id": "emqx-public",
            "label": "EMQX Public Broker（演示）",
            "host": "broker.emqx.io",
            "port": 1883,
            "username": "",
            "description": "EMQX 公网免费 broker，演示用",
        },
        {
            "id": "mosquitto-test",
            "label": "test.mosquitto.org（演示）",
            "host": "test.mosquitto.org",
            "port": 1883,
            "username": "",
            "description": "Eclipse Mosquitto 官方测试 broker",
        },
        {
            "id": "custom",
            "label": "自定义 broker",
            "host": "",
            "port": 1883,
            "username": "",
            "description": "填入任意 MQTT broker 地址（host/port/用户名/密码）",
        },
    ]})


@app.get("/api/v1/system/mqtt-broker")
@require_auth()
def get_mqtt_broker_endpoint():
    """Read the persisted global MQTT broker configuration.

    The frontend dashboard uses this to render the broker editor. The password
    is masked in the response to avoid leaking it back through logs / DevTools;
    a non-empty `password_set` flag indicates whether a password is configured.
    """
    config = get_mqtt_broker()
    return jsonify({
        "host": config["host"],
        "port": config["port"],
        "username": config["username"],
        "password_set": bool(config["password"]),
        "updated_at": config["updated_at"],
        "source": config["source"],
    })


@app.put("/api/v1/system/mqtt-broker")
@require_auth("manage_sensors")
def put_mqtt_broker_endpoint():
    """Persist a new global broker. Changes apply after the API listener and the
    simulator reconnect; for the cloud deployment run a `docker compose up -d
    --force-recreate api simulator` to pick up the new broker."""
    body = request.get_json(silent=True) or {}
    host = (body.get("host") or "").strip()
    if not host:
        return jsonify({"error": "host_required"}), 400
    try:
        port = int(body.get("port") or 1883)
    except (TypeError, ValueError):
        return jsonify({"error": "port_must_be_integer"}), 400
    if not (1 <= port <= 65535):
        return jsonify({"error": "port_out_of_range"}), 400
    username = (body.get("username") or "").strip()
    password = body.get("password") or ""
    if password == "__KEEP__":  # frontend sentinel: keep current password
        existing = get_mqtt_broker()
        password = existing["password"]
    set_mqtt_broker(host, port, username, password)
    return jsonify({"host": host, "port": port, "username": username,
                    "password_set": bool(password), "updated_at": utc_now(),
                    "restart_required": True})


@app.get("/api/v1/devices/<device_id>/telemetry/latest")
@require_auth()
def latest_telemetry(device_id):
    if not _can_access_plot(device_id):
        return _plot_access_error(device_id)
    with registry_lock:
        device = registry.get(device_id)
        if device is None:
            return jsonify({"error": "device_not_found", "device_id": device_id}), 404
        return jsonify(device)


@app.get("/api/v1/devices/<device_id>/pump")
@require_auth()
def pump_status(device_id):
    if not _can_access_plot(device_id):
        return _plot_access_error(device_id)
    return jsonify(_pump_snapshot(device_id))


@app.get("/api/v1/devices/<device_id>/telemetry/history")
@require_auth()
def telemetry_history(device_id):
    if not _can_access_plot(device_id):
        return _plot_access_error(device_id)
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
    if not _can_access_plot(device_id):
        return _plot_access_error(device_id)
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


@app.get("/api/v1/alerts/logs")
@require_auth()
def alerts_logs():
    """Historical alert records (persisted in SQLite) for traceability.
    Optional filters: ?device_id= & ?level= & ?limit= (default 50, max 500)."""
    device_id = request.args.get("device_id") or None
    level = request.args.get("level") or None
    allowed = _accessible_device_ids()
    if device_id is not None and allowed is not None and device_id not in allowed:
        return _plot_access_error(device_id)
    try:
        limit = min(max(int(request.args.get("limit", "50")), 1), 500)
    except ValueError:
        return jsonify({"error": "limit_must_be_integer"}), 400
    try:
        items = list_alerts(device_id=device_id, level=level, limit=limit)
    except Exception as exc:
        LOGGER.warning("alert log read failed: %s", exc)
        return jsonify({"error": "alert_log_unavailable", "message": str(exc)}), 503
    if allowed is not None:
        items = [item for item in items if item.get("device_id") in allowed]
    return jsonify({"items": items, "count": len(items), "filters": {"device_id": device_id, "level": level}})


@app.post("/api/v1/devices/<device_id>/pump")
@require_auth("control_pump")
def pump(device_id):
    if not _can_access_plot(device_id):
        return _plot_access_error(device_id)
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
    if not _can_access_plot(device_id):
        return _plot_access_error(device_id)
    return jsonify(_get_irrigation_rule(device_id))


@app.put("/api/v1/devices/<device_id>/irrigation-rules")
@require_auth("manage_rules")
def put_irrigation_rule(device_id):
    if not _can_access_plot(device_id):
        return _plot_access_error(device_id)
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
    if not _can_access_plot(device_id):
        return _plot_access_error(device_id)
    try:
        limit = min(max(int(request.args.get("limit", "20")), 1), 200)
    except ValueError:
        return jsonify({"error": "limit_must_be_integer"}), 400
    with irrigation_rules_lock:
        items = [dict(event) for event in irrigation_events if event["device_id"] == device_id]
    return jsonify({"device_id": device_id, "count": len(items[-limit:]), "items": items[-limit:]})


@app.post("/api/v1/agent/ask")
@require_auth()
def agent_ask():
    """Irrigation advisor with two modes:
    - mode="kb":   knowledge-base RAG synthesizer (available to all roles)
    - mode="luna": Luna model (OpenAI-compatible) - farmer/manager only
    Optional thinking controls (luna mode): reasoning (bool) toggles the
    chain-of-thought, reasoning_effort selects {"low","medium"}.
    Guests can only use the knowledge-base mode.
    """
    body = request.get_json(silent=True) or {}
    question = (body.get("question") or "").strip()
    if not question:
        return jsonify({"error": "question_required"}), 400
    if len(question) > 500:
        return jsonify({"error": "question_too_long", "max_chars": 500}), 400
    mode = body.get("mode") or "kb"
    if mode not in {"kb", "luna"}:
        return jsonify({"error": "mode_must_be_kb_or_luna"}), 400
    role = (current_user() or {}).get("role")
    if mode == "luna" and role == "guest":
        return jsonify({"error": "luna_requires_privileged_role", "message": "游客模式仅支持知识库问答"}), 403
    reasoning = bool(body.get("reasoning", False))
    reasoning_effort = body.get("reasoning_effort") or "medium"
    if reasoning_effort not in {"low", "medium"}:
        return jsonify({"error": "reasoning_effort_must_be_low_or_medium"}), 400
    history = body.get("history") or []
    if not isinstance(history, list):
        history = []
    history = [h for h in history if isinstance(h, dict) and h.get("question")][-5:]

    device_id = body.get("device_id")
    if not device_id:
        with registry_lock:
            device_id = next(iter(registry.keys()), None)

    history_rows = {}
    with registry_lock:
        if device_id:
            device = registry.get(device_id)
            if device:
                history_rows[device_id] = list(device.get("history", []))
    with irrigation_rules_lock:
        rules_snapshot = {k: dict(v) for k, v in irrigation_rules.items()}

    try:
        result = answer_question(
            question,
            history=history,
            device_id=device_id,
            registry=registry,
            history_rows=history_rows,
            irrigation_rules=rules_snapshot,
            mode=mode,
            reasoning=reasoning,
            reasoning_effort=reasoning_effort,
        )
    except Exception as exc:
        LOGGER.warning("agent ask failed: %s", exc)
        return jsonify({"error": "agent_unavailable", "message": str(exc)}), 503

    result["device_id"] = device_id
    result["question"] = question
    result["reasoning"] = result.get("reasoning")
    result["mode"] = mode
    return jsonify(result)


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
