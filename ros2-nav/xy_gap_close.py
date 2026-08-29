#!/usr/bin/env python3
"""Final XY gap-close via analog forward/yaw-to-bearing pulses + settle.

Runs before goal-yaw pulse align. Stops forward when past the goal (fwd<=0)
so we do not drive through the mark. Caller should Nav2-replan if done but
still far (pulse cap / overshoot).
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

from drive_interface import wrap_angle

try:
    from nav_imu import (
        forward_decel_hint as _fwd_decel_hint,
        yaw_pulse_early_stop as _yaw_imu_early_stop,
    )
except ImportError:  # pragma: no cover
    _fwd_decel_hint = None  # type: ignore[assignment]
    _yaw_imu_early_stop = None  # type: ignore[assignment]


@dataclass(frozen=True)
class XyGapCloseConfig:
    xy_tol_m: float = 0.08
    settle_s: float = 3.0
    forward_stick: float = 0.42
    face_stick: float = 0.36
    pulse_small_s: float = 0.12
    pulse_mid_s: float = 0.20
    pulse_large_s: float = 0.28
    # Soft last push — old 0.32s@0.52 drove through the mark.
    final_dist_m: float = 0.20
    final_forward_stick: float = 0.40
    final_forward_pulse_s: float = 0.16
    face_goal_tol_rad: float = math.radians(18.0)
    face_near_tol_rad: float = math.radians(24.0)
    face_near_dist_m: float = 0.26
    overshoot_pulse_s: float = 0.04
    max_pulses: int = 28
    no_progress_pulses: int = 3
    # If best was near and we drifted farther after a pulse → overshoot.
    overshoot_slack_m: float = 0.05
    invert_angular: bool = False


@dataclass
class XyGapCloseState:
    active: bool = False
    phase: str = "idle"  # idle | settle | pulse
    until: float = 0.0
    drive: dict[str, float] = field(default_factory=lambda: {"x": 0.0, "y": 0.0})
    settle_s: float = 3.0
    approach_sign: int = 0
    pulse_count: int = 0
    best_dist_m: float = float("inf")
    stale_pulses: int = 0
    final_push_used: bool = False
    last_was_forward: bool = False
    note: str = ""
    # "ok" | "failed" — failed means caller should Nav2-replan
    result: str = "ok"
    # Soft IMU face-turn early-stop (same idea as yaw_pulse_align).
    err_at_pulse_start: float = 0.0
    imu_yaw_integ0: float = 0.0


@dataclass(frozen=True)
class XyImuAssist:
    ok: bool = False
    gy: float = 0.0
    gz: float = 0.0
    integrated_yaw_rad: float = 0.0


def dist_xy(px: float, py: float, tx: float, ty: float) -> float:
    return math.hypot(tx - px, ty - py)


def bearing_to_point(px: float, py: float, tx: float, ty: float) -> float:
    return math.atan2(ty - py, tx - px)


def body_fwd_to_goal(px: float, py: float, pyaw: float, tx: float, ty: float) -> float:
    dx = tx - px
    dy = ty - py
    return dx * math.cos(pyaw) + dy * math.sin(pyaw)


def _stick_for_bearing_err(bearing_err: float, stick: float, invert: bool) -> float:
    signed = -stick if bearing_err > 0.0 else stick
    if invert:
        signed = -signed
    return signed


def _forward_pulse_s(fwd_m: float, dist_m: float, cfg: XyGapCloseConfig) -> float:
    # Cap duration by remaining forward distance (~0.4 m/s at stick≈0.4).
    by_dist = max(0.08, min(cfg.pulse_large_s, fwd_m / 0.45))
    if dist_m <= cfg.final_dist_m:
        return min(cfg.final_forward_pulse_s, by_dist)
    if dist_m >= 0.35:
        return min(cfg.pulse_large_s, by_dist)
    return min(cfg.pulse_mid_s, by_dist)


def _forward_stick(dist_m: float, cfg: XyGapCloseConfig) -> float:
    if dist_m <= cfg.final_dist_m:
        return cfg.final_forward_stick
    return cfg.forward_stick


def _face_tol(dist_m: float, cfg: XyGapCloseConfig) -> float:
    if dist_m <= cfg.face_near_dist_m:
        return cfg.face_near_tol_rad
    return cfg.face_goal_tol_rad


def _mark_done(
    state: XyGapCloseState, dist: float, note: str, *, result: str = "ok"
) -> tuple[dict[str, float], XyGapCloseState, bool]:
    zero = {"x": 0.0, "y": 0.0}
    state.active = False
    state.phase = "idle"
    state.drive = dict(zero)
    state.result = result
    state.note = f"{note} dist={dist:.3f}m"
    return zero, state, True


def tick_xy_gap_close(
    px: float,
    py: float,
    pyaw: float,
    tx: float,
    ty: float,
    state: XyGapCloseState,
    cfg: XyGapCloseConfig,
    now: float,
    *,
    imu: XyImuAssist | None = None,
) -> tuple[dict[str, float], XyGapCloseState, bool]:
    """One tick: return (drive, state, done).

    Optional ``imu`` may early-end face/forward pulses. SLAM still decides done.
    """
    zero = {"x": 0.0, "y": 0.0}
    dist = dist_xy(px, py, tx, ty)
    fwd = body_fwd_to_goal(px, py, pyaw, tx, ty)

    if state.best_dist_m == float("inf") or dist < state.best_dist_m - 0.006:
        state.best_dist_m = dist
        state.stale_pulses = 0
    elif state.phase == "settle" and state.pulse_count > 0:
        state.stale_pulses += 1

    if dist <= cfg.xy_tol_m:
        return _mark_done(state, dist, "xy closed")

    # Drove through the mark: goal is behind us.
    if fwd < 0.0 and state.pulse_count > 0:
        if dist <= cfg.xy_tol_m + 0.06:
            return _mark_done(state, dist, "xy closed (passed mark)")
        return _mark_done(
            state, dist, f"xy overshoot fwd={fwd:.3f}m", result="failed"
        )

    # After a forward pulse, distance got worse → overshoot / wrong way.
    if (
        state.phase == "settle"
        and state.last_was_forward
        and state.best_dist_m < 0.30
        and dist > state.best_dist_m + cfg.overshoot_slack_m
    ):
        if state.best_dist_m <= cfg.xy_tol_m + 0.04:
            return _mark_done(
                state, dist, f"xy closed (overshoot best={state.best_dist_m:.3f}m)"
            )
        return _mark_done(
            state,
            dist,
            f"xy overshoot best={state.best_dist_m:.3f}m",
            result="failed",
        )

    if (
        state.stale_pulses >= cfg.no_progress_pulses
        and state.best_dist_m <= cfg.xy_tol_m + 0.03
    ):
        return _mark_done(
            state,
            dist,
            f"xy closed (no progress best={state.best_dist_m:.3f}m)",
        )

    if state.pulse_count >= cfg.max_pulses:
        if dist <= cfg.xy_tol_m + 0.06:
            return _mark_done(state, dist, "xy closed (pulse cap near)")
        return _mark_done(state, dist, "xy pulse cap far", result="failed")

    if not state.active:
        state.active = True
        state.phase = "settle"
        state.settle_s = cfg.settle_s
        state.until = now + state.settle_s
        state.best_dist_m = dist
        state.result = "ok"
        state.note = f"xy gap-close start dist={dist:.3f}m"
        return zero, state, False

    if now < state.until:
        if state.phase == "pulse":
            # Abort forward mid-pulse if we already passed the goal.
            if state.last_was_forward and fwd < 0.0:
                state.phase = "settle"
                state.until = now
                state.drive = dict(zero)
                state.note = f"xy abort forward (passed) dist={dist:.3f}m"
                return zero, state, False
            # Soft IMU: cut face-turn early when gyro Δyaw covers most of err.
            if (
                not state.last_was_forward
                and imu is not None
                and imu.ok
                and _yaw_imu_early_stop is not None
                and state.approach_sign != 0
            ):
                delta = imu.integrated_yaw_rad - state.imu_yaw_integ0
                if _yaw_imu_early_stop(
                    approach_sign=state.approach_sign,
                    err_at_pulse_start=state.err_at_pulse_start,
                    integrated_yaw_rad=delta,
                    live_gz=imu.gz,
                    yaw_tol_rad=cfg.face_goal_tol_rad,
                ):
                    state.phase = "settle"
                    state.until = now + state.settle_s
                    state.drive = dict(zero)
                    state.note = (
                        f"xy imu face early-stop Δ={math.degrees(delta):+.1f}° "
                        f"dist={dist:.3f}m"
                    )
                    return zero, state, False
            # Soft IMU: cut forward early on decel hint when already close.
            if (
                state.last_was_forward
                and imu is not None
                and imu.ok
                and _fwd_decel_hint is not None
                and dist <= cfg.final_dist_m
                and fwd <= cfg.xy_tol_m * 2.5
                and _fwd_decel_hint(live_gy=imu.gy, commanded_forward=True)
            ):
                state.phase = "settle"
                state.until = now + state.settle_s
                state.drive = dict(zero)
                state.note = (
                    f"xy imu fwd early-stop gy={imu.gy:+.3f} "
                    f"fwd={fwd:.3f}m dist={dist:.3f}m"
                )
                return zero, state, False
            state.note = f"xy pulse #{state.pulse_count} dist={dist:.3f}m"
            return dict(state.drive), state, False
        state.note = f"xy settle dist={dist:.3f}m"
        return zero, state, False

    if state.phase == "pulse":
        state.phase = "settle"
        state.until = now + state.settle_s
        state.drive = dict(zero)
        state.note = f"xy post-pulse settle dist={dist:.3f}m"
        return zero, state, False

    bearing = bearing_to_point(px, py, tx, ty)
    bearing_err = wrap_angle(bearing - pyaw)
    face_tol = _face_tol(dist, cfg)
    allow_overshoot = dist >= cfg.face_near_dist_m

    if abs(bearing_err) > face_tol:
        if state.approach_sign == 0:
            state.approach_sign = 1 if bearing_err > 0.0 else -1
        overshot = (state.approach_sign > 0 and bearing_err < 0.0) or (
            state.approach_sign < 0 and bearing_err > 0.0
        )
        if overshot and allow_overshoot and abs(bearing_err) < math.radians(35.0):
            pulse_s = cfg.overshoot_pulse_s
            mode = "face_overshoot"
        else:
            pulse_s = cfg.pulse_mid_s if dist <= cfg.final_dist_m else cfg.pulse_small_s
            mode = "face"
        stick_x = _stick_for_bearing_err(bearing_err, cfg.face_stick, cfg.invert_angular)
        state.drive = {"x": stick_x, "y": 0.0}
        state.phase = "pulse"
        state.until = now + pulse_s
        state.pulse_count += 1
        state.last_was_forward = False
        state.err_at_pulse_start = bearing_err
        state.imu_yaw_integ0 = (
            float(imu.integrated_yaw_rad) if imu is not None and imu.ok else 0.0
        )
        state.note = (
            f"xy {mode} {pulse_s:.2f}s bear_err={math.degrees(bearing_err):+.1f}° "
            f"dist={dist:.3f}m"
        )
        return dict(state.drive), state, False

    state.approach_sign = 0

    # Goal behind / beside — face first; do not reverse-drive through.
    if fwd <= max(0.03, cfg.xy_tol_m * 0.4):
        state.note = f"xy waiting bearing dist={dist:.3f}m fwd={fwd:.3f}m"
        state.phase = "settle"
        state.until = now + min(cfg.settle_s, 1.0)
        state.last_was_forward = False
        return zero, state, False

    pulse_s = _forward_pulse_s(fwd, dist, cfg)
    stick_y = _forward_stick(dist, cfg)
    mode = "final" if dist <= cfg.final_dist_m and not state.final_push_used else "forward"
    if mode == "final":
        state.final_push_used = True
    state.drive = {"x": 0.0, "y": -stick_y}
    state.phase = "pulse"
    state.until = now + pulse_s
    state.pulse_count += 1
    state.last_was_forward = True
    state.note = f"xy {mode} {pulse_s:.2f}s stick={stick_y:.2f} fwd={fwd:.3f}m dist={dist:.3f}m"
    return dict(state.drive), state, False
