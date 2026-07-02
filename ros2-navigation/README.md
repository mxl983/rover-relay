# ros2-navigation

ROS 2 local planner for autonomous **roam** mode on the Mango rover.

## Behavior

- Subscribes to `/scan` (same DDS peer setup as `ros2-lidar`)
- Polls `GET /api/system/navigation` on the Pi (~2 Hz)
- When enabled, plans at 10 Hz and posts `POST /api/navigation/drive`
- **Roam** at low–medium speed (`NAV_ROAM_LINEAR`, default `0.48`)
- On forward obstacle: stop, score LiDAR sectors, escape toward the clearest opening
- Anti-oscillation: penalizes immediate reverse bearings; recovery turn after repeated alternation

## Run

```bash
docker compose up -d ros2-navigation
docker compose logs -f ros2-navigation
```

## Tests (planner only)

```bash
docker compose run --rm ros2-navigation test
```

## Pi integration

Copy modules from `vendor-rover/server/` into the onboard rover server and wire motor output from `applyNavigationDrive()`.

Set the same `NAVIGATION_API_TOKEN` on the Pi and in compose env for `ros2-navigation`.
