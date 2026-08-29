"""Path-distance helpers for Nav2 progress monitoring."""

from __future__ import annotations

import math
from collections.abc import Sequence


def remaining_path_distance(
    path_xy: Sequence[Sequence[float]],
    pose_xy: tuple[float, float],
) -> float | None:
    """Return distance from the nearest path point/segment to the goal.

    Nav2 global paths may contain detours, so Euclidean distance to the goal
    can increase while the rover is making valid progress. Projecting the
    current pose onto the path gives the distance that remains along that
    route.
    """
    points: list[tuple[float, float]] = []
    for point in path_xy:
        if len(point) < 2:
            continue
        x, y = float(point[0]), float(point[1])
        if math.isfinite(x) and math.isfinite(y):
            points.append((x, y))
    if len(points) < 2:
        return None

    total = 0.0
    segment_lengths: list[float] = []
    for start, end in zip(points, points[1:]):
        length = math.hypot(end[0] - start[0], end[1] - start[1])
        segment_lengths.append(length)
        total += length
    if total <= 1e-6:
        return 0.0

    px, py = pose_xy
    best_distance_sq = math.inf
    best_along = 0.0
    along = 0.0
    for index, (start, end) in enumerate(zip(points, points[1:])):
        dx = end[0] - start[0]
        dy = end[1] - start[1]
        length = segment_lengths[index]
        if length > 1e-6:
            t = ((px - start[0]) * dx + (py - start[1]) * dy) / (length * length)
            t = max(0.0, min(1.0, t))
            closest_x = start[0] + t * dx
            closest_y = start[1] + t * dy
            distance_sq = (px - closest_x) ** 2 + (py - closest_y) ** 2
            if distance_sq < best_distance_sq:
                best_distance_sq = distance_sq
                best_along = along + t * length
        along += length

    return max(0.0, total - best_along)
