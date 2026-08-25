#!/usr/bin/env python3
"""Live forward/back throttle characterization via POST /api/navigation/drive.

Sends proportional drive.x pulses at N throttle levels, alternating F/B so the
rover stays near the start pose. After each pulse waits settle_s (default 3s)
and records body-frame forward offset from SLAM pose.

  python3 scripts/live_characterize_throttle.py
  python3 scripts/live_characterize_throttle.py --levels 10 --on 0.40 --rounds 2
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
            "stamp": float(pose.get("stamp") or 0.0),
        }

    def drive(self, x: float, y: float = 0.0) -> dict:
        return self._req(
            "POST",
            "/api/navigation/drive",
            {"drive": {"x": float(x), "y": float(y)}},
            timeout=2.5,
        )

    def stop(self) -> None:
        try:
            self.drive(0.0, 0.0)
        except Exception as exc:  # noqa: BLE001
            print(f"stop failed: {exc}", file=sys.stderr)

    def cancel_nav(self) -> None:
        try:
            self._req("POST", "/api/navigation/cancel", {}, timeout=3.0)
        except Exception:
            pass


def body_fwd_delta(
    x0: float, y0: float, yaw0: float, x1: float, y1: float
) -> tuple[float, float, float]:
    """Return (forward_m, left_m, hypot_m) of motion in the start body frame."""
    dx = x1 - x0
    dy = y1 - y0
    fwd = dx * math.cos(yaw0) + dy * math.sin(yaw0)
    left = -dx * math.sin(yaw0) + dy * math.cos(yaw0)
    return fwd, left, math.hypot(dx, dy)


def wait_settle(client: RoverClient, settle_s: float, poll_s: float = 0.08) -> dict:
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
    throttle: float,
    on_s: float,
    settle_s: float,
) -> dict:
    """Hold drive.x=throttle for on_s (keepalive posts), then settle."""
    before = wait_settle(client, min(0.5, settle_s * 0.25))
    t_end = time.monotonic() + max(0.05, on_s)
    while time.monotonic() < t_end:
        client.drive(throttle, 0.0)
        time.sleep(0.05)
    client.stop()
    after = wait_settle(client, settle_s)
    fwd, left, hyp = body_fwd_delta(
        before["x"], before["y"], before["yaw"], after["x"], after["y"]
    )
    return {
        "throttle": throttle,
        "dir": "F" if throttle > 0 else "B",
        "on_s": on_s,
        "x0": round(before["x"], 4),
        "y0": round(before["y"], 4),
        "yaw0_deg": round(before["deg"], 2),
        "x1": round(after["x"], 4),
        "y1": round(after["y"], 4),
        "yaw1_deg": round(after["deg"], 2),
        "fwd_m": round(fwd, 4),
        "left_m": round(left, 4),
        "hyp_m": round(hyp, 4),
        "dyaw_deg": round(after["deg"] - before["deg"], 2),
    }


def summarize(rows: list[dict]) -> None:
    print("\n=== per-throttle summary (signed fwd_m; F>0, B<0) ===")
    print(
        f"{'thr':>5} {'dir':>3} {'n':>3} {'mean_fwd':>9} {'std':>7} "
        f"{'mean_|fwd|':>10} {'mean_left':>10} {'mean_|yaw|':>10}"
    )
    by: dict[tuple[float, str], list[dict]] = {}
    for r in rows:
        by.setdefault((abs(r["throttle"]), r["dir"]), []).append(r)
    for key in sorted(by.keys()):
        samples = by[key]
        fwds = [s["fwd_m"] for s in samples]
        lefts = [s["left_m"] for s in samples]
        yaws = [abs(s["dyaw_deg"]) for s in samples]
        mean = sum(fwds) / len(fwds)
        var = sum((x - mean) ** 2 for x in fwds) / max(1, len(fwds) - 1) if len(fwds) > 1 else 0.0
        std = math.sqrt(var)
        mean_abs = sum(abs(x) for x in fwds) / len(fwds)
        mean_left = sum(lefts) / len(lefts)
        mean_yaw = sum(yaws) / len(yaws)
        thr, direction = key
        print(
            f"{thr:5.2f} {direction:>3} {len(samples):3d} {mean:9.4f} {std:7.4f} "
            f"{mean_abs:10.4f} {mean_left:10.4f} {mean_yaw:10.2f}"
        )


def consistency_report(round_rows: list[list[dict]]) -> None:
    if len(round_rows) < 2:
        return
    print("\n=== consistency round1 vs round2 (|fwd| delta) ===")
    print(f"{'thr':>5} {'dir':>3} {'r1_|fwd|':>9} {'r2_|fwd|':>9} {'Δ':>8} {'ratio':>7}")
    r1 = {(abs(r["throttle"]), r["dir"]): r for r in round_rows[0]}
    r2 = {(abs(r["throttle"]), r["dir"]): r for r in round_rows[1]}
    for key in sorted(set(r1) | set(r2)):
        a = r1.get(key)
        b = r2.get(key)
        if not a or not b:
            continue
        a_abs = abs(a["fwd_m"])
        b_abs = abs(b["fwd_m"])
        delta = b_abs - a_abs
        ratio = (b_abs / a_abs) if a_abs > 1e-4 else float("nan")
        thr, direction = key
        print(
            f"{thr:5.2f} {direction:>3} {a_abs:9.4f} {b_abs:9.4f} "
            f"{delta:8.4f} {ratio:7.2f}"
        )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--base",
        default=os.environ.get("RELAY_BASE", "https://127.0.0.1:8787"),
    )
    parser.add_argument("--levels", type=int, default=10, help="Throttle steps 1..N → N/10..1.0")
    parser.add_argument(
        "--on",
        type=float,
        default=0.40,
        help="Hold duration (s) at each throttle",
    )
    parser.add_argument(
        "--settle",
        type=float,
        default=3.0,
        help="Settle / observe after each move (s)",
    )
    parser.add_argument(
        "--rounds",
        type=int,
        default=2,
        help="Full F/B level sweeps (for consistency)",
    )
    parser.add_argument(
        "--out",
        default="/tmp/throttle_characterize.jsonl",
        help="JSONL of every pulse",
    )
    args = parser.parse_args()

    levels = [i / args.levels for i in range(1, args.levels + 1)]
    client = RoverClient(args.base, _load_token())
    atexit.register(client.stop)

    print(
        f"relay={args.base} levels={levels} on={args.on}s settle={args.settle}s "
        f"rounds={args.rounds}"
    )
    client.cancel_nav()
    time.sleep(0.5)
    try:
        p0 = client.pose()
    except Exception as exc:  # noqa: BLE001
        print(f"FAILED to read SLAM pose: {exc}", file=sys.stderr)
        return 2
    print(f"start pose xy=({p0['x']:.3f},{p0['y']:.3f}) yaw={p0['deg']:.1f}°")
    client.stop()
    time.sleep(0.3)

    all_rows: list[dict] = []
    round_rows: list[list[dict]] = []
    out = Path(args.out)
    out.write_text("")

    for rnd in range(1, args.rounds + 1):
        print(f"\n----- ROUND {rnd}/{args.rounds} -----", flush=True)
        this_round: list[dict] = []
        # Alternate F then B at each level so net travel stays near start.
        for thr in levels:
            for sign, label in ((+1.0, "F"), (-1.0, "B")):
                cmd = sign * thr
                print(
                    f"  R{rnd} {label} throttle={thr:.2f} hold={args.on:.2f}s …",
                    flush=True,
                )
                try:
                    row = one_pulse(
                        client,
                        throttle=cmd,
                        on_s=args.on,
                        settle_s=args.settle,
                    )
                except Exception as exc:  # noqa: BLE001
                    client.stop()
                    print(f"  FAILED: {exc}", file=sys.stderr)
                    return 1
                row["round"] = rnd
                this_round.append(row)
                all_rows.append(row)
                with out.open("a") as fh:
                    fh.write(json.dumps(row) + "\n")
                print(
                    f"    → fwd={row['fwd_m']:+.4f}m left={row['left_m']:+.4f}m "
                    f"|xy|={row['hyp_m']:.4f}m Δyaw={row['dyaw_deg']:+.1f}° "
                    f"pose=({row['x1']:.3f},{row['y1']:.3f})",
                    flush=True,
                )
        round_rows.append(this_round)

    summarize(all_rows)
    consistency_report(round_rows)
    print(f"\nraw pulses → {out}")
    p1 = client.pose()
    print(f"end pose xy=({p1['x']:.3f},{p1['y']:.3f}) yaw={p1['deg']:.1f}°")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
