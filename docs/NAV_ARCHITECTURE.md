# Rover navigation architecture

Audit date: 2026-08-25. Marker / AprilTag docking is **not** implemented yet;
this document covers SLAM → Nav2 → continuous `/cmd_vel` → motors.

## A. Architecture (target)

```
LD19 /scan
    → scan_filter (/scan_filtered, /scan_nav)
Pi IMU HTTP /api/sensors/imu
    → ros2-nav NavImuAssist (soft: early-stop yaw/XY pulses only)
    → imu_bridge (/imu) available but Cartographer use_imu_data=false by default
         publishes: /map, TF map→odom→base_link

TF map→odom→base_link
    → odom republisher → /odom
    → Nav2 (NavFn + Regulated Pure Pursuit + costmaps)
         action: navigate_to_pose
         topic:  /cmd_vel  (linear.x, angular.z only)

/cmd_vel
    → bridges.CmdVelBridge (watchdog + accel limit)
    → drive_interface.twist_to_pi_drive
    → HTTP POST /api/navigation/drive {x,y}  (Pi analog stick)
    → skid-steer left/right PWM → 4 motors
```

## B. Old vs new

| Old | New |
| --- | --- |
| Nav2 plans path, then bridge **cancels** Nav2 | Nav2 **owns** the whole trip |
| Path → straight segments → A/D/W pulses | Continuous Twist → calibrated stick |
| 3 s observe/settle after every pulse | Controller frequency (~20 Hz) + accel limits |
| Multiple `/cmd_vel` owners (segment + dock hold) | One owner: Nav2 → bridge consumer |
| Goal checker XY=0.35, yaw=π + WASD fine dock | XY=0.18 m, yaw=0.25 rad via Nav2 |
| `fine_dock.py` / `lateral_maneuver` WASD | Deferred until marker stack exists |

## C–E. File plan

| File | Action | Why |
| --- | --- | --- |
| `ros2-nav/bridges.py` | **REWRITE** | Remove segment/WASD; continuous drive + goals |
| `ros2-nav/drive_interface.py` | **NEW** | Calibrated Twist↔Pi stick |
| `ros2-nav/config/nav2_params.yaml` | **NEW (restored)** | Was only in Docker image |
| `ros2-nav/config/navigate_stable.xml` | **NEW (restored)** | BT without blind reverse |
| `ros2-nav/Dockerfile` | **NEW (restored)** | Build image from repo |
| `ros2-nav/cmd_vel_bridge.py` | **MODIFY** | Use `drive_interface` |
| `ros2-nav/nav_context.py` | **MODIFY** | Drop pulse/settle config |
| `ros2-nav/segment_nav.py` | **KEEP (deprecated)** | Tests / replay only |
| `ros2-nav/lateral_maneuver.py` | **KEEP (deprecated)** | Tests only |
| `ros2-nav/fine_dock.py` | **KEEP (deprecated)** | Not launched |
| `ros2-nav/goal_server.py` | **KEEP** | Legacy standalone; bridges embeds goals |
| `ros2-nav/odom_republisher.py` | **KEEP** | Same idea; also in bridges |
| `ros2-slam/*` | **KEEP** | Working lidar SLAM — do not change |
| `src/routes/navigation.js` | **RESTORE** | Missing from workspace; still in image |
| Dashboard SlamMap labels | **MODIFY** | Phase 1 = Nav2 Approach |

## F. Nav2 plugins

- **Planner:** `NavfnPlanner` (A*) — reliable indoor grid planning on Cartographer `/map`.
- **Controller:** `RegulatedPurePursuitController` — best fit for a small non-holonomic skid-steer rover: rotate-to-heading, regulated speed near obstacles, no `linear.y`. DWB/MPPI are heavier and benefit more from good wheel odom; we have **no encoders**.
- **Goal checker:** `SimpleGoalChecker` (0.18 m / 0.25 rad).
- **Progress checker:** `SimpleProgressChecker` (60 s / 0.05 m).
- **BT:** `navigate_stable.xml` — clear+replan, spin escape; no reverse into walls.

## G. Important parameters (starting values)

| Param | Value | Notes |
| --- | --- | --- |
| `desired_linear_vel` | 0.30 m/s | ≈ stick y=−0.6 |
| `max_velocity` smoother | 0.35 / 0 / 0.80 | Cap below 0.56 m/s full stick |
| `xy_goal_tolerance` | 0.18 m | |
| `yaw_goal_tolerance` | 0.25 rad (~14°) | |
| `rotate_to_heading_min_angle` | 0.35 rad | |
| `inflation_radius` local/global | 0.40 / 0.55 m | Footprint ~0.35 m |
| `NAV_CMD_VEL_STALE_SEC` | 0.35 | Hardware-facing stop |
| `NAV_TF_STALE_SEC` | 1.0 | Cancel nav if TF lost |

## H. TF tree

```
map
 └─ odom          (Cartographer provide_odom_frame=true)
     └─ base_link
         └─ base_laser / laser frame (static TF from slam entrypoint)
```

No wheel-odom TF publisher. Cartographer runs `use_odometry=false` and
`use_imu_data=false` by default (lidar-only SLAM). Navigation uses Pi gyro as a
**soft assist** (`ros2-nav/nav_imu.py`): bias-corrected gz early-stops yaw/face
pulses; gy can shorten near-goal forward pulses. SLAM pose still decides success.
Disable with `NAV_USE_IMU=false`. Opt Cartographer IMU in only via `SLAM_USE_IMU=true`.

## I. ROS interfaces

| Type | Name |
| --- | --- |
| Action | `/navigate_to_pose` (`nav2_msgs/NavigateToPose`) |
| Topic | `/cmd_vel` `geometry_msgs/Twist` |
| Topic | `/odom` `nav_msgs/Odometry` (TF republish) |
| Topic | `/map`, `/scan_nav`, `/plan` |
| HTTP | relay `/api/navigation/goto|cancel|pause|status|drive` |
| File | `/app/lidar/navigation_command.json` (goto bridge) |

## J. Docking state machine (current / future)

**Now (no markers):**

```
idle → navigating (Nav2 Approach) → idle[succeeded|canceled|aborted|localization_lost]
```

**Later (when markers ready):**

```
idle → navigating (approach pose)
    → marker_acquire
    → docking (DockRobot or visual servo)
    → idle
```

## K. Safety

- `/cmd_vel` watchdog: stop if no Twist for `NAV_CMD_VEL_STALE_SEC`
- Kill file / pause: bridge posts zero drive
- TF stale: cancel NavigateToPose
- Drive POST failures: log + keep commanding stop
- Costmap obstacle layer + RPP collision detection
- No autonomous reverse (no rear lidar)
- `linear.y` ignored

## L. Test procedure

```bash
# Unit
cd ros2-nav && python3 -m unittest test_drive_interface.py -v

# After deploying ros2-nav image
ros2 run tf2_tools view_frames   # expect map→odom→base_link
ros2 topic echo /cmd_vel --once # during nav: continuous non-zero then settle
# Dashboard: freeze map → goto waypoint → watch continuous drive (no 3s settles)
```

## M. Hardware limitations

- **No wheel encoders** — cannot run classical `diff_drive_controller` odometry.
- **IMU on Pi** — dashboard/drive-assist + Cartographer via `imu_bridge` (`SLAM_IMU_URL`).
- Motors driven via the Pi's **persistent WebSocket analog-stick protocol**,
  matching dashboard joystick/gamepad control; HTTP analog stick remains an
  explicit compatibility mode for co-sim and legacy deployments.
- Marker detector **not in this repo**.

## N. Remaining debt

- Wire Nav2 `velocity_smoother` into BT remapping if jerky.
- Optional `robot_localization` / wheel encoders if they appear.
- Marker acquire + DockRobot / visual servo (phases 2–3).
- Delete deprecated `segment_nav` / `fine_dock` after confidence period.
- Restore `ros2-nav` / `ros2-slam` compose services into `docker-compose.yml` if still run ad-hoc.

## O. Build / run

```bash
# Build nav image
docker build -f ros2-nav/Dockerfile -t relay-ros2-nav:local .

# Run (host net + lidar volume + direct Pi WebSocket drive)
docker run --network host --rm \
  -v lidar-data:/app/lidar \
  -e NAV_DRIVE_WS_URL=wss://rover.tail9d0237.ts.net:3000 \
  -e NAV_DRIVE_TRANSPORT=ws \
  -e NAVIGATION_API_TOKEN=... \
  relay-ros2-nav:local nav

# Relay still proxies goto → navigation_command.json
```

## P. True Nav2 co-sim (preferred for nav debugging)

Sim no longer “mimics” RPP. The plant publishes Cartographer-shaped interfaces;
**real** `ros2-nav` (Nav2 + bridges) drives the plant over the compatibility
HTTP API, selected explicitly with `NAV_DRIVE_TRANSPORT=http`.

```
sim plant: /map, /scan_nav, TF map→odom→base_link
         + HTTP POST /api/navigation/drive  (Pi stick)
    ← real ros2-nav (Nav2 RPP + bridges, slam:=True)
Goals: GUI click → :8768/goto → NavigateToPose
```

```bash
./scripts/run_cosim.sh
# or: docker compose -p rover-cosim -f docker-compose.cosim.yml up --build

# Observe: http://127.0.0.1:8879/  (goals go to Nav2, not sim autopilot)
# Drive:   http://127.0.0.1:8880/api/navigation/drive
# Goals:   http://127.0.0.1:8769/goto
# Domain 87 + ROS_LOCALHOST_ONLY=1 — will not reach a physical rover
# Ports are offset from live GUI :8877 / goals :8768
```

Internal autopilot (`python3 -m sim` without `--cosim`) remains for map/SLAM
regressions only. Use co-sim for anything that should match live Nav2 behavior.
