#!/usr/bin/env bash
set -eo pipefail

ROS_DOMAIN_ID="${ROS_DOMAIN_ID:-0}"
RMW_IMPLEMENTATION="${RMW_IMPLEMENTATION:-rmw_cyclonedds_cpp}"
ROS_LOCALHOST_ONLY="${ROS_LOCALHOST_ONLY:-0}"
NAV_SCAN_TOPIC="${NAV_SCAN_TOPIC:-/scan}"
DDS_LOCAL_INTERFACE="${DDS_LOCAL_INTERFACE:-lo}"
DDS_TAILSCALE_INTERFACE="${DDS_TAILSCALE_INTERFACE:-tailscale0}"
SERVER_DDS_EXTERNAL_ADDRESS="${SERVER_DDS_EXTERNAL_ADDRESS:-100.96.16.121}"
ROVER_DDS_PEER="${ROVER_DDS_PEER:-100.109.197.90}"
LOCAL_DDS_PEER="${LOCAL_DDS_PEER:-127.0.0.1}"
CYCLONEDDS_CONFIG_DIR="${CYCLONEDDS_CONFIG_DIR:-/etc/cyclonedds}"
CYCLONEDDS_CONFIG_PATH="${CYCLONEDDS_CONFIG_DIR}/cyclonedds.xml"

mkdir -p "${CYCLONEDDS_CONFIG_DIR}"

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
      <MaxAutoParticipantIndex>50</MaxAutoParticipantIndex>
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
export NAV_SCAN_TOPIC

source /opt/ros/humble/setup.bash

echo "ros2-navigation: domain=${ROS_DOMAIN_ID} topic=${NAV_SCAN_TOPIC} pi=${NAV_PI_BASE_URL:-unset}"

case "${1:-run}" in
  test)
    exec python3 -m unittest discover -s /opt/ros2-navigation -p 'test_*.py' -v
    ;;
  run|*)
    exec python3 /opt/ros2-navigation/navigation_node.py
    ;;
esac
