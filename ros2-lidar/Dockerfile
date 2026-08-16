FROM ros:humble-ros-base-jammy

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        iproute2 \
        dnsutils \
        ros-humble-rmw-cyclonedds-cpp \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /opt/ros2-lidar

COPY ros2-lidar/entrypoint.sh /entrypoint.sh
COPY ros2-lidar/scan_viewer.py /opt/ros2-lidar/scan_viewer.py

RUN chmod +x /entrypoint.sh /opt/ros2-lidar/scan_viewer.py

ENTRYPOINT ["/entrypoint.sh"]
CMD ["viewer"]
