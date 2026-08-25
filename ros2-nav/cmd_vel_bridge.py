#!/usr/bin/env python3
"""Subscribe to Nav2 /cmd_vel and forward analog drive vectors to the relay/Pi.

Pi convention (from teleop): forward = negative y, turn = x.
"""

from __future__ import annotations

import json
import math
import os
import ssl
import threading
import time
import urllib.error
import urllib.request
from typing import Any

import rclpy
from geometry_msgs.msg import Twist
from rclpy.node import Node
from rclpy.qos import qos_profile_sensor_data

CMD_VEL_TOPIC = os.environ.get("NAV_CMD_VEL_TOPIC", "/cmd_vel")
DRIVE_URL = os.environ.get(
    "NAV_DRIVE_URL",
    os.environ.get("NAV_DRIVE_BASE_URL", "http://127.0.0.1:8787") + "/api/navigation/drive",
).rstrip("/")
if not DRIVE_URL.endswith("/drive") and "/api/" not in DRIVE_URL:
    DRIVE_URL = DRIVE_URL.rstrip("/") + "/api/navigation/drive"

NAV_API_TOKEN = os.environ.get("NAVIGATION_API_TOKEN", "")
SSL_VERIFY = os.environ.get("NAV_SSL_VERIFY", "false").lower() not in {"0", "false", "no"}
MAX_LINEAR_MPS = float(os.environ.get("NAV_MAX_LINEAR_MPS", "0.28"))
MAX_ANGULAR_RPS = float(os.environ.get("NAV_MAX_ANGULAR_RPS", "0.9"))
KEEPALIVE_HZ = float(os.environ.get("NAV_DRIVE_KEEPALIVE_HZ", "10"))
STALE_STOP_SEC = float(os.environ.get("NAV_CMD_VEL_STALE_SEC", "0.4"))
STATUS_PATH = os.environ.get("NAV_STATUS_FILE_PATH", "/app/lidar/navigation_status.json")


def twist_to_drive(msg: Twist) -> dict[str, float]:
    """Map ROS Twist → Pi {x,y} in [-1,1]. Forward = −y."""
    vx = float(msg.linear.x)
    wz = float(msg.angular.z)
    # Normalize by configured max speeds used in Nav2 params.
    y = -max(-1.0, min(1.0, vx / max(MAX_LINEAR_MPS, 1e-3)))
    x = max(-1.0, min(1.0, wz / max(MAX_ANGULAR_RPS, 1e-3)))
    # Deadzone tiny noise.
    if abs(x) < 0.02:
        x = 0.0
    if abs(y) < 0.02:
        y = 0.0
    return {"x": round(x, 3), "y": round(y, 3)}


class CmdVelBridge(Node):
    def __init__(self) -> None:
        super().__init__("rover_cmd_vel_bridge")
        self._lock = threading.Lock()
        self._latest: Twist | None = None
        self._latest_at = 0.0
        self._last_sent = {"x": 0.0, "y": 0.0}
        self._ssl = None if SSL_VERIFY else ssl._create_unverified_context()
        self.create_subscription(Twist, CMD_VEL_TOPIC, self._on_cmd, qos_profile_sensor_data)
        self.create_timer(1.0 / max(KEEPALIVE_HZ, 1.0), self._tick)
        self.get_logger().info(
            f"cmd_vel bridge topic={CMD_VEL_TOPIC} drive={DRIVE_URL} "
            f"max_v={MAX_LINEAR_MPS} max_w={MAX_ANGULAR_RPS}"
        )

    def _on_cmd(self, msg: Twist) -> None:
        with self._lock:
            self._latest = msg
            self._latest_at = time.monotonic()

    def _tick(self) -> None:
        with self._lock:
            msg = self._latest
            age = time.monotonic() - self._latest_at
        if msg is None or age > STALE_STOP_SEC:
            drive = {"x": 0.0, "y": 0.0}
        else:
            drive = twist_to_drive(msg)
        if drive != self._last_sent or (drive["x"] or drive["y"]):
            self._post({"drive": drive})
            self._last_sent = drive
        self._write_status(drive, age if msg else None)

    def _post(self, payload: dict[str, Any]) -> None:
        data = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(
            DRIVE_URL,
            data=data,
            method="POST",
            headers={"Content-Type": "application/json"},
        )
        if NAV_API_TOKEN:
            req.add_header("Authorization", f"Bearer {NAV_API_TOKEN}")
        try:
            urllib.request.urlopen(req, timeout=1.2, context=self._ssl)
        except (urllib.error.URLError, TimeoutError) as err:
            self.get_logger().warning(f"drive post failed: {err}", throttle_duration_sec=2.0)

    def _write_status(self, drive: dict[str, float], age: float | None) -> None:
        moving = abs(drive.get("x", 0)) > 1e-3 or abs(drive.get("y", 0)) > 1e-3
        status = {
            "enabled": True,
            "phase": "driving" if moving else "idle",
            "drive": drive,
            "cmd_age_s": None if age is None else round(age, 3),
            "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        }
        try:
            directory = os.path.dirname(STATUS_PATH)
            if directory:
                os.makedirs(directory, exist_ok=True)
            tmp = f"{STATUS_PATH}.tmp"
            with open(tmp, "w", encoding="utf-8") as handle:
                json.dump(status, handle)
            os.replace(tmp, STATUS_PATH)
        except OSError:
            pass


def main() -> None:
    rclpy.init()
    node = CmdVelBridge()
    try:
        rclpy.spin(node)
    finally:
        node._post({"drive": {"x": 0.0, "y": 0.0}})  # noqa: SLF001
        node.destroy_node()
        rclpy.shutdown()


if __name__ == "__main__":
    main()
