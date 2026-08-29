#!/usr/bin/env python3
"""Realistic floorplan fixtures for pose-continuity scenarios.

Maps are simple 2D wall polylines + named poses (meters). Used by unit tests
and the browser visualizer — not lettered hallway toys.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class FloorPlan:
    """A tiny occupancy sketch for visualization + continuity tests."""

    id: str
    title: str
    # Wall polylines: each is a list of (x, y) vertices in meters.
    walls: tuple[tuple[tuple[float, float], ...], ...]
    # Optional filled obstacles (boxes, shelves, cars): (x0,y0,x1,y1)
    obstacles: tuple[tuple[float, float, float, float], ...] = ()
    # Named poses on the map.
    poses: dict[str, tuple[float, float]] = field(default_factory=dict)
    # Soft annotations for the UI.
    labels: tuple[tuple[str, float, float], ...] = ()  # (text, x, y)


def office_floor() -> FloorPlan:
    """Small office: lobby, hallway, two identical meeting rooms (lookalikes)."""
    # Outer shell
    outer = (
        (0.0, 0.0),
        (12.0, 0.0),
        (12.0, 8.0),
        (0.0, 8.0),
        (0.0, 0.0),
    )
    # Interior walls
    hall_south = ((0.0, 3.0), (12.0, 3.0))
    room_div = ((6.0, 3.0), (6.0, 8.0))
    lobby_div = ((4.0, 0.0), (4.0, 3.0))
    return FloorPlan(
        id="office",
        title="Office suite",
        walls=(outer, hall_south, room_div, lobby_div),
        obstacles=(
            # Reception desk
            (0.4, 0.4, 2.2, 1.2),
            # Identical conference tables (lookalike rooms)
            (6.8, 4.5, 10.8, 6.8),
            (0.8, 4.5, 4.8, 6.8),
            # Hall lockers
            (8.0, 1.0, 11.5, 1.6),
        ),
        poses={
            "lobby": (2.0, 1.6),
            "hall_west": (5.0, 1.6),
            "hall_mid": (7.5, 1.6),
            "hall_east": (10.5, 1.6),
            "meet_a": (2.8, 5.5),   # west meeting room
            "meet_b": (8.8, 5.5),   # east meeting room (lookalike)
            "door_a": (3.0, 3.4),
            "door_b": (9.0, 3.4),
        },
        labels=(
            ("Lobby", 1.5, 2.2),
            ("Hall", 7.5, 2.2),
            ("Meet A", 2.8, 7.2),
            ("Meet B", 8.8, 7.2),
        ),
    )


def warehouse_floor() -> FloorPlan:
    """Warehouse with three identical rack aisles (classic teleport trap)."""
    outer = (
        (0.0, 0.0),
        (16.0, 0.0),
        (16.0, 10.0),
        (0.0, 10.0),
        (0.0, 0.0),
    )
    # Vertical rack rows create lookalike corridors between them
    racks = []
    for x0 in (2.0, 5.5, 9.0, 12.5):
        racks.append((x0, 1.5, x0 + 1.2, 8.5))
    return FloorPlan(
        id="warehouse",
        title="Warehouse aisles",
        walls=(outer,),
        obstacles=tuple(racks),
        poses={
            "dock": (1.0, 5.0),
            "aisle1_south": (3.9, 2.5),
            "aisle1_mid": (3.9, 5.0),
            "aisle1_north": (3.9, 7.5),
            "aisle2_south": (7.4, 2.5),
            "aisle2_mid": (7.4, 5.0),
            "aisle2_north": (7.4, 7.5),
            "aisle3_south": (10.9, 2.5),
            "aisle3_mid": (10.9, 5.0),
            "aisle3_north": (10.9, 7.5),
            "exit": (15.0, 5.0),
        },
        labels=(
            ("Dock", 0.8, 9.2),
            ("Aisle 1", 3.5, 0.6),
            ("Aisle 2", 7.0, 0.6),
            ("Aisle 3", 10.5, 0.6),
        ),
    )


def apartment_floor() -> FloorPlan:
    """L-shaped apartment: living → kitchen → hall → bedroom."""
    # L polygon outline
    outer = (
        (0.0, 0.0),
        (8.0, 0.0),
        (8.0, 4.0),
        (4.0, 4.0),
        (4.0, 9.0),
        (0.0, 9.0),
        (0.0, 0.0),
    )
    kitchen_wall = ((4.0, 0.0), (4.0, 4.0))
    bedroom_wall = ((0.0, 5.5), (4.0, 5.5))
    return FloorPlan(
        id="apartment",
        title="L-shaped apartment",
        walls=(outer, kitchen_wall, bedroom_wall),
        obstacles=(
            # Couch
            (0.4, 0.4, 3.2, 1.4),
            # Kitchen island
            (5.0, 1.2, 7.2, 2.6),
            # Bed
            (0.5, 6.2, 2.8, 8.4),
        ),
        poses={
            "living": (2.0, 2.5),
            "kitchen": (6.0, 3.0),
            "hall": (2.0, 4.8),
            "bedroom": (2.0, 7.2),
            "entry": (7.2, 0.8),
        },
        labels=(
            ("Living", 1.5, 3.5),
            ("Kitchen", 5.5, 0.5),
            ("Hall", 2.2, 5.1),
            ("Bedroom", 2.0, 8.6),
        ),
    )


def yard_floor() -> FloorPlan:
    """Fenced yard / parking with two similar car bays (outdoor lookalikes)."""
    fence = (
        (0.0, 0.0),
        (14.0, 0.0),
        (14.0, 9.0),
        (0.0, 9.0),
        (0.0, 0.0),
    )
    return FloorPlan(
        id="yard",
        title="Fenced yard / parking",
        walls=(fence,),
        obstacles=(
            # Two similar parked cars
            (2.0, 2.0, 5.0, 3.4),
            (2.0, 5.5, 5.0, 6.9),
            # Shed
            (11.0, 1.0, 13.5, 4.0),
            # Tree trunk
            (8.5, 7.0, 9.2, 7.7),
        ),
        poses={
            "gate": (0.8, 4.5),
            "bay_south": (3.5, 1.2),
            "bay_mid": (3.5, 4.5),
            "bay_north": (3.5, 7.8),
            "beside_car_a": (6.0, 2.7),
            "beside_car_b": (6.0, 6.2),  # lookalike of beside_car_a
            "shed": (12.0, 5.5),
        },
        labels=(
            ("Gate", 0.5, 5.2),
            ("Car A", 3.0, 3.8),
            ("Car B", 3.0, 7.3),
            ("Shed", 11.5, 0.5),
        ),
    )


MAPS: dict[str, FloorPlan] = {
    m.id: m
    for m in (office_floor(), warehouse_floor(), apartment_floor(), yard_floor())
}


def map_to_json(plan: FloorPlan) -> dict[str, Any]:
    return {
        "id": plan.id,
        "title": plan.title,
        "walls": [list(poly) for poly in plan.walls],
        "obstacles": [list(o) for o in plan.obstacles],
        "poses": {k: {"x": v[0], "y": v[1]} for k, v in plan.poses.items()},
        "labels": [{"text": t, "x": x, "y": y} for t, x, y in plan.labels],
    }
