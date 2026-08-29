#!/usr/bin/env python3
"""Skid-steer drive mapping: ROS Twist (vx, wz) ↔ Pi analog stick {x, y}.

Pi teleop convention (unchanged):
  drive.y < 0  → forward
  drive.y > 0  → reverse (not used by autonomous nav — no rear lidar)
  drive.x > 0  → turn right
  drive.x < 0  → turn left

Characterization (held stick, measured ground speed), 2026-08:
  |y|=1.0 → 0.56 m/s … |y|=0.2 → 0.04 m/s … |y|=0.1 → stall
  |x|=1.0 ≈ large in-place yaw (teleop ~80° pulse scale)

Autonomous nav must use continuous /cmd_vel → this mapping, not WASD pulses.

Important (from first continuous-nav run):
  Nav2 RPP rotate-to-heading often emits small intermittent wz with vx=0.
  Mapping those proportionally caused in-place jerking. Pure rotate must be
  decisive (|x| ≥ PURE_ROTATE_STICK). Arc trims while translating stay soft.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Sequence

# Measured forward stick magnitude → body speed (m/s). First entry is stall.
_FWD_STICK_TO_MPS: tuple[tuple[float, float], ...] = (
    (0.10, 0.00),
    (0.20, 0.04),
    (0.30, 0.08),
    (0.40, 0.17),
    (0.50, 0.24),
    (0.60, 0.30),
    (0.70, 0.38),
    (0.80, 0.48),
    (0.90, 0.53),
    (1.00, 0.56),
)

MIN_FWD_STICK = 0.20
# Arc / combined motion yaw assist (while also translating).
MIN_TURN_STICK = 0.30
TURN_COMMIT_RPS = 0.18
# In-place tank turn (vx≈0): commit enough stick to overcome static friction.
# Was 0.45 — RPP rotate-to-heading emits ~0.22–0.35 (and PP often ~0.05–0.14
# when vx is crushed), which mapped to stick≈0.14 and never broke static friction
# (nav-20260826-073411: tiny wz, rover did not turn).
# Keep pure heading turns slower than manual full-stick rotation while still
# clearing the measured static-friction region.
PURE_ROTATE_STICK = 0.55
PURE_ROTATE_COMMIT_RPS = 0.10
# While translating: cap yaw trim so forward+drive doesn't arc into circles.
ARC_MAX_STICK = 0.20
ARC_WZ_VX_RATIO = 0.42  # max |wz| (rad/s) ≈ ratio × |vx| (m/s)


@dataclass(frozen=True)
class DriveLimits:
    """Software clamps for Twist → Pi stick."""

    max_linear_mps: float = 0.35
    max_angular_rps: float = 0.80
    min_linear_mps: float = 0.04
    min_angular_rps: float = 0.05
    invert_angular: bool = False
    linear_deadband_mps: float = 0.02
    angular_deadband_rps: float = 0.03


def _lerp_table(x: float, table: Sequence[tuple[float, float]]) -> float:
    """Piecewise-linear interpolate y for x using sorted (x, y) samples."""
    if not table:
        return 0.0
    if x <= table[0][0]:
        return table[0][1]
    if x >= table[-1][0]:
        return table[-1][1]
    for i in range(1, len(table)):
        x0, y0 = table[i - 1]
        x1, y1 = table[i]
        if x <= x1:
            t = 0.0 if x1 <= x0 else (x - x0) / (x1 - x0)
            return y0 + t * (y1 - y0)
    return table[-1][1]


def stick_y_to_speed_mps(abs_y: float) -> float:
    """Map |drive.y| ∈ [0,1] → forward speed (m/s)."""
    return _lerp_table(max(0.0, min(1.0, abs_y)), _FWD_STICK_TO_MPS)


def speed_mps_to_stick_y(speed_mps: float) -> float:
    """Inverse of stick_y_to_speed_mps for |y| (always ≥ 0)."""
    speed = max(0.0, float(speed_mps))
    inv = tuple((mps, stick) for stick, mps in _FWD_STICK_TO_MPS)
    useful = tuple(p for p in inv if p[0] > 0.0)
    if speed <= 0.0:
        return 0.0
    stick = _lerp_table(speed, useful)
    return max(MIN_FWD_STICK, min(1.0, stick)) if speed >= useful[0][0] else 0.0


def clamp_twist(
    vx: float,
    wz: float,
    limits: DriveLimits | None = None,
) -> tuple[float, float]:
    """Saturate Twist to configured limits; zero tiny noise."""
    lim = limits or DriveLimits()
    vx = float(vx)
    wz = float(wz)
    if abs(vx) < lim.linear_deadband_mps:
        vx = 0.0
    if abs(wz) < lim.angular_deadband_rps:
        wz = 0.0
    vx = max(-lim.max_linear_mps, min(lim.max_linear_mps, vx))
    wz = max(-lim.max_angular_rps, min(lim.max_angular_rps, wz))
    return vx, wz


def limit_arc_twist(
    vx: float,
    wz: float,
    limits: DriveLimits | None = None,
) -> tuple[float, float]:
    """Skid-steer cannot strafe — heavy wz while vx>0 causes tight overturn arcs."""
    lim = limits or DriveLimits()
    vx, wz = float(vx), float(wz)
    if abs(vx) >= lim.min_linear_mps and abs(wz) > 0.0:
        cap = max(lim.min_angular_rps, abs(vx) * ARC_WZ_VX_RATIO)
        wz = max(-cap, min(cap, wz))
    return vx, wz


def twist_to_pi_drive(
    vx: float,
    wz: float,
    *,
    limits: DriveLimits | None = None,
    allow_reverse: bool = False,
    pure_rotate_stick: float | None = None,
) -> dict[str, float]:
    """Map body Twist → Pi {x, y} in [-1, 1].

    Skid-steer only (linear.y ignored).
    - Forward: calibrated speed table → −y
    - Pure rotate (vx≈0): decisive |x| (PURE_ROTATE_STICK) by default
    - Arc (vx+wz): softer proportional yaw assist

    ``pure_rotate_stick``: override the in-place |x| floor (sim uses a lower
    value while fine-aligning so latency cannot chatter at PURE_ROTATE_STICK).
    """
    lim = limits or DriveLimits()
    vx, wz = clamp_twist(vx, wz, lim)
    rotate_floor = (
        PURE_ROTATE_STICK if pure_rotate_stick is None else float(pure_rotate_stick)
    )

    # Large yaw + tiny forward → pure rotate (avoid scrubbing).
    if abs(vx) > 0.0 and abs(wz) > 0.0:
        if abs(wz) >= 0.40 and abs(vx) < 0.10:
            vx = 0.0

    y = 0.0
    if abs(vx) >= lim.min_linear_mps:
        mag = speed_mps_to_stick_y(abs(vx))
        if vx > 0.0:
            y = -mag
        elif allow_reverse:
            y = mag
        else:
            y = 0.0

    x = 0.0
    if abs(wz) >= lim.angular_deadband_rps:
        frac = abs(wz) / max(lim.max_angular_rps, 1e-3)
        pure_rotate = abs(vx) < lim.min_linear_mps
        if pure_rotate:
            # Any surviving pure-yaw command must be physically actionable.
            # Preserve its direction while raising tiny Nav2 commands to the
            # lowest angular speed that clears rover static friction.
            effective_wz = math.copysign(
                max(abs(wz), PURE_ROTATE_COMMIT_RPS),
                wz,
            )
            frac = abs(effective_wz) / max(lim.max_angular_rps, 1e-3)
            mag = max(rotate_floor, min(1.0, frac))
        elif abs(wz) >= TURN_COMMIT_RPS:
            mag = max(0.10, min(ARC_MAX_STICK, frac * 0.38))
        else:
            mag = max(0.06, min(0.15, frac * 0.32))
        # +wz = CCW/left → negative drive.x
        signed = -mag if wz > 0.0 else mag
        if lim.invert_angular:
            signed = -signed
        x = signed

    return {"x": round(x, 3), "y": round(y, 3)}


def pi_drive_to_twist_approx(
    drive: dict[str, float],
    *,
    limits: DriveLimits | None = None,
) -> tuple[float, float]:
    """Approximate inverse for logging / sim."""
    lim = limits or DriveLimits()
    x = float(drive.get("x", 0.0))
    y = float(drive.get("y", 0.0))
    vx = stick_y_to_speed_mps(abs(y))
    if y > 0.0:
        vx = -vx
    wz = 0.0
    if abs(x) >= 0.02:
        wz = -x * lim.max_angular_rps
        if lim.invert_angular:
            wz = -wz
    return vx, wz


def limit_accel(
    prev_vx: float,
    prev_wz: float,
    vx: float,
    wz: float,
    *,
    dt: float,
    max_linear_accel: float = 0.40,
    max_angular_accel: float = 0.90,
    bypass_angular: bool = False,
) -> tuple[float, float]:
    """First-order accel/decel limiting.

    ``bypass_angular``: used for pure rotate so decisive tank turns are not
    crushed by a slow ramp that never finishes before the next cmd_vel gap.
    """
    dt = max(1e-3, float(dt))
    dv = max_linear_accel * dt
    out_vx = prev_vx + max(-dv, min(dv, vx - prev_vx))
    if bypass_angular:
        out_wz = wz
    else:
        dw = max_angular_accel * dt
        out_wz = prev_wz + max(-dw, min(dw, wz - prev_wz))
    return out_vx, out_wz


def wrap_angle(angle: float) -> float:
    return math.atan2(math.sin(angle), math.cos(angle))
