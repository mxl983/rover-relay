#!/usr/bin/env python3
"""Live A/D pulse → yaw characterization on the real rover via relay.

Sends WASD key pulses through POST /api/navigation/drive/keys and measures
Δyaw from GET /api/slam/map pose. Use results to set NAV_TURN_PULSE_* .

  python3 scripts/live_characterize_yaw.py
  python3 scripts/live_characterize_yaw.py --ons 0.05,0.08,0.12 --pulses 2
"""

from __future__ import annotations

import argparse
import atexit
import json
import math
import os
import ssl
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]


def _load_token() -> str:
    env = REPO / ".env"
    if env.is_file():
        for line in env.read_text().splitlines():
            if line.startswith("ROVER_API_TOKEN=") or line.startswith(
                "NAVIGATION_API_TOKEN="
            ):
                return line.split("=", 1)[1].strip().strip('"').strip("'")
    return os.environ.get("ROVER_API_TOKEN") or os.environ.get(
        "NAVIGATION_API_TOKEN", ""
    )


def _ctx() -> ssl.SSLContext:
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    return ctx


class RoverClient:
    def __init__(self, base: str, token: str) -> None:
        self.base = base.rstrip("/")
        self.token = token
        self._ctx = _ctx()

    def _req(self, method: str, path: str, body: dict | None = None, timeout: float = 3.0):
        data = None if body is None else json.dumps(body).encode()
        headers = {"Accept": "application/json"}
        if body is not None:
            headers["Content-Type"] = "application/json"
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        req = urllib.request.Request(
            self.base + path, data=data, headers=headers, method=method
        )
        with urllib.request.urlopen(req, context=self._ctx, timeout=timeout) as resp:
            return json.loads(resp.read().decode())

    def pose(self) -> dict:
        data = self._req("GET", "/api/slam/map", timeout=4.0)
        pose = data.get("pose") or {}
        yaw = float(pose.get("yaw", 0.0))
        return {
            "x": float(pose.get("x", 0.0)),
            "y": float(pose.get("y", 0.0)),
            "yaw": yaw,
            "deg": math.degrees(yaw),
            "stamp": float(pose.get("stamp") or pose.get("stamp") or 0.0),
            "raw": data,
        }

    def keys(self, keys: list[str]) -> dict:
        return self._req("POST", "/api/navigation/drive/keys", {"keys": keys}, timeout=2.5)

    def stop(self) -> None:
        try:
            self.keys([])
        except Exception as exc:  # noqa: BLE001
            print(f"stop failed: {exc}", file=sys.stderr)


def wrap_delta(a: float, b: float) -> float:
    """Signed shortest yaw delta a→b (rad)."""
    return math.atan2(math.sin(b - a), math.cos(b - a))


def wait_pose_stable(client: RoverClient, settle_s: float, poll_s: float = 0.05) -> dict:
    """Hold stop and sample pose after settle_s (lets lidar/SLAM catch up)."""
    client.stop()
    deadline = time.monotonic() + settle_s
    last = client.pose()
    while time.monotonic() < deadline:
        time.sleep(poll_s)
        try:
            last = client.pose()
        except Exception:
            pass
    return last


def one_pulse(
    client: RoverClient,
    *,
    turn_key: str,
    on_s: float,
    settle_s: float,
) -> dict:
    before = wait_pose_stable(client, min(0.4, settle_s * 0.5))
    stamp0 = before["stamp"]
    # Pi teleop treats each POST as a short command — re-assert while "holding".
    t_end = time.monotonic() + max(0.03, on_s)
    while time.monotonic() < t_end:
        client.keys([turn_key])
        time.sleep(0.05)
    client.stop()
    # Wait until SLAM stamp advances (or settle timeout).
    deadline = time.monotonic() + max(settle_s, 0.8)
    after = before
    while time.monotonic() < deadline:
        time.sleep(0.05)
        try:
            after = client.pose()
            if after["stamp"] > stamp0 + 0.05:
                # small extra settle after first fresh pose
                time.sleep(min(0.35, settle_s * 0.4))
                after = client.pose()
                break
        except Exception:
            pass
    dyaw = wrap_delta(before["yaw"], after["yaw"])
    return {
        "on_s": on_s,
        "key": turn_key,
        "yaw0_deg": before["deg"],
        "yaw1_deg": after["deg"],
        "dyaw_deg": math.degrees(dyaw),
        "abs_deg": abs(math.degrees(dyaw)),
        "stamp0": stamp0,
        "stamp1": after["stamp"],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--base",
        default=os.environ.get("RELAY_BASE", "https://127.0.0.1:8787"),
    )
    parser.add_argument("--key", default="a", choices=("a", "d"))
    parser.add_argument(
        "--ons",
        default="0.05,0.08,0.10,0.12,0.15",
        help="Comma-separated pulse on durations (seconds)",
    )
    parser.add_argument("--pulses", type=int, default=2, help="Repeats per on duration")
    parser.add_argument("--settle", type=float, default=0.7, help="Settle after each pulse")
    parser.add_argument(
        "--max-total-deg",
        type=float,
        default=100.0,
        help="Abort if cumulative |Δyaw| exceeds this",
    )
    args = parser.parse_args()

    ons = [float(x) for x in args.ons.split(",") if x.strip()]
    client = RoverClient(args.base, _load_token())
    atexit.register(client.stop)

    print(f"relay={args.base} key={args.key} ons={ons} pulses/on={args.pulses}")
    try:
        p0 = client.pose()
    except Exception as exc:  # noqa: BLE001
        print(f"FAILED to read SLAM pose: {exc}", file=sys.stderr)
        return 2
    print(f"start pose yaw={p0['deg']:.2f}°  xy=({p0['x']:.2f},{p0['y']:.2f})")
    client.stop()
    time.sleep(0.3)

    rows: list[dict] = []
    total = 0.0
    try:
        for on_s in ons:
            samples: list[float] = []
            for i in range(args.pulses):
                if abs(total) >= args.max_total_deg:
                    print(f"hit max-total-deg={args.max_total_deg}; stopping")
                    break
                r = one_pulse(
                    client, turn_key=args.key, on_s=on_s, settle_s=args.settle
                )
                samples.append(r["abs_deg"])
                total += r["dyaw_deg"]
                sign = "+" if r["dyaw_deg"] >= 0 else ""
                print(
                    f"  on={on_s:.3f}s #{i+1}: {sign}{r['dyaw_deg']:.2f}°  "
                    f"({r['yaw0_deg']:.1f}→{r['yaw1_deg']:.1f})"
                )
                rows.append(r)
            if samples:
                mean = sum(samples) / len(samples)
                rate = mean / on_s if on_s > 1e-6 else 0.0
                print(
                    f"→ on={on_s:.3f}s  mean |Δyaw|={mean:.2f}°/pulse  "
                    f"implied ω≈{rate:.1f}°/s"
                )
    finally:
        client.stop()

    # Summarize + suggest pulse timings for ~10° and ~15° steps
    by_on: dict[float, list[float]] = {}
    for r in rows:
        by_on.setdefault(r["on_s"], []).append(r["abs_deg"])
    if not by_on:
        print("no samples")
        return 1

    print("\n=== summary ===")
    print(f"{'on':>6} {'mean°':>7} {'ω°/s':>7} {'n':>3}")
    rates = []
    for on_s in sorted(by_on):
        xs = by_on[on_s]
        mean = sum(xs) / len(xs)
        rate = mean / on_s
        rates.append(rate)
        print(f"{on_s:6.3f} {mean:7.2f} {rate:7.1f} {len(xs):3d}")
    omega = sum(rates) / len(rates)
    print(f"\nmean implied continuous yaw rate ≈ {omega:.1f}°/s")

    def on_for(target_deg: float) -> float:
        return max(0.04, min(0.20, target_deg / max(omega, 1.0)))

    sug_fine = on_for(10.0)
    sug_coarse = on_for(18.0)
    print(
        "\nSuggested NAV_TURN_PULSE_ON_PURE_SEC:\n"
        f"  ~10°/pulse → {sug_fine:.3f}s  (off ≈ {sug_fine * 1.5:.3f}s)\n"
        f"  ~18°/pulse → {sug_coarse:.3f}s  (off ≈ {sug_coarse:.3f}s)\n"
        f"  current compose default still 0.12s → ~{0.12 * omega:.1f}°/pulse at this rate"
    )

    out = REPO / "scripts" / "live_yaw_pulse_results.json"
    out.write_text(
        json.dumps(
            {
                "omega_deg_s": omega,
                "rows": rows,
                "suggest_on_10deg": sug_fine,
                "suggest_on_18deg": sug_coarse,
            },
            indent=2,
        )
        + "\n"
    )
    print(f"wrote {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
