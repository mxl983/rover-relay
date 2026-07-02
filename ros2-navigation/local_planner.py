"""Local LiDAR planner for low-speed roam with obstacle escape (no Nav2 dependency)."""

from __future__ import annotations

import math
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Deque, Iterable, Sequence


def normalize_deg(angle: float) -> float:
    wrapped = (angle + 180.0) % 360.0 - 180.0
    return wrapped


def angular_diff_deg(a: float, b: float) -> float:
    return abs(normalize_deg(a - b))


@dataclass(frozen=True)
class ScanPoint:
    angle_deg: float
    range_m: float


@dataclass(frozen=True)
class PlannerConfig:
    stop_distance_m: float = 0.28
    roam_linear: float = 0.48
    escape_linear: float = 0.38
    escape_arc_linear: float = 0.28
    turn_gain: float = 1.0
    roam_turn_scale: float = 0.8
    min_escape_angular: float = 0.62
    forward_arc_deg: float = 55.0
    max_escape_bearing_deg: float = 70.0
    max_drive_bearing_deg: float = 50.0
    max_wander_heading_deg: float = 40.0
    sector_count: int = 16
    min_escape_clearance_m: float = 0.42
    escape_commit_s: float = 1.5
    reverse_penalty_deg: float = 145.0
    alternate_window_s: float = 8.0
    alternate_penalty: float = 0.35
    stuck_alternate_limit: int = 2
    recovery_turn_deg: float = 95.0
    recovery_commit_s: float = 2.0
    wander_turn_rate_deg_s: float = 32.0


@dataclass(frozen=True)
class DriveCommand:
    linear: float
    angular: float
    phase: str
    escape_bearing_deg: float | None = None


@dataclass
class LocalPlanner:
    config: PlannerConfig = field(default_factory=PlannerConfig)
    phase: str = "roam"
    wander_heading_deg: float = 0.0
    escape_bearing_deg: float = 0.0
    escape_until_monotonic: float = 0.0
    recovery_until_monotonic: float = 0.0
    last_tick_monotonic: float = field(default_factory=time.monotonic)
    escape_history: Deque[tuple[float, float]] = field(default_factory=lambda: deque(maxlen=8))
    alternate_sign: int = 0
    alternate_count: int = 0

    def reset(self) -> None:
        self.phase = "roam"
        self.wander_heading_deg = 0.0
        self.escape_bearing_deg = 0.0
        self.escape_until_monotonic = 0.0
        self.recovery_until_monotonic = 0.0
        self.escape_history.clear()
        self.alternate_sign = 0
        self.alternate_count = 0

    def tick(self, points: Sequence[ScanPoint], now: float | None = None) -> DriveCommand:
        tick_now = time.monotonic() if now is None else now
        dt = max(0.0, min(0.5, tick_now - self.last_tick_monotonic))
        self.last_tick_monotonic = tick_now

        if self.phase == "recovery" and tick_now < self.recovery_until_monotonic:
            return self._drive_recovery()

        if self.phase == "escape" and tick_now < self.escape_until_monotonic:
            cmd = self._drive_escape(points)
            if self._forward_clear(points):
                self.phase = "roam"
            return cmd

        forward_blocked = self._forward_blocked(points)
        if forward_blocked:
            self.phase = "escape"
            self.wander_heading_deg = 0.0
            self.escape_bearing_deg = self._pick_escape_bearing(points, tick_now)
            self.escape_until_monotonic = tick_now + self.config.escape_commit_s
            self._record_escape(tick_now, self.escape_bearing_deg)
            return self._drive_escape(points)

        self.phase = "roam"
        self.wander_heading_deg = normalize_deg(
            self.wander_heading_deg + self.config.wander_turn_rate_deg_s * dt * self._wander_sign()
        )
        limit = self.config.max_wander_heading_deg
        self.wander_heading_deg = max(-limit, min(limit, self.wander_heading_deg))
        linear = self.config.roam_linear
        angular = self._heading_to_angular(self.wander_heading_deg) * self.config.roam_turn_scale
        return self._finalize(DriveCommand(linear=linear, angular=angular, phase="roam"))

    def _wander_sign(self) -> float:
        if self.alternate_sign == 0:
            return 1.0
        return float(self.alternate_sign)

    def _forward_blocked(self, points: Sequence[ScanPoint]) -> bool:
        arc = self.config.forward_arc_deg
        blocked_ranges = [
            p.range_m
            for p in points
            if abs(p.angle_deg) <= arc and p.range_m > 0.05
        ]
        if not blocked_ranges:
            return False
        return min(blocked_ranges) < self.config.stop_distance_m

    def _forward_clear(self, points: Sequence[ScanPoint]) -> bool:
        arc = self.config.forward_arc_deg
        forward_ranges = [
            p.range_m
            for p in points
            if abs(p.angle_deg) <= arc and p.range_m > 0.05
        ]
        if not forward_ranges:
            return True
        return min(forward_ranges) >= self.config.min_escape_clearance_m

    def _sector_metrics(self, points: Sequence[ScanPoint]) -> list[tuple[float, float]]:
        count = self.config.sector_count
        sector_width = 360.0 / count
        metrics: list[tuple[float, float]] = []
        for index in range(count):
            center = -180.0 + sector_width * (index + 0.5)
            low = center - sector_width * 0.5
            high = center + sector_width * 0.5
            ranges = [
                p.range_m
                for p in points
                if self._angle_in_sector(p.angle_deg, low, high) and p.range_m > 0.05
            ]
            clearance = min(ranges) if ranges else 8.0
            metrics.append((center, clearance))
        return metrics

    @staticmethod
    def _angle_in_sector(angle_deg: float, low: float, high: float) -> bool:
        angle = normalize_deg(angle_deg)
        low_n = normalize_deg(low)
        high_n = normalize_deg(high)
        if low_n <= high_n:
            return low_n <= angle <= high_n
        return angle >= low_n or angle <= high_n

    def _forward_sectors(
        self, metrics: list[tuple[float, float]]
    ) -> list[tuple[float, float]]:
        limit = self.config.max_escape_bearing_deg
        return [(center, clearance) for center, clearance in metrics if abs(center) <= limit]

    def _pick_escape_bearing(self, points: Sequence[ScanPoint], now: float) -> float:
        metrics = self._sector_metrics(points)
        forward_metrics = self._forward_sectors(metrics)
        if not forward_metrics:
            forward_metrics = metrics

        viable = [
            (center, clearance)
            for center, clearance in forward_metrics
            if clearance >= self.config.min_escape_clearance_m
        ]
        if not viable and self.alternate_count >= self.config.stuck_alternate_limit:
            self.phase = "recovery"
            sign = 1 if self.alternate_sign >= 0 else -1
            self.escape_bearing_deg = normalize_deg(sign * self.config.recovery_turn_deg)
            self.recovery_until_monotonic = now + self.config.recovery_commit_s
            self.alternate_count = 0
            return self.escape_bearing_deg

        if not viable:
            # No forward opening: turn in place toward the best forward-side sector.
            center, _ = max(forward_metrics, key=lambda item: item[1])
            return center

        scored: list[tuple[float, float, float]] = []
        for center, clearance in viable:
            penalty = self._reverse_penalty(center, now)
            score = clearance * penalty
            scored.append((score, center, clearance))

        scored.sort(reverse=True)
        best_center = scored[0][1]
        second_center = scored[1][1] if len(scored) > 1 else best_center

        if self.alternate_sign == 0:
            self.alternate_sign = 1 if best_center >= 0 else -1
        elif (
            angular_diff_deg(best_center, second_center) > 70.0
            and math.copysign(1.0, best_center or 1.0) != math.copysign(1.0, second_center or 1.0)
        ):
            self.alternate_count += 1
            if self.alternate_count >= self.config.stuck_alternate_limit:
                self.phase = "recovery"
                sign = -1 if self.alternate_sign > 0 else 1
                self.recovery_until_monotonic = now + self.config.recovery_commit_s
                self.alternate_count = 0
                return normalize_deg(sign * self.config.recovery_turn_deg)

        self.alternate_sign = 1 if best_center >= 0 else -1
        return best_center

    def _reverse_penalty(self, bearing_deg: float, now: float) -> float:
        penalty = 1.0
        for ts, past_bearing in self.escape_history:
            if now - ts > self.config.alternate_window_s:
                continue
            if angular_diff_deg(bearing_deg, past_bearing) >= self.config.reverse_penalty_deg:
                penalty *= self.config.alternate_penalty
        return penalty

    def _record_escape(self, now: float, bearing_deg: float) -> None:
        if self.escape_history and angular_diff_deg(self.escape_history[-1][1], bearing_deg) < 12.0:
            return
        self.escape_history.append((now, bearing_deg))

    def _drive_escape(self, points: Sequence[ScanPoint]) -> DriveCommand:
        bearing = self.escape_bearing_deg
        bearing_abs = abs(bearing)
        angular = self._boost_turn(self._heading_to_angular(bearing), bearing_abs)
        aligned = bearing_abs <= self.config.max_drive_bearing_deg
        forward_ranges = [
            p.range_m
            for p in points
            if angular_diff_deg(p.angle_deg, bearing) <= 20.0 and p.range_m > 0.05
        ]
        forward_clear = not forward_ranges or min(forward_ranges) >= self.config.stop_distance_m
        if aligned and forward_clear and bearing_abs <= self.config.max_escape_bearing_deg:
            linear = self.config.escape_linear
        elif (
            not aligned
            and forward_clear
            and bearing_abs <= self.config.max_escape_bearing_deg
        ):
            # Arc out of traps: creep forward while still turning hard.
            linear = self.config.escape_arc_linear
        else:
            linear = 0.0
        return self._finalize(
            DriveCommand(
                linear=linear,
                angular=angular,
                phase="escape",
                escape_bearing_deg=bearing,
            )
        )

    def _drive_recovery(self) -> DriveCommand:
        bearing_abs = abs(self.escape_bearing_deg)
        angular = self._boost_turn(
            self._heading_to_angular(self.escape_bearing_deg),
            bearing_abs,
        )
        return self._finalize(
            DriveCommand(
                linear=0.0,
                angular=angular,
                phase="recovery",
                escape_bearing_deg=self.escape_bearing_deg,
            )
        )

    def _finalize(self, cmd: DriveCommand) -> DriveCommand:
        """Forward-only planner output (linear magnitude); mapped to Pi y = -linear at send."""
        linear, angular = self._skid_steer_safe(cmd.linear, cmd.angular)
        return DriveCommand(
            linear=linear,
            angular=angular,
            phase=cmd.phase,
            escape_bearing_deg=cmd.escape_bearing_deg,
        )

    @staticmethod
    def _skid_steer_safe(linear: float, angular: float) -> tuple[float, float]:
        linear = max(0.0, linear)
        angular = max(-1.0, min(1.0, angular))
        if linear <= 0:
            return 0.0, angular
        # Pi: forward=-y, left=-y+x, right=-y-x → |x| <= forward magnitude.
        angular = max(-linear, min(linear, angular))
        return linear, angular

    def _heading_to_angular(self, bearing_deg: float) -> float:
        return max(-1.0, min(1.0, bearing_deg / 90.0 * self.config.turn_gain))

    def _boost_turn(self, angular: float, bearing_abs: float) -> float:
        if bearing_abs < 12.0:
            return angular
        floor = self.config.min_escape_angular
        if angular >= 0:
            return max(angular, floor)
        return min(angular, -floor)

    @staticmethod
    def points_from_ranges(
        ranges: Iterable[float],
        angle_min_rad: float,
        angle_increment_rad: float,
        range_min: float,
        range_max: float,
    ) -> list[ScanPoint]:
        points: list[ScanPoint] = []
        for index, distance in enumerate(ranges):
            if not math.isfinite(distance):
                continue
            if distance < range_min or distance > range_max:
                continue
            angle_rad = angle_min_rad + index * angle_increment_rad
            points.append(
                ScanPoint(angle_deg=math.degrees(angle_rad), range_m=float(distance))
            )
        return points
