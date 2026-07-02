FROM ros:humble-ros-base-jammy

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        iproute2 \
        dnsutils \
        ros-humble-rmw-cyclonedds-cpp \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /opt/ros2-navigation

COPY ros2-navigation/entrypoint.sh /entrypoint.sh
COPY ros2-navigation/local_planner.py /opt/ros2-navigation/local_planner.py
COPY ros2-navigation/drive_payload.py /opt/ros2-navigation/drive_payload.py
COPY ros2-navigation/navigation_node.py /opt/ros2-navigation/navigation_node.py
COPY ros2-navigation/test_local_planner.py /opt/ros2-navigation/test_local_planner.py
COPY ros2-navigation/test_navigation_node.py /opt/ros2-navigation/test_navigation_node.py

RUN chmod +x /entrypoint.sh /opt/ros2-navigation/navigation_node.py /opt/ros2-navigation/test_local_planner.py

ENTRYPOINT ["/entrypoint.sh"]
CMD ["run"]
