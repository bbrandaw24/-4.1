"""Day 11 authentication and role-based access control tests."""
import sqlite3

import main
import auth
import pytest


@pytest.fixture()
def client():
    main.app.config["TESTING"] = True
    with main.app.test_client() as test_client:
        yield test_client


@pytest.fixture(autouse=True)
def clean_users():
    conn = sqlite3.connect(auth.DB_PATH)
    conn.execute("DELETE FROM users")
    conn.commit()
    conn.close()
    yield


def _register(client, username, password, role, display_name=None):
    return client.post("/api/v1/auth/register",
                       json={"username": username, "password": password, "role": role,
                             "display_name": display_name})


def _login(client, username, password):
    return client.post("/api/v1/auth/login", json={"username": username, "password": password})


def _guest(client):
    return client.post("/api/v1/auth/guest")


def _bearer(token):
    return {"Authorization": f"Bearer {token}"}


def test_guest_login_and_me(client):
    resp = _guest(client)
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["user"]["role"] == "guest"
    assert data["token"]
    me = client.get("/api/v1/auth/me", headers=_bearer(data["token"]))
    assert me.status_code == 200
    assert me.get_json()["user"]["role"] == "guest"


def test_register_then_login(client):
    assert _register(client, "alice", "secret1", "farmer").status_code == 200
    assert _login(client, "alice", "secret1").status_code == 200
    assert _login(client, "alice", "wrong").status_code == 401


def test_register_validation(client):
    assert _register(client, "ab", "secret1", "farmer").status_code == 400
    assert _register(client, "bob", "123", "farmer").status_code == 400
    assert _register(client, "carol", "secret1", "guest").status_code == 400


def test_duplicate_username_rejected(client):
    assert _register(client, "dave", "secret1", "farmer").status_code == 200
    assert _register(client, "dave", "secret1", "manager").status_code == 409


def test_read_requires_valid_token(client):
    assert client.get("/api/v1/devices").status_code == 401
    token = _guest(client).get_json()["token"]
    assert client.get("/api/v1/devices", headers=_bearer(token)).status_code == 200


def test_control_pump_permission(client):
    farmer = _register(client, "farmer1", "secret1", "farmer").get_json()["token"]
    guest = _guest(client).get_json()["token"]
    # guest cannot operate irrigation
    assert client.post("/api/v1/devices/x/pump", json={"action": "start"},
                       headers=_bearer(guest)).status_code == 403
    # farmer passes auth; without an MQTT broker the command publish fails (503),
    # which still proves authorization succeeded (not 401/403).
    code = client.post("/api/v1/devices/x/pump", json={"action": "start"},
                       headers=_bearer(farmer)).status_code
    assert code in (202, 503)


def test_manage_rules_permission(client):
    farmer = _register(client, "farmer2", "secret1", "farmer").get_json()["token"]
    manager = _register(client, "mgr", "secret1", "manager").get_json()["token"]
    body = {"auto_enabled": True, "start_threshold_pct": 40, "stop_threshold_pct": 55}
    assert client.put("/api/v1/devices/x/irrigation-rules", json=body,
                      headers=_bearer(farmer)).status_code == 403
    assert client.put("/api/v1/devices/x/irrigation-rules", json=body,
                      headers=_bearer(manager)).status_code == 200


def test_upload_permission(client):
    guest = _guest(client).get_json()["token"]
    manager = _register(client, "mgr2", "secret1", "manager").get_json()["token"]
    # guest blocked before the file check
    assert client.post("/api/v1/images", headers=_bearer(guest)).status_code == 403
    # manager passes auth; missing file yields 400 (not 401/403)
    code = client.post("/api/v1/images", headers=_bearer(manager)).status_code
    assert code not in (401, 403)


def test_list_users_permission(client):
    farmer = _register(client, "farmer3", "secret1", "farmer").get_json()["token"]
    manager = _register(client, "mgr3", "secret1", "manager").get_json()["token"]
    assert client.get("/api/v1/auth/users", headers=_bearer(farmer)).status_code == 403
    resp = client.get("/api/v1/auth/users", headers=_bearer(manager))
    assert resp.status_code == 200
    assert "items" in resp.get_json()
