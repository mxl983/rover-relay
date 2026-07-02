#!/usr/bin/env python3
"""Unit tests for local_planner anti-oscillation behavior."""

from __future__ import annotations

import time
import unittest

from local_planner import LocalPlanner, PlannerConfig, ScanPoint


def wall_ahead_points() -> list[ScanPoint]:
    points: list[ScanPoint] = []
    for deg in range(-180, 180, 5):
        if abs(deg) <= 40:
            points.append(ScanPoint(angle_deg=float(deg), range_m=0.18))
        elif deg > 0:
            points.append(ScanPoint(angle_deg=float(deg), range_m=1.2))
        else:
            points.append(ScanPoint(angle_deg=float(deg), range_m=0.9))
    return points


class LocalPlannerTests(unittest.TestCase):
    def test_roam_moves_forward_when_clear(self) -> None:
        planner = LocalPlanner()
        points = [ScanPoint(angle_deg=float(d), range_m=2.0) for d in range(-180, 180, 10)]
        cmd = planner.tick(points, now=1.0)
        self.assertEqual(cmd.phase, "roam")
        self.assertGreater(cmd.linear, 0.0)

    def test_blocks_and_escapes_toward_clearer_side(self) -> None:
        planner = LocalPlanner()
        cmd = planner.tick(wall_ahead_points(), now=1.0)
        self.assertIn(cmd.phase, {"escape", "recovery"})
        self.assertIsNotNone(cmd.escape_bearing_deg)
        self.assertGreater(cmd.escape_bearing_deg or 0.0, 0.0)

    def test_avoids_immediate_reverse_after_recent_escape(self) -> None:
        planner = LocalPlanner(PlannerConfig(alternate_window_s=10.0))
        now = 10.0
        first = planner.tick(wall_ahead_points(), now=now)
        self.assertEqual(first.phase, "escape")
        bearing = first.escape_bearing_deg or 0.0

        planner.phase = "escape"
        planner.escape_until_monotonic = now + 0.1
        second = planner.tick(wall_ahead_points(), now=now + 0.2)
        self.assertNotAlmostEqual(second.escape_bearing_deg or 0.0, -bearing, delta=25.0)

    def test_skid_steer_never_reverses_a_wheel(self) -> None:
        linear, angular = LocalPlanner._skid_steer_safe(0.18, 0.55)
        self.assertGreaterEqual(linear, 0.0)
        self.assertLessEqual(abs(angular), linear)

    def test_escape_bold_turn_and_arc(self) -> None:
        planner = LocalPlanner()
        cmd = planner.tick(wall_ahead_points(), now=1.0)
        self.assertEqual(cmd.phase, "escape")
        self.assertGreaterEqual(abs(cmd.angular), planner.config.min_escape_angular)

    def test_never_commands_reverse(self) -> None:
        planner = LocalPlanner()
        blocked_behind = [
            ScanPoint(angle_deg=float(d), range_m=0.15 if abs(d) > 120 else 2.0)
            for d in range(-180, 180, 5)
        ]
        for t in range(20):
            cmd = planner.tick(blocked_behind, now=float(t) * 0.2)
            self.assertGreaterEqual(cmd.linear, 0.0)

    def test_escape_bearing_stays_in_forward_arc(self) -> None:
        planner = LocalPlanner()
        cmd = planner.tick(wall_ahead_points(), now=1.0)
        if cmd.escape_bearing_deg is not None:
            self.assertLessEqual(abs(cmd.escape_bearing_deg), planner.config.max_escape_bearing_deg)

    def test_recovery_after_repeated_alternation(self) -> None:
        planner = LocalPlanner(
            PlannerConfig(
                stuck_alternate_limit=2,
                recovery_turn_deg=90.0,
                recovery_commit_s=1.0,
                min_escape_clearance_m=0.5,
            )
        )
        blocked = [ScanPoint(angle_deg=float(d), range_m=0.2) for d in range(-180, 180, 10)]
        planner.alternate_count = 2
        bearing = planner._pick_escape_bearing(blocked, now=0.0)
        self.assertEqual(planner.phase, "recovery")
        self.assertAlmostEqual(abs(bearing), 90.0, delta=1.0)


if __name__ == "__main__":
    unittest.main()
