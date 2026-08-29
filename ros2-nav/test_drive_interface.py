#!/usr/bin/env python3
"""Unit tests for calibrated Twist ↔ Pi stick mapping."""

from __future__ import annotations

import unittest

from drive_interface import (
    PURE_ROTATE_STICK,
    DriveLimits,
    clamp_twist,
    limit_accel,
    pi_drive_to_twist_approx,
    speed_mps_to_stick_y,
    stick_y_to_speed_mps,
    twist_to_pi_drive,
)


class DriveInterfaceTests(unittest.TestCase):
    def test_forward_char_table_endpoints(self) -> None:
        self.assertAlmostEqual(stick_y_to_speed_mps(1.0), 0.56, places=2)
        self.assertAlmostEqual(stick_y_to_speed_mps(0.5), 0.24, places=2)
        self.assertAlmostEqual(stick_y_to_speed_mps(0.2), 0.04, places=2)
        self.assertEqual(stick_y_to_speed_mps(0.1), 0.0)

    def test_speed_to_stick_roundtrip_mid(self) -> None:
        stick = speed_mps_to_stick_y(0.30)
        self.assertGreaterEqual(stick, 0.55)
        self.assertLessEqual(stick, 0.65)
        self.assertAlmostEqual(stick_y_to_speed_mps(stick), 0.30, delta=0.03)

    def test_zero_command(self) -> None:
        self.assertEqual(twist_to_pi_drive(0.0, 0.0), {"x": 0.0, "y": 0.0})

    def test_forward_motion(self) -> None:
        drive = twist_to_pi_drive(0.30, 0.0)
        self.assertEqual(drive["x"], 0.0)
        self.assertLess(drive["y"], -0.5)
        self.assertGreaterEqual(abs(drive["y"]), 0.2)

    def test_no_reverse_by_default(self) -> None:
        drive = twist_to_pi_drive(-0.20, 0.0)
        self.assertEqual(drive["y"], 0.0)

    def test_inplace_tiny_turn_commits_floor(self) -> None:
        # Any pure-yaw command above the deadband must clear static friction.
        drive = twist_to_pi_drive(0.0, 0.06)
        self.assertEqual(drive["y"], 0.0)
        self.assertLess(drive["x"], 0.0)
        self.assertGreaterEqual(abs(drive["x"]), PURE_ROTATE_STICK - 1e-6)

    def test_inplace_sub_min_angular_turn_commits_floor(self) -> None:
        drive = twist_to_pi_drive(0.0, 0.04)
        self.assertEqual(drive["y"], 0.0)
        self.assertGreaterEqual(abs(drive["x"]), PURE_ROTATE_STICK - 1e-6)

    def test_inplace_nav2_rotate_commits_floor(self) -> None:
        # RPP rotate-to-heading / PP yaw (~0.12+) must break static friction.
        drive = twist_to_pi_drive(0.0, 0.12)
        self.assertEqual(drive["y"], 0.0)
        self.assertLess(drive["x"], 0.0)
        self.assertGreaterEqual(abs(drive["x"]), PURE_ROTATE_STICK - 1e-6)

    def test_inplace_large_turn_uses_floor(self) -> None:
        drive = twist_to_pi_drive(0.0, 0.60)
        self.assertGreaterEqual(abs(drive["x"]), PURE_ROTATE_STICK - 1e-6)

    def test_inplace_right_turn(self) -> None:
        drive = twist_to_pi_drive(0.0, -0.60)
        self.assertGreater(drive["x"], 0.0)
        self.assertGreaterEqual(abs(drive["x"]), PURE_ROTATE_STICK - 1e-6)

    def test_arc_yaw_trim_stays_soft(self) -> None:
        # While translating, small wz must NOT jump to pure-rotate stick.
        drive = twist_to_pi_drive(0.25, 0.08)
        self.assertLess(drive["y"], 0.0)  # still forward
        self.assertLess(abs(drive["x"]), PURE_ROTATE_STICK)
        self.assertGreater(abs(drive["x"]), 0.0)

    def test_arc_heavy_wz_capped_while_forward(self) -> None:
        from drive_interface import ARC_MAX_STICK, limit_arc_twist

        vx, wz = limit_arc_twist(0.30, 0.67)
        self.assertLess(abs(wz), 0.20)
        drive = twist_to_pi_drive(vx, wz)
        self.assertLess(drive["y"], 0.0)
        self.assertLessEqual(abs(drive["x"]), ARC_MAX_STICK + 1e-6)

    def test_invert_angular(self) -> None:
        lim = DriveLimits(invert_angular=True)
        drive = twist_to_pi_drive(0.0, 0.60, limits=lim)
        self.assertGreater(drive["x"], 0.0)

    def test_clamp_saturates(self) -> None:
        lim = DriveLimits(max_linear_mps=0.35, max_angular_rps=0.80)
        vx, wz = clamp_twist(2.0, -3.0, lim)
        self.assertEqual(vx, 0.35)
        self.assertEqual(wz, -0.80)

    def test_accel_limit(self) -> None:
        vx, wz = limit_accel(0.0, 0.0, 0.35, 0.0, dt=0.05, max_linear_accel=0.40)
        self.assertLess(vx, 0.35)
        self.assertAlmostEqual(vx, 0.02, places=3)

    def test_accel_bypass_angular(self) -> None:
        _, wz = limit_accel(
            0.0, 0.0, 0.0, 0.70, dt=0.05, bypass_angular=True
        )
        self.assertAlmostEqual(wz, 0.70, places=3)

    def test_rotate_priority_when_yaw_dominates(self) -> None:
        drive = twist_to_pi_drive(0.08, 0.50)
        self.assertEqual(drive["y"], 0.0)
        self.assertNotEqual(drive["x"], 0.0)

    def test_approx_inverse_forward(self) -> None:
        drive = twist_to_pi_drive(0.30, 0.0)
        vx, wz = pi_drive_to_twist_approx(drive)
        self.assertGreater(vx, 0.25)
        self.assertAlmostEqual(wz, 0.0, places=2)


if __name__ == "__main__":
    unittest.main()
