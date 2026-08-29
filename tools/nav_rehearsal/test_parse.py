"""Smoke tests for nav rehearsal parsing (stdlib unittest)."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from tools.nav_rehearsal.mapdata import load_best_map, load_slam_map
from tools.nav_rehearsal.parse import (
    build_frames,
    canonical_nav_id,
    latest_nav_id,
    list_runs,
    load_jsonl,
    parse_fine_dock_log_events,
)


class NavRehearsalParseTests(unittest.TestCase):
    def test_build_frames_from_minimal_jsonl(self) -> None:
        lines = [
            {
                "ts": 1.0,
                "iso": "2026-01-01T00:00:00Z",
                "event": "goto",
                "nav_id": "nav-test-1",
                "label": "mark-1",
                "target": {"x": 1.0, "y": 2.0, "yaw": 0.0},
                "start_pose": {"x": 0.0, "y": 0.0, "yaw": 0.0},
            },
            {
                "ts": 2.0,
                "iso": "2026-01-01T00:00:01Z",
                "event": "progress",
                "nav_id": "nav-test-1",
                "elapsed_s": 1.0,
                "distance_remaining": 2.1,
                "pose": {"x": 0.1, "y": 0.0, "yaw": 0.1, "yaw_deg": 5.7},
                "goal": {"x": 1.0, "y": 2.0, "yaw": 0.0},
                "drive": {
                    "phase": "path_align",
                    "keys": ["d"],
                    "nav_ui": {
                        "phase": 1,
                        "label": "Phase 1 · Align",
                        "yaw_remaining_deg": -40.0,
                        "note": "",
                    },
                },
            },
            {
                "ts": 3.0,
                "iso": "2026-01-01T00:00:02Z",
                "event": "progress",
                "nav_id": "nav-test-1",
                "pose": {"x": 0.5, "y": 0.8, "yaw": 0.2, "yaw_deg": 11.5},
                "goal": {"x": 1.0, "y": 2.0, "yaw": 0.0},
                "drive": {
                    "phase": "segment_drive",
                    "keys": ["w"],
                    "nav_ui": {
                        "phase": 2,
                        "label": "Phase 2 · Segments",
                        "segment": 1,
                        "segments_total": 2,
                    },
                },
            },
            {
                "ts": 4.0,
                "iso": "2026-01-01T00:00:03Z",
                "event": "fine_dock_start",
                "nav_id": "nav-test-1",
                "target": [1.0, 2.0, 0.0],
                "start_pose": {"x": 0.95, "y": 1.9, "yaw": 0.1},
            },
            {
                "ts": 5.0,
                "iso": "2026-01-01T00:00:04Z",
                "event": "dock_finished",
                "nav_id": "nav-test-1",
                "result": "succeeded",
                "pose": {"x": 1.0, "y": 2.0, "yaw": 0.0, "yaw_deg": 0.0},
            },
        ]
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "run.jsonl"
            path.write_text(
                "\n".join(json.dumps(x) for x in lines) + "\n", encoding="utf-8"
            )
            events = load_jsonl(path)
        self.assertEqual(latest_nav_id(events), "nav-test-1")
        runs = list_runs(events)
        self.assertEqual(len(runs), 1)
        self.assertEqual(runs[0].result, "succeeded")
        self.assertEqual(runs[0].phases, [1, 2, 3])
        frames = build_frames(events, "nav-test-1")
        self.assertEqual(len(frames), 5)
        self.assertEqual(frames[1].action_keys, ["d"])
        self.assertIn("Phase 1", frames[1].decision)
        self.assertEqual(frames[2].nav_ui.get("phase"), 2)
        self.assertEqual(frames[3].nav_ui.get("phase"), 3)
        self.assertEqual(frames[4].nav_ui.get("phase"), 3)

    def test_phase3_from_docker_fine_dock_logs(self) -> None:
        events = [
            {
                "ts": 10.0,
                "event": "goto",
                "nav_id": "nav-a",
                "label": "m",
                "target": {"x": 1.0, "y": 0.0, "yaw": 0.0},
                "start_pose": {"x": 0.0, "y": 0.0, "yaw": 0.0},
            },
            {
                "ts": 11.0,
                "event": "progress",
                "nav_id": "nav-a",
                "pose": {"x": 0.9, "y": 0.0, "yaw": 0.0, "yaw_deg": 0.0},
                "goal": {"x": 1.0, "y": 0.0, "yaw": 0.0},
                "drive": {
                    "keys": ["w"],
                    "nav_ui": {"phase": 2, "label": "Phase 2 · Segments"},
                },
            },
            {
                "ts": 12.0,
                "event": "fine_dock_start",
                "nav_id": "nav-a",
                "target": [1.0, 0.0, 0.0],
                "start_pose": {"x": 0.95, "y": 0.0, "yaw": 0.2},
            },
            {
                "ts": 12.5,
                "event": "progress",
                "nav_id": "nav-a",
                "pose": {"x": 0.96, "y": 0.0, "yaw": 0.1, "yaw_deg": 5.7},
                "goal": {"x": 1.0, "y": 0.0, "yaw": 0.0},
                "drive": {"keys": [], "nav_ui": {"phase": None}},
            },
            {
                "ts": 15.0,
                "event": "dock_finished",
                "nav_id": "nav-a",
                "result": "succeeded",
                "pose": {"x": 1.0, "y": 0.0, "yaw": 0.0, "yaw_deg": 0.0},
            },
        ]
        log = "\n".join(
            [
                "[INFO] [12.1] [rover_nav_goal_server]: "
                "Fine dock start nav_id=nav-a Δxy=0.05m Δyaw=10.0° xy_tol=0.16m yaw_tol=5°",
                "[INFO] [12.2] [rover_nav_goal_server]: "
                "Fine dock nav_id=nav-a phase=yaw keys=A Δxy=0.04m fwd=+0.03 left=+0.01 "
                "Δyaw=8.0° hold=0.080s — yaw err +8.0°",
                "[INFO] [13.0] [rover_nav_goal_server]: "
                "Fine dock nav_id=nav-a phase=fwd keys=W Δxy=0.03m fwd=+0.03 left=+0.00 "
                "Δyaw=2.0° hold=0.120s — close gap",
                "[INFO] [14.8] [rover_nav_goal_server]: "
                "Fine dock finished: succeeded nav_id=nav-a final Δxy=0.02m Δyaw=1.0° "
                "phase=done yaw_pulses=2",
            ]
        )
        dock = parse_fine_dock_log_events(log, "nav-a")
        self.assertGreaterEqual(len([e for e in dock if e["event"] == "dock_step"]), 2)
        frames = build_frames(events, "nav-a", docker_log_text=log)
        phases = {f.nav_ui.get("phase") for f in frames}
        self.assertIn(2, phases)
        self.assertIn(3, phases)
        dock_frames = [f for f in frames if f.event == "dock_step"]
        self.assertGreaterEqual(len(dock_frames), 2)
        self.assertEqual(dock_frames[0].action_keys, ["a"])
        self.assertEqual(dock_frames[0].nav_ui.get("phase"), 3)
        self.assertIn("Phase 3", dock_frames[0].nav_ui.get("label", ""))


class NavRehearsalMapTests(unittest.TestCase):
    def test_load_map_points(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "slam_live.json"
            path.write_text(
                json.dumps(
                    {
                        "resolution": 0.05,
                        "origin": {"x": -1.0, "y": -1.0},
                        "width": 40,
                        "height": 40,
                        "map_points": [
                            {"x": 0.1, "y": 0.2},
                            {"x": 0.3, "y": 0.4},
                            {"x": 5.0, "y": 5.0},
                        ],
                        "occupied": [20, 20, 22, 22],
                    }
                ),
                encoding="utf-8",
            )
            layer = load_slam_map(path)
            self.assertIsNotNone(layer)
            assert layer is not None
            self.assertEqual(len(layer.occupied_xy), 3)
            cropped = layer.crop(0.0, 0.0, 0.5, 0.5, pad=0.1)
            self.assertEqual(len(cropped.occupied_xy), 2)

    def test_prefer_live_over_grid(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            live = Path(tmp) / "slam_live.json"
            grid = Path(tmp) / "slam_map.json"
            live.write_text(
                json.dumps(
                    {
                        "resolution": 0.05,
                        "map_points": [{"x": 1.0, "y": 1.0}],
                    }
                ),
                encoding="utf-8",
            )
            grid.write_text(
                json.dumps(
                    {
                        "resolution": 0.08,
                        "width": 2,
                        "height": 2,
                        "origin_x": 0,
                        "origin_y": 0,
                        "occupied": [0, 0, 1, 1],
                    }
                ),
                encoding="utf-8",
            )
            best = load_best_map(live, grid)
            self.assertIsNotNone(best)
            assert best is not None
            self.assertEqual(best.source, "slam_live.json")

    def test_canonical_nav_id_strips_settle_suffixes(self) -> None:
        self.assertEqual(
            canonical_nav_id("nav-20260826-193315-bf20c6-fine-done"),
            "nav-20260826-193315-bf20c6",
        )
        self.assertEqual(
            canonical_nav_id("nav-x-motion"),
            "nav-x",
        )
        self.assertEqual(canonical_nav_id("nav-x"), "nav-x")

    def test_list_runs_merges_suffix_events(self) -> None:
        events = [
            {
                "ts": 1.0,
                "iso": "2026-01-01T00:00:00Z",
                "event": "goto",
                "nav_id": "nav-test-1",
                "label": "mark",
                "target": {"x": 1.0, "y": 0.0, "yaw": 0.0},
                "start_pose": {"x": 0.0, "y": 0.0, "yaw": 0.0},
            },
            {
                "ts": 2.0,
                "iso": "2026-01-01T00:00:01Z",
                "event": "settled",
                "nav_id": "nav-test-1-motion",
                "pose": {"x": 0.1, "y": 0.0, "yaw": 0.0},
            },
            {
                "ts": 3.0,
                "iso": "2026-01-01T00:00:02Z",
                "event": "progress",
                "nav_id": "nav-test-1",
                "pose": {"x": 0.2, "y": 0.0, "yaw": 0.0},
                "goal": {"x": 1.0, "y": 0.0, "yaw": 0.0},
            },
            {
                "ts": 4.0,
                "iso": "2026-01-01T00:00:03Z",
                "event": "note",
                "nav_id": "nav-test-1-fine-done",
            },
        ]
        runs = list_runs(events)
        self.assertEqual(len(runs), 1)
        self.assertEqual(runs[0].nav_id, "nav-test-1")
        self.assertEqual(runs[0].event_count, 4)
        self.assertEqual(latest_nav_id(events), "nav-test-1")
        frames = build_frames(events, "nav-test-1-fine-done")
        self.assertGreaterEqual(len(frames), 3)


if __name__ == "__main__":
    unittest.main()
