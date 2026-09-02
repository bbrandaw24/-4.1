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
    from .auth import current_user, get_user_by_id, init_db, register_auth_routes, require_auth, _connect as _users_connect
    from .agent import answer_question, load_knowledge_base
except ImportError:  # allow running main.py directly without the package context
    from auth import current_user, get_user_by_id, init_db, register_auth_routes, require_auth, _connect as _users_connect
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
    # created_at: fixed demo start so built-in plots carry a realistic growth
    # age for the report / 3D-farm progress views (they have no custom_plots row).
    "sim-plot-apple": {"name": "苹果园", "crop": "苹果", "created_at": "2026-08-01T00:00:00+00:00"},
    "sim-plot-pear": {"name": "梨园", "crop": "梨", "created_at": "2026-08-01T00:00:00+00:00"},
    "sim-plot-orange": {"name": "橘园", "crop": "橘子", "created_at": "2026-08-01T00:00:00+00:00"},
}

# --- Crop catalog (v15.7.0): canonical list of plantable crops ----------------
# crop 字段不再自由文本：创建地块时必须命中本目录（按 name / alias / key 归一化），
# 存储使用目录中的标准中文名。参考区间（适宜土壤湿度 % / 气温 °C / pH / 目标 NPK
# mg/kg）为后续作物差异化告警阈值、生长进度、PK 评分预留，当前仅作目录元数据。
CROPS = {
    "apple":     {"name": "苹果", "alias": ["苹果"], "type": "果树", "growing_days": 170,
                  "soil_moisture": [45, 65], "air_temp": [15, 28], "ph": [5.5, 7.5], "npk": {"n": 150, "p": 60, "k": 150}},
    "pear":      {"name": "梨", "alias": ["梨", "梨子"], "type": "果树", "growing_days": 175,
                  "soil_moisture": [45, 65], "air_temp": [15, 28], "ph": [6.0, 7.5], "npk": {"n": 150, "p": 60, "k": 150}},
    "orange":    {"name": "橘子", "alias": ["橘子", "橘", "柑橘", "桔子", "橘子园"], "type": "果树", "growing_days": 220,
                  "soil_moisture": [45, 70], "air_temp": [20, 30], "ph": [5.5, 7.0], "npk": {"n": 180, "p": 70, "k": 180}},
    "grape":     {"name": "葡萄", "alias": ["葡萄"], "type": "果树", "growing_days": 155,
                  "soil_moisture": [40, 65], "air_temp": [18, 30], "ph": [6.0, 7.5], "npk": {"n": 120, "p": 50, "k": 140}},
    "strawberry":{"name": "草莓", "alias": ["草莓"], "type": "浆果", "growing_days": 100,
                  "soil_moisture": [55, 75], "air_temp": [15, 25], "ph": [5.5, 6.8], "npk": {"n": 130, "p": 50, "k": 150}},
    "tomato":    {"name": "番茄", "alias": ["番茄", "西红柿"], "type": "茄果", "growing_days": 120,
                  "soil_moisture": [50, 75], "air_temp": [18, 28], "ph": [6.0, 7.0], "npk": {"n": 140, "p": 50, "k": 160}},
    "cucumber":  {"name": "黄瓜", "alias": ["黄瓜"], "type": "瓜类", "growing_days": 70,
                  "soil_moisture": [60, 85], "air_temp": [20, 30], "ph": [6.0, 7.0], "npk": {"n": 120, "p": 45, "k": 140}},
    "chili":     {"name": "辣椒", "alias": ["辣椒", "尖椒"], "type": "茄果", "growing_days": 105,
                  "soil_moisture": [50, 70], "air_temp": [20, 30], "ph": [6.0, 7.0], "npk": {"n": 130, "p": 50, "k": 150}},
    "eggplant":  {"name": "茄子", "alias": ["茄子"], "type": "茄果", "growing_days": 115,
                  "soil_moisture": [55, 75], "air_temp": [22, 30], "ph": [6.0, 7.0], "npk": {"n": 140, "p": 55, "k": 160}},
    "watermelon":{"name": "西瓜", "alias": ["西瓜"], "type": "瓜类", "growing_days": 100,
                  "soil_moisture": [55, 75], "air_temp": [22, 32], "ph": [6.0, 7.5], "npk": {"n": 110, "p": 45, "k": 130}},
    "bokchoy":   {"name": "白菜", "alias": ["白菜", "大白菜", "小白菜"], "type": "叶菜", "growing_days": 70,
                  "soil_moisture": [60, 80], "air_temp": [15, 25], "ph": [6.0, 7.0], "npk": {"n": 160, "p": 60, "k": 140}},
    "spinach":   {"name": "菠菜", "alias": ["菠菜"], "type": "叶菜", "growing_days": 50,
                  "soil_moisture": [60, 80], "air_temp": [10, 25], "ph": [6.0, 7.5], "npk": {"n": 140, "p": 50, "k": 130}},
    "lettuce":   {"name": "生菜", "alias": ["生菜"], "type": "叶菜", "growing_days": 60,
                  "soil_moisture": [55, 75], "air_temp": [12, 24], "ph": [6.0, 7.0], "npk": {"n": 130, "p": 50, "k": 140}},
    "rice":      {"name": "水稻", "alias": ["水稻", "稻谷"], "type": "大田", "growing_days": 135,
                  "soil_moisture": [70, 90], "air_temp": [20, 35], "ph": [5.5, 7.0], "npk": {"n": 150, "p": 70, "k": 120}},
    "wheat":     {"name": "小麦", "alias": ["小麦"], "type": "大田", "growing_days": 210,
                  "soil_moisture": [50, 70], "air_temp": [10, 25], "ph": [6.0, 7.5], "npk": {"n": 120, "p": 50, "k": 100}},
    "corn":      {"name": "玉米", "alias": ["玉米", "包谷"], "type": "大田", "growing_days": 120,
                  "soil_moisture": [50, 75], "air_temp": [18, 30], "ph": [5.5, 7.0], "npk": {"n": 180, "p": 60, "k": 150}},
    "soybean":   {"name": "大豆", "alias": ["大豆", "黄豆"], "type": "大田", "growing_days": 105,
                  "soil_moisture": [50, 70], "air_temp": [18, 28], "ph": [6.0, 7.0], "npk": {"n": 60, "p": 50, "k": 80}},
    "peanut":    {"name": "花生", "alias": ["花生"], "type": "油料", "growing_days": 135,
                  "soil_moisture": [45, 65], "air_temp": [20, 30], "ph": [5.5, 7.0], "npk": {"n": 100, "p": 60, "k": 120}},
}
# 归一化查找表：别名/标准名/key → 标准名
_CROP_LOOKUP = {}
for _key, _meta in CROPS.items():
    for _t in (_meta["name"], * _meta.get("alias", []), _key):
        _CROP_LOOKUP.setdefault(_t, _meta["name"])


def normalize_crop(text):
    """Resolve user input to a canonical crop name from the catalog, or None."""
    if not text:
        return None
    key = str(text).strip()
    return _CROP_LOOKUP.get(key)

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
        # AI farm steward (v15.8.0): per-account automation config + action log.
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS steward_config (
                owner_user_id INTEGER PRIMARY KEY,
                auto_pump_enabled INTEGER NOT NULL DEFAULT 0,
                moisture_threshold_pct REAL NOT NULL DEFAULT 35.0,
                pump_duration_min INTEGER NOT NULL DEFAULT 5,
                auto_tickets_enabled INTEGER NOT NULL DEFAULT 1,
                updated_at TEXT NOT NULL
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS steward_actions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                owner_user_id INTEGER NOT NULL,
                device_id TEXT NOT NULL,
                action_type TEXT NOT NULL,
                reason TEXT NOT NULL,
                detail TEXT,
                created_at TEXT NOT NULL
            )
            """
        )
        conn.execute("CREATE INDEX IF NOT EXISTS idx_steward_actions_owner ON steward_actions(owner_user_id, created_at)")
        # Adoption farm (v15.10.0): a user adopts a crop, the platform creates a
        # dedicated plot owned by that account, and the record below tracks the
        # "adoption certificate" (nickname / crop / date).
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS adoptions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                owner_user_id INTEGER NOT NULL,
                device_id TEXT NOT NULL,
                crop TEXT NOT NULL,
                nickname TEXT,
                adopted_at TEXT NOT NULL
            )
            """
        )
        conn.execute("CREATE INDEX IF NOT EXISTS idx_adoptions_owner ON adoptions(owner_user_id)")
        # v15.11.0: adoption gamification — time acceleration + harvest ledger.
        for col, decl in (("time_scale", "INTEGER NOT NULL DEFAULT 1"),
                          ("harvest_count", "INTEGER NOT NULL DEFAULT 0")):
            cols = [r[1] for r in conn.execute("PRAGMA table_info(adoptions)").fetchall()]
            if col not in cols:
                conn.execute(f"ALTER TABLE adoptions ADD COLUMN {col} {decl}")
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS harvests (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                owner_user_id INTEGER NOT NULL,
                device_id TEXT NOT NULL,
                crop TEXT NOT NULL,
                nickname TEXT,
                health_score REAL NOT NULL,
                grade TEXT NOT NULL,
                grade_label TEXT NOT NULL,
                points INTEGER NOT NULL,
                harvested_at TEXT NOT NULL
            )
            """
        )
        conn.execute("CREATE INDEX IF NOT EXISTS idx_harvests_owner ON harvests(owner_user_id, harvested_at)")
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
        plot = {"name": meta["name"], "crop": meta["crop"],
                "created_at": meta.get("created_at")}
        with registry_lock:
            existing = registry.get(device_id)
            if existing is not None:
                if existing.get("owner_user_id") is None:
                    existing["owner_user_id"] = BUILTIN_PLOT_OWNER_ID
                existing["plot"] = plot
                continue
            registry[device_id] = {
                "device_id": device_id,
                "telemetry": {},
                "last_seen": None,
                "pump": {"action": "stop", "running": False, "status": "standby",
                         "timestamp": None, "command_id": None},
                "plot": plot,
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


@app.get("/api/v1/crops")
@require_auth()
def crops_catalog():
    """Canonical crop catalog (v15.7.0). Returns reference ranges that future
    features (growing progress, crop-specific alerts, PK scoring) will use."""
    items = []
    for key, meta in CROPS.items():
        items.append({
            "key": key, "name": meta["name"], "type": meta["type"],
            "growing_days": meta["growing_days"],
            "soil_moisture": meta["soil_moisture"], "air_temp": meta["air_temp"],
            "ph": meta["ph"], "npk": meta["npk"],
        })
    return jsonify({"items": items, "count": len(items)})


# --- AI farm steward (v15.8.0) ----------------------------------------------
# Per-account automation "butler": the user configures permissions (which
# automations may run) and thresholds (moisture level that triggers a pump);
# a deterministic rule engine executes the decision, and every action is
# written to the steward_actions timeline ("08:32 自动开泵，因为湿度跌破 35%").
# LLM is deliberately NOT in the hot decision path (slow/expensive/unstable);
# it may be layered on later for explaining/ticketing in natural language.
STEWARD_LOOP_SECONDS = 30
STEWARD_TICKET_COOLDOWN_SECONDS = 1800  # per-plot ticket flood protection
STEWARD_ACTION_COOLDOWN_SECONDS = 300   # per-plot per-action pump spam guard

_steward_cooldown: dict[tuple, float] = {}
_steward_pump_started_at: dict[str, float] = {}


def _steward_default_config():
    return {"owner_user_id": None, "auto_pump_enabled": False,
            "moisture_threshold_pct": 35.0, "pump_duration_min": 5,
            "auto_tickets_enabled": True}


def _load_steward_config(owner_user_id):
    conn = _telemetry_connect()
    try:
        row = conn.execute("SELECT * FROM steward_config WHERE owner_user_id=?",
                           (owner_user_id,)).fetchone()
    finally:
        conn.close()
    if row is None:
        cfg = _steward_default_config()
        cfg["owner_user_id"] = owner_user_id
        return cfg
    return {"owner_user_id": row[0], "auto_pump_enabled": bool(row[1]),
            "moisture_threshold_pct": row[2], "pump_duration_min": row[3],
            "auto_tickets_enabled": bool(row[4]), "updated_at": row[5]}


def _save_steward_config(owner_user_id, auto_pump_enabled, moisture_threshold_pct,
                         pump_duration_min, auto_tickets_enabled):
    conn = _telemetry_connect()
    try:
        conn.execute(
            """INSERT INTO steward_config (owner_user_id, auto_pump_enabled,
               moisture_threshold_pct, pump_duration_min, auto_tickets_enabled, updated_at)
               VALUES (?,?,?,?,?,?)
               ON CONFLICT(owner_user_id) DO UPDATE SET
                 auto_pump_enabled=excluded.auto_pump_enabled,
                 moisture_threshold_pct=excluded.moisture_threshold_pct,
                 pump_duration_min=excluded.pump_duration_min,
                 auto_tickets_enabled=excluded.auto_tickets_enabled,
                 updated_at=excluded.updated_at""",
            (owner_user_id, 1 if auto_pump_enabled else 0, float(moisture_threshold_pct),
             int(pump_duration_min), 1 if auto_tickets_enabled else 0, utc_now()),
        )
        conn.commit()
    finally:
        conn.close()


def _insert_steward_action(owner_user_id, device_id, action_type, reason, detail=None):
    conn = _telemetry_connect()
    try:
        conn.execute(
            "INSERT INTO steward_actions (owner_user_id, device_id, action_type, reason, detail, created_at) "
            "VALUES (?,?,?,?,?,?)",
            (owner_user_id, device_id, action_type, reason, detail, utc_now()),
        )
        conn.commit()
    finally:
        conn.close()


def _plot_moisture(device):
    return (device.get("telemetry", {}).get("soil", {}).get("payload", {}) or {}).get("moisture_pct")


def _plot_temperature(device):
    return (device.get("telemetry", {}).get("climate", {}).get("payload", {}) or {}).get("air_temperature_c")


@app.get("/api/v1/steward/config")
@require_auth()
def steward_config_get():
    """The signed-in account's butler config (per-account: isolated by owner)."""
    uid = _current_user_id()
    return jsonify(_load_steward_config(uid))


@app.put("/api/v1/steward/config")
@require_auth("manage_rules")
def steward_config_put():
    """Save automation permissions + thresholds for the signed-in account."""
    body = request.get_json(silent=True) or {}
    if not isinstance(body, dict):
        return jsonify({"error": "json_object_required"}), 400
    if "auto_pump_enabled" in body and not isinstance(body["auto_pump_enabled"], bool):
        return jsonify({"error": "auto_pump_enabled_must_be_boolean"}), 400
    threshold = body.get("moisture_threshold_pct", 35.0)
    try:
        threshold = float(threshold)
    except (TypeError, ValueError):
        return jsonify({"error": "moisture_threshold_pct_must_be_number"}), 400
    if not 10 <= threshold <= 90:
        return jsonify({"error": "moisture_threshold_pct_out_of_range", "range": [10, 90]}), 400
    try:
        duration = int(body.get("pump_duration_min", 5))
    except (TypeError, ValueError):
        return jsonify({"error": "pump_duration_min_must_be_integer"}), 400
    if not 1 <= duration <= 60:
        return jsonify({"error": "pump_duration_min_out_of_range", "range": [1, 60]}), 400
    if "auto_tickets_enabled" in body and not isinstance(body["auto_tickets_enabled"], bool):
        return jsonify({"error": "auto_tickets_enabled_must_be_boolean"}), 400
    uid = _current_user_id()
    _save_steward_config(uid,
                         body.get("auto_pump_enabled", False),
                         threshold, duration,
                         body.get("auto_tickets_enabled", True))
    return jsonify(_load_steward_config(uid))


@app.get("/api/v1/steward/actions")
@require_auth()
def steward_actions_get():
    """Butler timeline, scoped to plots the signed-in account may see."""
    try:
        limit = min(max(int(request.args.get("limit", "50")), 1), 200)
    except ValueError:
        return jsonify({"error": "limit_must_be_integer"}), 400
    allowed = _accessible_device_ids()
    conn = _telemetry_connect()
    try:
        if allowed is None:
            rows = conn.execute(
                "SELECT owner_user_id, device_id, action_type, reason, detail, created_at "
                "FROM steward_actions ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
        else:
            placeholders = ",".join("?" * len(allowed))
            rows = conn.execute(
                f"SELECT owner_user_id, device_id, action_type, reason, detail, created_at "
                f"FROM steward_actions WHERE device_id IN ({placeholders}) ORDER BY id DESC LIMIT ?",
                (*allowed, limit)).fetchall()
    finally:
        conn.close()
    items = [{"owner_user_id": r[0], "device_id": r[1], "action_type": r[2],
              "reason": r[3], "detail": r[4], "created_at": r[5]} for r in rows]
    return jsonify({"items": items, "count": len(items)})


def steward_loop():
    """Deterministic rule engine: read per-account configs, act on live telemetry,
    write every decision to the steward_actions timeline."""
    while True:
        time.sleep(STEWARD_LOOP_SECONDS)  # sleep first: module fully loads first
        try:
            with registry_lock:
                devices = {did: dict(dev) for did, dev in registry.items()}
            conn = _telemetry_connect()
            try:
                rows = conn.execute("SELECT owner_user_id, auto_pump_enabled, "
                                    "moisture_threshold_pct, pump_duration_min, auto_tickets_enabled "
                                    "FROM steward_config WHERE auto_pump_enabled=1 OR auto_tickets_enabled=1").fetchall()
            finally:
                conn.close()
            now = time.time()
            for owner, pump_on, threshold, duration, tickets_on in rows:
                mine = [d for d in devices.values() if d.get("owner_user_id") == owner]
                for device in mine:
                    did = device.get("device_id")
                    moisture = _plot_moisture(device)
                    temperature = _plot_temperature(device)
                    pump_state = (device.get("pump") or {})
                    running = pump_state.get("running") or pump_state.get("status") == "pending"
                    # 1) Automatic irrigation: moisture below threshold → start pump.
                    if pump_on and isinstance(moisture, (int, float)):
                        key = (owner, did, "pump_on")
                        last = _steward_cooldown.get(key, 0.0)
                        if moisture < threshold and not running and (now - last) > STEWARD_ACTION_COOLDOWN_SECONDS:
                            command, error = _publish_pump_command(did, "start", source="steward")
                            if error is None:
                                _steward_cooldown[key] = now
                                _steward_pump_started_at[did] = now
                                _insert_steward_action(
                                    owner, did, "pump_on",
                                    f"检测到土壤湿度 {moisture:.1f}% 跌破阈值 {threshold:.0f}%，自动开启水泵 {duration} 分钟")
                        # 2) Stop after the configured duration.
                        elif running and did in _steward_pump_started_at:
                            elapsed_min = (now - _steward_pump_started_at[did]) / 60.0
                            if elapsed_min >= duration:
                                _publish_pump_command(did, "stop", source="steward")
                                _steward_pump_started_at.pop(did, None)
                                _insert_steward_action(
                                    owner, did, "pump_off",
                                    f"自动灌溉完成（已运行 {duration} 分钟），关闭水泵")
                    # 3) Pest/ticket heuristic: hot + humid for a while → advisory ticket.
                    if tickets_on and isinstance(temperature, (int, float)) and isinstance(moisture, (int, float)):
                        if temperature > 30 and moisture > 80:
                            key = (owner, did, "ticket")
                            if (now - _steward_cooldown.get(key, 0.0)) > STEWARD_TICKET_COOLDOWN_SECONDS:
                                _steward_cooldown[key] = now
                                detail = (f"温度 {temperature:.1f}°C、湿度 {moisture:.1f}% 持续偏高，"
                                          "易诱发白粉病/霜霉病。建议：加强通风、降低密度、"
                                          "傍晚喷施对症防治药剂，连续 3 天复查。")
                                _insert_steward_action(owner, did, "ticket",
                                                       "高温高湿预警：已生成病虫害防治工单", detail)
        except Exception as exc:
            LOGGER.warning("steward loop pass failed: %s", exc)


Thread(target=steward_loop, name="steward-loop", daemon=True).start()


# --- Adoption farm (v15.10.0) -----------------------------------------------
def _create_owned_plot(device_id, name, crop, owner_user_id):
    """Register a new plot in the registry/DB, seed its 5 sensors and wake the
    simulator. Shared by plot creation and crop adoption."""
    with registry_lock:
        if device_id in registry:
            return False
        registry[device_id] = {"device_id": device_id, "telemetry": {}, "last_seen": None,
                               "pump": {"action": "stop", "running": False, "status": "standby",
                                        "timestamp": None, "command_id": None},
                               "plot": {"name": name or device_id, "crop": crop or "",
                                        "created_at": utc_now()},
                               "owner_user_id": owner_user_id}
    _save_custom_plot(device_id, name, crop, owner_user_id)
    _deleted_plots.pop(device_id, None)
    for sensor_type in sorted(SENSOR_TYPES.keys()):
        try:
            create_sensor(device_id, sensor_type)
        except ValueError:
            pass
    try:
        _publish_new_plot(device_id)
    except Exception:
        pass
    LOGGER.info("created owned plot %s (%s / %s) owner=%s", device_id, name, crop, owner_user_id)
    return True


def _adoption_certificate(owner_user_id, device_id, crop, nickname):
    return {"owner_user_id": owner_user_id, "device_id": device_id, "crop": crop,
            "nickname": nickname, "adopted_at": utc_now()}


@app.post("/api/v1/adoptions")
@require_auth("manage_sensors")
def adopt_crop():
    """Adopt a crop from the catalog: the platform creates a dedicated plot
    owned by the signed-in account and issues an adoption certificate."""
    body = request.get_json(silent=True) or {}
    crop = (body.get("crop") or "").strip()
    nickname = (body.get("nickname") or "").strip()
    canonical = normalize_crop(crop)
    if canonical is None:
        return jsonify({
            "error": "crop_not_in_catalog",
            "message": f"暂不支持认养「{crop}」，可选：{'、'.join(c['name'] for c in CROPS.values())}",
            "available": [c["name"] for c in CROPS.values()],
        }), 400
    crop = canonical
    if not nickname:
        nickname = f"我的{crop}地"
    device_id = f"adopt-{uuid4().hex[:8]}"
    owner_user_id = _current_user_id()
    _create_owned_plot(device_id, nickname, crop, owner_user_id)
    conn = _telemetry_connect()
    try:
        conn.execute(
            "INSERT INTO adoptions (owner_user_id, device_id, crop, nickname, adopted_at) VALUES (?,?,?,?,?)",
            (owner_user_id, device_id, crop, nickname, utc_now()),
        )
        conn.commit()
    finally:
        conn.close()
    return jsonify(_adoption_certificate(owner_user_id, device_id, crop, nickname)), 201


@app.get("/api/v1/adoptions")
@require_auth()
def list_adoptions():
    """Adoption certificates for the signed-in account (manager: all)."""
    uid = _current_user_id()
    conn = _telemetry_connect()
    try:
        if _is_manager():
            rows = conn.execute(
                "SELECT owner_user_id, device_id, crop, nickname, adopted_at, time_scale, harvest_count "
                "FROM adoptions ORDER BY id DESC LIMIT 200").fetchall()
        else:
            rows = conn.execute(
                "SELECT owner_user_id, device_id, crop, nickname, adopted_at, time_scale, harvest_count "
                "FROM adoptions WHERE owner_user_id=? ORDER BY id DESC LIMIT 200", (uid,)).fetchall()
    finally:
        conn.close()
    items = []
    for r in rows:
        row = {"owner_user_id": r[0], "device_id": r[1], "crop": r[2], "nickname": r[3],
               "adopted_at": r[4], "time_scale": r[5] or 1, "harvest_count": r[6] or 0}
        items.append(_adoption_certificate_full(row))
    return jsonify({"items": items, "count": len(items)})


@app.delete("/api/v1/adoptions/<device_id>")
@require_auth("manage_sensors")
def unadopt(device_id):
    """Release an adoption: deletes the dedicated plot and its certificate."""
    if not _can_access_plot(device_id):
        return _plot_access_error(device_id)
    conn = _telemetry_connect()
    try:
        row = conn.execute("SELECT owner_user_id FROM adoptions WHERE device_id=?", (device_id,)).fetchone()
    finally:
        conn.close()
    if row is None:
        return jsonify({"error": "adoption_not_found", "device_id": device_id}), 404
    # Mirror delete_plot_endpoint: drop registry entry, sensors, custom_plots row.
    with registry_lock:
        registry.pop(device_id, None)
        _deleted_plots[device_id] = time.time()
    conn = _telemetry_connect()
    try:
        conn.execute("DELETE FROM sensors WHERE device_id=?", (device_id,))
        conn.execute("DELETE FROM custom_plots WHERE device_id=?", (device_id,))
        conn.execute("DELETE FROM adoptions WHERE device_id=?", (device_id,))
        conn.commit()
    finally:
        conn.close()
    return jsonify({"deleted": device_id})


# --- v15.11.0: adoption gamification -----------------------------------------
# Time acceleration (1 minute = time_scale days), harvest + points, points
# leaderboard, and the public one-code trace endpoint. Health scoring mirrors
# the frontend report card (moisture 40 / temperature 30 / ph 20 / offline 10).
_CROP_BASE_POINTS = {
    "apple": 150, "pear": 150, "orange": 160, "grape": 140,
    "strawberry": 120, "tomato": 100, "cucumber": 90, "chili": 100,
    "eggplant": 100, "watermelon": 90, "bokchoy": 70, "spinach": 70,
    "lettuce": 70, "rice": 140, "wheat": 150, "corn": 140, "soybean": 110,
    "peanut": 110,
}
_GRADE_RULES = [
    (80, "excellent", "优秀", 2.0),
    (60, "good", "良好", 1.5),
    (40, "pass", "及格", 1.0),
    (0, "fail", "不及格", 0.5),
]


def _crop_base_points(crop):
    # adoptions store the canonical Chinese name (e.g. "大豆"), while the
    # points table is keyed by catalog key ("soybean") — resolve both.
    for key, meta in CROPS.items():
        if meta["name"] == crop or key == crop:
            return _CROP_BASE_POINTS.get(key, 100)
    return 100


def _deviation_score(value, range_):
    """Mirror frontend deviationScore(): 0 = centered, 1 = at/beyond boundary."""
    try:
        value = float(value)
    except (TypeError, ValueError):
        return None
    if not range_ or range_[0] is None:
        return None
    mid = (range_[0] + range_[1]) / 2
    half = max(0.0001, (range_[1] - range_[0]) / 2)
    return min(1.0, abs(value - mid) / half)


def _plot_health_score(device_id):
    """Health score 0-100 for a device (same algorithm as the frontend report)."""
    with registry_lock:
        device = registry.get(device_id)
    if device is None:
        return None
    plot = device.get("plot") or {}
    crop_key = normalize_crop(plot.get("crop") or "")
    crop = CROPS.get(crop_key)
    if crop is None:
        return None
    soil = (device.get("telemetry", {}).get("soil", {}).get("payload", {}) or {})
    climate = (device.get("telemetry", {}).get("climate", {}).get("payload", {}) or {})
    deduct = 0.0
    m = _deviation_score(soil.get("moisture_pct"), crop["soil_moisture"])
    if m is not None:
        deduct += 40 * m
    t = _deviation_score(climate.get("air_temperature_c"), crop["air_temp"])
    if t is not None:
        deduct += 30 * t
    p = _deviation_score(soil.get("ph"), crop["ph"])
    if p is not None:
        deduct += 20 * p
    sensors = list_sensors_for_device(device_id)
    if sensors:
        offline = sum(1 for s in sensors if s.get("status") != "connected")
        if offline:
            deduct += 10 * (offline / len(sensors))
    return max(0, round(100 - deduct))


def _adoption_row(device_id):
    conn = _telemetry_connect()
    try:
        row = conn.execute(
            "SELECT owner_user_id, device_id, crop, nickname, adopted_at, time_scale, harvest_count "
            "FROM adoptions WHERE device_id=?", (device_id,)).fetchone()
    finally:
        conn.close()
    if row is None:
        return None
    return {"owner_user_id": row[0], "device_id": row[1], "crop": row[2], "nickname": row[3],
            "adopted_at": row[4], "time_scale": row[5] or 1, "harvest_count": row[6] or 0}


def _adoption_growth(row, now_ts=None):
    """1 minute = 1 day at time_scale 1. Returns age/progress/maturity info."""
    now_ts = now_ts if now_ts is not None else time.time()
    try:
        adopted_ts = datetime.fromisoformat(row["adopted_at"]).timestamp()
    except (ValueError, TypeError):
        adopted_ts = now_ts
    elapsed_min = max(0.0, (now_ts - adopted_ts) / 60.0)
    age_days = elapsed_min * (row["time_scale"] or 1)
    growing = CROPS.get(row["crop"], {}).get("growing_days", 120)
    pct = min(100.0, round(age_days / growing * 100, 1))
    return {"age_days": round(age_days, 1), "pct": pct,
            "remaining_days": round(max(0.0, growing - age_days), 1),
            "mature": pct >= 100.0, "growing_days": growing,
            "time_scale": row["time_scale"]}


def _grade_for(score):
    for low, key, label, mult in _GRADE_RULES:
        if score >= low:
            return {"grade": key, "label": label, "multiplier": mult}
    return {"grade": "fail", "label": "不及格", "multiplier": 0.5}


def _adoption_certificate_full(row):
    return {"owner_user_id": row["owner_user_id"], "device_id": row["device_id"],
            "crop": row["crop"], "nickname": row["nickname"], "adopted_at": row["adopted_at"],
            "time_scale": row["time_scale"], "harvest_count": row["harvest_count"],
            "growth": _adoption_growth(row)}


@app.patch("/api/v1/adoptions/<device_id>")
@require_auth("manage_sensors")
def adoption_patch(device_id):
    """Owner/manager adjusts the adoption time scale (1 minute = time_scale days)."""
    if not _can_access_plot(device_id):
        return _plot_access_error(device_id)
    row = _adoption_row(device_id)
    if row is None:
        return jsonify({"error": "adoption_not_found", "device_id": device_id}), 404
    body = request.get_json(silent=True) or {}
    scale = body.get("time_scale")
    if scale is not None:
        try:
            scale = int(scale)
        except (TypeError, ValueError):
            return jsonify({"error": "time_scale_invalid"}), 400
        if not (1 <= scale <= 60):
            return jsonify({"error": "time_scale_out_of_range",
                            "message": "倍率须在 1-60 之间"}), 400
        conn = _telemetry_connect()
        try:
            conn.execute("UPDATE adoptions SET time_scale=? WHERE device_id=?", (scale, device_id))
            conn.commit()
        finally:
            conn.close()
        row["time_scale"] = scale
    return jsonify(_adoption_certificate_full(row))


@app.post("/api/v1/adoptions/<device_id>/harvest")
@require_auth("manage_sensors")
def harvest_adoption(device_id):
    """Harvest a mature adopted crop: score -> grade -> points; replant afterwards."""
    if not _can_access_plot(device_id):
        return _plot_access_error(device_id)
    row = _adoption_row(device_id)
    if row is None:
        return jsonify({"error": "adoption_not_found", "device_id": device_id}), 404
    growth = _adoption_growth(row)
    if not growth["mature"]:
        return jsonify({"error": "crop_not_mature",
                        "message": f"作物尚未成熟（进度 {growth['pct']}%，还需约 {growth['remaining_days']} 天）"}), 409
    score = _plot_health_score(device_id)
    if score is None:
        score = 50
    grade = _grade_for(score)
    points = max(1, round(_crop_base_points(row["crop"]) * grade["multiplier"]))
    conn = _telemetry_connect()
    try:
        conn.execute(
            "INSERT INTO harvests (owner_user_id, device_id, crop, nickname, health_score, "
            "grade, grade_label, points, harvested_at) VALUES (?,?,?,?,?,?,?,?,?)",
            (row["owner_user_id"], device_id, row["crop"], row["nickname"], score,
             grade["grade"], grade["label"], points, utc_now()))
        conn.execute("UPDATE adoptions SET harvest_count=harvest_count+1, adopted_at=? WHERE device_id=?",
                     (utc_now(), device_id))
        conn.commit()
    finally:
        conn.close()
    return jsonify({"harvested": device_id, "crop": row["crop"], "nickname": row["nickname"],
                    "health_score": score, "grade": grade, "points": points,
                    "total_harvests": row["harvest_count"] + 1}), 201


@app.get("/api/v1/adoptions/points")
@require_auth()
def adoption_points():
    """The signed-in account's harvest points summary."""
    uid = _current_user_id()
    conn = _telemetry_connect()
    try:
        total = conn.execute(
            "SELECT COALESCE(SUM(points),0), COUNT(*) FROM harvests WHERE owner_user_id=?",
            (uid,)).fetchone()
        by_crop = conn.execute(
            "SELECT crop, COUNT(*), SUM(points), ROUND(AVG(health_score),1) FROM harvests "
            "WHERE owner_user_id=? GROUP BY crop ORDER BY SUM(points) DESC", (uid,)).fetchall()
    finally:
        conn.close()
    return jsonify({"total_points": total[0] or 0, "total_harvests": total[1] or 0,
                    "by_crop": [{"crop": r[0], "harvests": r[1], "points": r[2] or 0,
                                 "avg_health": r[3]} for r in by_crop]})


def _farmer_display_names(user_ids):
    """Map user ids → account name (display_name, else username) from users.db.

    The leaderboard ranks FARMERS, so it must show the account holder's name,
    not the nickname of whatever plot they happened to adopt.
    """
    ids = [u for u in (user_ids or []) if u is not None]
    if not ids:
        return {}
    try:
        uc = _users_connect()
    except Exception:
        return {}
    try:
        placeholders = ",".join("?" * len(ids))
        rows = uc.execute(
            f"SELECT id, username, display_name FROM users WHERE id IN ({placeholders})",
            tuple(ids)).fetchall()
    except Exception as exc:
        LOGGER.warning("leaderboard: user-name lookup failed: %s", exc)
        return {}
    finally:
        uc.close()
    return {r[0]: (r[2] or r[1] or f"用户{r[0]}") for r in rows}


@app.get("/api/v1/adoptions/leaderboard")
@require_auth()
def adoption_leaderboard():
    """Farmer-only points leaderboard: a single ranking by total harvest points,
    excluding any user whose role is 'manager' (so admin-collected points don't
    pollute the public farmer standings)."""
    manager_ids = set()
    try:
        uc = _users_connect()
        manager_ids = {r[0] for r in uc.execute("SELECT id FROM users WHERE role='manager'").fetchall()}
        uc.close()
    except Exception as exc:
        LOGGER.warning("leaderboard: manager-id lookup failed: %s", exc)
    # Always exclude the built-in admin id too, even if users.db is unreachable.
    manager_ids.add(BUILTIN_PLOT_OWNER_ID)
    conn = _telemetry_connect()
    try:
        if manager_ids:
            placeholders = ",".join("?" * len(manager_ids))
            sql = (
                f"SELECT owner_user_id, SUM(points) AS total, COUNT(*) AS cnt, "
                f"ROUND(AVG(health_score),1) AS avg_health FROM harvests "
                f"WHERE owner_user_id NOT IN ({placeholders}) "
                f"GROUP BY owner_user_id HAVING total > 0 ORDER BY total DESC LIMIT 100")
            rows = conn.execute(sql, tuple(manager_ids)).fetchall()
        else:
            rows = conn.execute(
                "SELECT owner_user_id, SUM(points) AS total, COUNT(*) AS cnt, "
                "ROUND(AVG(health_score),1) AS avg_health FROM harvests "
                "GROUP BY owner_user_id HAVING total > 0 ORDER BY total DESC LIMIT 100").fetchall()
    finally:
        conn.close()
    names = _farmer_display_names([r[0] for r in rows])
    # Show the ACCOUNT name (display_name → username), not the adopted plot's
    # nickname: "老马" is a plot name, the farmer themselves is "123456".
    entries = [{"owner_user_id": uid, "nickname": names.get(uid) or f"用户{uid}",
                "points": total, "harvests": cnt, "avg_health": avg}
               for uid, total, cnt, avg in rows]
    return jsonify({"entries": entries, "scope": "farmers"})


@app.get("/api/v1/trace/<device_id>")
def trace_public(device_id):
    """Public read-only one-code trace for an adopted plot (no auth required)."""
    row = _adoption_row(device_id)
    if row is None:
        return jsonify({"error": "trace_not_found", "device_id": device_id}), 404
    growth = _adoption_growth(row)
    score = _plot_health_score(device_id)
    grade = _grade_for(score) if score is not None else None
    conn = _telemetry_connect()
    try:
        harvests = conn.execute(
            "SELECT grade_label, points, health_score, harvested_at FROM harvests "
            "WHERE device_id=? ORDER BY harvested_at DESC LIMIT 10", (device_id,)).fetchall()
        actions = conn.execute(
            "SELECT action_type, reason, created_at FROM steward_actions "
            "WHERE device_id=? ORDER BY created_at DESC LIMIT 8", (device_id,)).fetchall()
    finally:
        conn.close()
    return jsonify({
        "crop": row["crop"], "nickname": row["nickname"], "adopted_at": row["adopted_at"],
        "time_scale": row["time_scale"], "harvest_count": row["harvest_count"],
        "growth": growth, "health_score": score, "grade": grade,
        "harvests": [{"grade_label": h[0], "points": h[1], "health_score": h[2],
                      "harvested_at": h[3]} for h in harvests],
        "events": [{"action_type": a[0], "reason": a[1], "created_at": a[2]} for a in actions],
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
        plot = dict(device.get("plot") or PLOT_META.get(device_id, {}) or {})
        # v15.9.0: expose planting time so the frontend can compute growing
        # progress against the crop catalog's growing_days.
        plot["created_at"] = _load_plot_created_at(device_id)
        item["plot"] = plot
        owner = device.get("owner_user_id")
        if owner is None and device_id in PLOT_META:
            owner = BUILTIN_PLOT_OWNER_ID
        item["owner_user_id"] = owner
        item["owner_label"] = _owner_label(owner)
        item["sensors"] = list_sensors_for_device(device_id)
        enriched.append(item)
    return jsonify({"items": enriched, "count": len(enriched),
                    "scope": "all" if allowed is None else "own"})


def _load_plot_created_at(device_id):
    """Planting time from custom_plots; falls back to PLOT_META for built-ins."""
    conn = _telemetry_connect()
    try:
        row = conn.execute("SELECT created_at FROM custom_plots WHERE device_id=?",
                           (device_id,)).fetchone()
    finally:
        conn.close()
    if row and row[0]:
        return row[0]
    meta = PLOT_META.get(device_id)
    return (meta or {}).get("created_at")


@app.post("/api/v1/devices")
@require_auth("manage_sensors")
def register_device_endpoint():
    body = request.get_json(silent=True) or {}
    device_id = (body.get("device_id") or "").strip()
    name = (body.get("name") or "").strip()
    crop = (body.get("crop") or "").strip()
    # Crop catalog enforcement (v15.7.0): crop must resolve to a catalog entry.
    # Legacy plots (already stored) keep their original text; new plots must
    # pick a canonical crop, so downstream features (growing progress, PK,
    # crop-specific alerts) have a stable dimension to key on.
    if crop:
        canonical = normalize_crop(crop)
        if canonical is None:
            return jsonify({
                "error": "crop_not_in_catalog",
                "message": f"暂不支持种植「{crop}」，可选：{'、'.join(c['name'] for c in CROPS.values())}",
                "available": [c["name"] for c in CROPS.values()],
            }), 400
        crop = canonical
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


# ---------------------------------------------------------------------------
# v16.5: built-in demo plots are NO LONGER auto-seeded (product decision —
# a fresh deployment starts with an empty farm; farmers create plots from the
# web UI or adopt a crop, which creates a dedicated plot). `_seed_builtin_
# plots_into_registry` is kept for reference; PLOT_META still reserves the
# demo plot ids and their planting-time fallback.
# ---------------------------------------------------------------------------

