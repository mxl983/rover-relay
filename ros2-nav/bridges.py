#!/usr/bin/env python3
"""Single-process ROS bridges: odom republish + cmd_vel→Pi + Nav2 goal file/HTTP.

Running these as separate `rclpy.init()` processes caused one crash/SIGTERM to
invalidate another's context ("rcl node's context is invalid").
"""

from __future__ import annotations

import json
import math
import os
import ssl
import threading
import time
import urllib.error
import urllib.request
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.parse import urlparse

import rclpy
from action_msgs.msg import GoalStatus
from action_msgs.srv import CancelGoal
from geometry_msgs.msg import PoseStamped, TransformStamped, Twist
from nav2_msgs.action import NavigateToPose
from nav_msgs.msg import Odometry, Path
from rclpy.action import ActionClient
from rclpy.duration import Duration
from rclpy.executors import ExternalShutdownException, MultiThreadedExecutor
from rclpy.node import Node
from rclpy.qos import DurabilityPolicy, HistoryPolicy, QoSProfile, ReliabilityPolicy, qos_profile_sensor_data
from rclpy.time import Time
from tf2_ros import Buffer, TransformException, TransformListener
from nav_context import (
    DRIVE_ASSIST_SNAPSHOT_PATH,
    drive_assist_blocking,
    map_context,
    nav_context,
    scan_context,
)
from lateral_maneuver import (
    QUARTER_TURN_RAD,
    GapCloseConfig,
    GapCloseState,
    drive_progress_m,
    gap_close_accepted,
    next_gap_close_step,
    turn_progress_rad,
)
from segment_nav import (
    Segment,
    SegmentNavConfig,
    SegmentNavState,
    nearest_segment_index,
    next_segment_step,
    segment_aim_heading,
    segment_start_drift_m,
    segmentize_path,
    trim_path_from_pose,
)

# --- shared env ---
CMD_VEL_TOPIC = os.environ.get("NAV_CMD_VEL_TOPIC", "/cmd_vel")
DRIVE_URL = os.environ.get(
    "NAV_DRIVE_URL",
    os.environ.get("NAV_DRIVE_BASE_URL", "https://127.0.0.1:8787")
    + "/api/navigation/drive",
).rstrip("/")
if "/api/" not in DRIVE_URL:
    DRIVE_URL = DRIVE_URL.rstrip("/") + "/api/navigation/drive"
# Autonomous nav uses the Pi's WASD key protocol (same as dashboard teleop) —
# proportional stick vectors feel sluggish, especially on the initial rotate.
_drive_keys_default = (
    DRIVE_URL if DRIVE_URL.endswith("/keys") else f"{DRIVE_URL.rstrip('/')}/keys"
)
DRIVE_KEYS_URL = os.environ.get("NAV_DRIVE_KEYS_URL", _drive_keys_default).rstrip("/")
NAV_API_TOKEN = os.environ.get("NAVIGATION_API_TOKEN", "")
SSL_VERIFY = os.environ.get("NAV_SSL_VERIFY", "false").lower() not in {"0", "false", "no"}
MAX_LINEAR_MPS = float(os.environ.get("NAV_MAX_LINEAR_MPS", "0.22"))
# Map full turn PWM at this ROS wz — lower than Nav2 max so mild path corrections
# still produce decisive stick deflection (was 0.9 → tiny twitches).
MAX_ANGULAR_RPS = float(os.environ.get("NAV_MAX_ANGULAR_RPS", "0.55"))
DRIVE_MIN_LINEAR = float(os.environ.get("NAV_DRIVE_MIN_LINEAR", os.environ.get("NAV_DRIVE_MIN", "0.32")))
DRIVE_MIN_ANGULAR = float(os.environ.get("NAV_DRIVE_MIN_ANGULAR", "0.28"))
# Optional invert if hardware turn sense differs from teleop (default matches stick).
DRIVE_INVERT_ANGULAR = os.environ.get("NAV_DRIVE_INVERT_ANGULAR", "false").lower() in {
    "1",
    "true",
    "yes",
}
KEEPALIVE_HZ = float(os.environ.get("NAV_DRIVE_KEEPALIVE_HZ", "10"))
STALE_STOP_SEC = float(os.environ.get("NAV_CMD_VEL_STALE_SEC", "0.4"))
# Brief pause for a fresh lidar pose; keep short — WASD is binary (no ramp).
START_SETTLE_SEC = float(os.environ.get("NAV_START_SETTLE_SEC", "0.0"))
START_RAMP_SEC = float(os.environ.get("NAV_START_RAMP_SEC", "0.0"))
# Never combine forward with a strong heading correction (skid-steer scrubbing).
# Low threshold → most yaw corrections become pure A/D rotate-in-place.
ALIGN_ANGULAR_RPS = float(os.environ.get("NAV_ALIGN_ANGULAR_RPS", "0.22"))
# HARD RULE: after every motion pulse (align / W / midflight / dock), stop and
# wait this long before reading pose/yaw and deciding the next action.
# Localization lags — shorter settles decide on stale pose and amplify error.
OBSERVE_SETTLE_SEC = float(os.environ.get("NAV_OBSERVE_SETTLE_SEC", "3.0"))
# Pulse A/D so binary teleop turns do not overshoot into continuous spin.
# Live quietMode: held A ≈62°/s, but short pulses often lose to static friction.
TURN_PULSE_ON_PURE_SEC = float(os.environ.get("NAV_TURN_PULSE_ON_PURE_SEC", "0.45"))


def _settle_sec(env_key: str) -> float:
    """Floor every settle at OBSERVE_SETTLE_SEC — docker env must not shorten it."""
    raw = os.environ.get(env_key)
    if raw is None or raw.strip() == "":
        return OBSERVE_SETTLE_SEC
    try:
        return max(OBSERVE_SETTLE_SEC, float(raw))
    except ValueError:
        return OBSERVE_SETTLE_SEC


TURN_PULSE_OFF_PURE_SEC = _settle_sec("NAV_TURN_PULSE_OFF_PURE_SEC")
# Initial path align (phase 1) + final yaw (phase 3): observe→pulse→settle.
# Pulse width scales with live |yaw error| — large gaps get bigger taps to save time;
# near the mark / after overshoot stay small. Never precompute pulse count.
PATH_ALIGN_FINAL_TOL_RAD = float(
    os.environ.get("NAV_PATH_ALIGN_FINAL_TOL_RAD", str(math.radians(5.0)))
)
PATH_ALIGN_PULSE_LARGE_SEC = float(os.environ.get("NAV_PATH_ALIGN_PULSE_LARGE_SEC", "0.30"))
PATH_ALIGN_PULSE_MID_SEC = float(os.environ.get("NAV_PATH_ALIGN_PULSE_MID_SEC", "0.22"))
_path_align_pulse_raw = float(os.environ.get("NAV_PATH_ALIGN_PULSE_ON_SEC", "0.07"))
PATH_ALIGN_PULSE_ON_SEC = min(_path_align_pulse_raw, 0.08)  # small / near-target
PATH_ALIGN_OVERSHOOT_PULSE_ON_SEC = float(
    os.environ.get("NAV_PATH_ALIGN_OVERSHOOT_PULSE_ON_SEC", "0.035")
)
PATH_ALIGN_SETTLE_SEC = _settle_sec("NAV_PATH_ALIGN_SETTLE_SEC")
# Same full observe settle for large yaw — do NOT shorten (SLAM needs the wait).
PATH_ALIGN_LARGE_SETTLE_SEC = _settle_sec("NAV_PATH_ALIGN_LARGE_SETTLE_SEC")
PATH_ALIGN_WAIT_PATH_SEC = float(os.environ.get("NAV_PATH_ALIGN_WAIT_PATH_SEC", "3.0"))
# Legacy aliases
PATH_ALIGN_COARSE_TOL_RAD = PATH_ALIGN_FINAL_TOL_RAD
PATH_ALIGN_FINE_PULSE_ON_SEC = PATH_ALIGN_PULSE_MID_SEC
PATH_ALIGN_MICRO_PULSE_ON_SEC = PATH_ALIGN_OVERSHOOT_PULSE_ON_SEC
PATH_ALIGN_MICRO_SETTLE_SEC = PATH_ALIGN_SETTLE_SEC


def yaw_align_pulse_sec(
    abs_err_rad: float, *, overshot: bool = False
) -> tuple[float, float, str]:
    """Return (hold_s, settle_s, mode). ≥30° large pulse; settle always full observe.

    Overshoot micro-taps only apply when nearly closed — a false overshoot with
    a large remaining gap still uses large bites the other way.
    Settle is always OBSERVE_SETTLE_SEC so localization can catch up before decide.
    """
    deg = abs(math.degrees(abs_err_rad))
    settle = PATH_ALIGN_SETTLE_SEC
    if overshot and deg < 30.0:
        return PATH_ALIGN_OVERSHOOT_PULSE_ON_SEC, settle, "overshoot"
    if deg + 1e-6 >= 30.0:
        return PATH_ALIGN_PULSE_LARGE_SEC, settle, "large"
    return PATH_ALIGN_PULSE_ON_SEC, settle, "small"
# Phase 2: straight-line segment execution (rotate → drive per segment).
SEGMENT_MIN_M = float(os.environ.get("NAV_SEGMENT_MIN_M", "0.18"))
SEGMENT_MAX_CORNER_DEG = float(os.environ.get("NAV_SEGMENT_MAX_CORNER_DEG", "25.0"))
# Skid-steer XY wanders during A/D — only replan when truly off the plan.
SEGMENT_DRIFT_REPLAN_M = float(os.environ.get("NAV_SEGMENT_DRIFT_REPLAN_M", "0.90"))
SEGMENT_FORWARD_BLOCK_M = float(os.environ.get("NAV_SEGMENT_FORWARD_BLOCK_M", "0.28"))
SEGMENT_FORWARD_OCTANTS = frozenset(
    int(x)
    for x in os.environ.get("NAV_SEGMENT_FORWARD_OCTANTS", "3,4,5,6").split(",")
    if x.strip().isdigit()
)
# Phase 2: prefer driving over rotating. Only align when yaw gap exceeds this.
# 25° left a dead zone vs midflight_max=22° — drove at ~26° yaw error and W
# never advanced along the segment (nav-20260825-030144 stall on seg 4).
SEGMENT_ALIGN_TOL_DEG = float(os.environ.get("NAV_SEGMENT_ALIGN_TOL_DEG", "12.0"))
SEGMENT_ALIGN_TOL_RAD = math.radians(SEGMENT_ALIGN_TOL_DEG)
# Pose rem not improving while pulsing W → replan (stall detector was disabled
# during segment phase so this used to hang until harness timeout).
SEGMENT_DRIVE_STALL_SEC = float(os.environ.get("NAV_SEGMENT_DRIVE_STALL_SEC", "25.0"))
# Look-ahead for phase-1 heading (avoid locking onto a tiny stub segment).
PATH_ALIGN_LOOKAHEAD_M = float(os.environ.get("NAV_PATH_ALIGN_LOOKAHEAD_M", "0.60"))
# W hold: first ~80% continuous (char ≈0.40 m/s), last ~20% small stepper.
# From scripts/live_characterize_ws.py latch holds (2026-08-25).
SEGMENT_DRIVE_CRUISE_MPS = float(os.environ.get("NAV_SEGMENT_DRIVE_CRUISE_MPS", "0.40"))
SEGMENT_DRIVE_CRUISE_FRACTION = float(
    os.environ.get("NAV_SEGMENT_DRIVE_CRUISE_FRACTION", "0.80")
)
SEGMENT_DRIVE_CRUISE_MIN_SEGMENT_M = float(
    os.environ.get("NAV_SEGMENT_DRIVE_CRUISE_MIN_SEGMENT_M", "0.50")
)
SEGMENT_DRIVE_CRUISE_MAX_S = float(os.environ.get("NAV_SEGMENT_DRIVE_CRUISE_MAX_S", "2.5"))
SEGMENT_DRIVE_PULSE_LARGE_S = float(os.environ.get("NAV_SEGMENT_DRIVE_PULSE_LARGE_S", "0.25"))
SEGMENT_DRIVE_PULSE_MID_S = float(os.environ.get("NAV_SEGMENT_DRIVE_PULSE_MID_S", "0.20"))
SEGMENT_DRIVE_PULSE_S = float(os.environ.get("NAV_SEGMENT_DRIVE_PULSE_S", "0.15"))
SEGMENT_DRIVE_PULSE_TINY_S = float(os.environ.get("NAV_SEGMENT_DRIVE_PULSE_TINY_S", "0.15"))
SEGMENT_DRIVE_SETTLE_S = _settle_sec("NAV_SEGMENT_DRIVE_SETTLE_S")
SEGMENT_DRIVE_TOL_M = float(os.environ.get("NAV_SEGMENT_DRIVE_TOL_M", "0.03"))
SEGMENT_GOAL_ARRIVE_M = float(os.environ.get("NAV_SEGMENT_GOAL_ARRIVE_M", "0.05"))
# Last ~20cm belongs to fine dock — never spin on leftover Nav2 crumbs.
SEGMENT_GOAL_HANDOFF_M = float(os.environ.get("NAV_SEGMENT_GOAL_HANDOFF_M", "0.40"))
# Mid-drive micro A/D to kill heading drift without stopping for a full spin.
SEGMENT_MIDFLIGHT_STEER_MIN_DEG = float(
    os.environ.get("NAV_SEGMENT_MIDFLIGHT_STEER_MIN_DEG", "5.0")
)
SEGMENT_MIDFLIGHT_STEER_MAX_DEG = float(
    os.environ.get("NAV_SEGMENT_MIDFLIGHT_STEER_MAX_DEG", "22.0")
)
SEGMENT_MIDFLIGHT_STEER_PULSE_S = float(
    os.environ.get("NAV_SEGMENT_MIDFLIGHT_STEER_PULSE_S", "0.035")
)
SEGMENT_MIDFLIGHT_MIN_REMAIN_M = float(
    os.environ.get("NAV_SEGMENT_MIDFLIGHT_MIN_REMAIN_M", "0.40")
)
SEGMENT_MIDFLIGHT_MIN_SEGMENT_M = float(
    os.environ.get("NAV_SEGMENT_MIDFLIGHT_MIN_SEGMENT_M", "0.45")
)
SEGMENT_MIDFLIGHT_STEER_SETTLE_S = _settle_sec("NAV_SEGMENT_MIDFLIGHT_STEER_SETTLE_S")
# Nav2 sometimes reports success early — never start fine dock above this distance.
NAV_COARSE_DONE_MIN_M = float(os.environ.get("NAV_COARSE_DONE_MIN_M", "0.45"))
SEGMENT_NAV_CFG = SegmentNavConfig(
    min_segment_m=SEGMENT_MIN_M,
    max_corner_deg=SEGMENT_MAX_CORNER_DEG,
    align_final_tol_rad=SEGMENT_ALIGN_TOL_RAD,
    align_pulse_s=PATH_ALIGN_PULSE_ON_SEC,
    align_pulse_mid_s=PATH_ALIGN_PULSE_MID_SEC,
    align_pulse_large_s=PATH_ALIGN_PULSE_LARGE_SEC,
    align_overshoot_pulse_s=PATH_ALIGN_OVERSHOOT_PULSE_ON_SEC,
    align_settle_s=PATH_ALIGN_SETTLE_SEC,
    drive_tol_m=SEGMENT_DRIVE_TOL_M,
    drive_cruise_mps=SEGMENT_DRIVE_CRUISE_MPS,
    drive_cruise_fraction=SEGMENT_DRIVE_CRUISE_FRACTION,
    drive_cruise_min_segment_m=SEGMENT_DRIVE_CRUISE_MIN_SEGMENT_M,
    drive_cruise_max_s=SEGMENT_DRIVE_CRUISE_MAX_S,
    drive_pulse_large_s=SEGMENT_DRIVE_PULSE_LARGE_S,
    drive_pulse_mid_s=SEGMENT_DRIVE_PULSE_MID_S,
    drive_pulse_s=SEGMENT_DRIVE_PULSE_S,
    drive_pulse_tiny_s=SEGMENT_DRIVE_PULSE_TINY_S,
    drive_settle_s=SEGMENT_DRIVE_SETTLE_S,
    drift_replan_m=SEGMENT_DRIFT_REPLAN_M,
    forward_block_m=SEGMENT_FORWARD_BLOCK_M,
    goal_arrive_m=SEGMENT_GOAL_ARRIVE_M,
    goal_handoff_m=SEGMENT_GOAL_HANDOFF_M,
    midflight_steer_min_rad=math.radians(SEGMENT_MIDFLIGHT_STEER_MIN_DEG),
    midflight_steer_max_rad=math.radians(SEGMENT_MIDFLIGHT_STEER_MAX_DEG),
    midflight_steer_pulse_s=SEGMENT_MIDFLIGHT_STEER_PULSE_S,
    midflight_min_remain_m=SEGMENT_MIDFLIGHT_MIN_REMAIN_M,
    midflight_min_segment_m=SEGMENT_MIDFLIGHT_MIN_SEGMENT_M,
    midflight_steer_settle_s=SEGMENT_MIDFLIGHT_STEER_SETTLE_S,
)
# Legacy alias
PATH_ALIGN_YAW_TOL_RAD = PATH_ALIGN_FINAL_TOL_RAD
# Arc (W+A / W+D): shorter on than pure — forward momentum already helps yaw.
TURN_PULSE_ON_ARC_SEC = float(os.environ.get("NAV_TURN_PULSE_ON_ARC_SEC", "0.14"))
TURN_PULSE_OFF_ARC_SEC = _settle_sec("NAV_TURN_PULSE_OFF_ARC_SEC")
# Pure-rotate noise floor only. Do NOT set high — live RPP often commands |wz|≈0.06–0.12.
# Anti-flip comes from latched pulse+settle, not from ignoring real turn intent.
TURN_PURE_WZ_DEADBAND = float(os.environ.get("NAV_TURN_PURE_WZ_DEADBAND", "0.05"))
STATUS_PATH = os.environ.get("NAV_STATUS_FILE_PATH", "/app/lidar/navigation_status.json")

ODOM_FRAME = os.environ.get("NAV_ODOM_FRAME", "odom")
BASE_FRAME = os.environ.get("NAV_BASE_FRAME", "base_link")
ODOM_TOPIC = os.environ.get("NAV_ODOM_TOPIC", "/odom")
ODOM_HZ = float(os.environ.get("NAV_ODOM_HZ", "20"))

HOST = os.environ.get("NAV_GOAL_HOST", "0.0.0.0")
PORT = int(os.environ.get("NAV_GOAL_PORT", "8768"))
MAP_FRAME = os.environ.get("SLAM_MAP_FRAME", "map")
GOAL_STATUS_PATH = os.environ.get(
    "NAV_GOAL_STATUS_PATH",
    os.environ.get("NAV_GOAL_STATUS_FILE", "/app/lidar/navigation_goal.json"),
)
COMMAND_PATH = os.environ.get("NAV_COMMAND_PATH", "/app/lidar/navigation_command.json")
KILL_PATH = os.environ.get("NAV_KILL_PATH", "/app/lidar/navigation_kill.json")
PATH_FILE = os.environ.get("NAV_PATH_FILE_PATH", "/app/lidar/navigation_path.json")
# Append-only JSONL for postmortem of stuck / aborted nav runs.
RUN_LOG_PATH = os.environ.get("NAV_RUN_LOG_PATH", "/app/lidar/navigation_run.jsonl")
GLOBAL_PLAN_TOPIC = os.environ.get("NAV_GLOBAL_PLAN_TOPIC", "/plan")
LOCAL_PLAN_TOPIC = os.environ.get("NAV_LOCAL_PLAN_TOPIC", "/local_plan")
PATH_MAX_POINTS = int(os.environ.get("NAV_PATH_MAX_POINTS", "120"))
NAV_PROGRESS_PERIOD_SEC = float(os.environ.get("NAV_PROGRESS_PERIOD_SEC", "2.0"))
# Warn / event when distance_remaining stops improving for this long.
NAV_STALL_WARN_SEC = float(os.environ.get("NAV_STALL_WARN_SEC", "8.0"))
NAV_STALL_EVENT_SEC = float(os.environ.get("NAV_STALL_EVENT_SEC", "20.0"))
NAV_CONTEXT_REFRESH_SEC = float(os.environ.get("NAV_CONTEXT_REFRESH_SEC", "30.0"))
NAV_ASSIST_REFRESH_SEC = float(os.environ.get("NAV_ASSIST_REFRESH_SEC", "5.0"))
# Fine docking: pulse→settle→remeasure. Close XY with W/S/face, then yaw.
# Turns always follow the *current* error (no locked reverse — that spiraled).
FINE_DOCK_XY_TOL_M = float(os.environ.get("NAV_FINE_DOCK_XY_TOL_M", "0.25"))
FINE_DOCK_YAW_TOL_RAD = float(os.environ.get("NAV_FINE_DOCK_YAW_TOL_RAD", str(math.radians(12.0))))
# Docker image still ships 0.16m / 5° — too tight for skid-steer mark dock.
# Match harness acceptance (≤0.25m / ≤20°) with a little margin on yaw.
FINE_DOCK_XY_TOL_M = max(FINE_DOCK_XY_TOL_M, 0.25)
FINE_DOCK_YAW_TOL_RAD = max(FINE_DOCK_YAW_TOL_RAD, math.radians(12.0))
FINE_DOCK_TIMEOUT_SEC = float(os.environ.get("NAV_FINE_DOCK_TIMEOUT_SEC", "120"))
FINE_DOCK_MAX_VX = float(os.environ.get("NAV_FINE_DOCK_MAX_VX", "0.10"))
FINE_DOCK_MAX_WZ = float(os.environ.get("NAV_FINE_DOCK_MAX_WZ", "0.22"))
FINE_DOCK_SETTLE_TICKS = int(os.environ.get("NAV_FINE_DOCK_SETTLE_TICKS", "2"))
FINE_DOCK_FACE_AHEAD_RAD = float(os.environ.get("NAV_FINE_DOCK_FACE_AHEAD_RAD", str(math.radians(40.0))))
FINE_DOCK_FACE_BEHIND_RAD = float(
    os.environ.get("NAV_FINE_DOCK_FACE_BEHIND_RAD", str(math.radians(140.0)))
)
FINE_DOCK_FACE_TOL_RAD = FINE_DOCK_FACE_AHEAD_RAD  # alias used by older tests/docs
FINE_DOCK_TURN_COARSE_SEC = float(os.environ.get("NAV_FINE_DOCK_TURN_COARSE_SEC", "0.22"))
FINE_DOCK_TURN_PULSE_SEC = float(os.environ.get("NAV_FINE_DOCK_TURN_PULSE_SEC", "0.12"))
FINE_DOCK_TURN_MICRO_SEC = float(os.environ.get("NAV_FINE_DOCK_TURN_MICRO_SEC", "0.05"))
FINE_DOCK_DRIVE_PULSE_SEC = float(os.environ.get("NAV_FINE_DOCK_DRIVE_PULSE_SEC", "0.10"))
FINE_DOCK_TURN_SETTLE_SEC = _settle_sec("NAV_FINE_DOCK_TURN_SETTLE_SEC")
FINE_DOCK_MAX_YAW_PULSES = int(os.environ.get("NAV_FINE_DOCK_MAX_YAW_PULSES", "48"))
FINE_DOCK_POSE_SAMPLES = int(os.environ.get("NAV_FINE_DOCK_POSE_SAMPLES", "5"))
# Keep old env name as alias for the yaw pulse width.
if "NAV_FINE_DOCK_TURN_FINE_SEC" in os.environ and "NAV_FINE_DOCK_TURN_PULSE_SEC" not in os.environ:
    FINE_DOCK_TURN_PULSE_SEC = float(os.environ["NAV_FINE_DOCK_TURN_FINE_SEC"])

_goal_state: dict[str, Any] = {
    "status": "idle",
    "nav_id": None,
    "goal": None,
    "result": None,
    "feedback": None,
    "updated_at": 0.0,
    "cmd_seq": 0,
    "cmd_error": None,
}
_goal_lock = threading.Lock()
_goal_node: "GoalNode | None" = None
_path_bridge: "PathBridge | None" = None
_cmd_bridge: "CmdVelBridge | None" = None
_last_cmd_seq = 0


def yaw_from_quat(x: float, y: float, z: float, w: float) -> float:
    return math.atan2(2.0 * (w * z + x * y), 1.0 - 2.0 * (y * y + z * z))


def yaw_to_quat(yaw: float) -> tuple[float, float, float, float]:
    half = yaw * 0.5
    return (0.0, 0.0, math.sin(half), math.cos(half))


def wrap_angle(angle: float) -> float:
    return math.atan2(math.sin(angle), math.cos(angle))


def body_frame_error(
    px: float, py: float, pyaw: float, tx: float, ty: float
) -> tuple[float, float, float, float]:
    """Map-frame residual → (dist, forward, left, heading_err) in base_link."""
    dx = tx - px
    dy = ty - py
    dist = math.hypot(dx, dy)
    fwd = dx * math.cos(pyaw) + dy * math.sin(pyaw)
    left = -dx * math.sin(pyaw) + dy * math.cos(pyaw)
    heading_err = wrap_angle(math.atan2(dy, dx) - pyaw) if dist > 1e-4 else 0.0
    return dist, fwd, left, heading_err


def fine_dock_accepted(
    dist_m: float,
    yaw_err_rad: float,
    *,
    dock_phase: str = "",
) -> bool:
    """Phase 3 success: XY within tol and yaw within tol."""
    del dock_phase
    return dist_m <= FINE_DOCK_XY_TOL_M and abs(yaw_err_rad) <= FINE_DOCK_YAW_TOL_RAD


GAP_CLOSE_CFG = GapCloseConfig(
    xy_tol_m=FINE_DOCK_XY_TOL_M,
    yaw_tol_rad=FINE_DOCK_YAW_TOL_RAD,
    skid_tol_m=min(0.08, FINE_DOCK_XY_TOL_M),
    drive_tol_m=0.02,
    allow_reverse=True,
)


def dock_yaw_pulse_sec(abs_err_rad: float) -> float:
    # Stronger bites than path-align micro — 0.08s stalls ~15° short of a
    # quarter turn (nav-20260825-025023 lat_t1 timeout).
    if abs_err_rad > math.radians(35):
        return max(FINE_DOCK_TURN_COARSE_SEC, 0.30)
    if abs_err_rad > math.radians(18):
        return max(FINE_DOCK_TURN_COARSE_SEC, 0.22)
    if abs_err_rad > math.radians(8):
        return max(FINE_DOCK_TURN_PULSE_SEC, 0.12)
    return FINE_DOCK_TURN_MICRO_SEC


def dock_drive_pulse_sec(dist_m: float) -> float:
    # Favour tiny pulses near the mark — big W steps overshoot on skid-steer.
    if dist_m > 0.40:
        return min(0.20, FINE_DOCK_DRIVE_PULSE_SEC * 1.6)
    if dist_m > 0.28:
        return FINE_DOCK_DRIVE_PULSE_SEC
    if dist_m > 0.18:
        return max(0.06, FINE_DOCK_DRIVE_PULSE_SEC * 0.7)
    return max(0.05, FINE_DOCK_DRIVE_PULSE_SEC * 0.5)


def fine_dock_plan(
    px: float,
    py: float,
    pyaw: float,
    tx: float,
    ty: float,
    tyaw: float,
) -> dict[str, Any]:
    """One measured action: close XY (W / S / face), else yaw toward current error."""
    dist, fwd, left, heading_err = body_frame_error(px, py, pyaw, tx, ty)
    yaw_err = wrap_angle(tyaw - pyaw)
    metrics = {
        "position_error_m": round(dist, 4),
        "yaw_error_deg": round(math.degrees(yaw_err), 2),
        "fwd_m": round(fwd, 3),
        "left_m": round(left, 3),
        "heading_err_deg": round(math.degrees(heading_err), 1),
        "pose_yaw_deg": round(math.degrees(pyaw), 2),
        "target_yaw_deg": round(math.degrees(tyaw), 2),
    }
    if fine_dock_accepted(dist, yaw_err):
        return {
            "keys": [],
            "pulse_s": 0.0,
            "done": True,
            "phase": "done",
            **metrics,
        }

    if dist > FINE_DOCK_XY_TOL_M:
        if abs(heading_err) <= FINE_DOCK_FACE_AHEAD_RAD:
            return {
                "keys": ["w"],
                "pulse_s": dock_drive_pulse_sec(dist),
                "done": False,
                "phase": "shift_fwd",
                **metrics,
            }
        if abs(heading_err) >= FINE_DOCK_FACE_BEHIND_RAD:
            return {
                "keys": ["s"],
                "pulse_s": dock_drive_pulse_sec(dist),
                "done": False,
                "phase": "shift_back",
                **metrics,
            }
        key = "a" if heading_err > 0 else "d"
        return {
            "keys": [key],
            "pulse_s": dock_yaw_pulse_sec(abs(heading_err)),
            "done": False,
            "phase": "shift_face",
            **metrics,
        }

    key = "a" if yaw_err > 0 else "d"
    return {
        "keys": [key],
        "pulse_s": dock_yaw_pulse_sec(abs(yaw_err)),
        "done": False,
        "phase": "yaw",
        **metrics,
    }


def make_nav_id() -> str:
    """Human-sortable session id: nav-YYYYMMDD-HHMMSS-xxxxxx."""
    ts = time.strftime("%Y%m%d-%H%M%S", time.gmtime())
    return f"nav-{ts}-{uuid.uuid4().hex[:6]}"


def _with_min(v: float, floor: float) -> float:
    """Zero tiny noise; boost non-zero axes to a motor-friendly PWM floor."""
    if abs(v) < 0.02:
        return 0.0
    if floor <= 0:
        return max(-1.0, min(1.0, v))
    mag = max(abs(v), floor)
    return max(-1.0, min(1.0, math.copysign(mag, v)))


def twist_to_drive(msg: Twist) -> dict[str, float]:
    """Map ROS Twist → Pi {x,y} in [-1,1].

    Teleop: forward = −y, left turn = −x.
    base_link +x is drive forward (laser TF is −90°), +angular.z = CCW/left → −x.
    """
    vx = float(msg.linear.x)
    wz = float(msg.angular.z)

    if abs(vx) < 0.02 and abs(wz) < 0.04:
        return {"x": 0.0, "y": 0.0}

    if abs(vx) < 0.02:
        y = 0.0
    else:
        y = -max(-1.0, min(1.0, vx / max(MAX_LINEAR_MPS, 1e-3)))
        y = _with_min(y, DRIVE_MIN_LINEAR)

    if abs(wz) < 0.03:
        x = 0.0
    else:
        x = -max(-1.0, min(1.0, wz / max(MAX_ANGULAR_RPS, 1e-3)))
        if DRIVE_INVERT_ANGULAR:
            x = -x
        x = _with_min(x, DRIVE_MIN_ANGULAR)

    return {"x": round(x, 3), "y": round(y, 3)}


def cmd_vel_to_keys(
    vx: float,
    wz: float,
    *,
    align_angular_rps: float = ALIGN_ANGULAR_RPS,
    invert_angular: bool = DRIVE_INVERT_ANGULAR,
    pure_wz_deadband: float = TURN_PURE_WZ_DEADBAND,
) -> list[str]:
    """Map (vx, wz) intent → Pi WASD keys (same protocol as dashboard teleop).

    Skid-steer / RPP contract:
    - Pure rotate (vx≈0): A/D only when |wz| clears deadband (ignore noise).
    - Any forward request (vx>0): always include W.
    """
    del align_angular_rps  # kept for call-site compatibility
    keys: list[str] = []
    driving = vx > 0.02
    if driving:
        keys.append("w")
    # No rear lidar — never emit S.
    wz_gate = 0.03 if driving else max(0.03, pure_wz_deadband)
    if abs(wz) >= wz_gate:
        turn_left = wz > 0
        if invert_angular:
            turn_left = not turn_left
        keys.append("a" if turn_left else "d")
    return keys


def pulse_turn_keys(
    keys: list[str],
    *,
    now: float,
    phase: str,
    phase_until: float,
    latched_turn: list[str] | None = None,
    on_pure: float = TURN_PULSE_ON_PURE_SEC,
    off_pure: float = TURN_PULSE_OFF_PURE_SEC,
    on_arc: float = TURN_PULSE_ON_ARC_SEC,
    off_arc: float = TURN_PULSE_OFF_ARC_SEC,
) -> tuple[list[str], str, float, list[str] | None]:
    """Latched pure-turn pulse → settle → remeasure.

    During settle we ignore Nav2 A/D flips (those are usually stale TF). Only
    after settle expires do we sample a fresh turn direction for the next pulse.

    Pure W/S (no A/D) uses the same on/off machine so segment nav drives in
    tiny discrete steps instead of holding forward continuously.
    """
    del on_arc, off_arc
    drive = [k for k in keys if k in ("w", "s")]
    turn = [k for k in keys if k in ("a", "d")]

    if drive and not turn:
        # Honour requested duration — do NOT floor up to long holds.
        on_s = max(0.02, on_pure)
        settle_s = max(OBSERVE_SETTLE_SEC, off_pure)
        latched = list(drive)
        if phase == "on" and now < phase_until:
            return latched, "on", phase_until, latched
        if phase == "off" and now < phase_until:
            return [], "off", phase_until, latched
        if phase == "on":
            return [], "off", now + settle_s, latched
        return latched, "on", now + on_s, latched

    if drive:
        return list(drive) + list(turn), "idle", 0.0, None
    if not turn and phase in ("idle", "") and not latched_turn:
        return list(keys), "idle", 0.0, None

    # Honour requested pulse length (path-align micro taps were previously
    # floored to 0.20s — that blew past a nearly-closed yaw gap).
    on_s = max(0.02, on_pure)
    settle_s = max(OBSERVE_SETTLE_SEC, off_pure)

    if phase == "on" and now < phase_until:
        # Hold latched direction even if Nav2 goes quiet or flips mid-pulse.
        held = list(latched_turn) if latched_turn else list(turn)
        if not held:
            return [], "on", phase_until, latched_turn
        return held, "on", phase_until, held

    if phase == "off" and now < phase_until:
        # Settle: motors stopped. Ignore A/D chatter from stale TF entirely.
        return [], "off", phase_until, latched_turn

    # Period boundary.
    if phase == "on":
        # Pulse done → wait for mechanical + SLAM yaw to land before remeasure.
        return [], "off", now + settle_s, latched_turn

    # Settle done (or idle): sample FRESH turn intent only now.
    if not turn:
        return [], "idle", 0.0, None
    # If the first post-settle sample flips vs last pulse, wait a FULL observe
    # settle again — never a short half-settle (localization still lagging).
    if latched_turn and turn != latched_turn and phase == "off":
        return [], "off", now + settle_s, None
    latched = list(turn)
    return latched, "on", now + on_s, latched


def twist_to_keys(msg: Twist) -> list[str]:
    """Map ROS motion intent to the Pi's unambiguous keyboard protocol."""
    return cmd_vel_to_keys(float(msg.linear.x), float(msg.angular.z))


def write_json_atomic(path: str, payload: Any) -> None:
    try:
        directory = os.path.dirname(path)
        if directory:
            os.makedirs(directory, exist_ok=True)
        tmp = f"{path}.tmp"
        with open(tmp, "w", encoding="utf-8") as handle:
            json.dump(payload, handle)
        os.replace(tmp, path)
    except OSError:
        pass


def write_goal_status() -> None:
    with _goal_lock:
        payload = dict(_goal_state)
    write_json_atomic(GOAL_STATUS_PATH, payload)


def append_nav_run_event(event: str, **fields: Any) -> None:
    """Append one structured nav-run line for later stuck/abort analysis."""
    row: dict[str, Any] = {
        "ts": time.time(),
        "iso": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "event": event,
    }
    row.update(fields)
    try:
        directory = os.path.dirname(RUN_LOG_PATH)
        if directory:
            os.makedirs(directory, exist_ok=True)
        with open(RUN_LOG_PATH, "a", encoding="utf-8") as handle:
            handle.write(json.dumps(row, separators=(",", ":")) + "\n")
    except OSError:
        pass


def read_path_global_xy() -> list[list[float]]:
    try:
        with open(PATH_FILE, encoding="utf-8") as handle:
            raw = json.load(handle)
    except (OSError, json.JSONDecodeError, TypeError):
        return []
    if not isinstance(raw, dict):
        return []
    global_xy = raw.get("global") or []
    if not isinstance(global_xy, list):
        return []
    out: list[list[float]] = []
    for pt in global_xy:
        try:
            out.append([float(pt[0]), float(pt[1])])
        except (TypeError, ValueError, IndexError):
            continue
    return out


def path_initial_heading(path_xy: list[list[float]]) -> float | None:
    """Bearing of the first meaningful path segment (path[0] is at the rover)."""
    if len(path_xy) < 2:
        return None
    x0, y0 = path_xy[0][0], path_xy[0][1]
    for i in range(1, len(path_xy)):
        x1, y1 = path_xy[i][0], path_xy[i][1]
        if math.hypot(x1 - x0, y1 - y0) >= 0.05:
            return math.atan2(y1 - y0, x1 - x0)
    return None


def path_lookahead_heading(
    px: float,
    py: float,
    path_xy: list[list[float]],
    *,
    lookahead_m: float = 0.60,
) -> float | None:
    """Bearing from the rover to a point ~lookahead_m along the trimmed path.

    Using pose→look-ahead (not a tiny first segment chord) matches what the
    driver sees from the start and avoids locking onto a noisy stub heading.
    """
    if len(path_xy) < 2:
        return None
    remaining = float(lookahead_m)
    for i in range(len(path_xy) - 1):
        x0, y0 = float(path_xy[i][0]), float(path_xy[i][1])
        x1, y1 = float(path_xy[i + 1][0]), float(path_xy[i + 1][1])
        seg_len = math.hypot(x1 - x0, y1 - y0)
        if seg_len < 1e-6:
            continue
        if remaining <= seg_len:
            t = remaining / seg_len
            tx = x0 + t * (x1 - x0)
            ty = y0 + t * (y1 - y0)
            if math.hypot(tx - px, ty - py) < 0.05:
                # Look-ahead collapsed onto the rover — keep walking the path.
                remaining = 0.05
                continue
            return math.atan2(ty - py, tx - px)
        remaining -= seg_len
    # Path shorter than lookahead — aim at the tip.
    ex, ey = float(path_xy[-1][0]), float(path_xy[-1][1])
    if math.hypot(ex - px, ey - py) < 0.05:
        return None
    return math.atan2(ey - py, ex - px)


def first_drive_heading(px: float, py: float) -> float | None:
    """Heading phase 1 must match — look-ahead along the planned path."""
    path_xy = trim_path_from_pose(px, py, read_path_global_xy())
    heading = path_lookahead_heading(
        px, py, path_xy, lookahead_m=PATH_ALIGN_LOOKAHEAD_M
    )
    if heading is not None:
        return heading
    segments = segmentize_path(
        path_xy,
        min_segment_m=SEGMENT_MIN_M,
        max_corner_deg=SEGMENT_MAX_CORNER_DEG,
    )
    # Prefer the first segment that is long enough to be meaningful.
    for seg in segments:
        if seg.length_m >= max(0.25, SEGMENT_MIN_M):
            return seg.heading_rad
    if segments:
        return segments[0].heading_rad
    return path_initial_heading(path_xy)


def forward_scan_blocked(block_m: float = SEGMENT_FORWARD_BLOCK_M) -> bool:
    scan = scan_context()
    if not scan.get("loaded"):
        return False
    # Ignore sub-body hits (self / noise) — 4cm "obstacles" caused false
    # replan_blocked near C (nav-20260825-023608).
    min_hit_m = float(os.environ.get("NAV_FORWARD_MIN_HIT_M", "0.18"))
    for o in scan.get("octants") or []:
        if not isinstance(o, dict):
            continue
        try:
            sector = int(o["sector"])
            dist = float(o["min_m"])
        except (KeyError, TypeError, ValueError):
            continue
        if sector in SEGMENT_FORWARD_OCTANTS and min_hit_m <= dist < block_m:
            return True
    return False


def path_file_updated_at() -> float:
    try:
        with open(PATH_FILE, encoding="utf-8") as handle:
            raw = json.load(handle)
        if isinstance(raw, dict):
            return float(raw.get("updated_at") or 0.0)
    except (OSError, json.JSONDecodeError, TypeError, ValueError):
        pass
    return 0.0


def lookup_map_pose_from_context() -> tuple[float, float, float] | None:
    pose = map_context().get("pose")
    if not isinstance(pose, dict):
        return None
    try:
        x = float(pose["x"])
        y = float(pose["y"])
        yaw = float(pose.get("theta", pose.get("yaw", 0.0)))
        return x, y, yaw
    except (KeyError, TypeError, ValueError):
        return None


def read_path_summary() -> dict[str, Any]:
    try:
        with open(PATH_FILE, encoding="utf-8") as handle:
            raw = json.load(handle)
    except (OSError, json.JSONDecodeError, TypeError):
        return {}
    if not isinstance(raw, dict):
        return {}
    global_xy = raw.get("global") or []
    local_xy = raw.get("local") or []
    path_len = 0.0
    if isinstance(global_xy, list) and len(global_xy) >= 2:
        for i in range(1, len(global_xy)):
            try:
                x0, y0 = float(global_xy[i - 1][0]), float(global_xy[i - 1][1])
                x1, y1 = float(global_xy[i][0]), float(global_xy[i][1])
            except (TypeError, ValueError, IndexError):
                continue
            path_len += math.hypot(x1 - x0, y1 - y0)
    return {
        "global_points": len(global_xy) if isinstance(global_xy, list) else 0,
        "local_points": len(local_xy) if isinstance(local_xy, list) else 0,
        "path_length_m": round(path_len, 3),
        "path_updated_at": raw.get("updated_at"),
    }


def path_msg_to_xy(msg: Path, max_points: int = PATH_MAX_POINTS) -> list[list[float]]:
    poses = list(msg.poses or [])
    if not poses:
        return []
    if max_points > 0 and len(poses) > max_points:
        # Preserve both endpoints when thinning so the UI route reaches the
        # destination rather than stopping one sample early.
        if max_points == 1:
            poses = [poses[-1]]
        else:
            step = (len(poses) - 1) / (max_points - 1)
            poses = [poses[round(i * step)] for i in range(max_points)]
    out: list[list[float]] = []
    for ps in poses:
        out.append(
            [
                round(float(ps.pose.position.x), 3),
                round(float(ps.pose.position.y), 3),
            ]
        )
    return out


class PathBridge(Node):
    """Mirror Nav2 /plan + /local_plan into a JSON file for the dashboard."""

    def __init__(self) -> None:
        super().__init__("rover_nav_path_bridge")
        self._lock = threading.Lock()
        self._global: list[list[float]] = []
        self._local: list[list[float]] = []
        self._frame = MAP_FRAME
        self._buf = Buffer()
        self._listener = TransformListener(self._buf, self)
        # Nav2 publishes plans VOLATILE (not transient local) — mismatched QoS
        # silently drops every message and the dashboard shows an empty path.
        plan_qos = QoSProfile(
            history=HistoryPolicy.KEEP_LAST,
            depth=5,
            reliability=ReliabilityPolicy.RELIABLE,
            durability=DurabilityPolicy.VOLATILE,
        )
        local_qos = QoSProfile(
            history=HistoryPolicy.KEEP_LAST,
            depth=5,
            reliability=ReliabilityPolicy.BEST_EFFORT,
            durability=DurabilityPolicy.VOLATILE,
        )
        # Only /plan is the complete start→destination route. The controller's
        # received_global_plan topics are transformed/pruned working copies and
        # were overwriting the dashboard route with a short misleading segment.
        self.create_subscription(Path, GLOBAL_PLAN_TOPIC, self._on_global, plan_qos)
        self.create_subscription(Path, LOCAL_PLAN_TOPIC, self._on_local, local_qos)
        self.create_subscription(
            Path, "/controller_server/local_plan", self._on_local, local_qos
        )
        self.create_timer(0.5, self._flush)
        self.get_logger().info(
            f"path bridge global={GLOBAL_PLAN_TOPIC} local={LOCAL_PLAN_TOPIC} "
            f"→ {PATH_FILE}"
        )

    def _to_map_xy(self, msg: Path, max_points: int) -> list[list[float]]:
        pts = path_msg_to_xy(msg, max_points=max_points)
        if not pts:
            return []
        src = str(msg.header.frame_id or MAP_FRAME).lstrip("/")
        if src == MAP_FRAME:
            return pts
        try:
            tf = self._buf.lookup_transform(
                MAP_FRAME, src, Time(), timeout=Duration(seconds=0.05)
            )
        except TransformException:
            return pts
        tx = float(tf.transform.translation.x)
        ty = float(tf.transform.translation.y)
        q = tf.transform.rotation
        yaw = yaw_from_quat(q.x, q.y, q.z, q.w)
        c, s = math.cos(yaw), math.sin(yaw)
        out: list[list[float]] = []
        for x, y in pts:
            out.append([round(c * x - s * y + tx, 3), round(s * x + c * y + ty, 3)])
        return out

    def _on_global(self, msg: Path) -> None:
        pts = self._to_map_xy(msg, PATH_MAX_POINTS)
        if not pts:
            return
        with self._lock:
            self._global = pts
            self._frame = MAP_FRAME
        self._flush()

    def _on_local(self, msg: Path) -> None:
        pts = self._to_map_xy(msg, 60)
        if not pts:
            return
        with self._lock:
            self._local = pts
        self._flush()

    def clear(self) -> None:
        with self._lock:
            self._global = []
            self._local = []
        self._flush()

    def _flush(self) -> None:
        with self._lock:
            payload = {
                "frame_id": self._frame,
                "global": list(self._global),
                "local": list(self._local),
                "updated_at": time.time(),
            }
        write_json_atomic(PATH_FILE, payload)


class OdomRepublisher(Node):
    def __init__(self) -> None:
        super().__init__("rover_odom_republisher")
        self._buf = Buffer()
        self._listener = TransformListener(self._buf, self)
        self._pub = self.create_publisher(Odometry, ODOM_TOPIC, 10)
        self._prev: TransformStamped | None = None
        self._prev_t: float | None = None
        self.create_timer(1.0 / max(ODOM_HZ, 1.0), self._tick)
        self.get_logger().info(f"odom republish {ODOM_FRAME}→{BASE_FRAME} → {ODOM_TOPIC}")

    def _tick(self) -> None:
        try:
            tf = self._buf.lookup_transform(
                ODOM_FRAME, BASE_FRAME, Time(), timeout=Duration(seconds=0.05)
            )
        except TransformException:
            return
        msg = Odometry()
        msg.header = tf.header
        msg.header.frame_id = ODOM_FRAME
        msg.child_frame_id = BASE_FRAME
        msg.pose.pose.position.x = tf.transform.translation.x
        msg.pose.pose.position.y = tf.transform.translation.y
        msg.pose.pose.position.z = tf.transform.translation.z
        msg.pose.pose.orientation = tf.transform.rotation

        now = self.get_clock().now().nanoseconds * 1e-9
        if self._prev is not None and self._prev_t is not None:
            dt = max(1e-3, now - self._prev_t)
            dx = tf.transform.translation.x - self._prev.transform.translation.x
            dy = tf.transform.translation.y - self._prev.transform.translation.y
            yaw = yaw_from_quat(
                tf.transform.rotation.x,
                tf.transform.rotation.y,
                tf.transform.rotation.z,
                tf.transform.rotation.w,
            )
            pyaw = yaw_from_quat(
                self._prev.transform.rotation.x,
                self._prev.transform.rotation.y,
                self._prev.transform.rotation.z,
                self._prev.transform.rotation.w,
            )
            dyaw = math.atan2(math.sin(yaw - pyaw), math.cos(yaw - pyaw))
            c, s = math.cos(yaw), math.sin(yaw)
            msg.twist.twist.linear.x = (c * dx + s * dy) / dt
            msg.twist.twist.linear.y = (-s * dx + c * dy) / dt
            msg.twist.twist.angular.z = dyaw / dt

        self._prev = tf
        self._prev_t = now
        self._pub.publish(msg)


class CmdVelBridge(Node):
    def __init__(self) -> None:
        super().__init__("rover_cmd_vel_bridge")
        self._lock = threading.Lock()
        self._latest: Twist | None = None
        self._latest_at = 0.0
        # Force one explicit stop on startup (kill latch / stale motors).
        self._last_sent: list[str] | None = None
        self._ssl = None if SSL_VERIFY else ssl._create_unverified_context()
        self._phase = "idle"
        self._paused = False
        self._settle_until = 0.0
        self._last_moving_at = 0.0
        self._turn_pulse_phase = "idle"
        self._turn_pulse_until = 0.0
        self._latched_turn: list[str] | None = None
        self._pure_turn_since = 0.0
        # Phase-1 path align: rotate-only toward first segment before Nav2 drive.
        self._path_align_active = False
        self._path_align_done = False
        self._path_align_target_yaw: float | None = None
        self._path_align_started_at = 0.0
        self._path_align_approach_sign = 0
        self._path_align_pulse_count = 0
        self._path_align_last_phase = "idle"
        self._replan_after_align_requested = False
        # Phase 2: segment-by-segment straight-line following.
        self._segment_nav_active = False
        self._segment_nav_state = SegmentNavState()
        self._segments: list[Segment] = []
        self._segment_waiting_replan = False
        self._segment_last_path_updated_at = 0.0
        self._segment_pulse_on = SEGMENT_DRIVE_PULSE_S
        self._segment_pulse_off = SEGMENT_DRIVE_SETTLE_S
        self._seg_drive_best_rem: float | None = None
        self._seg_drive_improve_at = 0.0
        self._seg_drive_stall_idx = -1
        # When set, fine-dock owns the motors — cmd_vel idle must not clear keys.
        self._hold_active = False
        self._hold_keys: list[str] = []
        self.create_subscription(Twist, CMD_VEL_TOPIC, self._on_cmd, qos_profile_sensor_data)
        self.create_timer(1.0 / max(KEEPALIVE_HZ, 1.0), self._tick)
        self.get_logger().info(
            f"cmd_vel bridge topic={CMD_VEL_TOPIC} keys={DRIVE_KEYS_URL} "
            f"max_v={MAX_LINEAR_MPS} max_w={MAX_ANGULAR_RPS} "
            f"invert_ang={DRIVE_INVERT_ANGULAR} align_w={ALIGN_ANGULAR_RPS} "
            f"mode=wasd_pulse_settle on={TURN_PULSE_ON_PURE_SEC}s "
            f"observe_settle={OBSERVE_SETTLE_SEC}s "
            f"settle={TURN_PULSE_OFF_PURE_SEC}s wz_deadband={TURN_PURE_WZ_DEADBAND} "
            f"path_align_coarse={math.degrees(PATH_ALIGN_COARSE_TOL_RAD):.0f}° "
            f"final={math.degrees(PATH_ALIGN_FINAL_TOL_RAD):.0f}° "
            f"path_align_settle={PATH_ALIGN_SETTLE_SEC}s "
            f"segment_nav=on drift_replan={SEGMENT_DRIFT_REPLAN_M}m "
            f"start_settle={START_SETTLE_SEC}s"
        )

    def begin_path_align(self) -> None:
        """Rotate-only toward the first path segment before following Nav2."""
        with self._lock:
            self._path_align_active = True
            self._path_align_done = False
            self._path_align_target_yaw = None
            self._path_align_started_at = time.monotonic()
            self._path_align_approach_sign = 0
            self._path_align_pulse_count = 0
            self._path_align_last_phase = "idle"
            self._replan_after_align_requested = False
            self._segment_nav_active = False
            self._segment_nav_state = SegmentNavState()
            self._segments = []
            self._segment_waiting_replan = False
            self._turn_pulse_phase = "idle"
            self._turn_pulse_until = 0.0
            self._latched_turn = None
            self._pure_turn_since = 0.0

    def _complete_path_align(self, pose_yaw: float, err_rad: float) -> None:
        self._path_align_done = True
        self._path_align_active = False
        self._turn_pulse_phase = "idle"
        self._turn_pulse_until = 0.0
        self._latched_turn = None
        self.get_logger().info(
            f"path align done yaw={math.degrees(pose_yaw):.1f}° "
            f"err={math.degrees(err_rad):.1f}° target={math.degrees(self._path_align_target_yaw or 0):.1f}° "
            f"pulses={self._path_align_pulse_count} — starting segment nav"
        )
        pose = lookup_map_pose_from_context()
        if pose is not None:
            self._start_segment_nav(pose)

    def _path_xy_for_pose(self) -> list[list[float]]:
        path_xy = read_path_global_xy()
        pose = lookup_map_pose_from_context()
        if pose is not None and path_xy:
            path_xy = trim_path_from_pose(pose[0], pose[1], path_xy)
        return path_xy

    def segment_nav_active(self) -> bool:
        return self._segment_nav_active

    def _start_segment_nav(self, pose: tuple[float, float, float]) -> None:
        path_xy = trim_path_from_pose(pose[0], pose[1], read_path_global_xy())
        segments = segmentize_path(
            path_xy,
            min_segment_m=SEGMENT_MIN_M,
            max_corner_deg=SEGMENT_MAX_CORNER_DEG,
        )
        if not segments:
            self.get_logger().info(
                "segment nav: no usable segments (path too short) — fine dock"
            )
            self._segment_nav_active = False
            if _goal_node is not None:
                _goal_node.on_segment_nav_complete()
            return
        self._segments = segments
        self._segment_nav_active = True
        self._segment_waiting_replan = False
        self._segment_last_path_updated_at = path_file_updated_at()
        # Always align to segment 0 — path align may target path[0] while rover XY has moved.
        self._segment_nav_state = SegmentNavState(
            segment_index=0,
            phase="align",
        )
        self._segment_pulse_on = SEGMENT_DRIVE_PULSE_S
        self._segment_pulse_off = SEGMENT_DRIVE_SETTLE_S
        if _goal_node is not None:
            _goal_node.detach_nav2_for_segment_nav()
        self.get_logger().info(
            f"segment nav: {len(segments)} segments, first "
            f"{segments[0].length_m:.2f}m @ {segments[0].heading_deg:.0f}°"
        )
        for i, seg in enumerate(segments):
            self.get_logger().info(
                f"  seg[{i}] {seg.length_m:.2f}m @ {seg.heading_deg:.0f}° "
                f"({seg.x0:.2f},{seg.y0:.2f})→({seg.x1:.2f},{seg.y1:.2f})"
            )

    def _try_reload_remaining_segments(self, pose: tuple[float, float, float]) -> bool:
        """Reload trimmed path segments without finishing phase 2. True if usable."""
        path_xy = trim_path_from_pose(pose[0], pose[1], read_path_global_xy())
        segments = segmentize_path(
            path_xy,
            min_segment_m=SEGMENT_MIN_M,
            max_corner_deg=SEGMENT_MAX_CORNER_DEG,
        )
        if not segments:
            return False
        idx = nearest_segment_index(pose[0], pose[1], segments)
        self._segments = segments
        self._segment_nav_state = SegmentNavState(segment_index=idx, phase="align")
        self._segment_waiting_replan = False
        self._segment_last_path_updated_at = path_file_updated_at()
        self._segment_nav_active = True
        self._turn_pulse_phase = "idle"
        self._turn_pulse_until = 0.0
        self._latched_turn = None
        return True

    def _reload_segments_after_replan(self, pose: tuple[float, float, float]) -> None:
        path_xy = trim_path_from_pose(pose[0], pose[1], read_path_global_xy())
        segments = segmentize_path(
            path_xy,
            min_segment_m=SEGMENT_MIN_M,
            max_corner_deg=SEGMENT_MAX_CORNER_DEG,
        )
        if not segments:
            # Remaining path shorter than keep floor — treat as phase-2 done.
            self.get_logger().info(
                "segment nav replan: no segments left — fine dock"
            )
            self._segment_waiting_replan = False
            self._segment_nav_active = False
            self._turn_pulse_phase = "idle"
            self._turn_pulse_until = 0.0
            self._latched_turn = None
            if _goal_node is not None:
                _goal_node.on_segment_nav_complete()
            return
        idx = nearest_segment_index(pose[0], pose[1], segments)
        self._segments = segments
        self._segment_nav_state = SegmentNavState(segment_index=idx, phase="align")
        self._segment_waiting_replan = False
        self._segment_last_path_updated_at = path_file_updated_at()
        self._turn_pulse_phase = "idle"
        self._turn_pulse_until = 0.0
        self._latched_turn = None
        self.get_logger().info(
            f"segment nav replan loaded {len(segments)} segments, resume at {idx}"
        )

    def _segment_nav_keys(self, now: float) -> tuple[list[str], bool]:
        """Return (keys, still_active). Phase 2: rotate/drive each path segment."""
        if not self._segment_nav_active:
            return [], False

        if self._segment_waiting_replan:
            updated = path_file_updated_at()
            pose = lookup_map_pose_from_context()
            if updated > self._segment_last_path_updated_at and pose is not None:
                self._reload_segments_after_replan(pose)
            else:
                self._phase = "segment_replan_wait"
                return [], True

        pose = lookup_map_pose_from_context()
        if pose is None:
            self._phase = "segment_wait_pose"
            return [], True

        goal_xy: tuple[float, float] | None = None
        if _goal_node is not None and _goal_node._dock_target is not None:
            goal_xy = (_goal_node._dock_target[0], _goal_node._dock_target[1])

        turn_busy = self._turn_pulse_phase in ("on", "off") and now < (
            self._turn_pulse_until + 0.05
        )

        # Abort an in-progress W immediately once on the marker / past segment end.
        if (
            turn_busy
            and self._turn_pulse_phase == "on"
            and self._latched_turn == ["w"]
        ):
            abort = next_segment_step(
                pose[0],
                pose[1],
                pose[2],
                self._segments,
                self._segment_nav_state,
                cfg=SEGMENT_NAV_CFG,
                goal_xy=goal_xy,
            )
            if abort.done or (
                abort.state is not None
                and abort.state.phase == "align"
                and abort.keys == []
            ):
                self._turn_pulse_phase = "idle"
                self._turn_pulse_until = 0.0
                self._latched_turn = None
                self._segment_nav_state = abort.state or self._segment_nav_state
                if abort.done:
                    self._segment_nav_active = False
                    self.get_logger().info(
                        f"segment nav: abort W — {abort.note or 'complete'}"
                    )
                    if _goal_node is not None:
                        _goal_node.on_segment_nav_complete()
                    return [], False
                turn_busy = False

        if not turn_busy:
            step = next_segment_step(
                pose[0],
                pose[1],
                pose[2],
                self._segments,
                self._segment_nav_state,
                cfg=SEGMENT_NAV_CFG,
                forward_blocked=forward_scan_blocked(),
                goal_xy=goal_xy,
            )
            self._segment_nav_state = step.state or self._segment_nav_state
            st_now = self._segment_nav_state
            # Segment-drive rem stall → replan (global stall_s is zeroed in phase 2).
            if st_now.phase == "drive" and not step.replan and not step.done:
                rem_ui = None
                note = step.note or ""
                if "rem=" in note:
                    try:
                        rem_ui = float(note.split("rem=")[1].split("m")[0])
                    except (IndexError, ValueError):
                        rem_ui = None
                if rem_ui is None and self._segments and st_now.segment_index < len(
                    self._segments
                ):
                    seg = self._segments[st_now.segment_index]
                    origin = st_now.drive_origin or (pose[0], pose[1])
                    along, to_end = (
                        max(
                            0.0,
                            seg.length_m
                            - drive_progress_m(origin, pose[0], pose[1], seg.heading_rad),
                        ),
                        math.hypot(pose[0] - seg.x1, pose[1] - seg.y1),
                    )
                    rem_ui = min(along, to_end)
                if rem_ui is not None:
                    if (
                        st_now.segment_index != self._seg_drive_stall_idx
                        or self._seg_drive_best_rem is None
                        or rem_ui < self._seg_drive_best_rem - 0.04
                    ):
                        self._seg_drive_stall_idx = st_now.segment_index
                        self._seg_drive_best_rem = rem_ui
                        self._seg_drive_improve_at = now
                    elif now - self._seg_drive_improve_at >= SEGMENT_DRIVE_STALL_SEC:
                        self.get_logger().warning(
                            f"segment drive stall seg={st_now.segment_index} "
                            f"rem={rem_ui:.2f}m for {now - self._seg_drive_improve_at:.0f}s "
                            f"— requesting replan"
                        )
                        self._seg_drive_best_rem = None
                        self._segment_waiting_replan = True
                        if _goal_node is not None:
                            _goal_node.request_segment_replan("replan_drive_stall")
                        self._phase = "segment_replan_wait"
                        return [], True
            elif st_now.phase != "drive":
                self._seg_drive_best_rem = None

            if st_now.segment_index != getattr(
                self, "_segment_log_idx", -1
            ) or st_now.phase != getattr(self, "_segment_log_phase", ""):
                nseg = len(self._segments)
                self.get_logger().info(
                    f"segment nav step: [{st_now.segment_index + 1}/{nseg}] "
                    f"phase={st_now.phase} keys={step.keys or []} "
                    f"note={step.note or st_now.note}"
                )
                self._segment_log_idx = st_now.segment_index
                self._segment_log_phase = st_now.phase
            elif step.keys and step.phase in ("seg_drive", "seg_midflight"):
                # Sample drive / mid-flight steer so long rushes are visible in logs.
                self.get_logger().info(
                    f"segment nav drive: [{st_now.segment_index + 1}/{len(self._segments)}] "
                    f"{step.note}",
                    throttle_duration_sec=1.5,
                )
            if step.replan and _goal_node is not None and not self._segment_waiting_replan:
                # Prefer reloading the on-disk path first — old segmentize used to
                # drop curved leftovers, leaving meters still on the published path.
                if step.phase == "replan_short":
                    # Last-run lesson: at ≤~20cm, leftover crumbs caused a 180° spin.
                    # Hand off to fine dock instead of reloading micro-segments.
                    if goal_xy is not None:
                        gdist = math.hypot(pose[0] - goal_xy[0], pose[1] - goal_xy[1])
                        if gdist <= SEGMENT_GOAL_HANDOFF_M:
                            self.get_logger().info(
                                f"segment nav: near goal ({gdist:.2f}m) — "
                                f"skip replan, hand off to fine dock"
                            )
                            self._segment_nav_active = False
                            self._turn_pulse_phase = "idle"
                            self._turn_pulse_until = 0.0
                            self._latched_turn = None
                            if _goal_node is not None:
                                _goal_node.on_segment_nav_complete()
                            return [], False
                    before = len(self._segments)
                    if self._try_reload_remaining_segments(pose):
                        self.get_logger().info(
                            f"segment nav: reloaded remaining path "
                            f"({before} → {len(self._segments)} segs) — {step.note}"
                        )
                        return [], True
                self.get_logger().info(f"segment nav replan: {step.note}")
                self._segment_waiting_replan = True
                _goal_node.request_segment_replan(step.phase)
                self._phase = "segment_replan_wait"
                return [], True
            if step.done:
                self._segment_nav_active = False
                self._turn_pulse_phase = "idle"
                self._turn_pulse_until = 0.0
                self._latched_turn = None
                self.get_logger().info(
                    f"segment nav: all segments complete ({step.note})"
                )
                if _goal_node is not None:
                    _goal_node.on_segment_nav_complete()
                return [], False
            if step.keys:
                if step.pulse_on_s > 0:
                    self._segment_pulse_on = step.pulse_on_s
                if step.pulse_off_s > 0:
                    self._segment_pulse_off = step.pulse_off_s
                desired = step.keys
            elif step.pulse_off_s > 0 and self._turn_pulse_phase == "idle":
                self._turn_pulse_phase = "off"
                self._turn_pulse_until = now + step.pulse_off_s
                self._phase = "segment_settle"
                return [], True
            else:
                desired = []
            self._phase = step.phase or "segment"
        else:
            desired = list(self._latched_turn or [])

        if desired:
            pulse_on = self._segment_pulse_on
            pulse_off = self._segment_pulse_off
            if desired == ["w"]:
                pulse_on = pulse_on or SEGMENT_DRIVE_PULSE_S
                pulse_off = pulse_off or SEGMENT_DRIVE_SETTLE_S
            keys, phase, until, latch = pulse_turn_keys(
                desired,
                now=now,
                phase=self._turn_pulse_phase,
                phase_until=self._turn_pulse_until,
                latched_turn=self._latched_turn,
                on_pure=pulse_on,
                off_pure=pulse_off,
            )
            self._turn_pulse_phase = phase
            self._turn_pulse_until = until
            self._latched_turn = latch
            if phase == "off":
                self._phase = "segment_settle"
            return keys, True

        self._phase = self._segment_nav_state.phase
        return [], True

    def _path_align_keys(self, now: float) -> tuple[list[str], bool]:
        """Return (keys, align_complete). While incomplete, ignore Nav2 cmd_vel.

        Pure observe→pulse→settle→observe. Never precompute how many taps are
        needed — each pulse's effect varies on skid-steer.
        """
        if self._path_align_done or not self._path_align_active:
            return [], True

        pose = lookup_map_pose_from_context()
        if pose is None:
            return [], False

        if self._path_align_target_yaw is None:
            heading = first_drive_heading(pose[0], pose[1])
            if heading is None:
                if now - self._path_align_started_at > PATH_ALIGN_WAIT_PATH_SEC:
                    self._path_align_done = True
                    self._path_align_active = False
                    return [], True
                return [], False
            self._path_align_target_yaw = heading
            self._path_align_path_stamp = path_file_updated_at()
            self.get_logger().info(
                f"path align start target_yaw={math.degrees(heading):.1f}° "
                f"(look-ahead {PATH_ALIGN_LOOKAHEAD_M:.2f}m) "
                f"pose_yaw={math.degrees(pose[2]):.1f}° "
                f"done_tol={math.degrees(PATH_ALIGN_FINAL_TOL_RAD):.1f}° "
                f"pulse_large/mid/small="
                f"{PATH_ALIGN_PULSE_LARGE_SEC:.3f}/"
                f"{PATH_ALIGN_PULSE_MID_SEC:.3f}/"
                f"{PATH_ALIGN_PULSE_ON_SEC:.3f}s "
                f"overshoot={PATH_ALIGN_OVERSHOOT_PULSE_ON_SEC:.3f}s"
            )
        else:
            # Refresh look-ahead while still far — early Nav2 paths often change.
            stamp = path_file_updated_at()
            if stamp > getattr(self, "_path_align_path_stamp", 0.0):
                refreshed = first_drive_heading(pose[0], pose[1])
                if refreshed is not None:
                    old = self._path_align_target_yaw
                    delta = abs(wrap_angle(refreshed - old))
                    if delta > math.radians(12.0):
                        self.get_logger().info(
                            f"path align retarget {math.degrees(old):.1f}° → "
                            f"{math.degrees(refreshed):.1f}° (path updated)"
                        )
                        self._path_align_target_yaw = refreshed
                        self._path_align_approach_sign = 0
                    self._path_align_path_stamp = stamp

        err = wrap_angle(self._path_align_target_yaw - pose[2])

        # During pulse or settle: hold/wait — do not decide done mid-motion.
        turn_busy = self._turn_pulse_phase in ("on", "off") and now < (
            self._turn_pulse_until + 0.02
        )
        if turn_busy:
            if self._turn_pulse_phase == "on":
                return list(self._latched_turn or []), False
            return [], False

        # Idle after settle — observe live yaw, then either done or one more tap.
        if abs(err) <= PATH_ALIGN_FINAL_TOL_RAD:
            self._turn_pulse_phase = "idle"
            self._turn_pulse_until = 0.0
            self._latched_turn = None
            self._complete_path_align(pose[2], err)
            return [], True

        if self._path_align_approach_sign == 0 and abs(err) > math.radians(1.0):
            self._path_align_approach_sign = 1 if err > 0 else -1

        overshot = self._path_align_approach_sign != 0 and (
            (self._path_align_approach_sign > 0 and err < 0)
            or (self._path_align_approach_sign < 0 and err > 0)
        )

        on_s, settle_s, mode = yaw_align_pulse_sec(abs(err), overshot=overshot)

        turn_key = "a" if err > 0 else "d"
        prev_phase = self._turn_pulse_phase
        keys, phase, until, latch = pulse_turn_keys(
            [turn_key],
            now=now,
            phase=self._turn_pulse_phase,
            phase_until=self._turn_pulse_until,
            latched_turn=self._latched_turn,
            on_pure=on_s,
            off_pure=settle_s,
        )
        if phase == "on" and prev_phase != "on":
            self._path_align_pulse_count += 1
            self.get_logger().info(
                f"path align pulse #{self._path_align_pulse_count} mode={mode} "
                f"key={turn_key.upper()} live_err={math.degrees(err):.1f}° "
                f"overshot={overshot} hold={on_s:.3f}s"
            )
        self._turn_pulse_phase = phase
        self._turn_pulse_until = until
        self._latched_turn = latch

        self._path_align_last_phase = phase
        return keys, False

    def _on_cmd(self, msg: Twist) -> None:
        with self._lock:
            self._latest = msg
            self._latest_at = time.monotonic()

    def _desired(self) -> tuple[dict[str, float], list[str], float, bool]:
        with self._lock:
            msg = self._latest
            age = time.monotonic() - self._latest_at
            paused = self._paused or os.path.isfile(KILL_PATH)
        if paused:
            return ({"x": 0.0, "y": 0.0}, [], age, True)
        if msg is None or age > STALE_STOP_SEC:
            return ({"x": 0.0, "y": 0.0}, [], age, False)
        # Status still records the proportional mapping for debugging; motors
        # only ever receive discrete WASD keys.
        return (twist_to_drive(msg), twist_to_keys(msg), age, False)

    def _tick(self) -> None:
        now = time.monotonic()
        with self._lock:
            hold_active = self._hold_active
            hold_keys = list(self._hold_keys)
        if hold_active:
            self._phase = "dock_hold"
            keys = hold_keys
            self._turn_pulse_phase = "idle"
            self._turn_pulse_until = 0.0
            self._latched_turn = None
            # Keepalive every tick — Pi needs continuous key posts while turning.
            self._post_keys(keys)
            self._last_sent = list(keys)
            self._write_status({"x": 0.0, "y": 0.0}, keys, keys, 0.0)
            return

        desired_drive, desired_keys, age, paused = self._desired()
        align_keys, align_done = self._path_align_keys(now)
        if not align_done:
            self._phase = (
                "path_align_settle" if self._turn_pulse_phase == "off" else "path_align"
            )
            keys = align_keys
            if keys != self._last_sent or self._turn_pulse_phase in ("on", "off"):
                self._post_keys(keys)
                self._last_sent = list(keys)
            self._write_status(
                {"x": 0.0, "y": 0.0}, keys, [keys[0]] if keys else [], age
            )
            return

        segment_keys, segment_active = self._segment_nav_keys(now)
        if segment_active or self._segment_nav_active:
            self._phase = self._phase if segment_active else "segment_done"
            keys = segment_keys
            if keys != self._last_sent or self._turn_pulse_phase in ("on", "off"):
                self._post_keys(keys)
                self._last_sent = list(keys)
            self._write_status({"x": 0.0, "y": 0.0}, keys, segment_keys, age)
            return

        pulse_on = TURN_PULSE_ON_PURE_SEC
        pulse_off = TURN_PULSE_OFF_PURE_SEC
        turn_machine_busy = self._turn_pulse_phase in ("on", "off") and now < (
            self._turn_pulse_until + 0.05
        )
        moving_cmd = bool(desired_keys) or turn_machine_busy

        if paused:
            self._phase = "paused"
            keys = []
            self._turn_pulse_phase = "idle"
            self._turn_pulse_until = 0.0
            self._latched_turn = None
            self._pure_turn_since = 0.0
        elif moving_cmd or turn_machine_busy:
            if now - self._last_moving_at > 2.0:
                self._settle_until = now + max(0.0, START_SETTLE_SEC)
            self._last_moving_at = now
            if now < self._settle_until:
                self._phase = "settle"
                keys = []
                self._turn_pulse_phase = "idle"
                self._turn_pulse_until = 0.0
                self._latched_turn = None
            else:
                self._phase = "drive"
                (
                    keys,
                    self._turn_pulse_phase,
                    self._turn_pulse_until,
                    self._latched_turn,
                ) = pulse_turn_keys(
                    desired_keys,
                    now=now,
                    phase=self._turn_pulse_phase,
                    phase_until=self._turn_pulse_until,
                    latched_turn=self._latched_turn,
                    on_pure=pulse_on,
                    off_pure=pulse_off,
                )
                if self._turn_pulse_phase == "off":
                    self._phase = "yaw_settle"
                turn_only = (
                    bool(desired_keys)
                    and not any(k in desired_keys for k in ("w", "s"))
                ) or self._turn_pulse_phase in ("on", "off")
                if turn_only:
                    if self._pure_turn_since <= 0.0:
                        self._pure_turn_since = now
                else:
                    self._pure_turn_since = 0.0
        else:
            self._phase = "idle"
            keys = []
            self._turn_pulse_phase = "idle"
            self._turn_pulse_until = 0.0
            self._latched_turn = None

        if keys != self._last_sent or moving_cmd or turn_machine_busy:
            self._post_keys(keys)
            self._last_sent = list(keys)
        self._write_status(desired_drive, keys, desired_keys, age)

    def hold_keys(self, keys: list[str]) -> None:
        """Exclusive motor ownership for fine docking (blocks cmd_vel key spam)."""
        with self._lock:
            self._hold_active = True
            self._hold_keys = list(keys)
            self._paused = False
        self._phase = "dock_hold"
        self._post_keys(keys)
        self._last_sent = list(keys)

    def release_hold(self) -> None:
        with self._lock:
            self._hold_active = False
            self._hold_keys = []
        self._post_keys([])
        self._last_sent = []
        self._phase = "idle"

    def _post_keys(self, keys: list[str]) -> None:
        data = json.dumps({"keys": keys}).encode("utf-8")
        req = urllib.request.Request(
            DRIVE_KEYS_URL,
            data=data,
            method="POST",
            headers={"Content-Type": "application/json"},
        )
        if NAV_API_TOKEN:
            req.add_header("Authorization", f"Bearer {NAV_API_TOKEN}")
        try:
            urllib.request.urlopen(req, timeout=1.2, context=self._ssl)
        except Exception as err:  # noqa: BLE001 — never crash the ROS context on drive I/O
            self.get_logger().warning(
                f"drive keys post failed: {err}", throttle_duration_sec=2.0
            )

    def _nav_ui_payload(self) -> dict[str, Any]:
        """Structured phase + live gap metrics (no predicted pulse counts)."""
        pose = lookup_map_pose_from_context()
        ui: dict[str, Any] = {"phase": None}

        if self._path_align_active and not self._path_align_done:
            ui["phase"] = 1
            ui["label"] = "Phase 1 · Align"
            if pose is not None and self._path_align_target_yaw is not None:
                err = wrap_angle(self._path_align_target_yaw - pose[2])
                ui["yaw_remaining_deg"] = round(math.degrees(err), 1)
                ui["target_yaw_deg"] = round(math.degrees(self._path_align_target_yaw), 1)
            else:
                ui["yaw_remaining_deg"] = None
            ui["pulses_done"] = self._path_align_pulse_count
            return ui

        if self._segment_nav_active:
            ui["phase"] = 2
            idx = self._segment_nav_state.segment_index
            total = len(self._segments)
            ui["label"] = "Phase 2 · Segments"
            ui["segment"] = idx + 1 if total else 0
            ui["segments_total"] = total
            ui["segment_phase"] = self._segment_nav_state.phase
            ui["waiting_replan"] = self._segment_waiting_replan
            ui["note"] = self._segment_nav_state.note or ""
            if pose is not None and total and 0 <= idx < total:
                seg = self._segments[idx]
                ui["active_segment"] = {
                    "index": idx,
                    "x0": round(seg.x0, 3),
                    "y0": round(seg.y0, 3),
                    "x1": round(seg.x1, 3),
                    "y1": round(seg.y1, 3),
                    "length_m": round(seg.length_m, 3),
                    "heading_deg": round(seg.heading_deg, 1),
                }
                # Compact list for map overlay (all remaining + done context).
                ui["segments"] = [
                    {
                        "i": i,
                        "x0": round(s.x0, 3),
                        "y0": round(s.y0, 3),
                        "x1": round(s.x1, 3),
                        "y1": round(s.y1, 3),
                    }
                    for i, s in enumerate(self._segments)
                ]
                yaw_gap = wrap_angle(segment_aim_heading(pose[0], pose[1], seg) - pose[2])
                ui["yaw_gap_deg"] = round(math.degrees(yaw_gap), 1)
                ui["aim_heading_deg"] = round(
                    math.degrees(segment_aim_heading(pose[0], pose[1], seg)), 1
                )
                if self._segment_nav_state.phase == "drive":
                    traveled = drive_progress_m(
                        self._segment_nav_state.drive_origin,
                        pose[0],
                        pose[1],
                        seg.heading_rad,
                    )
                    ui["drive_remaining_m"] = round(max(0.0, seg.length_m - traveled), 2)
                    ui["drive_done_m"] = round(traveled, 2)
                    ui["segment_length_m"] = round(seg.length_m, 2)
                else:
                    ui["start_drift_m"] = round(
                        segment_start_drift_m(pose[0], pose[1], seg), 2
                    )
            return ui

        if _goal_node is not None and getattr(_goal_node, "_fine_docking", False):
            dock_target = getattr(_goal_node, "_dock_target", None)
            if dock_target is not None:
                ui["phase"] = 3
                ui["label"] = "Phase 3 · Dock"
                ui["dock_phase"] = getattr(_goal_node, "_dock_phase", None)
                ui["note"] = ""
                tx, ty, tyaw = dock_target
                if pose is not None:
                    dist = math.hypot(tx - pose[0], ty - pose[1])
                    yaw_err = wrap_angle(tyaw - pose[2])
                    _, fwd, left, _ = body_frame_error(pose[0], pose[1], pose[2], tx, ty)
                    ui["position_error_m"] = round(dist, 4)
                    ui["yaw_remaining_deg"] = round(math.degrees(yaw_err), 1)
                    ui["fwd_m"] = round(fwd, 3)
                    ui["left_m"] = round(left, 3)
                    ui["target_yaw_deg"] = round(math.degrees(tyaw), 1)
                ui["yaw_pulses"] = getattr(_goal_node, "_dock_yaw_pulses", 0)
                ui["keys"] = list(getattr(_goal_node, "_dock_hold_keys", None) or [])
                return ui

        return ui

    def _write_status(
        self,
        desired_drive: dict[str, float],
        keys: list[str],
        desired_keys: list[str],
        age: float,
    ) -> None:
        status = {
            "enabled": True,
            "phase": self._phase,
            "control": "nav2_wasd_pulsed_turn",
            "start_settle_s": START_SETTLE_SEC,
            "start_ramp_s": START_RAMP_SEC,
            "turn_pulse": self._turn_pulse_phase,
            "keys": keys,
            "desired_keys": desired_keys,
            "drive": {"x": 0.0, "y": 0.0},
            "desired": desired_drive,
            "cmd_age_s": round(age, 3),
            "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "nav_ui": self._nav_ui_payload(),
        }
        try:
            directory = os.path.dirname(STATUS_PATH)
            if directory:
                os.makedirs(directory, exist_ok=True)
            tmp = f"{STATUS_PATH}.tmp"
            with open(tmp, "w", encoding="utf-8") as handle:
                json.dump(status, handle)
            os.replace(tmp, STATUS_PATH)
        except OSError:
            pass

    def stop_motors(self) -> None:
        self._phase = "idle"
        self._post_keys([])
        self._last_sent = []

    def snapshot(self) -> dict[str, Any]:
        """Current drive bridge state for nav progress / stall diagnostics."""
        desired_drive, desired_keys, age, paused = self._desired()
        with self._lock:
            latest = self._latest
            vx = float(latest.linear.x) if latest is not None else 0.0
            wz = float(latest.angular.z) if latest is not None else 0.0
        # Prefer last-sent; during latched on, surface the latch even if a post lagged.
        keys = list(self._last_sent or [])
        if self._turn_pulse_phase == "on" and self._latched_turn and not keys:
            keys = list(self._latched_turn)
        dock_keys: list[str] = []
        if not keys and _goal_node is not None:
            dock_keys = list(getattr(_goal_node, "_dock_hold_keys", None) or [])
        return {
            "phase": self._phase,
            "paused": paused,
            "keys": keys if keys else dock_keys,
            "desired_keys": list(desired_keys),
            "cmd_age_s": round(age, 3),
            "cmd_vx": round(vx, 3),
            "cmd_wz": round(wz, 3),
            "desired": desired_drive,
            "turn_pulse": self._turn_pulse_phase,
            "latched_turn": list(self._latched_turn) if self._latched_turn else [],
            "path_align": {
                "active": self._path_align_active,
                "done": self._path_align_done,
                "target_yaw_deg": round(math.degrees(self._path_align_target_yaw), 1)
                if self._path_align_target_yaw is not None
                else None,
                "pulses": self._path_align_pulse_count,
            },
            "segment_nav": {
                "active": self._segment_nav_active,
                "waiting_replan": self._segment_waiting_replan,
                "segment_index": self._segment_nav_state.segment_index,
                "phase": self._segment_nav_state.phase,
                "segments_total": len(self._segments),
                "note": self._segment_nav_state.note,
            },
            "nav_ui": self._nav_ui_payload(),
        }

    def prepare_final_yaw_handoff(self) -> None:
        """Stop segment/path-align ownership without latching the kill switch.

        ``pause()`` writes navigation_kill.json, which the phase-3 timer treats
        as a user cancel — that aborted final yaw instantly and left kill latched
        until the next goto.
        """
        with self._lock:
            self._path_align_active = False
            self._path_align_done = True
            self._path_align_target_yaw = None
            self._segment_nav_active = False
            self._segment_nav_state = SegmentNavState()
            self._segments = []
            self._segment_waiting_replan = False
            self._turn_pulse_phase = "idle"
            self._turn_pulse_until = 0.0
            self._latched_turn = None
            self._paused = False
        self._phase = "final_yaw_handoff"
        self._last_sent = []
        self._post_keys([])

    def pause(self) -> None:
        try:
            directory = os.path.dirname(KILL_PATH)
            if directory:
                os.makedirs(directory, exist_ok=True)
            tmp = f"{KILL_PATH}.tmp"
            with open(tmp, "w", encoding="utf-8") as handle:
                json.dump({"latched": True, "updated_at": time.time()}, handle)
            os.replace(tmp, KILL_PATH)
        except OSError:
            pass
        with self._lock:
            self._paused = True
            self._latest = None
            self._latest_at = 0.0
            self._hold_active = False
            self._hold_keys = []
            self._path_align_active = False
            self._path_align_done = False
            self._path_align_target_yaw = None
            self._segment_nav_active = False
            self._segment_nav_state = SegmentNavState()
            self._segments = []
            self._segment_waiting_replan = False
        self._phase = "paused"
        self._last_sent = []
        self._post_keys([])

    def resume(self) -> None:
        try:
            os.remove(KILL_PATH)
        except FileNotFoundError:
            pass
        with self._lock:
            self._paused = False
            self._latest = None
            self._latest_at = 0.0
            self._hold_active = False
            self._hold_keys = []
        self.begin_path_align()
        self._phase = "idle"


class GoalNode(Node):
    def __init__(self) -> None:
        super().__init__("rover_nav_goal_server")
        self._client = ActionClient(self, NavigateToPose, "navigate_to_pose")
        self._follow_cancel_client = self.create_client(
            CancelGoal, "/follow_path/_action/cancel_goal"
        )
        self._cmd_pub = self.create_publisher(Twist, CMD_VEL_TOPIC, 10)
        self._tf_buffer = Buffer()
        self._tf_listener = TransformListener(self._tf_buffer, self)
        self._goal_handle = None
        self._send_lock = threading.Lock()
        self._generation = 0
        self._fine_docking = False
        self._dock_target: tuple[float, float, float] | None = None
        self._dock_started = 0.0
        self._dock_generation = 0
        self._dock_settled = 0
        self._dock_timer = None
        self._dock_settle_until = 0.0
        self._dock_phase = "approach"  # approach | yaw | measure | done
        self._dock_yaw_dir = 0  # +1 = A (CCW), -1 = D (CW)
        self._dock_yaw_pulses = 0
        self._dock_yaw_overshot = False
        self._dock_hold_keys: list[str] = []
        self._dock_hold_until = 0.0
        self._dock_pose_samples: list[tuple[float, float, float]] = []
        self._dock_need_measure = False
        self._nav_id = ""
        self._nav_started_at = 0.0
        self._best_distance: float | None = None
        self._last_improve_at = 0.0
        self._last_distance: float | None = None
        self._stall_warned = False
        self._stall_evented = False
        self._last_progress_log_at = 0.0
        self._last_context_refresh_at = 0.0
        self._last_assist_refresh_at = 0.0
        self._gap_close_state = GapCloseState()
        self.create_timer(max(0.5, NAV_PROGRESS_PERIOD_SEC), self._progress_tick)
        self.get_logger().info(
            f"Nav2 NavigateToPose action client ready run_log={RUN_LOG_PATH}"
        )

    def wait_server(self, timeout_sec: float = 120.0) -> bool:
        return self._client.wait_for_server(timeout_sec=timeout_sec)

    def _reset_progress_trackers(self) -> None:
        self._best_distance = None
        self._last_improve_at = time.time()
        self._last_distance = None
        self._stall_warned = False
        self._stall_evented = False
        self._last_progress_log_at = 0.0
        self._last_context_refresh_at = 0.0
        self._last_assist_refresh_at = 0.0

    def _drive_snapshot(self) -> dict[str, Any]:
        if _cmd_bridge is None:
            return {}
        try:
            return _cmd_bridge.snapshot()
        except Exception:  # noqa: BLE001
            return {}

    def _attach_environment(self, info: dict[str, Any], *, full: bool, refresh_assist: bool) -> None:
        ctx = nav_context(full=full, refresh_assist=refresh_assist)
        info["map"] = ctx.get("map")
        info["scan"] = ctx.get("scan")
        info["drive_assist"] = ctx.get("drive_assist")
        if full and ctx.get("nav_config"):
            info["nav_config"] = ctx["nav_config"]
        if drive_assist_blocking(info.get("drive_assist")):
            info["motion_hold"] = "drive_assist"

    def _progress_payload(
        self,
        *,
        pose: tuple[float, float, float] | None = None,
        distance: float | None = None,
    ) -> dict[str, Any]:
        if pose is None:
            pose = self._lookup_map_pose()
        goal = self._dock_target
        drive = self._drive_snapshot()
        path = read_path_summary()
        now = time.time()
        dist = distance
        if dist is None and pose is not None and goal is not None:
            dist = math.hypot(goal[0] - pose[0], goal[1] - pose[1])
        stall_s = 0.0
        if self._last_improve_at > 0:
            stall_s = max(0.0, now - self._last_improve_at)
        payload: dict[str, Any] = {
            "nav_id": self._nav_id,
            "generation": self._generation,
            "elapsed_s": round(now - self._nav_started_at, 1) if self._nav_started_at else 0.0,
            "distance_remaining": round(dist, 3) if dist is not None else None,
            "best_distance_m": round(self._best_distance, 3)
            if self._best_distance is not None
            else None,
            "stall_s": round(stall_s, 1),
            "drive": drive,
            "path": path,
        }
        if pose is not None:
            payload["pose"] = {
                "x": round(pose[0], 3),
                "y": round(pose[1], 3),
                "yaw": round(pose[2], 4),
                "yaw_deg": round(math.degrees(pose[2]), 1),
            }
        if goal is not None:
            payload["goal"] = {
                "x": round(goal[0], 3),
                "y": round(goal[1], 3),
                "yaw": round(goal[2], 4),
            }
            if pose is not None:
                bearing = math.atan2(goal[1] - pose[1], goal[0] - pose[0])
                payload["heading_err_deg"] = round(
                    math.degrees(wrap_angle(bearing - pose[2])), 1
                )
                payload["goal_yaw_err_deg"] = round(
                    math.degrees(wrap_angle(goal[2] - pose[2])), 1
                )
        return payload

    def _note_distance(self, distance: float | None) -> None:
        if distance is None:
            return
        self._last_distance = float(distance)
        if self._best_distance is None or distance < self._best_distance - 0.05:
            self._best_distance = float(distance)
            self._last_improve_at = time.time()
            self._stall_warned = False
            self._stall_evented = False

    def _progress_tick(self) -> None:
        with _goal_lock:
            status = str(_goal_state.get("status") or "")
            feedback = _goal_state.get("feedback")
        if status not in ("navigating", "docking"):
            return
        pose = self._lookup_map_pose()
        # Prefer map pose→goal distance. Nav2 feedback can report 0 early and
        # poison best_distance / stall timers for the whole run.
        dist = None
        if pose is not None and self._dock_target is not None:
            dist = math.hypot(
                self._dock_target[0] - pose[0], self._dock_target[1] - pose[1]
            )
        elif isinstance(feedback, dict) and feedback.get("distance_remaining") is not None:
            try:
                dist = float(feedback["distance_remaining"])
            except (TypeError, ValueError):
                dist = None
        self._note_distance(dist)
        now = time.time()
        refresh_assist = now - self._last_assist_refresh_at >= NAV_ASSIST_REFRESH_SEC
        if refresh_assist:
            self._last_assist_refresh_at = now
        full_ctx = now - self._last_context_refresh_at >= NAV_CONTEXT_REFRESH_SEC
        if full_ctx:
            self._last_context_refresh_at = now
        info = self._progress_payload(pose=pose, distance=dist)
        self._attach_environment(info, full=full_ctx, refresh_assist=refresh_assist)
        assist = info.get("drive_assist") or {}
        drive_snap = info.get("drive") or {}
        drive_phase = str(drive_snap.get("phase") or "")
        path_align = drive_snap.get("path_align") or {}
        if drive_assist_blocking(assist):
            # Pi drive-assist may block wheels while Nav2 still commands forward —
            # do not treat that as a nav planner stall.
            self._last_improve_at = now
            info["stall_s"] = 0.0
        elif path_align.get("active") or drive_phase.startswith(
            ("path_align", "segment")
        ):
            # Phase 1/2 rotate or drive segments — distance may not improve yet.
            self._last_improve_at = now
            info["stall_s"] = 0.0
        enriched = dict(feedback) if isinstance(feedback, dict) else {}
        enriched.update(
            {
                "pose": info.get("pose"),
                "stall_s": info.get("stall_s"),
                "best_distance_m": info.get("best_distance_m"),
                "heading_err_deg": info.get("heading_err_deg"),
                "keys": (info.get("drive") or {}).get("keys"),
                "cmd_vx": (info.get("drive") or {}).get("cmd_vx"),
                "cmd_wz": (info.get("drive") or {}).get("cmd_wz"),
                "drive_phase": (info.get("drive") or {}).get("phase"),
                "path_length_m": (info.get("path") or {}).get("path_length_m"),
                "motion_hold": info.get("motion_hold"),
                "scan_nearest_m": (info.get("scan") or {}).get("nearest_m"),
                "drive_assist_blocked": drive_assist_blocking(assist),
            }
        )
        with _goal_lock:
            if str(_goal_state.get("status") or "") not in ("navigating", "docking"):
                return
            _goal_state["feedback"] = enriched
            _goal_state["updated_at"] = time.time()
        write_goal_status()

        now = time.time()
        stall_s = float(info.get("stall_s") or 0.0)
        keys = (info.get("drive") or {}).get("keys") or []
        assist_hold = info.get("motion_hold") == "drive_assist"
        log_line = (
            f"nav progress nav_id={self._nav_id} status={status} "
            f"elapsed={info.get('elapsed_s')}s dist={info.get('distance_remaining')} "
            f"best={info.get('best_distance_m')} stall={stall_s:.1f}s "
            f"keys={keys} cmd_vx={(info.get('drive') or {}).get('cmd_vx')} "
            f"cmd_wz={(info.get('drive') or {}).get('cmd_wz')} "
            f"pose={info.get('pose')} path_m={(info.get('path') or {}).get('path_length_m')} "
            f"hdg_err={info.get('heading_err_deg')} "
            f"scan_near={(info.get('scan') or {}).get('nearest_m')} "
            f"assist={assist_hold} "
            f"drive_phase={(info.get('drive') or {}).get('phase')} "
            f"nav_ui={(info.get('drive') or {}).get('nav_ui')}"
        )
        if now - self._last_progress_log_at >= NAV_PROGRESS_PERIOD_SEC:
            self._last_progress_log_at = now
            self.get_logger().info(log_line)
            append_nav_run_event("progress", **info)

        if assist_hold:
            return

        if path_align.get("active") or drive_phase.startswith(
            ("path_align", "segment")
        ):
            return

        if status == "navigating" and stall_s >= NAV_STALL_WARN_SEC and not self._stall_warned:
            self._stall_warned = True
            self.get_logger().warning(
                f"nav stall warn nav_id={self._nav_id} stall={stall_s:.1f}s "
                f"dist={info.get('distance_remaining')} keys={keys} "
                f"pose={info.get('pose')} path_m={(info.get('path') or {}).get('path_length_m')}"
            )
            append_nav_run_event("stall_warn", **info)
        if status == "navigating" and stall_s >= NAV_STALL_EVENT_SEC and not self._stall_evented:
            self._stall_evented = True
            self.get_logger().error(
                f"nav stall nav_id={self._nav_id} stall={stall_s:.1f}s "
                f"no distance improvement; likely collision-ahead / recovery loop"
            )
            append_nav_run_event("stall", **info)

    def goto(
        self,
        x: float,
        y: float,
        yaw: float,
        label: str = "",
        *,
        fine_docking: bool = False,
        nav_id: str | None = None,
    ) -> dict[str, Any]:
        if not rclpy.ok():
            return {"success": False, "error": "ROS context is shutting down"}
        with self._send_lock:
            if not self._client.server_is_ready():
                if not self.wait_server(5.0):
                    return {"success": False, "error": "Nav2 navigate_to_pose not ready"}
            try:
                os.remove(KILL_PATH)
            except FileNotFoundError:
                pass
            if _cmd_bridge is not None:
                _cmd_bridge.resume()

            # Drop prior goal without clobbering the new "navigating" status.
            self._stop_fine_docking(publish_stop=False)
            self._cancel_handle_only()
            self._generation += 1
            generation = self._generation
            self._fine_docking = bool(fine_docking)
            self._dock_target = (float(x), float(y), float(yaw))
            self._nav_id = (nav_id or "").strip() or make_nav_id()
            self._nav_started_at = time.time()
            self._reset_progress_trackers()
            return self._dispatch_navigate_goal(
                x,
                y,
                yaw,
                label=label,
                fine_docking=fine_docking,
                nav_id=self._nav_id,
                generation=generation,
                log_event="goto",
            )

    def replan_after_path_align(self) -> None:
        """Legacy alias — segment nav calls ``request_segment_replan``."""
        self.request_segment_replan("legacy_replan")

    def request_segment_replan(self, reason: str) -> None:
        """Ask Nav2 to replan from the current pose (drift or blocked path)."""
        if not rclpy.ok():
            return
        with self._send_lock:
            with _goal_lock:
                if str(_goal_state.get("status") or "") != "navigating":
                    return
                goal_meta = _goal_state.get("goal")
                if not isinstance(goal_meta, dict):
                    return
                x = float(goal_meta["x"])
                y = float(goal_meta["y"])
                yaw = float(goal_meta.get("yaw") or 0.0)
                label = str(goal_meta.get("label") or "")
                fine = bool(goal_meta.get("fine_docking"))
            start_pose = self._lookup_map_pose()
            self.get_logger().info(
                f"segment replan ({reason}) nav_id={self._nav_id} pose={start_pose}"
            )
            self._cancel_handle_only()
            self._cancel_controller_goals()
            self._generation += 1
            generation = self._generation
            self._dispatch_navigate_goal(
                x,
                y,
                yaw,
                label=label,
                fine_docking=fine,
                nav_id=self._nav_id,
                generation=generation,
                log_event=f"replan_{reason}",
            )

    def cancel_controller_for_segment_nav(self) -> None:
        """Stop Nav2 FollowPath while the bridge executes segment actions."""
        self._cancel_controller_goals()

    def detach_nav2_for_segment_nav(self) -> None:
        """Cancel Nav2 BT + controller; bump generation so the cancel callback is ignored."""
        self._generation += 1
        self._cancel_handle_only()
        self._cancel_controller_goals()
        self._goal_handle = None
        self.get_logger().info(
            f"Nav2 detached for segment nav nav_id={self._nav_id} gen={self._generation}"
        )

    def on_segment_nav_complete(self) -> None:
        """Phase 2 done — hand off to phase 3 final yaw (no translation)."""
        pose = self._lookup_map_pose()
        if self._dock_target is not None and pose is not None:
            dist = math.hypot(
                self._dock_target[0] - pose[0], self._dock_target[1] - pose[1]
            )
            if dist > NAV_COARSE_DONE_MIN_M:
                self.get_logger().warning(
                    f"segment nav complete ignored — still {dist:.2f}m from goal "
                    f"(min={NAV_COARSE_DONE_MIN_M}m) nav_id={self._nav_id}; "
                    f"requesting replan"
                )
                if _cmd_bridge is not None and _cmd_bridge._try_reload_remaining_segments(
                    pose
                ):
                    self.get_logger().info(
                        f"resumed segment nav with "
                        f"{len(_cmd_bridge._segments)} remaining segments"
                    )
                    return
                self.request_segment_replan("replan_short_of_goal")
                if _cmd_bridge is not None:
                    _cmd_bridge._segment_nav_active = True
                    _cmd_bridge._segment_waiting_replan = True
                return
        # Mark docking before canceling Nav2 so the async cancel callback does
        # not latch kill / abort the handoff (nav-20260825-023608).
        if self._dock_target is not None:
            self._fine_docking = True
        try:
            os.remove(KILL_PATH)
        except FileNotFoundError:
            pass
        self._cancel_handle_only()
        self._cancel_controller_goals()
        info = self._progress_payload()
        # info already includes nav_id — do not pass it twice.
        append_nav_run_event("segments_done", **info)
        self.get_logger().info(f"segment nav complete nav_id={self._nav_id}")
        if _cmd_bridge is not None:
            _cmd_bridge.prepare_final_yaw_handoff()
        if self._dock_target is not None:
            self._start_final_yaw(self._generation)
            return
        with _goal_lock:
            _goal_state.update(
                {
                    "status": "idle",
                    "result": "succeeded",
                    "updated_at": time.time(),
                }
            )
        write_goal_status()

    def _dispatch_navigate_goal(
        self,
        x: float,
        y: float,
        yaw: float,
        *,
        label: str,
        fine_docking: bool,
        nav_id: str,
        generation: int,
        log_event: str,
    ) -> dict[str, Any]:
        pose = PoseStamped()
        pose.header.frame_id = MAP_FRAME
        pose.header.stamp = self.get_clock().now().to_msg()
        pose.pose.position.x = float(x)
        pose.pose.position.y = float(y)
        pose.pose.position.z = 0.0
        qx, qy, qz, qw = yaw_to_quat(float(yaw))
        pose.pose.orientation.x = qx
        pose.pose.orientation.y = qy
        pose.pose.orientation.z = qz
        pose.pose.orientation.w = qw

        goal = NavigateToPose.Goal()
        goal.pose = pose
        goal_meta = {
            "x": round(float(x), 3),
            "y": round(float(y), 3),
            "yaw": round(float(yaw), 4),
            "label": label or "",
            "fine_docking": bool(fine_docking),
        }
        start_pose = self._lookup_map_pose()
        straight = None
        if start_pose is not None:
            straight = round(
                math.hypot(float(x) - start_pose[0], float(y) - start_pose[1]), 3
            )
            if log_event == "goto":
                self._note_distance(straight)
        with _goal_lock:
            _goal_state.update(
                {
                    "status": "navigating",
                    "goal": goal_meta,
                    "result": None,
                    "feedback": {
                        "distance_remaining": straight,
                        "pose": {
                            "x": round(start_pose[0], 3),
                            "y": round(start_pose[1], 3),
                            "yaw": round(start_pose[2], 4),
                        }
                        if start_pose is not None
                        else None,
                    },
                    "updated_at": time.time(),
                    "nav_id": nav_id,
                }
            )
        write_goal_status()

        send_future = self._client.send_goal_async(
            goal, feedback_callback=lambda fb, gen=generation: self._on_feedback(fb, gen)
        )
        send_future.add_done_callback(
            lambda fut, gen=generation: self._on_goal_response(fut, gen)
        )
        if log_event == "goto":
            self.get_logger().info(
                f"goto nav_id={nav_id} label={label or ''} fine_docking={bool(fine_docking)} "
                f"target=({x:.3f},{y:.3f},{math.degrees(yaw):.1f}°) "
                f"start={start_pose} straight_m={straight}"
            )
        else:
            self.get_logger().info(
                f"{log_event} nav_id={nav_id} target=({x:.3f},{y:.3f}) "
                f"start={start_pose} straight_m={straight}"
            )
        if log_event == "goto":
            self._last_context_refresh_at = time.time()
            self._last_assist_refresh_at = time.time()
        event_fields: dict[str, Any] = {
            "nav_id": nav_id,
            "generation": generation,
            "label": label or "",
            "fine_docking": bool(fine_docking),
            "target": {"x": float(x), "y": float(y), "yaw": float(yaw)},
            "start_pose": (
                {
                    "x": round(start_pose[0], 3),
                    "y": round(start_pose[1], 3),
                    "yaw": round(start_pose[2], 4),
                }
                if start_pose is not None
                else None
            ),
            "straight_m": straight,
        }
        if log_event == "goto":
            event_fields["context"] = nav_context(full=True, refresh_assist=True)
        append_nav_run_event(log_event, **event_fields)
        return {
            "success": True,
            "nav_id": nav_id,
            "goal": goal_meta,
            "status": "navigating",
        }

    def _lookup_map_pose(self) -> tuple[float, float, float] | None:
        try:
            tf = self._tf_buffer.lookup_transform(
                MAP_FRAME,
                BASE_FRAME,
                Time(),
                timeout=Duration(seconds=0.25),
            )
        except TransformException:
            return None
        q = tf.transform.rotation
        yaw = yaw_from_quat(q.x, q.y, q.z, q.w)
        return (
            float(tf.transform.translation.x),
            float(tf.transform.translation.y),
            float(yaw),
        )

    def _average_pose_samples(
        self, samples: list[tuple[float, float, float]]
    ) -> tuple[float, float, float] | None:
        if not samples:
            return None
        xs = [s[0] for s in samples]
        ys = [s[1] for s in samples]
        # Circular mean for yaw — avoids stale single-frame spikes.
        s = sum(math.sin(s[2]) for s in samples)
        c = sum(math.cos(s[2]) for s in samples)
        return (
            sum(xs) / len(xs),
            sum(ys) / len(ys),
            math.atan2(s, c),
        )

    def _dock_post_keys(self, keys: list[str]) -> None:
        if _cmd_bridge is not None:
            # Exclusive hold so cmd_vel idle keepalive cannot clear A/D mid-dock.
            _cmd_bridge.hold_keys(keys)
        else:
            self._cmd_pub.publish(Twist())

    def _fine_dock_step(
        self,
        pose: tuple[float, float, float],
        target: tuple[float, float, float],
    ) -> tuple[list[str], bool, float, dict[str, Any]]:
        """Phase 3: face+drive to XY, then final yaw (no 90° lateral combos).

        Lateral turn→drive→turn stalled ~75° into lat_t1 with micro pulses
        (nav-20260825-025023 docking_timeout). Face-and-drive remeasures every
        tick so yaw alignment cannot trap us in a quarter-turn.
        """
        px, py, pyaw = pose
        tx, ty, tyaw = target
        plan = fine_dock_plan(px, py, pyaw, tx, ty, tyaw)
        phase = str(plan.get("phase") or "pick")
        self._dock_phase = phase
        self._gap_close_state = GapCloseState()  # unused; keep reset for status

        dist = float(plan.get("position_error_m") or 0.0)
        yaw_err_deg = float(plan.get("yaw_error_deg") or 0.0)
        fwd = float(plan.get("fwd_m") or 0.0)
        left = float(plan.get("left_m") or 0.0)
        yaw_err = wrap_angle(tyaw - pyaw)

        metrics: dict[str, Any] = {
            "position_error_m": round(dist, 4),
            "yaw_error_deg": round(yaw_err_deg, 2),
            "fwd_m": round(fwd, 3),
            "left_m": round(left, 3),
            "heading_err_deg": plan.get("heading_err_deg"),
            "dock_phase": phase,
            "dock_note": phase,
            "yaw_pulses": self._dock_yaw_pulses,
            "pose_yaw_deg": plan.get("pose_yaw_deg"),
            "target_yaw_deg": plan.get("target_yaw_deg"),
            "fine_docking": True,
            "final_yaw": phase == "yaw",
        }

        if plan.get("done"):
            metrics["dock_phase"] = "done"
            metrics["dock_note"] = "within tolerance"
            return [], True, 0.0, metrics

        keys = list(plan.get("keys") or [])
        if not keys:
            return [], False, 0.0, metrics

        pulse_s = float(plan.get("pulse_s") or 0.0)
        settle_s = OBSERVE_SETTLE_SEC

        if phase in ("shift_face", "yaw"):
            if self._dock_yaw_dir == 0 and abs(yaw_err) > math.radians(1.0):
                # Face uses heading_err; yaw uses goal yaw — track sign of keys.
                self._dock_yaw_dir = 1 if keys == ["a"] else -1
            if phase == "yaw":
                overshot = self._dock_yaw_dir != 0 and (
                    (self._dock_yaw_dir > 0 and yaw_err < 0)
                    or (self._dock_yaw_dir < 0 and yaw_err > 0)
                )
                if overshot:
                    self._dock_yaw_overshot = True
                    pulse_s = FINE_DOCK_TURN_MICRO_SEC
                self._dock_yaw_pulses += 1
                metrics["yaw_pulses"] = self._dock_yaw_pulses
                if self._dock_yaw_pulses >= FINE_DOCK_MAX_YAW_PULSES:
                    metrics["yaw_budget_exhausted"] = True
                    metrics["dock_note"] = "yaw pulse budget exhausted"
                    return [], False, 0.0, metrics

        metrics["yaw_settle_s"] = settle_s
        metrics["dock_note"] = f"{phase} [{pulse_s:.3f}s]"
        self.get_logger().info(
            f"Fine dock nav_id={self._nav_id} phase={phase} "
            f"keys={''.join(keys).upper()} Δxy={dist:.3f}m "
            f"fwd={fwd:+.3f} left={left:+.3f} Δyaw={yaw_err_deg:.1f}° "
            f"hold={pulse_s:.3f}s settle={settle_s:.1f}s"
        )
        return keys, False, pulse_s, metrics

    def _stop_fine_docking(self, *, publish_stop: bool = True) -> None:
        self._dock_generation += 1
        if self._dock_timer is not None:
            self._dock_timer.cancel()
            self._dock_timer = None
        self._dock_target = None
        self._dock_started = 0.0
        self._dock_settle_until = 0.0
        self._dock_phase = "yaw"
        self._dock_yaw_dir = 0
        self._dock_yaw_pulses = 0
        self._dock_yaw_overshot = False
        self._dock_hold_keys = []
        self._dock_hold_until = 0.0
        self._dock_pose_samples = []
        self._dock_need_measure = False
        self._gap_close_state = GapCloseState()
        if publish_stop:
            self._dock_post_keys([])
            if _cmd_bridge is not None:
                # Clear path_align BEFORE release_hold — otherwise leftover phase-1
                # align resumes and spins the mark (nav-20260825-025844 drifted
                # −12°→−28° after dock_finished succeeded).
                _cmd_bridge.prepare_final_yaw_handoff()
                _cmd_bridge.release_hold()

    def _start_final_yaw(self, generation: int) -> None:
        """Phase 3: face+drive to XY, then align destination yaw."""
        if self._dock_target is None:
            return
        # Near-goal Nav2 success never ran segment handoff — kill path_align now
        # or it fights the dock hold and resumes after release.
        if _cmd_bridge is not None:
            _cmd_bridge.prepare_final_yaw_handoff()
        self._dock_generation += 1
        dock_gen = self._dock_generation
        self._dock_started = time.time()
        self._dock_settled = 0
        self._dock_settle_until = 0.0
        self._dock_phase = "pick"
        self._dock_yaw_dir = 0
        self._dock_yaw_pulses = 0
        self._dock_yaw_overshot = False
        self._dock_hold_keys = []
        self._dock_hold_until = 0.0
        self._dock_pose_samples = []
        self._dock_need_measure = False
        self._gap_close_state = GapCloseState()
        self._fine_docking = True
        # Segment handoff cancels Nav2 which can race-latch kill — clear it.
        try:
            os.remove(KILL_PATH)
        except FileNotFoundError:
            pass
        pose0 = self._lookup_map_pose()
        with _goal_lock:
            _goal_state.update(
                {
                    "status": "docking",
                    "result": None,
                    "feedback": {
                        "fine_docking": True,
                        "final_yaw": False,
                        "dock_phase": "pick",
                    },
                    "updated_at": time.time(),
                }
            )
        write_goal_status()
        if pose0 is not None:
            tx, ty, tyaw = self._dock_target
            d0 = math.hypot(tx - pose0[0], ty - pose0[1])
            y0 = math.degrees(wrap_angle(tyaw - pose0[2]))
            self.get_logger().info(
                f"Fine dock start nav_id={self._nav_id} "
                f"Δxy={d0:.3f}m Δyaw={y0:.1f}° "
                f"xy_tol={FINE_DOCK_XY_TOL_M:.2f}m "
                f"yaw_tol={math.degrees(FINE_DOCK_YAW_TOL_RAD):.0f}°"
            )
        append_nav_run_event(
            "fine_dock_start",
            nav_id=self._nav_id,
            generation=generation,
            dock_generation=dock_gen,
            target=list(self._dock_target) if self._dock_target else None,
            start_pose=(
                {
                    "x": round(pose0[0], 3),
                    "y": round(pose0[1], 3),
                    "yaw": round(pose0[2], 4),
                }
                if pose0 is not None
                else None
            ),
        )

        def tick() -> None:
            if (
                generation != self._generation
                or dock_gen != self._dock_generation
                or self._dock_target is None
            ):
                return
            if os.path.isfile(KILL_PATH):
                self._finish_fine_docking("canceled", generation, dock_gen)
                return
            now = time.monotonic()
            if now < self._dock_hold_until:
                self._dock_post_keys(self._dock_hold_keys)
                return
            if now < self._dock_settle_until:
                self._dock_post_keys([])
                return

            pose = self._lookup_map_pose()
            if pose is None:
                return

            tx, ty, tyaw = self._dock_target
            keys, done, pulse_s, metrics = self._fine_dock_step(pose, (tx, ty, tyaw))
            feedback = {"fine_docking": True, **metrics}
            with _goal_lock:
                _goal_state["feedback"] = feedback
                _goal_state["updated_at"] = time.time()
            write_goal_status()
            # Record each dock pulse so rehearsal can step phase 3.
            if keys or done:
                dock_ui = {
                    "phase": 3,
                    "label": "Phase 3 · Dock",
                    "dock_phase": metrics.get("dock_phase"),
                    "position_error_m": metrics.get("position_error_m"),
                    "yaw_remaining_deg": metrics.get("yaw_error_deg"),
                    "fwd_m": metrics.get("fwd_m"),
                    "left_m": metrics.get("left_m"),
                    "note": metrics.get("dock_note") or "",
                    "dock_note": metrics.get("dock_note") or "",
                    "yaw_pulses": metrics.get("yaw_pulses"),
                }
                append_nav_run_event(
                    "dock_step",
                    nav_id=self._nav_id,
                    generation=generation,
                    dock_generation=dock_gen,
                    keys=list(keys),
                    pulse_s=round(pulse_s, 3) if pulse_s else 0.0,
                    distance_remaining=metrics.get("position_error_m"),
                    pose={
                        "x": round(pose[0], 3),
                        "y": round(pose[1], 3),
                        "yaw": round(pose[2], 4),
                        "yaw_deg": round(math.degrees(pose[2]), 1),
                    },
                    goal={
                        "x": round(tx, 3),
                        "y": round(ty, 3),
                        "yaw": round(tyaw, 4),
                    },
                    drive={
                        "phase": f"dock_{metrics.get('dock_phase') or 'step'}",
                        "keys": list(keys),
                        "nav_ui": dock_ui,
                    },
                    nav_ui=dock_ui,
                    dock_note=metrics.get("dock_note"),
                    feedback=feedback,
                )
            if metrics.get("yaw_budget_exhausted"):
                self._finish_fine_docking("docking_timeout", generation, dock_gen)
                return
            if done:
                self._dock_settled += 1
                self._dock_hold_keys = []
                self._dock_hold_until = 0.0
                self._dock_post_keys([])
                if self._dock_settled >= FINE_DOCK_SETTLE_TICKS:
                    self._finish_fine_docking("succeeded", generation, dock_gen)
                return
            self._dock_settled = 0
            if time.time() - self._dock_started > FINE_DOCK_TIMEOUT_SEC:
                self._finish_fine_docking("docking_timeout", generation, dock_gen)
                return
            if keys:
                self._dock_post_keys(keys)
                if pulse_s > 0:
                    settle_s = max(
                        float(metrics.get("yaw_settle_s") or 0.0),
                        OBSERVE_SETTLE_SEC,
                    )
                    self._dock_hold_keys = list(keys)
                    self._dock_hold_until = now + pulse_s
                    self._dock_settle_until = now + pulse_s + settle_s
                else:
                    self._dock_hold_keys = list(keys)
                    self._dock_hold_until = now + 0.15
                    self._dock_settle_until = 0.0
            else:
                self._dock_hold_keys = []
                self._dock_hold_until = 0.0
                self._dock_post_keys([])

        if self._dock_timer is not None:
            self._dock_timer.cancel()
        self._dock_timer = self.create_timer(0.1, tick)

    def _start_fine_docking(self, generation: int) -> None:
        self._start_final_yaw(generation)

    def _finish_fine_docking(
        self, result: str, generation: int, dock_gen: int
    ) -> None:
        if generation != self._generation or dock_gen != self._dock_generation:
            return
        pose_f = self._lookup_map_pose()
        target = self._dock_target
        info = self._progress_payload(pose=pose_f)
        if pose_f is not None and target is not None:
            tx, ty, tyaw = target
            df = math.hypot(tx - pose_f[0], ty - pose_f[1])
            yf = abs(wrap_angle(tyaw - pose_f[2]))
            if result == "docking_timeout" and fine_dock_accepted(df, yf):
                result = "succeeded"
            yf_deg = math.degrees(wrap_angle(tyaw - pose_f[2]))
            self.get_logger().info(
                f"Fine dock finished: {result} nav_id={self._nav_id} "
                f"final Δxy={df:.3f}m Δyaw={yf_deg:.1f}° phase={self._dock_phase} "
                f"yaw_pulses={self._dock_yaw_pulses}"
            )
        else:
            self.get_logger().info(
                f"Fine dock finished: {result} nav_id={self._nav_id}"
            )
        self._stop_fine_docking(publish_stop=True)
        self._fine_docking = False
        with _goal_lock:
            _goal_state.update(
                {
                    "status": "idle",
                    "result": result,
                    "updated_at": time.time(),
                }
            )
        write_goal_status()
        finished = dict(info)
        finished["generation"] = generation
        finished["result"] = result
        append_nav_run_event("dock_finished", **finished)

    def _cancel_handle_only(self) -> None:
        handle = self._goal_handle
        self._goal_handle = None
        if handle is not None:
            try:
                handle.cancel_goal_async()
            except Exception:  # noqa: BLE001
                pass

    def _cancel_controller_goals(self) -> None:
        """Cancel every FollowPath goal, including late/orphaned BT requests."""
        if not self._follow_cancel_client.service_is_ready():
            self.get_logger().warning(
                "follow_path cancel service unavailable",
                throttle_duration_sec=2.0,
            )
            return
        request = CancelGoal.Request()
        # Zero UUID + zero timestamp is the ROS action protocol's cancel-all.
        self._follow_cancel_client.call_async(request)

    def cancel(self) -> dict[str, Any]:
        self._generation += 1
        info = self._progress_payload()
        self._stop_fine_docking()
        self._fine_docking = False
        self._cancel_handle_only()
        self._cancel_controller_goals()
        if _cmd_bridge is not None:
            _cmd_bridge.pause()
        with _goal_lock:
            _goal_state.update(
                {
                    "status": "idle",
                    "result": "canceled",
                    "updated_at": time.time(),
                }
            )
        write_goal_status()
        if _path_bridge is not None:
            _path_bridge.clear()
        self.get_logger().info(f"nav canceled nav_id={self._nav_id} {info}")
        append_nav_run_event("canceled", **info)
        return {"success": True, "status": "idle"}

    def pause(self) -> dict[str, Any]:
        self._generation += 1
        info = self._progress_payload()
        self._stop_fine_docking()
        self._fine_docking = False
        self._cancel_handle_only()
        self._cancel_controller_goals()
        if _cmd_bridge is not None:
            _cmd_bridge.pause()
        with _goal_lock:
            _goal_state.update(
                {
                    "status": "paused",
                    "result": None,
                    "updated_at": time.time(),
                }
            )
        write_goal_status()
        self.get_logger().info(f"nav paused nav_id={self._nav_id} {info}")
        append_nav_run_event("paused", **info)
        return {"success": True, "status": "paused"}

    def _on_goal_response(self, future: Any, generation: int) -> None:
        if generation != self._generation:
            return
        try:
            handle = future.result()
        except Exception as exc:  # noqa: BLE001
            if _cmd_bridge is not None:
                _cmd_bridge.pause()
            self._cancel_controller_goals()
            if _path_bridge is not None:
                _path_bridge.clear()
            with _goal_lock:
                _goal_state.update(
                    {"status": "failed", "result": str(exc), "updated_at": time.time()}
                )
            write_goal_status()
            self.get_logger().error(f"nav goal response error nav_id={self._nav_id}: {exc}")
            append_nav_run_event(
                "goal_error", nav_id=self._nav_id, generation=generation, error=str(exc)
            )
            return
        if not handle.accepted:
            if _cmd_bridge is not None:
                _cmd_bridge.pause()
            self._cancel_controller_goals()
            if _path_bridge is not None:
                _path_bridge.clear()
            with _goal_lock:
                _goal_state.update(
                    {
                        "status": "rejected",
                        "result": "goal rejected",
                        "updated_at": time.time(),
                    }
                )
            write_goal_status()
            self.get_logger().warning(f"nav goal rejected nav_id={self._nav_id}")
            append_nav_run_event(
                "rejected", nav_id=self._nav_id, generation=generation
            )
            return
        self._goal_handle = handle
        self.get_logger().info(f"nav goal accepted nav_id={self._nav_id}")
        accepted = self._progress_payload()
        accepted["generation"] = generation
        append_nav_run_event("accepted", **accepted)
        result_future = handle.get_result_async()
        result_future.add_done_callback(
            lambda fut, gen=generation: self._on_result(fut, gen)
        )

    def _on_feedback(self, feedback_msg: Any, generation: int) -> None:
        if generation != self._generation:
            return
        fb = feedback_msg.feedback
        info: dict[str, Any] = {}
        try:
            dist = getattr(fb, "distance_remaining", None)
            if dist is not None:
                info["distance_remaining"] = round(float(dist), 3)
                self._note_distance(float(dist))
        except Exception:  # noqa: BLE001
            pass
        with _goal_lock:
            prev = _goal_state.get("feedback")
            merged = dict(prev) if isinstance(prev, dict) else {}
            merged.update(info)
            _goal_state["feedback"] = merged or None
            _goal_state["updated_at"] = time.time()
        write_goal_status()

    def _on_result(self, future: Any, generation: int) -> None:
        if generation != self._generation:
            return
        try:
            wrapped = future.result()
            status = int(wrapped.status)
        except Exception as exc:  # noqa: BLE001
            if _cmd_bridge is not None:
                _cmd_bridge.pause()
            self._cancel_controller_goals()
            if _path_bridge is not None:
                _path_bridge.clear()
            with _goal_lock:
                _goal_state.update(
                    {"status": "failed", "result": str(exc), "updated_at": time.time()}
                )
            write_goal_status()
            self._goal_handle = None
            self.get_logger().error(f"nav result error nav_id={self._nav_id}: {exc}")
            err_info = self._progress_payload()
            err_info["generation"] = generation
            err_info["error"] = str(exc)
            append_nav_run_event("result_error", **err_info)
            return

        status_name = {
            GoalStatus.STATUS_SUCCEEDED: "succeeded",
            GoalStatus.STATUS_CANCELED: "canceled",
            GoalStatus.STATUS_ABORTED: "aborted",
        }.get(status, f"status_{status}")
        # Segment handoff cancels Nav2 on purpose — do NOT latch kill / abort dock.
        if status_name == "canceled" and self._fine_docking:
            self.get_logger().info(
                f"Nav2 cancel ignored — fine docking nav_id={self._nav_id}"
            )
            self._goal_handle = None
            return
        if status_name == "canceled" and _cmd_bridge is not None and _cmd_bridge.segment_nav_active():
            self.get_logger().info(
                f"Nav2 cancel ignored — segment nav active nav_id={self._nav_id}"
            )
            return
        # Handoff just cleared segment_nav_active and is about to / just started dock.
        if (
            status_name == "canceled"
            and _cmd_bridge is not None
            and getattr(_cmd_bridge, "_path_align_done", False)
            and not _cmd_bridge.segment_nav_active()
            and self._dock_target is not None
            and _goal_state.get("status") == "docking"
        ):
            self.get_logger().info(
                f"Nav2 cancel ignored — segment→dock handoff nav_id={self._nav_id}"
            )
            self._goal_handle = None
            return
        info = self._progress_payload()
        if status_name == "succeeded" and self._fine_docking and self._dock_target is not None:
            if _cmd_bridge is not None and _cmd_bridge.segment_nav_active():
                self.get_logger().warning(
                    f"Nav2 success ignored — segment nav active nav_id={self._nav_id}"
                )
                return
            pose = self._lookup_map_pose()
            if pose is not None:
                dist = math.hypot(
                    self._dock_target[0] - pose[0], self._dock_target[1] - pose[1]
                )
                if dist > NAV_COARSE_DONE_MIN_M:
                    self.get_logger().warning(
                        f"Nav2 success ignored — still {dist:.2f}m from goal "
                        f"(min={NAV_COARSE_DONE_MIN_M}m) nav_id={self._nav_id}"
                    )
                    return
            self._goal_handle = None
            self.get_logger().info(
                f"NavigateToPose coarse phase done — fine docking nav_id={self._nav_id} {info}"
            )
            append_nav_run_event("coarse_done", result=status_name, **info)
            self._start_fine_docking(generation)
            return
        if status_name != "succeeded":
            if _cmd_bridge is not None:
                _cmd_bridge.pause()
            self._cancel_controller_goals()
            if _path_bridge is not None:
                _path_bridge.clear()
            self._fine_docking = False
        with _goal_lock:
            _goal_state.update(
                {
                    "status": "idle" if status_name in ("succeeded", "canceled") else status_name,
                    "result": status_name,
                    "updated_at": time.time(),
                }
            )
        write_goal_status()
        self._goal_handle = None
        self.get_logger().info(
            f"NavigateToPose finished: {status_name} nav_id={self._nav_id} {info}"
        )
        append_nav_run_event("finished", result=status_name, **info)

class GoalHttpHandler(BaseHTTPRequestHandler):
    def log_message(self, format: str, *args: Any) -> None:  # noqa: A003
        return

    def _json(self, code: int, obj: Any) -> None:
        body = json.dumps(obj).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _read(self) -> dict[str, Any]:
        length = int(self.headers.get("Content-Length", "0") or 0)
        if length <= 0:
            return {}
        try:
            data = json.loads(self.rfile.read(length).decode("utf-8"))
            return data if isinstance(data, dict) else {}
        except json.JSONDecodeError:
            return {}

    def do_GET(self) -> None:  # noqa: N802
        path = urlparse(self.path).path
        if path in ("/status", "/goal/status"):
            with _goal_lock:
                payload = dict(_goal_state)
            self._json(200, {"success": True, **payload})
            return
        if path in ("/", "/health"):
            self._json(200, {"success": True, "service": "nav-goal"})
            return
        self.send_error(404)

    def do_POST(self) -> None:  # noqa: N802
        global _goal_node
        path = urlparse(self.path).path
        body = self._read()
        if path in ("/goto", "/goal"):
            if _goal_node is None:
                self._json(503, {"success": False, "error": "goal node not ready"})
                return
            try:
                x = float(body["x"])
                y = float(body["y"])
                yaw = float(body.get("yaw", 0.0))
            except (KeyError, TypeError, ValueError):
                self._json(400, {"success": False, "error": "need x,y[,yaw]"})
                return
            label = str(body.get("label") or body.get("id") or "")
            fine_docking = bool(body.get("fine_docking"))
            nav_id = str(body.get("nav_id") or "").strip() or None
            result = _goal_node.goto(
                x, y, yaw, label=label, fine_docking=fine_docking, nav_id=nav_id
            )
            self._json(200 if result.get("success") else 503, result)
            return
        if path in ("/cancel", "/goal/cancel"):
            if _goal_node is None:
                self._json(503, {"success": False, "error": "goal node not ready"})
                return
            self._json(200, _goal_node.cancel())
            return
        if path in ("/pause", "/goal/pause"):
            if _goal_node is None:
                self._json(503, {"success": False, "error": "goal node not ready"})
                return
            self._json(200, _goal_node.pause())
            return
        self.send_error(404)


def poll_command_file() -> None:
    global _last_cmd_seq
    while rclpy.ok():
        time.sleep(0.15)
        if _goal_node is None:
            continue
        try:
            if not os.path.isfile(COMMAND_PATH):
                continue
            with open(COMMAND_PATH, encoding="utf-8") as handle:
                raw = json.load(handle)
        except (OSError, json.JSONDecodeError, TypeError):
            continue
        if not isinstance(raw, dict):
            continue
        seq = int(raw.get("seq") or 0)
        if seq <= 0 or seq == _last_cmd_seq:
            continue
        # A command file is a mailbox, not durable mission state. Do not replay
        # a minutes-old goto merely because this bridge container restarted.
        if _last_cmd_seq == 0 and (time.time() * 1000.0 - seq) > 5000.0:
            _last_cmd_seq = seq
            continue
        _last_cmd_seq = seq
        op = str(raw.get("op") or "").strip().lower()
        err: str | None = None
        try:
            if op == "goto":
                fine = bool(raw.get("fine_docking"))
                nav_id = str(raw.get("nav_id") or "").strip() or None
                assist_raw = raw.get("drive_assist")
                if isinstance(assist_raw, dict):
                    write_json_atomic(
                        DRIVE_ASSIST_SNAPSHOT_PATH,
                        {**assist_raw, "updated_at": time.time()},
                    )
                result = _goal_node.goto(
                    float(raw["x"]),
                    float(raw["y"]),
                    float(raw.get("yaw") or 0.0),
                    label=str(raw.get("label") or raw.get("id") or ""),
                    fine_docking=fine,
                    nav_id=nav_id,
                )
                try:
                    _goal_node.get_logger().info(
                        f"command goto nav_id={result.get('nav_id') or nav_id} "
                        f"fine_docking={fine} label={raw.get('label') or raw.get('id') or ''}"
                    )
                except Exception:  # noqa: BLE001
                    pass
                if not result.get("success"):
                    err = str(result.get("error") or "goto failed")
            elif op == "cancel":
                _goal_node.cancel()
            elif op == "pause":
                _goal_node.pause()
            else:
                err = f"unknown op: {op}"
        except Exception as exc:  # noqa: BLE001
            err = str(exc)
        with _goal_lock:
            _goal_state["cmd_seq"] = seq
            _goal_state["cmd_error"] = err
            _goal_state["updated_at"] = time.time()
        write_goal_status()
        try:
            _goal_node.get_logger().info(f"command seq={seq} op={op} err={err or 'ok'}")
        except Exception:  # noqa: BLE001
            pass


def main() -> None:
    global _goal_node, _path_bridge, _cmd_bridge
    rclpy.init()
    odom = OdomRepublisher()
    cmd = CmdVelBridge()
    goal = GoalNode()
    path = PathBridge()
    _goal_node = goal
    _path_bridge = path
    _cmd_bridge = cmd
    write_goal_status()
    path._flush()  # noqa: SLF001 — seed empty path file

    executor = MultiThreadedExecutor(num_threads=6)
    executor.add_node(odom)
    executor.add_node(cmd)
    executor.add_node(goal)
    executor.add_node(path)

    threading.Thread(target=poll_command_file, daemon=True).start()
    goal.get_logger().info(f"Polling nav commands from {COMMAND_PATH}")

    if goal.wait_server(120.0):
        goal.get_logger().info("navigate_to_pose action server connected")
    else:
        goal.get_logger().warn("navigate_to_pose not ready yet — will retry on demand")

    server = ThreadingHTTPServer((HOST, PORT), GoalHttpHandler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    goal.get_logger().info(f"Nav goal HTTP on http://{HOST}:{PORT}/")

    try:
        executor.spin()
    except ExternalShutdownException:
        pass
    finally:
        try:
            cmd.stop_motors()
        except Exception:  # noqa: BLE001
            pass
        try:
            server.shutdown()
        except Exception:  # noqa: BLE001
            pass
        for node in (odom, cmd, goal, path):
            try:
                executor.remove_node(node)
                node.destroy_node()
            except Exception:  # noqa: BLE001
                pass
        if rclpy.ok():
            try:
                rclpy.shutdown()
            except Exception:  # noqa: BLE001
                pass


if __name__ == "__main__":
    main()
