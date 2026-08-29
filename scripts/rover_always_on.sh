#!/usr/bin/env bash
# Ensure the non-rover-dependent application containers are running at boot.
set -u
set -o pipefail

# Space-separated Docker container names. These stay up even when the rover is
# offline so the relay can report offline state and serve the dashboard.
ROVER_ALWAYS_ON_CONTAINERS="${ROVER_ALWAYS_ON_CONTAINERS:-relay-relay-1 relay-control-dashboard-1 control_server}"

printf '[rover-always-on] ensuring: %s\n' "$ROVER_ALWAYS_ON_CONTAINERS"
for name in $ROVER_ALWAYS_ON_CONTAINERS; do
  if ! docker inspect "$name" >/dev/null 2>&1; then
    printf '[rover-always-on] missing container: %s\n' "$name" >&2
    continue
  fi
  running="$(docker inspect -f '{{.State.Running}}' "$name" 2>/dev/null || true)"
  if [[ "$running" == "true" ]]; then
    continue
  fi
  if docker start "$name" >/dev/null; then
    printf '[rover-always-on] started %s\n' "$name"
  else
    printf '[rover-always-on] failed to start %s\n' "$name" >&2
  fi
done
