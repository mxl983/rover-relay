FROM ros:humble-ros-base-jammy

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        iproute2 \
        dnsutils \
        python3-pip \
        python3-requests \
        python3-websocket \
        ros-humble-rmw-cyclonedds-cpp \
        ros-humble-navigation2 \
        ros-humble-nav2-bringup \
        ros-humble-tf2-ros \
        ros-humble-tf2-geometry-msgs \
        ros-humble-nav2-simple-commander \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /opt/ros2-nav

COPY ros2-nav/entrypoint.sh /entrypoint.sh
COPY ros2-nav/bridges.py /opt/ros2-nav/bridges.py
COPY ros2-nav/drive_interface.py /opt/ros2-nav/drive_interface.py
COPY ros2-nav/yaw_pulse_align.py /opt/ros2-nav/yaw_pulse_align.py
COPY ros2-nav/xy_gap_close.py /opt/ros2-nav/xy_gap_close.py
COPY ros2-nav/nav_context.py /opt/ros2-nav/nav_context.py
COPY ros2-nav/path_progress.py /opt/ros2-nav/path_progress.py
COPY ros2-nav/pi_drive_client.py /opt/ros2-nav/pi_drive_client.py
COPY ros2-nav/cmd_vel_bridge.py /opt/ros2-nav/cmd_vel_bridge.py
COPY ros2-nav/goal_server.py /opt/ros2-nav/goal_server.py
COPY ros2-nav/odom_republisher.py /opt/ros2-nav/odom_republisher.py
COPY ros2-nav/config /opt/ros2-nav/config

RUN chmod +x /entrypoint.sh \
    /opt/ros2-nav/bridges.py \
    /opt/ros2-nav/cmd_vel_bridge.py \
    /opt/ros2-nav/goal_server.py \
    /opt/ros2-nav/odom_republisher.py

ENV NAV_START_SETTLE_SEC=3.0 \
    NAV_MOTION_SETTLE_SEC=3.0 \
    NAV_CANCEL_SETTLE_SEC=3.0 \
    NAV_POSE_STABLE_SEC=2.0

ENTRYPOINT ["/entrypoint.sh"]
CMD ["nav"]
