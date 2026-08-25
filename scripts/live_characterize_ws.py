#!/usr/bin/env python3
"""Live W/S hold-time characterization via POST /api/navigation/drive/keys.

Only sends W or S (never A/D). Ten hold durations from micro-adjust to larger
moves; alternates W then S so net travel stays near the start. Settles after
each pulse and records body-frame forward offset from SLAM.

  python3 scripts/live_characterize_ws.py
  python3 scripts/live_characterize_ws.py --rounds 2 --settle 3.0
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

# 10 hold times. Skip ultra-short (<~RTT) — those can't be timed over HTTP.
DEFAULT_ONS = "0.15,0.20,0.25,0.30,0.35,0.40,0.50,0.60,0.80,1.00"


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

    def keys(self, keys: list[str]) -> dict:
        # Hard guard: never A/D in this harness.
        bad = [k for k in keys if k not in ("w", "s")]
        if bad:
            raise ValueError(f"refusing non W/S keys: {bad}")
        return self._req(
            "POST", "/api/navigation/drive/keys", {"keys": list(keys)}, timeout=2.5
        )

    def stop(self) -> None:
        try:
            self._req("POST", "/api/navigation/drive/keys", {"keys": []}, timeout=2.5)
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
    key: str,
    on_s: float,
    settle_s: float,
) -> dict:
    """Latch W or S once, sleep exact on_s, then clear keys.

    Pi keyboard keys *latch* (dashboard: DualJoystickControls) — analog needs
    keepalive, keys do not. The old loop (POST every 50ms for on_s) made short
    holds ≈ one HTTP RTT (~50–150ms) regardless of requested on_s, so distance
    vs hold looked random.
    """
    if key not in ("w", "s"):
        raise ValueError(f"key must be w or s, got {key!r}")
    before = wait_settle(client, min(0.5, settle_s * 0.2))
    # Latch once → sleep exact on_s → clear. Do NOT spam POSTs (that was the bug).
    # Note: HTTP RTT (~50–150ms) still pads every pulse; sub‑0.15s holds are noisy.
    t0 = time.monotonic()
    client.keys([key])
    latch_rtt = time.monotonic() - t0
    time.sleep(max(0.0, on_s))
    t_stop0 = time.monotonic()
    client.stop()
    stop_rtt = time.monotonic() - t_stop0
    held_s = time.monotonic() - t0
    after = wait_settle(client, settle_s)
    fwd, left, hyp = body_fwd_delta(
        before["x"], before["y"], before["yaw"], after["x"], after["y"]
    )
    # Forward along start heading: W should be +, S should be −.
    return {
        "key": key,
        "on_s": on_s,
        "held_s": round(held_s, 4),
        "latch_rtt_s": round(latch_rtt, 4),
        "stop_rtt_s": round(stop_rtt, 4),
        "x0": round(before["x"], 4),
        "y0": round(before["y"], 4),
        "yaw0_deg": round(before["deg"], 2),
        "x1": round(after["x"], 4),
        "y1": round(after["y"], 4),
        "yaw1_deg": round(after["deg"], 2),
        "fwd_m": round(fwd, 4),
        "left_m": round(left, 4),
        "hyp_m": round(hyp, 4),
        "dyaw_deg": round(
            math.degrees(
                math.atan2(
                    math.sin(after["yaw"] - before["yaw"]),
                    math.cos(after["yaw"] - before["yaw"]),
                )
            ),
            2,
        ),
    }


def summarize(rows: list[dict]) -> None:
    print("\n=== hold-time → offset (body fwd_m; W≈+, S≈−) ===")
    print(
        f"{'on_s':>6} {'key':>3} {'n':>3} {'mean_fwd':>9} {'std':>7} "
        f"{'mean_|fwd|':>10} {'mean_|left|':>11} {'mean_|yaw|':>10}"
    )
    by: dict[tuple[float, str], list[dict]] = {}
    for r in rows:
        by.setdefault((r["on_s"], r["key"]), []).append(r)
    for key in sorted(by.keys()):
        samples = by[key]
        fwds = [s["fwd_m"] for s in samples]
        lefts = [abs(s["left_m"]) for s in samples]
        yaws = [abs(s["dyaw_deg"]) for s in samples]
        mean = sum(fwds) / len(fwds)
        var = (
            sum((x - mean) ** 2 for x in fwds) / max(1, len(fwds) - 1)
            if len(fwds) > 1
            else 0.0
        )
        std = math.sqrt(var)
        mean_abs = sum(abs(x) for x in fwds) / len(fwds)
        mean_left = sum(lefts) / len(lefts)
        mean_yaw = sum(yaws) / len(yaws)
        on_s, k = key
        print(
            f"{on_s:6.3f} {k.upper():>3} {len(samples):3d} {mean:9.4f} {std:7.4f} "
            f"{mean_abs:10.4f} {mean_left:11.4f} {mean_yaw:10.2f}"
        )


def consistency_report(round_rows: list[list[dict]]) -> None:
    if len(round_rows) < 2:
        return
    print("\n=== consistency round1 vs round2 (|fwd| Δ) ===")
    print(f"{'on_s':>6} {'key':>3} {'r1':>8} {'r2':>8} {'Δ':>8} {'ratio':>7}")
    r1 = {(r["on_s"], r["key"]): r for r in round_rows[0]}
    r2 = {(r["on_s"], r["key"]): r for r in round_rows[1]}
    for key in sorted(set(r1) | set(r2)):
        a, b = r1.get(key), r2.get(key)
        if not a or not b:
            continue
        a_abs, b_abs = abs(a["fwd_m"]), abs(b["fwd_m"])
        delta = b_abs - a_abs
        ratio = (b_abs / a_abs) if a_abs > 1e-4 else float("nan")
        on_s, k = key
        print(
            f"{on_s:6.3f} {k.upper():>3} {a_abs:8.4f} {b_abs:8.4f} "
            f"{delta:8.4f} {ratio:7.2f}"
        )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--base",
        default=os.environ.get("RELAY_BASE", "https://127.0.0.1:8787"),
    )
    parser.add_argument("--ons", default=DEFAULT_ONS, help="Comma-separated hold times (s)")
    parser.add_argument("--settle", type=float, default=3.0)
    parser.add_argument("--rounds", type=int, default=2)
    parser.add_argument("--out", default="/tmp/ws_characterize.jsonl")
    args = parser.parse_args()

    ons = [float(x) for x in args.ons.split(",") if x.strip()]
    if len(ons) != 10:
        print(f"warning: expected 10 levels, got {len(ons)}: {ons}", file=sys.stderr)

    client = RoverClient(args.base, _load_token())
    atexit.register(client.stop)

    # Drop any prior table from the proportional-throttle run.
    for stale in (
        Path("/tmp/throttle_characterize.jsonl"),
        Path("/tmp/throttle_characterize.log"),
        Path(args.out),
    ):
        try:
            stale.unlink()
        except FileNotFoundError:
            pass

    print(
        f"relay={args.base} keys=W/S only ons={ons} settle={args.settle}s "
        f"rounds={args.rounds}"
    )
    client.cancel_nav()
    time.sleep(0.4)
    try:
        p0 = client.pose()
    except Exception as exc:  # noqa: BLE001
        print(f"FAILED pose: {exc}", file=sys.stderr)
        return 2
    print(f"start xy=({p0['x']:.3f},{p0['y']:.3f}) yaw={p0['deg']:.1f}°")
    client.stop()
    time.sleep(0.3)

    all_rows: list[dict] = []
    round_rows: list[list[dict]] = []
    out = Path(args.out)

    for rnd in range(1, args.rounds + 1):
        print(f"\n----- ROUND {rnd}/{args.rounds} -----", flush=True)
        this_round: list[dict] = []
        for on_s in ons:
            for key in ("w", "s"):  # F then B — stay clear of walls
                print(f"  R{rnd} {key.upper()} hold={on_s:.3f}s …", flush=True)
                try:
                    row = one_pulse(
                        client, key=key, on_s=on_s, settle_s=args.settle
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
                    f"Δyaw={row['dyaw_deg']:+.1f}° "
                    f"held={row['held_s']:.3f}s (rtt latch={row['latch_rtt_s']:.3f} "
                    f"stop={row['stop_rtt_s']:.3f}) "
                    f"pose=({row['x1']:.3f},{row['y1']:.3f})",
                    flush=True,
                )
        round_rows.append(this_round)

    summarize(all_rows)
    consistency_report(round_rows)
    print(f"\nraw → {out}")
    p1 = client.pose()
    print(f"end xy=({p1['x']:.3f},{p1['y']:.3f}) yaw={p1['deg']:.1f}°")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
