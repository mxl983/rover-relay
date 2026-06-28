#!/usr/bin/env python3
"""Run lidar stabilizer + HTTP viewer in one process."""

from __future__ import annotations

import rclpy
from rclpy.executors import MultiThreadedExecutor

from lidar_stabilizer import LidarStabilizerNode
from scan_viewer import ScanViewer, start_http_server


def main() -> None:
    server = start_http_server()
    print("ros2-lidar: pipeline stabilizer + viewer", flush=True)

    rclpy.init()
    stabilizer = LidarStabilizerNode()
    viewer = ScanViewer()
    executor = MultiThreadedExecutor()
    executor.add_node(stabilizer)
    executor.add_node(viewer)

    try:
        executor.spin()
    except KeyboardInterrupt:
        pass
    finally:
        executor.shutdown()
        stabilizer.destroy_node()
        viewer.destroy_node()
        rclpy.shutdown()
        server.shutdown()


if __name__ == "__main__":
    main()
