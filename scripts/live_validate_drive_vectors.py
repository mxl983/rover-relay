#!/usr/bin/env python3
"""Validate Pi analog drive vectors against SLAM pose/yaw.

Sends short holds of {x,y} stick commands via POST /api/navigation/drive,
settles, and measures body-frame forward/left + yaw change.

Expected teleop convention (user characterization):
  ( 0, -y)  → forward   (+body fwd)
  ( 0, +y)  → reverse   (−body fwd)  [optional; may be prohibited]
  (−x,  0)  → left/CCW  (+yaw)
  (+x,  0)  → right/CW  (−yaw)

  python3 scripts/live_validate_drive_vectors.py
  python3 scripts/live_validate_drive_vectors.py --on 0.35 --settle 3.0
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

    def drive(self, x: float, y: float) -> dict:
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


def wrap_deg(d: float) -> float:
    while d > 180.0:
        d -= 360.0
    while d < -180.0:
        d += 360.0
    return d


def body_delta(
    x0: float, y0: float, yaw0: float, x1: float, y1: float
) -> tuple[float, float, float]:
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


def hold_vector(
    client: RoverClient,
    *,
    name: str,
    x: float,
    y: float,
    on_s: float,
    settle_s: float,
) -> dict:
    before = wait_settle(client, min(0.6, settle_s * 0.25))
    t_end = time.monotonic() + max(0.05, on_s)
    posts = 0
    while time.monotonic() < t_end:
        client.drive(x, y)
        posts += 1
        time.sleep(0.05)
    client.stop()
    after = wait_settle(client, settle_s)
    fwd, left, hyp = body_delta(
        before["x"], before["y"], before["yaw"], after["x"], after["y"]
    )
    dyaw = wrap_deg(after["deg"] - before["deg"])
    return {
        "name": name,
        "cmd_x": x,
        "cmd_y": y,
        "on_s": on_s,
        "posts": posts,
        "x0": round(before["x"], 4),
        "y0": round(before["y"], 4),
        "yaw0_deg": round(before["deg"], 2),
        "x1": round(after["x"], 4),
        "y1": round(after["y"], 4),
        "yaw1_deg": round(after["deg"], 2),
        "fwd_m": round(fwd, 4),
        "left_m": round(left, 4),
        "hyp_m": round(hyp, 4),
        "dyaw_deg": round(dyaw, 2),
    }


def judge(row: dict) -> tuple[str, str]:
    """Return (PASS|FAIL|WARN, reason) for expected sense of each named vector."""
    name = row["name"]
    fwd = row["fwd_m"]
    left = row["left_m"]
    dyaw = row["dyaw_deg"]
    hyp = row["hyp_m"]

    if name.startswith("forward"):
        if fwd > 0.03 and abs(dyaw) < 25 and abs(left) < max(0.08, 0.5 * abs(fwd)):
            return "PASS", f"moved forward {fwd:+.3f}m"
        if fwd < -0.02:
            return "FAIL", f"moved BACKWARD {fwd:+.3f}m (y sign flipped?)"
        if hyp < 0.02:
            return "WARN", f"almost no motion (stall?) fwd={fwd:+.3f}m"
        return "WARN", f"weak/noisy forward fwd={fwd:+.3f} left={left:+.3f} yaw={dyaw:+.1f}"

    if name.startswith("reverse"):
        if fwd < -0.03 and abs(dyaw) < 25:
            return "PASS", f"moved reverse {fwd:+.3f}m"
        if fwd > 0.02:
            return "FAIL", f"moved FORWARD {fwd:+.3f}m (reverse y flipped?)"
        return "WARN", f"weak reverse fwd={fwd:+.3f}m"

    if name.startswith("left"):
        # +yaw = CCW = left for our body frame
        if dyaw > 8.0:
            return "PASS", f"yaw CCW/left {dyaw:+.1f}°"
        if dyaw < -8.0:
            return "FAIL", f"yaw CW/right {dyaw:+.1f}° (L/R flipped!)"
        return "WARN", f"little yaw change {dyaw:+.1f}°"

    if name.startswith("right"):
        if dyaw < -8.0:
            return "PASS", f"yaw CW/right {dyaw:+.1f}°"
        if dyaw > 8.0:
            return "FAIL", f"yaw CCW/left {dyaw:+.1f}° (L/R flipped!)"
        return "WARN", f"little yaw change {dyaw:+.1f}°"

    if name.startswith("forward_left"):
        if fwd > 0.02 and dyaw > 5.0:
            return "PASS", f"arc FL fwd={fwd:+.3f} yaw={dyaw:+.1f}"
        if fwd > 0.02 and dyaw < -5.0:
            return "FAIL", f"arc went right instead of left (yaw={dyaw:+.1f})"
        return "WARN", f"ambiguous arc fwd={fwd:+.3f} yaw={dyaw:+.1f}"

    if name.startswith("forward_right"):
        if fwd > 0.02 and dyaw < -5.0:
            return "PASS", f"arc FR fwd={fwd:+.3f} yaw={dyaw:+.1f}"
        if fwd > 0.02 and dyaw > 5.0:
            return "FAIL", f"arc went left instead of right (yaw={dyaw:+.1f})"
        return "WARN", f"ambiguous arc fwd={fwd:+.3f} yaw={dyaw:+.1f}"

    return "WARN", "no rule"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--base",
        default=os.environ.get("RELAY_BASE", "https://127.0.0.1:8787"),
    )
    parser.add_argument("--on", type=float, default=0.40, help="Hold each vector (s)")
    parser.add_argument("--settle", type=float, default=3.0, help="Settle after each (s)")
    parser.add_argument(
        "--mag",
        type=float,
        default=0.6,
        help="Stick magnitude for pure axes (user: 0.6≈30cm/s fwd, ~30° turn scale)",
    )
    parser.add_argument(
        "--include-reverse",
        action="store_true",
        help="Also test reverse (y>0). Default off — no rear lidar.",
    )
    parser.add_argument(
        "--out",
        default="/tmp/drive_vector_validate.jsonl",
    )
    args = parser.parse_args()

    mag = max(0.2, min(1.0, args.mag))
    # Pure axes + mild arcs. Order chosen to roughly undo translation then yaw.
    cases: list[tuple[str, float, float]] = [
        ("forward", 0.0, -mag),
        ("left", -mag, 0.0),
        ("right", mag, 0.0),
        ("forward_left", -0.45, -mag),
        ("forward_right", 0.45, -mag),
        ("forward_undo", 0.0, -mag),  # second forward sample
    ]
    if args.include_reverse:
        cases.insert(1, ("reverse", 0.0, mag))

    client = RoverClient(args.base, _load_token())
    atexit.register(client.stop)

    print(
        f"relay={args.base} mag={mag} on={args.on}s settle={args.settle}s "
        f"cases={[c[0] for c in cases]}"
    )
    client.cancel_nav()
    time.sleep(0.5)
    start = client.pose()
    print(
        f"start pose xy=({start['x']:.3f},{start['y']:.3f}) yaw={start['deg']:.1f}°"
    )
    print()

    rows: list[dict] = []
    fails = 0
    warns = 0
    with open(args.out, "w", encoding="utf-8") as out:
        for name, x, y in cases:
            print(f"  {name:14s} stick=({x:+.2f},{y:+.2f}) hold={args.on:.2f}s …", flush=True)
            try:
                row = hold_vector(
                    client,
                    name=name,
                    x=x,
                    y=y,
                    on_s=args.on,
                    settle_s=args.settle,
                )
            except Exception as exc:  # noqa: BLE001
                print(f"    ERROR: {exc}")
                client.stop()
                return 2
            verdict, reason = judge(row)
            row["verdict"] = verdict
            row["reason"] = reason
            rows.append(row)
            out.write(json.dumps(row) + "\n")
            out.flush()
            mark = {"PASS": "✓", "FAIL": "✗", "WARN": "!"}.get(verdict, "?")
            print(
                f"    {mark} {verdict}: {reason}\n"
                f"      Δ fwd={row['fwd_m']:+.4f}m left={row['left_m']:+.4f}m "
                f"|xy|={row['hyp_m']:.4f}m Δyaw={row['dyaw_deg']:+.1f}° "
                f"pose=({row['x1']:.3f},{row['y1']:.3f})"
            )
            if verdict == "FAIL":
                fails += 1
            elif verdict == "WARN":
                warns += 1

    print("\n=== summary ===")
    print(
        f"{'name':14s} {'x':>5} {'y':>5} {'fwd_m':>8} {'left_m':>8} "
        f"{'dyaw':>7} {'verdict':>7} reason"
    )
    for r in rows:
        print(
            f"{r['name']:14s} {r['cmd_x']:+5.2f} {r['cmd_y']:+5.2f} "
            f"{r['fwd_m']:+8.4f} {r['left_m']:+8.4f} {r['dyaw_deg']:+7.1f} "
            f"{r['verdict']:>7} {r['reason']}"
        )

    end = client.pose()
    print(
        f"\nend pose xy=({end['x']:.3f},{end['y']:.3f}) yaw={end['deg']:.1f}° "
        f"(start was {start['deg']:.1f}°)"
    )
    print(f"wrote {args.out}")
    if fails:
        print(f"RESULT: FAIL ({fails} flipped/wrong sense, {warns} warn)")
        return 1
    if warns:
        print(f"RESULT: OK with warnings ({warns})")
        return 0
    print("RESULT: ALL PASS — vector senses match expected convention")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
