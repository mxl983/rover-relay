#!/usr/bin/env python3
"""Subscribe to /scan and print a concise summary for connectivity checks."""

from __future__ import annotations

import math
import os
import time

import rclpy
from rclpy.node import Node
from rclpy.qos import qos_profile_sensor_data
from sensor_msgs.msg import LaserScan


def summarize_scan(msg: LaserScan) -> str:
    valid = [
        distance
        for distance in msg.ranges
        if math.isfinite(distance) and msg.range_min <= distance <= msg.range_max
    ]

    if not valid:
        return (
            f"frame={msg.header.frame_id} stamp={msg.header.stamp.sec}.{msg.header.stamp.nanosec:09d} "
            f"points={len(msg.ranges)} valid=0"
        )

    nearest = min(valid)
    farthest = max(valid)
    avg = sum(valid) / len(valid)

    return (
        f"frame={msg.header.frame_id} stamp={msg.header.stamp.sec}.{msg.header.stamp.nanosec:09d} "
        f"points={len(msg.ranges)} valid={len(valid)} "
        f"nearest={nearest:.2f}m farthest={farthest:.2f}m avg={avg:.2f}m "
        f"angle=[{math.degrees(msg.angle_min):.0f},{math.degrees(msg.angle_max):.0f}]deg "
        f"increment={math.degrees(msg.angle_increment):.2f}deg"
    )


class ScanMonitor(Node):
    def __init__(self) -> None:
        super().__init__("scan_monitor")
        topic = os.environ.get("LIDAR_TOPIC", "/scan")
        self._last_log = 0.0
        self._scan_count = 0
        self.create_subscription(LaserScan, topic, self._on_scan, qos_profile_sensor_data)
        self.get_logger().info(f"Listening on {topic}")

    def _on_scan(self, msg: LaserScan) -> None:
        self._scan_count += 1
        now = time.monotonic()
        if now - self._last_log < 1.0:
            return
        self._last_log = now
        self.get_logger().info(
            f"scan #{self._scan_count} {summarize_scan(msg)}"
        )


def main() -> None:
    rclpy.init()
    node = ScanMonitor()
    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    finally:
        node.destroy_node()
        rclpy.shutdown()


if __name__ == "__main__":
    main()
