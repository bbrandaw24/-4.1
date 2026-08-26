"""Alert log persistence + /alerts/logs endpoint tests (no MQTT, no alert thread)."""

import os
import sys
from pathlib import Path

os.environ.setdefault("MQTT_LISTENER_ENABLED", "false")
os.environ.setdefault("IRRIGATION_RULES_ENABLED", "false")
os.environ.setdefault("ALERT_LOGGING_ENABLED", "false")

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "app"))

import main  # noqa: E402
import pytest  # noqa: E402


@pytest.fixture()
def client():
    main.app.config["TESTING"] = True
    with main.app.test_client() as test_client:
        yield test_client


@pytest.fixture()
def guest_headers(client):
    token = client.post("/api/v1/auth/guest").get_json()["token"]
    return {"Authorization": f"Bearer {token}"}


def test_evaluate_alert_conditions():
    device = {
        "telemetry": {
            "soil": {"payload": {"moisture_pct": 35.0}},
            "climate": {"payload": {"air_temperature_c": 32.0}},
        }
    }
    conditions = {code: active for code, _level, _msg, active in main._evaluate_alert_conditions(device)}
    assert conditions["low_moisture"] is True
    assert conditions["high_temperature"] is True
    assert conditions["high_moisture"] is False

    recovered = {
        "telemetry": {
            "soil": {"payload": {"moisture_pct": 55.0}},
            "climate": {"payload": {"air_temperature_c": 24.0}},
        }
    }
    conditions2 = {code: active for code, _level, _msg, active in main._evaluate_alert_conditions(recovered)}
    assert conditions2["low_moisture"] is False
    assert conditions2["high_temperature"] is False


def test_insert_and_list_alerts_roundtrip():
    main._insert_alert("sim-plot-apple", "warning", "low_moisture", "湿度低", "active", "2026-08-26T10:00:00+00:00")
    main._insert_alert("sim-plot-pear", "warning", "high_temperature", "温度高", "cleared", "2026-08-26T10:00:05+00:00")
    items = main.list_alerts(limit=10)
    assert len(items) >= 2
    assert items[0]["device_id"] == "sim-plot-pear"  # newest first
    assert items[0]["status"] == "cleared"
    apple = main.list_alerts(device_id="sim-plot-apple", limit=10)
    assert all(item["device_id"] == "sim-plot-apple" for item in apple)
    warnings = main.list_alerts(level="warning", limit=10)
    assert all(item["level"] == "warning" for item in warnings)


def test_alerts_logs_endpoint_requires_auth(client):
    response = client.get("/api/v1/alerts/logs")
    assert response.status_code == 401


def test_alerts_logs_endpoint_lists_and_filters(client, guest_headers):
    main._insert_alert("sim-plot-apple", "warning", "low_moisture", "土壤湿度偏低", "active", "2026-08-26T11:00:00+00:00")
    response = client.get("/api/v1/alerts/logs?limit=20", headers=guest_headers)
    assert response.status_code == 200
    data = response.get_json()
    assert data["count"] >= 1
    assert data["items"][0]["timestamp"] >= "2026-08-26T11:00:00"
    filtered = client.get("/api/v1/alerts/logs?device_id=sim-plot-apple", headers=guest_headers).get_json()
    assert all(item["device_id"] == "sim-plot-apple" for item in filtered["items"])
