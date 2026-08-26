"""Day 13 irrigation advisor agent tests (no MQTT broker, no LLM required)."""

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
from agent import (  # noqa: E402
    answer_question,
    collect_live_context,
    load_knowledge_base,
    retrieve,
    synthesize_answer,
    tokenize,
)


@pytest.fixture()
def client():
    main.app.config["TESTING"] = True
    with main.app.test_client() as test_client:
        yield test_client


@pytest.fixture()
def guest_headers(client):
    token = client.post("/api/v1/auth/guest").get_json()["token"]
    return {"Authorization": f"Bearer {token}"}


def _seed_device(device_id, moisture, temperature=24.0, rule=None, history=None):
    """Inject a device + irrigation rule into the API for the agent to observe."""
    with main.registry_lock:
        main.registry[device_id] = {
            "device_id": device_id,
            "last_seen": datetime.now(timezone.utc).isoformat(),
            "telemetry": {
                "soil": {"timestamp": datetime.now(timezone.utc).isoformat(), "payload": {"moisture_pct": moisture}},
                "climate": {"timestamp": datetime.now(timezone.utc).isoformat(), "payload": {"air_temperature_c": temperature, "light_lux": 20000}},
            },
            "pump": {"action": "stop", "running": False, "status": "standby", "timestamp": None, "command_id": None},
            "history": history or [],
        }
    with main.irrigation_rules_lock:
        if rule:
            main.irrigation_rules[device_id] = rule
        else:
            main.irrigation_rules.pop(device_id, None)


def test_tokenize_chinese_bigrams():
    tokens = tokenize("土壤湿度低，怎么灌溉？")
    assert "土壤" in tokens
    assert "壤湿" in tokens
    assert "湿度" in tokens
    assert "灌溉" in tokens
    assert "?" not in tokens


def test_retrieve_ranks_low_moisture_first():
    docs = load_knowledge_base(force=True)
    scored = retrieve("土壤湿度低，作物缺水怎么办", docs, top_k=3)
    assert scored, "retrieval returned no docs"
    topics = [doc.get("topic") for doc, _ in scored]
    assert topics[0] == "低湿度"


def test_synthesize_includes_live_numbers_and_sources():
    docs = load_knowledge_base(force=True)
    scored = retrieve("现在湿度低要浇水吗", docs, top_k=3)
    ctx = collect_live_context(
        device_id="sim-test-agent",
        history={},
        irrigation_rules={"sim-test-agent": {"auto_enabled": True, "start_threshold_pct": 40, "stop_threshold_pct": 55}},
        registry={
            "sim-test-agent": {
                "telemetry": {
                    "soil": {"payload": {"moisture_pct": 25.0}},
                    "climate": {"payload": {"air_temperature_c": 22.0, "light_lux": 20000}},
                },
                "pump": {"running": False, "status": "standby"},
            }
        },
    )
    result = synthesize_answer("现在湿度低要浇水吗", scored, ctx)
    assert "25.0" in result["answer"]
    assert "40" in result["answer"]  # threshold referenced
    assert result["sources"], "no sources returned"
    assert result["context"]["moisture_pct"] == 25.0


def test_agent_ask_endpoint_requires_auth(client):
    response = client.post("/api/v1/agent/ask", json={"question": "湿度低怎么办"})
    assert response.status_code == 401


def test_agent_ask_endpoint_returns_grounded_answer(client, guest_headers):
    device_id = "sim-agent-e2e"
    _seed_device(device_id, moisture=26.0, temperature=33.0,
                 rule={"auto_enabled": False, "start_threshold_pct": 40, "stop_threshold_pct": 55})
    try:
        response = client.post(
            "/api/v1/agent/ask",
            json={"question": "现在土壤太干了，该怎么处理？", "device_id": device_id},
            headers=guest_headers,
        )
        assert response.status_code == 200, response.get_data(as_text=True)
        data = response.get_json()
        assert data["device_id"] == device_id
        assert data["answer_via"] in {"synthesizer", "llm"}
        assert "26.0" in data["answer"]
        assert "33.0" in data["answer"]
        assert data["sources"], "expected at least one cited source"
        top = data["sources"][0]
        assert top["topic"] in {"低湿度", "高温"}
        assert data["context"]["moisture_pct"] == 26.0
        assert data["context"]["air_temperature_c"] == 33.0
    finally:
        with main.registry_lock:
            main.registry.pop(device_id, None)
        with main.irrigation_rules_lock:
            main.irrigation_rules.pop(device_id, None)
