"""Skid-steer lateral moves via turn 90° → drive → turn back 90°.

From the rover's perspective, a body-frame lateral offset is closed by:
  - move right: turn right 90°, drive forward by |offset|, turn left 90°
  - move left:  turn left 90°,  drive forward by |offset|, turn right 90°

Quarter turns are done in small A/D steps; we stop once ~90° is reached and
correct modest overshoot instead of hunting forever. XY gaps are closed one axis
at a time (forward, then lateral, then goal yaw). Small skid on the orthogonal
axis after each leg is tolerated up to ``skid_tol_m``.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any

QUARTER_TURN_RAD = math.pi / 2
DEFAULT_XY_TOL_M = 0.08
DEFAULT_YAW_TOL_RAD = 0.12
DEFAULT_SKID_TOL_M = 0.06
DEFAULT_TURN_DONE_TOL_RAD = math.radians(8.0)
DEFAULT_TURN_OVERSHOOT_TOL_RAD = math.radians(12.0)
DEFAULT_DRIVE_TOL_M = 0.02


def wrap_angle(angle: float) -> float:
    return math.atan2(math.sin(angle), math.cos(angle))


def body_frame_error(
    px: float, py: float, pyaw: float, tx: float, ty: float
) -> tuple[float, float, float, float]:
    """Return (dist_m, fwd_m, left_m, heading_err_rad) in rover body frame."""
    dx = tx - px
    dy = ty - py
    dist = math.hypot(dx, dy)
    fwd = dx * math.cos(pyaw) + dy * math.sin(pyaw)
    left = -dx * math.sin(pyaw) + dy * math.cos(pyaw)
    heading_err = wrap_angle(math.atan2(dy, dx) - pyaw) if dist > 1e-4 else 0.0
    return dist, fwd, left, heading_err


def turn_progress_rad(start_yaw: float, current_yaw: float, direction: int) -> float:
    """Rotation progress toward a quarter turn.

    ``direction``: +1 = left/ccw, -1 = right/cw. Result is in [0, π].
    """
    delta = wrap_angle(current_yaw - start_yaw)
    progress = delta if direction > 0 else -delta
    return max(0.0, progress)


def drive_progress_m(
    origin_xy: tuple[float, float], px: float, py: float, yaw: float
) -> float:
    """Forward distance traveled from ``origin_xy`` along current heading."""
    dx = px - origin_xy[0]
    dy = py - origin_xy[1]
    return dx * math.cos(yaw) + dy * math.sin(yaw)


def _turn_keys(direction: int, *, correcting: bool) -> list[str]:
    d = -direction if correcting else direction
    return ["a"] if d > 0 else ["d"]


@dataclass
class GapCloseState:
    phase: str = "pick"  # pick|fwd|lat_t1|lat_drive|lat_t2|yaw|done
    turn_start_yaw: float = 0.0
    turn_dir: int = 0  # +1 left/ccw, -1 right/cw
    lat_restore_dir: int = 0
    drive_origin: tuple[float, float] = (0.0, 0.0)
    drive_target_m: float = 0.0
    fwd_key: str = ""
    correcting: bool = False
    meta: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class GapCloseConfig:
    xy_tol_m: float = DEFAULT_XY_TOL_M
    yaw_tol_rad: float = DEFAULT_YAW_TOL_RAD
    skid_tol_m: float = DEFAULT_SKID_TOL_M
    turn_done_tol_rad: float = DEFAULT_TURN_DONE_TOL_RAD
    turn_overshoot_tol_rad: float = DEFAULT_TURN_OVERSHOOT_TOL_RAD
    drive_tol_m: float = DEFAULT_DRIVE_TOL_M
    allow_reverse: bool = True


@dataclass(frozen=True)
class GapCloseStep:
    keys: list[str]
    done: bool
    state: GapCloseState
    phase: str
    note: str = ""


def gap_close_accepted(
    px: float,
    py: float,
    pyaw: float,
    tx: float,
    ty: float,
    tyaw: float,
    *,
    cfg: GapCloseConfig | None = None,
) -> bool:
    cfg = cfg or GapCloseConfig()
    _, fwd, left, _ = body_frame_error(px, py, pyaw, tx, ty)
    yaw_err = wrap_angle(tyaw - pyaw)
    return (
        abs(fwd) <= cfg.xy_tol_m
        and abs(left) <= cfg.xy_tol_m
        and abs(yaw_err) <= cfg.yaw_tol_rad
    )


def next_gap_close_step(
    px: float,
    py: float,
    pyaw: float,
    tx: float,
    ty: float,
    tyaw: float,
    state: GapCloseState | None = None,
    *,
    cfg: GapCloseConfig | None = None,
) -> GapCloseStep:
    """One measured WASD action toward closing body-frame XY + goal yaw."""
    cfg = cfg or GapCloseConfig()
    st = state or GapCloseState()

    if gap_close_accepted(px, py, pyaw, tx, ty, tyaw, cfg=cfg):
        st.phase = "done"
        return GapCloseStep([], True, st, "done", "within tolerance")

    _, fwd, left, _ = body_frame_error(px, py, pyaw, tx, ty)
    yaw_err = wrap_angle(tyaw - pyaw)

    if st.phase == "pick":
        return _pick_axis(px, py, pyaw, fwd, left, yaw_err, st, cfg)

    if st.phase == "fwd":
        if abs(fwd) <= cfg.xy_tol_m:
            st.phase = "pick"
            return GapCloseStep([], False, st, "pick", "forward leg done")
        return GapCloseStep([st.fwd_key], False, st, "fwd", f"close fwd {fwd:+.3f}m")

    if st.phase in ("lat_t1", "lat_t2"):
        return _quarter_turn_step(px, py, pyaw, st, cfg)

    if st.phase == "lat_drive":
        traveled = drive_progress_m(st.drive_origin, px, py, pyaw)
        if traveled >= st.drive_target_m - cfg.drive_tol_m:
            st.phase = "lat_t2"
            st.turn_start_yaw = pyaw
            st.turn_dir = st.lat_restore_dir
            st.correcting = False
            return GapCloseStep([], False, st, "lat_t2", "lateral drive done")
        return GapCloseStep(
            ["w"],
            False,
            st,
            "lat_drive",
            f"lateral drive {traveled:.3f}/{st.drive_target_m:.3f}m",
        )

    if st.phase == "yaw":
        if abs(yaw_err) <= cfg.yaw_tol_rad:
            st.phase = "pick"
            return GapCloseStep([], False, st, "pick", "yaw leg done")
        key = "a" if yaw_err > 0 else "d"
        return GapCloseStep([key], False, st, "yaw", f"yaw err {math.degrees(yaw_err):+.1f}°")

    st.phase = "pick"
    return _pick_axis(px, py, pyaw, fwd, left, yaw_err, st, cfg)


def _pick_axis(
    px: float,
    py: float,
    pyaw: float,
    fwd: float,
    left: float,
    yaw_err: float,
    st: GapCloseState,
    cfg: GapCloseConfig,
) -> GapCloseStep:
    """Choose the next axis to close: forward, lateral combo, or goal yaw."""
    fwd_out = abs(fwd) > cfg.xy_tol_m
    left_out = abs(left) > cfg.xy_tol_m
    yaw_out = abs(yaw_err) > cfg.yaw_tol_rad

    if not fwd_out and not left_out and not yaw_out:
        st.phase = "done"
        return GapCloseStep([], True, st, "done", "within tolerance")

    # Close the larger XY residual first; tolerate skid on the other axis.
    if fwd_out and (abs(fwd) >= abs(left) or abs(left) <= cfg.skid_tol_m):
        if fwd > 0 or cfg.allow_reverse:
            st.phase = "fwd"
            st.fwd_key = "w" if fwd > 0 else "s"
            return GapCloseStep([st.fwd_key], False, st, "fwd", f"close fwd {fwd:+.3f}m")
        # No reverse sensor — face backward with quarter turns, then drive.
        st.phase = "lat_t1"
        st.turn_dir = 1 if fwd > 0 else -1
        st.lat_restore_dir = -st.turn_dir
        st.drive_target_m = abs(fwd)
        st.turn_start_yaw = pyaw
        st.correcting = False
        st.meta = {"lateral_for": "forward"}
        return _quarter_turn_step(px, py, pyaw, st, cfg)

    if left_out:
        st.phase = "lat_t1"
        st.turn_dir = 1 if left > 0 else -1
        st.lat_restore_dir = -st.turn_dir
        st.drive_target_m = abs(left)
        st.turn_start_yaw = pyaw
        st.correcting = False
        st.meta = {"lateral_for": "left"}
        side = "left" if left > 0 else "right"
        return _quarter_turn_step(
            px, py, pyaw, st, cfg, note=f"lateral {side} {abs(left):.3f}m"
        )

    if yaw_out:
        st.phase = "yaw"
        key = "a" if yaw_err > 0 else "d"
        return GapCloseStep(
            [key], False, st, "yaw", f"yaw err {math.degrees(yaw_err):+.1f}°"
        )

    st.phase = "done"
    return GapCloseStep([], True, st, "done", "within tolerance")


def _quarter_turn_step(
    px: float,
    py: float,
    pyaw: float,
    st: GapCloseState,
    cfg: GapCloseConfig,
    *,
    note: str = "",
) -> GapCloseStep:
    progress = turn_progress_rad(st.turn_start_yaw, pyaw, st.turn_dir)
    target = QUARTER_TURN_RAD

    if progress > target + cfg.turn_overshoot_tol_rad:
        st.correcting = True
        keys = _turn_keys(st.turn_dir, correcting=True)
        return GapCloseStep(
            keys, False, st, st.phase, f"overshoot {math.degrees(progress):.1f}°"
        )

    if progress >= target - cfg.turn_done_tol_rad:
        st.correcting = False
        if st.phase == "lat_t1":
            st.phase = "lat_drive"
            st.drive_origin = (px, py)
            msg = note or "turn1 done → drive"
            return GapCloseStep([], False, st, "lat_drive", msg)
        if st.phase == "lat_t2":
            st.phase = "pick"
            st.meta = {}
            return GapCloseStep([], False, st, "pick", "turn2 done → remeasure")

    st.correcting = False
    keys = _turn_keys(st.turn_dir, correcting=False)
    msg = note or f"turn {math.degrees(progress):.1f}°/{math.degrees(target):.0f}°"
    return GapCloseStep(keys, False, st, st.phase, msg)


def keys_to_twist(keys: list[str]) -> tuple[float, float]:
    """Rough cmd_vel for sim / logging (linear m/s, angular rad/s)."""
    linear = 0.0
    angular = 0.0
    if "w" in keys:
        linear = 0.12
    if "s" in keys:
        linear = -0.12
    if "a" in keys:
        angular = 0.35
    if "d" in keys:
        angular = -0.35
    return linear, angular
