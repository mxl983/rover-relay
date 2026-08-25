#!/usr/bin/env python3
"""Frozen baseline (persistent) + ephemeral working copy for localization UI."""

from __future__ import annotations

import json
import math
import os
import threading
import time
from typing import Any

from persistent_map import (
    FREE_VALUE,
    MAX_RANGE,
    MIN_RANGE,
    OCCUPIED_THRESHOLD,
    OCCUPIED_VALUE,
    _clamp_occ,
    is_lidar_blind_bearing,
)

BASELINE_PATH = os.environ.get(
    "SLAM_BASELINE_GRID_PATH", "/app/lidar/maps/baseline_grid.json"
)
WORKING_WINDOW_M = float(os.environ.get("SLAM_WORKING_WINDOW_M", "8.0"))
MAX_SCAN_HITS = int(os.environ.get("SLAM_MAX_SCAN_HITS", "360"))
# Filtered scans live in base_laser_slam = base_link yaw −90° (entrypoint static TF).
LASER_YAW_OFFSET_RAD = float(
    os.environ.get("SLAM_LASER_YAW_OFFSET_RAD", str(-math.pi / 2.0))
)


def _cell_is_occupied(value: int) -> bool:
    return value >= OCCUPIED_THRESHOLD


def scan_hits_world(
    px: float,
    py: float,
    yaw: float,
    ranges: list[float],
    angle_min: float,
    angle_increment: float,
    range_min: float | None = None,
    range_max: float | None = None,
) -> list[dict[str, float]]:
    """Map-frame lidar hit points for the green 'currently scanning' overlay.

    ``yaw`` is base_link in map; scan bearings are in the corrected laser frame
    (base_link − 90°), matching Cartographer's scan TF.
    """
    rmin = range_min if range_min is not None else MIN_RANGE
    rmax = range_max if range_max is not None else MAX_RANGE
    hits: list[dict[str, float]] = []
    for i, distance in enumerate(ranges):
        beam = angle_min + i * angle_increment
        if is_lidar_blind_bearing(beam):
            continue
        if not math.isfinite(distance) or distance < rmin or distance >= rmax:
            continue
        world_angle = yaw + beam + LASER_YAW_OFFSET_RAD
        hits.append(
            {
                "x": round(px + distance * math.cos(world_angle), 3),
                "y": round(py + distance * math.sin(world_angle), 3),
            }
        )
    if MAX_SCAN_HITS > 0 and len(hits) > MAX_SCAN_HITS:
        step = len(hits) / MAX_SCAN_HITS
        hits = [hits[int(i * step)] for i in range(MAX_SCAN_HITS)]
    return hits


def scan_baseline_match_score(
    baseline: BaselineGrid,
    hits: list[dict[str, float]],
    *,
    search_cells: int = 2,
    min_hits: int = 15,
    occupied_threshold: int = OCCUPIED_THRESHOLD,
) -> float | None:
    """Fraction of lidar hits that land near a frozen-map occupied cell (0–1).

    Low scores mean the reported pose is likely wrong relative to the baseline.
    """
    if not hits or min_hits <= 0:
        return None
    matched = 0
    evaluated = 0
    radius = max(0, int(search_cells))
    with baseline.lock:
        if not baseline.loaded or baseline.width <= 0:
            return None
        for hit in hits:
            evaluated += 1
            cell = baseline.world_to_cell(float(hit["x"]), float(hit["y"]))
            if cell is None:
                continue
            ix, iy = cell
            found = False
            for dy in range(-radius, radius + 1):
                for dx in range(-radius, radius + 1):
                    if baseline.cell(ix + dx, iy + dy) >= occupied_threshold:
                        found = True
                        break
                if found:
                    break
            if found:
                matched += 1
    if evaluated < min_hits:
        return None
    return matched / evaluated


def pose_in_baseline_map(baseline: BaselineGrid, px: float, py: float) -> bool:
    with baseline.lock:
        if not baseline.loaded or baseline.width <= 0 or baseline.height <= 0:
            return True
        ox = baseline.origin_x
        oy = baseline.origin_y
        res = baseline.resolution
        map_max_x = ox + baseline.width * res
        map_max_y = oy + baseline.height * res
        return ox <= px <= map_max_x and oy <= py <= map_max_y


class BaselineGrid:
    """Persistent frozen-map occupancy snapshot (display + promote target)."""

    def __init__(self) -> None:
        self.lock = threading.RLock()
        self.resolution = 0.05
        self.origin_x = 0.0
        self.origin_y = 0.0
        self.width = 0
        self.height = 0
        self.data: list[int] = []
        self.frozen_at = 0.0
        self.loaded = False

    def load(self) -> bool:
        if not os.path.isfile(BASELINE_PATH):
            return False
        try:
            with open(BASELINE_PATH, encoding="utf-8") as handle:
                raw = json.load(handle)
            with self.lock:
                self.resolution = float(raw.get("resolution", 0.05))
                self.origin_x = float(raw["origin"]["x"])
                self.origin_y = float(raw["origin"]["y"])
                self.width = int(raw["width"])
                self.height = int(raw["height"])
                data = raw.get("data")
                if not isinstance(data, list) or len(data) != self.width * self.height:
                    return False
                self.data = [int(v) for v in data]
                self.frozen_at = float(raw.get("frozen_at", raw.get("updated_at", 0.0)))
                self.loaded = True
            return True
        except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError):
            return False

    def save(self) -> None:
        with self.lock:
            if not self.loaded or not self.data:
                return
            payload = {
                "version": 1,
                "resolution": self.resolution,
                "width": self.width,
                "height": self.height,
                "origin": {"x": self.origin_x, "y": self.origin_y},
                "frozen_at": self.frozen_at or time.time(),
                "updated_at": time.time(),
                "data": self.data,
            }
            directory = os.path.dirname(BASELINE_PATH)
            if directory:
                os.makedirs(directory, exist_ok=True)
            tmp = f"{BASELINE_PATH}.tmp"
            with open(tmp, "w", encoding="utf-8") as handle:
                json.dump(payload, handle, separators=(",", ":"))
            os.replace(tmp, BASELINE_PATH)

    def replace_from_occupancy_grid(
        self,
        resolution: float,
        origin_x: float,
        origin_y: float,
        width: int,
        height: int,
        data: list[int],
    ) -> None:
        with self.lock:
            if len(data) != width * height:
                return
            self.resolution = float(resolution)
            self.origin_x = float(origin_x)
            self.origin_y = float(origin_y)
            self.width = int(width)
            self.height = int(height)
            self.data = [int(v) for v in data]
            self.frozen_at = time.time()
            self.loaded = True

    def cell(self, ix: int, iy: int) -> int:
        if not (0 <= ix < self.width and 0 <= iy < self.height):
            return -1
        return self.data[iy * self.width + ix]

    def world_to_cell(self, x: float, y: float) -> tuple[int, int] | None:
        ix = int(math.floor((x - self.origin_x) / self.resolution))
        iy = int(math.floor((y - self.origin_y) / self.resolution))
        if 0 <= ix < self.width and 0 <= iy < self.height:
            return ix, iy
        return None


class WorkingCopy:
    """Ephemeral grid seeded from baseline; updated by live scans in a local window."""

    def __init__(self, baseline: BaselineGrid) -> None:
        self.baseline = baseline
        self.lock = threading.RLock()
        self.resolution = 0.05
        self.origin_x = 0.0
        self.origin_y = 0.0
        self.width = 0
        self.height = 0
        self.data: list[int] = []
        self.delta_count = 0
        self.update_count = 0

    def reset_from_baseline(self) -> None:
        with self.baseline.lock:
            if not self.baseline.loaded:
                with self.lock:
                    self.width = 0
                    self.height = 0
                    self.data = []
                    self.delta_count = 0
                return
            res = self.baseline.resolution
            ox = self.baseline.origin_x
            oy = self.baseline.origin_y
            width = self.baseline.width
            height = self.baseline.height
            data = list(self.baseline.data)
        with self.lock:
            self.resolution = res
            self.origin_x = ox
            self.origin_y = oy
            self.width = width
            self.height = height
            self.data = data
            self.delta_count = 0

    def adopt_baseline(self) -> None:
        """Promote: working state becomes the new baseline."""
        with self.lock, self.baseline.lock:
            if not self.data or self.width <= 0:
                return
            self.baseline.resolution = self.resolution
            self.baseline.origin_x = self.origin_x
            self.baseline.origin_y = self.origin_y
            self.baseline.width = self.width
            self.baseline.height = self.height
            self.baseline.data = list(self.data)
            self.baseline.frozen_at = time.time()
            self.baseline.loaded = True
            self.delta_count = 0

    def _set_cell(self, ix: int, iy: int, value: int) -> None:
        idx = iy * self.width + ix
        if self.data[idx] == value:
            return
        baseline_val = self.baseline.cell(ix, iy)
        was_delta = self.data[idx] != baseline_val
        will_delta = value != baseline_val
        self.data[idx] = value
        self.update_count += 1
        if was_delta and not will_delta:
            self.delta_count = max(0, self.delta_count - 1)
        elif not was_delta and will_delta:
            self.delta_count += 1

    def _trace_ray_cells(
        self, x0: float, y0: float, x1: float, y1: float
    ) -> list[tuple[int, int]]:
        dist = math.hypot(x1 - x0, y1 - y0)
        if dist < 1e-9:
            cell = self.baseline.world_to_cell(x0, y0)
            return [cell] if cell is not None else []
        step = self.resolution * 0.35
        n = max(1, int(math.ceil(dist / step)))
        seen: set[tuple[int, int]] = set()
        ordered: list[tuple[int, int]] = []
        for i in range(n + 1):
            t = i / n
            cell = self.baseline.world_to_cell(
                x0 + (x1 - x0) * t, y0 + (y1 - y0) * t
            )
            if cell is None or cell in seen:
                continue
            seen.add(cell)
            ordered.append(cell)
        return ordered

    def _in_local_window(self, ix: int, iy: int, px: float, py: float) -> bool:
        wx = self.origin_x + (ix + 0.5) * self.resolution
        wy = self.origin_y + (iy + 0.5) * self.resolution
        return math.hypot(wx - px, wy - py) <= WORKING_WINDOW_M

    def integrate_scan(
        self,
        px: float,
        py: float,
        yaw: float,
        ranges: list[float],
        angle_min: float,
        angle_increment: float,
        range_min: float | None = None,
        range_max: float | None = None,
    ) -> list[dict[str, float]]:
        """Update local cells from one scan; return map-frame hit points for UI."""
        rmin = range_min if range_min is not None else MIN_RANGE
        rmax = range_max if range_max is not None else MAX_RANGE
        hits: list[dict[str, float]] = []
        with self.lock:
            if not self.data or self.width <= 0:
                return hits
            for i, distance in enumerate(ranges):
                beam = angle_min + i * angle_increment
                if is_lidar_blind_bearing(beam):
                    continue
                world_angle = yaw + beam + LASER_YAW_OFFSET_RAD
                if not math.isfinite(distance):
                    continue
                if distance >= rmax:
                    end_x = px + rmax * math.cos(world_angle)
                    end_y = py + rmax * math.sin(world_angle)
                    for ix, iy in self._trace_ray_cells(px, py, end_x, end_y):
                        if self._in_local_window(ix, iy, px, py):
                            self._set_cell(ix, iy, FREE_VALUE)
                    continue
                if distance < rmin:
                    continue
                hit_x = px + distance * math.cos(world_angle)
                hit_y = py + distance * math.sin(world_angle)
                for ix, iy in self._trace_ray_cells(px, py, hit_x, hit_y):
                    if not self._in_local_window(ix, iy, px, py):
                        continue
                    wx = self.origin_x + (ix + 0.5) * self.resolution
                    wy = self.origin_y + (iy + 0.5) * self.resolution
                    if math.hypot(hit_x - wx, hit_y - wy) < self.resolution * 0.75:
                        continue
                    self._set_cell(ix, iy, FREE_VALUE)
                cell = self.baseline.world_to_cell(hit_x, hit_y)
                if cell is not None:
                    ix, iy = cell
                    if self._in_local_window(ix, iy, px, py):
                        self._set_cell(ix, iy, _clamp_occ(OCCUPIED_VALUE))
            return hits

    def overlay_cells(
        self,
        pose: dict[str, float],
        waypoints: list[dict[str, Any]] | None,
        window_m: float,
        max_cells: int,
    ) -> dict[str, Any]:
        """Cells where working copy differs from frozen baseline."""
        empty = {"occupied": [], "occupied_count": 0, "map_points": []}
        with self.lock, self.baseline.lock:
            if not self.data or self.delta_count <= 0:
                return {"added": dict(empty), "removed": dict(empty)}

            px = float(pose.get("x", 0.0))
            py = float(pose.get("y", 0.0))
            res = self.resolution
            ox, oy = self.origin_x, self.origin_y
            width, height = self.width, self.height
            data = self.data
            baseline = self.baseline.data

            centers: list[tuple[float, float]] = [(px, py)]
            halo_m = min(window_m, 4.0)
            for wp in waypoints or []:
                try:
                    centers.append((float(wp["x"]), float(wp["y"])))
                except (KeyError, TypeError, ValueError):
                    continue

            added: set[tuple[int, int]] = set()
            removed: set[tuple[int, int]] = set()
            for i, (cx, cy) in enumerate(centers):
                radius = window_m if i == 0 else halo_m
                ix0 = max(0, int((cx - radius - ox) / res) - 1)
                ix1 = min(width, int((cx + radius - ox) / res) + 2)
                iy0 = max(0, int((cy - radius - oy) / res) - 1)
                iy1 = min(height, int((cy + radius - oy) / res) + 2)
                for iy in range(iy0, iy1):
                    row = iy * width
                    for ix in range(ix0, ix1):
                        idx = row + ix
                        if data[idx] == baseline[idx]:
                            continue
                        was_occ = _cell_is_occupied(baseline[idx])
                        now_occ = _cell_is_occupied(data[idx])
                        if not was_occ and now_occ:
                            added.add((ix, iy))
                        elif was_occ and not now_occ:
                            removed.add((ix, iy))

            def pack(cells: set[tuple[int, int]]) -> dict[str, Any]:
                occupied: list[int] = []
                map_points: list[dict[str, float]] = []
                for ix, iy in cells:
                    occupied.extend((ix, iy))
                    map_points.append(
                        {
                            "x": round(ox + (ix + 0.5) * res, 3),
                            "y": round(oy + (iy + 0.5) * res, 3),
                        }
                    )
                if max_cells > 0 and len(occupied) // 2 > max_cells:
                    step = (len(occupied) // 2) / max_cells
                    slim_occ: list[int] = []
                    slim_pts: list[dict[str, float]] = []
                    for i in range(max_cells):
                        idx = int(i * step)
                        slim_occ.extend(occupied[idx * 2 : idx * 2 + 2])
                        if idx < len(map_points):
                            slim_pts.append(map_points[idx])
                    occupied = slim_occ
                    map_points = slim_pts
                return {
                    "occupied": occupied,
                    "occupied_count": len(occupied) // 2,
                    "map_points": map_points,
                }

            return {"added": pack(added), "removed": pack(removed)}
