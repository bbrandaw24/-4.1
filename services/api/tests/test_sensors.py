"""Day 16 sensor registry tests (no MQTT broker required).

Covers:
- default seed creates 5 sensor types per device
- sensor CRUD round-trips through SQLite
- API endpoints enforce role permissions (guest read-only, farmer/manager CRUD)
- MQTT payload updates sensors.value and rejects disconnected sensors
"""

import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

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
def farmer_headers(client):
    client.post("/api/v1/auth/register",
                json={"username": "farmer_sensor", "password": "secret1", "role": "farmer"})
    token = client.post("/api/v1/auth/login",
                        json={"username": "farmer_sensor", "password": "secret1"}).get_json()["token"]
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture()
def manager_headers(client):
    client.post("/api/v1/auth/register",
                json={"username": "mgr_sensor", "password": "secret1", "role": "manager"})
    return {"Authorization": f"Bearer {client.post('/api/v1/auth/login', json={'username': 'mgr_sensor', 'password': 'secret1'}).get_json()['token']}"}


@pytest.fixture()
def guest_headers(client):
    return {"Authorization": f"Bearer {client.post('/api/v1/auth/guest').get_json()['token']}"}


def test_seed_creates_five_sensor_types(client, farmer_headers):
    device_id = "sim-sensor-seed"
    client.post("/api/v1/devices", json={"device_id": device_id}, headers=farmer_headers)
    main.seed_default_sensors_for_device(device_id)
    sensors = main.list_sensors_for_device(device_id)
    types = {s["type"] for s in sensors}
    assert types == set(main.SENSOR_TYPES.keys())
    assert len(sensors) == 5
    for s in sensors:
        assert s["status"] == "connected"
        assert s["device_id"] == device_id
        assert s["created_at"]


def test_seed_is_idempotent(client, farmer_headers):
    device_id = "sim-sensor-idem"
    main.seed_default_sensors_for_device(device_id)
    main.seed_default_sensors_for_device(device_id)
    sensors = main.list_sensors_for_device(device_id)
    assert len(sensors) == 5


def test_create_sensor_unknown_type_returns_400(client, farmer_headers):
    device_id = "sim-unknown"
    response = client.post(f"/api/v1/devices/{device_id}/sensors",
                            json={"type": "soil_magic"}, headers=farmer_headers)
    assert response.status_code == 400
    assert response.get_json()["error"] == "unknown_sensor_type"


def test_create_sensor_duplicate_returns_409(client, farmer_headers):
    device_id = "sim-dup"
    main.seed_default_sensors_for_device(device_id)
    response = client.post(f"/api/v1/devices/{device_id}/sensors",
                            json={"type": "soil_temperature"}, headers=farmer_headers)
    assert response.status_code == 409
    assert response.get_json()["error"] == "sensor_already_exists"


def test_guest_cannot_create_sensor(client, farmer_headers, guest_headers):
    device_id = "sim-guest-block"
    client.post("/api/v1/devices", json={"device_id": device_id}, headers=farmer_headers)
    response = client.post(f"/api/v1/devices/{device_id}/sensors",
                            json={"type": "air_humidity"}, headers=guest_headers)
    assert response.status_code == 403
    assert response.get_json()["error"] == "forbidden"


def test_devices_endpoint_includes_sensors(client, farmer_headers):
    device_id = "sim-includes"
    client.post("/api/v1/devices", json={"device_id": device_id}, headers=farmer_headers)
    main.seed_default_sensors_for_device(device_id)
    response = client.get("/api/v1/devices", headers=farmer_headers)
    assert response.status_code == 200
    items = response.get_json()["items"]
    match = next((d for d in items if d["device_id"] == device_id), None)
    assert match is not None
    assert len(match["sensors"]) == 5


def test_patch_sensor_disconnect_blocks_telemetry(client, farmer_headers):
    device_id = "sim-patch"
    main.seed_default_sensors_for_device(device_id)
    sensors = main.list_sensors_for_device(device_id)
    target = next(s for s in sensors if s["type"] == "air_humidity")
    response = client.patch(f"/api/v1/sensors/{target['id']}",
                             json={"status": "disconnected"}, headers=farmer_headers)
    assert response.status_code == 200
    assert response.get_json()["status"] == "disconnected"
    # MQTT payload for the disconnected sensor must be ignored
    envelope = {
        "sensor_id": target["id"],
        "device_id": device_id,
        "type": "air_humidity",
        "value": {"air_humidity_pct": 88.0},
        "unit": "%",
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }
    message = SimpleNamespace(
        topic=f"farm/{device_id}/{target['id']}/telemetry",
        payload=json.dumps(envelope).encode("utf-8"),
    )
    main.on_mqtt_message(None, None, message)
    after = main.get_sensor(target["id"])
    assert after["value"] is None  # disconnected → dropped


def test_patch_invalid_status_returns_400(client, farmer_headers):
    device_id = "sim-bad-status"
    main.seed_default_sensors_for_device(device_id)
    sensors = main.list_sensors_for_device(device_id)
    target = sensors[0]
    response = client.patch(f"/api/v1/sensors/{target['id']}",
                             json={"status": "paused"}, headers=farmer_headers)
    assert response.status_code == 400


def test_delete_sensor_removes_row(client, farmer_headers):
    device_id = "sim-del"
    main.seed_default_sensors_for_device(device_id)
    sensors = main.list_sensors_for_device(device_id)
    target = sensors[0]
    response = client.delete(f"/api/v1/sensors/{target['id']}", headers=farmer_headers)
    assert response.status_code == 200
    assert main.get_sensor(target["id"]) is None
    assert len(main.list_sensors_for_device(device_id)) == 4


def test_delete_unknown_sensor_returns_404(client, farmer_headers):
    response = client.delete("/api/v1/sensors/does-not-exist", headers=farmer_headers)
    assert response.status_code == 404


def test_mqtt_payload_updates_connected_sensor(client, farmer_headers):
    device_id = "sim-mqtt-up"
    main.seed_default_sensors_for_device(device_id)
    sensors = main.list_sensors_for_device(device_id)
    target = next(s for s in sensors if s["type"] == "soil_temperature")
    envelope = {
        "sensor_id": target["id"],
        "device_id": device_id,
        "type": "soil_temperature",
        "value": {"temperature_c": 24.7},
        "unit": "°C",
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }
    message = SimpleNamespace(
        topic=f"farm/{device_id}/{target['id']}/telemetry",
        payload=json.dumps(envelope).encode("utf-8"),
    )
    main.on_mqtt_message(None, None, message)
    after = main.get_sensor(target["id"])
    assert after["value"] == {"temperature_c": 24.7}
    assert after["unit"] == "°C"
    assert after["last_seen"]


def test_mqtt_broker_round_trip(client, farmer_headers):
    """PUT a broker configuration; verify GET returns it (password masked)."""
    response = client.get("/api/v1/system/mqtt-broker", headers=farmer_headers)
    assert response.status_code == 200
    body = response.get_json()
    assert "host" in body and "port" in body
    assert "password_set" in body  # never echo raw password
    new_host = f"broker-{int.from_bytes(b'rt', 'big')}.example.com"
    put_response = client.put(
        "/api/v1/system/mqtt-broker",
        json={"host": new_host, "port": 1884, "username": "demo", "password": "secret"},
        headers=farmer_headers,
    )
    assert put_response.status_code == 200
    put_body = put_response.get_json()
    assert put_body["host"] == new_host
    assert put_body["port"] == 1884
    assert put_body["password_set"] is True
    assert put_body["restart_required"] is True
    follow_up = client.get("/api/v1/system/mqtt-broker", headers=farmer_headers)
    follow_body = follow_up.get_json()
    assert follow_body["host"] == new_host
    assert follow_body["password_set"] is True


def test_mqtt_broker_rejects_empty_host(client, farmer_headers):
    response = client.put("/api/v1/system/mqtt-broker",
                          json={"host": "", "port": 1883}, headers=farmer_headers)
    assert response.status_code == 400
    assert response.get_json()["error"] == "host_required"


def test_mqtt_broker_rejects_invalid_port(client, farmer_headers):
    response = client.put("/api/v1/system/mqtt-broker",
                          json={"host": "x", "port": 70000}, headers=farmer_headers)
    assert response.status_code == 400


def test_guest_cannot_modify_broker(client, farmer_headers, guest_headers):
    response = client.put("/api/v1/system/mqtt-broker",
                          json={"host": "x", "port": 1883}, headers=guest_headers)
    assert response.status_code == 403


def test_mqtt_broker_presets_listed(client, farmer_headers):
    """All authenticated users can read the preset catalog."""
    response = client.get("/api/v1/system/mqtt-broker-presets", headers=farmer_headers)
    assert response.status_code == 200
    presets = response.get_json()["presets"]
    assert len(presets) >= 4
    ids = {p["id"] for p in presets}
    assert {"tencent-mosquitto", "hivemq-public", "emqx-public", "custom"} <= ids
    for preset in presets:
        assert "host" in preset and "port" in preset
        assert preset["port"] > 0


def test_mqtt_broker_presets_visible_to_guest(client, guest_headers):
    response = client.get("/api/v1/system/mqtt-broker-presets", headers=guest_headers)
    assert response.status_code == 200