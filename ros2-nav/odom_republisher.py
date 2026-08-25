#!/usr/bin/env python3
"""Publish nav_msgs/Odometry from TF odom→base_link (Cartographer provides TF only)."""

from __future__ import annotations

import math
import os

import rclpy
from geometry_msgs.msg import TransformStamped
from nav_msgs.msg import Odometry
from rclpy.duration import Duration
from rclpy.node import Node
from rclpy.time import Time
from tf2_ros import Buffer, TransformException, TransformListener

ODOM_FRAME = os.environ.get("NAV_ODOM_FRAME", "odom")
BASE_FRAME = os.environ.get("NAV_BASE_FRAME", "base_link")
ODOM_TOPIC = os.environ.get("NAV_ODOM_TOPIC", "/odom")
HZ = float(os.environ.get("NAV_ODOM_HZ", "20"))


def yaw_from_quat(x: float, y: float, z: float, w: float) -> float:
    return math.atan2(2.0 * (w * z + x * y), 1.0 - 2.0 * (y * y + z * z))


class OdomRepublisher(Node):
    def __init__(self) -> None:
        super().__init__("rover_odom_republisher")
        self._buf = Buffer()
        self._listener = TransformListener(self._buf, self)
        self._pub = self.create_publisher(Odometry, ODOM_TOPIC, 10)
        self._prev: TransformStamped | None = None
        self._prev_t: float | None = None
        self.create_timer(1.0 / max(HZ, 1.0), self._tick)
        self.get_logger().info(f"odom republish {ODOM_FRAME}→{BASE_FRAME} → {ODOM_TOPIC}")

    def _tick(self) -> None:
        try:
            tf = self._buf.lookup_transform(
                ODOM_FRAME, BASE_FRAME, Time(), timeout=Duration(seconds=0.05)
            )
        except TransformException:
            return
        msg = Odometry()
        msg.header = tf.header
        msg.header.frame_id = ODOM_FRAME
        msg.child_frame_id = BASE_FRAME
        msg.pose.pose.position.x = tf.transform.translation.x
        msg.pose.pose.position.y = tf.transform.translation.y
        msg.pose.pose.position.z = tf.transform.translation.z
        msg.pose.pose.orientation = tf.transform.rotation

        now = self.get_clock().now().nanoseconds * 1e-9
        if self._prev is not None and self._prev_t is not None:
            dt = max(1e-3, now - self._prev_t)
            dx = tf.transform.translation.x - self._prev.transform.translation.x
            dy = tf.transform.translation.y - self._prev.transform.translation.y
            yaw = yaw_from_quat(
                tf.transform.rotation.x,
                tf.transform.rotation.y,
                tf.transform.rotation.z,
                tf.transform.rotation.w,
            )
            pyaw = yaw_from_quat(
                self._prev.transform.rotation.x,
                self._prev.transform.rotation.y,
                self._prev.transform.rotation.z,
                self._prev.transform.rotation.w,
            )
            dyaw = math.atan2(math.sin(yaw - pyaw), math.cos(yaw - pyaw))
            # Body-frame twist approx.
            c, s = math.cos(yaw), math.sin(yaw)
            msg.twist.twist.linear.x = (c * dx + s * dy) / dt
            msg.twist.twist.linear.y = (-s * dx + c * dy) / dt
            msg.twist.twist.angular.z = dyaw / dt

        self._prev = tf
        self._prev_t = now
        self._pub.publish(msg)


def main() -> None:
    rclpy.init()
    node = OdomRepublisher()
    try:
        rclpy.spin(node)
    finally:
        node.destroy_node()
        rclpy.shutdown()


if __name__ == "__main__":
    main()
