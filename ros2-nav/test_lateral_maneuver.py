"""Unit tests for skid-steer lateral gap closing."""

from __future__ import annotations

import math
import unittest

from lateral_maneuver import (
    GapCloseConfig,
    GapCloseState,
    body_frame_error,
    gap_close_accepted,
    next_gap_close_step,
    turn_progress_rad,
    wrap_angle,
)


class LateralManeuverTests(unittest.TestCase):
    def test_body_frame_right_is_negative_left(self) -> None:
        _, fwd, left, _ = body_frame_error(0.0, 0.0, 0.0, 0.0, -0.2)
        self.assertAlmostEqual(fwd, 0.0, places=3)
        self.assertAlmostEqual(left, -0.2, places=3)

    def test_turn_progress_right(self) -> None:
        start = 0.0
        current = -math.pi / 2
        prog = turn_progress_rad(start, current, direction=-1)
        self.assertAlmostEqual(prog, math.pi / 2, places=2)

    def test_lateral_right_starts_with_turn_right(self) -> None:
        # Target 0.15 m to the right → first key should be D.
        step = next_gap_close_step(
            0.0, 0.0, 0.0, 0.0, -0.15, 0.0, GapCloseState(), cfg=GapCloseConfig()
        )
        self.assertEqual(step.phase, "lat_t1")
        self.assertEqual(step.keys, ["d"])

    def test_lateral_left_starts_with_turn_left(self) -> None:
        step = next_gap_close_step(
            0.0, 0.0, 0.0, 0.0, 0.15, 0.0, GapCloseState(), cfg=GapCloseConfig()
        )
        self.assertEqual(step.phase, "lat_t1")
        self.assertEqual(step.keys, ["a"])

    def test_quarter_turn_advances_to_drive(self) -> None:
        st = GapCloseState(
            phase="lat_t1",
            turn_start_yaw=0.0,
            turn_dir=-1,
            lat_restore_dir=1,
            drive_target_m=0.15,
        )
        step = next_gap_close_step(
            0.0, 0.0, -math.pi / 2, 0.0, -0.15, 0.0, st, cfg=GapCloseConfig()
        )
        self.assertEqual(step.phase, "lat_drive")
        self.assertEqual(step.keys, [])

    def test_overshoot_corrects_opposite(self) -> None:
        st = GapCloseState(
            phase="lat_t1",
            turn_start_yaw=0.0,
            turn_dir=-1,
            lat_restore_dir=1,
            drive_target_m=0.15,
        )
        step = next_gap_close_step(
            0.0, 0.0, wrap_angle(-math.pi / 2 - math.radians(20)),
            0.0, -0.15, 0.0, st, cfg=GapCloseConfig()
        )
        self.assertEqual(step.keys, ["a"])
        self.assertIn("overshoot", step.note)

    def test_forward_before_lateral_when_larger(self) -> None:
        step = next_gap_close_step(
            0.0, 0.0, 0.0, 0.20, -0.05, 0.0, GapCloseState(), cfg=GapCloseConfig()
        )
        self.assertEqual(step.phase, "fwd")
        self.assertEqual(step.keys, ["w"])

    def test_done_when_within_tol(self) -> None:
        self.assertTrue(
            gap_close_accepted(0.0, 0.0, 0.0, 0.03, -0.02, 0.05, cfg=GapCloseConfig())
        )
        step = next_gap_close_step(
            0.0, 0.0, 0.0, 0.03, -0.02, 0.05, GapCloseState(), cfg=GapCloseConfig()
        )
        self.assertTrue(step.done)
        self.assertEqual(step.keys, [])


if __name__ == "__main__":
    unittest.main()
