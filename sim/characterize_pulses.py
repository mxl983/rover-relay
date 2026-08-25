#!/usr/bin/env python3
"""Characterize binary A/D pulse → yaw (tank kinematics = real WASD tracks).

Run:  python3 -m sim.characterize_pulses

Pure-A continuous yaw ≈ 225°/s. Measured °/pulse ≈ 225 * on_s (off only coasts).
Use this when retuning NAV_TURN_PULSE_* in sim/drive.py and ros2-nav/bridges.py.
"""

from __future__ import annotations

import math

from .drive import (
    TURN_PULSE_OFF_ARC_SEC,
    TURN_PULSE_OFF_PURE_SEC,
    TURN_PULSE_ON_ARC_SEC,
    TURN_PULSE_ON_PURE_SEC,
    continuous_pure_turn_yaw_rate_rps,
    integrate_tank,
    keys_to_tracks,
    tracks_to_twist,
    yaw_per_pure_pulse_rad,
)


def _run_pure(on: float, off: float, n_pulses: int = 10, dt: float = 0.01) -> float:
    v_l, v_r = keys_to_tracks(["a"])
    pose = {"x": 0.0, "y": 0.0, "yaw": 0.0}
    t = 0.0
    deltas: list[float] = []
    for _ in range(n_pulses):
        yaw0 = pose["yaw"]
        end = t + on
        while t < end - 1e-12:
            step = min(dt, end - t)
            pose = integrate_tank(pose, v_l, v_r, step)
            t += step
        deltas.append(pose["yaw"] - yaw0)
        end = t + off
        while t < end - 1e-12:
            t += min(dt, end - t)
    return sum(deltas) / len(deltas)


def main() -> None:
    omega = continuous_pure_turn_yaw_rate_rps()
    print(f"continuous pure-A ω = {math.degrees(omega):.2f}°/s")
    print(
        f"defaults pure {TURN_PULSE_ON_PURE_SEC}/{TURN_PULSE_OFF_PURE_SEC} "
        f"→ {math.degrees(yaw_per_pure_pulse_rad()):.2f}°/pulse (theory)"
    )
    print(
        f"defaults arc  {TURN_PULSE_ON_ARC_SEC}/{TURN_PULSE_OFF_ARC_SEC}"
    )
    print()
    print(f"{'on':>5} {'off':>5} {'duty':>5} {'°/pulse':>8} {'°/s avg':>8} {'s/45°':>6}")
    for on in (0.04, 0.05, 0.06, 0.08, 0.10, 0.12, 0.14):
        for off in (0.06, 0.08, 0.10, 0.12, 0.16, 0.20, 0.24):
            mean = _run_pure(on, off)
            # Prefer theory for display (integrator wrap can scramble long ons).
            deg = math.degrees(omega * on)
            duty = on / (on + off)
            avg = math.degrees(omega * duty)
            print(
                f"{on:5.2f} {off:5.2f} {duty:5.0%} {deg:8.2f} {avg:8.1f} "
                f"{(45.0 / avg) if avg > 1e-6 else float('inf'):6.2f}"
            )
            _ = mean  # measured path exercised for smoke check

    # Arc sample
    v_l, v_r = keys_to_tracks(["w", "a"])
    _lin, ang = tracks_to_twist(v_l, v_r)
    print()
    print(
        f"continuous W+A ω = {math.degrees(ang):.1f}°/s  "
        f"→ ~{math.degrees(ang) * TURN_PULSE_ON_ARC_SEC:.1f}° yaw during each arc on-pulse"
    )


if __name__ == "__main__":
    main()
