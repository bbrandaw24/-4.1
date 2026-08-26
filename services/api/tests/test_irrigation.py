"""Day 10 automatic irrigation rule tests (no MQTT broker required)."""

import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

os.environ.setdefault("MQTT_LISTENER_ENABLED", "false")
os.environ.setdefault("IRRIGATION_RULES_ENABLED", "false")

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "app"))

import main  # noqa: E402
import pytest  # noqa: E402


@pytest.fixture()
def client():
    main.app.config["TESTING"] = True
    with main.app.test_client() as test_client:
        yield test_client


@pytest.fixture()
def manager_headers(client):
    client.post("/api/v1/auth/register",
                json={"username": "mgr_irr", "password": "secret1", "role": "manager"})
    token = client.post("/api/v1/auth/login",
                        json={"username": "mgr_irr", "password": "secret1"}).get_json()["token"]
    return {"Authorization": f"Bearer {token}"}


BASE_RULE = {"auto_enabled": True, "start_threshold_pct": 40.0, "stop_threshold_pct": 55.0}


def _rule(**overrides):
    return {**BASE_RULE, **overrides}


def test_rule_disabled_never_triggers():
    assert main.evaluate_irrigation_rule(_rule(auto_enabled=False), 10.0, False, None) is None


def test_low_moisture_triggers_start():
    assert main.evaluate_irrigation_rule(_rule(), 39.9, False, None) == "start"


def test_moisture_at_start_threshold_waits():
    assert main.evaluate_irrigation_rule(_rule(), 40.0, False, None) is None


def test_running_and_dry_enough_stays_on():
    assert main.evaluate_irrigation_rule(_rule(), 45.0, True, None) is None


def test_hysteresis_stop_at_threshold():
    assert main.evaluate_irrigation_rule(_rule(), 55.0, True, None) == "stop"


def test_pending_command_blocks_new_decision():
    assert main.evaluate_irrigation_rule(_rule(), 30.0, False, {"command_id": "x"}) is None


def test_non_numeric_moisture_is_ignored():
    assert main.evaluate_irrigation_rule(_rule(), None, False, None) is None
    assert main.evaluate_irrigation_rule(_rule(), "35", False, None) is None
    assert main.evaluate_irrigation_rule(_rule(), True, False, None) is None


def test_get_rules_returns_defaults(client, manager_headers):
    response = client.get("/api/v1/devices/fresh-device/irrigation-rules", headers=manager_headers)
    assert response.status_code == 200
    body = response.get_json()
    assert body["auto_enabled"] is False
    assert body["start_threshold_pct"] == 40.0
    assert body["stop_threshold_pct"] == 55.0


def test_put_rejects_non_object_body(client, manager_headers):
    assert client.put("/api/v1/devices/d10/irrigation-rules", json=None, headers=manager_headers).status_code == 400
    assert client.put("/api/v1/devices/d10/irrigation-rules", json=[1], headers=manager_headers).status_code == 400


def test_put_rejects_boolean_auto_enabled(client, manager_headers):
    response = client.put("/api/v1/devices/d10/irrigation-rules", json={"auto_enabled": "yes"}, headers=manager_headers)
    assert response.status_code == 400
    assert response.get_json()["error"] == "auto_enabled_must_be_boolean"


def test_put_rejects_threshold_out_of_range(client, manager_headers):
    for key in ("start_threshold_pct", "stop_threshold_pct"):
        response = client.put("/api/v1/devices/d10/irrigation-rules", json={key: 120}, headers=manager_headers)
        assert response.status_code == 400
        assert response.get_json()["error"] == f"{key}_out_of_range"
        response = client.put("/api/v1/devices/d10/irrigation-rules", json={key: "40"}, headers=manager_headers)
        assert response.status_code == 400


def test_put_rejects_negative_cooldown(client, manager_headers):
    response = client.put("/api/v1/devices/d10/irrigation-rules", json={"cooldown_seconds": -5}, headers=manager_headers)
    assert response.status_code == 400


def test_put_rejects_stop_not_above_start(client, manager_headers):
    response = client.put("/api/v1/devices/d10/irrigation-rules",
                          json={"start_threshold_pct": 60, "stop_threshold_pct": 55}, headers=manager_headers)
    assert response.status_code == 400
    assert response.get_json()["error"] == "stop_threshold_must_exceed_start_threshold"
    response = client.put("/api/v1/devices/d10/irrigation-rules",
                          json={"start_threshold_pct": 55, "stop_threshold_pct": 55}, headers=manager_headers)
    assert response.status_code == 400


def test_put_valid_rule_persists(client, manager_headers):
    device_id = "d10-persist"
    response = client.put(f"/api/v1/devices/{device_id}/irrigation-rules",
                          json={"auto_enabled": True, "start_threshold_pct": 38, "stop_threshold_pct": 52,
                                "cooldown_seconds": 90}, headers=manager_headers)
    assert response.status_code == 200
    body = response.get_json()
    assert body["auto_enabled"] is True
    assert body["start_threshold_pct"] == 38.0
    assert body["cooldown_seconds"] == 90.0
    assert body["updated_at"]
    follow_up = client.get(f"/api/v1/devices/{device_id}/irrigation-rules", headers=manager_headers)
    assert follow_up.get_json()["start_threshold_pct"] == 38.0


def test_evaluation_pass_publishes_auto_commands():
    device_id = "d10-auto"
    calls = []

    def fake_publish(target_device_id, action, source="manual"):
        calls.append((target_device_id, action, source))
        return {"command_id": "abc123"}, None

    with main.registry_lock:
        main.irrigation_rules[device_id] = {"auto_enabled": True}
        main.registry[device_id] = {
            "device_id": device_id,
            "last_seen": "2026-08-26T06:00:00+00:00",
            "telemetry": {"soil": {"timestamp": "2026-08-26T06:00:00+00:00", "payload": {"moisture_pct": 31.0}}},
            "pump": {"running": False, "status": "standby"},
        }
    try:
        decisions = main.evaluate_all_irrigation_rules(publish=fake_publish)
        assert ("start", ) == tuple(item[1] for item in calls)
        assert calls[0][2] == "auto"
        assert len(decisions) == 1
    finally:
        with main.registry_lock:
            main.registry.pop(device_id, None)
            main.irrigation_rules.pop(device_id, None)


def test_cooldown_suppresses_repeat_actions():
    device_id = "d10-cooldown"
    calls = []

    def fake_publish(target_device_id, action, source="manual"):
        calls.append(action)
        return {"command_id": "abc123"}, None

    with main.registry_lock:
        main.irrigation_rules[device_id] = {"auto_enabled": True, "cooldown_seconds": 300}
        main.registry[device_id] = {
            "device_id": device_id,
            "last_seen": "2026-08-26T06:00:00+00:00",
            "telemetry": {"soil": {"timestamp": "2026-08-26T06:00:00+00:00", "payload": {"moisture_pct": 20.0}}},
            "pump": {"running": False, "status": "standby"},
        }
    try:
        main.evaluate_all_irrigation_rules(publish=fake_publish)
        main.last_auto_action_at[device_id] = __import__("time").time()  # pretend action just happened
        main.evaluate_all_irrigation_rules(publish=fake_publish)
        assert calls.count("start") == 1
    finally:
        with main.registry_lock:
            main.registry.pop(device_id, None)
            main.irrigation_rules.pop(device_id, None)
        main.last_auto_action_at.pop(device_id, None)


def test_telemetry_history_persists_to_sqlite(client):
    """Sensor MQTT messages are persisted to SQLite and served back via history."""
    device_id = "sim-test-persist"
    token = client.post("/api/v1/auth/guest").get_json()["token"]
    headers = {"Authorization": f"Bearer {token}"}
    envelope = {
        "device_id": device_id,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "payload": {"moisture_pct": 41.5},
    }
    message = SimpleNamespace(
        topic=f"farm/{device_id}/sensor/soil",
        payload=json.dumps(envelope).encode("utf-8"),
    )
    main.on_mqtt_message(None, None, message)
    response = client.get(f"/api/v1/devices/{device_id}/telemetry/history?hours=10", headers=headers)
    assert response.status_code == 200
    data = response.get_json()
    assert data["count"] >= 1
    assert data["items"][0]["kind"] == "soil"
    assert data["items"][0]["payload"]["moisture_pct"] == 41.5
    with main.registry_lock:
        main.registry.pop(device_id, None)
