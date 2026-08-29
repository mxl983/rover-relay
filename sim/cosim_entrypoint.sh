#!/usr/bin/env bash
# Sim plant entrypoint: fake Cartographer (/map + TF + /scan_nav) + Pi drive HTTP.
set -eo pipefail

export AMENT_TRACE_SETUP_FILES="${AMENT_TRACE_SETUP_FILES:-}"
export AMENT_PYTHON_EXECUTABLE="${AMENT_PYTHON_EXECUTABLE:-}"
source /opt/ros/humble/setup.bash

: "${ROS_DOMAIN_ID:=87}"
: "${RMW_IMPLEMENTATION:=rmw_cyclonedds_cpp}"
: "${ROS_LOCALHOST_ONLY:=1}"
export ROS_DOMAIN_ID RMW_IMPLEMENTATION ROS_LOCALHOST_ONLY
# Prepend repo path without clobbering ROS site-packages from setup.bash.
export PYTHONPATH="/opt/relay${PYTHONPATH:+:$PYTHONPATH}"

# Match ros2-nav localhost Cyclone: enough participant slots for Nav2 + plant.
CYCLONEDDS_CONFIG_PATH="${CYCLONEDDS_CONFIG_PATH:-/tmp/cyclonedds_sim.xml}"
cat > "${CYCLONEDDS_CONFIG_PATH}" <<EOF
<?xml version="1.0" encoding="UTF-8" ?>
<CycloneDDS xmlns="https://cdds.io/config" xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance" xsi:schemaLocation="https://cdds.io/config https://raw.githubusercontent.com/eclipse-cyclonedds/cyclonedds/master/etc/cyclonedds.xsd">
  <Domain Id="any">
    <General>
      <AllowMulticast>false</AllowMulticast>
    </General>
    <Discovery>
      <ParticipantIndex>auto</ParticipantIndex>
      <MaxAutoParticipantIndex>120</MaxAutoParticipantIndex>
      <Peers>
        <Peer Address="127.0.0.1" />
      </Peers>
    </Discovery>
  </Domain>
</CycloneDDS>
EOF
export CYCLONEDDS_URI="file://${CYCLONEDDS_CONFIG_PATH}"

case "${1:-plant}" in
  plant|cosim)
    exec python3 -m sim.ros_bridge
    ;;
  bash)
    shift
    exec bash "$@"
    ;;
  *)
    exec "$@"
    ;;
esac
