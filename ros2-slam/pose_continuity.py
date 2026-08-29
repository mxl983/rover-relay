#!/usr/bin/env python3
"""Pose continuity prior: rover cannot teleport unless match is strong.

Scan matching in low-feature spaces can prefer a distant lookalike corridor.
Favor poses near the last few snapshots; only accept a large jump when the
distant match is clearly strong (true kidnap / global reloc).
"""

from __future__ import annotations

import math
from collections import deque
from dataclasses import dataclass


@dataclass(frozen=True)
class PoseSample:
    x: float
    y: float
    yaw: float
    stamp: float = 0.0


class PoseHistory:
    """Short rolling window of accepted poses."""

    def __init__(self, maxlen: int = 8) -> None:
        self._samples: deque[PoseSample] = deque(maxlen=max(1, int(maxlen)))

    def __len__(self) -> int:
        return len(self._samples)

    def clear(self) -> None:
        self._samples.clear()

    def push(self, x: float, y: float, yaw: float, stamp: float = 0.0) -> None:
        self._samples.append(
            PoseSample(float(x), float(y), float(yaw), float(stamp))
        )

    def latest(self) -> PoseSample | None:
        return self._samples[-1] if self._samples else None

    def mean_xy(self) -> tuple[float, float] | None:
        if not self._samples:
            return None
        n = float(len(self._samples))
        return (
            sum(s.x for s in self._samples) / n,
            sum(s.y for s in self._samples) / n,
        )

    def jump_m(self, x: float, y: float) -> float:
        """Distance from candidate to recent mean (or latest)."""
        ref = self.mean_xy()
        if ref is None:
            return 0.0
        return math.hypot(float(x) - ref[0], float(y) - ref[1])


def continuity_bonus(
    x: float,
    y: float,
    history: PoseHistory,
    *,
    weight: float = 8.0,
    sigma_m: float = 0.40,
) -> float:
    """Additive score favoring candidates near recent poses (Gaussian prior)."""
    ref = history.mean_xy()
    if ref is None or weight <= 0.0 or sigma_m <= 1e-6:
        return 0.0
    d = math.hypot(float(x) - ref[0], float(y) - ref[1])
    return float(weight) * math.exp(-(d * d) / (2.0 * sigma_m * sigma_m))


def accept_pose_jump(
    *,
    jump_m: float,
    score_near: float | None,
    score_far: float | None,
    teleport_m: float = 0.80,
    strong_score: float = 0.55,
    margin: float = 0.12,
    global_reloc: bool = False,
    max_speed_mps: float | None = None,
    dt_s: float | None = None,
) -> bool:
    """Decide whether a candidate far from history should replace the local pose.

    Continuity law (normal tracking):
    - Small jumps accepted (normal driving / scan-match jitter).
    - Impossible motion (faster than ``max_speed_mps`` over ``dt_s``) rejected
      when scores are unavailable.
    - Large jumps need a *strong* far score.
    - If the local (near) match is still healthy (``>= strong_score``), reject
      teleports — temp objects / lookalikes must not yank the pose.
    - Otherwise far must beat near by ``margin`` (lookalike guard).

    Explicit Reposition (``global_reloc=True``) is exempt from continuity: a
    strong far score alone is enough. The user asked to search globally.
    """
    if jump_m < float(teleport_m):
        return True
    if (
        max_speed_mps is not None
        and dt_s is not None
        and float(dt_s) > 1e-3
        and jump_m > float(max_speed_mps) * float(dt_s) + float(teleport_m)
    ):
        # Physically impossible step — reject even before score checks when the
        # far score is missing; when scores exist, fall through to match logic
        # so a genuine high-confidence recovery can still win.
        if score_far is None and not global_reloc:
            return False
    if score_far is None:
        return False
    if float(score_far) < float(strong_score):
        return False
    if global_reloc:
        return True
    # Healthy local match ⇒ stay (temp objects / symmetric rooms / aisles).
    if score_near is not None and float(score_near) >= float(strong_score):
        return False
    if score_near is not None and float(score_far) < float(score_near) + float(margin):
        return False
    return True


@dataclass(frozen=True)
class PoseCandidate:
    """A scan-match hypothesis in map frame."""

    x: float
    y: float
    score: float
    yaw: float = 0.0
    label: str = ""


def select_nearest_strong_match(
    last_x: float,
    last_y: float,
    candidates: list[PoseCandidate],
    *,
    min_score: float = 0.55,
) -> PoseCandidate | None:
    """Among high-scoring lookalikes, pick the one closest to the last pose.

    Smooth corridors often have several poses (a, f, k, …) with nearly identical
    lidar scores. Continuity says: if the rover was at ``d``, prefer ``f`` over
    ``a`` or ``k`` when all three match equally well — the nearest strong match.
    """
    strong = [c for c in candidates if float(c.score) >= float(min_score)]
    if not strong:
        return None
    return min(
        strong,
        key=lambda c: math.hypot(float(c.x) - last_x, float(c.y) - last_y),
    )


def select_continuous_match(
    last_x: float,
    last_y: float,
    candidates: list[PoseCandidate],
    *,
    min_score: float = 0.55,
    local_radius_m: float = 1.5,
) -> PoseCandidate | None:
    """Continuous operation: prefer a strong match near the last pose.

    Temporary objects can drop the score at the true pose while a distant
    lookalike still looks "perfect" on the map. If any strong candidate lies
    within ``local_radius_m`` of the last pose, pick the best of those (no
    teleport). Only when nothing local is strong do we fall back to the
    nearest global strong match (kidnap / reloc).
    """
    strong = [c for c in candidates if float(c.score) >= float(min_score)]
    if not strong:
        return None

    def dist(c: PoseCandidate) -> float:
        return math.hypot(float(c.x) - last_x, float(c.y) - last_y)

    local = [c for c in strong if dist(c) <= float(local_radius_m)]
    if local:
        # Best local score; tie-break closer to last pose.
        return max(local, key=lambda c: (float(c.score), -dist(c)))
    return min(strong, key=dist)


def continuity_adjusted_score(
    candidate: PoseCandidate,
    last_x: float,
    last_y: float,
    *,
    proximity_weight: float = 0.15,
    sigma_m: float = 2.0,
) -> float:
    """Scan score plus a soft proximity prior (does not invent weak matches).

    Used to break ties between equally good corridor lookalikes without
    overriding a clearly stronger distant match (kidnap).
    """
    d = math.hypot(float(candidate.x) - last_x, float(candidate.y) - last_y)
    prior = float(proximity_weight) * math.exp(
        -(d * d) / (2.0 * float(sigma_m) * float(sigma_m))
    )
    return float(candidate.score) + prior
