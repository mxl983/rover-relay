#!/usr/bin/env python3
"""ROS 2 navigation node: LiDAR local planning + drive commands to the Pi rover server."""

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
from rclpy.node import Node
from rclpy.qos import qos_profile_sensor_data
from sensor_msgs.msg import LaserScan

from local_planner import DriveCommand, LocalPlanner, PlannerConfig, ScanPoint
from drive_payload import drive_to_payload

SCAN_TOPIC = os.environ.get("NAV_SCAN_TOPIC", "/scan")
STATUS_FILE_PATH = os.environ.get("NAV_STATUS_FILE_PATH", "/app/lidar/navigation_status.json")
MODE_FILE_PATH = os.environ.get("NAV_MODE_FILE_PATH", "/app/lidar/navigation_mode.json")
PI_BASE_URL = os.environ.get("NAV_PI_BASE_URL", "https://rover.tail9d0237.ts.net:3000").rstrip("/")
DRIVE_BASE_URL = os.environ.get(
    "NAV_DRIVE_BASE_URL",
    os.environ.get("NAV_RELAY_BASE_URL", "https://jjcloud.tail9d0237.ts.net"),
).rstrip("/")
NAV_API_TOKEN = os.environ.get("NAVIGATION_API_TOKEN", "")
CONTROL_HZ = float(os.environ.get("NAV_CONTROL_HZ", "10"))
ENABLED_POLL_S = float(os.environ.get("NAV_ENABLED_POLL_S", "0.5"))
ROAM_LINEAR = float(os.environ.get("NAV_ROAM_LINEAR", "0.48"))
ESCAPE_LINEAR = float(os.environ.get("NAV_ESCAPE_LINEAR", "0.38"))
STOP_DISTANCE_M = float(os.environ.get("NAV_STOP_DISTANCE_M", "0.28"))
SSL_VERIFY = os.environ.get("NAV_SSL_VERIFY", "false").lower() not in {"0", "false", "no"}


class NavigationNode(Node):
    def __init__(self) -> None:
        super().__init__("rover_navigation")
        self._planner = LocalPlanner(
            PlannerConfig(
                roam_linear=ROAM_LINEAR,
                escape_linear=ESCAPE_LINEAR,
                stop_distance_m=STOP_DISTANCE_M,
            )
        )
        self._latest_scan: LaserScan | None = None
        self._scan_lock = threading.Lock()
        self._navigation_enabled = False
        self._last_status: dict[str, Any] = {"enabled": False, "phase": "idle"}
        self._ssl_context = None if SSL_VERIFY else ssl._create_unverified_context()

        self.create_subscription(LaserScan, SCAN_TOPIC, self._on_scan, qos_profile_sensor_data)
        self.create_timer(1.0 / CONTROL_HZ, self._control_tick)
        self.create_timer(ENABLED_POLL_S, self._poll_enabled)
        self.get_logger().info(
            f"navigation: topic={SCAN_TOPIC} drive={DRIVE_BASE_URL} pi_ws_target={PI_BASE_URL} control_hz={CONTROL_HZ}"
        )

    def _on_scan(self, msg: LaserScan) -> None:
        with self._scan_lock:
            self._latest_scan = msg

    def _control_tick(self) -> None:
        if not self._navigation_enabled:
            self._write_status({"enabled": False, "phase": "idle"})
            return

        with self._scan_lock:
            scan = self._latest_scan
        if scan is None:
            self._write_status({"enabled": True, "phase": "waiting_for_scan"})
            return

        points = self._scan_to_points(scan)
        cmd = self._planner.tick(points)
        payload = drive_to_payload(cmd)
        self._post_drive(payload)
        self._write_status(
            {
                "enabled": True,
                "phase": cmd.phase,
                "drive": payload["drive"],
                "escapeBearingDeg": cmd.escape_bearing_deg,
                "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            }
        )

    def _scan_to_points(self, msg: LaserScan) -> list[ScanPoint]:
        points: list[ScanPoint] = []
        for index, distance in enumerate(msg.ranges):
            if not math.isfinite(distance):
                continue
            if distance < msg.range_min or distance > msg.range_max:
                continue
            angle_rad = msg.angle_min + index * msg.angle_increment
            # LD19 forward is +Y; ROS laser frame uses +X forward. Rotate to rover forward = 0°.
            rover_angle_deg = math.degrees(angle_rad) - 90.0
            points.append(ScanPoint(angle_deg=rover_angle_deg, range_m=float(distance)))
        return points

    def _poll_enabled(self) -> None:
        enabled = self._read_navigation_enabled_local()
        if enabled is None:
            enabled = self._fetch_navigation_enabled_remote()
        if enabled == self._navigation_enabled:
            return
        self._navigation_enabled = enabled
        if not enabled:
            self._planner.reset()
            self._send_stop()
        self.get_logger().info(f"navigation enabled={enabled}")

    def _read_navigation_enabled_local(self) -> bool | None:
        try:
            with open(MODE_FILE_PATH, encoding="utf-8") as handle:
                data = json.load(handle)
            if isinstance(data.get("enabled"), bool):
                return data["enabled"]
        except OSError:
            return None
        except json.JSONDecodeError:
            return None
        return None

    def _fetch_navigation_enabled_remote(self) -> bool:
        url = f"{PI_BASE_URL}/api/system/navigation"
        try:
            request = urllib.request.Request(url, method="GET")
            if NAV_API_TOKEN:
                request.add_header("Authorization", f"Bearer {NAV_API_TOKEN}")
            with urllib.request.urlopen(request, timeout=2.5, context=self._ssl_context) as response:
                body = json.loads(response.read().decode("utf-8"))
            return bool(body.get("enabled"))
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, KeyError):
            return False

    def _post_drive(self, payload: dict[str, Any]) -> None:
        url = f"{DRIVE_BASE_URL}/api/navigation/drive"
        data = json.dumps(payload).encode("utf-8")
        request = urllib.request.Request(
            url,
            data=data,
            method="POST",
            headers={"Content-Type": "application/json"},
        )
        if NAV_API_TOKEN:
            request.add_header("Authorization", f"Bearer {NAV_API_TOKEN}")
        try:
            urllib.request.urlopen(request, timeout=1.5, context=self._ssl_context)
        except (urllib.error.URLError, TimeoutError) as err:
            self.get_logger().warning(f"navigation drive post failed: {err}")

    def _send_stop(self) -> None:
        self._post_drive({"drive": {"x": 0.0, "y": 0.0}})

    def _write_status(self, status: dict[str, Any]) -> None:
        self._last_status = status
        try:
            directory = os.path.dirname(STATUS_FILE_PATH)
            if directory:
                os.makedirs(directory, exist_ok=True)
            tmp_path = f"{STATUS_FILE_PATH}.tmp"
            with open(tmp_path, "w", encoding="utf-8") as handle:
                json.dump(status, handle)
            os.replace(tmp_path, STATUS_FILE_PATH)
        except OSError as err:
            self.get_logger().warning(f"navigation status write failed: {err}")


def main() -> None:
    rclpy.init()
    node = NavigationNode()
    try:
        rclpy.spin(node)
    finally:
        node.destroy_node()
        rclpy.shutdown()


if __name__ == "__main__":
    main()
