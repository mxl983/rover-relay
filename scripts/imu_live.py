#!/usr/bin/env python3
"""Live IMU viewer — polls Pi /api/sensors/imu and prints accel + gyro.

Chip frame (rover flat, silkscreen Z up): +X forward, +Y left, +Z up.
At rest on level ground expect accel x≈0 y≈0 z≈±1 g and gyro z≈0.

Usage:
  python3 scripts/imu_live.py
  SLAM_IMU_URL=https://rover.tail9d0237.ts.net:3000/api/sensors/imu python3 scripts/imu_live.py
  python3 scripts/imu_live.py --hz 20 --history 8
"""

from __future__ import annotations

import argparse
import json
import math
import os
import ssl
import sys
import time
import urllib.error
import urllib.request
from collections import deque

G = 9.80665
DEFAULT_URL = os.environ.get(
    "SLAM_IMU_URL",
    os.environ.get(
        "PI_IMU_URL",
        "https://rover.tail9d0237.ts.net:3000/api/sensors/imu",
    ),
)
TOKEN = (
    os.environ.get("SLAM_IMU_TOKEN")
    or os.environ.get("NAVIGATION_API_TOKEN")
    or os.environ.get("ROVER_API_TOKEN")
    or ""
)
SSL_INSECURE = os.environ.get("SLAM_IMU_SSL_INSECURE", "1") not in (
    "0",
    "false",
    "False",
)


def fetch(url: str) -> dict | None:
    headers = {"Accept": "application/json"}
    if TOKEN:
        headers["Authorization"] = f"Bearer {TOKEN}"
    ctx = ssl._create_unverified_context() if url.startswith("https://") else None
    if url.startswith("https://") and not SSL_INSECURE:
        ctx = None
    try:
        req = urllib.request.Request(url, headers=headers, method="GET")
        with urllib.request.urlopen(req, timeout=2.0, context=ctx) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, OSError):
        return None


def parse_sample(raw: dict | None) -> dict | None:
    if not raw:
        return None
    status = raw.get("status") if isinstance(raw.get("status"), dict) else {}
    if status.get("connected") is False:
        return None
    body = raw.get("sample") if isinstance(raw.get("sample"), dict) else raw
    if not isinstance(body, dict):
        return None
    try:
        a, g = body["accel"], body["gyro"]
        return {
            "stamp": float(body.get("stamp") or 0.0),
            "seq": int(body.get("seq") or 0),
            "ax": float(a["x"]),
            "ay": float(a["y"]),
            "az": float(a["z"]),
            "gx": float(g["x"]),
            "gy": float(g["y"]),
            "gz": float(g["z"]),
            "age_ms": float(status.get("sampleAgeMs") or 0.0),
        }
    except (KeyError, TypeError, ValueError):
        return None


def bar(value: float, scale: float, width: int = 24) -> str:
    if scale <= 0:
        return " " * width
    t = max(-1.0, min(1.0, value / scale))
    mid = width // 2
    pos = mid + int(round(t * (mid - 1)))
    chars = [" "] * width
    chars[mid] = "│"
    step = 1 if pos >= mid else -1
    i = mid
    while i != pos:
        i += step
        if 0 <= i < width:
            chars[i] = "█"
    return "".join(chars)


def rest_hint(s: dict) -> str:
    mag = math.hypot(s["ax"], s["ay"], s["az"])
    horiz = math.hypot(s["ax"], s["ay"])
    hints: list[str] = []
    if abs(s["az"]) >= 0.65 * max(abs(s["ax"]), abs(s["ay"]), abs(s["az"]), 1e-6):
        hints.append("gravity on Z ✓")
    elif abs(s["ax"]) >= 0.65:
        hints.append("gravity on X? (tilted or wrong axes)")
    else:
        hints.append("gravity axis unclear")
    if horiz > 0.25:
        hints.append(f"tilt horiz={horiz:.2f}g")
    if abs(s["gz"]) > 0.35:
        hints.append(f"gyro.z bias {s['gz']:+.2f} rad/s")
    if mag < 0.7 or mag > 1.3:
        hints.append(f"|g|={mag:.2f}g")
    return " · ".join(hints)


def main() -> int:
    parser = argparse.ArgumentParser(description="Live Pi IMU viewer")
    parser.add_argument("--url", default=DEFAULT_URL, help="IMU HTTP endpoint")
    parser.add_argument("--hz", type=float, default=15.0, help="poll rate")
    parser.add_argument(
        "--history", type=int, default=6, help="sparkline history length"
    )
    args = parser.parse_args()
    period = 1.0 / max(1.0, args.hz)
    hist: deque[float] = deque(maxlen=max(2, args.history))

    print(f"IMU live  url={args.url}  hz={args.hz}")
    print("Frame: +X forward, +Y left, +Z up (chip flat). Ctrl+C to quit.\n")

    last_seq: int | None = None
    fails = 0
    t0 = time.monotonic()
    n = 0

    try:
        while True:
            raw = fetch(args.url)
            sample = parse_sample(raw)
            if sample is None:
                fails += 1
                sys.stdout.write(
                    f"\r\x1b[2Kfetch failed ({fails}) — is the rover online?\n"
                )
                sys.stdout.flush()
                time.sleep(period)
                continue

            fails = 0
            n += 1
            seq = sample["seq"]
            dseq = "" if last_seq is None else f"  Δseq={seq - last_seq}"
            last_seq = seq
            hist.append(sample["gz"])

            elapsed = time.monotonic() - t0
            rate = n / elapsed if elapsed > 0 else 0.0
            spark = "".join(
                "▁▂▃▄▅▆▇█"[min(7, int(abs(v) * 4))] for v in hist
            )

            lines = [
                f"seq={seq}{dseq}  age={sample['age_ms']:.0f}ms  rate={rate:.1f}Hz",
                "",
                "ACCEL (g)   expected at rest: x≈0  y≈0  z≈±1",
                f"  x fwd  {sample['ax']:+8.4f}  {bar(sample['ax'], 1.2)}",
                f"  y left {sample['ay']:+8.4f}  {bar(sample['ay'], 1.2)}",
                f"  z up   {sample['az']:+8.4f}  {bar(sample['az'], 1.2)}",
                f"  |g|={math.hypot(sample['ax'], sample['ay'], sample['az']):.3f}g",
                "",
                "GYRO (rad/s)   expected at rest: ≈0 on all axes",
                f"  x      {sample['gx']:+8.4f}  {bar(sample['gx'], 2.0)}",
                f"  y      {sample['gy']:+8.4f}  {bar(sample['gy'], 2.0)}",
                f"  z yaw  {sample['gz']:+8.4f}  {bar(sample['gz'], 2.0)}  {spark}",
                f"  z deg/s {math.degrees(sample['gz']):+8.2f}",
                "",
                rest_hint(sample),
            ]

            sys.stdout.write("\x1b[H\x1b[J" + "\n".join(lines) + "\n")
            sys.stdout.flush()
            time.sleep(period)
    except KeyboardInterrupt:
        print("\nStopped.")
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
