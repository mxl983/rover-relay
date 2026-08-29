#!/usr/bin/env python3
"""Tests for path-based navigation progress."""

from __future__ import annotations

import unittest

from path_progress import remaining_path_distance


class PathProgressTests(unittest.TestCase):
    def test_detour_uses_remaining_route_distance(self) -> None:
        path = [[0.0, 0.0], [0.0, -2.0], [5.0, -2.0], [5.0, 5.0]]

        self.assertAlmostEqual(
            remaining_path_distance(path, (0.0, -1.0)) or 0.0,
            13.0,
        )

    def test_pose_between_path_points_is_projected(self) -> None:
        path = [[0.0, 0.0], [4.0, 0.0], [4.0, 4.0]]

        self.assertAlmostEqual(
            remaining_path_distance(path, (2.5, 0.4)) or 0.0,
            5.5,
        )

    def test_short_path_has_no_progress_distance(self) -> None:
        self.assertIsNone(remaining_path_distance([[1.0, 2.0]], (1.0, 2.0)))


if __name__ == "__main__":
    unittest.main()
