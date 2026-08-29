#!/usr/bin/env python3
"""Drive + IMU axis experiment: which gyro tracks forward vs turn?

Phases: rest → forward → stop (decel) → turn left → turn right → stop.
Logs mean/median/max delta per axis vs rest baseline.
"""

from __future__ import annotations

import json
import math
import statistics
import subprocess
import sys
import time
import urllib.error
import urllib.request
import ssl

IMU_URL = "https://rover.tail9d0237.ts.net:3000/api/sensors/imu"
DRIVE_URL = "https://127.0.0.1:8787/api/navigation/drive"
CTX = ssl._create_unverified_context()


def fetch_imu() -> dict | None:
    try:
        req = urllib.request.Request(IMU_URL, headers={"Accept": "application/json"})
        with urllib.request.urlopen(req, timeout=2, context=CTX) as r:
            raw = json.loads(r.read().decode())
        s = raw.get("sample") or raw
        g = s["gyro"]
        a = s["accel"]
        return {
            "seq": int(s.get("seq") or 0),
            "gx": float(g["x"]),
            "gy": float(g["y"]),
            "gz": float(g["z"]),
            "ax": float(a["x"]),
            "ay": float(a["y"]),
            "az": float(a["z"]),
        }
    except (urllib.error.URLError, TimeoutError, KeyError, TypeError, ValueError):
        return None


def drive(x: float, y: float) -> None:
    payload = json.dumps({"drive": {"x": x, "y": y}}).encode()
    req = urllib.request.Request(
        DRIVE_URL,
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=2, context=CTX) as r:
        r.read()


def sample_phase(sec: float, hz: float = 40) -> list[dict]:
    rows: list[dict] = []
    t0 = time.monotonic()
    dt = 1.0 / hz
    while time.monotonic() - t0 < sec:
        row = fetch_imu()
        if row:
            rows.append(row)
        time.sleep(dt)
    return rows


def get_pose() -> tuple[float, float, float] | None:
    try:
        out = subprocess.check_output(
            ["docker", "exec", "relay-ros2-slam-1", "cat", "/app/lidar/slam_live.json"],
            text=True,
            timeout=3,
        )
        p = json.loads(out).get("pose") or {}
        return float(p["x"]), float(p["y"]), float(p.get("yaw", 0))
    except (subprocess.CalledProcessError, KeyError, TypeError, ValueError, json.JSONDecodeError):
        return None


def wrap(a: float) -> float:
    while a > math.pi:
        a -= 2 * math.pi
    while a < -math.pi:
        a += 2 * math.pi
    return a


def summarize_phase(name: str, rows: list[dict], bias: dict[str, float]) -> dict:
    if not rows:
        return {"phase": name, "n": 0}
    out: dict = {"phase": name, "n": len(rows)}
    for key in ("gx", "gy", "gz", "ax", "ay", "az"):
        vals = [r[key] - bias[key] for r in rows]
        out[f"d{key}_mean"] = statistics.fmean(vals)
        out[f"d{key}_med"] = statistics.median(vals)
        out[f"d{key}_max"] = max(vals, key=abs)
        out[f"d{key}_std"] = statistics.pstdev(vals) if len(vals) > 1 else 0.0
    # Dominant gyro axis by peak |delta|
    peaks = {
        "gx": abs(out["dgx_max"]),
        "gy": abs(out["dgy_max"]),
        "gz": abs(out["dgz_max"]),
    }
    out["dominant_gyro"] = max(peaks, key=peaks.get)
    out["gyro_peaks"] = peaks
    return out


def run_phase(
    name: str,
    x: float,
    y: float,
    move_sec: float,
    bias: dict[str, float],
    *,
    settle_before: float = 0.3,
    sample_after_cmd: float = 0.15,
) -> dict:
    pose0 = get_pose()
    time.sleep(settle_before)
    pre = sample_phase(0.4, hz=40)
    drive(x, y)
    time.sleep(sample_after_cmd)
    during = sample_phase(move_sec, hz=40)
    drive(0.0, 0.0)
    post = sample_phase(0.8, hz=40)  # capture decel tail
    time.sleep(0.5)
    pose1 = get_pose()
    dist = yaw_d = None
    if pose0 and pose1:
        dist = math.hypot(pose1[0] - pose0[0], pose1[1] - pose0[1])
        yaw_d = math.degrees(wrap(pose1[2] - pose0[2]))
    return {
        "name": name,
        "cmd": (x, y),
        "pose_dist_m": dist,
        "pose_dyaw_deg": yaw_d,
        "pre": summarize_phase(f"{name}_pre", pre, bias),
        "during": summarize_phase(f"{name}_during", during, bias),
        "post": summarize_phase(f"{name}_post", post, bias),
    }


def print_summary(results: list[dict], bias: dict[str, float]) -> None:
    print("\n=== REST BIAS (subtracted from all phases) ===")
    for k in ("gx", "gy", "gz", "ax", "ay", "az"):
        print(f"  {k}: {bias[k]:+.4f}")

    for r in results:
        print(f"\n=== {r['name']} cmd=({r['cmd'][0]:+.2f}, {r['cmd'][1]:+.2f}) ===")
        if r.get("pose_dist_m") is not None:
            print(
                f"  SLAM Δxy={r['pose_dist_m']:.3f}m  Δyaw={r['pose_dyaw_deg']:+.1f}°"
            )
        for part in ("during", "post"):
            s = r[part]
            if s.get("n", 0) == 0:
                continue
            print(f"  [{part}] n={s['n']}  dominant_gyro={s['dominant_gyro']}  "
                  f"peaks rad/s { {k: round(v,3) for k,v in s['gyro_peaks'].items()} }")
            print(
                f"    Δgyro mean: gx={s['dgx_mean']:+.3f}  "
                f"gy={s['dgy_mean']:+.3f}  gz={s['dgz_mean']:+.3f}"
            )
            print(
                f"    Δaccel mean: ax={s['dax_mean']:+.3f}  "
                f"ay={s['day_mean']:+.3f}  az={s['daz_mean']:+.3f}"
            )

    print("\n=== HYPOTHESIS CHECK ===")
    fwd = next((r for r in results if r["name"] == "FORWARD"), None)
    back = next((r for r in results if r["name"] == "REVERSE"), None)
    left = next((r for r in results if r["name"] == "TURN_LEFT"), None)
    right = next((r for r in results if r["name"] == "TURN_RIGHT"), None)

    def best_gyro(r: dict | None, part: str = "during") -> str | None:
        if not r:
            return None
        return r[part].get("dominant_gyro")

    print(f"  Forward motion  → dominant gyro: {best_gyro(fwd)} (during), post-stop: {best_gyro(fwd, 'post')}")
    print(f"  Reverse motion  → dominant gyro: {best_gyro(back)} (during), post-stop: {best_gyro(back, 'post')}")
    print(f"  Turn left       → dominant gyro: {best_gyro(left)}")
    print(f"  Turn right      → dominant gyro: {best_gyro(right)}")

    # User hypothesis: gy = fwd/back, gz = turn
    turn_phases = [left, right]
    lin_phases = [fwd, back]
    gy_turn = sum(
        1 for r in turn_phases if r and r["during"].get("dominant_gyro") == "gy"
    )
    gz_turn = sum(
        1 for r in turn_phases if r and r["during"].get("dominant_gyro") == "gz"
    )
    gy_lin = sum(
        1 for r in lin_phases if r and r["during"].get("dominant_gyro") == "gy"
    )
    gz_lin = sum(
        1 for r in lin_phases if r and r["during"].get("dominant_gyro") == "gz"
    )
    print(f"  Turns dominated by gy: {gy_turn}/2, gz: {gz_turn}/2")
    print(f"  Linear dominated by gy: {gy_lin}/2, gz: {gz_lin}/2")


def main() -> int:
    print("IMU gyro axis experiment")
    print("Ensure rover has space to move ~1m and spin. Ctrl+C aborts.\n")

    try:
        print("Rest baseline 2s…")
        rest = sample_phase(2.0, hz=40)
        if len(rest) < 10:
            print("Too few IMU samples — is rover online?")
            return 1
        bias = {k: statistics.fmean(r[k] for r in rest) for k in ("gx", "gy", "gz", "ax", "ay", "az")}
        print(f"  rest gz bias={bias['gz']:+.3f} rad/s ({math.degrees(bias['gz']):+.1f}°/s)")

        results = []
        results.append(run_phase("FORWARD", 0.0, -0.55, 2.5, bias))
        results.append(run_phase("REVERSE", 0.0, 0.45, 2.0, bias))
        results.append(run_phase("TURN_LEFT", -0.85, 0.0, 2.5, bias))
        results.append(run_phase("TURN_RIGHT", 0.85, 0.0, 2.5, bias))

        print_summary(results, bias)
        return 0
    except KeyboardInterrupt:
        print("\nAborted.")
        return 130
    finally:
        try:
            drive(0.0, 0.0)
            print("\nMotors stopped.")
        except Exception:
            pass


if __name__ == "__main__":
    raise SystemExit(main())
