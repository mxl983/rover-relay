FROM ros:humble-ros-base-jammy

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        iproute2 \
        python3-pip \
        ros-humble-rmw-cyclonedds-cpp \
        ros-humble-tf2-ros \
        ros-humble-tf2-geometry-msgs \
        ros-humble-nav-msgs \
        ros-humble-sensor-msgs \
        ros-humble-geometry-msgs \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /opt/relay

# drive_interface is shared with ros2-nav (Twist↔stick calibration).
COPY ros2-nav/drive_interface.py /opt/relay/ros2-nav/drive_interface.py
COPY ros2-nav/lateral_maneuver.py /opt/relay/ros2-nav/lateral_maneuver.py
COPY sim /opt/relay/sim

ENV PYTHONPATH=/opt/relay
ENV RMW_IMPLEMENTATION=rmw_cyclonedds_cpp
ENV ROS_LOCALHOST_ONLY=1

COPY sim/cosim_entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

EXPOSE 8877 8878
ENTRYPOINT ["/entrypoint.sh"]
CMD ["plant"]
