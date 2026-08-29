#!/usr/bin/env python3
import math
import unittest

from xy_gap_close import (
    XyGapCloseConfig,
    XyGapCloseState,
    dist_xy,
    tick_xy_gap_close,
)


class XyGapCloseTests(unittest.TestCase):
    def test_done_within_tol(self) -> None:
        cfg = XyGapCloseConfig(xy_tol_m=0.08)
        st = XyGapCloseState(active=True)
        _, _, done = tick_xy_gap_close(0.0, 0.0, 0.0, 0.05, 0.05, st, cfg, now=0.0)
        self.assertTrue(done)

    def test_final_push_when_close(self) -> None:
        cfg = XyGapCloseConfig(settle_s=0.0, xy_tol_m=0.08, final_dist_m=0.22)
        st = XyGapCloseState(active=True, phase="settle", until=0.0)
        drive, st2, done = tick_xy_gap_close(
            0.0, 0.0, 0.0, 0.15, 0.0, st, cfg, now=1.0
        )
        self.assertFalse(done)
        self.assertLess(drive["y"], 0.0)
        self.assertTrue(st2.final_push_used)

    def test_past_goal_fails_for_replan(self) -> None:
        cfg = XyGapCloseConfig(xy_tol_m=0.08)
        st = XyGapCloseState(
            active=True, phase="settle", until=0.0, pulse_count=2, best_dist_m=0.05
        )
        # Robot past goal on +x, facing +x → fwd negative, dist large.
        _, st2, done = tick_xy_gap_close(
            0.4, 0.0, 0.0, 0.0, 0.0, st, cfg, now=1.0
        )
        self.assertTrue(done)
        self.assertEqual(st2.result, "failed")

    def test_forward_when_facing_goal(self) -> None:
        cfg = XyGapCloseConfig(settle_s=0.0)
        st = XyGapCloseState(active=True, phase="settle", until=0.0)
        drive, _, done = tick_xy_gap_close(
            0.0, 0.0, 0.0, 0.5, 0.0, st, cfg, now=1.0
        )
        self.assertFalse(done)
        self.assertLess(drive["y"], 0.0)
        self.assertEqual(drive["x"], 0.0)

    def test_commit_stops_micro_chase(self) -> None:
        cfg = XyGapCloseConfig(xy_tol_m=0.08)
        st = XyGapCloseState(active=True)
        _, _, done = tick_xy_gap_close(0.0, 0.0, 0.0, 0.07, 0.0, st, cfg, now=0.0)
        self.assertTrue(done)


if __name__ == "__main__":
    unittest.main()
