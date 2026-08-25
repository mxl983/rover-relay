#!/usr/bin/env python3
"""Round-trip nav harness: goto waypoint, wait, analyze run log."""

from __future__ import annotations

import argparse
import json
import math
import ssl
import sys
import time
import urllib.error
import urllib.request
from collections import Counter
from pathlib import Path

TOKEN = "398bfea3682be769e93d28498a0fc388796127d35598c23757c15be704bfb4b7"
BASE = "https://127.0.0.1:8787"

# Dashboard letters by waypoint list order
WP = {
    "A": "83569044-9c96-4a2b-b115-30504069d237",  # mark-2
    "B": "da89c475-390c-4fa2-80fa-ff3336fcbb71",  # mark-3 (2.659, -3.96)
    "C": "1348c762-579f-46c0-af74-f4f8035bf7ce",  # mark (2.006, -0.619)
}


def ctx() -> ssl.SSLContext:
    c = ssl.create_default_context()
    c.check_hostname = False
    c.verify_mode = ssl.CERT_NONE
    return c


class Client:
    def __init__(self) -> None:
        self._ctx = ctx()

    def req(self, method: str, path: str, body: dict | None = None, timeout: float = 8.0):
        data = None if body is None else json.dumps(body).encode()
        headers = {"Accept": "application/json", "Authorization": f"Bearer {TOKEN}"}
        if body is not None:
            headers["Content-Type"] = "application/json"
        r = urllib.request.Request(BASE + path, data=data, headers=headers, method=method)
        with urllib.request.urlopen(r, context=self._ctx, timeout=timeout) as resp:
            return json.loads(resp.read().decode())

    def status(self) -> dict:
        return self.req("GET", "/api/navigation/status")

    def pose(self) -> dict:
        m = self.req("GET", "/api/slam/map", timeout=5)
        p = m.get("pose") or {}
        return {
            "x": float(p.get("x", 0)),
            "y": float(p.get("y", 0)),
            "yaw": float(p.get("yaw", 0)),
            "yaw_deg": float(p.get("theta_deg", math.degrees(float(p.get("yaw", 0))))),
        }

    def goto(self, letter: str, fine_docking: bool = True) -> dict:
        wid = WP[letter]
        return self.req(
            "POST",
            f"/api/navigation/goto/{wid}",
            {"fine_docking": fine_docking},
            timeout=10,
        )

    def cancel(self) -> dict:
        try:
            return self.req("POST", "/api/navigation/cancel", {})
        except Exception as e:  # noqa: BLE001
            return {"error": str(e)}


def docker_cp_run(dest: Path) -> None:
    import subprocess

    subprocess.run(
        ["docker", "cp", "relay-relay-1:/app/lidar/navigation_run.jsonl", str(dest)],
        check=False,
        capture_output=True,
    )


def yaw_deg(p: dict) -> float:
    if "yaw_deg" in p:
        return float(p["yaw_deg"])
    return math.degrees(float(p.get("yaw", 0)))


def analyze_run(nav_id: str, run_path: Path, goal_xy: tuple[float, float], goal_yaw: float) -> dict:
    rows = []
    for line in run_path.read_text().splitlines():
        try:
            r = json.loads(line)
        except json.JSONDecodeError:
            continue
        if r.get("nav_id") == nav_id:
            rows.append(r)
    if not rows:
        # fallback: last contiguous block with this nav in goal feedback
        return {"ok": False, "error": "no run rows", "nav_id": nav_id, "n": 0}

    issues: list[str] = []
    notes = Counter()
    phases = Counter()
    yaw_gaps: list[float] = []
    large_align = 0
    sign_flips = 0
    prev_gap = None
    replans = 0
    pulses_align = 0
    max_yaw_span_window = 0.0
    yaws: list[float] = []
    events = Counter()

    for r in rows:
        ev = r.get("event")
        if ev:
            events[str(ev)] += 1
        ui = ((r.get("drive") or {}).get("nav_ui")) or {}
        note = ui.get("note") or ""
        if note:
            notes[note] += 1
            if "replan" in note.lower() or "replan" in str(ui.get("segment_phase", "")):
                replans += 1
            if "align large" in note:
                large_align += 1
            if "skip micro" in note:
                pass
        sp = ui.get("segment_phase") or ui.get("phase")
        if sp is not None:
            phases[str(sp)] += 1
        yg = ui.get("yaw_gap_deg")
        if yg is not None:
            yaw_gaps.append(float(yg))
            if prev_gap is not None and prev_gap * float(yg) < 0 and abs(prev_gap) > 25 and abs(float(yg)) > 25:
                sign_flips += 1
            prev_gap = float(yg)
        pose = r.get("pose") or {}
        if pose:
            yaws.append(yaw_deg(pose))

    # yaw oscillation: max span in any 30s window of samples
    if len(yaws) >= 4:
        # rows are sparse (~2-3s); use all for span
        max_yaw_span_window = max(yaws) - min(yaws)

    last = rows[-1]
    first = rows[0]
    elapsed = last.get("elapsed_s")
    if elapsed is None and first.get("ts") and last.get("ts"):
        elapsed = last["ts"] - first["ts"]
    final_pose = last.get("pose") or {}
    fx = float(final_pose.get("x", 0))
    fy = float(final_pose.get("y", 0))
    fyaw = yaw_deg(final_pose) if final_pose else 0.0
    dist = math.hypot(fx - goal_xy[0], fy - goal_xy[1])
    yaw_err = abs((fyaw - math.degrees(goal_yaw) + 180) % 360 - 180)

    result = last.get("result") or (last.get("goal") or {}).get("result")
    status = last.get("status") or (last.get("goal") or {}).get("status")
    # scan events
    for r in rows:
        if r.get("event") == "dock_finished":
            result = r.get("result") or result
        if r.get("event") in ("succeeded", "arrived", "dock_succeeded", "failed", "canceled", "stall"):
            if r.get("event") != "stall":
                result = result or r.get("event")
        if r.get("event") == "replan" or str(r.get("event", "")).startswith("replan"):
            replans += 1

    if result == "succeeded":
        ok_result = True
    elif result in ("failed", "canceled", "aborted", "docking_timeout", "timeout"):
        ok_result = False
        issues.append(f"result={result}")
    else:
        ok_result = dist <= 0.25 and yaw_err <= 25
        if not ok_result:
            issues.append(f"no clear success; result={result} dist={dist:.3f} yaw_err={yaw_err:.1f}")

    if dist > 0.25:
        issues.append(f"ending XY error {dist:.3f}m (want ≤0.25)")
    if yaw_err > 20:
        issues.append(f"ending yaw error {yaw_err:.1f}° (want ≤20)")
    if sign_flips >= 2:
        issues.append(f"align yaw_gap sign flips={sign_flips} (spin spiral)")
    if large_align >= 12:
        issues.append(f"many large align pulses ({large_align})")
    # Long paths legitimately turn ~90–160°; only flag full spins.
    if max_yaw_span_window > 220:
        issues.append(f"yaw wandered {max_yaw_span_window:.0f}° over run (unnecessary rotate?)")
    if replans >= 3:
        issues.append(f"replans={replans}")
    if elapsed and elapsed > 300:
        issues.append(f"slow leg {elapsed:.0f}s")

    # Hard fail conditions for round-trip gate.
    hard = [
        i
        for i in issues
        if i.startswith("ending XY")
        or i.startswith("ending yaw")
        or i.startswith("result=")
        or i.startswith("align yaw")
        or i.startswith("api ")
        or i.startswith("no clear")
    ]
    soft_ok = ok_result and not hard
    # Still report soft issues but allow continue if arrival is good.
    return {
        "ok": soft_ok,
        "soft_issues": [i for i in issues if i not in hard],
        "nav_id": nav_id,
        "n": len(rows),
        "elapsed_s": elapsed,
        "dist_m": round(dist, 3),
        "yaw_err_deg": round(yaw_err, 1),
        "final_pose": {"x": fx, "y": fy, "yaw_deg": round(fyaw, 1)},
        "result": result,
        "events": dict(events),
        "sign_flips": sign_flips,
        "large_align": large_align,
        "replans": replans,
        "yaw_span_deg": round(max_yaw_span_window, 1),
        "top_notes": notes.most_common(8),
        "issues": issues,
    }


def wait_leg(client: Client, nav_id: str, timeout_s: float = 420.0) -> dict:
    t0 = time.time()
    last_print = 0.0
    saw_navigating = False
    while time.time() - t0 < timeout_s:
        st = client.status()
        goal = st.get("goal") or {}
        status = goal.get("status")
        result = goal.get("result")
        fb = goal.get("feedback") or {}
        ui = ((st.get("drive") or {}).get("nav_ui")) or {}
        gid = goal.get("nav_id")
        now = time.time()
        if status == "navigating" and gid == nav_id:
            saw_navigating = True
        if now - last_print > 8:
            last_print = now
            print(
                f"  … {status}/{result} elapsed={now-t0:.0f}s dist={fb.get('distance_remaining')} "
                f"seg={ui.get('segment')}/{ui.get('segments_total')} "
                f"phase={ui.get('segment_phase') or ui.get('dock_phase') or ui.get('phase')} "
                f"note={ui.get('note')}",
                flush=True,
            )
        # Finished: status returns to idle with a result after our nav.
        if saw_navigating and status == "idle" and (gid in (None, nav_id) or result):
            return goal
        if status == "idle" and result and gid == nav_id:
            return goal
        if status in ("failed", "canceled") and gid == nav_id:
            return goal
        time.sleep(1.0)
    client.cancel()
    return {"status": "timeout", "nav_id": nav_id}


def run_leg(client: Client, dest: str, run_cache: Path) -> dict:
    # waypoint coords
    wps = {
        "B": (2.659, -3.96, -0.0606),
        "C": (2.006, -0.619, 1.1645),
    }
    gx, gy, gyaw = wps[dest]
    pose0 = client.pose()
    print(f"\n=== GOTO {dest} from ({pose0['x']:.2f},{pose0['y']:.2f},{pose0['yaw_deg']:.0f}°) ===", flush=True)
    resp = client.goto(dest)
    nav_id = resp.get("nav_id") or (resp.get("status") if False else None)
    # status may nest
    if not nav_id:
        # enqueue returns nav_id in body
        nav_id = resp.get("command", {}).get("nav_id") if isinstance(resp.get("command"), dict) else None
    if not nav_id:
        # read from goal file via status
        time.sleep(1)
        st = client.status()
        nav_id = (st.get("goal") or {}).get("nav_id")
    print(f"  nav_id={nav_id} resp_keys={list(resp.keys())} success={resp.get('success')}", flush=True)
    if resp.get("success") is False:
        return {"ok": False, "error": resp, "dest": dest}
    goal = wait_leg(client, str(nav_id))
    time.sleep(1.5)  # let final log rows flush
    docker_cp_run(run_cache)
    report = analyze_run(str(nav_id), run_cache, (gx, gy), gyaw)
    pose1 = client.pose()
    report["dest"] = dest
    report["api_status"] = goal.get("status")
    report["api_result"] = goal.get("result")
    report["pose_end_live"] = pose1
    # Prefer live pose for ending error
    dist = math.hypot(pose1["x"] - gx, pose1["y"] - gy)
    yaw_err = abs((pose1["yaw_deg"] - math.degrees(gyaw) + 180) % 360 - 180)
    report["dist_m"] = round(dist, 3)
    report["yaw_err_deg"] = round(yaw_err, 1)
    if dist > 0.25:
        if f"ending XY error" not in " ".join(report["issues"]):
            report["issues"].append(f"ending XY error {dist:.3f}m (want ≤0.25)")
        report["ok"] = False
    if yaw_err > 20:
        report["issues"].append(f"ending yaw error {yaw_err:.1f}° (want ≤20)")
        report["ok"] = False
    if goal.get("status") == "succeeded" or goal.get("result") in ("succeeded", "dock_succeeded"):
        pass
    elif goal.get("status") in ("failed", "canceled", "timeout"):
        report["ok"] = False
        report["issues"].append(f"api {goal.get('status')}/{goal.get('result')}")
    print(json.dumps({k: report[k] for k in report if k != "top_notes"}, indent=2))
    print("  top_notes:", report.get("top_notes"))
    return report


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("legs", nargs="+", help="e.g. B C B C")
    ap.add_argument("--out", default="/tmp/nav_roundtrip_report.jsonl")
    args = ap.parse_args()
    client = Client()
    run_cache = Path("/tmp/navigation_run_rt.jsonl")
    reports = []
    for dest in args.legs:
        dest = dest.upper()
        try:
            rep = run_leg(client, dest, run_cache)
        except Exception as e:  # noqa: BLE001
            print(f"LEG ERROR: {e}", flush=True)
            client.cancel()
            rep = {"ok": False, "dest": dest, "error": str(e), "issues": [str(e)]}
        reports.append(rep)
        Path(args.out).write_text(json.dumps(reports, indent=2))
        if not rep.get("ok"):
            print(f"\n*** LEG TO {dest} FAILED — stop for fix ***", flush=True)
            return 1
        print(f"\n*** LEG TO {dest} OK — pause 3s ***", flush=True)
        time.sleep(3)
    print("\nALL LEGS OK", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
