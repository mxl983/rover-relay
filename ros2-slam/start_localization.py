#!/usr/bin/env python3
"""Start a Cartographer localization trajectory at the frozen rover pose."""

from __future__ import annotations

import json
import math
import os

import rclpy
from cartographer_ros_msgs.srv import StartTrajectory


def main() -> None:
    rclpy.init()
    node = rclpy.create_node("rover_start_localization")
    client = node.create_client(StartTrajectory, "/start_trajectory")
    if not client.wait_for_service(timeout_sec=30.0):
        raise RuntimeError("Cartographer start_trajectory service unavailable")

    request = StartTrajectory.Request()
    request.configuration_directory = os.environ.get(
        "SLAM_CONFIG_DIR", "/tmp/ros2-slam-config"
    )
    request.configuration_basename = os.environ.get(
        "SLAM_CONFIG_BASENAME", "rover_2d.lua"
    )

    pose_path = os.environ.get(
        "SLAM_FREEZE_POSE_PATH", "/app/lidar/maps/freeze_pose.json"
    )
    global_localization_path = os.environ.get(
        "SLAM_GLOBAL_LOCALIZATION_PATH", "/app/lidar/.global_localization"
    )
    global_active_path = os.environ.get(
        "SLAM_GLOBAL_LOCALIZATION_ACTIVE_PATH",
        "/app/lidar/.global_localization_active",
    )
    force_global = os.path.isfile(global_localization_path)
    if os.path.isfile(pose_path) and not force_global:
        with open(pose_path, encoding="utf-8") as handle:
            pose = json.load(handle)
        yaw = float(pose["yaw"])
        request.use_initial_pose = True
        request.relative_to_trajectory_id = 0
        request.initial_pose.position.x = float(pose["x"])
        request.initial_pose.position.y = float(pose["y"])
        request.initial_pose.orientation.z = math.sin(yaw / 2.0)
        request.initial_pose.orientation.w = math.cos(yaw / 2.0)

    future = client.call_async(request)
    rclpy.spin_until_future_complete(node, future, timeout_sec=30.0)
    response = future.result()
    if response is None:
        raise RuntimeError("Cartographer start_trajectory timed out")
    if int(response.status.code) != 0:
        raise RuntimeError(
            f"Cartographer rejected localization trajectory: {response.status.message}"
        )
    node.get_logger().info(
        "Localization trajectory started "
        f"initial_pose={request.use_initial_pose} global_scan_match={force_global}"
    )
    if force_global:
        os.replace(global_localization_path, global_active_path)
    node.destroy_node()
    rclpy.shutdown()


if __name__ == "__main__":
    main()
