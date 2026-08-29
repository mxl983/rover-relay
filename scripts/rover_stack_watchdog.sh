#!/usr/bin/env bash
# Start/stop the compute-heavy rover containers based on rover liveness.
#
# This deliberately runs on the Docker host, outside the relay compose stack.
# It must remain alive while ros2-lidar/ros2-slam/ros2-nav are stopped.
set -u
set -o pipefail

ROVER_WATCHDOG_URL="${ROVER_WATCHDOG_URL:-https://rover.tail9d0237.ts.net:3000/health}"
ROVER_WATCHDOG_TOKEN="${ROVER_WATCHDOG_TOKEN:-}"
ROVER_WATCHDOG_INTERVAL_SEC="${ROVER_WATCHDOG_INTERVAL_SEC:-15}"
ROVER_WATCHDOG_TIMEOUT_SEC="${ROVER_WATCHDOG_TIMEOUT_SEC:-5}"
ROVER_WATCHDOG_ONLINE_SUCCESSES="${ROVER_WATCHDOG_ONLINE_SUCCESSES:-2}"
ROVER_WATCHDOG_OFFLINE_FAILURES="${ROVER_WATCHDOG_OFFLINE_FAILURES:-3}"
ROVER_WATCHDOG_OFFLINE_GRACE_SEC="${ROVER_WATCHDOG_OFFLINE_GRACE_SEC:-90}"
ROVER_WATCHDOG_INSECURE="${ROVER_WATCHDOG_INSECURE:-false}"

# Space-separated Docker container names. Keep relay/control-dashboard out.
ROVER_WATCHDOG_CONTAINERS="${ROVER_WATCHDOG_CONTAINERS:-relay-ros2-lidar-1 relay-ros2-slam-1 relay-ros2-nav-1}"

log() {
  printf '[rover-stack-watchdog] %s\n' "$*"
}

positive_int() {
  [[ "$1" =~ ^[0-9]+$ ]] && ((10#$1 > 0))
}

positive_number() {
  [[ "$1" =~ ^[0-9]+([.][0-9]+)?$ ]] && awk "BEGIN { exit !($1 > 0) }"
}

if ! positive_number "$ROVER_WATCHDOG_INTERVAL_SEC" ||
  ! positive_number "$ROVER_WATCHDOG_TIMEOUT_SEC" ||
  ! positive_int "$ROVER_WATCHDOG_ONLINE_SUCCESSES" ||
  ! positive_int "$ROVER_WATCHDOG_OFFLINE_FAILURES" ||
  ! positive_int "$ROVER_WATCHDOG_OFFLINE_GRACE_SEC"; then
  log "invalid watchdog timing configuration" >&2
  exit 2
fi

if [[ "$ROVER_WATCHDOG_INSECURE" == "true" ]]; then
  CURL_TLS=(-k)
else
  CURL_TLS=()
fi

probe_rover() {
  local -a auth=()
  if [[ -n "$ROVER_WATCHDOG_TOKEN" ]]; then
    auth=(-H "Authorization: Bearer ${ROVER_WATCHDOG_TOKEN}")
  fi
  curl --fail --silent --show-error \
    --connect-timeout "$ROVER_WATCHDOG_TIMEOUT_SEC" \
    --max-time "$ROVER_WATCHDOG_TIMEOUT_SEC" \
    "${CURL_TLS[@]}" "${auth[@]}" \
    -o /dev/null "$ROVER_WATCHDOG_URL"
}

container_exists() {
  docker inspect "$1" >/dev/null 2>&1
}

container_running() {
  [[ "$(docker inspect -f '{{.State.Running}}' "$1" 2>/dev/null || true)" == "true" ]]
}

start_stack() {
  local name
  log "rover online — starting compute stack"
  for name in $ROVER_WATCHDOG_CONTAINERS; do
    if ! container_exists "$name"; then
      log "container missing: ${name} (not recreating it)"
      continue
    fi
    if container_running "$name"; then
      continue
    fi
    if docker start "$name" >/dev/null; then
      log "started ${name}"
    else
      log "failed to start ${name}" >&2
    fi
  done
}

stop_stack() {
  local name
  log "rover offline — stopping compute stack"
  for name in $ROVER_WATCHDOG_CONTAINERS; do
    if container_running "$name"; then
      if docker stop --time 15 "$name" >/dev/null; then
        log "stopped ${name}"
      else
        log "failed to stop ${name}" >&2
      fi
    fi
  done
}

online_streak=0
offline_streak=0
offline_since=0
stack_state="unknown"

log "monitoring ${ROVER_WATCHDOG_URL}"
log "containers: ${ROVER_WATCHDOG_CONTAINERS}"

while :; do
  now="$(date +%s)"
  if probe_rover >/dev/null 2>&1; then
    offline_streak=0
    offline_since=0
    ((online_streak += 1))
    if ((online_streak >= ROVER_WATCHDOG_ONLINE_SUCCESSES)) &&
      [[ "$stack_state" != "online" ]]; then
      start_stack
      stack_state="online"
    fi
  else
    online_streak=0
    ((offline_streak += 1))
    if ((offline_since == 0)); then
      offline_since="$now"
    fi
    offline_for=$((now - offline_since))
    log "rover probe failed (${offline_streak}/${ROVER_WATCHDOG_OFFLINE_FAILURES}), offline ${offline_for}s"
    if ((offline_streak >= ROVER_WATCHDOG_OFFLINE_FAILURES)) &&
      ((offline_for >= ROVER_WATCHDOG_OFFLINE_GRACE_SEC)) &&
      [[ "$stack_state" != "offline" ]]; then
      stop_stack
      stack_state="offline"
    fi
  fi
  sleep "$ROVER_WATCHDOG_INTERVAL_SEC"
done
