"""Unit tests for the internal SLAM/Nav simulation engine."""

from __future__ import annotations

import math
import unittest

from sim.engine import (
    LIDAR_ANGULAR_RES_DEG,
    LIDAR_DISPLAY_ARC_DEG,
    LIDAR_MIN_RANGE_M,
    LIDAR_MODEL,
    LIDAR_RANGE_M,
    LIDAR_RAY_COUNT,
    LIDAR_SCAN_HZ,
    Obstacle,
    SCENARIOS,
    SlamNavSimulation,
    cast_ray,
    distance,
    is_lidar_blind_bearing,
    run_regressions,
    wrap_angle,
)


class SimulationEngineTests(unittest.TestCase):
    def test_ld19_beam_geometry(self) -> None:
        self.assertEqual(LIDAR_MODEL, "LD19/D500")
        self.assertEqual(LIDAR_RAY_COUNT, 450)
        self.assertAlmostEqual(LIDAR_ANGULAR_RES_DEG, 0.8)
        self.assertAlmostEqual(LIDAR_RANGE_M, 13.4)
        self.assertAlmostEqual(LIDAR_MIN_RANGE_M, 0.1)
        self.assertAlmostEqual(LIDAR_SCAN_HZ, 10.0)
        sim = SlamNavSimulation("open_lab", noise_enabled=False)
        self.assertEqual(len(sim.lidar), 450)
        self.assertAlmostEqual(
            sim.lidar[1]["relative_angle"] - sim.lidar[0]["relative_angle"],
            math.radians(0.8),
            places=6,
        )

    def test_rear_blind_sector_matches_rover(self) -> None:
        self.assertAlmostEqual(LIDAR_DISPLAY_ARC_DEG, 270.0)
        self.assertTrue(is_lidar_blind_bearing(math.pi))  # straight rear
        self.assertFalse(is_lidar_blind_bearing(0.0))  # forward
        self.assertFalse(is_lidar_blind_bearing(math.pi / 2))  # left
        sim = SlamNavSimulation("open_lab", noise_enabled=False)
        blind = [ray for ray in sim.lidar if ray.get("blind")]
        expected = int(round((360.0 - LIDAR_DISPLAY_ARC_DEG) / LIDAR_ANGULAR_RES_DEG))
        self.assertAlmostEqual(len(blind), expected, delta=1)
        for ray in blind:
            self.assertFalse(ray["hit"])
            self.assertTrue(ray["invalid"])

    def test_min_range_rejects_near_hits(self) -> None:
        hit = cast_ray(
            {"x": 1.0, "y": 1.0},
            0.0,
            [Obstacle(id="close", x=1.05, y=0.5, width=0.2, height=1.0)],
            LIDAR_RANGE_M,
            LIDAR_MIN_RANGE_M,
        )
        self.assertFalse(hit["hit"])
        self.assertTrue(hit["invalid"])

    def test_noise_changes_ranges_with_seed(self) -> None:
        clean = SlamNavSimulation("open_lab", noise_enabled=False)
        noisy_a = SlamNavSimulation(
            "open_lab", noise_enabled=True, noise_seed=7
        )
        noisy_b = SlamNavSimulation(
            "open_lab", noise_enabled=True, noise_seed=7
        )
        noisy_c = SlamNavSimulation(
            "open_lab", noise_enabled=True, noise_seed=99
        )
        clean_hits = [ray["distance"] for ray in clean.lidar if ray["hit"]]
        noisy_hits = [ray["distance"] for ray in noisy_a.lidar if ray["hit"]]
        self.assertGreater(len(clean_hits), 10)
        self.assertNotEqual(clean_hits, noisy_hits)
        self.assertEqual(
            [ray["distance"] for ray in noisy_a.lidar],
            [ray["distance"] for ray in noisy_b.lidar],
        )
        self.assertNotEqual(
            [ray["distance"] for ray in noisy_a.lidar],
            [ray["distance"] for ray in noisy_c.lidar],
        )

    def test_raycast_is_deterministic(self) -> None:
        hit = cast_ray(
            {"x": 1.0, "y": 1.0},
            0.0,
            [Obstacle(id="wall", x=3.0, y=0.0, width=0.2, height=3.0)],
            LIDAR_RANGE_M,
        )
        self.assertTrue(hit["hit"])
        self.assertEqual(hit["obstacle_id"], "wall")
        self.assertAlmostEqual(hit["distance"], 2.0, places=6)

    def test_synthetic_lidar_builds_map(self) -> None:
        sim = SlamNavSimulation("open_lab", noise_enabled=False)
        for _ in range(80):
            sim.set_manual(0.0, 1.2)
            sim.step(1 / 30)
        metrics = sim.metrics()
        self.assertGreater(metrics["occupied_cells"], 25)
        self.assertGreater(metrics["known_percent"], 5.0)

    def test_frozen_map_is_immutable(self) -> None:
        sim = SlamNavSimulation("wide_vs_narrow", noise_enabled=False)
        sim.reveal_map()
        sim.freeze_map()
        before = list(sim.slam_grid)
        sim.set_manual(0.0, 1.0)
        for _ in range(60):
            sim.step(1 / 30)
        self.assertEqual(before, sim.slam_grid)

    def test_planner_prefers_wide_opening(self) -> None:
        sim = SlamNavSimulation("wide_vs_narrow", noise_enabled=False)
        sim.reveal_map()
        sim.freeze_map()
        self.assertTrue(sim.set_default_goal())
        crossings = [point for point in sim.path if abs(point["x"] - 6.85) < 0.13]
        self.assertTrue(any(7.0 < point["y"] < 9.0 for point in crossings))
        self.assertFalse(any(4.7 < point["y"] < 5.3 for point in crossings))

    def test_dynamic_obstacle_replans(self) -> None:
        sim = SlamNavSimulation("wide_vs_narrow", noise_enabled=False)
        sim.reveal_map()
        sim.freeze_map()
        sim.set_dynamic_obstacle(True)
        sim.set_default_goal()
        original = [(point["x"], point["y"]) for point in sim.path]
        mid = sim.path[len(sim.path) // 2]
        # Offset so the preferred opening is blocked but an alternate lane remains.
        self.assertTrue(sim.move_prop("crate", mid["x"], mid["y"] - 0.55))
        self.assertGreaterEqual(sim.replans, 1)
        self.assertGreater(len(sim.path), 0)
        self.assertNotEqual(original, [(point["x"], point["y"]) for point in sim.path])

    def test_each_scene_has_varied_movable_props(self) -> None:
        for scenario in SCENARIOS.values():
            if scenario.id == "saved_slam":
                continue  # raster fixture — not a hand-authored prop set
            props = [item for item in scenario.obstacles if item.kind == "dynamic"]
            self.assertGreaterEqual(len(props), 5, scenario.id)
            sizes = {(round(p.width, 2), round(p.height, 2)) for p in props}
            self.assertGreaterEqual(len(sizes), 4, scenario.id)

    def test_scenarios_have_valid_starts_and_goals(self) -> None:
        for scenario in SCENARIOS.values():
            self.assertGreater(scenario.start["x"], 0)
            self.assertLess(scenario.start["x"], scenario.width)
            self.assertGreater(scenario.default_goal["y"], 0)
            self.assertLess(scenario.default_goal["y"], scenario.height)

    def test_estimate_extrapolates_between_scans(self) -> None:
        """Cmd motion advances the estimate between lidar updates (not SLAM odom)."""
        sim = SlamNavSimulation("open_lab", noise_enabled=False)
        sim.reveal_map()
        sim.freeze_map()
        before = dict(sim.estimated_pose)
        sim.localize_from_lidar = lambda rays=None: None  # type: ignore[method-assign]
        sim.set_manual(0.55, 0.0)
        for _ in range(45):
            sim.step(1 / 30)
        self.assertGreater(sim.distance_m, 0.4)
        # Without lidar correction, extrapolator still tracks the driven motion.
        self.assertGreater(sim.estimated_pose["x"] - before["x"], 0.35)
        err = math.hypot(
            sim.pose["x"] - sim.estimated_pose["x"],
            sim.pose["y"] - sim.estimated_pose["y"],
        )
        self.assertLess(err, 0.08)

    def test_noise_free_mapping_keeps_estimate_glued(self) -> None:
        sim = SlamNavSimulation("apartment_loop", noise_enabled=False)
        sim.mode = "mapping"
        sim.set_manual(0.45, 0.25)
        for _ in range(90):
            sim.step(1 / 30)
            self.assertAlmostEqual(sim.pose["x"], sim.estimated_pose["x"], places=5)
            self.assertAlmostEqual(sim.pose["y"], sim.estimated_pose["y"], places=5)
            self.assertAlmostEqual(sim.pose["yaw"], sim.estimated_pose["yaw"], places=5)

    def test_pi_stick_drive_moves_without_autopilot(self) -> None:
        """Co-sim path: bridges POST stick; plant must not run internal RPP."""
        sim = SlamNavSimulation("apartment_loop", noise_enabled=False)
        sim.reveal_map()
        sim.freeze_map()
        before = dict(sim.pose)
        sim.set_pi_stick(0.0, -0.55, enabled=True)
        self.assertFalse(sim.autopilot)
        for _ in range(45):
            sim.step(1 / 30)
        moved = math.hypot(sim.pose["x"] - before["x"], sim.pose["y"] - before["y"])
        self.assertGreater(moved, 0.25)
        self.assertFalse(sim.autopilot)

    def test_goal_auto_freezes_and_navigates(self) -> None:
        sim = SlamNavSimulation("wide_vs_narrow", noise_enabled=False)
        sim.reveal_map()
        sim.speed_multiplier = 2.5
        self.assertEqual(sim.mode, "mapping")
        self.assertTrue(sim.set_default_goal())
        self.assertEqual(sim.mode, "localization")
        self.assertTrue(sim.autopilot)
        self.assertTrue(sim.goal_reachable)
        self.assertIn("navigating", sim.status)
        for _ in range(1800):
            sim.step(1 / 30)
            if sim.nav_complete or "arrived" in sim.status:
                break
        self.assertTrue(sim.nav_complete)
        self.assertIn("arrived", sim.status)
        self.assertLess(
            math.hypot(
                sim.pose["x"] - sim.goal["x"], sim.pose["y"] - sim.goal["y"]
            ),
            0.6,
        )

    def test_kidnap_relocalizes_from_lidar(self) -> None:
        sim = SlamNavSimulation("open_lab", noise_enabled=False)
        sim.reveal_map()
        sim.freeze_map()
        self.assertTrue(sim.kidnap_rover(6.0, 4.0))
        self.assertGreater(distance(sim.pose, sim.estimated_pose), 1.5)
        self.assertTrue(sim._relocalizing)
        sim.scan()  # global + local recovery
        self.assertFalse(sim._relocalizing)
        self.assertLess(distance(sim.pose, sim.estimated_pose), 0.25)

    def test_gui_regression_suite_passes(self) -> None:
        suite = run_regressions()
        self.assertEqual(len(suite["results"]), 8)
        self.assertTrue(suite["pass"], suite)

    def test_free_ray_clears_unsupported_occupied(self) -> None:
        """Later free rays must be able to erase false / shifted wall cells."""
        from sim.engine import FREE, GRID_RESOLUTION_M, LOG_ODDS_CLAMP, OCCUPIED, UNKNOWN

        sim = SlamNavSimulation("open_lab", noise_enabled=False)
        sim.mode = "mapping"
        x = int(5.4 / GRID_RESOLUTION_M)
        y = int(3.0 / GRID_RESOLUTION_M)
        idx = y * sim.width_cells + x
        # Unsupported speck — a couple of free rays should clear it.
        sim.slam_grid[idx] = OCCUPIED
        sim.evidence[idx] = 0.5
        pose = {"x": 2.0, "y": 3.0, "yaw": 0.0}
        sim.pose = dict(pose)
        sim.estimated_pose = dict(pose)
        miss = {
            "relative_angle": 0.0,
            "distance": 8.0,
            "hit": False,
            "invalid": False,
            "blind": False,
        }
        for _ in range(3):
            sim.integrate_scan([miss])
        self.assertNotEqual(sim.slam_grid[idx], OCCUPIED)
        self.assertIn(sim.slam_grid[idx], (FREE, UNKNOWN))
        self.assertEqual(sim.slam_grid[y * sim.width_cells + int(3.0 / GRID_RESOLUTION_M)], FREE)

        # Strongly confirmed wall survives a single grazing free vote.
        sim.slam_grid[idx] = OCCUPIED
        sim.evidence[idx] = LOG_ODDS_CLAMP
        sim.integrate_scan([miss])
        self.assertEqual(sim.slam_grid[idx], OCCUPIED)

    def test_auto_map_completes_apartment(self) -> None:
        from sim.harness import run_auto_map

        sim = SlamNavSimulation("apartment_loop", noise_enabled=False)
        # Tank+lidar scan matching is heavier than the old unicycle path; keep a
        # tighter budget so CI/dev loops stay interactive.
        result = run_auto_map(sim, max_steps=6000, speed=4.0)
        self.assertTrue(result["completed"], result)
        self.assertGreaterEqual(result["free_recall"], 0.80)
        self.assertTrue(
            result["frontiers"] <= 360 or result["free_recall"] >= 0.88,
            result,
        )
        self.assertEqual(sim.mode, "localization")

    def test_wall_thickness_stable_under_pose_jitter(self) -> None:
        """Shifted revisits must not thicken walls indefinitely."""
        from sim.engine import GRID_RESOLUTION_M, LETHAL

        sim = SlamNavSimulation("apartment_loop", noise_enabled=True, noise_seed=3)
        sim.mode = "mapping"
        means: list[float] = []
        for step in range(120):
            sim.pose = {"x": 2 + 0.01 * step, "y": 1.2, "yaw": -math.pi / 2}
            sim.estimated_pose = {
                "x": sim.pose["x"] + 0.05 * math.sin(step * 0.3),
                "y": sim.pose["y"] + 0.05 * math.cos(step * 0.25),
                "yaw": sim.pose["yaw"] + 0.05 * math.sin(step * 0.2),
            }
            sim.scan(localize=False, integrate=True)
            if step % 30 == 29:
                thicknesses = []
                for ix in range(15, 50):
                    cells = [
                        iy
                        for iy in range(0, 14)
                        if sim.slam_grid[iy * sim.width_cells + ix] >= LETHAL
                    ]
                    if cells:
                        thicknesses.append(
                            (max(cells) - min(cells) + 1) * GRID_RESOLUTION_M
                        )
                means.append(sum(thicknesses) / len(thicknesses) if thicknesses else 0.0)
        self.assertGreaterEqual(len(means), 3)
        # Correction may thin walls; they must not keep growing past the early band.
        self.assertLess(max(means), 0.40, means)
        self.assertLessEqual(means[-1], means[0] + 0.08, means)
        late = sum(means[-2:]) / 2.0
        early = sum(means[:2]) / 2.0
        self.assertLessEqual(late, early + 0.07, means)


    def test_reveal_map_not_corrupted_by_scan_integrate(self) -> None:
        """Build Map must keep GT walls — integrating after reveal used to walk them."""
        from sim.engine import LETHAL, UNKNOWN

        sim = SlamNavSimulation("apartment_loop", noise_enabled=False)
        sim.reveal_map()
        before = list(sim.slam_grid)
        occ_before = sum(1 for v in before if v >= LETHAL)
        unk_before = sum(1 for v in before if v == UNKNOWN)
        # A display scan must not rewrite the revealed grid.
        sim.scan(localize=False, integrate=False)
        self.assertEqual(sim.slam_grid, before)
        sim.freeze_map()
        self.assertEqual(sim.mode, "localization")
        self.assertTrue(getattr(sim, "_map_from_reveal", False))
        # Freeze must not thin a reveal/GT raster (that biased pose matching).
        occ_after = sum(1 for v in sim.slam_grid if v >= LETHAL)
        self.assertEqual(occ_after, occ_before)
        self.assertEqual(unk_before, 0)
        self.assertGreater(occ_before, 1000)

    def test_frozen_map_pose_stays_bounded(self) -> None:
        """After Build Map + freeze, localization error must stay small on a tour."""
        from sim.harness import apartment_tour_commands, drive, summarize_errors

        sim = SlamNavSimulation("apartment_loop", noise_enabled=False)
        sim.reveal_map()
        sim.freeze_map()
        summary = summarize_errors(drive(sim, apartment_tour_commands()))
        # Calibrated stick dynamics are slower/softer than the old abstract tank
        # model; allow a bit more correlative-match lag on a long tour.
        self.assertLess(summary["mean_xy_error_m"], 0.30, summary)
        self.assertLess(summary["max_xy_error_m"], 0.55, summary)
        self.assertLess(summary["final_xy_error_m"], 0.50, summary)

    def test_confirmed_walls_resist_free_ray_walk(self) -> None:
        """Free rays may clear weak ghosts but must not walk a confirmed wall away."""
        from sim.engine import GRID_RESOLUTION_M, LOG_ODDS_CLAMP, LETHAL, OCCUPIED

        sim = SlamNavSimulation("open_lab", noise_enabled=False)
        sim.mode = "mapping"
        x = int(4.0 / GRID_RESOLUTION_M)
        y = int(2.0 / GRID_RESOLUTION_M)
        idx = y * sim.width_cells + x
        sim.slam_grid[idx] = OCCUPIED
        sim.evidence[idx] = LOG_ODDS_CLAMP
        sim.pose = {"x": 1.0, "y": 2.0, "yaw": 0.0}
        sim.estimated_pose = dict(sim.pose)
        miss = {
            "relative_angle": 0.0,
            "distance": 8.0,
            "hit": False,
            "invalid": False,
            "blind": False,
        }
        for _ in range(4):
            sim.integrate_scan([miss])
        self.assertGreaterEqual(sim.slam_grid[idx], LETHAL)
        self.assertGreaterEqual(sim.evidence[idx], 0.5)

    def test_goal_always_has_yaw_pose(self) -> None:
        sim = SlamNavSimulation("open_lab", noise_enabled=False)
        sim.reveal_map()
        self.assertTrue(sim.set_goal(5.0, 4.0))
        self.assertIn("yaw", sim.goal)
        self.assertIsNotNone(sim.goal_yaw)
        # Explicit yaw is kept.
        self.assertTrue(sim.set_goal(6.0, 4.0, yaw=1.2))
        self.assertAlmostEqual(sim.goal["yaw"], 1.2, places=5)

    def test_click_goal_enables_autopilot_and_moves(self) -> None:
        """Reachable goals must arm autopilot and translate the rover."""
        sim = SlamNavSimulation("apartment_loop", noise_enabled=False)
        sim.reveal_map()
        start = dict(sim.pose)
        self.assertTrue(sim.set_goal(8.0, 6.0))
        self.assertEqual(sim.mode, "localization")
        self.assertTrue(sim.goal_reachable)
        self.assertTrue(sim.autopilot, sim.status)
        self.assertGreater(len(sim.path), 1)
        for _ in range(150):
            sim.set_manual(0.0, 0.0)  # GUI sends zeros every tick
            sim.step(1 / 30)
        moved = distance(start, sim.pose)
        self.assertGreater(moved, 0.8, f"moved only {moved:.3f}m status={sim.status}")
        self.assertTrue(sim.autopilot or sim.nav_complete)

    def test_autopilot_uses_rpp_twist(self) -> None:
        """Large yaw → rotate-to-heading; aligned → desired_linear_vel (Nav2 RPP)."""
        sim = SlamNavSimulation("open_lab", noise_enabled=False)
        sim.reveal_map()
        sim.freeze_map()
        sim.goal = {"x": sim.pose["x"] + 2.0, "y": sim.pose["y"], "yaw": 0.0}
        sim.goal_yaw = 0.0
        sim.path = [
            dict(sim.pose),
            {"x": sim.pose["x"] + 1.0, "y": sim.pose["y"]},
            dict(sim.goal),
        ]
        sim.path_cursor = 1
        sim.autopilot = True
        # Large heading error: rotate in place (nav2 rotate_to_heading_angular_vel).
        sim.estimated_pose = {
            "x": sim.pose["x"],
            "y": sim.pose["y"],
            "yaw": sim.pose["yaw"] + 0.7,
        }
        turn = sim.autopilot_command()
        self.assertEqual(turn["linear"], 0.0, turn)
        self.assertAlmostEqual(abs(turn["angular"]), 0.80, places=2)
        self.assertTrue(sim._rotate_to_heading)

        # Aligned: cruise at desired_linear_vel.
        sim._rotate_to_heading = False
        sim.estimated_pose = dict(sim.pose)
        drive = sim.autopilot_command()
        self.assertAlmostEqual(drive["linear"], 0.30, places=2)
        self.assertAlmostEqual(drive["angular"], 0.0, places=2)

        # Mild heading error: forward + soft yaw trim (under rotate-to-heading gate).
        sim.estimated_pose = {
            "x": sim.pose["x"],
            "y": sim.pose["y"],
            "yaw": sim.pose["yaw"] + 0.12,
        }
        arc = sim.autopilot_command()
        self.assertGreater(arc["linear"], 0.15)
        self.assertGreater(abs(arc["angular"]), 0.05)
        self.assertLess(abs(arc["angular"]), 0.50)

    def test_zero_manual_does_not_cancel_autopilot(self) -> None:
        """GUI ticks send linear=0,angular=0; that must not disarm nav."""
        sim = SlamNavSimulation("open_lab", noise_enabled=False)
        sim.reveal_map()
        sim.freeze_map()
        self.assertTrue(sim.set_goal(sim.pose["x"] + 2.5, sim.pose["y"]))
        self.assertTrue(sim.autopilot)
        for _ in range(10):
            sim.set_manual(0.0, 0.0)
            self.assertTrue(sim.autopilot, sim.status)
            sim.step(1 / 30)
        self.assertTrue(sim.autopilot or sim.nav_complete)

    def test_continuous_autopilot_reaches_nearby_goal(self) -> None:
        """End-to-end: continuous Twist + stick dynamics must arrive."""
        sim = SlamNavSimulation("open_lab", noise_enabled=False)
        sim.reveal_map()
        sim.freeze_map()
        goal_x = sim.pose["x"] + 2.0
        goal_y = sim.pose["y"]
        self.assertTrue(sim.set_goal(goal_x, goal_y))
        for _ in range(900):
            sim.set_manual(0.0, 0.0)
            sim.step(1 / 30)
            if sim.nav_complete or not sim.autopilot:
                break
        self.assertTrue(
            sim.nav_complete or distance(sim.pose, {"x": goal_x, "y": goal_y}) < 0.4,
            f"status={sim.status} pose={sim.pose} dist={distance(sim.pose, {'x': goal_x, 'y': goal_y}):.3f}",
        )

    def test_server_autostep_moves_when_client_stale(self) -> None:
        """Stale browser tick → server autostep must still drive a reachable goal."""
        from sim.gui import SimulatorServer

        server = SimulatorServer(host="127.0.0.1", port=0)
        server.sim.reset("open_lab")
        server.sim.reveal_map()
        server.sim.freeze_map()
        self.assertTrue(
            server.sim.set_goal(server.sim.pose["x"] + 2.0, server.sim.pose["y"])
        )
        self.assertTrue(server.sim.autopilot)
        start = dict(server.sim.pose)
        # Pretend the client has been silent for a long time.
        server._last_client_tick = 0.0
        stepped = 0
        for i in range(45):
            if server.maybe_autostep(now=10.0 + i * 0.05):
                stepped += 1
        self.assertGreater(stepped, 20)
        moved = distance(start, server.sim.pose)
        self.assertGreater(moved, 0.25, f"moved only {moved:.3f}m status={server.sim.status}")

    def test_server_autostep_skips_when_client_fresh(self) -> None:
        """Healthy browser ticks own the clock — autostep must not double-drive."""
        import time as time_mod

        from sim.gui import SimulatorServer

        server = SimulatorServer(host="127.0.0.1", port=0)
        server.sim.reset("open_lab")
        server.sim.reveal_map()
        server.sim.freeze_map()
        self.assertTrue(
            server.sim.set_goal(server.sim.pose["x"] + 2.0, server.sim.pose["y"])
        )
        server._last_client_tick = time_mod.monotonic()
        start = dict(server.sim.pose)
        self.assertFalse(server.maybe_autostep())
        self.assertEqual(server.sim.pose["x"], start["x"])
        self.assertEqual(server.sim.pose["y"], start["y"])

    def test_noise_free_mapping_walls_do_not_migrate(self) -> None:
        """With noise off, painted wall centroids must stay put while driving."""
        from sim.engine import GRID_RESOLUTION_M, LETHAL

        sim = SlamNavSimulation("apartment_loop", noise_enabled=False)
        sim.mode = "mapping"
        # Look at a nearby wall, then drive parallel to it.
        sim.pose = {"x": 2.0, "y": 1.5, "yaw": -math.pi / 2}
        sim.estimated_pose = dict(sim.pose)
        for _ in range(25):
            sim.scan(localize=False, integrate=True)
            sim.pose["yaw"] = wrap_angle(sim.pose["yaw"] + 0.2)
            sim.estimated_pose = dict(sim.pose)

        def wall_xs() -> list[float]:
            xs = []
            for ix in range(int(1.5 / GRID_RESOLUTION_M), int(6.0 / GRID_RESOLUTION_M)):
                col = [
                    iy
                    for iy in range(0, int(2.5 / GRID_RESOLUTION_M))
                    if sim.slam_grid[iy * sim.width_cells + ix] >= LETHAL
                ]
                if col:
                    xs.append(ix * GRID_RESOLUTION_M)
            return xs

        before = wall_xs()
        self.assertGreater(len(before), 3)
        mean0 = sum(before) / len(before)
        for step in range(80):
            sim.set_manual(0.35, 0.15 * math.sin(step * 0.08))
            sim.step(1 / 30)
        after = wall_xs()
        self.assertGreater(len(after), 3)
        mean1 = sum(after) / len(after)
        self.assertLess(abs(mean1 - mean0), 0.15, (mean0, mean1))


class TankDriveTests(unittest.TestCase):
    def test_a_key_is_left_reverse_right_forward(self) -> None:
        from sim.drive import keys_to_tracks

        left, right = keys_to_tracks(["a"])
        self.assertLess(left, 0.0)
        self.assertGreater(right, 0.0)
        self.assertAlmostEqual(abs(left), abs(right), places=3)

    def test_d_key_is_left_forward_right_reverse(self) -> None:
        from sim.drive import keys_to_tracks

        left, right = keys_to_tracks(["d"])
        self.assertGreater(left, 0.0)
        self.assertLess(right, 0.0)

    def test_pure_a_rotates_counterclockwise(self) -> None:
        from sim.drive import integrate_tank, keys_to_tracks

        pose = {"x": 0.0, "y": 0.0, "yaw": 0.0}
        left, right = keys_to_tracks(["a"])
        nxt = integrate_tank(pose, left, right, 0.2)
        self.assertGreater(nxt["yaw"], 0.2)
        self.assertAlmostEqual(nxt["x"], 0.0, places=2)
        self.assertAlmostEqual(nxt["y"], 0.0, places=2)

    def test_cmd_vel_align_gate_matches_nav(self) -> None:
        from sim.drive import cmd_vel_to_keys

        self.assertEqual(cmd_vel_to_keys(0.55, 0.0), ["w"])
        self.assertEqual(cmd_vel_to_keys(0.55, 0.8), ["a"])  # strong yaw → A only
        self.assertEqual(cmd_vel_to_keys(0.55, 0.2), ["w", "a"])

    def test_cmd_latency_delays_motion(self) -> None:
        """Relay→Pi latency: first frames after a Twist should not move yet."""
        from sim.drive import CMD_LATENCY_SEC

        sim = SlamNavSimulation("open_lab", noise_enabled=False)
        x0 = sim.pose["x"]
        sim.set_manual(0.30, 0.0)
        # Half the command latency — motors should still be idle.
        steps = max(1, int((CMD_LATENCY_SEC * 0.5) * 30))
        for _ in range(steps):
            sim.step(1 / 30)
        self.assertLess(abs(sim.pose["x"] - x0), 0.02)
        for _ in range(45):
            sim.step(1 / 30)
        self.assertGreater(sim.pose["x"] - x0, 0.20)

    def test_nav_twist_uses_drive_interface_stick(self) -> None:
        from sim.drive import nav_twist_to_body
        from drive_interface import PURE_ROTATE_STICK

        vx, wz, stick = nav_twist_to_body(0.0, 0.80)
        self.assertEqual(vx, 0.0)
        self.assertGreaterEqual(abs(stick["x"]), PURE_ROTATE_STICK - 1e-6)
        self.assertEqual(stick["y"], 0.0)
        self.assertGreater(abs(wz), 0.4)

    def test_sim_step_uses_calibrated_stick(self) -> None:
        sim = SlamNavSimulation("open_lab", noise_enabled=False)
        yaw0 = sim.pose["yaw"]
        sim.set_manual(0.0, 0.80)  # pure rotate Twist → decisive stick
        for _ in range(60):
            sim.step(1 / 30)
        self.assertGreater(sim.pose["yaw"] - yaw0, 0.4)
        snap = sim.snapshot()
        self.assertIn("drive", snap)
        self.assertIn("stick", snap["drive"])
        self.assertIn("tracks", snap["drive"])

    def test_saved_slam_scenario_available(self) -> None:
        from sim.engine import ensure_saved_slam_scenario

        sid = ensure_saved_slam_scenario(refresh=False)
        if sid is None:
            self.skipTest("no persistent_grid fixture available")
        self.assertIn("saved_slam", SCENARIOS)
        sim = SlamNavSimulation("saved_slam", noise_enabled=False)
        self.assertEqual(sim.scenario.id, "saved_slam")
        self.assertGreater(len(sim.obstacles), 10)


if __name__ == "__main__":
    unittest.main()
