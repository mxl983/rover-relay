#!/usr/bin/env python3
"""Run LiDAR HTTP viewer + persistent SLAM mapper in one process."""

from __future__ import annotations

import rclpy
from rclpy.executors import MultiThreadedExecutor

from scan_viewer import ScanViewer, start_http_server
from slam_mapper import SlamMapperNode


def main() -> None:
    server = start_http_server()
    print("ros2-lidar: pipeline viewer + slam", flush=True)

    rclpy.init()
    viewer = ScanViewer()
    slam = SlamMapperNode()
    executor = MultiThreadedExecutor()
    executor.add_node(viewer)
    executor.add_node(slam)

    try:
        executor.spin()
    except KeyboardInterrupt:
        pass
    finally:
        executor.shutdown()
        slam.destroy_node()
        viewer.destroy_node()
        rclpy.shutdown()
        server.shutdown()


if __name__ == "__main__":
    main()
