#!/usr/bin/env python3
"""DEPRECATED — WASD map-pose fine docking (pre-marker).

Marker-relative docking / Nav2 DockRobot is not ready yet. Autonomous arrival
is handled entirely by Nav2 NavigateToPose + RPP (see bridges.py).

Do not launch this loop alongside Nav2 — it steals motor ownership via /keys.
"""

from __future__ import annotations

import json
import os
import ssl
import time
import urllib.request
from typing import Any

from lateral_maneuver import GapCloseConfig, GapCloseState, next_gap_close_step

GOAL_PATH = os.environ.get(
    "NAV_GOAL_STATUS_PATH",
    os.environ.get("NAV_GOAL_STATUS_FILE", "/app/lidar/navigation_goal.json"),
)
SLAM_PATH = os.environ.get("SLAM_MAP_FILE_PATH", "/app/lidar/slam.json")
DRIVE_KEYS_URL = os.environ.get(
    "NAV_DRIVE_KEYS_URL",
    os.environ.get("NAV_DRIVE_BASE_URL", "http://127.0.0.1:8787")
    + "/api/navigation/drive/keys",
)
NAV_API_TOKEN = os.environ.get("NAVIGATION_API_TOKEN", "")
SSL_VERIFY = os.environ.get("NAV_SSL_VERIFY", "false").lower() not in {"0", "false", "no"}
TICK_HZ = float(os.environ.get("NAV_FINE_DOCK_HZ", "10"))
PULSE_ON_SEC = float(os.environ.get("NAV_FINE_DOCK_PULSE_ON_SEC", "0.35"))
SETTLE_SEC = float(os.environ.get("NAV_FINE_DOCK_SETTLE_SEC", "3.0"))
XY_TOL_M = float(os.environ.get("NAV_FINE_DOCK_XY_TOL_M", "0.08"))
YAW_TOL_RAD = float(os.environ.get("NAV_FINE_DOCK_YAW_TOL_RAD", "0.12"))
SKID_TOL_M = float(os.environ.get("NAV_FINE_DOCK_SKID_TOL_M", "0.06"))
STATUS_PATH = os.environ.get(
    "NAV_FINE_DOCK_STATUS_PATH", "/app/lidar/navigation_fine_dock.json"
)


def _read_json(path: str) -> dict[str, Any] | None:
    try:
        with open(path, encoding="utf-8") as handle:
            raw = json.load(handle)
        return raw if isinstance(raw, dict) else None
    except (OSError, json.JSONDecodeError, TypeError):
        return None


def _write_json(path: str, payload: dict[str, Any]) -> None:
    try:
        directory = os.path.dirname(path)
        if directory:
            os.makedirs(directory, exist_ok=True)
        tmp = f"{path}.tmp"
        with open(tmp, "w", encoding="utf-8") as handle:
            json.dump(payload, handle)
        os.replace(tmp, path)
    except OSError:
        pass


class FineDockLoop:
    def __init__(self) -> None:
        self._ssl = None if SSL_VERIFY else ssl._create_unverified_context()
        self._state = GapCloseState()
        self._active = False
        self._goal: dict[str, Any] | None = None
        self._keys: list[str] = []
        self._pulse_until = 0.0
        self._settle_until = 0.0
        self._last_sent: list[str] | None = None
        self._cfg = GapCloseConfig(
            xy_tol_m=XY_TOL_M,
            yaw_tol_rad=YAW_TOL_RAD,
            skid_tol_m=SKID_TOL_M,
        )

    def _post_keys(self, keys: list[str]) -> None:
        if keys == self._last_sent:
            return
        data = json.dumps({"keys": keys}).encode("utf-8")
        req = urllib.request.Request(
            DRIVE_KEYS_URL,
            data=data,
            method="POST",
            headers={"Content-Type": "application/json"},
        )
        if NAV_API_TOKEN:
            req.add_header("Authorization", f"Bearer {NAV_API_TOKEN}")
        try:
            urllib.request.urlopen(req, timeout=1.2, context=self._ssl)
            self._last_sent = list(keys)
        except Exception as err:  # noqa: BLE001
            print(f"fine_dock: drive keys failed: {err}", flush=True)

    def _slam_pose(self) -> dict[str, float] | None:
        raw = _read_json(SLAM_PATH)
        if not raw:
            return None
        pose = raw.get("pose") if isinstance(raw.get("pose"), dict) else raw
        if not isinstance(pose, dict):
            return None
        try:
            return {
                "x": float(pose["x"]),
                "y": float(pose["y"]),
                "yaw": float(pose.get("yaw", pose.get("theta", 0.0))),
            }
        except (KeyError, TypeError, ValueError):
            return None

    def _maybe_arm(self, goal_status: dict[str, Any]) -> None:
        goal = goal_status.get("goal")
        if not isinstance(goal, dict):
            self._active = False
            return
        if not goal.get("fine_docking"):
            self._active = False
            return
        status = str(goal_status.get("status") or "")
        result = str(goal_status.get("result") or "")
        coarse_done = status == "idle" and result == "succeeded"
        if coarse_done and not self._active:
            self._active = True
            self._goal = goal
            self._state = GapCloseState()
            print(
                f"fine_dock: starting gap close → ({goal.get('x')},{goal.get('y')})",
                flush=True,
            )

    def tick(self) -> None:
        now = time.monotonic()
        goal_status = _read_json(GOAL_PATH) or {}
        self._maybe_arm(goal_status)

        status = {
            "active": self._active,
            "phase": self._state.phase,
            "keys": list(self._keys),
            "note": self._state.meta.get("note", ""),
            "updated_at": time.time(),
        }

        if not self._active or not self._goal:
            self._post_keys([])
            _write_json(STATUS_PATH, status)
            return

        pose = self._slam_pose()
        if pose is None:
            _write_json(STATUS_PATH, {**status, "error": "no slam pose"})
            return

        if now < self._settle_until:
            self._keys = []
            self._post_keys([])
            _write_json(STATUS_PATH, {**status, "phase": "settle"})
            return

        if self._keys and now < self._pulse_until:
            self._post_keys(self._keys)
            _write_json(STATUS_PATH, status)
            return

        tx = float(self._goal["x"])
        ty = float(self._goal["y"])
        tyaw = float(self._goal.get("yaw") or 0.0)
        step = next_gap_close_step(
            pose["x"],
            pose["y"],
            pose["yaw"],
            tx,
            ty,
            tyaw,
            self._state,
            cfg=self._cfg,
        )
        self._state = step.state
        status["phase"] = step.phase
        status["note"] = step.note

        if step.done:
            self._active = False
            self._keys = []
            self._post_keys([])
            status["active"] = False
            status["result"] = "docked"
            print("fine_dock: gap close complete", flush=True)
            _write_json(STATUS_PATH, status)
            return

        if not step.keys:
            self._keys = []
            self._post_keys([])
            self._settle_until = now + SETTLE_SEC
            _write_json(STATUS_PATH, status)
            return

        self._keys = list(step.keys)
        self._pulse_until = now + PULSE_ON_SEC
        self._post_keys(self._keys)
        _write_json(STATUS_PATH, status)


def main() -> None:
    loop = FineDockLoop()
    period = 1.0 / max(TICK_HZ, 1.0)
    print(
        f"fine_dock: listening goal={GOAL_PATH} slam={SLAM_PATH} "
        f"pulse={PULSE_ON_SEC}s settle={SETTLE_SEC}s",
        flush=True,
    )
    try:
        while True:
            loop.tick()
            time.sleep(period)
    except KeyboardInterrupt:
        loop._post_keys([])


if __name__ == "__main__":
    main()
