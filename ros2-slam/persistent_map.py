#!/usr/bin/env python3
"""Persistent occupancy grid with ray hit/miss updates + waypoints."""

from __future__ import annotations

import json
import math
import os
import threading
import time
import uuid
from typing import Any

PERSIST_PATH = os.environ.get(
    "SLAM_PERSISTENT_MAP_PATH", "/app/lidar/maps/persistent_grid.json"
)
WAYPOINTS_PATH = os.environ.get(
    "SLAM_WAYPOINTS_PATH", "/app/lidar/maps/waypoints.json"
)
RESOLUTION = float(os.environ.get("SLAM_PERSIST_RESOLUTION", "0.05"))
# Visible FOV clears free→hit; blind sector is left untouched (matches lidarCoords.js).
OCCUPIED_VALUE = int(os.environ.get("SLAM_OCCUPIED_VALUE", "100"))
FREE_VALUE = int(os.environ.get("SLAM_FREE_VALUE", "0"))
OCCUPIED_THRESHOLD = int(os.environ.get("SLAM_OCCUPIED_THRESHOLD", "50"))
MAX_RANGE = float(os.environ.get("SLAM_FILTER_MAX_RANGE", "8.0"))
MIN_RANGE = float(os.environ.get("SLAM_FILTER_MIN_RANGE", "0.25"))
SAVE_INTERVAL_SEC = float(os.environ.get("SLAM_PERSIST_SAVE_SEC", "30"))
INITIAL_SIZE_M = float(os.environ.get("SLAM_PERSIST_INITIAL_M", "20"))
# Same as control-dashboard/src/utils/lidarCoords.js:
# LIDAR_MINIMAP_ARC_DEG=270, LIDAR_MINIMAP_REAR_CENTER_DEG=270 → 90° rear blind.
LIDAR_DISPLAY_ARC_DEG = float(os.environ.get("SLAM_LIDAR_DISPLAY_ARC_DEG", "270"))
LIDAR_BLIND_CENTER_DEG = float(os.environ.get("SLAM_LIDAR_BLIND_CENTER_DEG", "270"))
# Optional polar thin (off by default — keeps denser walls).
POLAR_BINS = int(os.environ.get("SLAM_POLAR_BINS", "720"))
POLAR_FILTER = os.environ.get("SLAM_POLAR_FILTER", "0").strip().lower() in (
    "1",
    "true",
    "yes",
    "on",
)


def _clamp_occ(value: int) -> int:
    return max(0, min(100, value))


def _normalize_deg(deg: float) -> float:
    return deg % 360.0


def is_lidar_blind_bearing(beam_angle_rad: float) -> bool:
    """True for the rear body-occlusion sector the minimap also hides."""
    if LIDAR_DISPLAY_ARC_DEG >= 360.0:
        return False
    half_hidden = (360.0 - LIDAR_DISPLAY_ARC_DEG) / 2.0
    deg = _normalize_deg(math.degrees(beam_angle_rad))
    delta = abs(deg - LIDAR_BLIND_CENTER_DEG)
    if delta > 180.0:
        delta = 360.0 - delta
    return delta < half_hidden

class PersistentOccupancyMap:
    """Occupancy grid that loads/saves to disk and updates via lidar raycasting."""

    def __init__(self) -> None:
        self.lock = threading.RLock()
        self.resolution = RESOLUTION
        half = INITIAL_SIZE_M / 2.0
        cells = max(4, int(INITIAL_SIZE_M / self.resolution))
        self.origin_x = -half
        self.origin_y = -half
        self.width = cells
        self.height = cells
        self.data = [-1] * (cells * cells)  # -1 unknown, 0..100 occupancy
        self.dirty = False
        self.last_save_at = 0.0
        self.update_count = 0
        self.loaded_from_disk = False
        self.load()

    def load(self) -> bool:
        if not os.path.isfile(PERSIST_PATH):
            return False
        try:
            with open(PERSIST_PATH, encoding="utf-8") as handle:
                raw = json.load(handle)
            with self.lock:
                self.resolution = float(raw.get("resolution", RESOLUTION))
                self.origin_x = float(raw["origin"]["x"])
                self.origin_y = float(raw["origin"]["y"])
                self.width = int(raw["width"])
                self.height = int(raw["height"])
                data = raw.get("data")
                if not isinstance(data, list) or len(data) != self.width * self.height:
                    return False
                self.data = [int(v) for v in data]
                self.dirty = False
                self.loaded_from_disk = True
                self.last_save_at = time.time()
            return True
        except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError):
            return False

    def save(self, force: bool = False) -> bool:
        with self.lock:
            if not force and not self.dirty:
                return False
            payload = {
                "version": 1,
                "resolution": self.resolution,
                "width": self.width,
                "height": self.height,
                "origin": {"x": self.origin_x, "y": self.origin_y},
                "updated_at": time.time(),
                "update_count": self.update_count,
                "data": self.data,
            }
            directory = os.path.dirname(PERSIST_PATH)
            if directory:
                os.makedirs(directory, exist_ok=True)
            tmp = f"{PERSIST_PATH}.tmp"
            with open(tmp, "w", encoding="utf-8") as handle:
                json.dump(payload, handle, separators=(",", ":"))
            os.replace(tmp, PERSIST_PATH)
            self.dirty = False
            self.last_save_at = time.time()
            return True

    def maybe_autosave(self) -> None:
        if time.time() - self.last_save_at >= SAVE_INTERVAL_SEC:
            self.save(force=False)

    def _ensure_world(self, x: float, y: float, margin_m: float = 2.0) -> None:
        """Grow the grid if a world point falls outside (keeps existing cells)."""
        res = self.resolution
        min_x = self.origin_x
        min_y = self.origin_y
        max_x = self.origin_x + self.width * res
        max_y = self.origin_y + self.height * res
        need_min_x = x - margin_m
        need_min_y = y - margin_m
        need_max_x = x + margin_m
        need_max_y = y + margin_m
        if need_min_x >= min_x and need_min_y >= min_y and need_max_x <= max_x and need_max_y <= max_y:
            return

        new_min_x = min(min_x, need_min_x)
        new_min_y = min(min_y, need_min_y)
        new_max_x = max(max_x, need_max_x)
        new_max_y = max(max_y, need_max_y)
        # Snap to resolution.
        new_min_x = math.floor(new_min_x / res) * res
        new_min_y = math.floor(new_min_y / res) * res
        new_w = int(math.ceil((new_max_x - new_min_x) / res))
        new_h = int(math.ceil((new_max_y - new_min_y) / res))
        new_data = [-1] * (new_w * new_h)
        ox_shift = int(round((self.origin_x - new_min_x) / res))
        oy_shift = int(round((self.origin_y - new_min_y) / res))
        for iy in range(self.height):
            for ix in range(self.width):
                nx = ix + ox_shift
                ny = iy + oy_shift
                if 0 <= nx < new_w and 0 <= ny < new_h:
                    new_data[ny * new_w + nx] = self.data[iy * self.width + ix]
        self.origin_x = new_min_x
        self.origin_y = new_min_y
        self.width = new_w
        self.height = new_h
        self.data = new_data
        self.dirty = True

    def world_to_cell(self, x: float, y: float) -> tuple[int, int] | None:
        ix = int(math.floor((x - self.origin_x) / self.resolution))
        iy = int(math.floor((y - self.origin_y) / self.resolution))
        if 0 <= ix < self.width and 0 <= iy < self.height:
            return ix, iy
        return None

    def _set_cell(self, ix: int, iy: int, value: int) -> None:
        idx = iy * self.width + ix
        if self.data[idx] != value:
            self.data[idx] = value
            self.dirty = True

    def _trace_ray_cells(
        self, x0: float, y0: float, x1: float, y1: float
    ) -> list[tuple[int, int]]:
        """Dense cell walk along a segment (supercover-ish)."""
        dist = math.hypot(x1 - x0, y1 - y0)
        if dist < 1e-9:
            cell = self.world_to_cell(x0, y0)
            return [cell] if cell is not None else []
        step = self.resolution * 0.35
        n = max(1, int(math.ceil(dist / step)))
        seen: set[tuple[int, int]] = set()
        ordered: list[tuple[int, int]] = []
        for i in range(n + 1):
            t = i / n
            cell = self.world_to_cell(x0 + (x1 - x0) * t, y0 + (y1 - y0) * t)
            if cell is None or cell in seen:
                continue
            seen.add(cell)
            ordered.append(cell)
        return ordered

    def _clear_free_segment(
        self, px: float, py: float, ex: float, ey: float, stop_short_m: float = 0.0
    ) -> None:
        """Mark cells along segment as free; optionally stop before the endpoint."""
        cells = self._trace_ray_cells(px, py, ex, ey)
        if not cells:
            return
        for ix, iy in cells:
            if stop_short_m > 0:
                wx = self.origin_x + (ix + 0.5) * self.resolution
                wy = self.origin_y + (iy + 0.5) * self.resolution
                if math.hypot(ex - wx, ey - wy) < stop_short_m:
                    continue
            self._set_cell(ix, iy, FREE_VALUE)

    def _mark_hit(self, px: float, py: float, angle: float, distance: float) -> None:
        hit_x = px + distance * math.cos(angle)
        hit_y = py + distance * math.sin(angle)
        self._ensure_world(hit_x, hit_y, margin_m=1.0)
        cell = self.world_to_cell(hit_x, hit_y)
        if cell is not None:
            self._set_cell(cell[0], cell[1], _clamp_occ(OCCUPIED_VALUE))

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
    ) -> None:
        rmin = range_min if range_min is not None else MIN_RANGE
        rmax = range_max if range_max is not None else MAX_RANGE
        with self.lock:
            self._ensure_world(px, py, margin_m=rmax + 1.0)
            for i, distance in enumerate(ranges):
                # Laser-frame bearing (matches lidarCoords / minimap), not map yaw.
                beam = angle_min + i * angle_increment
                if is_lidar_blind_bearing(beam):
                    # Rear blind: body occludes lidar — keep whatever is already mapped.
                    continue

                world_angle = yaw + beam
                if not math.isfinite(distance):
                    # Filtered / unknown sample — do not invent free space.
                    continue

                if distance >= rmax:
                    # True open reading in visible FOV: clear free out to max range.
                    end_x = px + rmax * math.cos(world_angle)
                    end_y = py + rmax * math.sin(world_angle)
                    self._ensure_world(end_x, end_y, margin_m=1.0)
                    self._clear_free_segment(px, py, end_x, end_y)
                    continue

                if distance < rmin:
                    continue

                # Valid hit: free only up to the obstacle; leave everything behind it.
                hit_x = px + distance * math.cos(world_angle)
                hit_y = py + distance * math.sin(world_angle)
                self._ensure_world(hit_x, hit_y, margin_m=1.0)
                self._clear_free_segment(
                    px, py, hit_x, hit_y, stop_short_m=self.resolution * 0.75
                )
                self._mark_hit(px, py, world_angle, float(distance))
            self.update_count += 1

    def seed_from_occupancy_grid(
        self,
        resolution: float,
        origin_x: float,
        origin_y: float,
        width: int,
        height: int,
        data: list[int],
    ) -> None:
        """Replace empty map with a Cartographer snapshot (first boot seed)."""
        with self.lock:
            if self.loaded_from_disk and any(v >= 0 for v in self.data):
                return
            self.replace_from_occupancy_grid(
                resolution, origin_x, origin_y, width, height, data
            )

    def replace_from_occupancy_grid(
        self,
        resolution: float,
        origin_x: float,
        origin_y: float,
        width: int,
        height: int,
        data: list[int],
    ) -> None:
        """Overwrite the stored grid with a Cartographer occupancy snapshot."""
        with self.lock:
            if len(data) != width * height:
                return
            self.resolution = float(resolution)
            self.origin_x = float(origin_x)
            self.origin_y = float(origin_y)
            self.width = int(width)
            self.height = int(height)
            self.data = [int(v) for v in data]
            self.dirty = True
            self.loaded_from_disk = True

    def snapshot_payload(
        self,
        pose: dict[str, float],
        hz: float,
        max_occupied: int,
        window_m: float,
    ) -> dict[str, Any]:
        with self.lock:
            px = float(pose.get("x", 0.0))
            py = float(pose.get("y", 0.0))
            yaw = float(pose.get("yaw", 0.0))
            res = self.resolution
            ox, oy = self.origin_x, self.origin_y
            width, height = self.width, self.height
            data = self.data

            map_max_x = ox + width * res
            map_max_y = oy + height * res
            pose_in_map = ox <= px <= map_max_x and oy <= py <= map_max_y
            view_x, view_y = px, py
            if not pose_in_map and width > 0 and height > 0:
                view_x = min(max(px, ox + res), map_max_x - res)
                view_y = min(max(py, oy + res), map_max_y - res)

            ix0 = max(0, int((view_x - window_m - ox) / res) - 1)
            ix1 = min(width, int((view_x + window_m - ox) / res) + 2)
            iy0 = max(0, int((view_y - window_m - oy) / res) - 1)
            iy1 = min(height, int((view_y + window_m - oy) / res) + 2)

            hits: list[tuple[int, int]] = []
            for iy in range(iy0, iy1):
                row = iy * width
                for ix in range(ix0, ix1):
                    if data[row + ix] >= OCCUPIED_THRESHOLD:
                        hits.append((ix, iy))

            if POLAR_FILTER and hits:
                bins = max(36, POLAR_BINS)
                best: dict[int, tuple[float, int, int]] = {}
                for ix, iy in hits:
                    wx = ox + (ix + 0.5) * res
                    wy = oy + (iy + 0.5) * res
                    dx = wx - view_x
                    dy = wy - view_y
                    r2 = dx * dx + dy * dy
                    if r2 < 1e-8:
                        continue
                    ang = math.atan2(dy, dx)
                    b = int((ang + math.pi) / (2.0 * math.pi) * bins) % bins
                    prev = best.get(b)
                    if prev is None or r2 < prev[0]:
                        best[b] = (r2, ix, iy)
                hits = [(ix, iy) for _, ix, iy in best.values()]

            occupied: list[int] = []
            map_points: list[dict[str, float]] = []
            for ix, iy in hits:
                occupied.extend((ix, iy))
                map_points.append(
                    {
                        "x": round(ox + (ix + 0.5) * res, 3),
                        "y": round(oy + (iy + 0.5) * res, 3),
                    }
                )

            if max_occupied > 0 and len(occupied) // 2 > max_occupied:
                step = (len(occupied) // 2) / max_occupied
                slim_occ: list[int] = []
                slim_pts: list[dict[str, float]] = []
                for i in range(max_occupied):
                    idx = int(i * step)
                    slim_occ.extend(occupied[idx * 2 : idx * 2 + 2])
                    if idx < len(map_points):
                        slim_pts.append(map_points[idx])
                occupied = slim_occ
                map_points = slim_pts

            return {
                "stamp": float(pose.get("stamp", time.time())),
                "updated_at": time.time(),
                "frame_id": "map",
                "resolution": round(res, 4),
                "width": width,
                "height": height,
                "origin": {"x": round(ox, 3), "y": round(oy, 3), "yaw": 0.0},
                "pose": {
                    "x": round(px, 3),
                    "y": round(py, 3),
                    "yaw": round(yaw, 4),
                    "theta_deg": round(math.degrees(yaw), 2),
                    "stamp": float(pose.get("stamp", 0.0)),
                },
                "view": {
                    "x": round(view_x, 3),
                    "y": round(view_y, 3),
                    "yaw": round(yaw, 4),
                },
                "pose_in_map": pose_in_map,
                "occupied": occupied,
                "occupied_count": len(occupied) // 2,
                "map_points": map_points,
                "hz": round(hz, 1),
                "version": 4,
                "enabled": True,
                "persistent": True,
                "persisted": self.loaded_from_disk or os.path.isfile(PERSIST_PATH),
                "update_count": self.update_count,
            }


def load_waypoints() -> list[dict[str, Any]]:
    if not os.path.isfile(WAYPOINTS_PATH):
        return []
    try:
        with open(WAYPOINTS_PATH, encoding="utf-8") as handle:
            raw = json.load(handle)
        items = raw.get("waypoints", raw if isinstance(raw, list) else [])
        items = items if isinstance(items, list) else []
        return _normalize_waypoint_labels(items)
    except (OSError, json.JSONDecodeError, TypeError):
        return []


def _letter_from_index(index: int) -> str:
    n = max(0, index)
    chars: list[str] = []
    while True:
        chars.append(chr(65 + (n % 26)))
        n = n // 26 - 1
        if n < 0:
            break
    return "".join(reversed(chars))


def _normalize_waypoint_labels(waypoints: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [{**wp, "label": _letter_from_index(i)} for i, wp in enumerate(waypoints)]


def save_waypoints(waypoints: list[dict[str, Any]]) -> None:
    directory = os.path.dirname(WAYPOINTS_PATH)
    if directory:
        os.makedirs(directory, exist_ok=True)
    normalized = _normalize_waypoint_labels(waypoints)
    payload = {"version": 1, "updated_at": time.time(), "waypoints": normalized}
    tmp = f"{WAYPOINTS_PATH}.tmp"
    with open(tmp, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=2)
    os.replace(tmp, WAYPOINTS_PATH)


def add_waypoint(label: str, x: float, y: float, yaw: float) -> dict[str, Any]:
    waypoints = load_waypoints()
    item = {
        "id": str(uuid.uuid4()),
        "label": _letter_from_index(len(waypoints)),
        "x": round(float(x), 3),
        "y": round(float(y), 3),
        "yaw": round(float(yaw), 4),
        "createdAt": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }
    waypoints.append(item)
    save_waypoints(waypoints)
    return _normalize_waypoint_labels(waypoints)[-1]


def delete_waypoint(waypoint_id: str) -> bool:
    waypoints = load_waypoints()
    next_items = [w for w in waypoints if str(w.get("id")) != str(waypoint_id)]
    if len(next_items) == len(waypoints):
        return False
    save_waypoints(next_items)
    return True
