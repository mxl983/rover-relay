#!/usr/bin/env python3
"""ROS bridges for standard Nav2 ownership of the rover.

Architecture (desired):
  Cartographer (map→odom→base_link) → Nav2 planner/controller → /cmd_vel
    → CmdVelBridge (Twist→Pi analog stick) → 4 motors

This process intentionally does NOT:
  - segment Nav2 paths into WASD pulses
  - cancel Nav2 to run a custom segment controller

Before every drive epoch (nav start, after motion stops, cancel/replan recovery),
CmdVelBridge waits for SLAM pose to settle (default 3 s observe + 2 s stable).

Nodes in one rclpy context (shared executor):
  - OdomRepublisher: TF odom→base_link → /odom
  - CmdVelBridge: /cmd_vel → continuous Pi drive vectors + watchdog
  - PathBridge: mirror /plan + local_plan → JSON for the dashboard
  - GoalNode: file/HTTP NavigateToPose client + kill/pause
"""

from __future__ import annotations

import json
import math
import os
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.parse import urlparse

import rclpy
from action_msgs.msg import GoalStatus
from action_msgs.srv import CancelGoal
from geometry_msgs.msg import PoseStamped, Twist
from nav2_msgs.action import NavigateToPose
from nav_msgs.msg import Odometry, Path
from rclpy.action import ActionClient
from rclpy.duration import Duration
from rclpy.executors import ExternalShutdownException, MultiThreadedExecutor
from rclpy.node import Node
from rclpy.qos import DurabilityPolicy, HistoryPolicy, QoSProfile, ReliabilityPolicy, qos_profile_sensor_data
from rclpy.time import Time
from tf2_ros import Buffer, TransformException, TransformListener

from drive_interface import (
    DriveLimits,
    clamp_twist,
    limit_accel,
    limit_arc_twist,
    twist_to_pi_drive,
    wrap_angle,
)
from nav_context import (
    drive_assist_blocking,
    nav_context,
)
from pi_drive_client import PiDriveClient
from path_progress import remaining_path_distance
from xy_gap_close import (
    XyGapCloseConfig,
    XyGapCloseState,
    XyImuAssist,
    dist_xy as xy_dist,
    tick_xy_gap_close,
)
from yaw_pulse_align import (
    YawImuAssist,
    YawPulseAlignConfig,
    YawPulseAlignState,
    goal_yaw_error,
    should_begin_yaw_align,
    should_handoff_xy,
    tick_yaw_pulse_align,
)
from nav_imu import NAV_USE_IMU, NavImuAssist

# --- env ---
CMD_VEL_TOPIC = os.environ.get("NAV_CMD_VEL_TOPIC", "/cmd_vel")
NAV_DRIVE_TRANSPORT = os.environ.get("NAV_DRIVE_TRANSPORT", "ws").lower().strip()
NAV_DRIVE_WS_URL = os.environ.get(
    "NAV_DRIVE_WS_URL", "wss://rover.tail9d0237.ts.net:3000"
).rstrip("/")
DRIVE_URL = os.environ.get(
    "NAV_DRIVE_URL",
    os.environ.get("NAV_DRIVE_BASE_URL", "https://127.0.0.1:8787")
    + "/api/navigation/drive",
).rstrip("/")
if "/api/" not in DRIVE_URL:
    DRIVE_URL = DRIVE_URL.rstrip("/") + "/api/navigation/drive"
# Prefer continuous analog endpoint (not /keys).
if DRIVE_URL.endswith("/keys"):
    DRIVE_URL = DRIVE_URL[: -len("/keys")]

NAV_API_TOKEN = os.environ.get("NAVIGATION_API_TOKEN", "")
SSL_VERIFY = os.environ.get("NAV_SSL_VERIFY", "false").lower() not in {"0", "false", "no"}

MAX_LINEAR_MPS = float(os.environ.get("NAV_MAX_LINEAR_MPS", "0.35"))
MAX_ANGULAR_RPS = float(os.environ.get("NAV_MAX_ANGULAR_RPS", "0.80"))
# Legacy compose pins were tuned for WASD pulses; lift floors for continuous Twist.
if MAX_LINEAR_MPS < 0.28:
    MAX_LINEAR_MPS = 0.30
if MAX_ANGULAR_RPS < 0.70:
    MAX_ANGULAR_RPS = 0.80
DRIVE_INVERT_ANGULAR = os.environ.get("NAV_DRIVE_INVERT_ANGULAR", "false").lower() in {
    "1",
    "true",
    "yes",
}
KEEPALIVE_HZ = float(os.environ.get("NAV_DRIVE_KEEPALIVE_HZ", "20"))
# Was 0.35s — Nav2 RPP gaps during rotate-to-heading caused jerk-stop-jerk.
STALE_STOP_SEC = float(os.environ.get("NAV_CMD_VEL_STALE_SEC", "0.90"))
# Hold last non-zero stick this long after cmd_vel goes quiet (covers RPP pauses).
CMD_HOLD_SEC = float(os.environ.get("NAV_CMD_HOLD_SEC", "0.55"))
MAX_LINEAR_ACCEL = float(os.environ.get("NAV_MAX_LINEAR_ACCEL", "0.50"))
MAX_ANGULAR_ACCEL = float(os.environ.get("NAV_MAX_ANGULAR_ACCEL", "2.50"))
TF_STALE_SEC = float(os.environ.get("NAV_TF_STALE_SEC", "1.0"))
# Initial Nav2 rotate-to-heading is also pulsed so SLAM can settle between
# heading corrections. Final goal yaw uses the slower error-sized pulses.
PURE_ROTATE_PULSE_ON_SEC = float(
    os.environ.get("NAV_PURE_ROTATE_PULSE_ON_SEC", "0.18")
)
PURE_ROTATE_PULSE_OFF_SEC = float(
    os.environ.get("NAV_PURE_ROTATE_PULSE_OFF_SEC", "0.55")
)
# Use slow, discrete final correction pulses only inside the final handoff zone.
# Nav2 still owns the normal trajectory and all motion outside that zone.
CUSTOM_FINAL_APPROACH = os.environ.get(
    "NAV_CUSTOM_FINAL_APPROACH", "true"
).lower() in {"1", "true", "yes"}
LOCALIZATION_FAIL_STOP = os.environ.get("NAV_LOCALIZATION_FAIL_STOP", "true").lower() in {
    "1",
    "true",
    "yes",
}

STATUS_PATH = os.environ.get("NAV_STATUS_FILE_PATH", "/app/lidar/navigation_status.json")
GOAL_STATUS_PATH = os.environ.get(
    "NAV_GOAL_STATUS_PATH",
    os.environ.get("NAV_GOAL_STATUS_FILE", "/app/lidar/navigation_goal.json"),
)
COMMAND_PATH = os.environ.get("NAV_COMMAND_PATH", "/app/lidar/navigation_command.json")
KILL_PATH = os.environ.get("NAV_KILL_PATH", "/app/lidar/navigation_kill.json")
PATH_FILE = os.environ.get("NAV_PATH_FILE_PATH", "/app/lidar/navigation_path.json")
RUN_LOG_PATH = os.environ.get("NAV_RUN_LOG_PATH", "/app/lidar/nav_runs.jsonl")
HOST = os.environ.get("NAV_GOAL_HOST", "0.0.0.0")
PORT = int(os.environ.get("NAV_GOAL_PORT", "8768"))
MAP_FRAME = os.environ.get("SLAM_MAP_FRAME", "map")
ODOM_FRAME = os.environ.get("NAV_ODOM_FRAME", "odom")
BASE_FRAME = os.environ.get("NAV_BASE_FRAME", "base_link")
ODOM_TOPIC = os.environ.get("NAV_ODOM_TOPIC", "/odom")
ODOM_HZ = float(os.environ.get("NAV_ODOM_HZ", "20"))
NAV_PROGRESS_PERIOD_SEC = float(os.environ.get("NAV_PROGRESS_PERIOD_SEC", "2.0"))
NAV_STALL_WARN_SEC = float(os.environ.get("NAV_STALL_WARN_SEC", "12.0"))
NAV_STALL_EVENT_SEC = float(os.environ.get("NAV_STALL_EVENT_SEC", "45.0"))
# Hard abort only after a long period without path progress. This is a safety
# backstop, not a route timeout; valid obstacle detours can take several
# minutes before their path distance reaches a new minimum.
NAV_STALL_ABORT_SEC = float(os.environ.get("NAV_STALL_ABORT_SEC", "180.0"))
NAV_CONTEXT_REFRESH_SEC = float(os.environ.get("NAV_CONTEXT_REFRESH_SEC", "10.0"))
NAV_ASSIST_REFRESH_SEC = float(os.environ.get("NAV_ASSIST_REFRESH_SEC", "2.0"))
# Hold still before planning/driving so Cartographer pose + scan_match stabilize.
# Legacy containers set NAV_*_SETTLE_SEC=0 — treat 0 as "use default" unless explicitly disabled.
def _settle_seconds(name: str, default: float = 3.0) -> float:
    allow_disable = os.environ.get("NAV_SETTLE_ALLOW_DISABLE", "").lower() in (
        "1",
        "true",
        "yes",
    )
    raw = os.environ.get(name)
    if raw is None:
        return default
    val = float(raw)
    if val <= 0.0 and not allow_disable:
        return default
    return val


NAV_START_SETTLE_SEC = _settle_seconds("NAV_START_SETTLE_SEC", 3.0)
NAV_START_SETTLE_MAX_SEC = float(os.environ.get("NAV_START_SETTLE_MAX_SEC", "12.0"))
NAV_MOTION_SETTLE_SEC = _settle_seconds(
    "NAV_MOTION_SETTLE_SEC", NAV_START_SETTLE_SEC
)
NAV_POSE_STABLE_SEC = float(os.environ.get("NAV_POSE_STABLE_SEC", "2.0"))
NAV_POSE_STABLE_XY_M = float(os.environ.get("NAV_POSE_STABLE_XY_M", "0.05"))
NAV_POSE_STABLE_YAW_RAD = float(os.environ.get("NAV_POSE_STABLE_YAW_RAD", "0.08"))
NAV_MIN_SCAN_MATCH = float(os.environ.get("NAV_MIN_SCAN_MATCH", "0.28"))
NAV_CANCEL_SETTLE_SEC = _settle_seconds("NAV_CANCEL_SETTLE_SEC", 3.0)
NAV_MOTION_IDLE_SEC = float(os.environ.get("NAV_MOTION_IDLE_SEC", "0.35"))
NAV_FALSE_SUCCESS_XY_M = float(os.environ.get("NAV_FALSE_SUCCESS_XY_M", "0.12"))
SLAM_MAP_PATH = os.environ.get("SLAM_MAP_FILE_PATH", "/app/lidar/slam.json")

YAW_ALIGN_CFG = YawPulseAlignConfig(
    xy_handoff_m=float(os.environ.get("NAV_YAW_HANDOFF_XY_M", "0.14")),
    xy_handoff_large_yaw_m=float(os.environ.get("NAV_YAW_HANDOFF_LARGE_XY_M", "0.40")),
    large_handoff_err_rad=float(
        os.environ.get("NAV_YAW_HANDOFF_LARGE_ERR_DEG", "40.0")
    )
    * math.pi
    / 180.0,
    yaw_tol_rad=float(os.environ.get("NAV_YAW_ALIGN_TOL_RAD", str(math.radians(10.0)))),
    settle_s=_settle_seconds("NAV_YAW_ALIGN_SETTLE_SEC", 3.0),
    invert_angular=DRIVE_INVERT_ANGULAR,
)

XY_GAP_CFG = XyGapCloseConfig(
    xy_tol_m=float(os.environ.get("NAV_XY_GAP_TOL_M", "0.08")),
    settle_s=_settle_seconds("NAV_XY_GAP_SETTLE_SEC", 3.0),
    forward_stick=float(os.environ.get("NAV_XY_GAP_FWD_STICK", "0.50")),
    face_stick=float(os.environ.get("NAV_XY_GAP_FACE_STICK", "0.36")),
    final_forward_stick=float(os.environ.get("NAV_XY_GAP_FINAL_STICK", "0.50")),
    final_forward_pulse_s=float(os.environ.get("NAV_XY_GAP_FINAL_PULSE_SEC", "0.24")),
    invert_angular=DRIVE_INVERT_ANGULAR,
)
# After XY is committed, ignore rotation skid unless pose jumps this far (kidnap).
XY_KIDNAP_M = float(os.environ.get("NAV_XY_KIDNAP_M", "0.50"))
# If fine XY/yaw leaves us farther than this, hand control back to Nav2 for a new path.
XY_REPLAN_M = float(os.environ.get("NAV_XY_REPLAN_M", "0.35"))

DRIVE_LIMITS = DriveLimits(
    max_linear_mps=MAX_LINEAR_MPS,
    max_angular_rps=MAX_ANGULAR_RPS,
    invert_angular=DRIVE_INVERT_ANGULAR,
)

_goal_lock = threading.Lock()
_goal_state: dict[str, Any] = {
    "status": "idle",
    "goal": None,
    "result": None,
    "feedback": None,
    "updated_at": 0.0,
    "cmd_seq": 0,
    "cmd_error": None,
    "nav_id": "",
}
_last_cmd_seq = 0
_process_started_ms = int(time.time() * 1000)
_goal_node: "GoalNode | None" = None
_cmd_bridge: "CmdVelBridge | None" = None
_path_bridge: "PathBridge | None" = None


def yaw_to_quat(yaw: float) -> tuple[float, float, float, float]:
    half = yaw * 0.5
    return (0.0, 0.0, math.sin(half), math.cos(half))


def yaw_from_quat(x: float, y: float, z: float, w: float) -> float:
    return math.atan2(2.0 * (w * z + x * y), 1.0 - 2.0 * (y * y + z * z))


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


def _read_slam_snapshot() -> dict[str, Any] | None:
    try:
        with open(SLAM_MAP_PATH, encoding="utf-8") as handle:
            raw = json.load(handle)
        return raw if isinstance(raw, dict) else None
    except (OSError, json.JSONDecodeError, TypeError):
        return None


def lookup_map_pose(buffer: Buffer, node: Node) -> tuple[float, float, float] | None:
    try:
        tf = buffer.lookup_transform(
            MAP_FRAME, BASE_FRAME, Time(), timeout=Duration(seconds=0.05)
        )
    except TransformException:
        return None
    t = tf.transform.translation
    q = tf.transform.rotation
    return (float(t.x), float(t.y), yaw_from_quat(q.x, q.y, q.z, q.w))


def path_xy_length(path_xy: list[list[float]]) -> float:
    total = 0.0
    for i in range(1, len(path_xy)):
        total += math.hypot(path_xy[i][0] - path_xy[i - 1][0], path_xy[i][1] - path_xy[i - 1][1])
    return total


# ---------------------------------------------------------------------------
# Odom
# ---------------------------------------------------------------------------


class OdomRepublisher(Node):
    """Publish nav_msgs/Odometry from TF odom→base_link (Cartographer TF)."""

    def __init__(self) -> None:
        super().__init__("rover_odom_republisher")
        self._buf = Buffer()
        self._listener = TransformListener(self._buf, self)
        self._pub = self.create_publisher(Odometry, ODOM_TOPIC, 10)
        self._prev = None
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
            dyaw = wrap_angle(yaw - pyaw)
            c, s = math.cos(yaw), math.sin(yaw)
            msg.twist.twist.linear.x = (c * dx + s * dy) / dt
            msg.twist.twist.linear.y = (-s * dx + c * dy) / dt
            msg.twist.twist.angular.z = dyaw / dt

        self._prev = tf
        self._prev_t = now
        self._pub.publish(msg)


# ---------------------------------------------------------------------------
# Path mirror (dashboard)
# ---------------------------------------------------------------------------


class PathBridge(Node):
    def __init__(self) -> None:
        super().__init__("rover_path_bridge")
        self._lock = threading.Lock()
        self._global: list[list[float]] = []
        self._local: list[list[float]] = []
        self._updated_at = 0.0
        plan_qos = QoSProfile(
            reliability=ReliabilityPolicy.RELIABLE,
            durability=DurabilityPolicy.VOLATILE,
            history=HistoryPolicy.KEEP_LAST,
            depth=1,
        )
        local_qos = qos_profile_sensor_data
        self.create_subscription(Path, "/plan", self._on_global, plan_qos)
        self.create_subscription(
            Path, "/controller_server/local_plan", self._on_local, local_qos
        )
        self.create_timer(0.5, self._flush)
        self.get_logger().info(f"path mirror → {PATH_FILE}")

    def _path_to_xy(self, msg: Path) -> list[list[float]]:
        out: list[list[float]] = []
        for pose in msg.poses:
            out.append([float(pose.pose.position.x), float(pose.pose.position.y)])
        return out

    def _on_global(self, msg: Path) -> None:
        with self._lock:
            self._global = self._path_to_xy(msg)
            self._updated_at = time.time()

    def _on_local(self, msg: Path) -> None:
        with self._lock:
            self._local = self._path_to_xy(msg)
            self._updated_at = time.time()

    def clear(self) -> None:
        with self._lock:
            self._global = []
            self._local = []
            self._updated_at = time.time()
        self._flush()

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            global_xy = list(self._global)
            local_xy = list(self._local)
            updated = self._updated_at
        return {
            "global": global_xy,
            "local": local_xy,
            "path_length_m": round(path_xy_length(global_xy), 3) if global_xy else 0.0,
            "updated_at": updated,
        }

    def _flush(self) -> None:
        write_json_atomic(PATH_FILE, self.snapshot())


# ---------------------------------------------------------------------------
# Continuous /cmd_vel → Pi analog drive
# ---------------------------------------------------------------------------


class CmdVelBridge(Node):
    """Sole consumer of Nav2 /cmd_vel for autonomous motion.

    Ownership model:
      navigating → Nav2 controller publishes /cmd_vel → this bridge → Pi
      paused/kill/idle → this bridge posts zero drive (watchdog)
    """

    def __init__(self) -> None:
        super().__init__("rover_cmd_vel_bridge")
        self._lock = threading.Lock()
        self._latest: Twist | None = None
        self._latest_at = 0.0
        self._last_sent = {"x": 0.0, "y": 0.0}
        self._prev_vx = 0.0
        self._prev_wz = 0.0
        self._prev_tick = time.monotonic()
        self._paused = False
        self._phase = "idle"
        self._cmd_vx = 0.0
        self._cmd_wz = 0.0
        self._post_failures = 0
        self._held_drive = {"x": 0.0, "y": 0.0}
        self._held_until = 0.0
        self._pure_rotate_started_at = 0.0
        self._pure_rotate_sign = 0
        self._drive_client = PiDriveClient(
            transport=NAV_DRIVE_TRANSPORT,
            ws_url=NAV_DRIVE_WS_URL,
            http_url=DRIVE_URL,
            token=NAV_API_TOKEN,
            ssl_verify=SSL_VERIFY,
            timeout=1.0,
        )
        self.create_subscription(Twist, CMD_VEL_TOPIC, self._on_cmd, qos_profile_sensor_data)
        self.create_timer(1.0 / max(KEEPALIVE_HZ, 1.0), self._tick)
        self.get_logger().info(
            f"cmd_vel bridge topic={CMD_VEL_TOPIC} transport={NAV_DRIVE_TRANSPORT} "
            f"ws={NAV_DRIVE_WS_URL} http={DRIVE_URL} "
            f"mode=continuous_analog max_v={MAX_LINEAR_MPS} max_w={MAX_ANGULAR_RPS} "
            f"stale_stop={STALE_STOP_SEC}s cmd_hold={CMD_HOLD_SEC}s "
            f"keepalive={KEEPALIVE_HZ}Hz start_settle={NAV_START_SETTLE_SEC}s "
            f"motion_settle={NAV_MOTION_SETTLE_SEC}s motion_idle={NAV_MOTION_IDLE_SEC}s"
            f" pure_rotate_pulse={PURE_ROTATE_PULSE_ON_SEC}/{PURE_ROTATE_PULSE_OFF_SEC}s"
        )

    def _on_cmd(self, msg: Twist) -> None:
        with self._lock:
            self._latest = msg
            self._latest_at = time.monotonic()

    def pause(self) -> None:
        with self._lock:
            self._paused = True
            self._latest = None
        self._post_drive({"x": 0.0, "y": 0.0})
        self._last_sent = {"x": 0.0, "y": 0.0}
        self._reset_pure_rotate_pulse()
        self._phase = "paused"

    def resume(self) -> None:
        with self._lock:
            self._paused = False

    def stop_motors(self) -> None:
        # Explicit neutral frames also clear the Pi's persistent drive command.
        for _ in range(4):
            self._post_drive({"x": 0.0, "y": 0.0})
            time.sleep(0.05)
        self._last_sent = {"x": 0.0, "y": 0.0}
        self._prev_vx = 0.0
        self._prev_wz = 0.0
        self._held_drive = {"x": 0.0, "y": 0.0}
        self._held_until = 0.0
        self._reset_pure_rotate_pulse()
        self._phase = "idle"

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            age = time.monotonic() - self._latest_at if self._latest_at else None
        return {
            "phase": self._phase,
            "drive": dict(self._last_sent),
            "cmd_vx": round(self._cmd_vx, 3),
            "cmd_wz": round(self._cmd_wz, 3),
            "cmd_age_s": None if age is None else round(age, 3),
            "control": "nav2_continuous_cmd_vel",
            "post_failures": self._post_failures,
            "nav_ui": self._nav_ui(),
        }

    def _nav_ui(self) -> dict[str, Any]:
        with _goal_lock:
            status = str(_goal_state.get("status") or "")
        if status == "navigating":
            return {"phase": 1, "label": "Nav2 · Approach"}
        if status == "yaw_align":
            return {"phase": 3, "label": "Yaw · Pulse align"}
        if status == "xy_close":
            return {"phase": 2, "label": "Position · Gap close"}
        if status == "docking":
            return {"phase": 3, "label": "Dock (map pose)"}
        return {"phase": None, "label": None}

    def _tick(self) -> None:
        now = time.monotonic()
        dt = now - self._prev_tick
        self._prev_tick = now
        killed = os.path.isfile(KILL_PATH)
        with self._lock:
            paused = self._paused or killed
            msg = self._latest
            age = now - self._latest_at

        if paused:
            self._phase = "paused" if self._paused else "killed"
            drive = {"x": 0.0, "y": 0.0}
            self._prev_vx = 0.0
            self._prev_wz = 0.0
            self._cmd_vx = 0.0
            self._cmd_wz = 0.0
            self._held_drive = {"x": 0.0, "y": 0.0}
            self._held_until = 0.0
        elif (
            not paused
            and _goal_node is not None
            and _goal_node.fine_approach_active()
        ):
            drive, done = _goal_node.tick_fine_approach(now)
            self._phase = _goal_node.fine_approach_phase()
            self._cmd_vx = 0.0
            self._cmd_wz = 0.0
            self._prev_vx = 0.0
            self._prev_wz = 0.0
            self._held_drive = {"x": 0.0, "y": 0.0}
            self._held_until = 0.0
            if drive != self._last_sent or (drive["x"] or drive["y"]):
                ok = self._post_drive(drive)
                if ok:
                    self._last_sent = drive
                    if abs(drive["x"]) > 1e-3 or abs(drive["y"]) > 1e-3:
                        _goal_node.note_drive_sent(now)
            self._write_status(drive, age)
            if done:
                _goal_node.finish_fine_approach()
            return
        elif msg is None or age > STALE_STOP_SEC:
            # Hard stop after true stale. Clear hold so we do not coast forever.
            self._phase = "watchdog_stop"
            drive = {"x": 0.0, "y": 0.0}
            self._prev_vx = 0.0
            self._prev_wz = 0.0
            self._cmd_vx = 0.0
            self._cmd_wz = 0.0
            self._held_drive = {"x": 0.0, "y": 0.0}
            self._held_until = 0.0
            if msg is not None and age > STALE_STOP_SEC:
                self.get_logger().warning(
                    f"cmd_vel stale ({age:.2f}s > {STALE_STOP_SEC}s) — stop",
                    throttle_duration_sec=2.0,
                )
        else:
            raw_vx = float(msg.linear.x)
            raw_wz = float(msg.angular.z)
            if abs(float(msg.linear.y)) > 1e-3:
                self.get_logger().warning(
                    "ignoring linear.y (skid-steer cannot strafe)",
                    throttle_duration_sec=5.0,
                )
            vx, wz = clamp_twist(raw_vx, raw_wz, DRIVE_LIMITS)
            vx, wz = limit_arc_twist(vx, wz, DRIVE_LIMITS)
            # Zero Twist (Nav2 quiet tick) → hold last stick briefly, don't jerk-stop.
            if abs(vx) < 1e-6 and abs(wz) < 1e-6:
                # Hold forward stick through brief Nav2 gaps only — never hold yaw.
                if (
                    now < self._held_until
                    and abs(self._held_drive.get("y", 0.0)) > 1e-3
                ):
                    self._phase = "cmd_hold"
                    drive = {"x": 0.0, "y": self._held_drive["y"]}
                    self._cmd_vx = self._prev_vx
                    self._cmd_wz = 0.0
                else:
                    self._phase = "idle"
                    drive = {"x": 0.0, "y": 0.0}
                    self._prev_vx = 0.0
                    self._prev_wz = 0.0
                    self._cmd_vx = 0.0
                    self._cmd_wz = 0.0
                    self._held_drive = {"x": 0.0, "y": 0.0}
                    self._held_until = 0.0
                    if _goal_node is not None:
                        _goal_node.note_cmd_idle(now)
            else:
                pure_rotate = abs(vx) < DRIVE_LIMITS.min_linear_mps and abs(wz) > 0.0
                if _goal_node is not None and _goal_node.nav_active():
                    if (
                        not _goal_node.fine_approach_active()
                        and not _goal_node.skip_motion_settle()
                        and _goal_node.motion_settle_required()
                        # Never gate pure yaw — RPP unlocks vx only after heading
                        # converges; zeroing wz here created infinite rotate-to-heading.
                        and not pure_rotate
                    ):
                        if not _goal_node.ensure_motion_settled():
                            self._phase = "motion_settle"
                            drive = {"x": 0.0, "y": 0.0}
                            self._cmd_vx = 0.0
                            self._cmd_wz = 0.0
                            if drive != self._last_sent or (drive["x"] or drive["y"]):
                                ok = self._post_drive(drive)
                                if ok:
                                    self._last_sent = drive
                            self._write_status(drive, age)
                            return
                vx, wz = limit_accel(
                    self._prev_vx,
                    self._prev_wz,
                    vx,
                    wz,
                    dt=dt,
                    max_linear_accel=MAX_LINEAR_ACCEL,
                    max_angular_accel=MAX_ANGULAR_ACCEL,
                    bypass_angular=pure_rotate,
                )
                self._prev_vx, self._prev_wz = vx, wz
                self._cmd_vx, self._cmd_wz = vx, wz
                drive = twist_to_pi_drive(
                    vx, wz, limits=DRIVE_LIMITS, allow_reverse=False
                )
                if pure_rotate and abs(wz) >= 0.10:
                    drive = self._pulse_pure_rotate(drive, wz, now)
                else:
                    self._reset_pure_rotate_pulse()
                moving = abs(drive["x"]) > 1e-3 or abs(drive["y"]) > 1e-3
                self._phase = "driving" if moving else "idle"
                if abs(drive["y"]) > 1e-3:
                    self._held_drive = {"x": 0.0, "y": drive["y"]}
                    self._held_until = now + CMD_HOLD_SEC
                else:
                    self._held_drive = {"x": 0.0, "y": 0.0}
                    self._held_until = 0.0

        if drive != self._last_sent or (drive["x"] or drive["y"]):
            ok = self._post_drive(drive)
            if ok:
                self._last_sent = drive
                if abs(drive["x"]) > 1e-3 or abs(drive["y"]) > 1e-3:
                    if _goal_node is not None:
                        # Forward stick only — pure yaw must not arm SLAM settle
                        # (Nav2 recovery spins were settling forever).
                        _goal_node.note_drive_sent(
                            now, translated=abs(drive["y"]) > 1e-3
                        )
        self._write_status(drive, None if msg is None else age)

    def _post_drive(self, drive: dict[str, float]) -> bool:
        try:
            self._drive_client.send(drive)
            self._post_failures = 0
            return True
        except Exception as err:
            self._post_failures += 1
            self.get_logger().warning(
                f"drive {NAV_DRIVE_TRANSPORT} send failed ({self._post_failures}): {err}",
                throttle_duration_sec=2.0,
            )
            if self._post_failures >= 5:
                self.get_logger().error(
                    "motor communication lost — holding stop",
                    throttle_duration_sec=5.0,
                )
            return False

    def _reset_pure_rotate_pulse(self) -> None:
        self._pure_rotate_started_at = 0.0
        self._pure_rotate_sign = 0

    def _pulse_pure_rotate(
        self, drive: dict[str, float], wz: float, now: float
    ) -> dict[str, float]:
        """Pulse initial Nav2 heading turns and settle between pulses."""
        sign = 1 if wz > 0.0 else -1
        if sign != self._pure_rotate_sign or self._pure_rotate_started_at <= 0.0:
            self._pure_rotate_started_at = now
            self._pure_rotate_sign = sign
        cycle = max(PURE_ROTATE_PULSE_ON_SEC + PURE_ROTATE_PULSE_OFF_SEC, 0.05)
        phase = (now - self._pure_rotate_started_at) % cycle
        if phase >= max(PURE_ROTATE_PULSE_ON_SEC, 0.0):
            return {"x": 0.0, "y": 0.0}
        return drive

    def _write_status(self, drive: dict[str, float], age: float | None) -> None:
        status = {
            "enabled": True,
            "phase": self._phase,
            "drive": drive,
            "cmd_vx": round(self._cmd_vx, 3),
            "cmd_wz": round(self._cmd_wz, 3),
            "cmd_age_s": None if age is None else round(age, 3),
            "control": "nav2_continuous_cmd_vel",
            "nav_ui": self._nav_ui(),
            "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        }
        write_json_atomic(STATUS_PATH, status)


# ---------------------------------------------------------------------------
# Goal / NavigateToPose
# ---------------------------------------------------------------------------


class GoalNode(Node):
    def __init__(self) -> None:
        super().__init__("rover_nav_goal_server")
        self._client = ActionClient(self, NavigateToPose, "navigate_to_pose")
        self._follow_cancel_client = self.create_client(
            CancelGoal, "/follow_path/_action/cancel_goal"
        )
        self._tf_buffer = Buffer()
        self._tf_listener = TransformListener(self._tf_buffer, self)
        self._goal_handle = None
        self._send_lock = threading.Lock()
        self._generation = 0
        self._nav_id = ""
        self._nav_started_at = 0.0
        self._target: tuple[float, float, float] | None = None
        self._best_distance: float | None = None
        self._progress_metric = "straight"
        self._last_improve_at = 0.0
        self._stall_warned = False
        self._stall_evented = False
        self._stall_aborting = False
        self._last_progress_log_at = 0.0
        self._last_context_refresh_at = 0.0
        self._last_assist_refresh_at = 0.0
        self._last_tf_ok_at = time.time()
        self._motion_settle_required = False
        self._motion_settle_lock = threading.Lock()
        self._motion_settle_active = False
        self._last_drive_at = 0.0
        self._last_drive_translated = False
        self._yaw_align = YawPulseAlignState()
        self._xy_close = XyGapCloseState()
        self._yaw_handoff_zone = False
        self._fine_best_xy: float | None = None
        self._xy_committed = False
        self._xy_retry_used = False
        self._replan_count = 0
        self._replan_inflight = False
        self._nav_imu = NavImuAssist()
        self.create_timer(max(0.5, NAV_PROGRESS_PERIOD_SEC), self._progress_tick)
        self.get_logger().info(
            f"Nav2 NavigateToPose client ready (continuous /cmd_vel ownership) "
            f"run_log={RUN_LOG_PATH} start_settle={NAV_START_SETTLE_SEC}s "
            f"motion_settle={NAV_MOTION_SETTLE_SEC}s "
            f"custom_final_approach={'on' if CUSTOM_FINAL_APPROACH else 'off'} "
            f"yaw_handoff={YAW_ALIGN_CFG.xy_handoff_m}m "
            f"large_yaw={YAW_ALIGN_CFG.xy_handoff_large_yaw_m}m "
            f"xy_tol={XY_GAP_CFG.xy_tol_m}m "
            f"nav_imu={'on' if NAV_USE_IMU else 'off'} (soft assist; SLAM lidar-only)"
        )

    def wait_server(self, timeout_sec: float = 120.0) -> bool:
        return self._client.wait_for_server(timeout_sec=timeout_sec)

    def _lookup_map_pose(self) -> tuple[float, float, float] | None:
        return lookup_map_pose(self._tf_buffer, self)

    def _reset_progress(self) -> None:
        self._best_distance = None
        self._progress_metric = "straight"
        self._last_improve_at = time.time()
        self._stall_warned = False
        self._stall_evented = False
        self._stall_aborting = False
        self._last_progress_log_at = 0.0

    def _nav_status(self) -> str:
        with _goal_lock:
            return str(_goal_state.get("status") or "")

    def nav_active(self) -> bool:
        return self._nav_status() in ("navigating", "xy_close", "yaw_align")

    def fine_approach_active(self) -> bool:
        return self._xy_close.active or self._yaw_align.active

    def yaw_align_active(self) -> bool:
        return self._yaw_align.active

    def fine_approach_phase(self) -> str:
        if self._xy_close.active:
            return "xy_close"
        if self._yaw_align.active:
            return "yaw_align"
        return "idle"

    def skip_motion_settle(self) -> bool:
        return self.fine_approach_active() or self._yaw_handoff_zone

    def note_cmd_idle(self, now: float) -> None:
        if (
            not self.nav_active()
            or self.skip_motion_settle()
            or self._motion_settle_required
            or self._motion_settle_active
        ):
            return
        if self._last_drive_at <= 0.0:
            return
        # Pure yaw / recovery spins must not trigger SLAM settle — that created
        # an infinite idle→settle→spin loop (nav-20260826-065940).
        if not self._last_drive_translated:
            return
        idle_s = now - self._last_drive_at
        if idle_s >= NAV_MOTION_IDLE_SEC:
            self._motion_settle_required = True
            self._last_drive_translated = False
            self.get_logger().info(
                f"motion idle {idle_s:.2f}s nav_id={self._nav_id} — "
                f"SLAM settle required before next drive"
            )
            append_nav_run_event(
                "motion_idle",
                nav_id=self._nav_id,
                idle_s=round(idle_s, 2),
            )

    def note_drive_sent(self, now: float, *, translated: bool = False) -> None:
        self._last_drive_at = now
        if translated:
            self._last_drive_translated = True

    def _reset_motion_settle(self) -> None:
        self._motion_settle_required = False
        self._motion_settle_active = False
        self._last_drive_at = 0.0
        self._last_drive_translated = False

    def _reset_fine_approach(self) -> None:
        self._yaw_align = YawPulseAlignState()
        self._xy_close = XyGapCloseState()
        self._yaw_handoff_zone = False
        self._fine_best_xy = None
        self._xy_committed = False
        self._xy_retry_used = False
        # Keep _replan_count across fine-approach resets within one goto.

    def _reset_yaw_align(self) -> None:
        self._reset_fine_approach()

    def _start_yaw_align_phase(self, yaw_err: float, dist_xy: float) -> None:
        # XY phase is done — yaw-only from here; ignore small rotation skid.
        self._xy_committed = True
        if self._fine_best_xy is None or dist_xy < self._fine_best_xy:
            self._fine_best_xy = dist_xy
        self._yaw_align = YawPulseAlignState(
            active=True,
            phase="settle",
            until=time.monotonic() + YAW_ALIGN_CFG.settle_s,
        )
        with _goal_lock:
            _goal_state["status"] = "yaw_align"
            fb = dict(_goal_state.get("feedback") or {})
            fb["yaw_align"] = True
            fb["xy_close"] = False
            fb["xy_committed"] = True
            fb["goal_yaw_err_deg"] = round(math.degrees(yaw_err), 1)
            fb["position_error_m"] = round(dist_xy, 3)
            _goal_state["feedback"] = fb
        write_goal_status()
        self.get_logger().info(
            f"yaw pulse align nav_id={self._nav_id} dist={dist_xy:.3f}m "
            f"err={math.degrees(yaw_err):+.1f}° (xy committed — ignore skid <{XY_KIDNAP_M:.2f}m)"
        )

    def _begin_fine_approach(self, yaw_err: float, dist_xy: float) -> None:
        if self.fine_approach_active():
            return
        self._generation += 1
        self._cancel_handle_only()
        self._cancel_controller_goals()
        self._reset_motion_settle()

        if dist_xy > XY_GAP_CFG.xy_tol_m:
            self._xy_close = XyGapCloseState(
                active=True,
                phase="settle",
                until=time.monotonic() + XY_GAP_CFG.settle_s,
                settle_s=XY_GAP_CFG.settle_s,
            )
            with _goal_lock:
                _goal_state["status"] = "xy_close"
                fb = dict(_goal_state.get("feedback") or {})
                fb["xy_close"] = True
                fb["yaw_align"] = False
                fb["position_error_m"] = round(dist_xy, 3)
                fb["goal_yaw_err_deg"] = round(math.degrees(yaw_err), 1)
                _goal_state["feedback"] = fb
            write_goal_status()
            self.get_logger().info(
                f"xy gap-close handoff nav_id={self._nav_id} dist={dist_xy:.3f}m "
                f"yaw_err={math.degrees(yaw_err):+.1f}° → close XY then align yaw"
            )
            append_nav_run_event(
                "xy_handoff",
                nav_id=self._nav_id,
                dist_xy=round(dist_xy, 3),
                yaw_err_deg=round(math.degrees(yaw_err), 1),
            )
            return

        self._start_yaw_align_phase(yaw_err, dist_xy)
        append_nav_run_event(
            "yaw_handoff",
            nav_id=self._nav_id,
            dist_xy=round(dist_xy, 3),
            yaw_err_deg=round(math.degrees(yaw_err), 1),
        )

    def _begin_yaw_align(self, yaw_err: float, dist_xy: float) -> None:
        self._begin_fine_approach(yaw_err, dist_xy)

    def _imu_hints(self) -> tuple[XyImuAssist, YawImuAssist]:
        """Poll soft IMU; always returns structs (ok=False = no effect)."""
        hint = self._nav_imu.poll()
        yaw_integ = self._nav_imu.integrated_yaw_rad
        xy = XyImuAssist(
            ok=hint.ok,
            gy=hint.gy,
            gz=hint.gz,
            integrated_yaw_rad=yaw_integ,
        )
        yaw = YawImuAssist(
            ok=hint.ok,
            gz=hint.gz,
            integrated_yaw_rad=yaw_integ,
        )
        return xy, yaw

    def tick_fine_approach(self, now: float) -> tuple[dict[str, float], bool]:
        if self._target is None:
            return {"x": 0.0, "y": 0.0}, False
        pose = self._lookup_map_pose()
        if pose is None:
            return {"x": 0.0, "y": 0.0}, False

        tx, ty, tyaw = self._target
        dist = xy_dist(pose[0], pose[1], tx, ty)
        yaw_err = goal_yaw_error(pose[2], tyaw)
        if self._fine_best_xy is None or dist < self._fine_best_xy:
            self._fine_best_xy = dist

        xy_imu, yaw_imu = self._imu_hints()

        if self._xy_close.active:
            drive, self._xy_close, xy_done = tick_xy_gap_close(
                pose[0],
                pose[1],
                pose[2],
                tx,
                ty,
                self._xy_close,
                XY_GAP_CFG,
                now,
                imu=xy_imu,
            )
            with _goal_lock:
                fb = dict(_goal_state.get("feedback") or {})
                fb["xy_close_phase"] = self._xy_close.phase
                fb["xy_close_note"] = self._xy_close.note
                fb["position_error_m"] = round(dist, 3)
                fb["goal_yaw_err_deg"] = round(math.degrees(yaw_err), 1)
                fb["imu_assist"] = {
                    "ok": xy_imu.ok,
                    "gy": round(xy_imu.gy, 3),
                    "gz": round(xy_imu.gz, 3),
                }
                _goal_state["feedback"] = fb
                _goal_state["updated_at"] = time.time()
            write_goal_status()
            if xy_done:
                self._fine_best_xy = dist
                failed = self._xy_close.result == "failed" or dist >= XY_REPLAN_M
                self._xy_close = XyGapCloseState()
                if failed:
                    self.get_logger().warning(
                        f"xy close failed nav_id={self._nav_id} dist={dist:.3f}m "
                        f"— Nav2 replan"
                    )
                    self._replan_nav2("xy_close_failed")
                    return {"x": 0.0, "y": 0.0}, False
                self._start_yaw_align_phase(yaw_err, dist)
            return drive, False

        drive, yaw_done = self._tick_yaw_align_only(pose, dist, now, imu=yaw_imu)
        return drive, yaw_done

    def _replan_nav2(self, reason: str) -> None:
        """Abort fine approach and re-send NavigateToPose for a fresh path."""
        if self._target is None or self._replan_inflight:
            return
        max_replans = int(os.environ.get("NAV_MAX_REPLANS", "3"))
        if self._replan_count >= max_replans:
            self.get_logger().error(
                f"Nav2 replan limit ({max_replans}) nav_id={self._nav_id} "
                f"reason={reason} — aborting"
            )
            self._yaw_align = YawPulseAlignState()
            self._xy_close = XyGapCloseState()
            self._xy_committed = False
            # Signal done so finish path can mark aborted-ish success cleanup.
            threading.Thread(
                target=self.cancel,
                kwargs={"result": "aborted"},
                daemon=True,
            ).start()
            return

        self._replan_count += 1
        self._replan_inflight = True
        self._yaw_align = YawPulseAlignState()
        self._xy_close = XyGapCloseState()
        self._xy_committed = False
        if _cmd_bridge is not None:
            _cmd_bridge.stop_motors()
        threading.Thread(
            target=self._replan_nav2_worker,
            args=(reason,),
            daemon=True,
        ).start()

    def _replan_nav2_worker(self, reason: str) -> None:
        try:
            if self._target is None:
                return
            tx, ty, tyaw = self._target
            self._generation += 1
            generation = self._generation
            self._cancel_handle_only()
            self._cancel_controller_goals()
            if _cmd_bridge is not None:
                _cmd_bridge.pause()
                _cmd_bridge.stop_motors()

            self.get_logger().info(
                f"Nav2 replan #{self._replan_count} nav_id={self._nav_id} "
                f"reason={reason} "
                f"target=({tx:.3f},{ty:.3f},{math.degrees(tyaw):.1f}°)"
            )
            append_nav_run_event(
                "replan",
                nav_id=self._nav_id,
                reason=reason,
                replan_n=self._replan_count,
                target={"x": tx, "y": ty, "yaw": tyaw},
            )

            start = self._wait_pose_settle(
                f"{self._nav_id}-replan",
                min_settle_sec=min(NAV_MOTION_SETTLE_SEC, 2.0),
                restore_status="navigating",
                reason=f"replan_{reason}",
            )
            if _cmd_bridge is not None:
                _cmd_bridge.resume()

            pose = PoseStamped()
            pose.header.frame_id = MAP_FRAME
            pose.header.stamp = self.get_clock().now().to_msg()
            pose.pose.position.x = float(tx)
            pose.pose.position.y = float(ty)
            pose.pose.position.z = 0.0
            qx, qy, qz, qw = yaw_to_quat(float(tyaw))
            pose.pose.orientation.x = qx
            pose.pose.orientation.y = qy
            pose.pose.orientation.z = qz
            pose.pose.orientation.w = qw
            goal = NavigateToPose.Goal()
            goal.pose = pose

            with _goal_lock:
                fb = dict(_goal_state.get("feedback") or {})
                fb["replan_reason"] = reason
                fb["replan_n"] = self._replan_count
                if start is not None:
                    fb["pose"] = {
                        "x": round(start[0], 3),
                        "y": round(start[1], 3),
                        "yaw": round(start[2], 4),
                    }
                _goal_state.update(
                    {
                        "status": "navigating",
                        "result": None,
                        "feedback": fb,
                        "updated_at": time.time(),
                    }
                )
            write_goal_status()

            send_future = self._client.send_goal_async(
                goal,
                feedback_callback=lambda fb, gen=generation: self._on_feedback(
                    fb, gen
                ),
            )
            send_future.add_done_callback(
                lambda fut, gen=generation: self._on_goal_response(fut, gen)
            )
        finally:
            self._replan_inflight = False

    def _tick_yaw_align_only(
        self,
        pose: tuple[float, float, float],
        dist_xy: float,
        now: float,
        *,
        imu: YawImuAssist | None = None,
    ) -> tuple[dict[str, float], bool]:
        if self._target is None:
            return {"x": 0.0, "y": 0.0}, False

        yaw_err = goal_yaw_error(pose[2], self._target[2])

        # XY committed: if kidnapped far away, hand back to Nav2 for a new path.
        if self._xy_committed and dist_xy >= XY_KIDNAP_M:
            self.get_logger().warning(
                f"yaw kidnap nav_id={self._nav_id} dist={dist_xy:.3f}m "
                f"(>{XY_KIDNAP_M:.2f}m) — Nav2 replan"
            )
            self._replan_nav2("kidnap")
            return {"x": 0.0, "y": 0.0}, False

        drive, self._yaw_align, done = tick_yaw_pulse_align(
            pose[2],
            self._target[2],
            self._yaw_align,
            YAW_ALIGN_CFG,
            now,
            dist_xy=dist_xy,
            imu=imu,
        )
        with _goal_lock:
            fb = dict(_goal_state.get("feedback") or {})
            fb["yaw_align_phase"] = self._yaw_align.phase
            fb["yaw_align_note"] = self._yaw_align.note
            fb["goal_yaw_err_deg"] = round(math.degrees(yaw_err), 1)
            fb["position_error_m"] = round(dist_xy, 3)
            fb["xy_committed"] = self._xy_committed
            fb["fine_best_xy_m"] = (
                round(self._fine_best_xy, 3) if self._fine_best_xy is not None else None
            )
            if imu is not None:
                fb["imu_assist"] = {
                    "ok": imu.ok,
                    "gz": round(imu.gz, 3),
                    "integ_deg": round(math.degrees(imu.integrated_yaw_rad), 1),
                }
            _goal_state["feedback"] = fb
            _goal_state["updated_at"] = time.time()
        write_goal_status()
        return drive, done

    def tick_yaw_align(self, now: float) -> tuple[dict[str, float], bool]:
        return self.tick_fine_approach(now)

    def finish_fine_approach(self) -> None:
        if self._target is None:
            return
        pose = self._lookup_map_pose()
        if pose is None:
            return
        dist = xy_dist(pose[0], pose[1], self._target[0], self._target[1])
        yaw_err = goal_yaw_error(pose[2], self._target[2])

        # Only reopen via Nav2 if kidnapped — never local chase after yaw done.
        if dist >= XY_KIDNAP_M and not self._xy_close.active:
            self.get_logger().warning(
                f"finish kidnap replan nav_id={self._nav_id} dist={dist:.3f}m"
            )
            self._replan_nav2("finish_kidnap")
            return

        self._generation += 1
        info = self._progress_payload()
        self._cancel_handle_only()
        if _cmd_bridge is not None:
            _cmd_bridge.pause()
            _cmd_bridge.stop_motors()
        self._wait_pose_settle(
            f"{self._nav_id}-fine-done",
            min_settle_sec=NAV_CANCEL_SETTLE_SEC,
            restore_status="idle",
            reason="fine_approach",
        )
        self._reset_fine_approach()
        self._reset_motion_settle()
        with _goal_lock:
            _goal_state.update(
                {
                    "status": "idle",
                    "result": "succeeded",
                    "feedback": {
                        **(dict(_goal_state.get("feedback") or {})),
                        "xy_close": False,
                        "yaw_align": False,
                        "xy_committed": True,
                        "position_error_m": info.get("position_error_m"),
                        "yaw_error_deg": info.get("yaw_error_deg"),
                    },
                    "updated_at": time.time(),
                }
            )
        write_goal_status()
        if _cmd_bridge is not None:
            _cmd_bridge.resume()
        self.get_logger().info(
            f"nav succeeded (xy+yaw fine approach) nav_id={self._nav_id} "
            f"dist={info.get('position_error_m')} "
            f"yaw_err={info.get('yaw_error_deg')}"
        )
        append_nav_run_event("finished", result="succeeded", **info)

    def motion_settle_required(self) -> bool:
        return self._motion_settle_required or self._motion_settle_active

    def finish_yaw_align(self) -> None:
        self.finish_fine_approach()

    def ensure_motion_settled(self) -> bool:
        """Block until pose stable when resuming drive after an idle gap."""
        if not self._motion_settle_required:
            return True
        with self._motion_settle_lock:
            if not self._motion_settle_required:
                return True
            if self._motion_settle_active:
                return False
            self._motion_settle_active = True
        try:
            if _cmd_bridge is not None:
                _cmd_bridge.pause()
                _cmd_bridge.stop_motors()
            pose = self._wait_pose_settle(
                f"{self._nav_id}-motion",
                min_settle_sec=NAV_MOTION_SETTLE_SEC,
                restore_status=self._nav_status(),
                reason="motion_resume",
            )
            self._motion_settle_required = False
            if _cmd_bridge is not None:
                _cmd_bridge.resume()
            return pose is not None
        finally:
            self._motion_settle_active = False

    def _wait_pose_settle(
        self,
        nav_id: str,
        *,
        min_settle_sec: float | None = None,
        restore_status: str | None = None,
        reason: str = "start",
    ) -> tuple[float, float, float] | None:
        """Observe TF + scan_match until pose stops jumping (avoids stale-map plans)."""
        min_sec = NAV_START_SETTLE_SEC if min_settle_sec is None else min_settle_sec
        if min_sec <= 0 and NAV_POSE_STABLE_SEC <= 0:
            return self._lookup_map_pose()

        t0 = time.monotonic()
        min_until = t0 + max(0.0, min_sec)
        deadline = t0 + max(min_until - t0, NAV_START_SETTLE_MAX_SEC, 0.1)
        stable_start: float | None = None
        last: tuple[float, float, float] | None = None

        with _goal_lock:
            _goal_state["status"] = "settling"
            fb = dict(_goal_state.get("feedback") or {})
            fb["settle_s"] = 0.0
            fb["settle_reason"] = reason
            _goal_state["feedback"] = fb
        write_goal_status()

        while time.monotonic() < deadline:
            pose = self._lookup_map_pose()
            now = time.monotonic()
            elapsed = now - t0

            if pose is not None and last is not None:
                drift = math.hypot(pose[0] - last[0], pose[1] - last[1])
                dyaw = abs(wrap_angle(pose[2] - last[2]))
                if drift <= NAV_POSE_STABLE_XY_M and dyaw <= NAV_POSE_STABLE_YAW_RAD:
                    if stable_start is None:
                        stable_start = now
                else:
                    stable_start = None
            elif pose is not None:
                stable_start = None
            last = pose

            scan_ok = True
            scan_match = None
            slam = _read_slam_snapshot()
            if slam and slam.get("mode") == "localization":
                raw_score = slam.get("scan_match_score")
                # None means SLAM isn't publishing match yet (was stuck None for
                # entire nav-20260826-181554 during auto-repos cooldown). Do not
                # treat missing score as a green light to drive.
                if raw_score is None:
                    scan_ok = False
                    stable_start = None
                else:
                    try:
                        scan_match = float(raw_score)
                    except (TypeError, ValueError):
                        scan_match = None
                        scan_ok = False
                        stable_start = None
                    if scan_match is not None and scan_match < NAV_MIN_SCAN_MATCH:
                        scan_ok = False
                        stable_start = None

            stable_for = (now - stable_start) if stable_start is not None else 0.0
            with _goal_lock:
                fb = dict(_goal_state.get("feedback") or {})
                fb["settle_s"] = round(elapsed, 1)
                fb["pose_stable_s"] = round(stable_for, 1)
                if scan_match is not None:
                    fb["scan_match_score"] = round(scan_match, 3)
                if pose is not None:
                    fb["pose"] = {
                        "x": round(pose[0], 3),
                        "y": round(pose[1], 3),
                        "yaw": round(pose[2], 4),
                    }
                _goal_state["feedback"] = fb
            write_goal_status()

            if (
                now >= min_until
                and pose is not None
                and stable_for >= NAV_POSE_STABLE_SEC
                and scan_ok
            ):
                self.get_logger().info(
                    f"pose settled nav_id={nav_id} elapsed={elapsed:.1f}s "
                    f"stable={stable_for:.1f}s scan_match={scan_match} pose={pose}"
                )
                append_nav_run_event(
                    "settled",
                    nav_id=nav_id,
                    reason=reason,
                    elapsed_s=round(elapsed, 2),
                    stable_s=round(stable_for, 2),
                    scan_match_score=scan_match,
                    pose={
                        "x": round(pose[0], 3),
                        "y": round(pose[1], 3),
                        "yaw": round(pose[2], 4),
                    },
                )
                if restore_status is not None:
                    with _goal_lock:
                        _goal_state["status"] = restore_status
                    write_goal_status()
                return pose

            time.sleep(0.1)

        self.get_logger().warning(
            f"pose settle timeout nav_id={nav_id} reason={reason} "
            f"elapsed={time.monotonic() - t0:.1f}s last={last}"
        )
        append_nav_run_event(
            "settle_timeout", nav_id=nav_id, reason=reason, last_pose=last
        )
        if restore_status is not None:
            with _goal_lock:
                _goal_state["status"] = restore_status
            write_goal_status()
        return last if last is not None else self._lookup_map_pose()

    def goto(
        self,
        x: float,
        y: float,
        yaw: float,
        label: str = "",
        *,
        fine_docking: bool = False,
        nav_id: str = "",
    ) -> dict[str, Any]:
        """Send NavigateToPose and leave control to Nav2 for the whole trip.

        ``fine_docking`` is retained for API compatibility (dashboard checkbox).
        Marker-relative docking is not implemented yet — Nav2 owns the approach.
        """
        with self._send_lock:
            if not self._client.server_is_ready():
                if not self.wait_server(5.0):
                    return {"success": False, "error": "Nav2 navigate_to_pose not ready"}

            try:
                os.remove(KILL_PATH)
            except FileNotFoundError:
                pass

            if _cmd_bridge is not None:
                _cmd_bridge.pause()
                _cmd_bridge.stop_motors()

            self._generation += 1
            generation = self._generation
            self._nav_id = nav_id or f"nav-{int(time.time())}"
            self._nav_started_at = time.time()
            self._target = (float(x), float(y), float(yaw))
            self._reset_progress()
            self._reset_yaw_align()
            self._replan_count = 0
            self._replan_inflight = False
            self._cancel_handle_only()
            self._cancel_controller_goals()
            if NAV_CANCEL_SETTLE_SEC > 0:
                time.sleep(min(NAV_CANCEL_SETTLE_SEC, 0.6))
            # Do not let the previous goal's global path seed the new goal's
            # progress baseline while Nav2 is still planning.
            if _path_bridge is not None:
                _path_bridge.clear()

            start = self._wait_pose_settle(
                self._nav_id,
                min_settle_sec=NAV_START_SETTLE_SEC,
                reason="nav_start",
            )
            self._reset_motion_settle()

            if _cmd_bridge is not None:
                _cmd_bridge.resume()

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
            straight = None
            if start is not None:
                straight = round(math.hypot(float(x) - start[0], float(y) - start[1]), 3)
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
                                "x": round(start[0], 3),
                                "y": round(start[1], 3),
                                "yaw": round(start[2], 4),
                            }
                            if start is not None
                            else None,
                        },
                        "updated_at": time.time(),
                        "nav_id": self._nav_id,
                    }
                )
            write_goal_status()

            send_future = self._client.send_goal_async(
                goal,
                feedback_callback=lambda fb, gen=generation: self._on_feedback(fb, gen),
            )
            send_future.add_done_callback(
                lambda fut, gen=generation: self._on_goal_response(fut, gen)
            )
            self.get_logger().info(
                f"goto nav_id={self._nav_id} label={label or ''} "
                f"fine_docking={bool(fine_docking)} "
                f"target=({x:.3f},{y:.3f},{math.degrees(yaw):.1f}°) "
                f"start={start} straight_m={straight} control=nav2_continuous"
            )
            append_nav_run_event(
                "goto",
                nav_id=self._nav_id,
                generation=generation,
                goal=goal_meta,
                start_pose=start,
                straight_m=straight,
                control="nav2_continuous",
            )
            return {"success": True, "nav_id": self._nav_id, "status": "navigating"}

    def _progress_distance(
        self, pose: tuple[float, float, float] | None
    ) -> tuple[float | None, str]:
        if pose is not None and _path_bridge is not None:
            path = _path_bridge.snapshot().get("global") or []
            path_distance = remaining_path_distance(path, (pose[0], pose[1]))
            if path_distance is not None:
                return path_distance, "path"
        if pose is not None and self._target is not None:
            return (
                math.hypot(self._target[0] - pose[0], self._target[1] - pose[1]),
                "straight",
            )
        return None, "straight"

    def _note_distance(self, distance: float | None, metric: str = "straight") -> None:
        if distance is None:
            return
        if metric != self._progress_metric:
            self._progress_metric = metric
            self._best_distance = None
            self._last_improve_at = time.time()
            self._stall_warned = False
            self._stall_evented = False
            self._stall_aborting = False
        if self._best_distance is None or distance < self._best_distance - 0.05:
            self._best_distance = float(distance)
            self._last_improve_at = time.time()
            self._stall_warned = False
            self._stall_evented = False
            self._stall_aborting = False

    def _progress_payload(
        self,
        *,
        pose: tuple[float, float, float] | None = None,
        distance: float | None = None,
        position_error: float | None = None,
    ) -> dict[str, Any]:
        if pose is None:
            pose = self._lookup_map_pose()
        drive = _cmd_bridge.snapshot() if _cmd_bridge is not None else {}
        path = _path_bridge.snapshot() if _path_bridge is not None else {}
        now = time.time()
        metric = self._progress_metric
        dist = distance
        if dist is None:
            dist, metric = self._progress_distance(pose)
        if position_error is None:
            position_error = dist
        stall_s = max(0.0, now - self._last_improve_at) if self._last_improve_at else 0.0
        payload: dict[str, Any] = {
            "nav_id": self._nav_id,
            "generation": self._generation,
            "elapsed_s": round(now - self._nav_started_at, 1) if self._nav_started_at else 0.0,
            "distance_remaining": round(dist, 3) if dist is not None else None,
            "best_distance_m": round(self._best_distance, 3)
            if self._best_distance is not None
            else None,
            "distance_metric": metric,
            "stall_s": round(stall_s, 1),
            "drive": drive,
            "path": {
                "path_length_m": path.get("path_length_m"),
                "remaining_m": round(dist, 3)
                if metric == "path" and dist is not None
                else None,
                "updated_at": path.get("updated_at"),
            },
            "control": "nav2_continuous",
        }
        if pose is not None:
            payload["pose"] = {
                "x": round(pose[0], 3),
                "y": round(pose[1], 3),
                "yaw": round(pose[2], 4),
                "yaw_deg": round(math.degrees(pose[2]), 1),
            }
        if self._target is not None:
            payload["goal"] = {
                "x": round(self._target[0], 3),
                "y": round(self._target[1], 3),
                "yaw": round(self._target[2], 4),
            }
            if pose is not None:
                bearing = math.atan2(self._target[1] - pose[1], self._target[0] - pose[0])
                payload["heading_err_deg"] = round(
                    math.degrees(wrap_angle(bearing - pose[2])), 1
                )
                payload["goal_yaw_err_deg"] = round(
                    math.degrees(wrap_angle(self._target[2] - pose[2])), 1
                )
                payload["position_error_m"] = (
                    round(position_error, 3) if position_error is not None else None
                )
                payload["yaw_error_deg"] = payload["goal_yaw_err_deg"]
        return payload

    def _progress_tick(self) -> None:
        with _goal_lock:
            status = str(_goal_state.get("status") or "")
            feedback = _goal_state.get("feedback")
        if status != "navigating" and status != "yaw_align":
            return

        pose = self._lookup_map_pose()
        now = time.time()
        if pose is None:
            if LOCALIZATION_FAIL_STOP and (now - self._last_tf_ok_at) > TF_STALE_SEC:
                self.get_logger().error(
                    f"localization TF stale >{TF_STALE_SEC}s — canceling nav"
                )
                append_nav_run_event(
                    "localization_lost", nav_id=self._nav_id, tf_stale_s=TF_STALE_SEC
                )
                self.cancel(result="localization_lost")
            return
        self._last_tf_ok_at = now

        dist, metric = self._progress_distance(pose)
        xy_dist = (
            math.hypot(self._target[0] - pose[0], self._target[1] - pose[1])
            if self._target is not None
            else None
        )
        yaw_err = None
        if self._target is not None:
            yaw_err = goal_yaw_error(pose[2], self._target[2])
        self._yaw_handoff_zone = CUSTOM_FINAL_APPROACH and (
            xy_dist is not None
            and (
                should_handoff_xy(xy_dist, YAW_ALIGN_CFG)
                or (
                    xy_dist <= YAW_ALIGN_CFG.xy_handoff_large_yaw_m
                    and yaw_err is not None
                    and abs(yaw_err) >= YAW_ALIGN_CFG.large_handoff_err_rad
                )
            )
        )
        if (
            CUSTOM_FINAL_APPROACH
            and not self.fine_approach_active()
            and yaw_err is not None
            and xy_dist is not None
            and should_begin_yaw_align(xy_dist, yaw_err, YAW_ALIGN_CFG)
        ):
            self._begin_fine_approach(yaw_err, xy_dist)

        self._note_distance(dist, metric)

        refresh_assist = now - self._last_assist_refresh_at >= NAV_ASSIST_REFRESH_SEC
        if refresh_assist:
            self._last_assist_refresh_at = now
        full_ctx = now - self._last_context_refresh_at >= NAV_CONTEXT_REFRESH_SEC
        if full_ctx:
            self._last_context_refresh_at = now

        info = self._progress_payload(
            pose=pose,
            distance=dist,
            position_error=xy_dist,
        )
        ctx = nav_context(full=full_ctx, refresh_assist=refresh_assist)
        info["map"] = ctx.get("map")
        info["scan"] = ctx.get("scan")
        info["drive_assist"] = ctx.get("drive_assist")
        if drive_assist_blocking(info.get("drive_assist")):
            self._last_improve_at = now
            info["stall_s"] = 0.0
            info["motion_hold"] = "drive_assist"

        enriched = dict(feedback) if isinstance(feedback, dict) else {}
        enriched.update(
            {
                "pose": info.get("pose"),
                "stall_s": info.get("stall_s"),
                "best_distance_m": info.get("best_distance_m"),
                "heading_err_deg": info.get("heading_err_deg"),
                "goal_yaw_err_deg": info.get("goal_yaw_err_deg"),
                "position_error_m": info.get("position_error_m"),
                "yaw_error_deg": info.get("yaw_error_deg"),
                "cmd_vx": (info.get("drive") or {}).get("cmd_vx"),
                "cmd_wz": (info.get("drive") or {}).get("cmd_wz"),
                "drive_phase": (info.get("drive") or {}).get("phase"),
                "path_length_m": (info.get("path") or {}).get("path_length_m"),
                "motion_hold": info.get("motion_hold"),
                "control": "nav2_continuous",
            }
        )
        with _goal_lock:
            _goal_state["feedback"] = enriched
            _goal_state["updated_at"] = now
        write_goal_status()

        stall_s = float(info.get("stall_s") or 0.0)
        if stall_s >= NAV_STALL_WARN_SEC and not self._stall_warned:
            self._stall_warned = True
            self.get_logger().warning(
                f"nav stall warn nav_id={self._nav_id} stall={stall_s:.1f}s "
                f"dist={dist}"
            )
            append_nav_run_event("stall_warn", **info)
        if stall_s >= NAV_STALL_EVENT_SEC and not self._stall_evented:
            self._stall_evented = True
            self.get_logger().error(
                f"nav stall event nav_id={self._nav_id} stall={stall_s:.1f}s"
            )
            append_nav_run_event("stall_event", **info)
        if (
            stall_s >= NAV_STALL_ABORT_SEC
            and not self._stall_aborting
            and self.nav_active()
            and not self.fine_approach_active()
        ):
            self._stall_aborting = True
            self.get_logger().error(
                f"nav stall abort nav_id={self._nav_id} stall={stall_s:.1f}s "
                f"dist={dist} best={self._best_distance} "
                f"(no progress >{NAV_STALL_ABORT_SEC:.0f}s)"
            )
            append_nav_run_event(
                "stall_abort",
                **info,
                abort_after_s=NAV_STALL_ABORT_SEC,
            )
            threading.Thread(
                target=self.cancel,
                kwargs={"result": "aborted"},
                daemon=True,
            ).start()
            return

        if now - self._last_progress_log_at >= NAV_PROGRESS_PERIOD_SEC:
            self._last_progress_log_at = now
            drive = info.get("drive") or {}
            self.get_logger().info(
                f"nav progress nav_id={self._nav_id} status={status} "
                f"elapsed={info.get('elapsed_s')}s dist={dist} "
                f"best={info.get('best_distance_m')} stall={stall_s:.1f}s "
                f"metric={info.get('distance_metric')} "
                f"cmd_vx={drive.get('cmd_vx')} cmd_wz={drive.get('cmd_wz')} "
                f"drive_phase={drive.get('phase')} "
                f"yaw_err={info.get('goal_yaw_err_deg')}° "
                f"pose={info.get('pose')} control="
                f"{'yaw_pulse' if status == 'yaw_align' else 'nav2_continuous'}"
            )

    def _cancel_handle_only(self) -> None:
        handle = self._goal_handle
        self._goal_handle = None
        if handle is not None:
            try:
                handle.cancel_goal_async()
            except Exception:  # noqa: BLE001
                pass

    def _cancel_controller_goals(self) -> None:
        if not self._follow_cancel_client.service_is_ready():
            return
        request = CancelGoal.Request()
        self._follow_cancel_client.call_async(request)

    def cancel(self, result: str = "canceled") -> dict[str, Any]:
        self._generation += 1
        info = self._progress_payload()
        self._cancel_handle_only()
        self._cancel_controller_goals()
        if _cmd_bridge is not None:
            _cmd_bridge.pause()
            _cmd_bridge.stop_motors()
        nav_id = self._nav_id
        self._wait_pose_settle(
            f"{nav_id}-cancel",
            min_settle_sec=NAV_CANCEL_SETTLE_SEC,
            restore_status="idle",
            reason="cancel",
        )
        self._reset_yaw_align()
        self._reset_motion_settle()
        with _goal_lock:
            _goal_state.update(
                {
                    "status": "idle",
                    "result": result,
                    "updated_at": time.time(),
                }
            )
        write_goal_status()
        if _path_bridge is not None:
            _path_bridge.clear()
        if _cmd_bridge is not None:
            _cmd_bridge.resume()
        self.get_logger().info(f"nav {result} nav_id={self._nav_id}")
        append_nav_run_event(result, **info)
        return {"success": True, "status": "idle", "result": result}

    def pause(self) -> dict[str, Any]:
        self._generation += 1
        info = self._progress_payload()
        self._cancel_handle_only()
        self._cancel_controller_goals()
        if _cmd_bridge is not None:
            _cmd_bridge.pause()
            _cmd_bridge.stop_motors()
        write_json_atomic(
            KILL_PATH,
            {"latched": True, "reason": "pause", "updatedAt": time.time()},
        )
        with _goal_lock:
            _goal_state.update(
                {
                    "status": "paused",
                    "result": None,
                    "updated_at": time.time(),
                }
            )
        write_goal_status()
        self.get_logger().info(f"nav paused nav_id={self._nav_id}")
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
            with _goal_lock:
                _goal_state.update(
                    {"status": "failed", "result": str(exc), "updated_at": time.time()}
                )
            write_goal_status()
            self.get_logger().error(f"nav goal response error: {exc}")
            append_nav_run_event("goal_error", nav_id=self._nav_id, error=str(exc))
            return
        if not handle.accepted:
            if _cmd_bridge is not None:
                _cmd_bridge.pause()
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
            append_nav_run_event("rejected", nav_id=self._nav_id)
            return
        self._goal_handle = handle
        result_future = handle.get_result_async()
        result_future.add_done_callback(
            lambda fut, gen=generation: self._on_result(fut, gen)
        )

    def _on_feedback(self, feedback_msg: Any, generation: int) -> None:
        if generation != self._generation:
            return
        fb = feedback_msg.feedback
        dist = None
        pose = self._lookup_map_pose()
        dist, metric = self._progress_distance(pose)
        if dist is None:
            try:
                dist = float(fb.distance_remaining)
                metric = self._progress_metric
            except (AttributeError, TypeError, ValueError):
                pass
        # Never feed Nav2's straight-line fallback into a path-based stall
        # timer when the current pose is temporarily unavailable.
        if metric != "path" or pose is not None:
            self._note_distance(dist, metric)
        with _goal_lock:
            current = dict(_goal_state.get("feedback") or {})
            current["distance_remaining"] = (
                round(dist, 3) if dist is not None else current.get("distance_remaining")
            )
            if pose is not None:
                current["pose"] = {
                    "x": round(pose[0], 3),
                    "y": round(pose[1], 3),
                    "yaw": round(pose[2], 4),
                }
            _goal_state["feedback"] = current
            _goal_state["updated_at"] = time.time()
        write_goal_status()

    def _on_result(self, future: Any, generation: int) -> None:
        if generation != self._generation:
            return
        if self._yaw_align.active:
            self.get_logger().info(
                f"ignoring Nav2 result during yaw pulse align nav_id={self._nav_id}"
            )
            return
        try:
            result = future.result()
            status = int(result.status)
        except Exception as exc:  # noqa: BLE001
            self.get_logger().error(f"nav result error: {exc}")
            status = GoalStatus.STATUS_UNKNOWN

        status_name = {
            GoalStatus.STATUS_SUCCEEDED: "succeeded",
            GoalStatus.STATUS_CANCELED: "canceled",
            GoalStatus.STATUS_ABORTED: "aborted",
        }.get(status, "failed")

        info = self._progress_payload()
        if status_name == "succeeded" and self._target is not None:
            pose = self._lookup_map_pose()
            dist_f = None
            yaw_err = None
            if pose is not None:
                dist_f = math.hypot(
                    self._target[0] - pose[0], self._target[1] - pose[1]
                )
                yaw_err = goal_yaw_error(pose[2], self._target[2])
            if (
                CUSTOM_FINAL_APPROACH
                and dist_f is not None
                and yaw_err is not None
                and should_begin_yaw_align(dist_f, yaw_err, YAW_ALIGN_CFG)
            ):
                self.get_logger().info(
                    f"Nav2 succeeded early dist={dist_f:.3f}m "
                    f"yaw_off={math.degrees(yaw_err):+.1f}° — fine approach"
                )
                if _cmd_bridge is not None:
                    _cmd_bridge.resume()
                self._begin_fine_approach(yaw_err, dist_f)
                return
            if dist_f is None:
                dist = info.get("position_error_m")
                if dist is None:
                    dist = info.get("distance_remaining")
                try:
                    dist_f = float(dist) if dist is not None else None
                except (TypeError, ValueError):
                    dist_f = None
            if dist_f is not None and dist_f > NAV_FALSE_SUCCESS_XY_M:
                self.get_logger().error(
                    f"nav false success nav_id={self._nav_id} dist={dist_f:.3f}m "
                    f"(>{NAV_FALSE_SUCCESS_XY_M}m) — treating as aborted"
                )
                status_name = "aborted"
                append_nav_run_event(
                    "false_success",
                    nav_id=self._nav_id,
                    distance_m=round(dist_f, 3),
                )

        if _cmd_bridge is not None:
            _cmd_bridge.pause()
            _cmd_bridge.stop_motors()

        nav_id = self._nav_id
        self._wait_pose_settle(
            f"{nav_id}-finish",
            min_settle_sec=NAV_CANCEL_SETTLE_SEC,
            restore_status="idle",
            reason=status_name,
        )
        self._reset_yaw_align()
        self._reset_motion_settle()

        with _goal_lock:
            _goal_state.update(
                {
                    "status": "idle",
                    "result": status_name,
                    "feedback": {
                        **(dict(_goal_state.get("feedback") or {})),
                        "position_error_m": info.get("position_error_m"),
                        "yaw_error_deg": info.get("yaw_error_deg"),
                    },
                    "updated_at": time.time(),
                }
            )
        write_goal_status()
        if _path_bridge is not None and status_name != "succeeded":
            _path_bridge.clear()
        self.get_logger().info(
            f"NavigateToPose finished: {status_name} nav_id={self._nav_id} "
            f"dist={info.get('distance_remaining')} "
            f"yaw_err={info.get('yaw_error_deg')} control=nav2_continuous"
        )
        append_nav_run_event("finished", result=status_name, **info)


class GoalHttpHandler(BaseHTTPRequestHandler):
    def log_message(self, fmt: str, *args: Any) -> None:  # noqa: A003
        return

    def _json(self, code: int, payload: dict[str, Any]) -> None:
        body = json.dumps(payload).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:  # noqa: N802
        path = urlparse(self.path).path
        if path in ("/status", "/"):
            with _goal_lock:
                payload = dict(_goal_state)
            self._json(200, payload)
            return
        self._json(404, {"error": "not found"})

    def do_POST(self) -> None:  # noqa: N802
        path = urlparse(self.path).path
        length = int(self.headers.get("Content-Length") or 0)
        raw: dict[str, Any] = {}
        if length:
            try:
                raw = json.loads(self.rfile.read(length).decode("utf-8"))
            except (json.JSONDecodeError, UnicodeDecodeError):
                self._json(400, {"error": "invalid json"})
                return
        if _goal_node is None:
            self._json(503, {"error": "goal node not ready"})
            return
        if path in ("/goto", "/navigate"):
            try:
                result = _goal_node.goto(
                    float(raw["x"]),
                    float(raw["y"]),
                    float(raw.get("yaw") or 0.0),
                    label=str(raw.get("label") or ""),
                    fine_docking=bool(raw.get("fine_docking")),
                    nav_id=str(raw.get("nav_id") or ""),
                )
            except (KeyError, TypeError, ValueError) as exc:
                self._json(400, {"error": str(exc)})
                return
            self._json(200 if result.get("success") else 502, result)
            return
        if path == "/cancel":
            self._json(200, _goal_node.cancel())
            return
        if path == "/pause":
            self._json(200, _goal_node.pause())
            return
        self._json(404, {"error": "not found"})


def poll_command_file() -> None:
    global _last_cmd_seq
    while rclpy.ok():
        time.sleep(0.2)
        if _goal_node is None:
            continue
        try:
            with open(COMMAND_PATH, encoding="utf-8") as handle:
                raw = json.load(handle)
        except (OSError, json.JSONDecodeError, TypeError):
            continue
        if not isinstance(raw, dict):
            continue
        try:
            seq = int(raw.get("seq") or 0)
        except (TypeError, ValueError):
            continue
        if seq <= _last_cmd_seq:
            continue
        _last_cmd_seq = seq
        op = str(raw.get("op") or "").lower()
        if seq <= _process_started_ms and op == "goto":
            # Navigation is non-resumable. Do not replay a command left on the
            # shared volume by an earlier process, even if startup cleanup
            # raced with this poller.
            try:
                _goal_node.get_logger().warning(
                    f"ignoring stale goto command seq={seq} during startup"
                )
            except Exception:  # noqa: BLE001
                pass
            continue
        err = None
        try:
            if op == "goto":
                fine = bool(raw.get("fine_docking"))
                nav_id = str(raw.get("nav_id") or "")
                result = _goal_node.goto(
                    float(raw["x"]),
                    float(raw["y"]),
                    float(raw.get("yaw") or 0.0),
                    label=str(raw.get("label") or raw.get("id") or ""),
                    fine_docking=fine,
                    nav_id=nav_id,
                )
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
    path._flush()  # noqa: SLF001

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
        rclpy.shutdown()


if __name__ == "__main__":
    main()
