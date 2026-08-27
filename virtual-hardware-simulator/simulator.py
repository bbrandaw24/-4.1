"""独立虚拟硬件模拟器：生成设备传感器数据并发布到可选 MQTT 服务器。"""

from __future__ import annotations

import argparse
import json
import logging
import random
import signal
import sys
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

try:
    import paho.mqtt.client as mqtt
except ImportError:  # pragma: no cover - gives a useful CLI error
    mqtt = None

LOGGER = logging.getLogger("virtual-hardware-simulator")


@dataclass
class Sensor:
    id: str
    type: str
    unit: str
    interval: float = 5.0
    baseline: float = 0.0
    minimum: float = -1_000_000.0
    maximum: float = 1_000_000.0
    drift: float = 0.1
    fields: list[str] = field(default_factory=list)


@dataclass
class Device:
    id: str
    name: str
    sensors: list[Sensor]


@dataclass
class RuntimeState:
    values: dict[str, float]
    sequence: int = 0


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _number(item: Any, fallback: float) -> float:
    try:
        return float(item)
    except (TypeError, ValueError):
        return fallback


def load_config(source: str | Path | list[dict[str, Any]]) -> list[Device]:
    """Load independent simulator config; no API/database is contacted."""
    if isinstance(source, (str, Path)):
        payload = json.loads(Path(source).read_text(encoding="utf-8"))
        raw_devices = payload.get("devices", payload) if isinstance(payload, dict) else payload
    else:
        raw_devices = source
    if not isinstance(raw_devices, list) or not raw_devices:
        raise ValueError("配置必须是非空 devices 数组")

    devices: list[Device] = []
    for raw_device in raw_devices:
        if not isinstance(raw_device, dict) or not raw_device.get("id"):
            raise ValueError("每个设备必须包含 id")
        raw_sensors = raw_device.get("sensors", [])
        if not isinstance(raw_sensors, list) or not raw_sensors:
            raise ValueError(f"设备 {raw_device['id']} 必须包含至少一个传感器")
        sensors: list[Sensor] = []
        for raw_sensor in raw_sensors:
            if not isinstance(raw_sensor, dict) or not raw_sensor.get("id") or not raw_sensor.get("type"):
                raise ValueError(f"设备 {raw_device['id']} 存在无效传感器")
            minimum = _number(raw_sensor.get("min"), -1_000_000.0)
            maximum = _number(raw_sensor.get("max"), 1_000_000.0)
            if minimum > maximum:
                raise ValueError(f"传感器 {raw_sensor['id']} 的 min 不能大于 max")
            fields = raw_sensor.get("fields", [])
            if not isinstance(fields, list):
                raise ValueError(f"传感器 {raw_sensor['id']} 的 fields 必须是数组")
            sensors.append(Sensor(
                id=str(raw_sensor["id"]), type=str(raw_sensor["type"]),
                unit=str(raw_sensor.get("unit", "")),
                interval=max(0.1, _number(raw_sensor.get("interval"), 5.0)),
                baseline=_number(raw_sensor.get("baseline"), minimum),
                minimum=minimum, maximum=maximum,
                drift=max(0.0, _number(raw_sensor.get("drift"), 0.1)),
                fields=[str(value) for value in fields],
            ))
        devices.append(Device(id=str(raw_device["id"]), name=str(raw_device.get("name", raw_device["id"])), sensors=sensors))
    return devices


def create_states(devices: list[Device]) -> dict[tuple[str, str], RuntimeState]:
    return {(device.id, sensor.id): RuntimeState({"value": sensor.baseline}) for device in devices for sensor in device.sensors}


def next_sensor_value(sensor: Sensor, state: RuntimeState) -> dict[str, Any]:
    value = state.values["value"] + random.gauss(0, sensor.drift)
    value = max(sensor.minimum, min(sensor.maximum, value))
    state.values["value"] = value
    state.sequence += 1
    if sensor.type == "pump_state":
        return {"running": bool(round(value))}
    if sensor.type == "npk" and len(sensor.fields) >= 3:
        nitrogen = max(sensor.minimum, min(sensor.maximum, value + random.gauss(0, sensor.drift)))
        phosphorus = max(sensor.minimum, min(sensor.maximum, value * 0.42 + random.gauss(0, sensor.drift)))
        potassium = max(sensor.minimum, min(sensor.maximum, value * 1.45 + random.gauss(0, sensor.drift)))
        return {sensor.fields[0]: round(nitrogen, 4), sensor.fields[1]: round(phosphorus, 4), sensor.fields[2]: round(potassium, 4)}
    if sensor.fields:
        return {name: round(value, 4) for name in sensor.fields}
    return {"value": round(value, 4)}


def build_message(device: Device, sensor: Sensor, state: RuntimeState, topic_prefix: str) -> tuple[str, str]:
    value = next_sensor_value(sensor, state)
    payload = {
        "message_id": str(uuid.uuid4()),
        "device_id": device.id,
        "device_name": device.name,
        "sensor_id": sensor.id,
        "sensor_type": sensor.type,
        "value": value,
        "unit": sensor.unit,
        "sequence": state.sequence,
        "timestamp": utc_now(),
    }
    prefix = topic_prefix.strip("/")
    topic = f"{prefix}/{device.id}/{sensor.id}/telemetry" if prefix else f"{device.id}/{sensor.id}/telemetry"
    return topic, json.dumps(payload, ensure_ascii=False, separators=(",", ":"))


def create_mqtt_client(args: argparse.Namespace):
    if mqtt is None:
        raise RuntimeError("缺少 paho-mqtt，请先执行: python -m pip install -r requirements.txt")
    client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id=args.client_id)
    if args.username:
        client.username_pw_set(args.username, args.password or None)
    client.reconnect_delay_set(min_delay=args.reconnect_min, max_delay=args.reconnect_max)
    return client


def publish_loop(args: argparse.Namespace, devices: list[Device]) -> None:
    states = create_states(devices)
    client = None
    if not args.dry_run:
        client = create_mqtt_client(args)
        while True:
            try:
                client.connect(args.host, args.port, keepalive=60)
                break
            except Exception as exc:
                LOGGER.warning("MQTT 连接失败 %s:%s：%s；%.1f 秒后重试", args.host, args.port, exc, args.reconnect_min)
                time.sleep(args.reconnect_min)
        client.loop_start()
        LOGGER.info("已连接 MQTT %s:%s", args.host, args.port)

    next_publish = {(device.id, sensor.id): 0.0 for device in devices for sensor in device.sensors}
    stopping = False

    def stop(_signum, _frame):
        nonlocal stopping
        stopping = True

    signal.signal(signal.SIGINT, stop)
    signal.signal(signal.SIGTERM, stop)
    try:
        while not stopping:
            now = time.monotonic()
            for device in devices:
                for sensor in device.sensors:
                    key = (device.id, sensor.id)
                    if now < next_publish[key]:
                        continue
                    topic, payload = build_message(device, sensor, states[key], args.topic_prefix)
                    if args.dry_run:
                        print(f"{topic} {payload}", flush=True)
                    else:
                        info = client.publish(topic, payload, qos=args.qos, retain=args.retain)
                        if info.rc != mqtt.MQTT_ERR_SUCCESS:
                            LOGGER.warning("发布失败 topic=%s rc=%s", topic, info.rc)
                        else:
                            LOGGER.info("已发布 %s", topic)
                    next_publish[key] = now + sensor.interval
            if args.once:
                break
            time.sleep(min(0.2, max(0.02, args.tick)))
    finally:
        if client is not None:
            client.loop_stop()
            client.disconnect()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="独立虚拟硬件模拟器：将设备传感器数据发布到 MQTT")
    parser.add_argument("--config", default="devices.json", help="设备配置 JSON 文件")
    parser.add_argument("--host", default="localhost", help="MQTT 服务器地址")
    parser.add_argument("--port", type=int, default=1883, help="MQTT 服务器端口")
    parser.add_argument("--username", default="", help="MQTT 用户名，可选")
    parser.add_argument("--password", default="", help="MQTT 密码，可选")
    parser.add_argument("--topic-prefix", default="farm", help="Topic 前缀，默认 farm")
    parser.add_argument("--client-id", default="virtual-hardware-simulator", help="MQTT Client ID")
    parser.add_argument("--qos", type=int, choices=(0, 1, 2), default=1)
    parser.add_argument("--retain", action="store_true", help="保留最后一条消息")
    parser.add_argument("--once", action="store_true", help="每个传感器只发送一次后退出")
    parser.add_argument("--dry-run", action="store_true", help="只打印 Topic 和 JSON，不连接 MQTT")
    parser.add_argument("--tick", type=float, default=0.2, help="调度检查间隔秒数")
    parser.add_argument("--reconnect-min", type=float, default=2.0, help="重连最小等待秒数")
    parser.add_argument("--reconnect-max", type=float, default=60.0, help="重连最大等待秒数")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    try:
        devices = load_config(args.config)
        publish_loop(args, devices)
        return 0
    except (OSError, ValueError, json.JSONDecodeError, RuntimeError) as exc:
        LOGGER.error("模拟器启动失败：%s", exc)
        return 2


if __name__ == "__main__":
    sys.exit(main())
