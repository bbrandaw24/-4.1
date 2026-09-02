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
import uuid
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


def _cleanup_plot(device_id):
    with main.registry_lock:
        main.registry.pop(device_id, None)
    conn = main._telemetry_connect()
    try:
        conn.execute("DELETE FROM custom_plots WHERE device_id=?", (device_id,))
        conn.execute("DELETE FROM sensors WHERE device_id=?", (device_id,))
        conn.commit()
    finally:
        conn.close()


# --- Day 16: add plot (user-created plots, not limited to the 3 built-ins) ---
def test_add_plot_registers_and_seeds_sensors(client, farmer_headers):
    response = client.post("/api/v1/devices",
                           json={"name": "葡萄园", "crop": "葡萄"},
                           headers=farmer_headers)
    assert response.status_code == 201, response.get_data(as_text=True)
    data = response.get_json()
    try:
        assert data["device_id"].startswith("sim-plot-")
        assert data["plot"]["name"] == "葡萄园"
        assert data["plot"]["crop"] == "葡萄"
        # New plot auto-seeds the 5 default sensors.
        sensors = main.list_sensors_for_device(data["device_id"])
        assert {s["type"] for s in sensors} == {"soil_temperature", "soil_ph", "soil_npk",
                                                "air_humidity", "soil_conductivity"}
        # Plot metadata is persisted and restored into the registry.
        main._load_custom_plots_into_registry()
        with main.registry_lock:
            assert main.registry[data["device_id"]]["plot"]["name"] == "葡萄园"
    finally:
        _cleanup_plot(data["device_id"])


def test_add_plot_visible_in_devices_with_plot_meta(client, farmer_headers):
    created = client.post("/api/v1/devices",
                          json={"name": "草莓园", "crop": "草莓"},
                          headers=farmer_headers).get_json()
    try:
        devices = client.get("/api/v1/devices", headers=farmer_headers).get_json()
        item = next((x for x in devices["items"] if x["device_id"] == created["device_id"]), None)
        assert item is not None
        assert item["plot"].get("name") == "草莓园"
        assert item["plot"].get("crop") == "草莓"
        assert len(item.get("sensors") or []) == 5
    finally:
        _cleanup_plot(created["device_id"])


def test_add_plot_existing_device_updates_metadata(client, farmer_headers):
    device_id = f"sim-plot-dup-{uuid.uuid4().hex[:6]}"
    try:
        first = client.post("/api/v1/devices", json={"device_id": device_id, "name": "旧名"},
                            headers=farmer_headers)
        assert first.status_code == 201
        second = client.post("/api/v1/devices", json={"device_id": device_id, "name": "新名", "crop": "桃"},
                             headers=farmer_headers)
        assert second.status_code == 200
        assert second.get_json()["plot"]["name"] == "新名"
    finally:
        _cleanup_plot(device_id)


def test_add_plot_requires_manage_sensors(client, guest_headers):
    response = client.post("/api/v1/devices", json={"name": "X", "crop": "苹果"},
                           headers=guest_headers)
    assert response.status_code == 403


# --- Day 16: delete plot -----------------------------------------------------
def test_delete_custom_plot_removes_everything(client, farmer_headers):
    created = client.post("/api/v1/devices",
                          json={"name": "待删地块", "crop": "番茄"},
                          headers=farmer_headers).get_json()
    device_id = created["device_id"]
    try:
        # sensors exist before deletion
        assert len(main.list_sensors_for_device(device_id)) == 5
        response = client.delete(f"/api/v1/devices/{device_id}", headers=farmer_headers)
        assert response.status_code == 200, response.get_data(as_text=True)
        data = response.get_json()
        assert data["deleted"] == device_id
        assert data["sensors_removed"] == 5
        # registry + sensors + custom_plots all cleaned
        with main.registry_lock:
            assert device_id not in main.registry
        assert main.list_sensors_for_device(device_id) == []
        conn = main._telemetry_connect()
        try:
            row = conn.execute("SELECT 1 FROM custom_plots WHERE device_id=?", (device_id,)).fetchone()
        finally:
            conn.close()
        assert row is None
    finally:
        _cleanup_plot(device_id)


def test_delete_nonexistent_plot_404(client, farmer_headers):
    # v16.4: built-in plots were removed — every plot is user-created, so an
    # unknown id is a plain 404 instead of the old builtin-protection 403.
    response = client.delete("/api/v1/devices/sim-plot-apple", headers=farmer_headers)
    assert response.status_code == 404
    assert response.get_json()["error"] == "plot_not_found"


def test_delete_plot_requires_manage_sensors(client, guest_headers):
    response = client.delete("/api/v1/devices/sim-plot-anything", headers=guest_headers)
    assert response.status_code == 403


def test_deleted_plot_not_revived_by_lingering_telemetry(client, farmer_headers):
    created = client.post("/api/v1/devices",
                          json={"name": "墓碑测试", "crop": "黄瓜"},
                          headers=farmer_headers).get_json()
    device_id = created["device_id"]
    try:
        response = client.delete(f"/api/v1/devices/{device_id}", headers=farmer_headers)
        assert response.status_code == 200
        # Simulator may still publish for up to a discovery cycle: legacy and
        # sensor telemetry must be dropped, not re-register the plot.
        main.on_mqtt_message(None, None, SimpleNamespace(
            topic=f"farm/{device_id}/sensor/soil",
            payload=json.dumps({"device_id": device_id, "payload": {"moisture_pct": 50.0}}).encode()))
        main.on_mqtt_message(None, None, SimpleNamespace(
            topic=f"farm/{device_id}/telemetry",
            payload=json.dumps({"device_id": device_id, "value": {"temperature_c": 23.0},
                                "sensor_id": "deadbeefdeadbeefdeadbeefdeadbeef", "type": "soil_temperature"}).encode()))
        with main.registry_lock:
            assert device_id not in main.registry
    finally:
        _cleanup_plot(device_id)
