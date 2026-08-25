"""Programmatic harness for SLAM/Nav sim experiments (no browser).

Usage:
  python3 -m sim --drift
  python3 -m sim --map-quality
  python3 -c "from sim.harness import run_apartment_drift_benchmark; print(run_apartment_drift_benchmark())"
"""

from __future__ import annotations

import math
from typing import Iterable, Sequence

from .engine import SlamNavSimulation, distance, wrap_angle


def pose_error(sim: SlamNavSimulation) -> dict:
    """Ground-truth vs lidar estimate delta (what 'drift' means here)."""
    true = sim.pose
    est = sim.estimated_pose
    return {
        "xy_error_m": distance(true, est),
        "yaw_error_rad": abs(wrap_angle(true["yaw"] - est["yaw"])),
        "yaw_error_deg": abs(math.degrees(wrap_angle(true["yaw"] - est["yaw"]))),
        "true": dict(true),
        "estimated": dict(est),
    }


def drive(
    sim: SlamNavSimulation,
    commands: Sequence[tuple[float, float]],
    *,
    dt: float = 1 / 30,
    ticks_per_command: int = 3,
    record_every: int = 1,
) -> list[dict]:
    """Apply (linear, angular) commands and record pose-error samples."""
    samples: list[dict] = []
    step_i = 0
    for linear, angular in commands:
        sim.set_manual(linear, angular)
        for _ in range(ticks_per_command):
            sim.step(dt)
            step_i += 1
            if record_every > 0 and step_i % record_every == 0:
                sample = pose_error(sim)
                sample["step"] = step_i
                sample["known_percent"] = sim.metrics()["known_percent"]
                samples.append(sample)
    return samples


def apartment_tour_commands() -> list[tuple[float, float]]:
    """Rough tour through apartment_loop rooms/corridors."""
    return (
        [(0.0, 0.9)] * 18
        + [(0.45, 0.0)] * 55
        + [(0.0, 0.95)] * 22
        + [(0.45, 0.0)] * 65
        + [(0.0, 0.95)] * 22
        + [(0.45, 0.0)] * 55
        + [(0.0, 0.95)] * 20
        + [(0.45, 0.0)] * 40
        + [(0.0, 0.0)] * 4
    )


def summarize_errors(samples: Iterable[dict]) -> dict:
    samples = list(samples)
    if not samples:
        return {
            "count": 0,
            "max_xy_error_m": 0.0,
            "final_xy_error_m": 0.0,
            "mean_xy_error_m": 0.0,
            "max_yaw_error_deg": 0.0,
            "final_yaw_error_deg": 0.0,
        }
    xy = [s["xy_error_m"] for s in samples]
    yaw = [s["yaw_error_deg"] for s in samples]
    return {
        "count": len(samples),
        "max_xy_error_m": max(xy),
        "final_xy_error_m": xy[-1],
        "mean_xy_error_m": sum(xy) / len(xy),
        "max_yaw_error_deg": max(yaw),
        "final_yaw_error_deg": yaw[-1],
        "final_true": samples[-1]["true"],
        "final_estimated": samples[-1]["estimated"],
        "final_known_percent": samples[-1].get("known_percent", 0.0),
    }


def run_apartment_drift_benchmark(*, noise_enabled: bool = False) -> dict:
    """Compare mapping-while-driving vs freeze-map localization drift."""
    cmds = apartment_tour_commands()

    mapping = SlamNavSimulation("apartment_loop", noise_enabled=noise_enabled)
    mapping_samples = drive(mapping, cmds)
    mapping_summary = summarize_errors(mapping_samples)

    localized = SlamNavSimulation("apartment_loop", noise_enabled=noise_enabled)
    localized.reveal_map()
    localized.freeze_map()
    localized_samples = drive(localized, cmds)
    localized_summary = summarize_errors(localized_samples)

    return {
        "scenario": "apartment_loop",
        "noise_enabled": noise_enabled,
        "mapping_while_driving": mapping_summary,
        "frozen_map_localization": localized_summary,
    }


def run_drift_cli() -> int:
    report = run_apartment_drift_benchmark(noise_enabled=False)
    print("Apartment loop localization drift benchmark")
    print(f"  noise_enabled={report['noise_enabled']}")
    for label in ("mapping_while_driving", "frozen_map_localization"):
        block = report[label]
        print(f"\n[{label}]")
        print(f"  max xy error : {block['max_xy_error_m']:.3f} m")
        print(f"  final xy err : {block['final_xy_error_m']:.3f} m")
        print(f"  mean xy err  : {block['mean_xy_error_m']:.3f} m")
        print(f"  max yaw err  : {block['max_yaw_error_deg']:.2f} deg")
        print(f"  final yaw    : {block['final_yaw_error_deg']:.2f} deg")
        print(f"  known map    : {block['final_known_percent']:.1f}%")
        true = block["final_true"]
        est = block["final_estimated"]
        print(
            "  true pose    : "
            f"x={true['x']:.2f} y={true['y']:.2f} yaw={true['yaw']:.2f}"
        )
        print(
            "  estimated    : "
            f"x={est['x']:.2f} y={est['y']:.2f} yaw={est['yaw']:.2f}"
        )
    frozen = report["frozen_map_localization"]["final_xy_error_m"]
    mapping = report["mapping_while_driving"]["final_xy_error_m"]
    print("\n[note]")
    print(
        f"  frozen-map final err {frozen:.3f}m vs mapping-while-driving {mapping:.3f}m."
    )
    print("  Prefer Build Map (reveal+freeze) before long drives / nav / kidnap tests.")
    return 0


def run_auto_map(
    sim: SlamNavSimulation,
    *,
    max_steps: int = 12000,
    dt: float = 1 / 30,
    speed: float = 2.5,
) -> dict:
    """Run autonomous exploration until map-complete or step budget."""
    sim.speed_multiplier = speed
    sim.start_auto_map()
    for step_i in range(max_steps):
        sim.step(dt)
        if not sim.exploring and sim.mode == "localization":
            break
    integrity = sim.map_integrity()
    err = pose_error(sim)
    return {
        "completed": (not sim.exploring) and sim.mode == "localization",
        "steps": step_i + 1,
        "status": sim.status,
        "spins": sim._explore_spins,
        "pose_error_m": err["xy_error_m"],
        **integrity,
    }


def run_mapping_quality_benchmark(*, noise_enabled: bool = False) -> dict:
    """Tour-built map vs auto-map vs full reveal ground truth."""
    cmds = apartment_tour_commands()

    tour = SlamNavSimulation("apartment_loop", noise_enabled=noise_enabled)
    drive(tour, cmds)
    tour.fill_map_holes()
    tour_integrity = tour.map_integrity()

    auto = SlamNavSimulation("apartment_loop", noise_enabled=noise_enabled)
    auto_result = run_auto_map(auto)

    revealed = SlamNavSimulation("apartment_loop", noise_enabled=noise_enabled)
    revealed.reveal_map()
    reveal_integrity = revealed.map_integrity()

    return {
        "scenario": "apartment_loop",
        "noise_enabled": noise_enabled,
        "tour_mapping": tour_integrity,
        "auto_mapping": auto_result,
        "reveal_oracle": reveal_integrity,
    }


def run_map_quality_cli() -> int:
    report = run_mapping_quality_benchmark(noise_enabled=False)
    print("Apartment loop map integrity benchmark (vs static GT)")
    print(f"  noise_enabled={report['noise_enabled']}")

    def dump(label: str, block: dict) -> None:
        print(f"\n[{label}]")
        for key in (
            "free_recall",
            "occupied_recall",
            "occupied_iou",
            "frontiers",
            "known_percent",
            "false_occupied_on_free",
        ):
            if key not in block:
                continue
            value = block[key]
            if isinstance(value, float) and key.endswith(("recall", "iou")):
                print(f"  {key:24s}: {value:.3f}")
            elif isinstance(value, float):
                print(f"  {key:24s}: {value:.1f}")
            else:
                print(f"  {key:24s}: {value}")
        for key in ("completed", "steps", "spins", "pose_error_m", "status"):
            if key in block:
                value = block[key]
                if isinstance(value, float):
                    print(f"  {key:24s}: {value:.3f}")
                else:
                    print(f"  {key:24s}: {value}")

    dump("tour_mapping", report["tour_mapping"])
    dump("auto_mapping", report["auto_mapping"])
    dump("reveal_oracle", report["reveal_oracle"])

    auto = report["auto_mapping"]
    tour = report["tour_mapping"]
    ok = (
        auto.get("completed")
        and auto.get("free_recall", 0) >= 0.85
        # Occupied recall vs GT can be lower with 1-cell walls (GT walls are thick).
        and auto.get("occupied_recall", 0) >= 0.14
        # Exterior UNKNOWN borders leave many frontiers even when the interior is mapped.
        and (auto.get("frontiers", 999) <= 320 or auto.get("free_recall", 0) >= 0.90)
        # Tour is a short open-loop drive (pose drifts while mapping); only require
        # that integration produced meaningful structure, not GT-closed coverage.
        and tour.get("known_percent", 0) >= 20.0
        and tour.get("occupied_recall", 0) >= 0.05
    )
    print("\n[verdict]")
    print("  PASS" if ok else "  FAIL (coverage / closure below target)")
    return 0 if ok else 1
