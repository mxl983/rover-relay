#!/usr/bin/env python3
"""Tests for soft nav IMU assist (fail-open, early-stop only)."""

from __future__ import annotations

import math
import unittest

from nav_imu import (
    parse_pi_imu,
    turn_seems_stuck,
    yaw_pulse_early_stop,
)
from yaw_pulse_align import (
    YawImuAssist,
    YawPulseAlignConfig,
    YawPulseAlignState,
    tick_yaw_pulse_align,
)


class NavImuTests(unittest.TestCase):
    def test_parse_sample_wrapper(self) -> None:
        g = parse_pi_imu(
            {
                "success": True,
                "sample": {"gyro": {"x": 0.1, "y": -0.2, "z": 1.3}},
            }
        )
        self.assertEqual(g, (0.1, -0.2, 1.3))

    def test_early_stop_requires_correct_sense(self) -> None:
        # Need CCW (+), but integrated went CW → no stop.
        self.assertFalse(
            yaw_pulse_early_stop(
                approach_sign=1,
                err_at_pulse_start=math.radians(30),
                integrated_yaw_rad=math.radians(-20),
                live_gz=0.5,
                yaw_tol_rad=math.radians(10),
            )
        )
        # Correct sense, covered 72% of 30° ≈ 21.6° → stop.
        self.assertTrue(
            yaw_pulse_early_stop(
                approach_sign=1,
                err_at_pulse_start=math.radians(30),
                integrated_yaw_rad=math.radians(22),
                live_gz=0.4,
                yaw_tol_rad=math.radians(10),
            )
        )

    def test_stuck_when_no_gz(self) -> None:
        self.assertTrue(turn_seems_stuck(approach_sign=1, live_gz=0.0))
        self.assertFalse(turn_seems_stuck(approach_sign=1, live_gz=0.5))

    def test_yaw_pulse_imu_early_stop_cuts_drive(self) -> None:
        cfg = YawPulseAlignConfig(settle_s=1.0, yaw_tol_rad=math.radians(10))
        st = YawPulseAlignState(
            active=True,
            phase="pulse",
            until=10.0,
            stick_x=-0.4,
            approach_sign=1,
            err_at_pulse_start=math.radians(40),
            imu_yaw_integ0=0.0,
            settle_s=1.0,
            pulse_count=1,
        )
        # SLAM still shows large error — without IMU would keep pulsing.
        imu = YawImuAssist(
            ok=True,
            gz=0.8,
            integrated_yaw_rad=math.radians(30),  # ~75% of 40°
        )
        drive, st2, done = tick_yaw_pulse_align(
            0.0, math.radians(40), st, cfg, now=1.0, imu=imu
        )
        self.assertFalse(done)  # SLAM not within tol yet
        self.assertEqual(drive["x"], 0.0)
        self.assertEqual(st2.phase, "settle")
        self.assertIn("imu early-stop", st2.note)

    def test_yaw_pulse_without_imu_unchanged(self) -> None:
        cfg = YawPulseAlignConfig(settle_s=1.0)
        st = YawPulseAlignState(
            active=True,
            phase="pulse",
            until=10.0,
            stick_x=-0.4,
            approach_sign=1,
            settle_s=1.0,
            pulse_count=1,
        )
        drive, st2, done = tick_yaw_pulse_align(
            0.0, math.radians(40), st, cfg, now=1.0, imu=None
        )
        self.assertFalse(done)
        self.assertLess(drive["x"], 0.0)
        self.assertEqual(st2.phase, "pulse")


if __name__ == "__main__":
    unittest.main()
