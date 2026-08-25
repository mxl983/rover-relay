"""Load the latest frozen SLAM occupancy grid into a sim Scenario."""

from __future__ import annotations

import json
import math
import os
import subprocess
from pathlib import Path
from typing import Any

from .engine import Obstacle, Scenario

REPO_ROOT = Path(__file__).resolve().parents[1]
FIXTURE_PATH = Path(__file__).resolve().parent / "fixtures" / "persistent_grid.json"
WAYPOINTS_FIXTURE = Path(__file__).resolve().parent / "fixtures" / "waypoints.json"
FREEZE_POSE_FIXTURE = Path(__file__).resolve().parent / "fixtures" / "freeze_pose.json"

OCCUPIED_THRESHOLD = 65
DOCKER_GRID = "/app/lidar/maps/persistent_grid.json"
DOCKER_WAYPOINTS = "/app/lidar/maps/waypoints.json"
DOCKER_FREEZE = "/app/lidar/maps/freeze_pose.json"


def _candidate_paths() -> list[Path]:
    env = os.environ.get("SIM_SLAM_GRID", "").strip()
    paths: list[Path] = []
    if env:
        paths.append(Path(env))
    paths.extend(
        [
            FIXTURE_PATH,
            Path("/app/lidar/maps/persistent_grid.json"),
            REPO_ROOT / "data" / "lidar" / "maps" / "persistent_grid.json",
        ]
    )
    return paths


def _docker_compose_cat(container_path: str) -> bytes | None:
    compose = REPO_ROOT / "docker-compose.yml"
    if not compose.is_file():
        return None
    for service in ("ros2-slam", "ros2-nav", "relay"):
        try:
            proc = subprocess.run(
                [
                    "docker",
                    "compose",
                    "-f",
                    str(compose),
                    "exec",
                    "-T",
                    service,
                    "cat",
                    container_path,
                ],
                capture_output=True,
                check=False,
                timeout=20,
            )
        except (OSError, subprocess.TimeoutExpired):
            continue
        if proc.returncode == 0 and proc.stdout:
            return proc.stdout
    return None


def refresh_fixtures_from_docker() -> bool:
    """Pull latest persistent grid (+ pose/waypoints) into sim/fixtures/."""
    grid = _docker_compose_cat(DOCKER_GRID)
    if not grid:
        return False
    FIXTURE_PATH.parent.mkdir(parents=True, exist_ok=True)
    FIXTURE_PATH.write_bytes(grid)
    for remote, local in (
        (DOCKER_WAYPOINTS, WAYPOINTS_FIXTURE),
        (DOCKER_FREEZE, FREEZE_POSE_FIXTURE),
    ):
        blob = _docker_compose_cat(remote)
        if blob:
            local.write_bytes(blob)
    return True


def resolve_grid_path(*, refresh: bool = True) -> Path | None:
    for path in _candidate_paths():
        if path.is_file():
            return path
    if refresh and refresh_fixtures_from_docker() and FIXTURE_PATH.is_file():
        return FIXTURE_PATH
    return None


def _merge_occupied_rects(
    data: list[int],
    width: int,
    height: int,
    resolution: float,
    *,
    threshold: int = OCCUPIED_THRESHOLD,
) -> list[Obstacle]:
    """Compress occupied cells into axis-aligned wall rects (greedy row runs)."""
    visited = [False] * (width * height)
    obstacles: list[Obstacle] = []
    oid = 0
    for y in range(height):
        x = 0
        while x < width:
            idx = y * width + x
            if visited[idx] or int(data[idx]) < threshold:
                x += 1
                continue
            x1 = x
            while (
                x1 < width
                and not visited[y * width + x1]
                and int(data[y * width + x1]) >= threshold
            ):
                x1 += 1
            # Grow downward while the run stays solid.
            y1 = y + 1
            while y1 < height:
                solid = True
                for xx in range(x, x1):
                    i2 = y1 * width + xx
                    if visited[i2] or int(data[i2]) < threshold:
                        solid = False
                        break
                if not solid:
                    break
                y1 += 1
            for yy in range(y, y1):
                for xx in range(x, x1):
                    visited[yy * width + xx] = True
            oid += 1
            obstacles.append(
                Obstacle(
                    id=f"slam-wall-{oid}",
                    x=x * resolution,
                    y=y * resolution,
                    width=max(resolution, (x1 - x) * resolution),
                    height=max(resolution, (y1 - y) * resolution),
                    kind="wall",
                    label="slam",
                )
            )
            x = x1
    return obstacles


def _load_json(path: Path) -> Any:
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def build_saved_slam_scenario(
    grid_path: Path | None = None,
    *,
    refresh: bool = True,
) -> Scenario | None:
    """Build a Scenario from persistent_grid.json (latest frozen SLAM map)."""
    path = grid_path or resolve_grid_path(refresh=refresh)
    if path is None:
        return None
    raw = _load_json(path)
    resolution = float(raw.get("resolution") or 0.05)
    width = int(raw["width"])
    height = int(raw["height"])
    origin = raw.get("origin") or {}
    origin_x = float(origin.get("x", 0.0))
    origin_y = float(origin.get("y", 0.0))
    data = raw.get("data") or []
    if len(data) != width * height:
        return None

    # Shift so scenario local origin is (0,0) at the grid origin.
    world_w = width * resolution
    world_h = height * resolution
    obstacles = _merge_occupied_rects(data, width, height, resolution)
    # Soft border so the rover cannot drive off-map into void.
    border = 0.12
    obstacles.extend(
        [
            Obstacle("map-south", 0.0, -border, world_w, border, kind="wall"),
            Obstacle("map-north", 0.0, world_h, world_w, border, kind="wall"),
            Obstacle("map-west", -border, 0.0, border, world_h, kind="wall"),
            Obstacle("map-east", world_w, 0.0, border, world_h, kind="wall"),
        ]
    )

    start = {"x": world_w * 0.5, "y": world_h * 0.5, "yaw": 0.0}
    freeze_path = FREEZE_POSE_FIXTURE if FREEZE_POSE_FIXTURE.is_file() else None
    if freeze_path is None and refresh:
        blob = _docker_compose_cat(DOCKER_FREEZE)
        if blob:
            FREEZE_POSE_FIXTURE.parent.mkdir(parents=True, exist_ok=True)
            FREEZE_POSE_FIXTURE.write_bytes(blob)
            freeze_path = FREEZE_POSE_FIXTURE
    if freeze_path and freeze_path.is_file():
        pose = _load_json(freeze_path)
        start = {
            "x": float(pose["x"]) - origin_x,
            "y": float(pose["y"]) - origin_y,
            "yaw": float(pose.get("yaw") or 0.0),
        }

    goal = {
        "x": min(world_w - 0.5, start["x"] + 1.5),
        "y": min(world_h - 0.5, start["y"] + 1.5),
    }
    wp_path = WAYPOINTS_FIXTURE if WAYPOINTS_FIXTURE.is_file() else None
    if wp_path is None and refresh:
        blob = _docker_compose_cat(DOCKER_WAYPOINTS)
        if blob:
            WAYPOINTS_FIXTURE.parent.mkdir(parents=True, exist_ok=True)
            WAYPOINTS_FIXTURE.write_bytes(blob)
            wp_path = WAYPOINTS_FIXTURE
    if wp_path and wp_path.is_file():
        wps = (_load_json(wp_path).get("waypoints") or [])
        if wps:
            wp = wps[0]
            goal = {
                "x": float(wp["x"]) - origin_x,
                "y": float(wp["y"]) - origin_y,
            }

    # Keep start inside free space if freeze pose landed in a wall.
    sx = max(0.4, min(world_w - 0.4, float(start["x"])))
    sy = max(0.4, min(world_h - 0.4, float(start["y"])))
    start = {"x": sx, "y": sy, "yaw": float(start["yaw"])}

    return Scenario(
        id="saved_slam",
        label="Saved SLAM map",
        description=(
            f"Latest persistent grid ({width}×{height} @ {resolution:.3f} m) "
            f"from {path.name}. Tank WASD drive + nav-init pulses."
        ),
        width=world_w,
        height=world_h,
        start=start,
        default_goal=goal,
        obstacles=obstacles,
    )


def register_saved_slam_scenario(
    scenarios: dict[str, Scenario],
    *,
    refresh: bool = True,
) -> str | None:
    """Insert/replace saved_slam in SCENARIOS. Returns id if loaded."""
    scenario = build_saved_slam_scenario(refresh=refresh)
    if scenario is None:
        return None
    scenarios[scenario.id] = scenario
    return scenario.id
