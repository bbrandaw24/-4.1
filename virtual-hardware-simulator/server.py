"""独立虚拟硬件模拟器的本地 Web 控制台。

支持两种数据源模式：
- 本地模式（默认）：设备列表来自 devices.json 的 devices 数组。
- 平台 API 模式：设备列表来自智慧农业平台 API（devices.json 配置 api 段），
  增删设备/传感器都代理到平台 API，传感器 ID 使用平台生成的 ID，
  遥测发布到共享 MQTT broker 后由平台 API 入库，实现双向管理。
"""

from __future__ import annotations

import json
import logging
import os
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from simulator import load_config

ROOT = Path(__file__).resolve().parent
CONFIG_PATH = ROOT / "devices.json"
RUNTIME_DIR = ROOT / ".runtime"
RUNTIME_CONFIG_PATH = RUNTIME_DIR / "devices.json"
WEB_ROOT = ROOT / "web"
LOGGER = logging.getLogger("virtual-hardware-console")

DEFAULT_MQTT = {
    "host": "localhost",
    "port": 1883,
    "username": "",
    "password": "",
    "topic_prefix": "farm",
    "qos": 1,
    "retain": False,
}

DEFAULT_API = {
    "base_url": "http://127.0.0.1:8010",
    "username": "admin",
    "password": "admin123",
    "sync_seconds": 30,
}

# 智慧农业平台内置地块：由平台自带模拟器负责，虚拟模拟器跳过，避免同传感器双源交替覆盖。
PLATFORM_BUILTIN_DEVICES = {"sim-plot-apple", "sim-plot-pear", "sim-plot-orange"}

# 平台 5 类传感器模拟参数：对齐 services/api main.py 的 SENSOR_TYPES
# （baseline 取 baseline_range 中间值，interval 取 publish_interval_seconds）。
PLATFORM_SENSOR_TYPES: dict[str, dict[str, Any]] = {
    "soil_temperature": {
        "name": "土壤温度", "unit": "°C", "baseline": 23.0, "min": 10.0, "max": 40.0,
        "interval": 30, "fields": ["temperature_c"],
    },
    "soil_ph": {
        "name": "pH", "unit": "", "baseline": 6.3, "min": 5.0, "max": 8.0,
        "interval": 30, "fields": ["ph"],
    },
    "soil_npk": {
        "name": "氮/磷/钾", "unit": "mg/kg", "baseline": 120.0, "min": 20.0, "max": 250.0,
        "interval": 60, "fields": ["nitrogen_mg_kg", "phosphorus_mg_kg", "potassium_mg_kg"],
    },
    "air_humidity": {
        "name": "空气湿度", "unit": "%", "baseline": 65.0, "min": 30.0, "max": 95.0,
        "interval": 15, "fields": ["air_humidity_pct"],
    },
    "soil_conductivity": {
        "name": "电导率", "unit": "mS/cm", "baseline": 1.6, "min": 0.3, "max": 4.0,
        "interval": 30, "fields": ["conductivity_ms_cm"],
    },
}


class ApiClient:
    """智慧农业平台 API 客户端（标准库 urllib，无第三方依赖）。"""

    def __init__(self, base_url: str, username: str, password: str, timeout: float = 8.0) -> None:
        self.base_url = base_url.rstrip("/")
        self.username = username
        self.password = password
        self.timeout = timeout
        self.token: str | None = None
        self.token_expires_at: float = 0.0

    def _post(self, path: str, body: dict[str, Any], token: bool = False) -> dict[str, Any]:
        headers = {"Content-Type": "application/json"}
        if token and self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        req = urllib.request.Request(
            f"{self.base_url}{path}", data=json.dumps(body).encode("utf-8"),
            headers=headers, method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            raw = exc.read().decode("utf-8", errors="replace")
            try:
                parsed = json.loads(raw)
            except json.JSONDecodeError:
                parsed = {"error": f"http_{exc.code}", "message": raw[:200]}
            raise ApiError(exc.code, parsed)

    def _request(self, method: str, path: str, body: dict[str, Any] | None = None) -> dict[str, Any]:
        headers = {"Content-Type": "application/json"}
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        data = json.dumps(body).encode("utf-8") if body is not None else None
        req = urllib.request.Request(f"{self.base_url}{path}", data=data, headers=headers, method=method)
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                raw = resp.read().decode("utf-8")
                return json.loads(raw) if raw else {}
        except urllib.error.HTTPError as exc:
            raw = exc.read().decode("utf-8", errors="replace")
            try:
                parsed = json.loads(raw)
            except json.JSONDecodeError:
                parsed = {"error": f"http_{exc.code}", "message": raw[:200]}
            raise ApiError(exc.code, parsed)

    def login(self) -> None:
        data = self._post("/api/v1/auth/login", {"username": self.username, "password": self.password})
        token = data.get("token")
        if not token:
            raise ApiError(401, {"error": "login_failed", "message": "平台登录未返回 token"})
        self.token = token
        self.token_expires_at = time.time() + 11 * 3600

    def _ensure_token(self) -> None:
        if not self.token or time.time() >= self.token_expires_at:
            self.login()

    def list_devices(self) -> list[dict[str, Any]]:
        self._ensure_token()
        data = self._request("GET", "/api/v1/devices")
        return data.get("items", [])

    def create_device(self, name: str, crop: str) -> dict[str, Any]:
        self._ensure_token()
        return self._request("POST", "/api/v1/devices", {"name": name, "crop": crop})

    def delete_device(self, device_id: str) -> dict[str, Any]:
        self._ensure_token()
        return self._request("DELETE", f"/api/v1/devices/{device_id}")

    def create_sensor(self, device_id: str, sensor_type: str) -> dict[str, Any]:
        self._ensure_token()
        return self._request("POST", f"/api/v1/devices/{device_id}/sensors", {"type": sensor_type})

    def delete_sensor(self, sensor_id: str) -> dict[str, Any]:
        self._ensure_token()
        return self._request("DELETE", f"/api/v1/sensors/{sensor_id}")

    def set_sensor_status(self, sensor_id: str, status: str) -> dict[str, Any]:
        self._ensure_token()
        return self._request("PATCH", f"/api/v1/sensors/{sensor_id}", {"status": status})


class ApiError(Exception):
    def __init__(self, code: int, payload: dict[str, Any]) -> None:
        self.code = code
        self.payload = payload
        message = payload.get("error") or payload.get("message") or f"HTTP {code}"
        super().__init__(str(message))


def api_devices_to_simulator(api_items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """把平台设备列表转成模拟器 devices 结构，跳过内置地块与平台不认识的传感器。"""
    result: list[dict[str, Any]] = []
    for item in api_items:
        device_id = item.get("device_id") or item.get("id")
        if not device_id or device_id in PLATFORM_BUILTIN_DEVICES:
            continue
        sensors: list[dict[str, Any]] = []
        for sensor in item.get("sensors") or []:
            sensor_type = sensor.get("type")
            meta = PLATFORM_SENSOR_TYPES.get(sensor_type)
            if not meta or not sensor.get("id"):
                continue
            sensors.append({
                "id": str(sensor["id"]),
                "type": sensor_type,
                "unit": meta["unit"],
                "baseline": meta["baseline"],
                "min": meta["min"],
                "max": meta["max"],
                "interval": meta["interval"],
                "fields": list(meta["fields"]),
            })
        if not sensors:
            continue
        plot = item.get("plot") or {}
        result.append({
            "id": device_id,
            "name": plot.get("name") or device_id,
            "crop": plot.get("crop") or "",
            "sensors": sensors,
        })
    return result


class SimulatorController:
    def __init__(self) -> None:
        self.lock = threading.RLock()
        self.process: subprocess.Popen[str] | None = None
        self.logs: list[str] = []
        self.log_thread: threading.Thread | None = None
        self._api: ApiClient | None = None
        self._last_sync = 0.0
        self._synced_devices: list[dict[str, Any]] = []
        self._api_ok = False
        self._api_error = ""

    # --- config -------------------------------------------------------------
    def read_config(self) -> dict[str, Any]:
        payload = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
        if isinstance(payload, list):
            return {"api": dict(DEFAULT_API), "mqtt": dict(DEFAULT_MQTT), "devices": payload}
        if not isinstance(payload, dict):
            raise ValueError("配置必须是 JSON 对象")
        mqtt_config = dict(DEFAULT_MQTT)
        mqtt_config.update(payload.get("mqtt") or {})
        api_config = dict(DEFAULT_API)
        api_config.update(payload.get("api") or {})
        result: dict[str, Any] = {"api": api_config, "mqtt": mqtt_config}
        if self.api_enabled(payload):
            self.sync_from_api()
            result["devices"] = self._synced_devices
        else:
            result["devices"] = payload.get("devices", [])
        return result

    @staticmethod
    def api_enabled(payload: dict[str, Any]) -> bool:
        api = payload.get("api")
        return isinstance(api, dict) and bool(api.get("enabled", True)) and bool(str(api.get("base_url", "")).strip())

    def api_mode(self) -> bool:
        try:
            payload = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return False
        return self.api_enabled(payload) if isinstance(payload, dict) else False

    def save_config(self, payload: dict[str, Any]) -> dict[str, Any]:
        if not isinstance(payload, dict):
            raise ValueError("配置必须是 JSON 对象")
        mqtt_config = dict(DEFAULT_MQTT)
        mqtt_config.update(payload.get("mqtt") or {})
        mqtt_config["host"] = str(mqtt_config["host"]).strip()
        mqtt_config["port"] = int(mqtt_config["port"])
        mqtt_config["qos"] = int(mqtt_config["qos"])
        if not mqtt_config["host"]:
            raise ValueError("MQTT 服务器地址不能为空")
        if not 1 <= mqtt_config["port"] <= 65535:
            raise ValueError("MQTT 端口必须在 1-65535 范围内")
        if mqtt_config["qos"] not in (0, 1, 2):
            raise ValueError("MQTT QoS 必须是 0、1 或 2")

        api_config = dict(DEFAULT_API)
        api_config.update(payload.get("api") or {})
        api_config["base_url"] = str(api_config["base_url"]).strip().rstrip("/")
        api_config["username"] = str(api_config.get("username", "")).strip()
        api_config["password"] = str(api_config.get("password", "") or "")
        api_config["sync_seconds"] = max(5, int(api_config.get("sync_seconds", 30)))

        api_on = bool(api_config["base_url"])
        # API 模式下设备列表归平台管理，只保存 mqtt + api 配置；本地模式才保存 devices。
        if api_on:
            saved: dict[str, Any] = {"api": api_config, "mqtt": mqtt_config}
        else:
            devices = payload.get("devices")
            if not isinstance(devices, list):
                raise ValueError("本地模式必须包含 devices 数组")
            load_config(devices)
            saved = {"mqtt": mqtt_config, "devices": devices}
        CONFIG_PATH.write_text(json.dumps(saved, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        return self.read_config()

    # --- platform API sync --------------------------------------------------
    def sync_from_api(self, force: bool = False) -> None:
        payload = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
        if not self.api_enabled(payload):
            return
        api_config = dict(DEFAULT_API)
        api_config.update(payload.get("api") or {})
        sync_seconds = max(5, int(api_config.get("sync_seconds", 30)))
        now = time.time()
        if not force and now - self._last_sync < sync_seconds:
            return
        client = self._get_api_client(api_config)
        try:
            items = client.list_devices()
            self._synced_devices = api_devices_to_simulator(items)
            self._write_runtime_config(api_config)
            self._api_ok = True
            self._api_error = ""
            self._last_sync = now
            self.append_log(f"[平台] 已同步 {len(self._synced_devices)} 个自定义地块（跳过内置地块）")
        except ApiError as exc:
            self._api_ok = False
            self._api_error = f"平台 API 错误 {exc.code}: {exc}"
            self.append_log(f"[平台] 同步失败：{self._api_error}")
        except Exception as exc:  # network / connection errors
            self._api_ok = False
            self._api_error = f"无法连接平台 API：{exc}"
            self.append_log(f"[平台] 同步失败：{self._api_error}")

    def _get_api_client(self, api_config: dict[str, Any]) -> ApiClient:
        if self._api is None or self._api.base_url != api_config["base_url"]:
            self._api = ApiClient(
                api_config["base_url"], api_config["username"], api_config["password"]
            )
        return self._api

    def _write_runtime_config(self, api_config: dict[str, Any]) -> None:
        RUNTIME_DIR.mkdir(parents=True, exist_ok=True)
        runtime = {
            "mqtt": api_config.get("mqtt") or {},
            "devices": self._synced_devices,
        }
        RUNTIME_CONFIG_PATH.write_text(
            json.dumps(runtime, ensure_ascii=False, indent=2), encoding="utf-8"
        )

    # --- local device management (non-API mode) -----------------------------
    def delete_device_local(self, device_id: str) -> dict[str, Any]:
        with self.lock:
            payload = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
            devices = payload.get("devices", [])
            kept = [device for device in devices if str(device.get("id")) != device_id]
            if len(kept) == len(devices):
                raise ValueError(f"设备不存在：{device_id}")
            payload["devices"] = kept
            return self.save_config(payload)

    def delete_sensor_local(self, device_id: str, sensor_id: str) -> dict[str, Any]:
        with self.lock:
            payload = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
            devices = payload.get("devices", [])
            found_device = next((d for d in devices if str(d.get("id")) == device_id), None)
            if found_device is None:
                raise ValueError(f"设备不存在：{device_id}")
            sensors = found_device.get("sensors", [])
            kept = [s for s in sensors if str(s.get("id")) != sensor_id]
            if len(kept) == len(sensors):
                raise ValueError(f"传感器不存在：{sensor_id}")
            if not kept:
                raise ValueError("设备至少需要保留一个传感器")
            found_device["sensors"] = kept
            return self.save_config(payload)

    def add_sensor_local(self, device_id: str, sensor: dict[str, Any]) -> dict[str, Any]:
        with self.lock:
            payload = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
            devices = payload.get("devices", [])
            found_device = next((d for d in devices if str(d.get("id")) == device_id), None)
            if found_device is None:
                raise ValueError(f"设备不存在：{device_id}")
            sensor_id = str(sensor.get("id", "")).strip()
            sensor_type = str(sensor.get("type", "")).strip()
            if not sensor_id or not sensor_type:
                raise ValueError("传感器必须包含 id 和 type")
            if any(str(item.get("id")) == sensor_id for item in found_device["sensors"]):
                raise ValueError(f"传感器 ID 已存在：{sensor_id}")
            found_device["sensors"].append(sensor)
            return self.save_config(payload)

    # --- unified management (proxy to platform API when in API mode) --------
    def add_device(self, name: str, crop: str) -> dict[str, Any]:
        if self.api_mode():
            payload = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
            api_config = dict(DEFAULT_API)
            api_config.update(payload.get("api") or {})
            client = self._get_api_client(api_config)
            try:
                result = client.create_device(name, crop)
            except ApiError as exc:
                raise ValueError(f"平台创建设备失败：{exc}") from exc
            self.append_log(f"[平台] 已创建设备 {result.get('device_id')}（{name or crop or '未命名'}）")
            self.sync_from_api(force=True)
            return self.read_config()
        with self.lock:
            payload = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
            devices = payload.get("devices", [])
            if any(str(d.get("id")) == name for d in devices):
                raise ValueError(f"设备 ID 已存在：{name}")
            device_id = name or f"virtual-device-{len(devices) + 1:02d}"
            devices.append({"id": device_id, "name": name or device_id,
                            "sensors": [{"id": f"{device_id}-sensor-01", "type": "soil_moisture",
                                          "unit": "%", "baseline": 50, "min": 0, "max": 100,
                                          "interval": 5, "fields": ["moisture_pct"]}]})
            payload["devices"] = devices
            return self.save_config(payload)

    def delete_device(self, device_id: str) -> dict[str, Any]:
        if self.api_mode():
            payload = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
            api_config = dict(DEFAULT_API)
            api_config.update(payload.get("api") or {})
            client = self._get_api_client(api_config)
            try:
                client.delete_device(device_id)
            except ApiError as exc:
                if exc.code == 403:
                    raise ValueError("平台内置地块不可删除，仅可删除自定义地块") from exc
                raise ValueError(f"平台删除设备失败：{exc}") from exc
            self.append_log(f"[平台] 已删除设备 {device_id}")
            self.sync_from_api(force=True)
            return self.read_config()
        return self.delete_device_local(device_id)

    def add_sensor(self, device_id: str, sensor_type: str) -> dict[str, Any]:
        if self.api_mode():
            payload = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
            api_config = dict(DEFAULT_API)
            api_config.update(payload.get("api") or {})
            client = self._get_api_client(api_config)
            try:
                client.create_sensor(device_id, sensor_type)
            except ApiError as exc:
                raise ValueError(f"平台添加传感器失败：{exc}") from exc
            self.append_log(f"[平台] 已为 {device_id} 添加传感器 {sensor_type}")
            self.sync_from_api(force=True)
            return self.read_config()
        raise ValueError("本地模式请直接编辑设备配置")

    def delete_sensor(self, sensor_id: str, device_id: str = "") -> dict[str, Any]:
        if self.api_mode():
            payload = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
            api_config = dict(DEFAULT_API)
            api_config.update(payload.get("api") or {})
            client = self._get_api_client(api_config)
            try:
                client.delete_sensor(sensor_id)
            except ApiError as exc:
                raise ValueError(f"平台删除传感器失败：{exc}") from exc
            self.append_log(f"[平台] 已删除传感器 {sensor_id[:8]}…")
            self.sync_from_api(force=True)
            return self.read_config()
        return self.delete_sensor_local(device_id, sensor_id)

    def set_sensor_status(self, sensor_id: str, status: str) -> dict[str, Any]:
        if not self.api_mode():
            raise ValueError("本地模式不支持传感器状态切换")
        if status not in ("connected", "disconnected"):
            raise ValueError("status 必须是 connected 或 disconnected")
        payload = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
        api_config = dict(DEFAULT_API)
        api_config.update(payload.get("api") or {})
        client = self._get_api_client(api_config)
        try:
            client.set_sensor_status(sensor_id, status)
        except ApiError as exc:
            raise ValueError(f"平台更新传感器状态失败：{exc}") from exc
        self.append_log(f"[平台] 传感器 {sensor_id[:8]}… 已{('连接' if status == 'connected' else '断开')}")
        self.sync_from_api(force=True)
        return self.read_config()

    # --- simulator process control -----------------------------------------
    def append_log(self, line: str) -> None:
        with self.lock:
            self.logs.append(line.rstrip())
            self.logs = self.logs[-300:]

    def _capture_output(self) -> None:
        assert self.process is not None
        process = self.process
        assert process.stdout is not None
        for line in process.stdout:
            self.append_log(line)
        return_code = process.wait()
        self.append_log(f"[控制台] 模拟器已退出，返回码 {return_code}")

    def is_running(self) -> bool:
        return self.process is not None and self.process.poll() is None

    def _effective_config_path(self) -> Path:
        """API 模式下使用同步后的运行时配置，本地模式直接用主配置。"""
        if self.api_mode():
            return RUNTIME_CONFIG_PATH
        return CONFIG_PATH

    def start(self) -> None:
        with self.lock:
            if self.is_running():
                return
            if self.api_mode():
                self.sync_from_api(force=True)
                if not self._synced_devices:
                    raise ValueError(f"平台没有可模拟的自定义地块（同步状态：{self._api_error or '无数据'}）")
            config = self.read_config()
            mqtt_config = config["mqtt"]
            config_path = self._effective_config_path()
            args = [
                sys.executable, str(ROOT / "simulator.py"),
                "--config", str(config_path),
                "--host", str(mqtt_config["host"]),
                "--port", str(mqtt_config["port"]),
                "--topic-prefix", str(mqtt_config.get("topic_prefix", "farm")),
                "--qos", str(mqtt_config.get("qos", 1)),
                "--reconnect-min", str(mqtt_config.get("reconnect_min", 2)),
                "--reconnect-max", str(mqtt_config.get("reconnect_max", 60)),
            ]
            if mqtt_config.get("username"):
                args.extend(["--username", str(mqtt_config["username"])])
            if mqtt_config.get("password"):
                args.extend(["--password", str(mqtt_config["password"])])
            if mqtt_config.get("retain"):
                args.append("--retain")
            env = os.environ.copy()
            env["PYTHONUNBUFFERED"] = "1"
            self.logs.clear()
            mode = "平台 API 对齐" if self.api_mode() else "本地"
            self.append_log(f"[控制台] 启动模拟器（{mode}模式），目标 MQTT: {mqtt_config['host']}:{mqtt_config['port']}")
            self.process = subprocess.Popen(
                args, cwd=ROOT, env=env,
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                text=True, encoding="utf-8", errors="replace",
            )
            self.log_thread = threading.Thread(target=self._capture_output, daemon=True)
            self.log_thread.start()

    def stop(self) -> None:
        with self.lock:
            if not self.is_running():
                return
            assert self.process is not None
            self.append_log("[控制台] 正在停止模拟器…")
            self.process.terminate()
            try:
                self.process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait(timeout=2)

    def status(self) -> dict[str, Any]:
        with self.lock:
            config = self.read_config()
            return {
                "running": self.is_running(),
                "pid": self.process.pid if self.is_running() and self.process else None,
                "api_mode": self.api_mode(),
                "api_ok": self._api_ok,
                "api_error": self._api_error,
                "mqtt": config["mqtt"],
                "device_count": len(config["devices"]),
                "logs": self.logs[-100:],
            }

    def preview(self) -> list[str]:
        if self.api_mode():
            self.sync_from_api(force=True)
            if not self._synced_devices:
                raise RuntimeError(f"平台没有可模拟的自定义地块（同步状态：{self._api_error or '无数据'}）")
        config = self.read_config()
        mqtt_config = config["mqtt"]
        config_path = self._effective_config_path()
        args = [
            sys.executable, str(ROOT / "simulator.py"),
            "--config", str(config_path), "--dry-run", "--once",
            "--topic-prefix", str(mqtt_config.get("topic_prefix", "farm")),
        ]
        result = subprocess.run(args, cwd=ROOT, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=20)
        output = (result.stdout + result.stderr).splitlines()
        if result.returncode:
            raise RuntimeError("\n".join(output) or f"预览失败，返回码 {result.returncode}")
        return output


CONTROLLER = SimulatorController()


def json_response(handler: BaseHTTPRequestHandler, data: Any, status: int = 200) -> None:
    raw = json.dumps(data, ensure_ascii=False).encode("utf-8")
    handler.send_response(status)
    handler.send_header("Content-Type", "application/json; charset=utf-8")
    handler.send_header("Content-Length", str(len(raw)))
    handler.send_header("Cache-Control", "no-store")
    handler.end_headers()
    handler.wfile.write(raw)


class ConsoleHandler(BaseHTTPRequestHandler):
    def log_message(self, format: str, *args: Any) -> None:
        LOGGER.info("%s - %s", self.address_string(), format % args)

    def do_GET(self) -> None:
        path = urlparse(self.path).path
        try:
            if path == "/api/config":
                json_response(self, CONTROLLER.read_config())
                return
            if path == "/api/status":
                json_response(self, CONTROLLER.status())
                return
            if path == "/api/devices":
                CONTROLLER.sync_from_api()
                json_response(self, {
                    "api_mode": CONTROLLER.api_mode(),
                    "api_ok": CONTROLLER._api_ok,
                    "api_error": CONTROLLER._api_error,
                    "mqtt": CONTROLLER.read_config()["mqtt"],
                    "devices": CONTROLLER._synced_devices if CONTROLLER.api_mode() else CONTROLLER.read_config()["devices"],
                })
                return
            self.serve_static(path)
        except Exception as exc:
            json_response(self, {"error": str(exc)}, HTTPStatus.BAD_REQUEST)

    def do_POST(self) -> None:
        path = urlparse(self.path).path
        try:
            length = int(self.headers.get("Content-Length", "0"))
            raw = self.rfile.read(length) if length else b"{}"
            body = json.loads(raw.decode("utf-8"))
            if path == "/api/config":
                json_response(self, CONTROLLER.save_config(body))
            elif path == "/api/start":
                CONTROLLER.start()
                json_response(self, CONTROLLER.status())
            elif path == "/api/stop":
                CONTROLLER.stop()
                json_response(self, CONTROLLER.status())
            elif path == "/api/preview":
                json_response(self, {"lines": CONTROLLER.preview()})
            elif path == "/api/device/add":
                json_response(self, CONTROLLER.add_device(str(body.get("name", "")).strip(), str(body.get("crop", "")).strip()))
            elif path == "/api/device/delete":
                json_response(self, CONTROLLER.delete_device(str(body.get("device_id", ""))))
            elif path == "/api/sensor/add":
                if CONTROLLER.api_mode():
                    json_response(self, CONTROLLER.add_sensor(str(body.get("device_id", "")), str(body.get("type", "")).strip()))
                else:
                    json_response(self, CONTROLLER.add_sensor_local(str(body.get("device_id", "")), body.get("sensor") or {}))
            elif path == "/api/sensor/delete":
                json_response(self, CONTROLLER.delete_sensor(
                    str(body.get("sensor_id", "")), str(body.get("device_id", ""))))
            elif path == "/api/sensor/status":
                json_response(self, CONTROLLER.set_sensor_status(
                    str(body.get("sensor_id", "")), str(body.get("status", ""))))
            else:
                json_response(self, {"error": "not_found"}, HTTPStatus.NOT_FOUND)
        except json.JSONDecodeError:
            json_response(self, {"error": "请求不是有效 JSON"}, HTTPStatus.BAD_REQUEST)
        except Exception as exc:
            json_response(self, {"error": str(exc)}, HTTPStatus.BAD_REQUEST)

    def serve_static(self, path: str) -> None:
        relative = "index.html" if path in ("", "/") else path.lstrip("/")
        target = (WEB_ROOT / relative).resolve()
        if WEB_ROOT.resolve() not in target.parents and target != WEB_ROOT.resolve():
            json_response(self, {"error": "forbidden"}, HTTPStatus.FORBIDDEN)
            return
        if not target.is_file():
            json_response(self, {"error": "not_found"}, HTTPStatus.NOT_FOUND)
            return
        content_type = "text/html; charset=utf-8" if target.suffix == ".html" else "text/css; charset=utf-8" if target.suffix == ".css" else "application/javascript; charset=utf-8"
        raw = target.read_bytes()
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)


def main() -> int:
    parser = __import__("argparse").ArgumentParser(description="虚拟硬件模拟器 Web 控制台")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    server = ThreadingHTTPServer((args.host, args.port), ConsoleHandler)
    LOGGER.info("控制台已启动：http://%s:%s", args.host, args.port)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        CONTROLLER.stop()
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
