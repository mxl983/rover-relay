#!/usr/bin/env python3
"""Unit tests for Pi IMU → ROS axis conversion / payload parse / health."""

from __future__ import annotations

import unittest
from collections import deque

from imu_bridge import (
    chip_accel_to_ros,
    chip_to_ros,
    evaluate_health,
    parse_imu_payload,
    pi_accel_to_ros,
)


class ImuBridgeTests(unittest.TestCase):
    def test_pi_accel_remaps_gravity_x_to_ros_z(self) -> None:
        # Measured rover rest: ~0.8 g on Pi X.
        rx, ry, rz = pi_accel_to_ros(0.81, 0.0, 0.0)
        self.assertAlmostEqual(rx, 0.0, places=3)
        self.assertAlmostEqual(ry, 0.0, places=3)
        self.assertAlmostEqual(rz, 0.81, places=2)

    def test_chip_to_ros_alias(self) -> None:
        rx, ry, rz = chip_accel_to_ros(0.81, -0.004, 0.01)
        self.assertAlmostEqual(rx, 0.01, places=3)
        self.assertAlmostEqual(ry, -0.004, places=3)
        self.assertAlmostEqual(rz, 0.81, places=2)

    def test_forward_accel_lateral_on_ros_x(self) -> None:
        # Pi lateral bump on Z becomes ROS X after remap (z,y,x).
        rx, ry, rz = chip_to_ros(0.81, 0.0, 0.2)
        self.assertAlmostEqual(rx, 0.2)
        self.assertAlmostEqual(rz, 0.81)

    def test_parse_pi_success_sample_wrapper(self) -> None:
        raw = {
            "success": True,
            "status": {"connected": True, "sampleAgeMs": 50.0, "seq": 129},
            "sample": {
                "stamp": 1787717501.8,
                "seq": 129,
                "accel": {"x": 0.81, "y": -0.004, "z": 0.01, "unit": "g"},
                "gyro": {"x": 0.0, "y": 0.0, "z": 1.29, "unit": "rad_s"},
            },
        }
        sample = parse_imu_payload(raw)
        assert sample is not None
        self.assertEqual(sample["seq"], 129)
        self.assertAlmostEqual(sample["accel_g"][0], 0.81)

    def test_health_accepts_constant_gz_bias_when_debiased_quiet(self) -> None:
        """Large rest gz offset is OK if debiased residuals are near zero."""
        window: deque = deque(maxlen=40)
        for _ in range(40):
            window.append(
                {
                    "accel_g": (0.81, 0.0, 0.0),
                    "gyro_rad_s": (0.0, 0.0, 1.292),
                }
            )
        ok, reason = evaluate_health(window)
        self.assertTrue(ok, reason)

    def test_health_rejects_gravity_on_pi_y_after_remap(self) -> None:
        window: deque = deque(maxlen=40)
        for _ in range(40):
            window.append(
                {
                    "accel_g": (0.0, 0.98, 0.0),
                    "gyro_rad_s": (0.0, 0.0, 0.02),
                }
            )
        ok, reason = evaluate_health(window)
        self.assertFalse(ok)
        self.assertIn("gravity_not_on_z", reason)

    def test_health_accepts_rover_rest_profile(self) -> None:
        window: deque = deque(maxlen=40)
        for i in range(40):
            window.append(
                {
                    "accel_g": (0.796, 0.001 * (i % 3 - 1), 0.0),
                    "gyro_rad_s": (
                        -0.007,
                        -0.001,
                        1.292 + 0.001 * (i % 3 - 1),
                    ),
                }
            )
        ok, reason = evaluate_health(window)
        self.assertTrue(ok, reason)


if __name__ == "__main__":
    unittest.main()
