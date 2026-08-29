#!/usr/bin/env python3
"""Unit tests for pose continuity on realistic floorplans."""

from __future__ import annotations

import math
import unittest

from continuity_maps import MAPS
from pose_continuity import (
    PoseCandidate,
    PoseHistory,
    accept_pose_jump,
    continuity_adjusted_score,
    continuity_bonus,
    select_continuous_match,
    select_nearest_strong_match,
)


def _xy(map_id: str, label: str) -> tuple[float, float]:
    return MAPS[map_id].poses[label]


def _cand(map_id: str, label: str, score: float) -> PoseCandidate:
    x, y = _xy(map_id, label)
    return PoseCandidate(x=x, y=y, score=score, label=label)


class PoseContinuityGateTests(unittest.TestCase):
    def test_small_jump_always_accepted(self) -> None:
        self.assertTrue(
            accept_pose_jump(
                jump_m=0.3,
                score_near=0.4,
                score_far=0.2,
                teleport_m=0.8,
            )
        )

    def test_weak_distant_match_rejected(self) -> None:
        self.assertFalse(
            accept_pose_jump(
                jump_m=2.5,
                score_near=0.45,
                score_far=0.48,
                teleport_m=0.8,
                strong_score=0.55,
                margin=0.12,
            )
        )

    def test_strong_kidnap_match_accepted(self) -> None:
        self.assertTrue(
            accept_pose_jump(
                jump_m=4.0,
                score_near=0.20,
                score_far=0.78,
                teleport_m=0.8,
                strong_score=0.55,
                margin=0.12,
            )
        )

    def test_global_reloc_accepts_when_near_also_strong(self) -> None:
        """Reposition is exempt from continuity: near≈far≈0.97 may still snap."""
        self.assertTrue(
            accept_pose_jump(
                jump_m=4.5,
                score_near=0.97,
                score_far=0.97,
                teleport_m=0.8,
                strong_score=0.55,
                margin=0.12,
                global_reloc=True,
            )
        )
        # Same scores without reloc flag still rejected (lookalike guard).
        self.assertFalse(
            accept_pose_jump(
                jump_m=4.5,
                score_near=0.97,
                score_far=0.97,
                teleport_m=0.8,
                strong_score=0.55,
                margin=0.12,
                global_reloc=False,
            )
        )

    def test_global_reloc_accepts_when_local_match_collapsed(self) -> None:
        """True kidnap under reloc flag: near dead, far strong → accept."""
        self.assertTrue(
            accept_pose_jump(
                jump_m=4.5,
                score_near=0.18,
                score_far=0.91,
                teleport_m=0.8,
                strong_score=0.55,
                margin=0.12,
                global_reloc=True,
            )
        )

    def test_impossible_speed_rejected_without_far_score(self) -> None:
        self.assertFalse(
            accept_pose_jump(
                jump_m=4.0,
                score_near=0.5,
                score_far=None,
                teleport_m=0.8,
                max_speed_mps=0.55,
                dt_s=1.0,
            )
        )

    def test_distant_without_score_rejected(self) -> None:
        self.assertFalse(
            accept_pose_jump(jump_m=3.0, score_near=0.5, score_far=None)
        )

    def test_continuity_bonus_prefers_near(self) -> None:
        hist = PoseHistory(maxlen=4)
        hist.push(1.0, 2.0, 0.0)
        hist.push(1.1, 2.0, 0.0)
        near = continuity_bonus(1.05, 2.0, hist, weight=8.0, sigma_m=0.4)
        far = continuity_bonus(5.0, 8.0, hist, weight=8.0, sigma_m=0.4)
        self.assertGreater(near, far)
        self.assertGreater(near, 5.0)
        self.assertLess(far, 0.1)

    def test_history_jump_from_mean(self) -> None:
        hist = PoseHistory(maxlen=3)
        hist.push(0.0, 0.0, 0.0)
        hist.push(0.2, 0.0, 0.0)
        hist.push(0.4, 0.0, 0.0)
        self.assertAlmostEqual(hist.jump_m(0.2, 0.0), 0.0, places=3)
        self.assertAlmostEqual(hist.jump_m(2.2, 0.0), 2.0, places=3)


class OfficeFloorplanTests(unittest.TestCase):
    """Office suite: lobby/hall + two identical meeting rooms."""

    MAP = "office"

    def test_identical_meeting_rooms_pick_nearest(self) -> None:
        """Meet A/B look the same; last at door_b → Meet B, not Meet A."""
        lx, ly = _xy(self.MAP, "door_b")
        pick = select_nearest_strong_match(
            lx,
            ly,
            [
                _cand(self.MAP, "meet_a", 0.95),
                _cand(self.MAP, "meet_b", 0.95),
                _cand(self.MAP, "lobby", 0.40),
            ],
        )
        assert pick is not None
        self.assertEqual(pick.label, "meet_b")
        ax, ay = _xy(self.MAP, "meet_a")
        self.assertLess(
            math.hypot(pick.x - lx, pick.y - ly),
            math.hypot(ax - lx, ay - ly),
        )

    def test_reject_teleport_to_lookalike_meeting_room(self) -> None:
        lx, ly = _xy(self.MAP, "door_b")
        tx, ty = _xy(self.MAP, "meet_a")
        jump = math.hypot(tx - lx, ty - ly)
        self.assertGreater(jump, 0.8)
        self.assertFalse(
            accept_pose_jump(
                jump_m=jump,
                score_near=0.90,
                score_far=0.95,
                teleport_m=0.8,
                strong_score=0.55,
                margin=0.12,
                global_reloc=False,
            )
        )

    def test_stay_in_hall_mid_among_equal_hall_scores(self) -> None:
        lx, ly = _xy(self.MAP, "hall_mid")
        pick = select_nearest_strong_match(
            lx,
            ly,
            [
                _cand(self.MAP, "hall_west", 0.92),
                _cand(self.MAP, "hall_mid", 0.93),
                _cand(self.MAP, "hall_east", 0.92),
            ],
        )
        assert pick is not None
        self.assertEqual(pick.label, "hall_mid")

    def test_adjusted_score_breaks_meeting_room_tie(self) -> None:
        lx, ly = _xy(self.MAP, "door_b")
        a = _cand(self.MAP, "meet_a", 0.95)
        b = _cand(self.MAP, "meet_b", 0.95)
        self.assertGreater(
            continuity_adjusted_score(b, lx, ly),
            continuity_adjusted_score(a, lx, ly),
        )


class WarehouseFloorplanTests(unittest.TestCase):
    """Three identical rack aisles — classic teleport trap."""

    MAP = "warehouse"

    def test_aisle_lookalikes_stay_in_current_aisle(self) -> None:
        lx, ly = _xy(self.MAP, "aisle2_mid")
        pick = select_nearest_strong_match(
            lx,
            ly,
            [
                _cand(self.MAP, "aisle1_mid", 0.94),
                _cand(self.MAP, "aisle2_mid", 0.94),
                _cand(self.MAP, "aisle3_mid", 0.94),
            ],
        )
        assert pick is not None
        self.assertEqual(pick.label, "aisle2_mid")

    def test_reject_parallel_aisle_teleport(self) -> None:
        lx, ly = _xy(self.MAP, "aisle2_mid")
        for bad in ("aisle1_mid", "aisle3_mid"):
            tx, ty = _xy(self.MAP, bad)
            jump = math.hypot(tx - lx, ty - ly)
            self.assertFalse(
                accept_pose_jump(
                    jump_m=jump,
                    score_near=0.94,
                    score_far=0.94,
                    global_reloc=False,
                ),
                msg=f"must not teleport aisle2→{bad}",
            )

    def test_temp_pallet_keeps_occluded_aisle(self) -> None:
        """Pallet drops aisle2 score; clean aisle1/3 still look perfect."""
        lx, ly = _xy(self.MAP, "aisle2_mid")
        cands = [
            _cand(self.MAP, "aisle2_mid", 0.70),
            _cand(self.MAP, "aisle1_mid", 0.95),
            _cand(self.MAP, "aisle3_mid", 0.95),
        ]
        pick = select_continuous_match(
            lx, ly, cands, min_score=0.55, local_radius_m=3.0
        )
        assert pick is not None
        self.assertEqual(pick.label, "aisle2_mid")

    def test_temp_pallet_rejects_cleaner_aisle_jump(self) -> None:
        lx, ly = _xy(self.MAP, "aisle2_mid")
        tx, ty = _xy(self.MAP, "aisle3_mid")
        self.assertFalse(
            accept_pose_jump(
                jump_m=math.hypot(tx - lx, ty - ly),
                score_near=0.70,
                score_far=0.95,
                strong_score=0.55,
                margin=0.12,
                global_reloc=False,
            )
        )


class ApartmentFloorplanTests(unittest.TestCase):
    """L-shaped apartment: living / kitchen / bedroom."""

    MAP = "apartment"

    def test_moved_chair_stays_in_living_room(self) -> None:
        lx, ly = _xy(self.MAP, "living")
        pick = select_continuous_match(
            lx,
            ly,
            [
                _cand(self.MAP, "living", 0.68),
                _cand(self.MAP, "kitchen", 0.55),
                _cand(self.MAP, "bedroom", 0.50),
                _cand(self.MAP, "entry", 0.88),
            ],
            min_score=0.55,
            local_radius_m=3.0,
        )
        assert pick is not None
        self.assertEqual(pick.label, "living")

    def test_moved_chair_rejects_entry_teleport(self) -> None:
        lx, ly = _xy(self.MAP, "living")
        tx, ty = _xy(self.MAP, "entry")
        self.assertFalse(
            accept_pose_jump(
                jump_m=math.hypot(tx - lx, ty - ly),
                score_near=0.68,
                score_far=0.88,
                global_reloc=False,
            )
        )

    def test_walk_to_kitchen_when_living_match_collapses(self) -> None:
        lx, ly = _xy(self.MAP, "living")
        pick = select_continuous_match(
            lx,
            ly,
            [
                _cand(self.MAP, "living", 0.40),
                _cand(self.MAP, "kitchen", 0.91),
                _cand(self.MAP, "bedroom", 0.40),
            ],
            min_score=0.55,
            local_radius_m=3.0,
        )
        assert pick is not None
        self.assertEqual(pick.label, "kitchen")

    def test_accept_kitchen_reloc_after_living_collapsed(self) -> None:
        lx, ly = _xy(self.MAP, "living")
        tx, ty = _xy(self.MAP, "kitchen")
        self.assertTrue(
            accept_pose_jump(
                jump_m=math.hypot(tx - lx, ty - ly),
                score_near=0.40,
                score_far=0.91,
                strong_score=0.55,
                margin=0.12,
                global_reloc=False,
            )
        )


class YardFloorplanTests(unittest.TestCase):
    """Fenced parking: two similar car bays + shed."""

    MAP = "yard"

    def test_car_bay_lookalikes_pick_current_car(self) -> None:
        lx, ly = _xy(self.MAP, "beside_car_a")
        pick = select_nearest_strong_match(
            lx,
            ly,
            [
                _cand(self.MAP, "beside_car_a", 0.93),
                _cand(self.MAP, "beside_car_b", 0.93),
                _cand(self.MAP, "shed", 0.35),
            ],
        )
        assert pick is not None
        self.assertEqual(pick.label, "beside_car_a")

    def test_reject_snap_to_other_car(self) -> None:
        lx, ly = _xy(self.MAP, "beside_car_a")
        tx, ty = _xy(self.MAP, "beside_car_b")
        self.assertFalse(
            accept_pose_jump(
                jump_m=math.hypot(tx - lx, ty - ly),
                score_near=0.93,
                score_far=0.93,
                global_reloc=False,
            )
        )

    def test_genuine_kidnap_to_shed(self) -> None:
        lx, ly = _xy(self.MAP, "gate")
        pick = select_nearest_strong_match(
            lx,
            ly,
            [
                _cand(self.MAP, "gate", 0.18),
                _cand(self.MAP, "shed", 0.91),
                _cand(self.MAP, "bay_mid", 0.25),
            ],
        )
        assert pick is not None
        self.assertEqual(pick.label, "shed")
        tx, ty = _xy(self.MAP, "shed")
        self.assertTrue(
            accept_pose_jump(
                jump_m=math.hypot(tx - lx, ty - ly),
                score_near=0.18,
                score_far=0.91,
                global_reloc=False,
            )
        )


class ContinuousVsNearestPolicyTests(unittest.TestCase):
    """Temp-object: continuous local preference vs pure nearest-strong."""

    def test_continuous_prefers_local_occluded_over_distant_clean(self) -> None:
        lx, ly = _xy("warehouse", "aisle2_mid")
        cands = [
            _cand("warehouse", "aisle2_mid", 0.70),
            _cand("warehouse", "aisle3_mid", 0.95),
        ]
        cont = select_continuous_match(lx, ly, cands, local_radius_m=3.0)
        near = select_nearest_strong_match(lx, ly, cands)
        assert cont is not None and near is not None
        self.assertEqual(cont.label, "aisle2_mid")
        # Nearest-strong also keeps aisle2 because it is a candidate and closer.
        self.assertEqual(near.label, "aisle2_mid")

    def test_no_strong_candidates_returns_none(self) -> None:
        lx, ly = _xy("office", "lobby")
        self.assertIsNone(
            select_nearest_strong_match(
                lx,
                ly,
                [
                    _cand("office", "meet_a", 0.2),
                    _cand("office", "meet_b", 0.25),
                ],
                min_score=0.55,
            )
        )

    def test_adjusted_score_does_not_override_clearly_better_match(self) -> None:
        lx, ly = _xy("yard", "gate")
        near_weak = _cand("yard", "bay_mid", 0.50)
        far_strong = _cand("yard", "shed", 0.95)
        self.assertGreater(
            continuity_adjusted_score(far_strong, lx, ly),
            continuity_adjusted_score(near_weak, lx, ly),
        )


if __name__ == "__main__":
    unittest.main()
