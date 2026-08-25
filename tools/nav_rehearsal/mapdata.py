"""Decode slam_map.json / slam.json into plottable occupied world points."""

from __future__ import annotations

import base64
import json
import zlib
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass
class MapLayer:
    resolution: float
    origin_x: float
    origin_y: float
    width: int
    height: int
    occupied_xy: list[list[float]]  # [[x,y], ...] world meters
    bounds: tuple[float, float, float, float]  # minx,miny,maxx,maxy
    source: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "resolution": self.resolution,
            "origin_x": self.origin_x,
            "origin_y": self.origin_y,
            "width": self.width,
            "height": self.height,
            "occupied_xy": self.occupied_xy,
            "bounds": list(self.bounds),
            "occupied_count": len(self.occupied_xy),
            "source": self.source,
        }

    def crop(
        self,
        minx: float,
        miny: float,
        maxx: float,
        maxy: float,
        *,
        pad: float = 1.5,
    ) -> MapLayer:
        """Return a copy with only points near the given world AABB."""
        lo_x, lo_y = minx - pad, miny - pad
        hi_x, hi_y = maxx + pad, maxy + pad
        pts = [
            p
            for p in self.occupied_xy
            if lo_x <= p[0] <= hi_x and lo_y <= p[1] <= hi_y
        ]
        if pts:
            xs = [p[0] for p in pts]
            ys = [p[1] for p in pts]
            bounds = (min(xs), min(ys), max(xs), max(ys))
        else:
            bounds = (lo_x, lo_y, hi_x, hi_y)
        return MapLayer(
            resolution=self.resolution,
            origin_x=self.origin_x,
            origin_y=self.origin_y,
            width=self.width,
            height=self.height,
            occupied_xy=pts,
            bounds=bounds,
            source=self.source,
        )


def load_best_map(*paths: str | Path | None, max_points: int = 14000) -> MapLayer | None:
    """Try candidates in order; prefer dashboard-style occupied/map_points exports."""
    best: MapLayer | None = None
    best_score = -1
    for path in paths:
        if path is None:
            continue
        layer = load_slam_map(path, max_points=max_points)
        if layer is None or not layer.occupied_xy:
            continue
        score = len(layer.occupied_xy)
        name = Path(path).name.lower()
        # Prefer live slam.json (local working map) over the huge persistent grid.
        if "slam_live" in name or name == "slam.json":
            score += 50_000
        if score > best_score:
            best = layer
            best_score = score
    return best


def load_slam_map(path: str | Path, *, max_points: int = 14000) -> MapLayer | None:
    try:
        raw = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(raw, dict):
        return None

    source = Path(path).name

    # Dashboard / slam.json: world-space map_points are the most reliable.
    if isinstance(raw.get("map_points"), list) and raw["map_points"]:
        layer = _from_map_points(raw, max_points=max_points)
        layer.source = source
        return layer

    if isinstance(raw.get("occupied"), list) and raw["occupied"]:
        layer = _from_occupied_list(raw, max_points=max_points)
        layer.source = source
        return layer

    cells_b64 = raw.get("cells_b64")
    if not cells_b64:
        return None
    try:
        packed = zlib.decompress(base64.b64decode(cells_b64))
    except Exception:  # noqa: BLE001
        return None

    width = int(raw.get("width") or 0)
    height = int(raw.get("height") or 0)
    res = float(raw.get("resolution") or 0.05)
    ox = float(raw.get("origin_x") or (raw.get("origin") or {}).get("x") or 0.0)
    oy = float(raw.get("origin_y") or (raw.get("origin") or {}).get("y") or 0.0)
    if width <= 0 or height <= 0 or len(packed) < width * height:
        return None

    occupied: list[list[float]] = []
    # Encoded as 0=unknown, 1=free, 2=occupied in this project's slam_map.json.
    step = 1
    total_occ = sum(1 for b in packed if b == 2)
    if total_occ > max_points:
        step = max(1, total_occ // max_points)

    kept = 0
    seen = 0
    for iy in range(height):
        row = iy * width
        for ix in range(width):
            if packed[row + ix] != 2:
                continue
            seen += 1
            if (seen % step) != 0:
                continue
            wx = ox + (ix + 0.5) * res
            wy = oy + (iy + 0.5) * res
            occupied.append([round(wx, 3), round(wy, 3)])
            kept += 1
            if kept >= max_points:
                break
        if kept >= max_points:
            break

    if not occupied:
        return MapLayer(
            res,
            ox,
            oy,
            width,
            height,
            [],
            (ox, oy, ox + width * res, oy + height * res),
            source=source,
        )

    xs = [p[0] for p in occupied]
    ys = [p[1] for p in occupied]
    return MapLayer(
        resolution=res,
        origin_x=ox,
        origin_y=oy,
        width=width,
        height=height,
        occupied_xy=occupied,
        bounds=(min(xs), min(ys), max(xs), max(ys)),
        source=source,
    )


def _from_map_points(raw: dict[str, Any], *, max_points: int) -> MapLayer:
    res = float(raw.get("resolution") or 0.05)
    origin = raw.get("origin") if isinstance(raw.get("origin"), dict) else {}
    ox = float(raw.get("origin_x", origin.get("x", 0.0)))
    oy = float(raw.get("origin_y", origin.get("y", 0.0)))
    width = int(raw.get("width") or 0)
    height = int(raw.get("height") or 0)
    pts: list[list[float]] = []
    step = 1
    n = len(raw["map_points"])
    if n > max_points:
        step = max(1, n // max_points)
    for i, pt in enumerate(raw["map_points"]):
        if step > 1 and (i % step) != 0:
            continue
        if not isinstance(pt, dict):
            continue
        try:
            x = float(pt["x"])
            y = float(pt["y"])
        except (KeyError, TypeError, ValueError):
            continue
        pts.append([round(x, 3), round(y, 3)])
        if len(pts) >= max_points:
            break
    if pts:
        xs = [p[0] for p in pts]
        ys = [p[1] for p in pts]
        bounds = (min(xs), min(ys), max(xs), max(ys))
    else:
        bounds = (ox, oy, ox + max(width, 1) * res, oy + max(height, 1) * res)
    return MapLayer(res, ox, oy, width, height, pts, bounds)


def _from_occupied_list(raw: dict[str, Any], *, max_points: int) -> MapLayer:
    res = float(raw.get("resolution") or 0.05)
    origin = raw.get("origin") if isinstance(raw.get("origin"), dict) else {}
    ox = float(raw.get("origin_x", origin.get("x", 0.0)))
    oy = float(raw.get("origin_y", origin.get("y", 0.0)))
    width = int(raw.get("width") or 0)
    height = int(raw.get("height") or 0)
    occ = raw["occupied"]
    pts: list[list[float]] = []
    step = 1
    n = len(occ) // 2
    if n > max_points:
        step = max(1, n // max_points)
    for i in range(0, len(occ) - 1, 2 * step):
        ix = int(occ[i])
        iy = int(occ[i + 1])
        pts.append([round(ox + (ix + 0.5) * res, 3), round(oy + (iy + 0.5) * res, 3)])
        if len(pts) >= max_points:
            break
    if pts:
        xs = [p[0] for p in pts]
        ys = [p[1] for p in pts]
        bounds = (min(xs), min(ys), max(xs), max(ys))
    else:
        bounds = (ox, oy, ox + width * res, oy + height * res)
    return MapLayer(res, ox, oy, width, height, pts, bounds)
