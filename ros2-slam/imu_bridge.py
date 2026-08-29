#!/usr/bin/env python3
"""Poll Pi HTTP IMU and publish sensor_msgs/Imu for Cartographer.

Drive experiment (2026-08) on this rover:
  - gyro.z → yaw / turns (strong signal, ~0.8+ rad/s when spinning)
  - gyro.y → forward / reverse / stop decel (weak pitch, ~0.05 rad/s peak)
  - rest gyro.z has a large constant offset (~1.3 rad/s) — subtract bias, do
    not treat as a stuck sensor if debiased values are quiet.

Pi HTTP accel often reports ~0.8 g on X at rest (not Z). We permute so
gravity lands on ROS +Z before health checks and publishing.

Gyro bias is tracked at rest; unhealthy streams (noisy debiased gyro, wrong |g|)
do not arm Cartographer.
"""

from __future__ import annotations

import json
import math
import os
import ssl
import time
import urllib.error
import urllib.request
from collections import deque
from typing import Any

G = 9.80665
READY_PATH = os.environ.get("SLAM_IMU_READY_PATH", "/tmp/ros2-slam-imu-ready")
IMU_URL = os.environ.get(
    "SLAM_IMU_URL",
    os.environ.get(
        "PI_IMU_URL",
        "https://rover.tail9d0237.ts.net:3000/api/sensors/imu",
    ),
)
IMU_TOPIC = os.environ.get("SLAM_IMU_TOPIC", "/imu")
IMU_FRAME = os.environ.get("SLAM_IMU_FRAME", "base_link")
POLL_HZ = float(os.environ.get("SLAM_IMU_POLL_HZ", "50"))
POLL_HZ = max(5.0, min(POLL_HZ, 200.0))
TOKEN = (
    os.environ.get("SLAM_IMU_TOKEN")
    or os.environ.get("NAVIGATION_API_TOKEN")
    or os.environ.get("ROVER_API_TOKEN")
    or ""
)
SSL_INSECURE = os.environ.get("SLAM_IMU_SSL_INSECURE", "1") not in (
    "0",
    "false",
    "False",
)
# Health / bias
HEALTH_WINDOW = int(os.environ.get("SLAM_IMU_HEALTH_WINDOW", "40"))
BIAS_ALPHA = float(os.environ.get("SLAM_IMU_BIAS_ALPHA", "0.02"))
MIN_ACCEL_G = float(os.environ.get("SLAM_IMU_MIN_ACCEL_G", "0.70"))
MAX_ACCEL_G = float(os.environ.get("SLAM_IMU_MAX_ACCEL_G", "1.30"))
STUCK_STD = float(os.environ.get("SLAM_IMU_STUCK_STD", "1e-5"))
MAX_DEBIASED_GYRO = float(os.environ.get("SLAM_IMU_MAX_DEBIASED_GYRO", "0.25"))


def pi_accel_to_ros(x: float, y: float, z: float) -> tuple[float, float, float]:
    """Map Pi rest gravity (~+X) into ROS base_link (+Z up).

    Measured rest: ax≈+0.8 g, ay≈0, az≈0 → ros (az, ay, ax) puts g on +Z.
    """
    return (float(z), float(y), float(x))


def pi_gyro_to_ros(x: float, y: float, z: float) -> tuple[float, float, float]:
    """Pi gyro: Y ≈ pitch (fwd/back), Z ≈ yaw — same labels as ROS base_link."""
    return (float(x), float(y), float(z))


def chip_accel_to_ros(x: float, y: float, z: float) -> tuple[float, float, float]:
    return pi_accel_to_ros(x, y, z)


def chip_gyro_to_ros(x: float, y: float, z: float) -> tuple[float, float, float]:
    return pi_gyro_to_ros(x, y, z)


# Back-compat for unit tests / old name.
def chip_to_ros(x: float, y: float, z: float) -> tuple[float, float, float]:
    return chip_accel_to_ros(x, y, z)


def parse_imu_payload(raw: Any) -> dict[str, Any] | None:
    """Accept Pi shapes: flat sample, ``{data:…}``, or ``{success,status,sample}``."""
    if not isinstance(raw, dict):
        return None
    status = raw.get("status") if isinstance(raw.get("status"), dict) else {}
    if raw.get("connected") is False or status.get("connected") is False:
        return None
    if isinstance(raw.get("sample"), dict):
        body = raw["sample"]
    elif isinstance(raw.get("data"), dict):
        body = raw["data"]
    else:
        body = raw
    if not isinstance(body, dict):
        return None
    if body.get("connected") is False:
        return None
    accel = body.get("accel") or {}
    gyro = body.get("gyro") or {}
    try:
        ax, ay, az = float(accel["x"]), float(accel["y"]), float(accel["z"])
        gx, gy, gz = float(gyro["x"]), float(gyro["y"]), float(gyro["z"])
    except (KeyError, TypeError, ValueError):
        return None
    stamp = body.get("stamp")
    try:
        stamp_f = float(stamp) if stamp is not None else None
    except (TypeError, ValueError):
        stamp_f = None
    return {
        "stamp": stamp_f,
        "seq": body.get("seq"),
        "accel_g": (ax, ay, az),
        "gyro_rad_s": (gx, gy, gz),
    }


def fetch_imu(url: str, token: str, *, insecure: bool) -> dict[str, Any] | None:
    headers = {"Accept": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    req = urllib.request.Request(url, headers=headers, method="GET")
    context = None
    if url.startswith("https://") and insecure:
        context = ssl._create_unverified_context()  # noqa: S323
    try:
        with urllib.request.urlopen(req, timeout=1.5, context=context) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, OSError):
        return None
    return parse_imu_payload(payload)


def mark_ready(ok: bool = True) -> None:
    if not ok:
        try:
            os.remove(READY_PATH)
        except FileNotFoundError:
            pass
        return
    try:
        with open(READY_PATH, "w", encoding="utf-8") as handle:
            handle.write(f"{time.time()}\n")
    except OSError:
        pass


def _window_gyro_bias(window: deque[dict[str, Any]]) -> tuple[float, float, float]:
    gx = sum(s["gyro_rad_s"][0] for s in window) / len(window)
    gy = sum(s["gyro_rad_s"][1] for s in window) / len(window)
    gz = sum(s["gyro_rad_s"][2] for s in window) / len(window)
    return gx, gy, gz


def evaluate_health(window: deque[dict[str, Any]]) -> tuple[bool, str]:
    """Return (healthy, reason). Uses remapped accel and debiased gyro."""
    if len(window) < max(10, HEALTH_WINDOW // 2):
        return False, "warming_up"

    axs, ays, azs = [], [], []
    for s in window:
        rax, ray, raz = pi_accel_to_ros(*s["accel_g"])
        axs.append(rax)
        ays.append(ray)
        azs.append(raz)
    ax = sum(axs) / len(axs)
    ay = sum(ays) / len(ays)
    az = sum(azs) / len(azs)
    mag = math.sqrt(ax * ax + ay * ay + az * az)
    if mag < MIN_ACCEL_G or mag > MAX_ACCEL_G:
        return False, f"accel_mag={mag:.2f}g"
    az_g = max(abs(az), abs(ax), abs(ay))
    if abs(az) < 0.65 * az_g and az_g > 0.5:
        return False, f"gravity_not_on_z ax={ax:.2f} ay={ay:.2f} az={az:.2f}"
    horiz = math.hypot(ax, ay)
    if horiz > 0.45:
        return False, f"tilted horiz={horiz:.2f}g"

    bx, by, bz = _window_gyro_bias(window)
    for idx, name in enumerate(("gx", "gy", "gz")):
        bias = (bx, by, bz)[idx]
        vals = [s["gyro_rad_s"][idx] - bias for s in window]
        mean = sum(vals) / len(vals)
        var = sum((v - mean) ** 2 for v in vals) / len(vals)
        std = math.sqrt(var)
        peak = max(abs(v) for v in vals)
        # Raw gz can sit at ~1.3 rad/s at rest; debiased must be quiet.
        if std < STUCK_STD and peak > MAX_DEBIASED_GYRO:
            return False, f"stuck_{name} debiased_peak={peak:.3f} std={std:.2e}"
        if peak > 1.5:
            return False, f"huge_debiased_{name}={peak:.3f}"
    return True, "ok"


def main() -> None:
    import rclpy
    from rclpy.node import Node
    from rclpy.qos import qos_profile_sensor_data
    from sensor_msgs.msg import Imu

    class ImuBridge(Node):
        def __init__(self) -> None:
            super().__init__("rover_imu_bridge")
            self._pub = self.create_publisher(Imu, IMU_TOPIC, qos_profile_sensor_data)
            self._ok = False
            self._fail_streak = 0
            self._window: deque[dict[str, Any]] = deque(maxlen=HEALTH_WINDOW)
            self._gyro_bias = [0.0, 0.0, 0.0]
            self._bias_init = False
            period = 1.0 / POLL_HZ
            self.create_timer(period, self._tick)
            self.get_logger().info(
                f"IMU bridge url={IMU_URL} topic={IMU_TOPIC} frame={IMU_FRAME} hz={POLL_HZ}"
            )

        def _tick(self) -> None:
            sample = fetch_imu(IMU_URL, TOKEN, insecure=SSL_INSECURE)
            if sample is None:
                self._fail_streak += 1
                if self._fail_streak in (1, 20, 100) or self._fail_streak % 500 == 0:
                    self.get_logger().warning(
                        f"IMU fetch failed ({self._fail_streak}) from {IMU_URL}",
                        throttle_duration_sec=5.0,
                    )
                return
            self._fail_streak = 0
            self._window.append(sample)
            healthy, reason = evaluate_health(self._window)

            # Slow bias track only when roughly still (small recent gyro variance).
            gx, gy, gz = sample["gyro_rad_s"]
            if not self._bias_init:
                self._gyro_bias = [gx, gy, gz]
                self._bias_init = True
            else:
                recent = list(self._window)[-min(15, len(self._window)) :]
                gz_vals = [s["gyro_rad_s"][2] for s in recent]
                mean = sum(gz_vals) / len(gz_vals)
                var = sum((v - mean) ** 2 for v in gz_vals) / len(gz_vals)
                if var < 0.002:  # ~still
                    a = BIAS_ALPHA
                    self._gyro_bias[0] += a * (gx - self._gyro_bias[0])
                    self._gyro_bias[1] += a * (gy - self._gyro_bias[1])
                    self._gyro_bias[2] += a * (gz - self._gyro_bias[2])

            if healthy:
                self._pub.publish(self._to_msg(sample))
                if not self._ok:
                    self._ok = True
                    mark_ready(True)
                    self.get_logger().info(
                        f"IMU healthy ({reason}) — Cartographer may use_imu_data "
                        f"bias_gz={self._gyro_bias[2]:.3f}"
                    )
            else:
                if self._ok:
                    self._ok = False
                    mark_ready(False)
                    self.get_logger().warning(
                        f"IMU unhealthy ({reason}) — disarmed for Cartographer",
                        throttle_duration_sec=5.0,
                    )
                elif len(self._window) == self._window.maxlen:
                    self.get_logger().warning(
                        f"IMU not arming ({reason})",
                        throttle_duration_sec=5.0,
                    )

        def _to_msg(self, sample: dict[str, Any]) -> Imu:
            ax, ay, az = sample["accel_g"]
            gx, gy, gz = sample["gyro_rad_s"]
            rax, ray, raz = chip_accel_to_ros(ax, ay, az)
            rgx, rgy, rgz = chip_gyro_to_ros(
                gx - self._gyro_bias[0],
                gy - self._gyro_bias[1],
                gz - self._gyro_bias[2],
            )

            msg = Imu()
            stamp = sample.get("stamp")
            if isinstance(stamp, (int, float)) and stamp > 1e9:
                sec = int(stamp)
                nsec = int((stamp - sec) * 1e9)
                msg.header.stamp.sec = sec
                msg.header.stamp.nanosec = nsec
            else:
                msg.header.stamp = self.get_clock().now().to_msg()
            msg.header.frame_id = IMU_FRAME

            msg.linear_acceleration.x = rax * G
            msg.linear_acceleration.y = ray * G
            msg.linear_acceleration.z = raz * G
            msg.linear_acceleration_covariance[0] = 0.08
            msg.linear_acceleration_covariance[4] = 0.08
            msg.linear_acceleration_covariance[8] = 0.08

            msg.angular_velocity.x = rgx
            msg.angular_velocity.y = rgy
            msg.angular_velocity.z = rgz
            msg.angular_velocity_covariance[0] = 0.05
            msg.angular_velocity_covariance[4] = 0.05
            msg.angular_velocity_covariance[8] = 0.05

            msg.orientation.w = 1.0
            msg.orientation_covariance[0] = -1.0
            return msg

    try:
        os.remove(READY_PATH)
    except FileNotFoundError:
        pass
    rclpy.init()
    node = ImuBridge()
    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    finally:
        node.destroy_node()
        rclpy.shutdown()


if __name__ == "__main__":
    main()
