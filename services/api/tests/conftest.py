"""Shared test fixtures: isolated SQLite DB and disabled background threads."""
import os
import sys
import tempfile
from pathlib import Path

os.environ["DB_PATH"] = os.path.join(tempfile.gettempdir(), "smartagri_test_users.db")
os.environ["TELEMETRY_DB"] = os.path.join(tempfile.gettempdir(), "smartagri_test_telemetry.db")
os.environ["AUTH_SEED_DEMO"] = "0"
os.environ["AUTH_SECRET"] = "test-secret"
os.environ["AUTH_TOKEN_MAX_AGE"] = "43200"
os.environ["MQTT_LISTENER_ENABLED"] = "false"
os.environ["IRRIGATION_RULES_ENABLED"] = "false"
os.environ["ALERT_LOGGING_ENABLED"] = "false"

SERVICES_API_APP = str(Path(__file__).resolve().parents[1] / "app")
if SERVICES_API_APP not in sys.path:
    sys.path.insert(0, SERVICES_API_APP)

import main  # noqa: E402
import auth  # noqa: E402
