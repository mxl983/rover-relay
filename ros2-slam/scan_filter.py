#!/usr/bin/env python3
"""Filter LD19 LaserScan noise before Cartographer.

Rejects near/far outliers and isolated range spikes that otherwise shatter
lidar-only scan matching into a speckled map.
"""

from __future__ import annotations

import copy
import math
import os

import rclpy
from rclpy.node import Node
from rclpy.qos import qos_profile_sensor_data
from sensor_msgs.msg import LaserScan

INPUT_TOPIC = os.environ.get("LIDAR_TOPIC", "/scan")
OUTPUT_TOPIC = os.environ.get("SLAM_FILTERED_TOPIC", "/scan_filtered")
NAV_OUTPUT_TOPIC = os.environ.get("NAV_FILTERED_TOPIC", "/scan_nav")
OUTPUT_FRAME = os.environ.get("SLAM_CORRECTED_LASER_FRAME", "").strip()
MIN_RANGE = float(os.environ.get("SLAM_FILTER_MIN_RANGE", "0.25"))
MAX_RANGE = float(os.environ.get("SLAM_FILTER_MAX_RANGE", "8.0"))
# Drop a return if it differs from both neighbors by more than this (meters).
SPIKE_M = float(os.environ.get("SLAM_FILTER_SPIKE_M", "0.35"))
# Require at least this many finite neighbors in ±window after spike filter.
ISOLATION_WINDOW = int(os.environ.get("SLAM_FILTER_ISOLATION_WINDOW", "2"))
MIN_NEIGHBORS = int(os.environ.get("SLAM_FILTER_MIN_NEIGHBORS", "1"))


def _finite_range(value: float, range_min: float, range_max: float) -> bool:
    return math.isfinite(value) and range_min <= value <= range_max


def filter_ranges(ranges: list[float], range_min: float, range_max: float) -> list[float]:
    n = len(ranges)
    if n == 0:
        return ranges

    clipped = [float("inf")] * n
    for i, value in enumerate(ranges):
        if _finite_range(value, range_min, range_max):
            clipped[i] = value

    # Spike rejection: keep only if close to at least one immediate neighbor.
    spiked = list(clipped)
    for i in range(n):
        value = clipped[i]
        if not math.isfinite(value):
            continue
        prev_v = clipped[i - 1] if i > 0 else float("nan")
        next_v = clipped[i + 1] if i + 1 < n else float("nan")
        ok_prev = math.isfinite(prev_v) and abs(value - prev_v) <= SPIKE_M
        ok_next = math.isfinite(next_v) and abs(value - next_v) <= SPIKE_M
        if not (ok_prev or ok_next):
            # Endpoints: allow if the only neighbor is close; otherwise drop.
            if i == 0 and ok_next:
                continue
            if i == n - 1 and ok_prev:
                continue
            if ok_prev or ok_next:
                continue
            spiked[i] = float("inf")

    # Isolation rejection: sparse speckles with no local support.
    cleaned = list(spiked)
    if ISOLATION_WINDOW > 0 and MIN_NEIGHBORS > 0:
        for i in range(n):
            if not math.isfinite(spiked[i]):
                continue
            support = 0
            for j in range(i - ISOLATION_WINDOW, i + ISOLATION_WINDOW + 1):
                if j == i or j < 0 or j >= n:
                    continue
                if math.isfinite(spiked[j]) and abs(spiked[j] - spiked[i]) <= SPIKE_M:
                    support += 1
            if support < MIN_NEIGHBORS:
                cleaned[i] = float("inf")

    return cleaned


class ScanFilter(Node):
    def __init__(self) -> None:
        super().__init__("rover_slam_scan_filter")
        self._pub = self.create_publisher(LaserScan, OUTPUT_TOPIC, qos_profile_sensor_data)
        self._nav_pub = self.create_publisher(
            LaserScan, NAV_OUTPUT_TOPIC, qos_profile_sensor_data
        )
        self.create_subscription(LaserScan, INPUT_TOPIC, self._on_scan, qos_profile_sensor_data)
        self._msg_count = 0
        self.get_logger().info(
            f"Filtering {INPUT_TOPIC} → SLAM:{OUTPUT_TOPIC} + Nav2:{NAV_OUTPUT_TOPIC} "
            f"(frame={OUTPUT_FRAME or 'source'}, range {MIN_RANGE}-{MAX_RANGE}m, "
            f"spike>{SPIKE_M}m)"
        )

    def _on_scan(self, msg: LaserScan) -> None:
        out = LaserScan()
        out.header = msg.header
        # The rover already publishes base_link→base_laser. Never override that
        # shared TF with our mounting correction: move the filtered scan onto a
        # private frame with one unambiguous transform instead.
        if OUTPUT_FRAME:
            out.header.frame_id = OUTPUT_FRAME
        out.angle_min = msg.angle_min
        out.angle_max = msg.angle_max
        out.angle_increment = msg.angle_increment
        # LD19 timestamps within a scan are often non-monotonic; treat scan as one shot.
        out.time_increment = 0.0
        out.scan_time = msg.scan_time if msg.scan_time > 0 else 0.1
        out.range_min = max(msg.range_min, MIN_RANGE)
        out.range_max = min(msg.range_max, MAX_RANGE) if msg.range_max > 0 else MAX_RANGE
        out.ranges = filter_ranges(list(msg.ranges), out.range_min, out.range_max)
        out.intensities = list(msg.intensities) if msg.intensities else []
        if out.intensities and len(out.intensities) != len(out.ranges):
            out.intensities = []
        # Preserve the original scan timestamp for Cartographer. This is the
        # last-known-good SLAM behavior; retiming changed motion estimation.
        self._pub.publish(out)
        # Nav2 costmaps need a current stamp or their TF filter drops the scan.
        nav_out = copy.deepcopy(out)
        nav_out.header.stamp = self.get_clock().now().to_msg()
        self._nav_pub.publish(nav_out)
        self._msg_count += 1
        if self._msg_count % 50 == 1:
            kept = sum(1 for r in out.ranges if math.isfinite(r))
            self.get_logger().info(f"scan filter kept {kept}/{len(out.ranges)} returns")


def main() -> None:
    rclpy.init()
    node = ScanFilter()
    try:
        rclpy.spin(node)
    finally:
        node.destroy_node()
        rclpy.shutdown()


if __name__ == "__main__":
    main()
