#!/usr/bin/env python3
"""Sim drive: continuous stick dynamics matching the real rover.

Real stack (post-refactor):
  Nav2 Twist → drive_interface.twist_to_pi_drive → Pi {x,y} → motors

This module:
  - Reuses ``ros2-nav/drive_interface`` for Twist↔stick mapping
  - Integrates body motion from calibrated stick speeds (user char table)
  - Keeps legacy WASD/track helpers for explore / recovery / pulse tools
"""

from __future__ import annotations

import math
import sys
from dataclasses import dataclass, field
from pathlib import Path

_NAV = Path(__file__).resolve().parents[1] / "ros2-nav"
if str(_NAV) not in sys.path:
    sys.path.insert(0, str(_NAV))

from drive_interface import (  # noqa: E402
    PURE_ROTATE_STICK,
    DriveLimits,
    stick_y_to_speed_mps,
    twist_to_pi_drive,
)

# --- Legacy WASD track model (explore / characterize / wall recovery) ---
ALIGN_ANGULAR_RPS = 0.55
TURN_PULSE_ON_PURE_SEC = 0.18
TURN_PULSE_OFF_PURE_SEC = 0.25
TURN_PULSE_ON_ARC_SEC = 0.14
TURN_PULSE_OFF_ARC_SEC = 0.18
START_SETTLE_SEC = 0.0
AUTOPILOT_SETTLE_SEC = 0.15

TRACK_FORWARD_MPS = 0.40
TRACK_REVERSE_MPS = 0.28
TRACK_TURN_MPS = 0.28
TRACK_BASE_M = 0.25

# Full stick yaw rate (rad/s) ≈ teleop held A (~60°/s). Scales with |drive.x|.
STICK_YAW_RATE_RPS = math.radians(62.0)

# Mimic relay→Pi HTTP path + motor commit (~1–2 keepalive ticks).
CMD_LATENCY_SEC = 0.08
# Cartographer-like: localization updates after scan processing, not instantly.
LOC_PROCESS_DELAY_SEC = 0.12

DEFAULT_LIMITS = DriveLimits(
    max_linear_mps=0.35,
    max_angular_rps=0.80,
)


def continuous_pure_turn_yaw_rate_rps() -> float:
    v_l, v_r = keys_to_tracks(["a"])
    _lin, ang = tracks_to_twist(v_l, v_r)
    return abs(ang)


def yaw_per_pure_pulse_rad(on_sec: float = TURN_PULSE_ON_PURE_SEC) -> float:
    return continuous_pure_turn_yaw_rate_rps() * max(0.0, on_sec)


def cmd_vel_to_keys(
    vx: float,
    wz: float,
    *,
    align_angular_rps: float = ALIGN_ANGULAR_RPS,
) -> list[str]:
    """Legacy discrete keys (explore / recovery). Prefer continuous stick for nav."""
    keys: list[str] = []
    # Strong yaw → pure tank turn (no W), matching old align gate.
    if abs(wz) >= align_angular_rps:
        keys.append("a" if wz > 0 else "d")
        return keys
    if vx > 0.02:
        keys.append("w")
    elif vx < -0.02:
        keys.append("s")
    if abs(wz) >= 0.03:
        keys.append("a" if wz > 0 else "d")
    return keys


def pulse_turn_keys(
    keys: list[str],
    *,
    now: float,
    phase: str,
    phase_until: float,
    on_pure: float = TURN_PULSE_ON_PURE_SEC,
    off_pure: float = TURN_PULSE_OFF_PURE_SEC,
    on_arc: float = TURN_PULSE_ON_ARC_SEC,
    off_arc: float = TURN_PULSE_OFF_ARC_SEC,
) -> tuple[list[str], str, float]:
    """Legacy on/off pulse machine (unused by continuous nav)."""
    del on_arc, off_arc
    turn = [k for k in keys if k in ("a", "d")]
    drive = [k for k in keys if k in ("w", "s")]
    if drive and not turn:
        return list(drive), "idle", 0.0
    if not turn:
        return list(keys), "idle", 0.0
    on_s = max(0.05, on_pure)
    off_s = max(0.05, off_pure)
    if phase == "on" and now < phase_until:
        return list(turn), "on", phase_until
    if phase == "off" and now < phase_until:
        return [], "off", phase_until
    if phase == "on":
        return [], "off", now + off_s
    return list(turn), "on", now + on_s


def keys_to_tracks(keys: list[str]) -> tuple[float, float]:
    keyset = {str(k).lower() for k in keys}
    v_l = 0.0
    v_r = 0.0
    if "w" in keyset:
        v_l = TRACK_FORWARD_MPS
        v_r = TRACK_FORWARD_MPS
    elif "s" in keyset:
        v_l = -TRACK_REVERSE_MPS
        v_r = -TRACK_REVERSE_MPS
    if "a" in keyset:
        v_l = -TRACK_TURN_MPS
        v_r = TRACK_TURN_MPS
    elif "d" in keyset:
        v_l = TRACK_TURN_MPS
        v_r = -TRACK_TURN_MPS
    return v_l, v_r


def tracks_to_twist(
    v_left: float, v_right: float, *, track_base_m: float = TRACK_BASE_M
) -> tuple[float, float]:
    base = max(0.001, track_base_m)
    linear = 0.5 * (v_left + v_right)
    angular = (v_right - v_left) / base
    return linear, angular


def integrate_tank(
    pose: dict,
    v_left: float,
    v_right: float,
    dt: float,
    *,
    track_base_m: float = TRACK_BASE_M,
) -> dict:
    linear, angular = tracks_to_twist(v_left, v_right, track_base_m=track_base_m)
    return integrate_body(pose, linear, angular, dt)


def integrate_body(pose: dict, vx: float, wz: float, dt: float) -> dict:
    """Unicycle integrate in map frame (body +x forward)."""
    dt = max(0.0, float(dt))
    yaw0 = float(pose["yaw"])
    if abs(wz) < 1e-9:
        return {
            "x": float(pose["x"]) + vx * math.cos(yaw0) * dt,
            "y": float(pose["y"]) + vx * math.sin(yaw0) * dt,
            "yaw": yaw0,
        }
    yaw1 = yaw0 + wz * dt
    # Exact arc
    radius = vx / wz if abs(wz) > 1e-9 else 0.0
    cx = float(pose["x"]) - radius * math.sin(yaw0)
    cy = float(pose["y"]) + radius * math.cos(yaw0)
    return {
        "x": cx + radius * math.sin(yaw1),
        "y": cy - radius * math.cos(yaw1),
        "yaw": math.atan2(math.sin(yaw1), math.cos(yaw1)),
    }


def stick_to_body_twist(drive: dict[str, float]) -> tuple[float, float]:
    """Pi stick → body Twist using the same forward table as the real rover."""
    x = float(drive.get("x", 0.0))
    y = float(drive.get("y", 0.0))
    vx = stick_y_to_speed_mps(abs(y))
    if y > 0.0:
        vx = -vx  # +y = reverse
    # twist_to_pi: +wz → −x  ⇒  wz = −x * rate
    wz = -x * STICK_YAW_RATE_RPS
    return vx, wz


def nav_twist_to_body(
    vx_cmd: float,
    wz_cmd: float,
    *,
    limits: DriveLimits | None = None,
    pure_rotate_stick: float | None = None,
) -> tuple[float, float, dict[str, float]]:
    """Nav2-style Twist → stick → calibrated body rates (real continuous path)."""
    lim = limits or DEFAULT_LIMITS
    stick = twist_to_pi_drive(
        vx_cmd,
        wz_cmd,
        limits=lim,
        allow_reverse=False,
        pure_rotate_stick=pure_rotate_stick,
    )
    body_vx, body_wz = stick_to_body_twist(stick)
    return body_vx, body_wz, stick


@dataclass
class NavDriveState:
    """Runtime drive state for the sim (continuous primary; keys for UI/debug)."""

    keys: list[str] = field(default_factory=list)
    desired_keys: list[str] = field(default_factory=list)
    tracks: tuple[float, float] = (0.0, 0.0)
    body_cmd: dict = field(default_factory=lambda: {"linear": 0.0, "angular": 0.0})
    stick: dict = field(default_factory=lambda: {"x": 0.0, "y": 0.0})
    phase: str = "idle"
    turn_pulse_phase: str = "idle"
    turn_pulse_until: float = 0.0
    settle_until: float = -1e9
    last_moving_at: float = -1e9
    # FIFO of (ready_time, vx_cmd, wz_cmd) — models relay→Pi latency.
    cmd_queue: list[tuple[float, float, float]] = field(default_factory=list)
    applied_vx: float = 0.0
    applied_wz: float = 0.0

    def reset(self) -> None:
        self.keys = []
        self.desired_keys = []
        self.tracks = (0.0, 0.0)
        self.body_cmd = {"linear": 0.0, "angular": 0.0}
        self.stick = {"x": 0.0, "y": 0.0}
        self.phase = "idle"
        self.turn_pulse_phase = "idle"
        self.turn_pulse_until = 0.0
        self.settle_until = -1e9
        self.last_moving_at = -1e9
        self.cmd_queue = []
        self.applied_vx = 0.0
        self.applied_wz = 0.0


def apply_nav_drive(
    state: NavDriveState,
    *,
    vx: float,
    wz: float,
    now: float,
    latency_sec: float = CMD_LATENCY_SEC,
    limits: DriveLimits | None = None,
    pure_rotate_stick: float | None = None,
) -> NavDriveState:
    """Continuous Nav2 path with HTTP-like command latency.

    Twist → ``drive_interface.twist_to_pi_drive`` → calibrated body rates.
    """
    if now < state.settle_until:
        state.phase = "settle"
        state.stick = {"x": 0.0, "y": 0.0}
        state.body_cmd = {"linear": 0.0, "angular": 0.0}
        state.tracks = (0.0, 0.0)
        state.keys = []
        state.cmd_queue = []
        state.applied_vx = 0.0
        state.applied_wz = 0.0
        return state

    latency = max(0.0, float(latency_sec))
    state.cmd_queue.append((now + latency, float(vx), float(wz)))
    if len(state.cmd_queue) > 8:
        state.cmd_queue = state.cmd_queue[-8:]

    applied_any = False
    while state.cmd_queue and state.cmd_queue[0][0] <= now:
        _t, qvx, qwz = state.cmd_queue.pop(0)
        body_vx, body_wz, stick = nav_twist_to_body(
            qvx, qwz, limits=limits, pure_rotate_stick=pure_rotate_stick
        )
        state.applied_vx = body_vx
        state.applied_wz = body_wz
        state.stick = stick
        applied_any = True

    if not applied_any and latency <= 1e-9:
        body_vx, body_wz, stick = nav_twist_to_body(
            vx, wz, limits=limits, pure_rotate_stick=pure_rotate_stick
        )
        state.applied_vx = body_vx
        state.applied_wz = body_wz
        state.stick = stick

    state.body_cmd = {"linear": state.applied_vx, "angular": state.applied_wz}
    if abs(state.applied_vx) > 1e-3 or abs(state.applied_wz) > 1e-3:
        state.last_moving_at = now

    state.keys = []
    if state.stick.get("y", 0) < -0.05:
        state.keys.append("w")
    if state.stick.get("x", 0) < -0.05:
        state.keys.append("a")
    elif state.stick.get("x", 0) > 0.05:
        state.keys.append("d")
    state.desired_keys = list(state.keys)
    state.tracks = (
        state.applied_vx - 0.5 * state.applied_wz * TRACK_BASE_M,
        state.applied_vx + 0.5 * state.applied_wz * TRACK_BASE_M,
    )
    if abs(state.applied_vx) < 1e-3 and abs(state.applied_wz) < 1e-3:
        state.phase = "idle"
    elif abs(state.applied_vx) < 1e-3:
        state.phase = "align"
    else:
        state.phase = "drive"
    return state
