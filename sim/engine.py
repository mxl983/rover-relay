"""Deterministic 2D SLAM + Nav engine for internal regression testing.

Mirrors the real rover stack: LD19 lidar only (no wheel odometry), freeze-map
localization, and Nav2-style plan/follow once a goal is set.
"""

from __future__ import annotations

import heapq
import math
import random
import sys
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Iterable

_NAV_COMMON = Path(__file__).resolve().parents[1] / "ros2-nav"
if str(_NAV_COMMON) not in sys.path:
    sys.path.insert(0, str(_NAV_COMMON))
from lateral_maneuver import (  # noqa: E402
    GapCloseConfig,
    GapCloseState,
    keys_to_twist,
    next_gap_close_step,
)

from .drive import (
    NavDriveState,
    apply_nav_drive,
    cmd_vel_to_keys,
    integrate_tank,
    keys_to_tracks,
    tracks_to_twist,
)

GRID_RESOLUTION_M = 0.1
ROVER_SIZE_M = 0.35
ROVER_PADDING_M = 0.02
# Circumscribed corner radius — too fat for AABB corridors on the saved map.
ROVER_COLLISION_RADIUS_M = (
    math.hypot(ROVER_SIZE_M / 2, ROVER_SIZE_M / 2) + ROVER_PADDING_M
)
# In-plane half-width for nav probes / path checks (matches planner inflation).
NAV_COLLISION_RADIUS_M = ROVER_SIZE_M / 2 + ROVER_PADDING_M
# LD19 / D500 lidar model (TOF, UART). Specs mirrored for sim fidelity.
LIDAR_MODEL = "LD19/D500"
LIDAR_FOV_DEG = 360.0
LIDAR_ANGULAR_RES_DEG = 0.8
LIDAR_RAY_COUNT = int(round(LIDAR_FOV_DEG / LIDAR_ANGULAR_RES_DEG))  # 450
LIDAR_SCAN_HZ = 10.0
LIDAR_MEASURE_HZ = 4500.0
LIDAR_MIN_RANGE_M = 0.1
LIDAR_RANGE_M = 13.4
LIDAR_ACCURACY_M = 0.020  # ±20 mm (3–12 m band)
LIDAR_SCAN_PERIOD_SEC = 1.0 / LIDAR_SCAN_HZ
# Body occlusion matches production: 270° visible, 90° rear blind.
LIDAR_DISPLAY_ARC_DEG = 270.0
LIDAR_BLIND_CENTER_BODY_DEG = 180.0
LIDAR_NOISE_STD_M = 0.010
LIDAR_NOISE_OUTLIER_PROB = 0.02
LIDAR_NOISE_OUTLIER_M = 0.12
LIDAR_NOISE_DROPOUT_PROB = 0.01
# Cartographer-like scan match search (no wheel odom prior).
# Window sized for ~0.2 m / scan at high GUI speed so thin-wall match stays locked.
SCAN_MATCH_XY_M = 0.24
SCAN_MATCH_YAW_RAD = math.radians(12.0)
SCAN_MATCH_XY_STEP_M = 0.06
SCAN_MATCH_YAW_STEP_RAD = math.radians(4.0)
SCAN_MATCH_HIT_SAMPLES = 40
# Relative scan-to-scan window (motion between 10 Hz scans is small).
SCAN_MATCH_REL_XY_M = 0.14
SCAN_MATCH_REL_YAW_RAD = math.radians(10.0)
SCAN_MATCH_REL_XY_STEP_M = 0.04
SCAN_MATCH_REL_YAW_STEP_RAD = math.radians(3.0)
SCAN_MATCH_FINE_XY_M = 0.06
SCAN_MATCH_FINE_YAW_RAD = math.radians(4.0)
SCAN_MATCH_FINE_XY_STEP_M = 0.02
SCAN_MATCH_FINE_YAW_STEP_RAD = math.radians(1.5)
# Coarse→fine global search after a kidnap / teleport (pose jump).
GLOBAL_RELOC_COARSE_XY_M = 0.45
GLOBAL_RELOC_COARSE_YAW_RAD = math.radians(30.0)
GLOBAL_RELOC_FINE_XY_M = 0.10
GLOBAL_RELOC_FINE_YAW_RAD = math.radians(5.0)
GLOBAL_RELOC_FINE_RADIUS_M = 0.55
GLOBAL_RELOC_TOP_K = 6

UNKNOWN = -1
FREE = 0
OCCUPIED = 100
LETHAL = 65
TAU = math.pi * 2.0
# Map integrity: paint hits without dilation (dilation + pose drift smeared walls).
# Floor UNKNOWN pockets → FREE via fill_map_holes(); never grow walls from hole-fill.
MAP_HIT_DILATE_CELLS = 0
MAP_HOLE_FILL_MAX_CELLS = 28
# Planner inflation ≈ nav half-width. Full circumscribed (~0.27 m) sealed doorways.
PLAN_INFLATION_M = NAV_COLLISION_RADIUS_M * 0.92  # ~0.18 m
# Occupancy evidence (log-odds): later free rays demote shifted walls; soft clamp
# so thickness cannot grow indefinitely under pose jitter.
LOG_ODDS_HIT = 0.90
LOG_ODDS_HIT_SOFT = 0.28  # once confirmed occupied, barely reinforce
LOG_ODDS_MISS = -0.52
LOG_ODDS_OCC_THRESH = 0.85
LOG_ODDS_FREE_THRESH = -0.60
LOG_ODDS_CLAMP = 2.6
# Snap new hits onto a nearby existing wall (±1 cell) to absorb pose shift.
MAP_HIT_SNAP_CELLS = 1
MAP_THIN_EVERY_SCANS = 0  # thin on spin/freeze only — continuous thin starved walls
EXPLORE_GHOST_MISS_SCALE = 0.40  # clear unsnapped ghosts; free-rays still protect walls
# Auto-map: one full spin, then short dwells at new viewpoints (no full re-scan loops).
EXPLORE_SPIN_ANGULAR = 2.0
EXPLORE_SPIN_RAD = TAU * 1.02
EXPLORE_DWELL_RAD = math.radians(85.0)  # cover rear blind without a full 360
EXPLORE_ARRIVE_M = 0.50
# Fine docking: hold until XY and yaw both within tolerance or timeout.
FINE_DOCK_XY_M = 0.08
FINE_DOCK_YAW_RAD = 0.12
FINE_DOCK_TIMEOUT_SEC = 60.0
FINE_DOCK_COARSE_M = 0.40
EXPLORE_MAX_SPINS = 14
EXPLORE_DONE_MAX_FRONTIERS = 10
EXPLORE_MIN_KNOWN_PERCENT = 68.0
EXPLORE_MIN_FREE_RECALL = 0.86
EXPLORE_VISIT_RADIUS_M = 1.55
EXPLORE_BLOCKED_GOAL_RADIUS_M = 1.1
EXPLORE_MAX_BLOCKED = 8
def clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def wrap_angle(angle: float) -> float:
    return math.atan2(math.sin(angle), math.cos(angle))


def distance(a: dict, b: dict) -> float:
    return math.hypot(a["x"] - b["x"], a["y"] - b["y"])


def normalize_deg(deg: float) -> float:
    return deg % 360.0


def is_lidar_blind_bearing(relative_angle_rad: float) -> bool:
    """True for the 90° rear body-occlusion sector (matches real rover)."""
    if LIDAR_DISPLAY_ARC_DEG >= 360.0:
        return False
    half_hidden = (360.0 - LIDAR_DISPLAY_ARC_DEG) / 2.0
    deg = normalize_deg(math.degrees(relative_angle_rad))
    delta = abs(deg - LIDAR_BLIND_CENTER_BODY_DEG)
    if delta > 180.0:
        delta = 360.0 - delta
    return delta < half_hidden


def _gauss(rng: random.Random, mean: float, std: float) -> float:
    # Box–Muller; avoid log(0).
    u1 = max(rng.random(), 1e-12)
    u2 = rng.random()
    z = math.sqrt(-2.0 * math.log(u1)) * math.cos(TAU * u2)
    return mean + std * z


def apply_lidar_noise(
    distance_m: float,
    hit: bool,
    rng: random.Random,
    *,
    enabled: bool = True,
) -> tuple[float, bool, bool]:
    """Return (distance, hit, dropped). Mimics LD19 ±20 mm class noise."""
    if not enabled or not hit:
        return distance_m, hit, False
    if rng.random() < LIDAR_NOISE_DROPOUT_PROB:
        return LIDAR_RANGE_M, False, True
    noisy = distance_m + _gauss(rng, 0.0, LIDAR_NOISE_STD_M)
    if rng.random() < LIDAR_NOISE_OUTLIER_PROB:
        noisy += rng.choice((-1.0, 1.0)) * (
            LIDAR_ACCURACY_M + rng.random() * LIDAR_NOISE_OUTLIER_M
        )
    noisy = clamp(noisy, LIDAR_MIN_RANGE_M, LIDAR_RANGE_M)
    # Quantize to sensor reporting resolution (~accuracy band).
    noisy = round(noisy / LIDAR_ACCURACY_M) * LIDAR_ACCURACY_M
    return noisy, True, False


@dataclass
class Obstacle:
    id: str
    x: float
    y: float
    width: float
    height: float
    kind: str = "wall"
    enabled_by_default: bool = True
    label: str = ""


@dataclass
class Scenario:
    id: str
    label: str
    description: str
    width: float
    height: float
    start: dict
    default_goal: dict
    obstacles: list[Obstacle] = field(default_factory=list)


def _wall(oid: str, x: float, y: float, w: float, h: float, **extra) -> Obstacle:
    return Obstacle(id=oid, x=x, y=y, width=w, height=h, **extra)


def _prop(
    oid: str,
    x: float,
    y: float,
    w: float,
    h: float,
    *,
    label: str,
    enabled: bool = True,
) -> Obstacle:
    return Obstacle(
        id=oid,
        x=x,
        y=y,
        width=w,
        height=h,
        kind="dynamic",
        enabled_by_default=enabled,
        label=label,
    )


SCENARIOS: dict[str, Scenario] = {
    "wide_vs_narrow": Scenario(
        id="wide_vs_narrow",
        label="Wide vs narrow hallway",
        description="Planner must prefer the large upper opening over the short narrow gap.",
        width=14.0,
        height=10.0,
        start={"x": 2.0, "y": 5.0, "yaw": 0.0},
        default_goal={"x": 11.5, "y": 5.0},
        obstacles=[
            _wall("south", 0, 0, 14, 0.16),
            _wall("north", 0, 9.84, 14, 0.16),
            _wall("west", 0, 0, 0.16, 10),
            _wall("east", 13.84, 0, 0.16, 10),
            _wall("divider-low", 6.75, 0.16, 0.2, 4.54),
            _wall("divider-mid", 6.75, 5.3, 0.2, 1.7),
            _wall("divider-high", 6.75, 9.0, 0.2, 0.84),
            _wall("left-room", 0.16, 2.2, 3.1, 0.16),
            _wall("right-room", 10.4, 6.6, 3.44, 0.16),
            _prop("box-sm", 3.2, 6.4, 0.35, 0.35, label="small box"),
            _prop("chair", 8.15, 7.45, 0.55, 0.55, label="chair"),
            _prop("crate", 9.6, 3.2, 0.85, 0.65, label="crate"),
            _prop("suitcase", 4.4, 3.5, 0.70, 0.40, label="suitcase"),
            _prop("plant", 11.2, 8.2, 0.30, 0.30, label="plant pot"),
            _prop("table", 1.4, 7.6, 1.10, 0.55, label="side table"),
        ],
    ),
    "apartment_loop": Scenario(
        id="apartment_loop",
        label="Apartment loop",
        description="Rooms, doorways, a loop corridor, and movable clutter of mixed sizes.",
        width=16.0,
        height=11.0,
        start={"x": 2.0, "y": 2.0, "yaw": 0.15},
        default_goal={"x": 13.5, "y": 8.5},
        obstacles=[
            _wall("south", 0, 0, 16, 0.16),
            _wall("north", 0, 10.84, 16, 0.16),
            _wall("west", 0, 0, 0.16, 11),
            _wall("east", 15.84, 0, 0.16, 11),
            _wall("room-a-1", 4.3, 0.16, 0.16, 3.1),
            _wall("room-a-2", 4.3, 4.45, 0.16, 2.2),
            _wall("room-a-3", 0.16, 6.5, 4.3, 0.16),
            _wall("center-low", 7.5, 0.16, 0.16, 4.2),
            _wall("center-high", 7.5, 5.65, 0.16, 5.19),
            _wall("room-b", 7.5, 7.1, 4.1, 0.16),
            _wall("room-c-1", 11.45, 4.0, 0.16, 3.25),
            _wall("room-c-2", 11.45, 8.55, 0.16, 2.29),
            _wall("alcove", 13.2, 3.9, 2.64, 0.16),
            _prop("chair", 9.1, 4.65, 0.55, 0.55, label="chair"),
            _prop("sofa-end", 1.2, 4.2, 1.40, 0.70, label="sofa section"),
            _prop("laundry", 5.4, 8.5, 0.75, 0.50, label="laundry basket"),
            _prop("trash", 12.4, 6.3, 0.35, 0.35, label="trash bin"),
            _prop("bookshelf", 14.2, 1.2, 0.45, 1.20, label="bookshelf"),
            _prop("toy", 3.0, 1.2, 0.25, 0.25, label="toy"),
            _prop("stroller", 8.4, 9.3, 0.95, 0.55, label="stroller"),
        ],
    ),
    "open_lab": Scenario(
        id="open_lab",
        label="Open localization lab",
        description="Sparse walls plus movable props of mixed sizes for relocalization tests.",
        width=12.0,
        height=9.0,
        start={"x": 2.0, "y": 2.0, "yaw": 0.0},
        default_goal={"x": 9.5, "y": 6.5},
        obstacles=[
            _wall("south", 0, 0, 12, 0.16),
            _wall("north", 0, 8.84, 12, 0.16),
            _wall("west", 0, 0, 0.16, 9),
            _wall("east", 11.84, 0, 0.16, 9),
            _wall("feature-a", 5.2, 1.8, 0.5, 2.5),
            _wall("feature-b", 7.5, 5.5, 2.1, 0.4),
            _prop("cone", 3.4, 4.8, 0.28, 0.28, label="cone"),
            _prop("chair", 5.6, 6.2, 0.55, 0.55, label="chair"),
            _prop("pallet", 9.0, 2.2, 1.20, 0.80, label="pallet"),
            _prop("toolbox", 2.4, 6.8, 0.60, 0.35, label="toolbox"),
            _prop("barrel", 8.4, 7.4, 0.70, 0.70, label="barrel"),
            _prop("cart", 10.2, 5.0, 0.90, 0.50, label="cart"),
        ],
    ),
}

_SAVED_SLAM_TRIED = False


def ensure_saved_slam_scenario(*, refresh: bool = False) -> str | None:
    """Load latest persistent_grid into SCENARIOS['saved_slam'] when available."""
    global _SAVED_SLAM_TRIED
    if "saved_slam" in SCENARIOS and not refresh:
        return "saved_slam"
    if _SAVED_SLAM_TRIED and not refresh:
        return "saved_slam" if "saved_slam" in SCENARIOS else None
    _SAVED_SLAM_TRIED = True
    try:
        from .slam_map import register_saved_slam_scenario
    except Exception:
        return None
    return register_saved_slam_scenario(SCENARIOS, refresh=refresh)


def default_scenario_id() -> str:
    ensure_saved_slam_scenario(refresh=False)
    if "saved_slam" in SCENARIOS:
        return "saved_slam"
    return "apartment_loop"


def point_inside_rect(x: float, y: float, rect: Obstacle, padding: float = 0.0) -> bool:
    return (
        rect.x - padding <= x <= rect.x + rect.width + padding
        and rect.y - padding <= y <= rect.y + rect.height + padding
    )


def ray_rect_distance(
    origin: dict, angle: float, rect: Obstacle, max_range: float
) -> float | None:
    dx = math.cos(angle)
    dy = math.sin(angle)
    t_min = 0.0
    t_max = max_range
    for position, direction, low, high in (
        (origin["x"], dx, rect.x, rect.x + rect.width),
        (origin["y"], dy, rect.y, rect.y + rect.height),
    ):
        if abs(direction) < 1e-9:
            if position < low or position > high:
                return None
            continue
        a = (low - position) / direction
        b = (high - position) / direction
        near, far = (a, b) if a < b else (b, a)
        t_min = max(t_min, near)
        t_max = min(t_max, far)
        if t_min > t_max:
            return None
    if 0.0 <= t_min <= max_range:
        return t_min
    return None


def cast_ray(
    origin: dict,
    angle: float,
    obstacles: Iterable[Obstacle],
    max_range: float = LIDAR_RANGE_M,
    min_range: float = LIDAR_MIN_RANGE_M,
) -> dict:
    """Cast one LD19-style TOF beam. Returns miss if closer than min range."""
    nearest = max_range
    hit = False
    obstacle_id = None
    for obstacle in obstacles:
        value = ray_rect_distance(origin, angle, obstacle, max_range)
        if value is not None and value < nearest:
            nearest = value
            hit = True
            obstacle_id = obstacle.id
    if hit and nearest < min_range:
        return {
            "distance": max_range,
            "hit": False,
            "obstacle_id": None,
            "invalid": True,
        }
    return {
        "distance": nearest,
        "hit": hit,
        "obstacle_id": obstacle_id,
        "invalid": False,
    }



def cast_ray_grid(
    origin: dict,
    angle: float,
    grid: list[int],
    width: int,
    height: int,
    resolution: float,
    *,
    occupied_min: int = OCCUPIED,
    max_range: float = LIDAR_RANGE_M,
    min_range: float = LIDAR_MIN_RANGE_M,
) -> dict:
    """DDA ray march through a solid occupancy grid (O(range/res) per beam)."""
    x0 = float(origin["x"])
    y0 = float(origin["y"])
    dx = math.cos(angle)
    dy = math.sin(angle)
    if abs(dx) < 1e-12 and abs(dy) < 1e-12:
        return {"distance": max_range, "hit": False, "obstacle_id": None, "invalid": False}

    # Start just outside the current cell center so we do not instantly self-hit.
    inv_res = 1.0 / resolution
    col = int(math.floor(x0 * inv_res))
    row = int(math.floor(y0 * inv_res))
    col = int(clamp(col, 0, width - 1))
    row = int(clamp(row, 0, height - 1))

    step_x = 1 if dx > 0.0 else -1 if dx < 0.0 else 0
    step_y = 1 if dy > 0.0 else -1 if dy < 0.0 else 0

    if step_x == 0:
        t_max_x = float("inf")
        t_delta_x = float("inf")
    else:
        next_boundary_x = (col + (1 if step_x > 0 else 0)) * resolution
        t_max_x = (next_boundary_x - x0) / dx
        t_delta_x = resolution / abs(dx)

    if step_y == 0:
        t_max_y = float("inf")
        t_delta_y = float("inf")
    else:
        next_boundary_y = (row + (1 if step_y > 0 else 0)) * resolution
        t_max_y = (next_boundary_y - y0) / dy
        t_delta_y = resolution / abs(dy)

    distance = 0.0
    # Skip the cell we start in (rover body), then march.
    for _ in range(width + height + 4):
        if t_max_x < t_max_y:
            distance = t_max_x
            t_max_x += t_delta_x
            col += step_x
        else:
            distance = t_max_y
            t_max_y += t_delta_y
            row += step_y
        if distance > max_range:
            return {"distance": max_range, "hit": False, "obstacle_id": None, "invalid": False}
        if col < 0 or row < 0 or col >= width or row >= height:
            return {"distance": max_range, "hit": False, "obstacle_id": None, "invalid": False}
        if grid[row * width + col] >= occupied_min:
            if distance < min_range:
                return {
                    "distance": max_range,
                    "hit": False,
                    "obstacle_id": None,
                    "invalid": True,
                }
            return {
                "distance": distance,
                "hit": True,
                "obstacle_id": "solid",
                "invalid": False,
            }
    return {"distance": max_range, "hit": False, "obstacle_id": None, "invalid": False}


def trace_grid_line(x0: int, y0: int, x1: int, y1: int) -> list[tuple[int, int]]:
    points: list[tuple[int, int]] = []
    x, y = x0, y0
    dx = abs(x1 - x0)
    sx = 1 if x0 < x1 else -1
    dy = -abs(y1 - y0)
    sy = 1 if y0 < y1 else -1
    error = dx + dy
    while True:
        points.append((x, y))
        if x == x1 and y == y1:
            break
        e2 = 2 * error
        if e2 >= dy:
            error += dy
            x += sx
        if e2 <= dx:
            error += dx
            y += sy
    return points


def _compute_clearance(blocked: list[int], width: int, height: int) -> list[int]:
    size = width * height
    clearance = [65535] * size
    queue: list[int] = []
    for index, value in enumerate(blocked):
        if value:
            clearance[index] = 0
            queue.append(index)
    head = 0
    while head < len(queue):
        index = queue[head]
        head += 1
        x = index % width
        y = index // width
        nxt = clearance[index] + 1
        for nx, ny in ((x - 1, y), (x + 1, y), (x, y - 1), (x, y + 1)):
            if nx < 0 or ny < 0 or nx >= width or ny >= height:
                continue
            next_index = ny * width + nx
            if nxt < clearance[next_index]:
                clearance[next_index] = nxt
                queue.append(next_index)
    return clearance


def _reconstruct_path(
    came_from: list[int], current: int, width: int, resolution: float
) -> list[dict]:
    path: list[dict] = []
    while current >= 0:
        x = current % width
        y = current // width
        path.append({"x": (x + 0.5) * resolution, "y": (y + 0.5) * resolution})
        current = came_from[current]
    path.reverse()
    return path



def plan_grid_path(
    grid: list[int],
    width: int,
    height: int,
    resolution: float,
    start: dict,
    goal: dict,
    *,
    unknown_is_blocked: bool = True,
    clearance_weight: float = 24.0,
    inflation_m: float | None = None,
) -> dict:
    """A* on an inflated occupancy grid.

    Clears a free disk around the start after inflation so mid-nav replans do
    not falsely report the robot as blocked when it is merely near a wall.
    Snaps inflated/occupied goals to the nearest free cell when possible.
    """
    size = width * height
    raw_blocked = [0] * size
    for index, value in enumerate(grid):
        if value >= LETHAL or (unknown_is_blocked and value == UNKNOWN):
            raw_blocked[index] = 1

    def to_cell(point: dict) -> tuple[int, int]:
        return (
            int(clamp(point["x"] / resolution, 0, width - 1)),
            int(clamp(point["y"] / resolution, 0, height - 1)),
        )

    def nearest_free(
        blocked: list[int], cell_x: int, cell_y: int, max_cells: int
    ) -> tuple[int, int] | None:
        if blocked[cell_y * width + cell_x] == 0:
            return cell_x, cell_y
        best: tuple[int, int] | None = None
        best_d = 1e18
        for dy in range(-max_cells, max_cells + 1):
            for dx in range(-max_cells, max_cells + 1):
                nx, ny = cell_x + dx, cell_y + dy
                if nx < 0 or ny < 0 or nx >= width or ny >= height:
                    continue
                if blocked[ny * width + nx]:
                    continue
                d = float(dx * dx + dy * dy)
                if d < best_d:
                    best_d = d
                    best = (nx, ny)
        return best

    start_x, start_y = to_cell(start)
    goal_x, goal_y = to_cell(goal)
    start_index = start_y * width + start_x
    goal_index = goal_y * width + goal_x

    # Hard walls / unknown at the true start are fatal.
    if raw_blocked[start_index]:
        snapped = nearest_free(raw_blocked, start_x, start_y, max_cells=4)
        if snapped is None:
            return {
                "path": [],
                "reason": "start_or_goal_blocked",
                "blocked": raw_blocked,
                "clearance": None,
            }
        start_x, start_y = snapped
        start_index = start_y * width + start_x

    if raw_blocked[goal_index]:
        snapped = nearest_free(raw_blocked, goal_x, goal_y, max_cells=12)
        if snapped is None:
            return {
                "path": [],
                "reason": "start_or_goal_blocked",
                "blocked": raw_blocked,
                "clearance": None,
            }
        goal_x, goal_y = snapped
        goal_index = goal_y * width + goal_x

    radius = (
        PLAN_INFLATION_M
        if inflation_m is None
        else max(0.0, float(inflation_m))
    )
    expansion = math.ceil(radius / resolution) if radius > 1e-9 else 0
    blocked = list(raw_blocked)
    if expansion > 0:
        for index, value in enumerate(raw_blocked):
            if not value:
                continue
            x = index % width
            y = index // width
            for dy in range(-expansion, expansion + 1):
                for dx in range(-expansion, expansion + 1):
                    if dx * dx + dy * dy > expansion * expansion:
                        continue
                    nx, ny = x + dx, y + dy
                    if 0 <= nx < width and 0 <= ny < height:
                        blocked[ny * width + nx] = 1

    # Robot is already here — carve a free disk through inflation only (never
    # through raw lethal cells) so replans near walls keep working.
    clear_r = max(expansion, 1)
    for dy in range(-clear_r, clear_r + 1):
        for dx in range(-clear_r, clear_r + 1):
            if dx * dx + dy * dy > clear_r * clear_r:
                continue
            nx, ny = start_x + dx, start_y + dy
            if nx < 0 or ny < 0 or nx >= width or ny >= height:
                continue
            idx = ny * width + nx
            if not raw_blocked[idx]:
                blocked[idx] = 0

    if blocked[goal_index]:
        snapped = nearest_free(blocked, goal_x, goal_y, max_cells=16)
        if snapped is None:
            return {
                "path": [],
                "reason": "start_or_goal_blocked",
                "blocked": blocked,
                "clearance": None,
            }
        goal_x, goal_y = snapped
        goal_index = goal_y * width + goal_x

    if blocked[start_index]:
        # Should be rare after carve; last-chance snap.
        snapped = nearest_free(blocked, start_x, start_y, max_cells=6)
        if snapped is None:
            return {
                "path": [],
                "reason": "start_or_goal_blocked",
                "blocked": blocked,
                "clearance": None,
            }
        start_x, start_y = snapped
        start_index = start_y * width + start_x

    clearance = _compute_clearance(blocked, width, height)
    g_score = [math.inf] * size
    came_from = [-1] * size
    closed = [0] * size
    open_heap: list[tuple[float, int]] = []
    g_score[start_index] = 0.0
    heapq.heappush(open_heap, (0.0, start_index))
    neighbors = (
        (-1, 0, 1.0),
        (1, 0, 1.0),
        (0, -1, 1.0),
        (0, 1, 1.0),
        (-1, -1, math.sqrt(2)),
        (1, -1, math.sqrt(2)),
        (-1, 1, math.sqrt(2)),
        (1, 1, math.sqrt(2)),
    )

    while open_heap:
        _, current = heapq.heappop(open_heap)
        if closed[current]:
            continue
        if current == goal_index:
            return {
                "path": _reconstruct_path(came_from, current, width, resolution),
                "reason": "ok",
                "blocked": blocked,
                "clearance": clearance,
            }
        closed[current] = 1
        cx = current % width
        cy = current // width
        for dx, dy, step_cost in neighbors:
            nx, ny = cx + dx, cy + dy
            if nx < 0 or ny < 0 or nx >= width or ny >= height:
                continue
            neighbor = ny * width + nx
            if blocked[neighbor] or closed[neighbor]:
                continue
            clear = clearance[neighbor]
            penalty = clearance_weight / (1.0 + clear)
            tentative = g_score[current] + step_cost + penalty
            if tentative >= g_score[neighbor]:
                continue
            came_from[neighbor] = current
            g_score[neighbor] = tentative
            hx = abs(goal_x - nx)
            hy = abs(goal_y - ny)
            heuristic = max(hx, hy) + (math.sqrt(2) - 1.0) * min(hx, hy)
            heapq.heappush(open_heap, (tentative + heuristic, neighbor))

    return {
        "path": [],
        "reason": "no_path",
        "blocked": blocked,
        "clearance": clearance,
    }



def _path_length(path: list[dict]) -> float:
    total = 0.0
    for index in range(1, len(path)):
        total += distance(path[index - 1], path[index])
    return total


def _rasterize_rects(
    grid: list[int],
    width: int,
    height: int,
    resolution: float,
    rects: Iterable[Obstacle],
    value: int = OCCUPIED,
) -> None:
    """Paint axis-aligned obstacle rectangles onto an occupancy grid."""
    for rect in rects:
        min_x = int(clamp(int(rect.x / resolution), 0, width - 1))
        max_x = int(
            clamp(math.ceil((rect.x + rect.width) / resolution), 0, width - 1)
        )
        min_y = int(clamp(int(rect.y / resolution), 0, height - 1))
        max_y = int(
            clamp(math.ceil((rect.y + rect.height) / resolution), 0, height - 1)
        )
        for y in range(min_y, max_y + 1):
            row = y * width
            for x in range(min_x, max_x + 1):
                grid[row + x] = value


class SlamNavSimulation:
    """Virtual rover: LD19 lidar SLAM (no odom), freeze-map, Nav2-style follow."""

    def __init__(
        self,
        scenario_id: str | None = None,
        *,
        noise_enabled: bool = True,
        noise_seed: int | None = None,
    ) -> None:
        self.speed_multiplier = 1.0
        self.noise_enabled = bool(noise_enabled)
        self.noise_seed = 0 if noise_seed is None else int(noise_seed)
        self._rng = random.Random(self.noise_seed)
        self._drive = NavDriveState()
        self.reset(scenario_id or default_scenario_id())

    def reset(self, scenario_id: str | None = None) -> "SlamNavSimulation":
        ensure_saved_slam_scenario(refresh=False)
        sid = scenario_id or getattr(getattr(self, "scenario", None), "id", None)
        sid = sid or default_scenario_id()
        self.scenario = SCENARIOS.get(sid, SCENARIOS["apartment_loop"])
        # Mutable working copy so props can be dragged without mutating templates.
        self.obstacles = [replace(item) for item in self.scenario.obstacles]
        self.width_cells = math.ceil(self.scenario.width / GRID_RESOLUTION_M)
        self.height_cells = math.ceil(self.scenario.height / GRID_RESOLUTION_M)
        self.slam_grid = [UNKNOWN] * (self.width_cells * self.height_cells)
        self.evidence = [0.0] * len(self.slam_grid)
        self.pose = dict(self.scenario.start)
        self.estimated_pose = dict(self.scenario.start)
        self.goal: dict | None = None
        self.path: list[dict] = []
        self.path_cursor = 0
        self.lidar: list[dict] = []
        self.mode = "mapping"
        self.autopilot = False
        self.manual = {"linear": 0.0, "angular": 0.0}
        self._drive = NavDriveState()
        self.dynamic_obstacle_enabled = True
        self.elapsed_sec = 0.0
        self.distance_m = 0.0
        self.collisions = 0
        self.replans = 0
        self._nav_stuck_count = 0
        self._nav_recovery_until = 0.0
        self._nav_recovery_wz = 1.0
        self._nav_best_goal_dist = math.inf
        self._nav_progress_at = 0.0
        self._nav_fail_streak = 0
        self._nav_last_progress_pose = dict(self.pose)
        self.scan_count = 0
        self.status = "mapping"
        self.last_plan_reason = "none"
        self.last_plan_length_m = 0.0
        self.goal_reachable: bool | None = None
        self.nav_complete = False
        self.fine_docking = False
        self.goal_yaw: float | None = None
        self._fine_dock_started: float | None = None
        self._gap_close_state: GapCloseState | None = None
        self._scan_accumulator = 0.0
        self._replan_accumulator = 0.0
        self._prev_body_hits: list[tuple[float, float]] = []
        self._prev_scan_pose: dict | None = None
        self._known_ratio = 0.0
        self._map_from_reveal = False
        self._relocalizing = False
        self._rng = random.Random(self.noise_seed)
        self._reset_explore_state()
        # Bootstrap lidar only — leave perceived map empty so construction can be tested.
        self._rebuild_solid_grid()
        self.scan(localize=False, integrate=False)
        self._refresh_known_ratio()
        return self

    def _reset_explore_state(self) -> None:
        self.exploring = False
        self._explore_phase = "idle"
        self._explore_yaw_accum = 0.0
        self._explore_spins = 0
        self._explore_full_spin = True
        self._explore_visited: list[tuple[float, float]] = []
        self._explore_failed_goals: list[tuple[float, float]] = []
        self._explore_blocked_streak = 0
        self._prev_frontier_count = -1
        self._frontier_stalls = 0


    def _rebuild_solid_grid(self) -> None:
        """Rasterize static walls once so lidar is O(rays * range) not O(rays * walls)."""
        grid = [FREE] * (self.width_cells * self.height_cells)
        _rasterize_rects(
            grid,
            self.width_cells,
            self.height_cells,
            GRID_RESOLUTION_M,
            [item for item in self.obstacles if item.kind != "dynamic"],
        )
        self._solid_grid = grid

    @property
    def active_obstacles(self) -> list[Obstacle]:
        return [
            item
            for item in self.obstacles
            if item.kind != "dynamic" or self.dynamic_obstacle_enabled
        ]

    def set_dynamic_obstacle(self, enabled: bool) -> None:
        self.dynamic_obstacle_enabled = bool(enabled)
        if self.goal:
            self.plan_to_goal(is_replan=True)

    def move_prop(self, prop_id: str, x: float, y: float) -> bool:
        """Reposition a movable prop; recenters on (x, y)."""
        for index, item in enumerate(self.obstacles):
            if item.id != prop_id or item.kind != "dynamic":
                continue
            new_x = clamp(x - item.width / 2, 0.05, self.scenario.width - item.width - 0.05)
            new_y = clamp(y - item.height / 2, 0.05, self.scenario.height - item.height - 0.05)
            self.obstacles[index] = replace(item, x=new_x, y=new_y)
            self.scan()
            if self.goal and self.mode == "localization":
                self.plan_to_goal(is_replan=True)
            self.status = f"moved {item.label or item.id}"
            return True
        return False

    def prop_at(self, x: float, y: float) -> Obstacle | None:
        if not self.dynamic_obstacle_enabled:
            return None
        for item in reversed(self.obstacles):
            if item.kind != "dynamic":
                continue
            if point_inside_rect(x, y, item, padding=0.02):
                return item
        return None

    def set_manual(self, linear: float, angular: float) -> None:
        self.manual = {
            "linear": clamp(float(linear or 0.0), -0.7, 0.7),
            "angular": clamp(float(angular or 0.0), -1.8, 1.8),
        }
        if abs(self.manual["linear"]) > 0 or abs(self.manual["angular"]) > 0:
            if self.exploring:
                self.stop_auto_map(freeze=False)
            self.autopilot = False
            self.status = "mapping" if self.mode == "mapping" else "manual"

    def reveal_map(self) -> None:
        self.slam_grid = [FREE] * len(self.slam_grid)
        self.evidence = [LOG_ODDS_FREE_THRESH - 0.1] * len(self.slam_grid)
        _rasterize_rects(
            self.slam_grid,
            self.width_cells,
            self.height_cells,
            GRID_RESOLUTION_M,
            [item for item in self.obstacles if item.kind != "dynamic"],
        )
        for index, value in enumerate(self.slam_grid):
            self.evidence[index] = (
                LOG_ODDS_CLAMP if value >= LETHAL else (LOG_ODDS_FREE_THRESH - 0.1)
            )
        # Do not integrate — that was overwriting GT walls with snapped/thin hits
        # and made scan-match prefer a shifted pose (visible "drift").
        self._map_from_reveal = True
        self.scan(localize=False, integrate=False)
        self._refresh_known_ratio()
        self.status = "map revealed"

    def clear_map(self) -> None:
        self._map_from_reveal = False
        self.slam_grid = [UNKNOWN] * len(self.slam_grid)
        self.evidence = [0.0] * len(self.slam_grid)
        self.mode = "mapping"
        self.path = []
        self.goal = None
        self.autopilot = False
        self._prev_body_hits = []
        self._prev_scan_pose = None
        self._known_ratio = 0.0
        self._reset_explore_state()
        self.status = "mapping"
        self.scan()

    def freeze_map(self) -> None:
        self.fill_map_holes()
        # Thin only SLAM-built maps. Thinning a reveal/GT raster removed wall
        # interiors and biased correlative matching off the true pose.
        if not getattr(self, "_map_from_reveal", False):
            self.thin_occupied_walls()
        self.mode = "localization"
        self.manual = {"linear": 0.0, "angular": 0.0}
        self.status = "map frozen"

    def resume_mapping(self) -> None:
        self.mode = "mapping"
        self.autopilot = False
        self.path = []
        self.status = "mapping"

    def reference_occupancy_grid(self) -> list[int]:
        """Ground-truth static layout (same basis as reveal_map), without mutating SLAM."""
        grid = [FREE] * len(self.slam_grid)
        _rasterize_rects(
            grid,
            self.width_cells,
            self.height_cells,
            GRID_RESOLUTION_M,
            [item for item in self.obstacles if item.kind != "dynamic"],
        )
        return grid

    def count_frontiers(self) -> int:
        return len(self._frontier_cells())

    def _frontier_cells(self) -> list[tuple[int, int]]:
        """FREE cells that border UNKNOWN — open map edges still to scan."""
        width = self.width_cells
        height = self.height_cells
        grid = self.slam_grid
        frontiers: list[tuple[int, int]] = []
        for y in range(height):
            row = y * width
            for x in range(width):
                if grid[row + x] != FREE:
                    continue
                for nx, ny in ((x - 1, y), (x + 1, y), (x, y - 1), (x, y + 1)):
                    if nx < 0 or ny < 0 or nx >= width or ny >= height:
                        continue
                    if grid[ny * width + nx] == UNKNOWN:
                        frontiers.append((x, y))
                        break
        return frontiers

    def _frontier_clusters(self) -> list[list[tuple[int, int]]]:
        cells = self._frontier_cells()
        if not cells:
            return []
        pending = set(cells)
        clusters: list[list[tuple[int, int]]] = []
        while pending:
            seed = pending.pop()
            stack = [seed]
            cluster = [seed]
            while stack:
                x, y = stack.pop()
                for nx, ny in (
                    (x - 1, y),
                    (x + 1, y),
                    (x, y - 1),
                    (x, y + 1),
                    (x - 1, y - 1),
                    (x + 1, y - 1),
                    (x - 1, y + 1),
                    (x + 1, y + 1),
                ):
                    key = (nx, ny)
                    if key in pending:
                        pending.remove(key)
                        stack.append(key)
                        cluster.append(key)
            clusters.append(cluster)
        return clusters

    def map_integrity(self) -> dict:
        """Compare perceived map to static ground truth (reveal_map basis)."""
        truth = self.reference_occupancy_grid()
        built = self.slam_grid
        gt_occ = 0
        gt_free = 0
        hit_occ = 0
        union_occ = 0
        free_ok = 0
        false_occ_on_free = 0
        for t, b in zip(truth, built):
            t_occ = t >= LETHAL
            b_occ = b >= LETHAL
            if t_occ:
                gt_occ += 1
                if b_occ:
                    hit_occ += 1
            else:
                gt_free += 1
                if b == FREE:
                    free_ok += 1
                elif b_occ:
                    false_occ_on_free += 1
            if t_occ or b_occ:
                union_occ += 1
        frontiers = self.count_frontiers()
        return {
            "frontiers": frontiers,
            "known_percent": self._known_ratio * 100.0,
            "occupied_iou": (hit_occ / union_occ) if union_occ else 1.0,
            "occupied_recall": (hit_occ / gt_occ) if gt_occ else 1.0,
            "free_recall": (free_ok / gt_free) if gt_free else 1.0,
            "false_occupied_on_free": false_occ_on_free,
            "gt_occupied": gt_occ,
            "gt_free": gt_free,
        }

    def _apply_evidence(
        self, index: int, delta: float, *, protect_confirmed: bool = True
    ) -> None:
        """Update log-odds evidence and project into the discrete SLAM grid."""
        # Free rays: confirmed walls resist demotion so the map does not walk.
        # Ghost-clear of snapped endpoints passes protect_confirmed=False.
        if (
            protect_confirmed
            and delta < 0
            and self.evidence[index] >= LOG_ODDS_OCC_THRESH
        ):
            delta *= 0.50
        value = clamp(self.evidence[index] + delta, -LOG_ODDS_CLAMP, LOG_ODDS_CLAMP)
        self.evidence[index] = value
        current = self.slam_grid[index]
        if value >= LOG_ODDS_OCC_THRESH:
            self.slam_grid[index] = OCCUPIED
        elif value <= LOG_ODDS_FREE_THRESH:
            self.slam_grid[index] = FREE
        elif current >= LETHAL and value > 0.15:
            # Hysteresis: keep a weakly-supported wall until free evidence wins.
            self.slam_grid[index] = OCCUPIED
        elif current == FREE and value < -0.20:
            self.slam_grid[index] = FREE
        else:
            self.slam_grid[index] = UNKNOWN

    def _hit_delta(self, index: int) -> float:
        """Soft-saturate hits so shifted revisits cannot thicken walls forever."""
        if self.evidence[index] >= LOG_ODDS_OCC_THRESH:
            return LOG_ODDS_HIT_SOFT
        return LOG_ODDS_HIT

    def _snap_hit_cell(
        self, cells: list[tuple[int, int]]
    ) -> tuple[int, int]:
        """Prefer an existing wall near the ray end (absorbs small pose shift)."""
        if not cells:
            return (0, 0)
        width = self.width_cells
        height = self.height_cells
        snap = MAP_HIT_SNAP_CELLS
        # Walk backward from the endpoint: reuse a confirmed wall if close.
        for x, y in reversed(cells[-min(4, len(cells)) :]):
            idx = y * width + x
            if self.slam_grid[idx] >= LETHAL or self.evidence[idx] >= 0.35:
                return (x, y)
        ex, ey = cells[-1]
        best = (ex, ey)
        best_score = -1.0
        for dy in range(-snap, snap + 1):
            for dx in range(-snap, snap + 1):
                nx, ny = ex + dx, ey + dy
                if nx < 0 or ny < 0 or nx >= width or ny >= height:
                    continue
                idx = ny * width + nx
                score = self.evidence[idx]
                if self.slam_grid[idx] >= LETHAL:
                    score += 1.5
                # Prefer nearer neighbors when scores tie.
                score -= 0.05 * (abs(dx) + abs(dy))
                if score > best_score:
                    best_score = score
                    best = (nx, ny)
        return best

    def _mark_occupied(self, x: int, y: int) -> None:
        width = self.width_cells
        height = self.height_cells
        dilate = MAP_HIT_DILATE_CELLS
        for dy in range(-dilate, dilate + 1):
            for dx in range(-dilate, dilate + 1):
                if abs(dx) + abs(dy) > dilate:
                    continue
                nx, ny = x + dx, y + dy
                if 0 <= nx < width and 0 <= ny < height:
                    idx = ny * width + nx
                    self._apply_evidence(idx, self._hit_delta(idx))

    def fill_map_holes(self, max_size: int = MAP_HOLE_FILL_MAX_CELLS) -> int:
        """Fill small enclosed UNKNOWN pockets (floor holes → FREE, wall gaps → OCCUPIED)."""
        width = self.width_cells
        height = self.height_cells
        grid = self.slam_grid
        size = width * height
        visited = [False] * size
        filled = 0
        neighbors = ((-1, 0), (1, 0), (0, -1), (0, 1))

        for start in range(size):
            if visited[start] or grid[start] != UNKNOWN:
                continue
            stack = [start]
            visited[start] = True
            component = [start]
            border_free = 0
            border_occ = 0
            touches_edge = False
            while stack:
                index = stack.pop()
                x = index % width
                y = index // width
                if x == 0 or y == 0 or x == width - 1 or y == height - 1:
                    touches_edge = True
                for dx, dy in neighbors:
                    nx, ny = x + dx, y + dy
                    if nx < 0 or ny < 0 or nx >= width or ny >= height:
                        continue
                    nidx = ny * width + nx
                    value = grid[nidx]
                    if value == UNKNOWN:
                        if not visited[nidx]:
                            visited[nidx] = True
                            stack.append(nidx)
                            component.append(nidx)
                    elif value == FREE:
                        border_free += 1
                    elif value >= LETHAL:
                        border_occ += 1
            if touches_edge or len(component) > max_size:
                continue
            # Only close floor holes (UNKNOWN fully enclosed by FREE).
            # Never fill wall-side UNKNOWN as OCCUPIED — that thickened walls and
            # sealed narrow but real passages.
            if border_free > 0 and border_occ == 0:
                fill_value = FREE
            else:
                continue
            for index in component:
                grid[index] = fill_value
                if fill_value == FREE:
                    self.evidence[index] = min(self.evidence[index], LOG_ODDS_FREE_THRESH - 0.05)
                else:
                    self.evidence[index] = max(self.evidence[index], LOG_ODDS_OCC_THRESH + 0.05)
            filled += len(component)
        if filled:
            self._refresh_known_ratio()
        return filled

    def thin_occupied_walls(self) -> int:
        """Keep only OCCUPIED cells on a free/unknown surface; drop interior smear."""
        width = self.width_cells
        height = self.height_cells
        grid = self.slam_grid
        keep = [False] * len(grid)
        neighbors = ((-1, 0), (1, 0), (0, -1), (0, 1))
        for y in range(height):
            row = y * width
            for x in range(width):
                index = row + x
                if grid[index] < LETHAL:
                    continue
                for dx, dy in neighbors:
                    nx, ny = x + dx, y + dy
                    if nx < 0 or ny < 0 or nx >= width or ny >= height:
                        keep[index] = True
                        break
                    neighbor = grid[ny * width + nx]
                    if neighbor == FREE or neighbor == UNKNOWN:
                        keep[index] = True
                        break
        removed = 0
        for index, retain in enumerate(keep):
            if grid[index] >= LETHAL and not retain:
                grid[index] = UNKNOWN
                self.evidence[index] = 0.0
                removed += 1
        if removed:
            self._refresh_known_ratio()
        return removed

    def start_auto_map(self) -> None:
        """Autonomous mapping: spin once, travel to new frontiers, short dwell scans."""
        self.clear_map()
        self.exploring = True
        self._explore_phase = "spin"
        self._explore_yaw_accum = 0.0
        self._explore_spins = 0
        self._explore_full_spin = True
        self._explore_visited = []
        self._explore_failed_goals: list[tuple[float, float]] = []
        self._explore_blocked_streak = 0
        self._prev_frontier_count = -1
        self._frontier_stalls = 0
        self.mode = "mapping"
        self.status = "auto-mapping · scanning"
    def stop_auto_map(self, *, freeze: bool = False) -> None:
        self.exploring = False
        self._explore_phase = "idle"
        self.autopilot = False
        self.manual = {"linear": 0.0, "angular": 0.0}
        self.path = []
        self.goal = None
        if freeze:
            self.fill_map_holes()
            self.freeze_map()
            self.status = "map complete · frozen"
        else:
            self.status = "mapping" if self.mode == "mapping" else self.status

    def _finish_auto_map(self) -> None:
        self.fill_map_holes(max_size=MAP_HOLE_FILL_MAX_CELLS * 3)
        self.fill_map_holes(max_size=MAP_HOLE_FILL_MAX_CELLS * 5)
        self.thin_occupied_walls()
        self.stop_auto_map(freeze=True)

    def _explore_complete(self) -> bool:
        frontiers = self.count_frontiers()
        if self._prev_frontier_count >= 0:
            if frontiers >= self._prev_frontier_count:
                self._frontier_stalls += 1
            else:
                self._frontier_stalls = 0
        self._prev_frontier_count = frontiers

        integrity = self.map_integrity()
        known = integrity["known_percent"]
        free_recall = integrity["free_recall"]
        occ_recall = integrity["occupied_recall"]

        if frontiers == 0 and known >= EXPLORE_MIN_KNOWN_PERCENT:
            return True
        # Never stop early while large open frontiers remain with poor coverage.
        if frontiers > 40 and free_recall < EXPLORE_MIN_FREE_RECALL:
            if self._explore_spins < EXPLORE_MAX_SPINS:
                return False
        # Good interior coverage: exterior UNKNOWN borders leave many frontiers.
        # Occupied recall vs thick GT is capped ~0.2 for 1-cell painted walls.
        if (
            free_recall >= 0.90
            and occ_recall >= 0.14
            and known >= EXPLORE_MIN_KNOWN_PERCENT
            and self._explore_spins >= 5
        ):
            return True
        # Closed house: high free/wall agreement and few open frontier edges.
        if (
            free_recall >= EXPLORE_MIN_FREE_RECALL
            and occ_recall >= 0.18
            and known >= EXPLORE_MIN_KNOWN_PERCENT
            and frontiers <= max(30, EXPLORE_DONE_MAX_FRONTIERS * 3)
            and self._explore_spins >= 3
        ):
            return True
        if (
            free_recall >= 0.88
            and occ_recall >= 0.14
            and known >= 75.0
            and self._explore_spins >= 5
        ):
            return True
        if (
            self._frontier_stalls >= 3
            and free_recall >= EXPLORE_MIN_FREE_RECALL
            and occ_recall >= 0.14
            and known >= EXPLORE_MIN_KNOWN_PERCENT
            and self._explore_spins >= 5
        ):
            return True
        if self._explore_spins >= EXPLORE_MAX_SPINS:
            return (
                free_recall >= EXPLORE_MIN_FREE_RECALL and occ_recall >= 0.14
            ) or known >= 80.0
        if self._explore_spins >= EXPLORE_MAX_SPINS * 2:
            return True
        return False
    def _plan_explore_to(self, x: float, y: float) -> bool:
        self.goal = {
            "x": clamp(x, 0.25, self.scenario.width - 0.25),
            "y": clamp(y, 0.25, self.scenario.height - 0.25),
        }
        # Treat UNKNOWN as traversable while exploring; light inflation only.
        result = plan_grid_path(
            self.slam_grid,
            self.width_cells,
            self.height_cells,
            GRID_RESOLUTION_M,
            self.estimated_pose,
            self.goal,
            unknown_is_blocked=False,
            clearance_weight=12.0,
            inflation_m=0.12,
        )
        self.path = result["path"]
        self.path_cursor = 1 if len(self.path) > 1 else 0
        self.last_plan_reason = result["reason"]
        self.last_plan_length_m = _path_length(self.path)
        if distance(self.estimated_pose, self.goal) < EXPLORE_ARRIVE_M:
            self.autopilot = False
            return True
        # Reject routes that clip physical walls (kinematic sim collision).
        for point in self.path[1:12]:
            if self.is_pose_collision(point):
                self.path = []
                self.path_cursor = 0
                self.autopilot = False
                self.last_plan_reason = "collision"
                return False
        self.autopilot = len(self.path) > 1
        return self.autopilot

    def _pick_next_frontier(self) -> bool:
        clusters = self._frontier_clusters()
        if not clusters:
            return False
        est = self.estimated_pose
        res = GRID_RESOLUTION_M

        def already_visited(wx: float, wy: float) -> bool:
            for vx, vy in self._explore_visited:
                if math.hypot(wx - vx, wy - vy) < EXPLORE_VISIT_RADIUS_M:
                    return True
            return False

        def already_failed(wx: float, wy: float) -> bool:
            for fx, fy in self._explore_failed_goals:
                if math.hypot(wx - fx, wy - fy) < EXPLORE_BLOCKED_GOAL_RADIUS_M:
                    return True
            return False

        def cluster_score(cluster: list[tuple[int, int]]) -> tuple[float, float]:
            cx = sum(c[0] for c in cluster) / len(cluster)
            cy = sum(c[1] for c in cluster) / len(cluster)
            wx = (cx + 0.5) * res
            wy = (cy + 0.5) * res
            dist = math.hypot(wx - est["x"], wy - est["y"])
            # Prefer large, distant unexplored pockets over re-scanning nearby ones.
            visited_penalty = 2000.0 if already_visited(wx, wy) else 0.0
            failed_penalty = 1500.0 if already_failed(wx, wy) else 0.0
            # Strong distance preference so we leapfrog rooms instead of orbiting.
            return (visited_penalty + failed_penalty, -dist * 0.35 - float(len(cluster)))

        attempts = 0
        for cluster in sorted(clusters, key=cluster_score)[:12]:
            cx = sum(c[0] for c in cluster) / len(cluster)
            cy = sum(c[1] for c in cluster) / len(cluster)
            wx = (cx + 0.5) * res
            wy = (cy + 0.5) * res
            if already_visited(wx, wy) or already_failed(wx, wy):
                continue
            ordered = sorted(
                cluster,
                key=lambda c: -math.hypot(
                    (c[0] + 0.5) * res - est["x"],
                    (c[1] + 0.5) * res - est["y"],
                ),
            )
            candidates: list[tuple[int, int]] = []
            for x, y in ordered[:12]:
                candidates.append((x, y))
                for dx, dy in ((-1, 0), (1, 0), (0, -1), (0, 1), (-2, 0), (2, 0)):
                    nx, ny = x + dx, y + dy
                    if (
                        0 <= nx < self.width_cells
                        and 0 <= ny < self.height_cells
                        and self.slam_grid[ny * self.width_cells + nx] == FREE
                    ):
                        candidates.append((nx, ny))
            seen: set[tuple[int, int]] = set()
            for x, y in candidates:
                if (x, y) in seen:
                    continue
                seen.add((x, y))
                gx = (x + 0.5) * res
                gy = (y + 0.5) * res
                if already_visited(gx, gy) or already_failed(gx, gy):
                    continue
                if math.hypot(gx - est["x"], gy - est["y"]) < 1.15:
                    continue
                attempts += 1
                if attempts > 14:
                    return False
                if self._plan_explore_to(gx, gy):
                    self.status = (
                        f"auto-mapping · explore ({len(cluster)} frontier)"
                    )
                    return True
        return False

    def _handle_explore_blocked(self) -> None:
        """Collision during travel: mark goal failed and replan — do not burn spins."""
        if self.goal:
            self._explore_failed_goals.append((self.goal["x"], self.goal["y"]))
            if len(self._explore_failed_goals) > 24:
                self._explore_failed_goals = self._explore_failed_goals[-16:]
        self.autopilot = False
        self.path = []
        self.goal = None
        self._explore_blocked_streak += 1
        if self._explore_blocked_streak >= EXPLORE_MAX_BLOCKED:
            # Too many dead-ends here — scan once then try elsewhere / finish.
            self._explore_blocked_streak = 0
            self._explore_phase = "spin"
            self._explore_full_spin = False
            self._explore_yaw_accum = 0.0
            self.status = "auto-mapping · blocked · rescanning"
            return
        if self._pick_next_frontier():
            self._explore_phase = "travel"
            self.status = "auto-mapping · rerouting"
            return
        self._explore_phase = "spin"
        self._explore_full_spin = False
        self._explore_yaw_accum = 0.0
        self.status = "auto-mapping · blocked · rescanning"

    def explore_command(self) -> dict:
        if not self.exploring:
            return {"linear": 0.0, "angular": 0.0}
        if self._explore_phase == "spin":
            return {"linear": 0.0, "angular": EXPLORE_SPIN_ANGULAR}
        if self._explore_phase == "travel":
            return self.autopilot_command()
        return {"linear": 0.0, "angular": 0.0}
    def _update_explore(self, dt: float, command: dict) -> None:
        if not self.exploring:
            return
        if self._explore_phase == "spin":
            self._explore_yaw_accum += abs(float(command.get("angular", 0.0))) * dt
            need = EXPLORE_SPIN_RAD if self._explore_full_spin else EXPLORE_DWELL_RAD
            if self._explore_yaw_accum < need:
                return
            self._explore_yaw_accum = 0.0
            self._explore_spins += 1
            self._explore_full_spin = False
            est = self.estimated_pose
            self._explore_visited.append((est["x"], est["y"]))
            self.fill_map_holes()
            self.thin_occupied_walls()
            if self._explore_complete():
                self._finish_auto_map()
                return
            if self._pick_next_frontier():
                self._explore_phase = "travel"
                self.status = "auto-mapping · traveling"
            elif self.count_frontiers() > 20 and self._explore_spins < EXPLORE_MAX_SPINS:
                # Stuck in one place after expand attempts — map is as closed as we can get.
                if (
                    len(self._explore_visited) >= 2
                    and all(
                        math.hypot(
                            self._explore_visited[-1][0] - self._explore_visited[-k][0],
                            self._explore_visited[-1][1] - self._explore_visited[-k][1],
                        )
                        < 0.7
                        for k in range(2, min(4, len(self._explore_visited) + 1))
                    )
                ):
                    self._finish_auto_map()
                    return
                # One short dwell retry, not another full 360 re-scan.
                self._explore_full_spin = False
                self.status = "auto-mapping · expanding"
            else:
                self._finish_auto_map()
            return

        if self._explore_phase == "travel":
            goal = self.goal
            if not goal:
                self._explore_phase = "spin"
                self._explore_full_spin = False
                return
            if (
                distance(self.estimated_pose, goal) < EXPLORE_ARRIVE_M
                or not self.autopilot
            ):
                self.autopilot = False
                self.path = []
                self._explore_blocked_streak = 0
                # Short dwell at the new viewpoint — not another full 360.
                self._explore_phase = "spin"
                self._explore_full_spin = False
                self._explore_yaw_accum = 0.0
                self.status = "auto-mapping · scanning"
    @staticmethod
    def _body_hits(rays: list[dict]) -> list[tuple[float, float]]:
        hits: list[tuple[float, float]] = []
        for ray in rays:
            if ray.get("blind") or ray.get("invalid") or not ray["hit"]:
                continue
            radius = float(ray["distance"])
            angle = float(ray["relative_angle"])
            hits.append((radius * math.cos(angle), radius * math.sin(angle)))
        return hits

    @staticmethod
    def _body_to_world(
        pose: dict, body_hits: list[tuple[float, float]]
    ) -> list[tuple[float, float]]:
        cy = math.cos(pose["yaw"])
        sy = math.sin(pose["yaw"])
        return [
            (pose["x"] + bx * cy - by * sy, pose["y"] + bx * sy + by * cy)
            for bx, by in body_hits
        ]

    @staticmethod
    def _subsample_hits(
        hits: list[tuple[float, float]], limit: int = SCAN_MATCH_HIT_SAMPLES
    ) -> list[tuple[float, float]]:
        if len(hits) <= limit:
            return hits
        step = len(hits) / limit
        return [hits[int(index * step)] for index in range(limit)]

    def _score_map_match_fast(
        self, x: float, y: float, yaw: float, body_hits: list[tuple[float, float]]
    ) -> float:
        """Correlative scan-to-map score on the occupancy grid."""
        cy = math.cos(yaw)
        sy = math.sin(yaw)
        score = 0.0
        width = self.width_cells
        height = self.height_cells
        grid = self.slam_grid
        inv = 1.0 / GRID_RESOLUTION_M
        for bx, by in body_hits:
            wx = x + bx * cy - by * sy
            wy = y + bx * sy + by * cy
            cx = int(wx * inv)
            cy_i = int(wy * inv)
            if cx < 0 or cy_i < 0 or cx >= width or cy_i >= height:
                score -= 1.5
                continue
            value = grid[cy_i * width + cx]
            if value >= LETHAL:
                score += 2.0
            elif value == FREE:
                score -= 0.7
            else:
                # Thin SLAM walls: allow a weak orthogonal neighbor hit.
                near = False
                for dx, dy in ((-1, 0), (1, 0), (0, -1), (0, 1)):
                    nx, ny = cx + dx, cy_i + dy
                    if 0 <= nx < width and 0 <= ny < height and grid[ny * width + nx] >= LETHAL:
                        near = True
                        break
                score += 0.4 if near else -0.1
        return score

    def _score_scan_to_scan_fast(
        self,
        x: float,
        y: float,
        yaw: float,
        body_hits: list[tuple[float, float]],
        prev_world: list[tuple[float, float]],
    ) -> float:
        if not prev_world:
            return -1e9
        cy = math.cos(yaw)
        sy = math.sin(yaw)
        total = 0.0
        for bx, by in body_hits:
            wx = x + bx * cy - by * sy
            wy = y + bx * sy + by * cy
            best = 1e9
            for px, py in prev_world:
                dx = wx - px
                dy = wy - py
                dist = dx * dx + dy * dy
                if dist < best:
                    best = dist
            total += math.sqrt(best)
        return -(total / len(body_hits)) * 40.0

    def kidnap_rover(
        self,
        x: float,
        y: float,
        yaw: float | None = None,
        *,
        keep_estimate: bool = True,
    ) -> bool:
        """Teleport ground-truth pose; optionally leave lidar estimate stale.

        Used to test relocalization: green jumps, yellow stays until scan matching
        (including a one-shot global search) recovers.
        """
        new_pose = {
            "x": clamp(float(x), 0.3, self.scenario.width - 0.3),
            "y": clamp(float(y), 0.3, self.scenario.height - 0.3),
            "yaw": wrap_angle(
                self.pose["yaw"] if yaw is None else float(yaw)
            ),
        }
        if self.is_pose_collision(new_pose):
            self.status = "teleport blocked · obstacle"
            return False

        self.pose = new_pose
        if not keep_estimate:
            self.estimated_pose = dict(new_pose)
            self._relocalizing = False
        else:
            # Leave yellow where it was; force a global scan-match on the next localize.
            self._relocalizing = True

        self._prev_body_hits = []
        self._prev_scan_pose = None
        self.autopilot = False
        self.manual = {"linear": 0.0, "angular": 0.0}
        # New lidar from the jumped pose, but do not correct the estimate yet so the
        # divergence is visible until the next localize / tick.
        self.scan(localize=False)
        err = distance(self.pose, self.estimated_pose)
        self.status = (
            f"kidnapped · err {err:.2f}m · recovering"
            if keep_estimate
            else f"teleported · estimate synced"
        )
        return True

    def _global_relocalize(
        self, sample: list[tuple[float, float]]
    ) -> tuple[float, float, float, float]:
        """Coarse→fine correlative match over known free space (kidnap recovery)."""

        def gather(xy_step: float, yaw_step: float, x0: float, y0: float, yaw0: float,
                   x_span: float, y_span: float, yaw_span: float) -> list[tuple[float, float, float, float]]:
            scored: list[tuple[float, float, float, float]] = []
            yaw = yaw0 - yaw_span
            while yaw <= yaw0 + yaw_span + 1e-9:
                x = x0 - x_span
                while x <= x0 + x_span + 1e-9:
                    y = y0 - y_span
                    while y <= y0 + y_span + 1e-9:
                        if (
                            0.2 <= x <= self.scenario.width - 0.2
                            and 0.2 <= y <= self.scenario.height - 0.2
                        ):
                            cx = int(x / GRID_RESOLUTION_M)
                            cy = int(y / GRID_RESOLUTION_M)
                            if (
                                0 <= cx < self.width_cells
                                and 0 <= cy < self.height_cells
                                and self.slam_grid[cy * self.width_cells + cx] == FREE
                            ):
                                cand = self._score_map_match_fast(
                                    x, y, wrap_angle(yaw), sample
                                )
                                scored.append((cand, x, y, wrap_angle(yaw)))
                        y += xy_step
                    x += xy_step
                yaw += yaw_step
            scored.sort(reverse=True, key=lambda item: item[0])
            return scored

        # Stage 1: coarse over the whole known free map.
        coarse = gather(
            GLOBAL_RELOC_COARSE_XY_M,
            GLOBAL_RELOC_COARSE_YAW_RAD,
            self.scenario.width * 0.5,
            self.scenario.height * 0.5,
            0.0,
            self.scenario.width * 0.5,
            self.scenario.height * 0.5,
            math.pi,
        )
        if not coarse:
            pose = self.estimated_pose
            return pose["x"], pose["y"], pose["yaw"], -1e9

        # Stage 2: fine search around the best coarse peaks.
        best_score, best_x, best_y, best_yaw = coarse[0]
        for _, cx, cy, cyaw in coarse[:GLOBAL_RELOC_TOP_K]:
            fine = gather(
                GLOBAL_RELOC_FINE_XY_M,
                GLOBAL_RELOC_FINE_YAW_RAD,
                cx,
                cy,
                cyaw,
                GLOBAL_RELOC_FINE_RADIUS_M,
                GLOBAL_RELOC_FINE_RADIUS_M,
                math.radians(20.0),
            )
            if fine and fine[0][0] > best_score:
                best_score, best_x, best_y, best_yaw = fine[0]
        return best_x, best_y, best_yaw, best_score

    def _search_pose(
        self,
        px: float,
        py: float,
        pyaw: float,
        score_fn,
        *,
        xy_m: float,
        yaw_rad: float,
        xy_step: float,
        yaw_step: float,
    ) -> tuple[float, float, float, float]:
        best_x, best_y, best_yaw = px, py, pyaw
        best_score = score_fn(px, py, pyaw)
        yaw_steps = int(round(yaw_rad / yaw_step))
        xy_steps = int(round(xy_m / xy_step))
        for iyaw in range(-yaw_steps, yaw_steps + 1):
            yaw = wrap_angle(pyaw + iyaw * yaw_step)
            for ix in range(-xy_steps, xy_steps + 1):
                x = px + ix * xy_step
                for iy in range(-xy_steps, xy_steps + 1):
                    if ix == 0 and iy == 0 and iyaw == 0:
                        continue
                    y = py + iy * xy_step
                    cand = score_fn(x, y, yaw)
                    if cand > best_score:
                        best_score = cand
                        best_x, best_y, best_yaw = x, y, yaw
        return best_x, best_y, best_yaw, best_score

    def localize_from_lidar(self, rays: list[dict] | None = None) -> None:
        """Cartographer-like correlative match. No wheel / cmd_vel odometry."""
        rays = rays if rays is not None else self.lidar
        body_hits = self._body_hits(rays)
        if len(body_hits) < 10:
            self._prev_body_hits = body_hits
            self._prev_scan_pose = dict(self.estimated_pose)
            return

        sample = self._subsample_hits(body_hits)
        prior = self.estimated_pose
        px, py, pyaw = prior["x"], prior["y"], prior["yaw"]
        known_ratio = getattr(self, "_known_ratio", 0.0)
        use_map = known_ratio > 0.01

        if self._relocalizing and use_map:
            px, py, pyaw, _ = self._global_relocalize(sample)
            self.estimated_pose = {"x": px, "y": py, "yaw": pyaw}
            self._relocalizing = False
            err = distance(self.pose, self.estimated_pose)
            self.status = f"relocalized · err {err:.2f}m"
            use_scan = False
        else:
            use_scan = (
                known_ratio < 0.08
                and bool(self._prev_body_hits and self._prev_scan_pose)
            )

        prev_world: list[tuple[float, float]] = []
        if use_scan:
            prev_full = self._body_to_world(
                self._prev_scan_pose, self._prev_body_hits
            )
            prev_world = self._subsample_hits(prev_full, 32)

        def score(x: float, y: float, yaw: float) -> float:
            total = 0.0
            if use_map:
                total += self._score_map_match_fast(x, y, yaw, sample)
            if use_scan:
                weight = 1.0 if known_ratio < 0.04 else 0.35
                total += weight * self._score_scan_to_scan_fast(
                    x, y, yaw, sample, prev_world
                )
            return total

        px, py, pyaw, _ = self._search_pose(
            px,
            py,
            pyaw,
            score,
            xy_m=SCAN_MATCH_XY_M,
            yaw_rad=SCAN_MATCH_YAW_RAD,
            xy_step=SCAN_MATCH_XY_STEP_M,
            yaw_step=SCAN_MATCH_YAW_STEP_RAD,
        )

        # Fine refine whenever we have map structure — cuts grid-step drift.
        # (Previously frozen-only; skipping it during mapping let yellow wander.)
        if use_map and known_ratio >= 0.04:
            px, py, pyaw, _ = self._search_pose(
                px,
                py,
                pyaw,
                score,
                xy_m=SCAN_MATCH_FINE_XY_M,
                yaw_rad=SCAN_MATCH_FINE_YAW_RAD,
                xy_step=SCAN_MATCH_FINE_XY_STEP_M,
                yaw_step=SCAN_MATCH_FINE_YAW_STEP_RAD,
            )

        # EMA toward the match — raw grid steps made the ego-map swim every scan.
        alpha = 0.45 if self.mode == "mapping" else 0.35
        prev = self.estimated_pose
        dyaw = wrap_angle(pyaw - prev["yaw"])
        self.estimated_pose = {
            "x": prev["x"] + alpha * (px - prev["x"]),
            "y": prev["y"] + alpha * (py - prev["y"]),
            "yaw": wrap_angle(prev["yaw"] + alpha * dyaw),
        }
        self._prev_body_hits = body_hits
        self._prev_scan_pose = dict(self.estimated_pose)

    def _refresh_known_ratio(self) -> None:
        known = sum(1 for value in self.slam_grid if value != UNKNOWN)
        self._known_ratio = known / max(1, len(self.slam_grid))

    def scan(self, *, localize: bool = True, integrate: bool | None = None) -> list[dict]:
        """Emit one LD19 revolution with rear blind sector + optional noise."""
        rays: list[dict] = []
        if getattr(self, "_solid_grid", None) is None:
            self._rebuild_solid_grid()
        dynamics = [
            item for item in self.active_obstacles if item.kind == "dynamic"
        ]
        solid = self._solid_grid
        width = self.width_cells
        height = self.height_cells
        for index in range(LIDAR_RAY_COUNT):
            relative = math.radians(index * LIDAR_ANGULAR_RES_DEG)
            angle = wrap_angle(self.pose["yaw"] + relative)
            blind = is_lidar_blind_bearing(relative)
            if blind:
                rays.append(
                    {
                        "angle": angle,
                        "relative_angle": relative,
                        "distance": LIDAR_RANGE_M,
                        "hit": False,
                        "obstacle_id": None,
                        "invalid": True,
                        "blind": True,
                    }
                )
                continue

            hit = cast_ray_grid(
                self.pose,
                angle,
                solid,
                width,
                height,
                GRID_RESOLUTION_M,
                max_range=LIDAR_RANGE_M,
                min_range=LIDAR_MIN_RANGE_M,
            )
            if dynamics:
                dyn = cast_ray(
                    self.pose,
                    angle,
                    dynamics,
                    max_range=LIDAR_RANGE_M,
                    min_range=LIDAR_MIN_RANGE_M,
                )
                if dyn.get("invalid") and not hit.get("invalid"):
                    # Prefer reporting near-range invalid from dynamics.
                    if dyn["distance"] <= hit["distance"]:
                        hit = dyn
                elif dyn["hit"] and (
                    not hit["hit"] or dyn["distance"] < hit["distance"]
                ):
                    hit = dyn
            if hit.get("invalid"):
                distance = LIDAR_RANGE_M
                is_hit = False
                dropped = False
            elif hit["hit"]:
                distance, is_hit, dropped = apply_lidar_noise(
                    hit["distance"],
                    True,
                    self._rng,
                    enabled=self.noise_enabled,
                )
            else:
                distance, is_hit, dropped = LIDAR_RANGE_M, False, False
            rays.append(
                {
                    "angle": angle,
                    "relative_angle": relative,
                    "distance": min(distance, LIDAR_RANGE_M),
                    "hit": is_hit,
                    "obstacle_id": hit["obstacle_id"] if is_hit else None,
                    "invalid": bool(hit.get("invalid")) or dropped,
                    "blind": False,
                }
            )
        self.lidar = rays
        self.scan_count += 1
        # Pose from lidar only (scan-to-map / scan-to-scan). Never cmd_vel odom.
        # Noise-free mapping/nav pins estimate to truth so path following stays
        # consistent. Localization-only (kidnap tests) still scan-matches.
        if not self.noise_enabled and (self.mode == "mapping" or self.autopilot):
            self.estimated_pose = dict(self.pose)
        elif localize:
            self.localize_from_lidar(rays)
        should_integrate = self.mode == "mapping" if integrate is None else integrate
        if should_integrate:
            self.integrate_scan(rays)
        return rays


    def integrate_scan(self, rays: list[dict] | None = None) -> None:
        rays = rays if rays is not None else self.lidar
        # Noise-free paints from truth so estimate jitter cannot smear walls.
        if not self.noise_enabled:
            paint = self.pose
        else:
            paint = self.estimated_pose
        width = self.width_cells

        def to_cell(px: float, py: float) -> tuple[int, int]:
            return (
                int(clamp(px / GRID_RESOLUTION_M, 0, self.width_cells - 1)),
                int(clamp(py / GRID_RESOLUTION_M, 0, self.height_cells - 1)),
            )

        origin = to_cell(paint["x"], paint["y"])
        for ray in rays:
            if ray.get("invalid") or ray.get("blind"):
                continue
            bearing = float(ray["relative_angle"])
            wx = paint["x"] + math.cos(paint["yaw"] + bearing) * ray["distance"]
            wy = paint["y"] + math.sin(paint["yaw"] + bearing) * ray["distance"]
            end = to_cell(wx, wy)
            cells = trace_grid_line(origin[0], origin[1], end[0], end[1])
            free_count = max(0, len(cells) - 1) if ray["hit"] else len(cells)
            for index in range(free_count):
                x, y = cells[index]
                # Free votes demote false / shifted wall cells over time.
                self._apply_evidence(y * width + x, LOG_ODDS_MISS)
            if ray["hit"] and cells:
                sx, sy = self._snap_hit_cell(cells)
                ex, ey = cells[-1]
                # Demote the geometric endpoint when we snapped elsewhere so a
                # pose-shifted double wall does not accumulate.
                if (sx, sy) != (ex, ey):
                    self._apply_evidence(
                        ey * width + ex,
                        LOG_ODDS_MISS * EXPLORE_GHOST_MISS_SCALE,
                        protect_confirmed=False,
                    )
                self._mark_occupied(sx, sy)
        self._refresh_known_ratio()
        # Periodic surface thin: drop interior smear so walls stay ~1 cell thick.
        if (
            self.mode == "mapping"
            and MAP_THIN_EVERY_SCANS > 0
            and self.scan_count > 0
            and self.scan_count % MAP_THIN_EVERY_SCANS == 0
        ):
            self.thin_occupied_walls()
    def planning_grid(self) -> list[int]:
        grid = list(self.slam_grid)
        _rasterize_rects(
            grid,
            self.width_cells,
            self.height_cells,
            GRID_RESOLUTION_M,
            [item for item in self.active_obstacles if item.kind == "dynamic"],
        )
        return grid

    def set_goal(
        self,
        x: float,
        y: float,
        yaw: float | None = None,
        *,
        fine_docking: bool = False,
    ) -> bool:
        if self.exploring:
            self.stop_auto_map(freeze=False)
        self.goal = {
            "x": clamp(x, 0.25, self.scenario.width - 0.25),
            "y": clamp(y, 0.25, self.scenario.height - 0.25),
        }
        self.fine_docking = bool(fine_docking)
        self.goal_yaw = float(yaw) if yaw is not None else None
        self._fine_dock_started = None
        self._gap_close_state = None
        self.nav_complete = False
        self.goal_reachable = None
        self._nav_best_goal_dist = math.inf
        self._nav_progress_at = self.elapsed_sec
        self._nav_fail_streak = 0
        self._nav_recovery_until = 0.0
        self._nav_stuck_count = 0
        self._nav_last_progress_pose = dict(self.pose)
        known = sum(1 for value in self.slam_grid if value != UNKNOWN)
        known_percent = (known / max(1, len(self.slam_grid))) * 100.0
        if self.mode != "localization":
            if known_percent < 2.0:
                self.autopilot = False
                self.path = []
                self.goal_reachable = False
                self.status = "map more before navigation"
                return False
            # Real workflow freezes before Nav2; one-click goal does the same.
            self.freeze_map()
        return self.plan_to_goal(is_replan=False)

    def set_default_goal(self) -> bool:
        return self.set_goal(
            self.scenario.default_goal["x"], self.scenario.default_goal["y"]
        )

    def plan_to_goal(self, is_replan: bool = False) -> bool:
        if not self.goal:
            return False
        # Navigation always requires a frozen map (Nav2-style localization mode).
        if self.mode != "localization":
            self.freeze_map()
        prev_path = list(self.path) if is_replan else []
        prev_cursor = self.path_cursor if is_replan else 0
        result = {"path": [], "reason": "no_path"}
        # Try safer inflation first, then thinner so narrow saved-map corridors
        # stay solvable (footprint probe still prevents wall ramming).
        for infl, weight in (
            (PLAN_INFLATION_M, 16.0),
            (max(0.10, PLAN_INFLATION_M * 0.75), 12.0),
            (0.10, 8.0),
        ):
            candidate = plan_grid_path(
                self.planning_grid(),
                self.width_cells,
                self.height_cells,
                GRID_RESOLUTION_M,
                self.estimated_pose,
                self.goal,
                unknown_is_blocked=True,
                clearance_weight=weight,
                inflation_m=infl,
            )
            if candidate["path"]:
                result = candidate
                break
        if not result["path"]:
            result = candidate
        self.path = result["path"]
        self.path_cursor = 1 if len(self.path) > 1 else 0
        self.last_plan_reason = result["reason"]
        self.last_plan_length_m = _path_length(self.path)
        self.goal_reachable = bool(self.path)
        self.nav_complete = False
        # Drive whenever we have a real polyline (not a single cell).
        self.autopilot = len(self.path) > 1
        if not self.path and is_replan and len(prev_path) > 1:
            # Never abort mid-nav because the robot brushed inflated cells.
            self.path = prev_path
            self.path_cursor = min(prev_cursor, len(self.path) - 1)
            self.autopilot = True
            self.goal_reachable = True
            self.status = "navigating · replan deferred"
            self.replans += 1
            return True
        if self.autopilot:
            # Nav-init settle: brief pause before first WASD (fresh lidar pose).
            from .drive import AUTOPILOT_SETTLE_SEC

            if not is_replan and AUTOPILOT_SETTLE_SEC > 0:
                self._drive.settle_until = self.elapsed_sec + AUTOPILOT_SETTLE_SEC
                self._drive.last_moving_at = self.elapsed_sec
                self._drive.phase = "settle"
            # Fresh path — don't treat longer detours as stalled progress.
            self._nav_best_goal_dist = math.inf
            self._nav_progress_at = self.elapsed_sec
            self.status = "navigating · goal reachable"
        elif self.path:
            # Already on the goal cell — mark arrived if close enough.
            if distance(self.estimated_pose, self.goal) < 0.45:
                self.nav_complete = True
                self.status = "nav complete · arrived"
            else:
                self.status = "path ready · waiting"
        else:
            reason = result["reason"].replace("_", " ")
            self.status = f"unreachable · {reason}"
            self.autopilot = False
        if is_replan:
            self.replans += 1
        return bool(self.path)

    def is_pose_collision(self, pose: dict, radius: float | None = None) -> bool:
        pad = ROVER_COLLISION_RADIUS_M if radius is None else float(radius)
        return any(
            point_inside_rect(pose["x"], pose["y"], rect, pad)
            for rect in self.active_obstacles
        )

    def is_path_blocked(self) -> bool:
        """True only when the remaining path hits lethal occupancy.

        Footprint checks against the geometric rover body false-trigger on paths
        planned with slightly thinner inflation and caused replan storms (hundreds
        of replans per goal) that left the rover stuck spinning.
        """
        if not self.path:
            return False
        grid = self.planning_grid()
        lookahead = min(len(self.path), self.path_cursor + 18)
        for index in range(self.path_cursor, lookahead):
            point = self.path[index]
            x = int(clamp(point["x"] / GRID_RESOLUTION_M, 0, self.width_cells - 1))
            y = int(clamp(point["y"] / GRID_RESOLUTION_M, 0, self.height_cells - 1))
            if grid[y * self.width_cells + x] >= LETHAL:
                return True
        return False

    def _fine_dock_command(self, est: dict, xy_err: float, yaw_err: float) -> dict:
        """Close body-frame XY + yaw via turn-drive-turn lateral combos."""
        del xy_err, yaw_err
        if not self.goal:
            return {"linear": 0.0, "angular": 0.0}
        tyaw = self.goal_yaw
        if tyaw is None:
            tyaw = math.atan2(self.goal["y"] - est["y"], self.goal["x"] - est["x"])
        state = self._gap_close_state or GapCloseState()
        step = next_gap_close_step(
            est["x"],
            est["y"],
            est["yaw"],
            self.goal["x"],
            self.goal["y"],
            tyaw,
            state,
            cfg=GapCloseConfig(
                xy_tol_m=FINE_DOCK_XY_M,
                yaw_tol_rad=FINE_DOCK_YAW_RAD,
                skid_tol_m=0.06,
            ),
        )
        self._gap_close_state = step.state
        if step.done:
            return {"linear": 0.0, "angular": 0.0}
        linear, angular = keys_to_twist(step.keys)
        return {"linear": linear, "angular": angular}

    def autopilot_command(self) -> dict:
        """Discrete WASD-style cmds (same feel as teleop A/D + W), not slow vectors.

        Controller uses lidar pose estimate (what Nav2 would see), not truth.
        Probes a short forward step so we rotate clear instead of ramming walls.
        """
        if not self.autopilot or not self.path or not self.goal:
            return {"linear": 0.0, "angular": 0.0}
        if self.elapsed_sec < getattr(self, "_nav_recovery_until", 0.0):
            self.status = "navigating · recovering"
            return {"linear": -0.40, "angular": getattr(self, "_nav_recovery_wz", 1.0)}
        est = self.estimated_pose
        footprint = NAV_COLLISION_RADIUS_M
        while (
            self.path_cursor < len(self.path) - 1
            and distance(est, self.path[self.path_cursor]) < 0.30
        ):
            self.path_cursor += 1
        while (
            self.path_cursor < len(self.path) - 1
            and self.is_pose_collision(
                {
                    "x": self.path[self.path_cursor]["x"],
                    "y": self.path[self.path_cursor]["y"],
                    "yaw": 0.0,
                },
                radius=footprint,
            )
        ):
            self.path_cursor += 1
        # Short carrot + line-of-sight: a long lookahead cuts corners through
        # walls on the saved map and triggered endless reverse/spin recovery.
        look = self.path_cursor
        traveled = 0.0
        while look < len(self.path) - 1 and traveled < 0.45:
            traveled += distance(self.path[look], self.path[look + 1])
            look += 1

        def los_blocked(a: dict, b: dict) -> bool:
            steps = max(2, int(distance(a, b) / 0.08))
            for i in range(1, steps + 1):
                t = i / steps
                sample = {
                    "x": a["x"] + t * (b["x"] - a["x"]),
                    "y": a["y"] + t * (b["y"] - a["y"]),
                    "yaw": 0.0,
                }
                if self.is_pose_collision(sample, radius=footprint):
                    return True
            return False

        while look > self.path_cursor and los_blocked(est, self.path[look]):
            look -= 1
        target = self.path[look] if look < len(self.path) else self.goal
        goal_dist = distance(est, self.goal)
        if goal_dist < 0.70 and not los_blocked(est, self.goal):
            target = self.goal
        # Progress = moved in space or shortened remaining path. Do not use goal
        # distance (detours grow it) or path length alone (align turns stall it).
        remaining = distance(est, self.path[self.path_cursor]) + _path_length(
            self.path[self.path_cursor :]
        )
        last_pose = getattr(self, "_nav_last_progress_pose", None) or est
        moved = distance(est, last_pose)
        if remaining + 0.08 < getattr(self, "_nav_best_goal_dist", math.inf) or moved > 0.15:
            if remaining + 0.08 < getattr(self, "_nav_best_goal_dist", math.inf):
                self._nav_best_goal_dist = remaining
            self._nav_last_progress_pose = dict(est)
            self._nav_progress_at = self.elapsed_sec
            self._nav_fail_streak = 0
        elif (
            self.elapsed_sec - getattr(self, "_nav_progress_at", 0.0) > 8.0
            and getattr(self, "_nav_fail_streak", 0) < 3
        ):
            # Truly wedged: no translation for 8s. Skip a short path stub + replan
            # once — do not thrash every tick (that froze heading forever).
            self._nav_progress_at = self.elapsed_sec
            self._nav_fail_streak = getattr(self, "_nav_fail_streak", 0) + 1
            self._nav_recovery_until = 0.0
            while (
                self.path_cursor < len(self.path) - 1
                and distance(est, self.path[self.path_cursor]) < 0.45
            ):
                self.path_cursor += 1
            self.plan_to_goal(is_replan=True)
            self._nav_best_goal_dist = math.inf
            self.status = "navigating · unstuck replan"
        target_yaw = math.atan2(target["y"] - est["y"], target["x"] - est["x"])
        error = wrap_angle(target_yaw - est["yaw"])
        if self.fine_docking and self.goal:
            target_heading = self.goal_yaw
            if target_heading is None:
                target_heading = math.atan2(
                    self.goal["y"] - est["y"], self.goal["x"] - est["x"]
                )
            yaw_err = wrap_angle(target_heading - est["yaw"])
            if goal_dist <= FINE_DOCK_XY_M and abs(yaw_err) <= FINE_DOCK_YAW_RAD:
                self.autopilot = False
                if not self.exploring:
                    self.nav_complete = True
                    self.goal_reachable = True
                    self.status = "nav complete · docked"
                return {"linear": 0.0, "angular": 0.0}
            if self._fine_dock_started is None and goal_dist <= FINE_DOCK_COARSE_M:
                self._fine_dock_started = self.elapsed_sec
                self.status = "navigating · fine docking"
            if self._fine_dock_started is not None:
                if self.elapsed_sec - self._fine_dock_started > FINE_DOCK_TIMEOUT_SEC:
                    self.autopilot = False
                    if not self.exploring:
                        self.nav_complete = True
                        self.status = "nav complete · dock timeout"
                    return {"linear": 0.0, "angular": 0.0}
                return self._fine_dock_command(est, goal_dist, yaw_err)
        elif goal_dist < 0.50:
            self.autopilot = False
            if not self.exploring:
                self.nav_complete = True
                self.goal_reachable = True
                self.status = "nav complete · arrived"
            return {"linear": 0.0, "angular": 0.0}

        if abs(error) > 0.35:
            # Rotate in place until roughly aimed (was 0.70 / ~40° — started
            # driving too early and cut corners). Continuous A/D at ~7.5°/frame.
            return {"linear": 0.0, "angular": math.copysign(1.25, error)}

        def forward_blocked(linear: float, angular: float, horizon: float = 0.20) -> bool:
            keys = cmd_vel_to_keys(linear, angular)
            # Keep reverse if we asked for it — probe only cares about W arcs.
            v_l, v_r = keys_to_tracks(keys)
            probe = integrate_tank(self.pose, v_l, v_r, horizon)
            return self.is_pose_collision(probe, radius=footprint)

        def start_backup(sign: float) -> dict:
            # Trigger the dedicated reverse+turn recovery (keeps S with A/D).
            # Do not extend an already-active timer — that wedged reverse forever.
            if self.elapsed_sec >= getattr(self, "_nav_recovery_until", 0.0):
                self._nav_recovery_until = self.elapsed_sec + 0.35
            self._nav_recovery_wz = float(sign)
            self.status = "navigating · backing up"
            return {"linear": -0.40, "angular": sign}

        def clear_or_recover(cmd: dict, *, backup_sign: float) -> dict:
            """Prefer forward arcs over in-place pivots when the nose is blocked.

            A pure pivot always 'succeeds' the collision probe (xy unchanged) and
            used to trap the rover spinning forever beside a wall it could arc past.
            """
            if not forward_blocked(cmd["linear"], cmd["angular"]):
                return cmd
            # Pure forward still open — path curves around an obstacle; crawl.
            if cmd["linear"] > 0.05 and not forward_blocked(cmd["linear"], 0.0):
                return {"linear": min(0.40, cmd["linear"]), "angular": 0.0}
            # Mild forward arcs (keep W held) — these clear corners pivots cannot.
            signs = []
            if abs(error) > 1e-3:
                signs.append(math.copysign(1.0, error))
                signs.append(-math.copysign(1.0, error))
            else:
                signs.extend((1.0, -1.0))
            for sign in signs:
                arc = {
                    "linear": max(0.28, min(0.45, cmd["linear"] if cmd["linear"] > 0 else 0.35)),
                    "angular": sign * 0.42,
                }
                if not forward_blocked(arc["linear"], arc["angular"]):
                    return arc
            # Heading meaningfully wrong — rotate in place toward the carrot.
            if abs(error) > 0.15:
                return {"linear": 0.0, "angular": math.copysign(1.15, error)}
            for sign in signs:
                nudge = {"linear": 0.22, "angular": sign * 0.55}
                if not forward_blocked(nudge["linear"], nudge["angular"]):
                    return nudge
            return start_backup(backup_sign)

        if abs(error) > 0.25:
            cmd = {"linear": 0.30, "angular": math.copysign(0.45, error)}
            return clear_or_recover(
                cmd, backup_sign=(-1.0 if error >= 0 else 1.0)
            )
        if abs(error) > 0.10:
            cmd = {"linear": 0.50, "angular": math.copysign(0.32, error)}
            return clear_or_recover(
                cmd, backup_sign=(-1.0 if error >= 0 else 1.0)
            )
        cmd = {"linear": 0.55, "angular": 0.0}
        return clear_or_recover(cmd, backup_sign=1.0)

    def step(self, dt_sec: float = 1 / 30) -> "SlamNavSimulation":
        dt = clamp(dt_sec, 0.001, 0.2) * self.speed_multiplier
        self.elapsed_sec += dt
        self._scan_accumulator += dt
        self._replan_accumulator += dt

        if self.autopilot and self._replan_accumulator >= 1.0:
            self._replan_accumulator = 0.0
            if self.is_path_blocked():
                if self.exploring:
                    # Prefer another frontier over spinning in place.
                    if not self._plan_explore_to(self.goal["x"], self.goal["y"]):
                        self._handle_explore_blocked()
                else:
                    self.plan_to_goal(is_replan=True)

        if self.exploring:
            intent = self.explore_command()
        elif self.autopilot:
            intent = self.autopilot_command()
        else:
            intent = self.manual

        vx = float(intent.get("linear", 0.0))
        wz = float(intent.get("angular", 0.0))
        # Wall recovery must keep reverse+turn together. cmd_vel_to_keys strips S
        # whenever A/D is held (real teleop has no SA/SD), which left the rover
        # spinning in place while wedged.
        if (
            self.autopilot
            and self.elapsed_sec < getattr(self, "_nav_recovery_until", 0.0)
        ):
            turn = "a" if getattr(self, "_nav_recovery_wz", 1.0) >= 0 else "d"
            keys = ["s", turn]
            v_left, v_right = keys_to_tracks(keys)
            linear, angular = tracks_to_twist(v_left, v_right)
            self._drive.keys = list(keys)
            self._drive.desired_keys = list(keys)
            self._drive.tracks = (v_left, v_right)
            self._drive.body_cmd = {"linear": linear, "angular": angular}
            self._drive.phase = "recover"
            self._drive.turn_pulse_phase = "idle"
            command = self._drive.body_cmd
            next_pose = integrate_tank(self.pose, v_left, v_right, dt)
            soft = NAV_COLLISION_RADIUS_M
            if self.is_pose_collision(next_pose, radius=soft):
                # Try the opposite turn if this reverse arc is blocked.
                turn = "d" if turn == "a" else "a"
                keys = ["s", turn]
                v_left, v_right = keys_to_tracks(keys)
                next_pose = integrate_tank(self.pose, v_left, v_right, dt)
                if self.is_pose_collision(next_pose, radius=soft):
                    # Pure reverse as last resort.
                    keys = ["s"]
                    v_left, v_right = keys_to_tracks(keys)
                    next_pose = integrate_tank(self.pose, v_left, v_right, dt)
                if self.is_pose_collision(next_pose, radius=soft):
                    # Reverse fully blocked — pivot in place instead of freezing.
                    turn = "a" if getattr(self, "_nav_recovery_wz", 1.0) >= 0 else "d"
                    keys = [turn]
                    v_left, v_right = keys_to_tracks(keys)
                    next_pose = integrate_tank(self.pose, v_left, v_right, dt)
                    self._nav_recovery_until = min(
                        self._nav_recovery_until, self.elapsed_sec + 0.15
                    )
                linear, angular = tracks_to_twist(v_left, v_right)
                self._drive.keys = list(keys)
                self._drive.tracks = (v_left, v_right)
                self._drive.body_cmd = {"linear": linear, "angular": angular}
                command = self._drive.body_cmd
            if not self.is_pose_collision(next_pose, radius=soft):
                self.distance_m += math.hypot(
                    next_pose["x"] - self.pose["x"], next_pose["y"] - self.pose["y"]
                )
                self.pose = next_pose
                if not self.noise_enabled:
                    self.estimated_pose = dict(self.pose)
                else:
                    self.estimated_pose = integrate_tank(
                        self.estimated_pose, v_left, v_right, dt
                    )
                self._nav_stuck_count = 0
                if self.goal and distance(self.pose, self.goal) < 0.55:
                    self._nav_recovery_until = 0.0
            else:
                # Nothing moves — abort recovery so the controller can replan.
                self.collisions += 1
                self._nav_recovery_until = 0.0
                self._nav_fail_streak = getattr(self, "_nav_fail_streak", 0) + 1
                if self.path_cursor < len(self.path) - 1:
                    self.path_cursor += 1
                if self._nav_fail_streak >= 2:
                    self.plan_to_goal(is_replan=True)
            if self._scan_accumulator >= LIDAR_SCAN_PERIOD_SEC:
                self._scan_accumulator = 0.0
                self.scan()
            return self
        if self.exploring:
            # Continuous tank tracks while mapping — pulsed A/D made 360° spins crawl.
            keys = cmd_vel_to_keys(vx, wz)
            v_left, v_right = keys_to_tracks(keys)
            linear, angular = tracks_to_twist(v_left, v_right)
            self._drive.keys = list(keys)
            self._drive.desired_keys = list(keys)
            self._drive.tracks = (v_left, v_right)
            self._drive.body_cmd = {"linear": linear, "angular": angular}
            self._drive.phase = "explore"
            command = self._drive.body_cmd
        elif abs(vx) < 0.05 and abs(wz) >= 0.55:
            # Pure in-place align: continuous A/D. Finer A/D pulses are used for
            # arc path following; continuous here stops within ~1 frame (~7.5°).
            keys = cmd_vel_to_keys(vx, wz)
            v_left, v_right = keys_to_tracks(keys)
            linear, angular = tracks_to_twist(v_left, v_right)
            self._drive.keys = list(keys)
            self._drive.desired_keys = list(keys)
            self._drive.tracks = (v_left, v_right)
            self._drive.body_cmd = {"linear": linear, "angular": angular}
            self._drive.phase = "align"
            self._drive.turn_pulse_phase = "idle"
            command = self._drive.body_cmd
        else:
            # Drive / teleop: cmd_vel → WASD → pulsed A/D (~11° pure / ~9° arc).
            self._drive = apply_nav_drive(
                self._drive,
                vx=vx,
                wz=wz,
                now=self.elapsed_sec,
            )
            v_left, v_right = self._drive.tracks
            command = self._drive.body_cmd

        next_pose = integrate_tank(self.pose, v_left, v_right, dt)

        # Autopilot uses in-plane half-width; circumscribed corners false-trigger
        # in saved-map corridors and caused endless recover/spin loops.
        check_radius = None
        if self.autopilot and not self.exploring:
            check_radius = NAV_COLLISION_RADIUS_M
        elif self._drive.phase in ("align", "recover"):
            check_radius = NAV_COLLISION_RADIUS_M
        if self.is_pose_collision(next_pose, radius=check_radius):
            if abs(command["linear"]) > 0.01 or abs(v_left) > 0.01 or abs(v_right) > 0.01:
                self.collisions += 1
            if self.exploring:
                self._handle_explore_blocked()
            elif self.autopilot:
                # Let an active recovery finish — retriggering wedged the rover in
                # an endless reverse/spin loop next to walls.
                if self.elapsed_sec < getattr(self, "_nav_recovery_until", 0.0):
                    pass
                else:
                    self._nav_stuck_count = getattr(self, "_nav_stuck_count", 0) + 1
                    if self._nav_stuck_count >= 12:
                        self._nav_recovery_until = self.elapsed_sec + 0.35
                        if self.path and self.path_cursor < len(self.path):
                            tgt = self.path[self.path_cursor]
                            desire = math.atan2(
                                tgt["y"] - self.pose["y"], tgt["x"] - self.pose["x"]
                            )
                            err = wrap_angle(desire - self.pose["yaw"])
                            self._nav_recovery_wz = -1.0 if err >= 0 else 1.0
                        else:
                            self._nav_recovery_wz = 1.0
                        self._nav_stuck_count = 0
                        self.status = "blocked · recovering"
                        self.plan_to_goal(is_replan=True)
                    elif self._nav_stuck_count in (3, 6):
                        self.plan_to_goal(is_replan=True)
        else:
            self._nav_stuck_count = 0
            moved = math.hypot(
                next_pose["x"] - self.pose["x"], next_pose["y"] - self.pose["y"]
            )
            self.distance_m += moved
            self.pose = next_pose
            # Short-horizon pose extrapolator (not wheel-odometry SLAM):
            # advance the estimate with the same tank command between lidar
            # updates so (1) yellow tracks green in the UI and (2) scan-match
            # stays inside its search window at high speed_multiplier.
            if self.mode == "mapping" and not self.noise_enabled:
                self.estimated_pose = dict(self.pose)
            elif not self.noise_enabled and self.autopilot:
                # Noise-free nav: keep estimate glued to truth so probes and
                # path following share one pose. Idle localization (kidnap
                # tests) leaves the estimate free for scan-match recovery.
                self.estimated_pose = dict(self.pose)
            else:
                self.estimated_pose = integrate_tank(
                    self.estimated_pose, v_left, v_right, dt
                )

        if self.exploring:
            self._update_explore(dt, command)

        if self._scan_accumulator >= LIDAR_SCAN_PERIOD_SEC:
            self._scan_accumulator = 0.0
            self.scan()
        return self

    def emergency_stop(self) -> None:
        if self.exploring:
            self.stop_auto_map(freeze=False)
        self.autopilot = False
        self.manual = {"linear": 0.0, "angular": 0.0}
        self._drive = NavDriveState()
        self.status = "stopped"

    def metrics(self) -> dict:
        known = sum(1 for value in self.slam_grid if value != UNKNOWN)
        occupied = sum(1 for value in self.slam_grid if value >= LETHAL)
        # map_integrity is expensive on large saved maps — refresh a few times/sec.
        cache = getattr(self, "_integrity_cache", None)
        cache_t = getattr(self, "_integrity_cache_t", -1e9)
        if cache is None or (self.elapsed_sec - cache_t) > 0.5:
            cache = self.map_integrity()
            self._integrity_cache = cache
            self._integrity_cache_t = self.elapsed_sec
        integrity = cache
        return {
            "known_percent": (known / len(self.slam_grid)) * 100.0,
            "occupied_cells": occupied,
            "localization_error_m": distance(self.pose, self.estimated_pose),
            "route_length_m": self.last_plan_length_m,
            "distance_m": self.distance_m,
            "collisions": self.collisions,
            "replans": self.replans,
            "scans": self.scan_count,
            "frontiers": integrity["frontiers"],
            "occupied_iou": integrity["occupied_iou"],
            "occupied_recall": integrity["occupied_recall"],
            "free_recall": integrity["free_recall"],
            "exploring": self.exploring,
            "explore_spins": self._explore_spins,
        }

    def snapshot(self) -> dict:
        occupied: list[int] = []
        for index, value in enumerate(self.slam_grid):
            if value >= LETHAL:
                occupied.extend(
                    [index % self.width_cells, index // self.width_cells]
                )
        active = self.active_obstacles
        pose = self.pose
        cull = len(active) > 180
        return {
            "scenario": {
                "id": self.scenario.id,
                "label": self.scenario.label,
                "description": self.scenario.description,
                "width": self.scenario.width,
                "height": self.scenario.height,
            },
            "mode": self.mode,
            "status": self.status,
            "exploring": self.exploring,
            "goal_reachable": self.goal_reachable,
            "nav_complete": self.nav_complete,
            "pose": dict(self.pose),
            "estimated_pose": dict(self.estimated_pose),
            "goal": dict(self.goal) if self.goal else None,
            "path": [dict(point) for point in self.path],
            "obstacles": [
                {
                    "id": item.id,
                    "x": item.x,
                    "y": item.y,
                    "width": item.width,
                    "height": item.height,
                    "kind": item.kind,
                    "label": item.label or item.id,
                }
                for item in active
                if (
                    not cull
                    or item.kind == "dynamic"
                    or (
                        item.x < pose["x"] + 8.0
                        and item.x + item.width > pose["x"] - 8.0
                        and item.y < pose["y"] + 8.0
                        and item.y + item.height > pose["y"] - 8.0
                    )
                )
            ],
            "props": [
                {
                    "id": item.id,
                    "x": item.x,
                    "y": item.y,
                    "width": item.width,
                    "height": item.height,
                    "label": item.label or item.id,
                }
                for item in self.obstacles
                if item.kind == "dynamic"
            ],
            "lidar": [
                {
                    "angle": ray["angle"],
                    "relative_angle": ray["relative_angle"],
                    "distance": ray["distance"],
                    "hit": ray["hit"],
                    "blind": bool(ray.get("blind")),
                    "invalid": bool(ray.get("invalid")),
                }
                # Downsample for the GUI (~225 beams @ 1.6°); engine keeps full 450 @ 0.8°.
                for ray in self.lidar[::2]
            ],
            "lidar_spec": {
                "model": LIDAR_MODEL,
                "fov_deg": LIDAR_FOV_DEG,
                "display_arc_deg": LIDAR_DISPLAY_ARC_DEG,
                "blind_center_body_deg": LIDAR_BLIND_CENTER_BODY_DEG,
                "blind_width_deg": 360.0 - LIDAR_DISPLAY_ARC_DEG,
                "angular_res_deg": LIDAR_ANGULAR_RES_DEG,
                "ray_count": LIDAR_RAY_COUNT,
                "scan_hz": LIDAR_SCAN_HZ,
                "measure_hz": LIDAR_MEASURE_HZ,
                "min_range_m": LIDAR_MIN_RANGE_M,
                "max_range_m": LIDAR_RANGE_M,
                "accuracy_m": LIDAR_ACCURACY_M,
                "noise_enabled": self.noise_enabled,
                "noise_std_m": LIDAR_NOISE_STD_M,
            },
            "occupied": occupied,
            "resolution": GRID_RESOLUTION_M,
            "width_cells": self.width_cells,
            "height_cells": self.height_cells,
            "dynamic_obstacle_enabled": self.dynamic_obstacle_enabled,
            "speed_multiplier": self.speed_multiplier,
            "autopilot": self.autopilot,
            "drive": {
                "phase": self._drive.phase,
                "keys": list(self._drive.keys),
                "desired_keys": list(self._drive.desired_keys),
                "tracks": {
                    "left": round(self._drive.tracks[0], 3),
                    "right": round(self._drive.tracks[1], 3),
                },
                "body": dict(self._drive.body_cmd),
                "turn_pulse": self._drive.turn_pulse_phase,
            },
            "metrics": self.metrics(),
            "scenarios": [
                {"id": item.id, "label": item.label}
                for item in SCENARIOS.values()
            ],
        }


def run_regressions() -> dict:
    results: list[dict] = []

    def add(name: str, passed: bool, detail: str) -> None:
        results.append({"name": name, "pass": passed, "detail": detail})

    wide = SlamNavSimulation("wide_vs_narrow", noise_enabled=False)
    wide.reveal_map()
    wide.freeze_map()
    wide.set_default_goal()
    crossings = [point for point in wide.path if abs(point["x"] - 6.85) < 0.13]
    uses_wide = any(7.0 < point["y"] < 9.0 for point in crossings)
    uses_narrow = any(4.7 < point["y"] < 5.3 for point in crossings)
    add(
        "planner prefers wide opening",
        uses_wide and not uses_narrow,
        (
            "crossing y=" + ",".join(f"{point['y']:.2f}" for point in crossings)
            if crossings
            else wide.last_plan_reason
        ),
    )

    frozen_before = list(wide.slam_grid)
    for _ in range(30):
        wide.step(1 / 30)
    add(
        "frozen SLAM map is immutable",
        frozen_before == wide.slam_grid,
        f"{wide.scan_count} live scans",
    )

    prop = SlamNavSimulation("wide_vs_narrow", noise_enabled=False)
    prop.reveal_map()
    prop.freeze_map()
    prop.set_dynamic_obstacle(True)
    prop.set_default_goal()
    original = [(point["x"], point["y"]) for point in prop.path]
    mid = prop.path[len(prop.path) // 2]
    # Offset so the preferred opening is obstructed but an alternate lane remains.
    prop.move_prop("crate", mid["x"], mid["y"] - 0.55)
    add(
        "movable prop triggers a valid alternate plan",
        bool(prop.path)
        and prop.replans > 0
        and original
        != [(point["x"], point["y"]) for point in prop.path],
        f"{len(prop.path)} points · {prop.replans} replans",
    )

    mapping = SlamNavSimulation("open_lab", noise_enabled=False)
    for _ in range(12):
        mapping.set_manual(0.0, 1.2)
        for _tick in range(14):
            mapping.step(1 / 30)
    mapping.set_manual(0.0, 0.0)
    map_metrics = mapping.metrics()
    add(
        "synthetic lidar builds occupied structure",
        map_metrics["occupied_cells"] > 25 and map_metrics["known_percent"] > 5,
        (
            f"{map_metrics['occupied_cells']} occupied · "
            f"{map_metrics['known_percent']:.1f}% known"
        ),
    )

    blind_sim = SlamNavSimulation("open_lab", noise_enabled=False)
    blind_count = sum(1 for ray in blind_sim.lidar if ray.get("blind"))
    expected_blind = int(round((360.0 - LIDAR_DISPLAY_ARC_DEG) / LIDAR_ANGULAR_RES_DEG))
    add(
        "rear blind matches 90° body occlusion",
        abs(blind_count - expected_blind) <= 1,
        f"{blind_count} blind beams · expected ~{expected_blind}",
    )

    nav = SlamNavSimulation("wide_vs_narrow", noise_enabled=False)
    nav.reveal_map()
    nav.speed_multiplier = 2.5
    started = nav.set_default_goal()
    for _ in range(1800):
        nav.step(1 / 30)
        if nav.nav_complete or "arrived" in nav.status:
            break
    add(
        "goal starts lidar-nav and arrives",
        started
        and nav.mode == "localization"
        and nav.nav_complete
        and distance(nav.pose, nav.goal) < 0.6,
        f"status={nav.status} · dist={distance(nav.pose, nav.goal):.2f}m",
    )

    holes = SlamNavSimulation("open_lab", noise_enabled=False)
    # Synthetic FREE ring around UNKNOWN — fill should close the floor hole.
    width = holes.width_cells
    cx, cy = 35, 35
    for dy in range(-3, 4):
        for dx in range(-3, 4):
            holes.slam_grid[(cy + dy) * width + (cx + dx)] = FREE
    for dy in range(-1, 2):
        for dx in range(-1, 2):
            holes.slam_grid[(cy + dy) * width + (cx + dx)] = UNKNOWN
    filled = holes.fill_map_holes(max_size=20)
    pocket_unknown = sum(
        1
        for dy in range(-1, 2)
        for dx in range(-1, 2)
        if holes.slam_grid[(cy + dy) * width + (cx + dx)] == UNKNOWN
    )
    add(
        "enclosed unknown pockets are filled",
        filled > 0 and pocket_unknown == 0,
        f"filled={filled} · remaining_unknown={pocket_unknown}",
    )

    protect = SlamNavSimulation("open_lab", noise_enabled=False)
    protect.mode = "mapping"
    wall_x = int(5.4 / GRID_RESOLUTION_M)
    wall_y = int(3.0 / GRID_RESOLUTION_M)
    idx = wall_y * protect.width_cells + wall_x
    # Strong wall survives one miss; weak false wall is cleared by free rays.
    protect.slam_grid[idx] = OCCUPIED
    protect.evidence[idx] = LOG_ODDS_CLAMP
    protect.pose = {"x": 2.0, "y": 3.0, "yaw": 0.0}
    protect.estimated_pose = {"x": 2.0, "y": 3.0, "yaw": 0.0}
    miss = {
        "relative_angle": 0.0,
        "distance": 8.0,
        "hit": False,
        "invalid": False,
        "blind": False,
    }
    protect.integrate_scan([miss])
    strong_ok = protect.slam_grid[idx] == OCCUPIED
    protect.slam_grid[idx] = OCCUPIED
    protect.evidence[idx] = 0.5
    for _ in range(3):
        protect.integrate_scan([miss])
    weak_cleared = protect.slam_grid[idx] != OCCUPIED
    add(
        "free rays refine walls without erasing strong hits",
        strong_ok and weak_cleared,
        f"strong_ok={strong_ok} · weak_cleared={weak_cleared}",
    )

    return {"pass": all(item["pass"] for item in results), "results": results}
