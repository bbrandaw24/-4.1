"""Authentication and role-based access control for the Smart Agriculture API.

State is persisted in a SQLite database (``DB_PATH``) so registered accounts
survive restarts and live on the backend server. Passwords are hashed with
werkzeug; sessions are stateless bearer tokens signed with itsdangerous.

Permission model (three tiers):
  guest   -> view                       (read-only dashboard)
  farmer  -> view + control_pump + manage_sensors
  manager -> view + control_pump + manage_rules + manage_sensors + upload_image + list_users
"""
import os
import re
from datetime import datetime, timezone
from functools import wraps
from pathlib import Path

import sqlite3
from flask import jsonify, g, request
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer
from werkzeug.security import check_password_hash, generate_password_hash

DEFAULT_DB_PATH = Path(__file__).resolve().parent.parent / "data" / "users.db"
DB_PATH = os.getenv("DB_PATH", str(DEFAULT_DB_PATH))
AUTH_SECRET = os.getenv("AUTH_SECRET", "dev-insecure-change-me")
TOKEN_MAX_AGE_SECONDS = int(os.getenv("AUTH_TOKEN_MAX_AGE", "43200"))  # 12h
SEED_DEMO = os.getenv("AUTH_SEED_DEMO", "1") != "0"

ALLOWED_REGISTER_ROLES = ("farmer", "manager")
ROLE_PERMISSIONS = {
    "guest": {"view"},
    "farmer": {"view", "control_pump", "manage_rules", "manage_sensors", "upload_image"},
    "manager": {"view", "control_pump", "manage_rules", "manage_sensors", "upload_image", "list_users"},
}
ROLE_LABELS = {"guest": "游客", "farmer": "农户", "manager": "管理者"}

USERNAME_RE = re.compile(r"^[A-Za-z0-9_]{3,32}$")


def permission_for(role):
    return ROLE_PERMISSIONS.get(role, set())


# --- database helpers -------------------------------------------------------
def _connect():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    """Create the users table and optionally seed demo accounts."""
    path = Path(DB_PATH)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = _connect()
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT UNIQUE NOT NULL,
            password_hash TEXT NOT NULL,
            role TEXT NOT NULL,
            display_name TEXT,
            created_at TEXT NOT NULL
        )
        """
    )
    conn.commit()
    if SEED_DEMO and conn.execute("SELECT COUNT(*) AS c FROM users").fetchone()["c"] == 0:
        _insert_user(conn, "admin", "admin123", "manager", "演示管理员")
        _insert_user(conn, "farmer", "farmer123", "farmer", "演示农户")
        conn.commit()
        print("auth: seeded demo accounts admin/admin123 (manager) and farmer/farmer123 (farmer)", flush=True)
    conn.close()


def _insert_user(conn, username, password, role, display_name):
    conn.execute(
        "INSERT INTO users (username, password_hash, role, display_name, created_at) VALUES (?,?,?,?,?)",
        (username, generate_password_hash(password), role, display_name, datetime.now(timezone.utc).isoformat()),
    )


def create_user(username, password, role, display_name=None):
    """Persist a new account. Returns (user_dict, None) or (None, error_code)."""
    if not USERNAME_RE.match(username or ""):
        return None, "username_invalid"
    if not isinstance(password, str) or len(password) < 6:
        return None, "password_too_short"
    if role not in ALLOWED_REGISTER_ROLES:
        return None, "role_not_allowed"
    conn = _connect()
    try:
        cur = conn.execute(
            "INSERT INTO users (username, password_hash, role, display_name, created_at) VALUES (?,?,?,?,?)",
            (username, generate_password_hash(password), role, display_name or username,
             datetime.now(timezone.utc).isoformat()),
        )
        conn.commit()
        uid = cur.lastrowid
    except sqlite3.IntegrityError:
        conn.close()
        return None, "username_taken"
    row = conn.execute("SELECT id, username, role, display_name FROM users WHERE id=?", (uid,)).fetchone()
    conn.close()
    return dict(row), None


def authenticate(username, password):
    conn = _connect()
    row = conn.execute("SELECT * FROM users WHERE username=?", (username,)).fetchone()
    conn.close()
    if row is None or not check_password_hash(row["password_hash"], password):
        return None
    return {"user_id": row["id"], "username": row["username"], "role": row["role"],
            "display_name": row["display_name"]}


def list_users():
    conn = _connect()
    rows = conn.execute(
        "SELECT id, username, role, display_name, created_at FROM users ORDER BY id"
    ).fetchall()
    conn.close()
    return [dict(row) for row in rows]


def get_user_by_id(uid):
    conn = _connect()
    row = conn.execute(
        "SELECT id, username, role, display_name, created_at FROM users WHERE id=?", (uid,)
    ).fetchone()
    conn.close()
    return dict(row) if row else None


def count_managers():
    conn = _connect()
    try:
        return conn.execute("SELECT COUNT(*) AS c FROM users WHERE role='manager'").fetchone()["c"]
    finally:
        conn.close()


def delete_user(uid):
    """Delete a user by id. Returns ('ok'|'not_found'|'cannot_delete', row or None)."""
    target = get_user_by_id(uid)
    if target is None:
        return "not_found", None
    if target["role"] == "manager":
        return "cannot_delete", target
    conn = _connect()
    conn.execute("DELETE FROM users WHERE id=?", (uid,))
    conn.commit()
    conn.close()
    return "ok", target


# --- token helpers ----------------------------------------------------------
def issue_token(payload):
    return URLSafeTimedSerializer(AUTH_SECRET).dumps(payload)


def verify_token(token):
    try:
        return URLSafeTimedSerializer(AUTH_SECRET).loads(token, max_age=TOKEN_MAX_AGE_SECONDS)
    except (BadSignature, SignatureExpired, TypeError):
        return None


def _payload_from_request():
    header = request.headers.get("Authorization", "")
    if not header.startswith("Bearer "):
        return None
    return verify_token(header.split(" ", 1)[1].strip())


def require_auth(permission=None):
    """Decorator: enforce a valid bearer token, optionally a specific permission."""
    def decorator(view):
        @wraps(view)
        def wrapper(*args, **kwargs):
            payload = _payload_from_request()
            if not payload:
                return jsonify(error="unauthorized"), 401
            if permission and permission not in permission_for(payload.get("role")):
                return jsonify(error="forbidden", required_permission=permission), 403
            g.user = payload
            return view(*args, **kwargs)
        return wrapper
    return decorator


def current_user():
    return getattr(g, "user", None)


def _public_user(payload):
    return {
        "user_id": payload.get("user_id"),
        "username": payload.get("username"),
        "role": payload.get("role"),
        "role_label": ROLE_LABELS.get(payload.get("role"), payload.get("role")),
        "display_name": payload.get("display_name") or payload.get("username"),
        "permissions": sorted(permission_for(payload.get("role"))),
    }


# --- route registration -----------------------------------------------------
def register_auth_routes(app):
    @app.post("/api/v1/auth/register")
    def auth_register():
        body = request.get_json(silent=True) or {}
        username = (body.get("username") or "").strip()
        password = body.get("password") or ""
        role = body.get("role") or ""
        display_name = (body.get("display_name") or "").strip() or None
        user, error = create_user(username, password, role, display_name)
        if error:
            code = {"username_invalid": 400, "password_too_short": 400,
                    "role_not_allowed": 400, "username_taken": 409}.get(error, 400)
            return jsonify(error=error), code
        payload = {"user_id": user["id"], "username": user["username"], "role": user["role"],
                   "display_name": user["display_name"]}
        token = issue_token(payload)
        return jsonify(token=token, expires_in_seconds=TOKEN_MAX_AGE_SECONDS, user=_public_user(payload))

    @app.post("/api/v1/auth/login")
    def auth_login():
        body = request.get_json(silent=True) or {}
        username = (body.get("username") or "").strip()
        password = body.get("password") or ""
        user = authenticate(username, password)
        if user is None:
            return jsonify(error="invalid_credentials"), 401
        token = issue_token(user)
        return jsonify(token=token, expires_in_seconds=TOKEN_MAX_AGE_SECONDS, user=_public_user(user))

    @app.post("/api/v1/auth/guest")
    def auth_guest():
        payload = {"user_id": None, "username": "guest", "role": "guest", "display_name": "游客"}
        token = issue_token(payload)
        return jsonify(token=token, expires_in_seconds=TOKEN_MAX_AGE_SECONDS, user=_public_user(payload))

    @app.get("/api/v1/auth/me")
    @require_auth()
    def auth_me():
        return jsonify(user=_public_user(g.user))

    @app.get("/api/v1/auth/users")
    @require_auth("list_users")
    def auth_users():
        return jsonify(items=list_users(), count=len(list_users()))

    @app.patch("/api/v1/auth/users/<int:uid>")
    @require_auth("list_users")
    def auth_patch_user(uid):
        """Update an account's role / display_name.

        The built-in admin account (id=1) is protected; a manager account may
        be demoted only if at least one manager would remain.
        """
        body = request.get_json(silent=True) or {}
        target = get_user_by_id(uid)
        if target is None:
            return jsonify(error="user_not_found"), 404
        if uid == 1:
            return jsonify(error="cannot_edit_builtin_admin"), 403
        updates = []
        params = []
        new_role = (body.get("role") or "").strip()
        if new_role:
            if new_role not in ALLOWED_REGISTER_ROLES:
                return jsonify(error="role_not_allowed"), 400
            if target["role"] == "manager" and new_role != "manager" and count_managers() <= 1:
                return jsonify(error="last_manager_protected"), 403
            updates.append("role=?")
            params.append(new_role)
        display_name = body.get("display_name")
        if display_name is not None:
            updates.append("display_name=?")
            params.append((display_name or "").strip() or None)
        if not updates:
            return jsonify(error="no_updates"), 400
        conn = _connect()
        try:
            conn.execute(f"UPDATE users SET {', '.join(updates)} WHERE id=?", (*params, uid))
            conn.commit()
        finally:
            conn.close()
        return jsonify(user=get_user_by_id(uid))

    @app.delete("/api/v1/auth/users/<int:uid>")
    @require_auth("list_users")
    def auth_delete_user(uid):
        """Delete a farmer/guest account. Manager accounts cannot be deleted."""
        status, target = delete_user(uid)
        if status == "not_found":
            return jsonify(error="user_not_found"), 404
        if status == "cannot_delete":
            return jsonify(error="cannot_delete_manager"), 403
        return jsonify(deleted=target["username"], user_id=target["id"])
