"""Phase-2 navigation: follow a planned path as straight segments.

Each segment is executed as rotate-then-drive (never simultaneously):
  1. Align yaw to the segment bearing (small A/D pulses + settle).
  2. Drive forward along that bearing until the segment length is covered.
  3. While driving, fire tiny A/D taps if yaw drifts vs the frozen segment
     heading — kills small errors before a long W rush amplifies them.

Small XY skid during rotation is ignored. Before starting a new segment, if the
rover is farther than ``drift_replan_m`` from where the plan expected, signal
``replan``. Forward motion stops with ``blocked`` when lidar sees an obstacle.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any

from lateral_maneuver import drive_progress_m, wrap_angle


@dataclass(frozen=True)
class Segment:
    x0: float
    y0: float
    x1: float
    y1: float
    heading_rad: float
    length_m: float

    @property
    def heading_deg(self) -> float:
        return math.degrees(self.heading_rad)


@dataclass(frozen=True)
class SegmentNavConfig:
    min_segment_m: float = 0.18
    max_corner_deg: float = 12.0
    align_final_tol_rad: float = math.radians(12.0)
    # ≥30° uses large; <30° uses small. mid kept for callers/env overrides.
    align_pulse_s: float = 0.07
    align_pulse_mid_s: float = 0.22
    align_pulse_large_s: float = 0.30
    align_overshoot_pulse_s: float = 0.035
    align_settle_s: float = 3.0
    drive_tol_m: float = 0.03
    # W hold: live_characterize_ws.py (latch hold, 2026-08-25) → ~0.41 m/s.
    # First cruise_fraction of a segment = one continuous W; last fraction =
    # small observe→tap→settle steps (table: 0.15s≈9cm, 0.20s≈10cm, 0.25s≈13cm).
    drive_cruise_mps: float = 0.40
    drive_cruise_fraction: float = 0.80
    drive_cruise_min_segment_m: float = 0.50
    drive_cruise_max_s: float = 2.5
    drive_pulse_large_s: float = 0.25  # stepper ~13cm
    drive_pulse_mid_s: float = 0.20  # stepper ~10cm
    drive_pulse_s: float = 0.15  # stepper ~9cm (min reliable over HTTP keys)
    drive_pulse_tiny_s: float = 0.15
    drive_settle_s: float = 3.0
    drift_replan_m: float = 0.90
    forward_block_m: float = 0.28
    # Finish phase 2 only when this close to the destination marker.
    goal_arrive_m: float = 0.05
    # Within this radius, stop phase 2 and hand off to fine dock — do NOT replan
    # or spin on leftover Nav2 micro-segments. 0.22 left a 0.34m gap that
    # replanned a stub + midflight spiral (nav-20260825-022547).
    goal_handoff_m: float = 0.40
    # Skip Nav2 crumbs shorter than this — a ~9cm first segment + live aim-at-end
    # spun forever in nav-20260825-021406 (10cm lateral → aim flips to −90°).
    skip_segment_m: float = 0.18
    # Live aim-at-end only when the end is at least this far (else XY noise
    # swings bearing by tens of degrees).
    aim_min_dist_m: float = 0.25
    # Mid-drive micro A/D against frozen segment heading (kill amplified yaw drift).
    midflight_steer_min_rad: float = math.radians(5.0)
    midflight_steer_max_rad: float = math.radians(22.0)
    midflight_steer_pulse_s: float = 0.035
    # Only midflight when enough path remains — at ~0.22m rem, A/D ate ~40s
    # with 3s settles and never advanced (nav-20260825-022547 seg 5).
    midflight_min_remain_m: float = 0.40
    midflight_min_segment_m: float = 0.45
    midflight_steer_settle_s: float = 3.0
    # Kept for callers that still pass legacy fields; unused by align logic.
    align_coarse_tol_rad: float = math.radians(5.0)
    align_coarse_pulse_s: float = 0.06
    align_fine_pulse_s: float = 0.06
    align_micro_pulse_s: float = 0.035
    align_micro_settle_s: float = 3.0


@dataclass
class SegmentNavState:
    segment_index: int = 0
    phase: str = "align"  # align | drive
    approach_sign: int = 0
    drive_origin: tuple[float, float] = (0.0, 0.0)
    note: str = ""
    meta: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class SegmentNavStep:
    keys: list[str]
    done: bool = False
    replan: bool = False
    blocked: bool = False
    state: SegmentNavState | None = None
    phase: str = ""
    note: str = ""
    pulse_on_s: float = 0.0
    pulse_off_s: float = 0.0


def trim_path_from_pose(
    px: float, py: float, path_xy: list[list[float]]
) -> list[list[float]]:
    """Drop path points already passed — start from the nearest on-path index.

    Always snaps the first vertex to the rover pose so phase-2 segment 0 starts
    where we actually are (phase-1 spinning skids off the stale Nav2 waypoint).
    """
    if len(path_xy) < 2:
        return list(path_xy)
    best_i = 0
    best_d = float("inf")
    for i, pt in enumerate(path_xy):
        d = math.hypot(pt[0] - px, pt[1] - py)
        if d < best_d:
            best_d = d
            best_i = i
    trimmed = path_xy[best_i:]
    if len(trimmed) < 2:
        trimmed = path_xy[-2:]
    # Skip the nearest waypoint if it coincides with the rover; keep the rest.
    rest = trimmed[1:] if math.hypot(trimmed[0][0] - px, trimmed[0][1] - py) < 0.03 else trimmed
    if not rest:
        rest = trimmed[-1:]
    return [[float(px), float(py)], *[list(p) for p in rest]]


def segmentize_path(
    path_xy: list[list[float]],
    *,
    min_segment_m: float = 0.12,
    max_corner_deg: float = 12.0,
) -> list[Segment]:
    """Merge nearly-collinear planner samples into straight drive segments.

    Chunks shorter than ``min_segment_m`` are still kept down to a small floor
    so curved Nav2 paths are not discarded (dropping them used to jump phase 2
    to phase 3 while still meters from the goal).
    """
    if len(path_xy) < 2:
        return []
    max_corner_rad = math.radians(max_corner_deg)
    # Keep short corner pieces; only drop pure sample noise.
    keep_floor_m = min(0.05, min_segment_m)
    segments: list[Segment] = []
    i = 0
    n = len(path_xy)
    while i < n - 1:
        x0, y0 = float(path_xy[i][0]), float(path_xy[i][1])
        j = i + 1
        x1, y1 = float(path_xy[j][0]), float(path_xy[j][1])
        heading = math.atan2(y1 - y0, x1 - x0)
        while j + 1 < n:
            xn, yn = float(path_xy[j + 1][0]), float(path_xy[j + 1][1])
            xj, yj = float(path_xy[j][0]), float(path_xy[j][1])
            next_heading = math.atan2(yn - yj, xn - xj)
            if abs(wrap_angle(next_heading - heading)) > max_corner_rad:
                break
            j += 1
            x1, y1 = xn, yn
        length = math.hypot(x1 - x0, y1 - y0)
        if length >= keep_floor_m:
            segments.append(
                Segment(
                    x0=x0,
                    y0=y0,
                    x1=x1,
                    y1=y1,
                    heading_rad=heading,
                    length_m=length,
                )
            )
        i = j

    # Guarantee the path terminus is covered (noise drops / gaps).
    end = path_xy[-1]
    ex, ey = float(end[0]), float(end[1])
    if not segments:
        x0, y0 = float(path_xy[0][0]), float(path_xy[0][1])
        length = math.hypot(ex - x0, ey - y0)
        if length >= keep_floor_m:
            return [
                Segment(
                    x0=x0,
                    y0=y0,
                    x1=ex,
                    y1=ey,
                    heading_rad=math.atan2(ey - y0, ex - x0),
                    length_m=length,
                )
            ]
        return []
    last = segments[-1]
    gap = math.hypot(ex - last.x1, ey - last.y1)
    if gap >= keep_floor_m:
        segments.append(
            Segment(
                x0=last.x1,
                y0=last.y1,
                x1=ex,
                y1=ey,
                heading_rad=math.atan2(ey - last.y1, ex - last.x1),
                length_m=gap,
            )
        )
    return merge_near_collinear_segments(segments, max_heading_deg=max_corner_deg)


def merge_near_collinear_segments(
    segments: list[Segment],
    *,
    max_heading_deg: float = 15.0,
) -> list[Segment]:
    """Collapse consecutive nearly-same-heading pieces into longer drives."""
    if len(segments) < 2:
        return list(segments)
    max_h = math.radians(max_heading_deg)
    out: list[Segment] = []
    cur = segments[0]
    for nxt in segments[1:]:
        if abs(wrap_angle(nxt.heading_rad - cur.heading_rad)) <= max_h:
            x0, y0 = cur.x0, cur.y0
            x1, y1 = nxt.x1, nxt.y1
            length = math.hypot(x1 - x0, y1 - y0)
            if length < 1e-6:
                continue
            cur = Segment(
                x0=x0,
                y0=y0,
                x1=x1,
                y1=y1,
                heading_rad=math.atan2(y1 - y0, x1 - x0),
                length_m=length,
            )
        else:
            out.append(cur)
            cur = nxt
    out.append(cur)
    return out


def segment_start_drift_m(px: float, py: float, seg: Segment) -> float:
    """XY distance from the rover to this segment's planned start."""
    return math.hypot(px - seg.x0, py - seg.y0)


def nearest_segment_index(
    px: float, py: float, segments: list[Segment]
) -> int:
    if not segments:
        return 0
    best_i = 0
    best_d = float("inf")
    for i, seg in enumerate(segments):
        d = segment_start_drift_m(px, py, seg)
        if d < best_d:
            best_d = d
            best_i = i
    return best_i


def segment_along_progress(px: float, py: float, seg: Segment) -> float:
    """Fraction traveled along segment (0 = start, 1 = end)."""
    if seg.length_m < 1e-6:
        return 1.0
    dx = seg.x1 - seg.x0
    dy = seg.y1 - seg.y0
    return ((px - seg.x0) * dx + (py - seg.y0) * dy) / (seg.length_m * seg.length_m)


def segment_drive_pulse_sec(
    remaining_m: float,
    cfg: SegmentNavConfig,
    *,
    goal_dist_m: float | None = None,
    segment_length_m: float | None = None,
) -> float:
    """W hold from remaining distance.

    Long segments (e.g. 1m): cruise the first ~80% as one continuous hold
    timed at ``drive_cruise_mps`` (from W/S latch characterization), then
    finish the last ~20% with small table-based stepper pulses + settle.
    """
    if remaining_m <= cfg.drive_tol_m:
        return 0.0
    scale_m = remaining_m
    if goal_dist_m is not None:
        scale_m = min(remaining_m, max(0.0, goal_dist_m))

    seg_len = float(segment_length_m) if segment_length_m is not None else 0.0
    use_cruise = (
        seg_len >= cfg.drive_cruise_min_segment_m
        and cfg.drive_cruise_fraction > 0.0
        and cfg.drive_cruise_mps > 1e-3
    )
    if use_cruise:
        # Last (1-fraction) of the segment is stepper territory.
        stepper_zone_m = max(cfg.drive_tol_m, (1.0 - cfg.drive_cruise_fraction) * seg_len)
        if scale_m > stepper_zone_m:
            cruise_m = scale_m - stepper_zone_m
            on_s = cruise_m / cfg.drive_cruise_mps
            return max(cfg.drive_pulse_s, min(cfg.drive_cruise_max_s, on_s))

    # Stepper / short-segment: hold times from live_characterize_ws table.
    if scale_m >= 0.18:
        return cfg.drive_pulse_large_s
    if scale_m >= 0.12:
        return cfg.drive_pulse_mid_s
    if scale_m >= 0.06:
        return cfg.drive_pulse_s
    return cfg.drive_pulse_tiny_s


def segment_drive_in_cruise(
    remaining_m: float,
    cfg: SegmentNavConfig,
    *,
    segment_length_m: float,
) -> bool:
    """True while still in the continuous-W portion of this segment."""
    if segment_length_m < cfg.drive_cruise_min_segment_m:
        return False
    stepper_zone_m = max(cfg.drive_tol_m, (1.0 - cfg.drive_cruise_fraction) * segment_length_m)
    return remaining_m > stepper_zone_m


def _segment_drive_remaining(
    px: float, py: float, seg: Segment, origin: tuple[float, float]
) -> tuple[float, float]:
    """Return (remaining along heading, distance to segment endpoint)."""
    traveled = drive_progress_m(origin, px, py, seg.heading_rad)
    along = seg.length_m - traveled
    to_end = math.hypot(px - seg.x1, py - seg.y1)
    return max(0.0, along), to_end


def _segment_drive_complete(
    px: float,
    py: float,
    seg: Segment,
    origin: tuple[float, float],
    cfg: SegmentNavConfig,
) -> bool:
    traveled = drive_progress_m(origin, px, py, seg.heading_rad)
    along_rem, to_end = _segment_drive_remaining(px, py, seg, origin)
    if to_end <= cfg.drive_tol_m:
        return True
    if along_rem <= cfg.drive_tol_m or traveled >= seg.length_m - cfg.drive_tol_m:
        return True
    if segment_along_progress(px, py, seg) >= 1.0 - cfg.drive_tol_m / max(seg.length_m, 0.01):
        return True
    return False


def segment_aim_heading(
    px: float,
    py: float,
    seg: Segment,
    *,
    aim_min_dist_m: float = 0.25,
) -> float:
    """Prefer bearing from the rover to the segment end (live aim).

    Planned segment heading is wrong once the rover has skidded off the start —
    aiming at the end keeps phase 2 driving forward with fewer corrective spins.

    BUT: aiming at a nearby end point is angle-noise. A 10 cm lateral offset to
    a 9 cm segment end looks like a −90° turn (nav-20260825-021406 death spiral).
    Only use live aim when the end is far enough that bearing is stable, and the
    segment itself is long enough to be worth tracking.
    """
    dx = seg.x1 - px
    dy = seg.y1 - py
    dist = math.hypot(dx, dy)
    min_dist = max(float(aim_min_dist_m), 0.5 * seg.length_m)
    if seg.length_m >= 0.20 and dist >= min_dist:
        return math.atan2(dy, dx)
    return seg.heading_rad


def _align_pulses(abs_err_rad: float, cfg: SegmentNavConfig, overshot: bool) -> tuple[float, float, str]:
    """Scale A/D hold from live |error|; overshoot uses the tiny reverse tap.

    ≥30° → large hold. Settle is always the full observe settle (never shortened)
    so localization can catch up before the next decision.
    """
    deg = abs(math.degrees(abs_err_rad))
    settle = cfg.align_settle_s
    if overshot and deg < 30.0:
        return cfg.align_overshoot_pulse_s, settle, "overshoot"
    if deg + 1e-6 >= 30.0:
        return cfg.align_pulse_large_s, settle, "large"
    return cfg.align_pulse_s, settle, "small"


def _goal_handoff_m(cfg: SegmentNavConfig) -> float:
    return max(cfg.goal_arrive_m, cfg.goal_handoff_m)


def _near_goal(
    px: float, py: float, goal_xy: tuple[float, float] | None, cfg: SegmentNavConfig
) -> tuple[bool, float | None]:
    if goal_xy is None:
        return False, None
    dist = math.hypot(px - goal_xy[0], py - goal_xy[1])
    return dist <= _goal_handoff_m(cfg), dist


def next_segment_step(
    px: float,
    py: float,
    pyaw: float,
    segments: list[Segment],
    state: SegmentNavState | None = None,
    *,
    cfg: SegmentNavConfig | None = None,
    forward_blocked: bool = False,
    goal_xy: tuple[float, float] | None = None,
) -> SegmentNavStep:
    """One discrete action toward completing the current path segment."""
    cfg = cfg or SegmentNavConfig()
    st = state or SegmentNavState()

    if not segments:
        st.phase = "done"
        return SegmentNavStep([], done=True, state=st, phase="done", note="no segments")

    # Near-goal handoff first — fine dock owns the last ~20cm (XY + yaw).
    near, goal_dist = _near_goal(px, py, goal_xy, cfg)
    if near:
        st.phase = "done"
        st.note = f"near goal ({goal_dist:.2f}m) — hand off to fine dock"
        return SegmentNavStep(
            [],
            done=True,
            state=st,
            phase="done",
            note=st.note,
        )

    if st.segment_index >= len(segments):
        if goal_xy is not None and goal_dist is not None:
            # Only replan when still meaningfully far — crumbs near the mark
            # caused a 180° align death spiral in nav-20260825-013644-18be7f.
            if goal_dist > _goal_handoff_m(cfg):
                st.note = (
                    f"segments exhausted but still {goal_dist:.2f}m from goal — replan"
                )
                return SegmentNavStep(
                    [],
                    replan=True,
                    state=st,
                    phase="replan_short",
                    note=st.note,
                )
        st.phase = "done"
        return SegmentNavStep([], done=True, state=st, phase="done", note="all segments done")

    seg = segments[st.segment_index]

    if st.phase == "align":
        # Skip Nav2 micro-crumbs — not worth a pulse+3s-settle align, and
        # aim-at-end on them spins forever when the rover is slightly offset.
        to_end = math.hypot(px - seg.x1, py - seg.y1)
        if seg.length_m < cfg.skip_segment_m or (
            seg.length_m < 0.25 and to_end <= max(0.12, seg.length_m)
        ):
            old_i = st.segment_index
            st.segment_index += 1
            st.approach_sign = 0
            st.note = (
                f"skip micro seg {old_i} ({seg.length_m:.2f}m, "
                f"to_end={to_end:.2f}m) → seg {st.segment_index}"
            )
            return next_segment_step(
                px,
                py,
                pyaw,
                segments,
                st,
                cfg=cfg,
                forward_blocked=forward_blocked,
                goal_xy=goal_xy,
            )

        # Aim at the segment end from where we are now — not the frozen plan heading.
        aim = segment_aim_heading(px, py, seg, aim_min_dist_m=cfg.aim_min_dist_m)
        err = wrap_angle(aim - pyaw)

        # Skid-steer A/D always wanders XY. Never replan mid-align for "drift"
        # unless we are already within yaw tol (ready to drive) and still far off.
        drift = segment_start_drift_m(px, py, seg)
        if (
            abs(err) <= cfg.align_final_tol_rad
            and drift > cfg.drift_replan_m
        ):
            st.note = f"drift {drift:.2f}m before seg {st.segment_index}"
            return SegmentNavStep(
                [],
                replan=True,
                state=st,
                phase="replan_drift",
                note=st.note,
            )

        if abs(err) <= cfg.align_final_tol_rad:
            st.phase = "drive"
            st.drive_origin = (px, py)
            st.approach_sign = 0
            st.note = (
                f"seg {st.segment_index} drive {seg.length_m:.2f}m "
                f"@ {math.degrees(aim):.0f}° (aim)"
            )
            return SegmentNavStep(
                [],
                state=st,
                phase="seg_drive",
                note=st.note,
                pulse_off_s=cfg.drive_settle_s,
            )

        if st.approach_sign == 0 and abs(err) > math.radians(1.0):
            st.approach_sign = 1 if err > 0 else -1
        overshot = st.approach_sign != 0 and (
            (st.approach_sign > 0 and err < 0) or (st.approach_sign < 0 and err > 0)
        )
        on_s, off_s, mode = _align_pulses(abs(err), cfg, overshot)
        key = "a" if err > 0 else "d"
        st.note = (
            f"seg {st.segment_index} align {mode} err={math.degrees(err):+.1f}°"
        )
        return SegmentNavStep(
            [key],
            state=st,
            phase="seg_align",
            note=st.note,
            pulse_on_s=on_s,
            pulse_off_s=off_s,
        )

    if st.phase == "drive":
        if forward_blocked:
            st.note = f"blocked on seg {st.segment_index}"
            return SegmentNavStep(
                [],
                blocked=True,
                replan=True,
                state=st,
                phase="replan_blocked",
                note=st.note,
            )

        # Crooked drive (yaw beyond align tol) → stop W and realign. Otherwise
        # body-forward W barely projects onto the segment and rem stalls.
        aim = segment_aim_heading(px, py, seg, aim_min_dist_m=cfg.aim_min_dist_m)
        yaw_err = wrap_angle(aim - pyaw)
        if abs(yaw_err) > cfg.align_final_tol_rad:
            st.phase = "align"
            st.approach_sign = 0
            st.note = (
                f"seg {st.segment_index} realign err={math.degrees(yaw_err):+.1f}°"
            )
            return SegmentNavStep(
                [],
                state=st,
                phase="seg_align",
                note=st.note,
                pulse_off_s=cfg.drive_settle_s,
            )

        traveled = drive_progress_m(st.drive_origin, px, py, seg.heading_rad)
        along_rem, to_end = _segment_drive_remaining(px, py, seg, st.drive_origin)
        remaining = min(along_rem, to_end)

        if _segment_drive_complete(px, py, seg, st.drive_origin, cfg):
            st.segment_index += 1
            st.phase = "align"
            st.approach_sign = 0
            if st.segment_index >= len(segments):
                near_end, gdist = _near_goal(px, py, goal_xy, cfg)
                if goal_xy is not None and not near_end:
                    st.note = (
                        f"segments exhausted but still {gdist:.2f}m "
                        f"from goal — replan"
                    )
                    return SegmentNavStep(
                        [],
                        replan=True,
                        state=st,
                        phase="replan_short",
                        note=st.note,
                        pulse_off_s=cfg.drive_settle_s,
                    )
                st.phase = "done"
                return SegmentNavStep(
                    [],
                    done=True,
                    state=st,
                    phase="done",
                    note=(
                        f"near goal ({gdist:.2f}m) — hand off to fine dock"
                        if near_end and gdist is not None
                        else "all segments done"
                    ),
                    pulse_off_s=cfg.drive_settle_s,
                )
            st.note = f"seg {st.segment_index - 1} done → seg {st.segment_index}"
            return SegmentNavStep(
                [],
                state=st,
                phase="seg_align",
                note=st.note,
                pulse_off_s=cfg.drive_settle_s,
            )

        pulse_on = segment_drive_pulse_sec(
            remaining,
            cfg,
            goal_dist_m=(
                math.hypot(px - goal_xy[0], py - goal_xy[1]) if goal_xy is not None else None
            ),
            segment_length_m=seg.length_m,
        )
        in_cruise = segment_drive_in_cruise(
            remaining, cfg, segment_length_m=seg.length_m
        )
        if pulse_on <= 0:
            st.segment_index += 1
            st.phase = "align"
            st.approach_sign = 0
            if st.segment_index >= len(segments):
                near_end, gdist = _near_goal(px, py, goal_xy, cfg)
                if goal_xy is not None and not near_end:
                    st.note = (
                        f"segments exhausted but still {gdist:.2f}m "
                        f"from goal — replan"
                    )
                    return SegmentNavStep(
                        [],
                        replan=True,
                        state=st,
                        phase="replan_short",
                        note=st.note,
                        pulse_off_s=cfg.drive_settle_s,
                    )
                st.phase = "done"
                return SegmentNavStep(
                    [],
                    done=True,
                    state=st,
                    phase="done",
                    note=(
                        f"near goal ({gdist:.2f}m) — hand off to fine dock"
                        if near_end and gdist is not None
                        else "all segments done"
                    ),
                    pulse_off_s=cfg.drive_settle_s,
                )
            st.note = f"seg {st.segment_index - 1} done (within tol) → seg {st.segment_index}"
            return SegmentNavStep(
                [],
                state=st,
                phase="seg_align",
                note=st.note,
                pulse_off_s=cfg.drive_settle_s,
            )

        # Mid-flight micro A/D — only in the stepper zone. During cruise a
        # continuous W must not be interrupted by A/D+3s settle.
        if (
            not in_cruise
            and remaining >= cfg.midflight_min_remain_m
            and seg.length_m >= cfg.midflight_min_segment_m
        ):
            yaw_err = wrap_angle(seg.heading_rad - pyaw)
            abs_err = abs(yaw_err)
            if cfg.midflight_steer_min_rad <= abs_err <= cfg.midflight_steer_max_rad:
                key = "a" if yaw_err > 0 else "d"
                st.note = (
                    f"seg {st.segment_index} midflight {key.upper()} "
                    f"err={math.degrees(yaw_err):+.1f}° rem={remaining:.2f}m"
                )
                return SegmentNavStep(
                    [key],
                    state=st,
                    phase="seg_midflight",
                    note=st.note,
                    pulse_on_s=cfg.midflight_steer_pulse_s,
                    pulse_off_s=cfg.midflight_steer_settle_s,
                )

        mode = "cruise" if in_cruise else "step"
        st.note = (
            f"seg {st.segment_index} {mode} {traveled:.2f}/{seg.length_m:.2f}m "
            f"rem={remaining:.2f} pulse={pulse_on:.3f}s"
        )
        return SegmentNavStep(
            ["w"],
            state=st,
            phase="seg_drive",
            note=st.note,
            pulse_on_s=pulse_on,
            pulse_off_s=cfg.drive_settle_s,
        )

    st.phase = "align"
    return next_segment_step(
        px,
        py,
        pyaw,
        segments,
        st,
        cfg=cfg,
        forward_blocked=forward_blocked,
        goal_xy=goal_xy,
    )
