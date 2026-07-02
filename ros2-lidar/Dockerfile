FROM ros:humble-ros-base-jammy

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        iproute2 \
        dnsutils \
        ros-humble-rmw-cyclonedds-cpp \
        ros-humble-tf2-ros \
        ros-humble-tf2-geometry-msgs \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /opt/ros2-lidar

COPY ros2-lidar/entrypoint.sh /entrypoint.sh
COPY ros2-lidar/scan_monitor.py /opt/ros2-lidar/scan_monitor.py
COPY ros2-lidar/scan_viewer.py /opt/ros2-lidar/scan_viewer.py
COPY ros2-lidar/lidar_stabilizer.py /opt/ros2-lidar/lidar_stabilizer.py
COPY ros2-lidar/scan_pipeline.py /opt/ros2-lidar/scan_pipeline.py
COPY ros2-lidar/slam_mapper.py /opt/ros2-lidar/slam_mapper.py
COPY ros2-lidar/scan_slam_pipeline.py /opt/ros2-lidar/scan_slam_pipeline.py

RUN chmod +x /entrypoint.sh /opt/ros2-lidar/scan_monitor.py /opt/ros2-lidar/scan_viewer.py \
    /opt/ros2-lidar/lidar_stabilizer.py /opt/ros2-lidar/scan_pipeline.py \
    /opt/ros2-lidar/slam_mapper.py /opt/ros2-lidar/scan_slam_pipeline.py

ENTRYPOINT ["/entrypoint.sh"]
CMD ["monitor"]
