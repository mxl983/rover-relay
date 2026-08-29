#!/usr/bin/env python3
"""Goal-yaw fine alignment via timed analog stick pulses + SLAM settle.

Direction is locked to the initial shortest-path sign. We never fire reverse
"overshoot" micro-pulses — those look like sign flips and undo progress.
When heading crosses the goal, we commit (or re-lock) after settle.

Always turns the shortest way: wrap_angle(goal_yaw - current_yaw).
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

from drive_interface import wrap_angle

try:
    from nav_imu import yaw_pulse_early_stop as _yaw_imu_early_stop
except ImportError:  # pragma: no cover - same package dir in container
    _yaw_imu_early_stop = None  # type: ignore[assignment]


@dataclass(frozen=True)
class YawPulseAlignConfig:
    xy_handoff_m: float = 0.14
    # Take over from Nav2 early when still far in yaw but near XY (avoids spin-in-place skid).
    xy_handoff_large_yaw_m: float = 0.40
    large_handoff_err_rad: float = math.radians(40.0)
    yaw_tol_rad: float = math.radians(10.0)  # ~10° — stop; no fine taps below this
    settle_s: float = 3.0
    invert_angular: bool = False
    # Rough deg/s at stick=1.0 for pulse duration cap (measured ~90–120°/s teleop burst).
    turn_rate_deg_per_s: float = 100.0
    max_turn_fraction: float = 0.50  # never pulse more than this fraction of |err|
    # When already at tight XY, avoid tiny taps that skid without helping.
    tight_xy_m: float = 0.14
    tight_xy_min_err_rad: float = math.radians(12.0)


@dataclass
class YawPulseAlignState:
    active: bool = False
    phase: str = "idle"  # idle | settle | pulse
    until: float = 0.0
    stick_x: float = 0.0
    settle_s: float = 3.0
    approach_sign: int = 0  # +1 = CCW needed, -1 = CW — locked until commit/re-lock
    pulse_count: int = 0
    note: str = ""
    meta: dict = field(default_factory=dict)
    # Soft IMU: SLAM still decides done; gyro only shortens pulses.
    err_at_pulse_start: float = 0.0
    imu_yaw_integ0: float = 0.0


@dataclass(frozen=True)
class YawImuAssist:
    """Optional gyro hint for mid-pulse early-stop. Missing/ok=False = no effect."""

    ok: bool = False
    gz: float = 0.0
    integrated_yaw_rad: float = 0.0


def goal_yaw_error(current_yaw: float, goal_yaw: float) -> float:
    """Shortest turn to goal heading (rad). + = CCW / left."""
    return wrap_angle(float(goal_yaw) - float(current_yaw))


def should_handoff_xy(dist_xy: float, cfg: YawPulseAlignConfig) -> bool:
    return dist_xy <= cfg.xy_handoff_m


def should_begin_yaw_align(
    dist_xy: float,
    yaw_err_rad: float,
    cfg: YawPulseAlignConfig,
) -> bool:
    if abs(yaw_err_rad) <= cfg.yaw_tol_rad:
        return False
    if dist_xy <= cfg.xy_handoff_m:
        return True
    return (
        dist_xy <= cfg.xy_handoff_large_yaw_m
        and abs(yaw_err_rad) >= cfg.large_handoff_err_rad
    )


def _pulse_profile(
    abs_err: float, cfg: YawPulseAlignConfig
) -> tuple[float, float, float, str]:
    """Return (pulse_s, stick_mag, settle_s, mode). No reverse/overshoot tier."""
    deg = math.degrees(abs_err)
    if deg >= 70.0:
        pulse_s, stick, settle_s, mode = 0.55, 0.50, 1.2, "xlarge"
    elif deg >= 40.0:
        pulse_s, stick, settle_s, mode = 0.40, 0.44, 1.4, "large"
    elif deg >= 20.0:
        pulse_s, stick, settle_s, mode = 0.26, 0.36, 1.7, "mid"
    else:
        # Close to the goal: short low-power pulse, then let SLAM settle.
        pulse_s, stick, settle_s, mode = 0.15, 0.30, 2.0, "final"

    rate = math.radians(cfg.turn_rate_deg_per_s)
    if rate > 1e-3 and abs_err > 1e-3:
        max_s = (abs_err * cfg.max_turn_fraction) / (stick * rate)
        pulse_s = min(pulse_s, max(0.08, max_s))

    return pulse_s, stick, settle_s, mode


def _stick_for_approach(approach_sign: int, stick_mag: float, cfg: YawPulseAlignConfig) -> float:
    """Stick follows locked approach_sign, not a noisy instantaneous error sign."""
    stick = -stick_mag if approach_sign > 0 else stick_mag
    if cfg.invert_angular:
        stick = -stick
    return stick


def tick_yaw_pulse_align(
    current_yaw: float,
    goal_yaw: float,
    state: YawPulseAlignState,
    cfg: YawPulseAlignConfig,
    now: float,
    *,
    dist_xy: float | None = None,
    imu: YawImuAssist | None = None,
) -> tuple[dict[str, float], YawPulseAlignState, bool]:
    """One tick: return (drive, state, done).

    ``imu`` may early-end a pulse when integrated gz covers most of the error.
    Goal success still requires SLAM ``current_yaw`` within ``yaw_tol_rad``.
    """
    err = goal_yaw_error(current_yaw, goal_yaw)
    zero = {"x": 0.0, "y": 0.0}
    commit = max(cfg.yaw_tol_rad, cfg.tight_xy_min_err_rad)

    if abs(err) <= cfg.yaw_tol_rad:
        state.active = False
        state.phase = "idle"
        state.until = 0.0
        state.stick_x = 0.0
        state.approach_sign = 0
        state.pulse_count = 0
        state.note = f"yaw aligned err={math.degrees(err):+.1f}°"
        return zero, state, True

    if dist_xy is not None and dist_xy <= cfg.tight_xy_m and abs(err) <= commit:
        state.active = False
        state.phase = "idle"
        state.note = (
            f"yaw commit tight xy dist={dist_xy:.3f}m err={math.degrees(err):+.1f}°"
        )
        return zero, state, True

    if not state.active:
        state.active = True
        state.phase = "settle"
        state.settle_s = cfg.settle_s
        state.until = now + state.settle_s
        state.approach_sign = 0
        state.pulse_count = 0
        state.note = f"yaw handoff err={math.degrees(err):+.1f}°"
        return zero, state, False

    if now < state.until:
        if state.phase == "pulse":
            if imu is not None and imu.ok and _yaw_imu_early_stop is not None:
                delta = imu.integrated_yaw_rad - state.imu_yaw_integ0
                if _yaw_imu_early_stop(
                    approach_sign=state.approach_sign,
                    err_at_pulse_start=state.err_at_pulse_start,
                    integrated_yaw_rad=delta,
                    live_gz=imu.gz,
                    yaw_tol_rad=cfg.yaw_tol_rad,
                ):
                    state.phase = "settle"
                    state.until = now + state.settle_s
                    state.stick_x = 0.0
                    state.note = (
                        f"yaw imu early-stop Δ={math.degrees(delta):+.1f}° "
                        f"err={math.degrees(err):+.1f}°"
                    )
                    return zero, state, False
            state.note = (
                f"yaw pulse #{state.pulse_count} err={math.degrees(err):+.1f}°"
            )
            return {"x": state.stick_x, "y": 0.0}, state, False
        state.note = f"yaw settle err={math.degrees(err):+.1f}°"
        return zero, state, False

    if state.phase == "pulse":
        state.phase = "settle"
        state.until = now + state.settle_s
        state.stick_x = 0.0
        state.note = f"yaw post-pulse settle err={math.degrees(err):+.1f}°"
        return zero, state, False

    # Lock shortest-path direction once; never reverse-jerk on overshoot.
    if state.approach_sign == 0 and abs(err) > math.radians(1.0):
        state.approach_sign = 1 if err > 0.0 else -1

    crossed = state.approach_sign != 0 and (
        (state.approach_sign > 0 and err < 0.0)
        or (state.approach_sign < 0 and err > 0.0)
    )
    if crossed:
        if abs(err) <= commit:
            state.active = False
            state.phase = "idle"
            state.note = f"yaw commit after cross err={math.degrees(err):+.1f}°"
            return zero, state, True
        # Past the goal but still far (noise / big overshoot) — re-lock, no reverse tap.
        state.approach_sign = 1 if err > 0.0 else -1
        state.phase = "settle"
        state.until = now + min(cfg.settle_s, 2.0)
        state.note = (
            f"yaw re-lock after cross err={math.degrees(err):+.1f}° "
            f"(no reverse pulse)"
        )
        return zero, state, False

    pulse_s, stick_mag, settle_s, mode = _pulse_profile(abs(err), cfg)
    if dist_xy is not None and dist_xy <= cfg.tight_xy_m:
        if mode == "xlarge":
            pulse_s, stick_mag, settle_s, mode = 0.42, 0.44, 1.5, "large_tight_xy"
        elif mode == "large":
            pulse_s, stick_mag, settle_s, mode = 0.32, 0.40, 1.7, "mid_tight_xy"
        elif mode == "final":
            # Last ~20° at tight XY: short pulse or commit.
            if abs(err) <= commit:
                state.active = False
                state.phase = "idle"
                state.note = (
                    f"yaw commit tight xy dist={dist_xy:.3f}m "
                    f"err={math.degrees(err):+.1f}°"
                )
                return zero, state, True
            pulse_s, stick_mag, settle_s, mode = 0.15, 0.30, 2.0, "final_tight_xy"

    state.stick_x = _stick_for_approach(state.approach_sign, stick_mag, cfg)
    state.settle_s = settle_s
    state.phase = "pulse"
    state.until = now + pulse_s
    state.pulse_count += 1
    state.err_at_pulse_start = err
    state.imu_yaw_integ0 = (
        float(imu.integrated_yaw_rad) if imu is not None and imu.ok else 0.0
    )
    state.note = (
        f"yaw {mode} {pulse_s:.2f}s stick={state.stick_x:+.2f} "
        f"err={math.degrees(err):+.1f}°"
    )
    return {"x": state.stick_x, "y": 0.0}, state, False
