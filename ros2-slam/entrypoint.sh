#!/usr/bin/env bash
set -eo pipefail

ROS_DOMAIN_ID="${ROS_DOMAIN_ID:-0}"
RMW_IMPLEMENTATION="${RMW_IMPLEMENTATION:-rmw_cyclonedds_cpp}"
ROS_LOCALHOST_ONLY="${ROS_LOCALHOST_ONLY:-0}"
LIDAR_TOPIC="${LIDAR_TOPIC:-/scan}"
DDS_LOCAL_INTERFACE="${DDS_LOCAL_INTERFACE:-lo}"
DDS_TAILSCALE_INTERFACE="${DDS_TAILSCALE_INTERFACE:-tailscale0}"
SERVER_DDS_EXTERNAL_ADDRESS="${SERVER_DDS_EXTERNAL_ADDRESS:-100.96.16.121}"
ROVER_DDS_PEER="${ROVER_DDS_PEER:-100.109.197.90}"
LOCAL_DDS_PEER="${LOCAL_DDS_PEER:-127.0.0.1}"
CYCLONEDDS_CONFIG_DIR="${CYCLONEDDS_CONFIG_DIR:-/etc/cyclonedds}"
CYCLONEDDS_CONFIG_PATH="${CYCLONEDDS_CONFIG_DIR}/cyclonedds.xml"

SLAM_CONFIG_TEMPLATE="${SLAM_CONFIG_TEMPLATE:-/opt/ros2-slam/config/rover_2d.lua}"
SLAM_CONFIG_DIR="${SLAM_CONFIG_DIR:-/tmp/ros2-slam-config}"
SLAM_CONFIG_BASENAME="${SLAM_CONFIG_BASENAME:-rover_2d.lua}"
SLAM_RESOLUTION="${SLAM_RESOLUTION:-0.05}"
SLAM_PUBLISH_PERIOD_SEC="${SLAM_PUBLISH_PERIOD_SEC:-0.5}"
SLAM_MAP_FILE_PATH="${SLAM_MAP_FILE_PATH:-/app/lidar/slam.json}"
SLAM_TRACKING_FRAME="${SLAM_TRACKING_FRAME:-}"
SLAM_WAIT_SCAN_SEC="${SLAM_WAIT_SCAN_SEC:-60}"
SLAM_FILTERED_TOPIC="${SLAM_FILTERED_TOPIC:-/scan_filtered}"
SLAM_CORRECTED_LASER_FRAME="${SLAM_CORRECTED_LASER_FRAME:-base_laser_slam}"
# LiDAR is mounted 15 cm forward of the rover's base_link/rotation center.
SLAM_LIDAR_X_M="${SLAM_LIDAR_X_M:-0.15}"
SLAM_LIDAR_Y_M="${SLAM_LIDAR_Y_M:-0.0}"
# Fresh session on every container start (avoids stale marks duplicated onto a new map).
SLAM_WIPE_ON_START="${SLAM_WIPE_ON_START:-true}"
SLAM_PERSISTENT_MAP_PATH="${SLAM_PERSISTENT_MAP_PATH:-/app/lidar/maps/persistent_grid.json}"
SLAM_WAYPOINTS_PATH="${SLAM_WAYPOINTS_PATH:-/app/lidar/maps/waypoints.json}"
SLAM_PURGE_REQUEST_PATH="${SLAM_PURGE_REQUEST_PATH:-/app/lidar/.purge_slam}"
SLAM_FREEZE_REQUEST_PATH="${SLAM_FREEZE_REQUEST_PATH:-/app/lidar/.freeze_slam}"
SLAM_MODE_PATH="${SLAM_MODE_PATH:-/app/lidar/maps/slam_mode}"
SLAM_FROZEN_STATE_PATH="${SLAM_FROZEN_STATE_PATH:-/app/lidar/maps/frozen.pbstream}"
SLAM_FREEZE_POSE_PATH="${SLAM_FREEZE_POSE_PATH:-/app/lidar/maps/freeze_pose.json}"
SLAM_BASELINE_GRID_PATH="${SLAM_BASELINE_GRID_PATH:-/app/lidar/maps/baseline_grid.json}"
SLAM_REPOSITION_REQUEST_PATH="${SLAM_REPOSITION_REQUEST_PATH:-/app/lidar/.reposition_slam}"
SLAM_GLOBAL_LOCALIZATION_PATH="${SLAM_GLOBAL_LOCALIZATION_PATH:-/app/lidar/.global_localization}"
SLAM_GLOBAL_LOCALIZATION_ACTIVE_PATH="${SLAM_GLOBAL_LOCALIZATION_ACTIVE_PATH:-/app/lidar/.global_localization_active}"
NAV_COMMAND_PATH="${NAV_COMMAND_PATH:-/app/lidar/navigation_command.json}"
NAV_KILL_PATH="${NAV_KILL_PATH:-/app/lidar/navigation_kill.json}"
SLAM_IMU_URL="${SLAM_IMU_URL:-https://rover.tail9d0237.ts.net:3000/api/sensors/imu}"
SLAM_IMU_TOPIC="${SLAM_IMU_TOPIC:-/imu}"
SLAM_IMU_FRAME="${SLAM_IMU_FRAME:-base_link}"
SLAM_IMU_READY_PATH="${SLAM_IMU_READY_PATH:-/tmp/ros2-slam-imu-ready}"
SLAM_IMU_WAIT_SEC="${SLAM_IMU_WAIT_SEC:-20}"
# Default off: Cartographer IMU is all-or-nothing and a biased Pi gyro can
# corrupt lidar-only localization / reposition. Opt in with SLAM_USE_IMU=true.
SLAM_USE_IMU="${SLAM_USE_IMU:-false}"

mkdir -p "${CYCLONEDDS_CONFIG_DIR}" "${SLAM_CONFIG_DIR}"

latch_navigation_shutdown() {
  # SLAM loss invalidates the map pose, so autonomous navigation must be
  # canceled and remain inhibited until a deliberate new goto clears the latch.
  mkdir -p "$(dirname "${NAV_COMMAND_PATH}")" "$(dirname "${NAV_KILL_PATH}")" 2>/dev/null || true
  local seq ts
  seq="$(date +%s%3N)"
  ts="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
  printf '{"latched":true,"reason":"slam_shutdown","updatedAt":"%s"}\n' "${ts}" \
    > "${NAV_KILL_PATH}.tmp" 2>/dev/null || true
  mv -f "${NAV_KILL_PATH}.tmp" "${NAV_KILL_PATH}" 2>/dev/null || true
  printf '{"op":"cancel","seq":%s,"ts":"%s"}\n' "${seq}" "${ts}" \
    > "${NAV_COMMAND_PATH}.tmp" 2>/dev/null || true
  mv -f "${NAV_COMMAND_PATH}.tmp" "${NAV_COMMAND_PATH}" 2>/dev/null || true
}

wipe_slam_session() {
  local wipe="${SLAM_WIPE_ON_START,,}"
  local forced="false"
  if [[ -f "${SLAM_PURGE_REQUEST_PATH}" ]]; then
    forced="true"
    echo "ros2-slam: purge request found; forcing clean map + marks"
  fi
  local mode="mapping"
  if [[ -f "${SLAM_MODE_PATH}" ]]; then
    mode="$(tr -d '[:space:]' < "${SLAM_MODE_PATH}")"
  fi
  if [[ "${forced}" != "true" && "${mode}" == "localization" && -f "${SLAM_FROZEN_STATE_PATH}" ]]; then
    echo "ros2-slam: preserving frozen map for localization mode"
    return 0
  fi
  if [[ "${forced}" != "true" && "${wipe}" != "1" && "${wipe}" != "true" && "${wipe}" != "yes" && "${wipe}" != "on" ]]; then
    echo "ros2-slam: keeping persistent map/waypoints (SLAM_WIPE_ON_START=${SLAM_WIPE_ON_START})"
    return 0
  fi
  echo "ros2-slam: wiping map + marks for a clean session"
  mkdir -p /app/lidar/maps
  rm -f \
    "${SLAM_MAP_FILE_PATH}" \
    "${SLAM_MAP_FILE_PATH}.tmp" \
    "${SLAM_PERSISTENT_MAP_PATH}" \
    "${SLAM_PERSISTENT_MAP_PATH}.tmp" \
    "${SLAM_WAYPOINTS_PATH}" \
    "${SLAM_WAYPOINTS_PATH}.tmp" \
    /app/lidar/slam_live.json \
    /app/lidar/navigation_path.json \
    /app/lidar/navigation_goal.json \
    /app/lidar/navigation_command.json \
    /app/lidar/navigation_status.json \
    /app/lidar/maps/*.pbstream \
    /app/lidar/maps/*.pgm \
    /app/lidar/maps/*.yaml \
    "${SLAM_MODE_PATH}" \
    "${SLAM_FREEZE_REQUEST_PATH}" \
    "${SLAM_FREEZE_POSE_PATH}" \
    "${SLAM_BASELINE_GRID_PATH}" \
    "${SLAM_BASELINE_GRID_PATH}.tmp" \
    "${SLAM_REPOSITION_REQUEST_PATH}" \
    "${SLAM_GLOBAL_LOCALIZATION_PATH}" \
    "${SLAM_GLOBAL_LOCALIZATION_ACTIVE_PATH}" \
    2>/dev/null || true
  rm -f "${SLAM_PURGE_REQUEST_PATH}" 2>/dev/null || true
  # Empty waypoints file so readers don't see a missing-file race as "keep old".
  printf '%s\n' '{"version":1,"updated_at":0,"waypoints":[]}' > "${SLAM_WAYPOINTS_PATH}"
}

resolve_peer() {
  local peer="$1"
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
      <MaxAutoParticipantIndex>200</MaxAutoParticipantIndex>
      <Peers>
        <Peer Address="${ROVER_DDS_PEER_RESOLVED}" />
        <Peer Address="${LOCAL_DDS_PEER}" />
      </Peers>
    </Discovery>
  </Domain>
</CycloneDDS>
EOF

export ROS_DOMAIN_ID
export RMW_IMPLEMENTATION
export ROS_LOCALHOST_ONLY
export CYCLONEDDS_URI="file://${CYCLONEDDS_CONFIG_PATH}"
export LIDAR_TOPIC
export SLAM_MAP_FILE_PATH
export PYTHONPATH="/opt/ros2-slam:${PYTHONPATH:-}"

source /opt/ros/humble/setup.bash

echo "ros2-slam: domain=${ROS_DOMAIN_ID} rmw=${RMW_IMPLEMENTATION} topic=${LIDAR_TOPIC}"
echo "ros2-slam: interfaces=${DDS_LOCAL_INTERFACE}(1),${DDS_TAILSCALE_INTERFACE}(2) external_address=${SERVER_DDS_EXTERNAL_ADDRESS}"
echo "ros2-slam: rover_peer=${ROVER_DDS_PEER} (${ROVER_DDS_PEER_RESOLVED}) local_peer=${LOCAL_DDS_PEER}"
echo "ros2-slam: cyclonedds=${CYCLONEDDS_URI}"

detect_laser_frame() {
  if [[ -n "${SLAM_TRACKING_FRAME}" ]]; then
    printf '%s' "${SLAM_TRACKING_FRAME}"
    return
  fi
  python3 - <<'PY'
import os, time
import rclpy
from rclpy.qos import qos_profile_sensor_data
from sensor_msgs.msg import LaserScan

topic = os.environ.get("LIDAR_TOPIC", "/scan")
timeout = float(os.environ.get("SLAM_WAIT_SCAN_SEC", "60"))
rclpy.init()
node = rclpy.create_node("slam_frame_probe")
holder = {"frame": None}

def cb(msg):
    holder["frame"] = msg.header.frame_id or "laser"

node.create_subscription(LaserScan, topic, cb, qos_profile_sensor_data)
deadline = time.time() + timeout
while holder["frame"] is None and time.time() < deadline and rclpy.ok():
    rclpy.spin_once(node, timeout_sec=0.25)
node.destroy_node()
rclpy.shutdown()
print(holder["frame"] or "laser")
PY
}

write_lua_config() {
  local out="${SLAM_CONFIG_DIR}/${SLAM_CONFIG_BASENAME}"
  # cartographer (C++ lib) installs share files but is not a ros2 package name;
  # only cartographer_ros / cartographer_ros_msgs appear in `ros2 pkg list`.
  local carto_cfg="/opt/ros/humble/share/cartographer/configuration_files"
  local share_cfg="/opt/ros/humble/share/cartographer_ros/configuration_files"
  if [[ -d "${carto_cfg}" ]]; then
    cp -a "${carto_cfg}/." "${SLAM_CONFIG_DIR}/"
  fi
  if [[ -d "${share_cfg}" ]]; then
    cp -a "${share_cfg}/." "${SLAM_CONFIG_DIR}/"
  fi
  if [[ ! -f "${SLAM_CONFIG_DIR}/map_builder.lua" ]]; then
    echo "ros2-slam: missing map_builder.lua in ${SLAM_CONFIG_DIR}" >&2
    return 1
  fi
  cp "${SLAM_CONFIG_TEMPLATE}" "${out}"
  local use_imu="false"
  case "${SLAM_USE_IMU,,}" in
    1|true|yes|on) use_imu="true" ;;
    0|false|no|off) use_imu="false" ;;
    *)
      if [[ -f "${SLAM_IMU_READY_PATH}" ]]; then
        use_imu="true"
      fi
      ;;
  esac
  python3 - "${out}" "${use_imu}" <<'PY'
import sys
path, use_imu = sys.argv[1], sys.argv[2]
with open(path, encoding="utf-8") as handle:
    config = handle.read()
old = "TRAJECTORY_BUILDER_2D.use_imu_data = false"
new = f"TRAJECTORY_BUILDER_2D.use_imu_data = {use_imu}"
if old not in config and f"TRAJECTORY_BUILDER_2D.use_imu_data = {use_imu}" not in config:
    # Already patched or unexpected template — force-replace any boolean.
    import re
    config2, n = re.subn(
        r"TRAJECTORY_BUILDER_2D\.use_imu_data\s*=\s*(true|false)",
        new,
        config,
        count=1,
    )
    if n != 1:
        raise SystemExit("could not patch use_imu_data in rover_2d.lua")
    config = config2
else:
    config = config.replace(old, new, 1)
with open(path, "w", encoding="utf-8") as handle:
    handle.write(config)
print(use_imu)
PY
  echo "ros2-slam: use_imu_data=$(tr -d '[:space:]' <<<"${use_imu}") url=${SLAM_IMU_URL}" >&2
  local mode="mapping"
  if [[ -f "${SLAM_MODE_PATH}" ]]; then
    mode="$(tr -d '[:space:]' < "${SLAM_MODE_PATH}")"
  fi
  if [[ "${mode}" == "localization" && -f "${SLAM_FROZEN_STATE_PATH}" ]]; then
    python3 - "${out}" "${SLAM_GLOBAL_LOCALIZATION_PATH}" <<'PY'
import os
import sys

path = sys.argv[1]
global_localization_path = sys.argv[2]
with open(path, encoding="utf-8") as handle:
    config = handle.read()
needle = "return options"
if os.path.isfile(global_localization_path):
    localization_tuning = """
-- Explicit Reposition / kidnap: allow distant matches (score floor lower than
-- normal tracking so we can snap after a bad freeze_pose / IMU episode).
POSE_GRAPH.optimize_every_n_nodes = 1
POSE_GRAPH.global_sampling_ratio = 1.
POSE_GRAPH.constraint_builder.sampling_ratio = 1.
POSE_GRAPH.constraint_builder.max_constraint_distance = 100.
POSE_GRAPH.constraint_builder.fast_correlative_scan_matcher.linear_search_window = 12.
POSE_GRAPH.constraint_builder.fast_correlative_scan_matcher.angular_search_window = math.rad(45.)
POSE_GRAPH.constraint_builder.min_score = 0.55
POSE_GRAPH.constraint_builder.global_localization_min_score = 0.55
POSE_GRAPH.global_constraint_search_after_n_seconds = 1.
TRAJECTORY_BUILDER_2D.real_time_correlative_scan_matcher.linear_search_window = 0.5
TRAJECTORY_BUILDER_2D.real_time_correlative_scan_matcher.angular_search_window = math.rad(25.)
TRAJECTORY_BUILDER_2D.real_time_correlative_scan_matcher.translation_delta_cost_weight = 5.
"""
else:
    localization_tuning = """
-- Normal localization: stay near last pose; no periodic global teleport search.
POSE_GRAPH.optimize_every_n_nodes = 30
POSE_GRAPH.constraint_builder.max_constraint_distance = 2.0
POSE_GRAPH.constraint_builder.min_score = 0.78
POSE_GRAPH.constraint_builder.global_localization_min_score = 0.78
POSE_GRAPH.constraint_builder.fast_correlative_scan_matcher.linear_search_window = 2.0
POSE_GRAPH.constraint_builder.fast_correlative_scan_matcher.angular_search_window = math.rad(15.)
POSE_GRAPH.global_constraint_search_after_n_seconds = 1e9
"""
localization = f"""
-- Frozen structural map: retain only a few live localization submaps.
TRAJECTORY_BUILDER.pure_localization_trimmer = {{
  max_submaps_to_keep = 3,
}}
{localization_tuning}

return options"""
if needle not in config:
    raise SystemExit(f"missing {needle!r} in {path}")
with open(path, "w", encoding="utf-8") as handle:
    handle.write(config.rsplit(needle, 1)[0] + localization + "\n")
PY
    echo "ros2-slam: localization mode using frozen state ${SLAM_FROZEN_STATE_PATH}" >&2
  fi
  printf '%s' "${out}"
}

start_slam() {
  wipe_slam_session
  latch_navigation_shutdown

  local laser_frame
  laser_frame="$(detect_laser_frame)"
  local corrected_laser_frame="${SLAM_CORRECTED_LASER_FRAME}"
  # Cartographer tracks base_link; static TF links it to the LaserScan frame_id.
  local tracking_frame="${SLAM_TRACKING_FRAME:-base_link}"
  echo "ros2-slam: tracking_frame=${tracking_frame} source_laser_frame=${laser_frame} corrected_laser_frame=${corrected_laser_frame}"
  printf '%s' "${tracking_frame}" > /tmp/ros2-slam-tracking-frame
  printf '%s' "${corrected_laser_frame}" > /tmp/ros2-slam-laser-frame
  export SLAM_TRACKING_FRAME="${tracking_frame}"
  export SLAM_LASER_FRAME="${corrected_laser_frame}"
  export SLAM_CORRECTED_LASER_FRAME="${corrected_laser_frame}"
  export SLAM_LIDAR_X_M SLAM_LIDAR_Y_M
  export SLAM_FILTERED_TOPIC
  export SLAM_IMU_URL SLAM_IMU_TOPIC SLAM_IMU_FRAME SLAM_IMU_READY_PATH

  local pids=()
  cleanup() {
    latch_navigation_shutdown
    local pid
    for pid in "${pids[@]:-}"; do
      kill "${pid}" 2>/dev/null || true
    done
  }
  trap cleanup EXIT INT TERM

  # The filter rewrites scans to this private frame. Include the real sensor
  # offset so a turn is represented around base_link, not around the LiDAR.
  ros2 run tf2_ros static_transform_publisher \
    --x "${SLAM_LIDAR_X_M}" --y "${SLAM_LIDAR_Y_M}" --z 0 \
    --yaw -1.57079632679 --pitch 0 --roll 0 \
    --frame-id "${tracking_frame}" \
    --child-frame-id "${corrected_laser_frame}" &
  pids+=($!)

  python3 /opt/ros2-slam/scan_filter.py &
  pids+=($!)

  # Pi IMU → /imu before Cartographer starts (use_imu_data blocks without it).
  rm -f "${SLAM_IMU_READY_PATH}" 2>/dev/null || true
  case "${SLAM_USE_IMU,,}" in
    0|false|no|off)
      echo "ros2-slam: IMU disabled (SLAM_USE_IMU=${SLAM_USE_IMU})"
      ;;
    *)
      python3 /opt/ros2-slam/imu_bridge.py &
      pids+=($!)
      local imu_deadline=$((SECONDS + ${SLAM_IMU_WAIT_SEC%.*}))
      while (( SECONDS < imu_deadline )); do
        if [[ -f "${SLAM_IMU_READY_PATH}" ]]; then
          echo "ros2-slam: IMU ready"
          break
        fi
        sleep 0.25
      done
      if [[ ! -f "${SLAM_IMU_READY_PATH}" ]]; then
        echo "ros2-slam: IMU not ready after ${SLAM_IMU_WAIT_SEC}s — Cartographer stays lidar-only" >&2
      fi
      ;;
  esac

  write_lua_config >/dev/null

  # Give the filter / static TF a moment so Cartographer does not start empty.
  sleep 1

  local cartographer_args=(
    -configuration_directory "${SLAM_CONFIG_DIR}"
    -configuration_basename "${SLAM_CONFIG_BASENAME}"
  )
  if [[ -f "${SLAM_MODE_PATH}" && -f "${SLAM_FROZEN_STATE_PATH}" ]] \
    && [[ "$(tr -d '[:space:]' < "${SLAM_MODE_PATH}")" == "localization" ]]; then
    cartographer_args+=(
      "-load_state_filename=${SLAM_FROZEN_STATE_PATH}"
      -load_frozen_state=true
      -start_trajectory_with_default_topics=false
    )
  fi

  ros2 run cartographer_ros cartographer_node "${cartographer_args[@]}" \
    --ros-args \
    -r scan:="${SLAM_FILTERED_TOPIC}" \
    -r imu:="${SLAM_IMU_TOPIC}" \
    -p use_sim_time:=false &
  pids+=($!)

  if [[ -f "${SLAM_MODE_PATH}" && -f "${SLAM_FROZEN_STATE_PATH}" ]] \
    && [[ "$(tr -d '[:space:]' < "${SLAM_MODE_PATH}")" == "localization" ]]; then
    SLAM_CONFIG_DIR="${SLAM_CONFIG_DIR}" \
    SLAM_CONFIG_BASENAME="${SLAM_CONFIG_BASENAME}" \
    SLAM_FREEZE_POSE_PATH="${SLAM_FREEZE_POSE_PATH}" \
    SLAM_GLOBAL_LOCALIZATION_PATH="${SLAM_GLOBAL_LOCALIZATION_PATH}" \
    SLAM_GLOBAL_LOCALIZATION_ACTIVE_PATH="${SLAM_GLOBAL_LOCALIZATION_ACTIVE_PATH}" \
      python3 /opt/ros2-slam/start_localization.py
  fi

  ros2 run cartographer_ros cartographer_occupancy_grid_node \
    -resolution "${SLAM_RESOLUTION}" \
    -publish_period_sec "${SLAM_PUBLISH_PERIOD_SEC}" \
    --ros-args -p use_sim_time:=false &
  pids+=($!)

  python3 /opt/ros2-slam/map_bridge.py &
  pids+=($!)

  echo "ros2-slam: filter(${LIDAR_TOPIC}->${SLAM_FILTERED_TOPIC}) + imu(${SLAM_IMU_TOPIC}) + cartographer + map bridge"
  wait -n "${pids[@]}" || true
  cleanup
  wait || true
}

case "${1:-slam}" in
  slam|cartographer)
    start_slam
    ;;
  bridge-only)
    exec python3 /opt/ros2-slam/map_bridge.py
    ;;
  echo)
    shift
    exec ros2 topic echo "${LIDAR_TOPIC}" "$@"
    ;;
  hz)
    exec ros2 topic hz "${LIDAR_TOPIC}"
    ;;
  list)
    exec ros2 topic list
    ;;
  bash)
    shift
    exec bash "$@"
    ;;
  *)
    exec "$@"
    ;;
esac
