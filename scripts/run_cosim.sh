#!/usr/bin/env bash
# Start true Nav2 ↔ sim co-simulation on localhost (ROS_DOMAIN_ID=87).
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

mkdir -p data/lidar-cosim/maps

if ! docker image inspect relay-ros2-nav:local >/dev/null 2>&1; then
  echo "Building relay-ros2-nav:local ..."
  docker build -f ros2-nav/Dockerfile -t relay-ros2-nav:local .
fi

echo "Starting co-sim (sim plant + real Nav2)..."
echo "  GUI:        http://127.0.0.1:8879/"
echo "  Drive API:  http://127.0.0.1:8880/api/navigation/drive"
echo "  Nav2 goals: http://127.0.0.1:8769/goto"
echo "  ROS_DOMAIN_ID=87 (localhost only — will not reach a physical rover)"
echo "  (ports offset from live :8877 / :8768 to avoid clashes)"
exec docker compose -p rover-cosim -f docker-compose.cosim.yml up --build "$@"
