#!/usr/bin/env bash
# Nav2 on Cartographer map/TF + unified Python bridges (odom/cmd_vel/goals).
set -eo pipefail

# ROS setup.bash references optional ament vars; keep nounset off while sourcing.
export AMENT_TRACE_SETUP_FILES="${AMENT_TRACE_SETUP_FILES:-}"
export AMENT_PYTHON_EXECUTABLE="${AMENT_PYTHON_EXECUTABLE:-}"
source /opt/ros/humble/setup.bash

: "${ROS_DOMAIN_ID:=0}"
: "${RMW_IMPLEMENTATION:=rmw_cyclonedds_cpp}"
: "${ROS_LOCALHOST_ONLY:=0}"
: "${DDS_LOCAL_INTERFACE:=lo}"
: "${DDS_TAILSCALE_INTERFACE:=tailscale0}"
: "${SERVER_DDS_EXTERNAL_ADDRESS:=}"
: "${ROVER_DDS_PEER:=}"
: "${LOCAL_DDS_PEER:=127.0.0.1}"

export ROS_DOMAIN_ID RMW_IMPLEMENTATION ROS_LOCALHOST_ONLY

resolve_peer() {
  local peer="${1:-}"
  if [[ -z "${peer}" ]]; then
    printf ''
    return
  fi
  if [[ "${peer}" =~ ^[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+$ ]]; then
    printf '%s' "${peer}"
    return
  fi
  if command -v getent >/dev/null 2>&1; then
    local resolved
    resolved="$(getent ahostsv4 "${peer}" | awk 'NR == 1 { print $1; exit }')"
    if [[ -n "${resolved}" ]]; then
      printf '%s' "${resolved}"
      return
    fi
  fi
  printf '%s' "${peer}"
}

ROVER_DDS_PEER_RESOLVED="$(resolve_peer "${ROVER_DDS_PEER}")"
CYCLONEDDS_CONFIG_PATH="${CYCLONEDDS_CONFIG_PATH:-/tmp/cyclonedds_nav.xml}"

cat > "${CYCLONEDDS_CONFIG_PATH}" <<EOF
<?xml version="1.0" encoding="UTF-8" ?>
<CycloneDDS xmlns="https://cdds.io/config" xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance" xsi:schemaLocation="https://cdds.io/config https://raw.githubusercontent.com/eclipse-cyclonedds/cyclonedds/master/etc/cyclonedds.xsd">
  <Domain Id="any">
    <General>
      <Interfaces>
        <NetworkInterface name="${DDS_LOCAL_INTERFACE}" priority="1" multicast="false" />
        <NetworkInterface name="${DDS_TAILSCALE_INTERFACE}" priority="2" multicast="false" />
      </Interfaces>
      <ExternalNetworkAddress>${SERVER_DDS_EXTERNAL_ADDRESS}</ExternalNetworkAddress>
      <AllowMulticast>false</AllowMulticast>
    </General>
    <Discovery>
      <ParticipantIndex>auto</ParticipantIndex>
      <MaxAutoParticipantIndex>50</MaxAutoParticipantIndex>
      <Peers>
        <Peer Address="${ROVER_DDS_PEER_RESOLVED}" />
        <Peer Address="${LOCAL_DDS_PEER}" />
      </Peers>
    </Discovery>
  </Domain>
</CycloneDDS>
EOF
export CYCLONEDDS_URI="file://${CYCLONEDDS_CONFIG_PATH}"

PARAMS="${NAV2_PARAMS_FILE:-/opt/ros2-nav/config/nav2_params.yaml}"
SCAN_TOPIC="${NAV_SCAN_TOPIC:-/scan_filtered}"
NAV_WIPE_ON_START="${NAV_WIPE_ON_START:-false}"
SLAM_MAP_FILE_PATH="${SLAM_MAP_FILE_PATH:-/app/lidar/slam.json}"
SLAM_PERSISTENT_MAP_PATH="${SLAM_PERSISTENT_MAP_PATH:-/app/lidar/maps/persistent_grid.json}"
SLAM_WAYPOINTS_PATH="${SLAM_WAYPOINTS_PATH:-/app/lidar/maps/waypoints.json}"

wipe_nav_session() {
  local wipe="${NAV_WIPE_ON_START,,}"
  if [[ "${wipe}" != "1" && "${wipe}" != "true" && "${wipe}" != "yes" && "${wipe}" != "on" ]]; then
    echo "ros2-nav: keeping prior navigation session (NAV_WIPE_ON_START=${NAV_WIPE_ON_START})"
    return 0
  fi
  # Nav2 owns only these transient command/status files. Persistent SLAM maps,
  # Cartographer states, and waypoints are managed by the explicit purge API.
  echo "ros2-nav: clearing transient navigation session"
  rm -f \
    /app/lidar/navigation_path.json \
    /app/lidar/navigation_goal.json \
    /app/lidar/navigation_command.json \
    /app/lidar/navigation_status.json \
    2>/dev/null || true
}

start_nav() {
  wipe_nav_session

  local nav2_pid="" bridges_pid=""
  cleanup() {
    [[ -n "${bridges_pid}" ]] && kill "${bridges_pid}" 2>/dev/null || true
    [[ -n "${nav2_pid}" ]] && kill "${nav2_pid}" 2>/dev/null || true
  }
  trap cleanup EXIT INT TERM

  echo "ros2-nav: launching Nav2 (slam mode — Cartographer provides /map + TF)"
  ros2 launch nav2_bringup navigation_launch.py \
    use_sim_time:=False \
    slam:=True \
    params_file:="${PARAMS}" \
    autostart:=True \
    use_composition:=False \
    use_respawn:=True &
  nav2_pid=$!

  sleep 4

  # Unified bridges: odom + cmd_vel + goals + fine dock (single rclpy context).
  while true; do
    if ! kill -0 "${nav2_pid}" 2>/dev/null; then
      echo "ros2-nav: Nav2 launch exited" >&2
      break
    fi
    echo "ros2-nav: starting bridges (odom/cmd_vel/goal) scan=${SCAN_TOPIC}"
    python3 /opt/ros2-nav/bridges.py &
    bridges_pid=$!
    wait "${bridges_pid}" || true
    kill "${bridges_pid}" 2>/dev/null || true
    echo "ros2-nav: bridges exited — restarting in 2s" >&2
    sleep 2
  done

  cleanup
  wait || true
}

case "${1:-nav}" in
  nav|navigation)
    start_nav
    ;;
  bash)
    shift
    exec bash "$@"
    ;;
  *)
    exec "$@"
    ;;
esac
