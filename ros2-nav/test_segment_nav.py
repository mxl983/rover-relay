"""Unit tests for segment-based path following."""

from __future__ import annotations

import math
import unittest

from segment_nav import (
    SegmentNavConfig,
    SegmentNavState,
    nearest_segment_index,
    next_segment_step,
    segment_drive_in_cruise,
    segment_drive_pulse_sec,
    segmentize_path,
    segment_start_drift_m,
    trim_path_from_pose,
)


class SegmentNavTests(unittest.TestCase):
    def test_segmentize_merges_collinear(self) -> None:
        path = [[0.0, 0.0], [0.1, 0.0], [0.2, 0.0], [0.2, 0.2]]
        segs = segmentize_path(path, min_segment_m=0.05, max_corner_deg=15.0)
        self.assertEqual(len(segs), 2)
        self.assertAlmostEqual(segs[0].length_m, 0.2, places=2)
        self.assertAlmostEqual(segs[0].heading_deg, 0.0, places=1)

    def test_align_then_drive(self) -> None:
        segs = segmentize_path([[0.0, 0.0], [0.5, 0.0]], min_segment_m=0.05)
        cfg = SegmentNavConfig(align_final_tol_rad=math.radians(5.0))
        st = SegmentNavState(segment_index=0, phase="align")
        step = next_segment_step(0.0, 0.0, math.radians(20), segs, st, cfg=cfg)
        self.assertEqual(step.keys, ["d"])
        self.assertEqual(step.phase, "seg_align")

        st2 = SegmentNavState(segment_index=0, phase="align")
        step2 = next_segment_step(0.0, 0.0, 0.0, segs, st2, cfg=cfg)
        self.assertEqual(step2.keys, [])
        self.assertEqual(step2.state.phase, "drive")

        step3 = next_segment_step(0.0, 0.0, 0.0, segs, step2.state, cfg=cfg)
        self.assertEqual(step3.keys, ["w"])
        self.assertEqual(step3.phase, "seg_drive")

    def test_align_done_within_tol(self) -> None:
        segs = segmentize_path([[0.0, 0.0], [0.5, 0.0]], min_segment_m=0.05)
        cfg = SegmentNavConfig(align_final_tol_rad=math.radians(25.0))
        st = SegmentNavState(segment_index=0, phase="align")
        # 20° is within the loose phase-2 tol — drive without correcting.
        step = next_segment_step(0.0, 0.0, math.radians(20.0), segs, st, cfg=cfg)
        self.assertEqual(step.keys, [])
        self.assertEqual(step.state.phase, "drive")

    def test_align_skipped_when_within_loose_tol(self) -> None:
        segs = segmentize_path([[0.0, 0.0], [0.5, 0.0]], min_segment_m=0.05)
        cfg = SegmentNavConfig(align_final_tol_rad=math.radians(25.0))
        st = SegmentNavState(segment_index=0, phase="align")
        far = next_segment_step(0.0, 0.0, math.radians(40.0), segs, st, cfg=cfg)
        self.assertEqual(far.keys, ["d"])

    def test_no_drift_replan_while_yaw_still_large(self) -> None:
        segs = segmentize_path([[0.0, 0.0], [0.5, 0.0]], min_segment_m=0.05)
        st = SegmentNavState(segment_index=0, phase="align")
        cfg = SegmentNavConfig(
            drift_replan_m=0.20,
            align_final_tol_rad=math.radians(25.0),
        )
        # 0.35m off start but still 50° yaw — must NOT replan (skid during turn).
        step = next_segment_step(0.0, 0.35, math.radians(50.0), segs, st, cfg=cfg)
        self.assertFalse(step.replan)
        self.assertEqual(step.keys, ["d"])

    def test_align_pulse_scales_with_error(self) -> None:
        segs = segmentize_path([[0.0, 0.0], [0.5, 0.0]], min_segment_m=0.05)
        cfg = SegmentNavConfig(
            align_pulse_s=0.07,
            align_pulse_mid_s=0.22,
            align_pulse_large_s=0.30,
            align_overshoot_pulse_s=0.035,
            align_final_tol_rad=math.radians(5.0),
        )
        st = SegmentNavState(segment_index=0, phase="align")
        far = next_segment_step(0.0, 0.0, math.radians(50), segs, st, cfg=cfg)
        self.assertAlmostEqual(far.pulse_on_s, 0.30, places=3)

        st2 = SegmentNavState(segment_index=0, phase="align")
        # 25° is below the 30° large threshold → fine taps.
        mid = next_segment_step(0.0, 0.0, math.radians(25), segs, st2, cfg=cfg)
        self.assertAlmostEqual(mid.pulse_on_s, 0.07, places=3)

        st2b = SegmentNavState(segment_index=0, phase="align")
        large_edge = next_segment_step(0.0, 0.0, math.radians(30), segs, st2b, cfg=cfg)
        self.assertAlmostEqual(large_edge.pulse_on_s, 0.30, places=3)

        st3 = SegmentNavState(segment_index=0, phase="align")
        near = next_segment_step(0.0, 0.0, math.radians(8), segs, st3, cfg=cfg)
        self.assertAlmostEqual(near.pulse_on_s, 0.07, places=3)

        st4 = SegmentNavState(segment_index=0, phase="align", approach_sign=-1)
        over = next_segment_step(0.0, 0.0, math.radians(-8), segs, st4, cfg=cfg)
        self.assertAlmostEqual(over.pulse_on_s, 0.035, places=3)

        # Large remaining after a false overshoot still uses large bites.
        st5 = SegmentNavState(segment_index=0, phase="align", approach_sign=-1)
        big_over = next_segment_step(0.0, 0.0, math.radians(-50), segs, st5, cfg=cfg)
        self.assertAlmostEqual(big_over.pulse_on_s, 0.30, places=3)

    def test_segmentize_keeps_curved_short_pieces(self) -> None:
        # Mimic Nav2: long straight then many short corner samples under min_segment.
        path = [[0.0, 0.0], [1.0, 0.0]]
        x, y = 1.0, 0.0
        for deg in (20, 40, 60, 80, 100):
            rad = math.radians(deg)
            x += 0.12 * math.cos(rad)
            y += 0.12 * math.sin(rad)
            path.append([x, y])
        segs = segmentize_path(path, min_segment_m=0.18, max_corner_deg=12.0)
        total = sum(s.length_m for s in segs)
        self.assertGreater(len(segs), 1)
        self.assertGreater(total, 1.4)

    def test_segments_exhausted_far_from_goal_replans(self) -> None:
        segs = segmentize_path([[0.0, 0.0], [0.3, 0.0]], min_segment_m=0.05)
        st = SegmentNavState(
            segment_index=0,
            phase="drive",
            drive_origin=(0.0, 0.0),
        )
        cfg = SegmentNavConfig(goal_arrive_m=0.05, goal_handoff_m=0.22)
        step = next_segment_step(
            0.29, 0.0, 0.0, segs, st, cfg=cfg, goal_xy=(1.0, 0.0)
        )
        self.assertTrue(step.replan)
        self.assertEqual(step.phase, "replan_short")
        self.assertFalse(step.done)

    def test_near_goal_hands_off_instead_of_spinning(self) -> None:
        """Last-run lesson: 0.06–0.15m crumbs + 180° align = death spiral."""
        segs = segmentize_path([[2.70, -3.94], [2.66, -3.96]], min_segment_m=0.01)
        st = SegmentNavState(segment_index=0, phase="align")
        cfg = SegmentNavConfig(goal_handoff_m=0.22, goal_arrive_m=0.05)
        # Pose already next to the marker; leftover segment aims backward.
        step = next_segment_step(
            2.68, -4.00, math.radians(-70), segs, st, cfg=cfg, goal_xy=(2.659, -3.96)
        )
        self.assertTrue(step.done)
        self.assertIn("hand off", step.note)

    def test_micro_segment_align_skipped_not_spin_spiral(self) -> None:
        """nav-20260825-021406: 9cm east crumb + 10cm south offset → aim −90° forever."""
        from segment_nav import Segment, segment_aim_heading

        crumb = Segment(
            x0=1.719,
            y0=-1.772,
            x1=1.808,
            y1=-1.776,
            heading_rad=math.radians(-2.6),
            length_m=0.089,
        )
        long_south = Segment(
            x0=1.808,
            y0=-1.776,
            x1=1.776,
            y1=-2.148,
            heading_rad=math.atan2(-2.148 - (-1.776), 1.776 - 1.808),
            length_m=0.373,
        )
        # Live aim from offset pose must NOT return ~−90° for the crumb.
        aim = segment_aim_heading(1.806, -1.677, crumb)
        self.assertLess(abs(math.degrees(aim) - (-2.6)), 5.0)

        segs = [crumb, long_south]
        st = SegmentNavState(segment_index=0, phase="align")
        cfg = SegmentNavConfig(align_final_tol_rad=math.radians(25.0))
        # Facing roughly south already (−22°) — should skip crumb and work on south seg.
        step = next_segment_step(
            1.806, -1.677, math.radians(-22.0), segs, st, cfg=cfg, goal_xy=(2.659, -3.96)
        )
        self.assertEqual(step.state.segment_index, 1)
        # Recursive call overwrites note with the next seg's action — index is the proof.

    def test_exhausted_near_goal_does_not_replan(self) -> None:
        segs = segmentize_path([[0.0, 0.0], [0.3, 0.0]], min_segment_m=0.05)
        st = SegmentNavState(segment_index=1, phase="align")  # past last
        cfg = SegmentNavConfig(goal_handoff_m=0.22, goal_arrive_m=0.05)
        step = next_segment_step(
            0.28, 0.0, 0.0, segs, st, cfg=cfg, goal_xy=(0.35, 0.0)
        )
        self.assertTrue(step.done)
        self.assertFalse(step.replan)

    def test_align_uses_consistent_step_size(self) -> None:
        segs = segmentize_path([[0.0, 0.0], [0.5, 0.0]], min_segment_m=0.05)
        cfg = SegmentNavConfig(
            align_pulse_s=0.06,
            align_overshoot_pulse_s=0.035,
            align_final_tol_rad=math.radians(5.0),
        )
        st = SegmentNavState(segment_index=0, phase="align")
        step_far = next_segment_step(0.0, 0.0, math.radians(8), segs, st, cfg=cfg)
        self.assertEqual(step_far.keys, ["d"])
        self.assertAlmostEqual(step_far.pulse_on_s, 0.06, places=3)

        st2 = SegmentNavState(segment_index=0, phase="align", approach_sign=-1)
        step_over = next_segment_step(0.0, 0.0, math.radians(-8), segs, st2, cfg=cfg)
        self.assertEqual(step_over.keys, ["a"])
        self.assertAlmostEqual(step_over.pulse_on_s, 0.035, places=3)

    def test_drive_pulse_scales_with_remaining(self) -> None:
        cfg = SegmentNavConfig(
            drive_tol_m=0.03,
            drive_pulse_large_s=0.25,
            drive_pulse_mid_s=0.20,
            drive_pulse_s=0.15,
            drive_pulse_tiny_s=0.15,
            drive_cruise_min_segment_m=99.0,  # force stepper path
        )
        self.assertEqual(segment_drive_pulse_sec(0.80, cfg), 0.25)
        self.assertEqual(segment_drive_pulse_sec(0.20, cfg), 0.25)
        self.assertEqual(segment_drive_pulse_sec(0.14, cfg), 0.20)
        self.assertEqual(segment_drive_pulse_sec(0.08, cfg), 0.15)
        self.assertEqual(segment_drive_pulse_sec(0.02, cfg), 0.0)
        self.assertEqual(
            segment_drive_pulse_sec(0.80, cfg, goal_dist_m=0.08), 0.15
        )

    def test_cruise_then_step_on_1m_segment(self) -> None:
        """1m segment: first 80% continuous (~2s), last 20% stepper."""
        cfg = SegmentNavConfig(
            drive_cruise_mps=0.40,
            drive_cruise_fraction=0.80,
            drive_cruise_min_segment_m=0.50,
            drive_cruise_max_s=2.5,
            drive_pulse_large_s=0.25,
            drive_pulse_s=0.15,
        )
        # At start of 1m: cruise 0.8m / 0.40 = 2.0s
        self.assertAlmostEqual(
            segment_drive_pulse_sec(1.0, cfg, segment_length_m=1.0), 2.0, places=2
        )
        self.assertTrue(segment_drive_in_cruise(1.0, cfg, segment_length_m=1.0))
        # Deep in last 20% (rem=0.12): stepper mid pulse
        self.assertAlmostEqual(
            segment_drive_pulse_sec(0.12, cfg, segment_length_m=1.0), 0.20, places=3
        )
        self.assertFalse(segment_drive_in_cruise(0.12, cfg, segment_length_m=1.0))
        # Still cruising with rem=0.35 (>0.20 zone): (0.35-0.20)/0.40 = 0.375s
        self.assertTrue(segment_drive_in_cruise(0.35, cfg, segment_length_m=1.0))
        self.assertAlmostEqual(
            segment_drive_pulse_sec(0.35, cfg, segment_length_m=1.0),
            0.375,
            places=2,
        )

    def test_short_segment_uses_scaled_pulse(self) -> None:
        segs = segmentize_path([[0.0, 0.0], [0.25, 0.0]], min_segment_m=0.05)
        st = SegmentNavState(segment_index=0, phase="drive", drive_origin=(0.0, 0.0))
        cfg = SegmentNavConfig(
            drive_pulse_s=0.15,
            drive_pulse_tiny_s=0.15,
            drive_pulse_large_s=0.25,
            drive_cruise_min_segment_m=0.50,
        )
        step = next_segment_step(0.0, 0.0, 0.0, segs, st, cfg=cfg)
        self.assertEqual(step.keys, ["w"])
        self.assertAlmostEqual(step.pulse_on_s, 0.25, places=3)

    def test_near_goal_uses_tiny_pulse(self) -> None:
        segs = segmentize_path([[0.0, 0.0], [0.5, 0.0]], min_segment_m=0.05)
        st = SegmentNavState(segment_index=0, phase="drive", drive_origin=(0.0, 0.0))
        cfg = SegmentNavConfig(
            drive_pulse_tiny_s=0.15,
            drive_pulse_s=0.15,
            drive_pulse_large_s=0.25,
            goal_arrive_m=0.05,
            goal_handoff_m=0.05,
            drive_cruise_min_segment_m=0.50,
        )
        step = next_segment_step(
            0.40, 0.0, 0.0, segs, st, cfg=cfg, goal_xy=(0.48, 0.0)
        )
        self.assertEqual(step.keys, ["w"])
        self.assertAlmostEqual(step.pulse_on_s, 0.15, places=3)

    def test_1m_segment_cruise_pulse_in_step(self) -> None:
        segs = segmentize_path([[0.0, 0.0], [1.0, 0.0]], min_segment_m=0.05)
        st = SegmentNavState(segment_index=0, phase="drive", drive_origin=(0.0, 0.0))
        cfg = SegmentNavConfig(
            drive_cruise_mps=0.40,
            drive_cruise_fraction=0.80,
            drive_cruise_min_segment_m=0.50,
            align_final_tol_rad=math.radians(25.0),
        )
        step = next_segment_step(0.0, 0.0, 0.0, segs, st, cfg=cfg)
        self.assertEqual(step.keys, ["w"])
        self.assertIn("cruise", step.note)
        self.assertAlmostEqual(step.pulse_on_s, 2.0, places=2)

    def test_stops_drive_when_at_goal(self) -> None:
        segs = segmentize_path([[0.0, 0.0], [0.5, 0.0], [1.0, 0.0]], min_segment_m=0.05)
        st = SegmentNavState(segment_index=0, phase="drive", drive_origin=(0.0, 0.0))
        cfg = SegmentNavConfig(
            goal_arrive_m=0.05, goal_handoff_m=0.05, drive_pulse_s=0.03
        )
        step = next_segment_step(
            0.97, 0.01, 0.0, segs, st, cfg=cfg, goal_xy=(1.0, 0.0)
        )
        self.assertTrue(step.done)
        self.assertIn("hand off", step.note)

    def test_drive_completes_at_segment_endpoint(self) -> None:
        segs = segmentize_path([[0.0, 0.0], [0.3, 0.0]], min_segment_m=0.05)
        st = SegmentNavState(
            segment_index=0,
            phase="drive",
            drive_origin=(0.0, 0.0),
        )
        step = next_segment_step(0.29, 0.02, 0.0, segs, st)
        self.assertTrue(step.done)
        self.assertEqual(step.state.segment_index, 1)

    def test_drive_completes_segment(self) -> None:
        segs = segmentize_path([[0.0, 0.0], [0.3, 0.0]], min_segment_m=0.05)
        st = SegmentNavState(
            segment_index=0,
            phase="drive",
            drive_origin=(0.0, 0.0),
        )
        step = next_segment_step(0.28, 0.0, 0.0, segs, st)
        self.assertTrue(step.done)
        self.assertEqual(step.state.segment_index, 1)

    def test_drift_triggers_replan(self) -> None:
        segs = segmentize_path([[0.0, 0.0], [0.5, 0.0], [0.5, 0.5]], min_segment_m=0.05)
        # Facing the segment end (yaw within tol) but far from its planned start.
        st = SegmentNavState(segment_index=1, phase="align")
        cfg = SegmentNavConfig(
            drift_replan_m=0.20,
            align_final_tol_rad=math.radians(25.0),
        )
        # Aim at (0.5, 0.5) from (-0.3, 0.5) ≈ east; yaw 0° is close enough.
        step = next_segment_step(-0.3, 0.5, 0.0, segs, st, cfg=cfg)
        self.assertTrue(step.replan)
        self.assertEqual(step.phase, "replan_drift")

    def test_forward_blocked_triggers_replan(self) -> None:
        segs = segmentize_path([[0.0, 0.0], [0.5, 0.0]], min_segment_m=0.05)
        st = SegmentNavState(segment_index=0, phase="drive", drive_origin=(0.0, 0.0))
        step = next_segment_step(0.1, 0.0, 0.0, segs, st, forward_blocked=True)
        self.assertTrue(step.blocked)
        self.assertTrue(step.replan)

    def test_nearest_segment_index(self) -> None:
        segs = segmentize_path([[0.0, 0.0], [0.5, 0.0], [0.5, 0.5]], min_segment_m=0.05)
        idx = nearest_segment_index(0.48, 0.02, segs)
        self.assertEqual(idx, 1)
        drift = segment_start_drift_m(0.48, 0.02, segs[1])
        self.assertLess(drift, 0.05)

    def test_trim_path_from_pose(self) -> None:
        path = [[0.0, 0.0], [0.1, 0.0], [0.2, 0.0], [0.2, 0.5], [0.2, 1.0]]
        trimmed = trim_path_from_pose(0.19, 0.01, path)
        # First vertex is always the rover pose.
        self.assertEqual(trimmed[0], [0.19, 0.01])
        self.assertGreaterEqual(len(trimmed), 3)
        segs = segmentize_path(trimmed, min_segment_m=0.05)
        self.assertGreaterEqual(segs[0].heading_deg, 80.0)

    def test_trim_snaps_path_start_to_rover(self) -> None:
        # Phase-1 skid: path waypoint is 8cm from rover (matches latest run).
        path = [[1.97, -0.742], [1.70, -3.092], [2.66, -3.96]]
        trimmed = trim_path_from_pose(1.887, -0.734, path)
        self.assertEqual(trimmed[0], [1.887, -0.734])
        segs = segmentize_path(trimmed, min_segment_m=0.05)
        self.assertAlmostEqual(segs[0].x0, 1.887, places=3)
        self.assertAlmostEqual(segs[0].y0, -0.734, places=3)
        self.assertLess(segment_start_drift_m(1.887, -0.734, segs[0]), 0.01)

    def test_midflight_steer_while_driving(self) -> None:
        segs = segmentize_path([[0.0, 0.0], [1.5, 0.0]], min_segment_m=0.05)
        st = SegmentNavState(
            segment_index=0,
            phase="drive",
            drive_origin=(0.0, 0.0),
        )
        cfg = SegmentNavConfig(
            align_final_tol_rad=math.radians(12.0),
            midflight_steer_min_rad=math.radians(5.0),
            midflight_steer_max_rad=math.radians(22.0),
            midflight_steer_pulse_s=0.035,
            midflight_min_remain_m=0.40,
            midflight_min_segment_m=0.45,
            drive_cruise_min_segment_m=99.0,  # exercise stepper midflight path
        )
        # 10° off heading mid-segment → tiny D pulse, not a full realign.
        step = next_segment_step(0.4, 0.0, math.radians(10.0), segs, st, cfg=cfg)
        self.assertEqual(step.keys, ["d"])
        self.assertEqual(step.phase, "seg_midflight")
        self.assertAlmostEqual(step.pulse_on_s, 0.035, places=3)
        # Still in drive phase after the tap.
        self.assertEqual(step.state.phase, "drive")

        # Straight-on → keep W.
        st2 = SegmentNavState(
            segment_index=0, phase="drive", drive_origin=(0.0, 0.0)
        )
        step2 = next_segment_step(0.4, 0.0, 0.0, segs, st2, cfg=cfg)
        self.assertEqual(step2.keys, ["w"])
        self.assertEqual(step2.phase, "seg_drive")

    def test_drive_realigns_when_yaw_beyond_tol(self) -> None:
        """nav-20260825-030144: ~26° yaw error while driving stalled rem forever."""
        segs = segmentize_path([[0.0, 0.0], [1.5, 0.0]], min_segment_m=0.05)
        st = SegmentNavState(
            segment_index=0, phase="drive", drive_origin=(0.0, 0.0)
        )
        cfg = SegmentNavConfig(align_final_tol_rad=math.radians(12.0))
        step = next_segment_step(0.5, 0.0, math.radians(26.0), segs, st, cfg=cfg)
        self.assertEqual(step.state.phase, "align")
        self.assertEqual(step.keys, [])
        self.assertIn("realign", step.note)

    def test_midflight_skipped_on_short_remainder(self) -> None:
        """nav-20260825-022547: rem≈0.22m midflight A/D ate ~40s, never advanced."""
        segs = segmentize_path([[0.0, 0.0], [0.5, 0.0]], min_segment_m=0.05)
        st = SegmentNavState(segment_index=0, phase="drive", drive_origin=(0.0, 0.0))
        cfg = SegmentNavConfig(
            midflight_min_remain_m=0.40,
            midflight_min_segment_m=0.45,
            midflight_steer_min_rad=math.radians(5.0),
            midflight_steer_max_rad=math.radians(22.0),
        )
        step = next_segment_step(0.28, 0.0, math.radians(12.0), segs, st, cfg=cfg)
        self.assertEqual(step.keys, ["w"])
        self.assertEqual(step.phase, "seg_drive")

    def test_merge_near_collinear_reduces_count(self) -> None:
        from segment_nav import merge_near_collinear_segments, Segment
        import math

        segs = [
            Segment(0, 0, 0.2, 0, 0.0, 0.2),
            Segment(0.2, 0, 0.4, 0.02, math.atan2(0.02, 0.2), 0.201),
            Segment(0.4, 0.02, 0.4, 0.3, math.pi / 2, 0.28),
        ]
        merged = merge_near_collinear_segments(segs, max_heading_deg=15.0)
        self.assertEqual(len(merged), 2)
        self.assertGreater(merged[0].length_m, 0.35)


if __name__ == "__main__":
    unittest.main()
