#!/usr/bin/env python3
import math
import unittest

from yaw_pulse_align import (
    YawPulseAlignConfig,
    YawPulseAlignState,
    _pulse_profile,
    goal_yaw_error,
    should_begin_yaw_align,
    tick_yaw_pulse_align,
)


class YawPulseAlignTests(unittest.TestCase):
    def test_shortest_yaw_error(self) -> None:
        err = goal_yaw_error(0.0, math.pi / 2)
        self.assertAlmostEqual(err, math.pi / 2, places=3)
        err2 = goal_yaw_error(0.0, -math.pi / 2)
        self.assertAlmostEqual(err2, -math.pi / 2, places=3)

    def test_ccw_needs_negative_stick(self) -> None:
        cfg = YawPulseAlignConfig(settle_s=0.0)
        st = YawPulseAlignState(active=True, phase="settle", until=0.0)
        drive, st2, done = tick_yaw_pulse_align(0.0, 0.5, st, cfg, now=1.0)
        self.assertFalse(done)
        self.assertLess(drive["x"], 0.0)
        self.assertEqual(st2.approach_sign, 1)

    def test_cw_needs_positive_stick(self) -> None:
        cfg = YawPulseAlignConfig(settle_s=0.0)
        st = YawPulseAlignState(active=True, phase="settle", until=0.0)
        drive, st2, done = tick_yaw_pulse_align(0.0, -0.5, st, cfg, now=1.0)
        self.assertFalse(done)
        self.assertGreater(drive["x"], 0.0)
        self.assertEqual(st2.approach_sign, -1)

    def test_done_within_tol(self) -> None:
        cfg = YawPulseAlignConfig(yaw_tol_rad=math.radians(10.0))
        st = YawPulseAlignState(active=True)
        _, _, done = tick_yaw_pulse_align(0.0, 0.05, st, cfg, now=0.0)
        self.assertTrue(done)

    def test_large_error_gets_longer_pulse(self) -> None:
        cfg = YawPulseAlignConfig()
        big, _, _, _ = _pulse_profile(math.radians(80.0), cfg)
        small, _, _, _ = _pulse_profile(math.radians(15.0), cfg)
        self.assertGreater(big, small)

    def test_no_reverse_on_overshoot(self) -> None:
        """Crossing the goal must not fire a reverse micro-pulse."""
        cfg = YawPulseAlignConfig(settle_s=0.0, yaw_tol_rad=math.radians(5.0))
        st = YawPulseAlignState(
            active=True,
            phase="settle",
            until=0.0,
            approach_sign=1,  # was turning CCW
        )
        # Now well past goal (err negative) outside commit → re-lock settle, zero drive.
        drive, st2, done = tick_yaw_pulse_align(
            0.40, 0.0, st, cfg, now=1.0  # err ≈ -23°
        )
        self.assertFalse(done)
        self.assertEqual(drive["x"], 0.0)
        self.assertEqual(st2.phase, "settle")
        self.assertEqual(st2.approach_sign, -1)
        self.assertIn("no reverse", st2.note)

    def test_early_handoff_large_yaw_near_goal(self) -> None:
        cfg = YawPulseAlignConfig()
        self.assertTrue(
            should_begin_yaw_align(0.35, math.radians(-50.0), cfg)
        )
        self.assertFalse(
            should_begin_yaw_align(0.50, math.radians(-50.0), cfg)
        )


if __name__ == "__main__":
    unittest.main()
