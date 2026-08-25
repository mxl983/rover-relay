#!/usr/bin/env python3
"""Compact map / scan / config snapshots for nav run replication."""

from __future__ import annotations

import json
import math
import os
import time
import urllib.error
import urllib.request
from typing import Any

SLAM_MAP_PATH = os.environ.get("SLAM_MAP_FILE_PATH", "/app/lidar/slam.json")
SCAN_PATH = os.environ.get("LIDAR_SCAN_FILE_PATH", "/app/lidar/scan.json")
BASELINE_PATH = os.environ.get(
    "SLAM_BASELINE_GRID_PATH", "/app/lidar/maps/baseline_grid.json"
)
FROZEN_STATE_PATH = os.environ.get(
    "SLAM_FROZEN_STATE_PATH", "/app/lidar/maps/frozen.pbstream"
)
DRIVE_ASSIST_SNAPSHOT_PATH = os.environ.get(
    "NAV_DRIVE_ASSIST_SNAPSHOT_PATH", "/app/lidar/drive_assist_snapshot.json"
)
DRIVE_ASSIST_INFO_URL = os.environ.get("NAV_PI_DRIVE_ASSIST_INFO_URL", "").strip()
NAV2_PARAMS_FILE = os.environ.get("NAV2_PARAMS_FILE", "/opt/ros2-nav/config/nav2_params.yaml")
NAV_BT_XML = os.environ.get(
    "NAV_BT_XML", "/opt/ros2-nav/config/navigate_stable.xml"
)


def _read_json(path: str) -> dict[str, Any] | None:
    try:
        with open(path, encoding="utf-8") as handle:
            raw = json.load(handle)
        return raw if isinstance(raw, dict) else None
    except (OSError, json.JSONDecodeError, TypeError):
        return None


def _octant_ranges(points: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Eight compass buckets of minimum range for compact scan fingerprint."""
    buckets: list[list[float]] = [[] for _ in range(8)]
    for pt in points:
        try:
            angle = math.radians(float(pt.get("a_deg", 0.0)))
            dist = float(pt.get("r", 0.0))
        except (TypeError, ValueError):
            continue
        if not math.isfinite(dist) or dist <= 0:
            continue
        idx = int((angle + math.pi) / (2 * math.pi) * 8) % 8
        buckets[idx].append(dist)
    out: list[dict[str, Any]] = []
    for i, vals in enumerate(buckets):
        if not vals:
            continue
        out.append({"sector": i, "min_m": round(min(vals), 3), "n": len(vals)})
    return out


def map_context() -> dict[str, Any]:
    raw = _read_json(SLAM_MAP_PATH)
    if not raw:
        return {"loaded": False}
    pose = raw.get("pose") if isinstance(raw.get("pose"), dict) else {}
    origin = raw.get("origin") if isinstance(raw.get("origin"), dict) else {}
    ctx: dict[str, Any] = {
        "loaded": True,
        "mode": raw.get("mode"),
        "source": raw.get("source"),
        "resolution": raw.get("resolution"),
        "origin": origin,
        "width": raw.get("width"),
        "height": raw.get("height"),
        "occupied_count": raw.get("occupied_count"),
        "pose": pose,
        "pose_in_map": raw.get("pose_in_map"),
        "scan_match_score": raw.get("scan_match_score"),
        "scan_hit_count": raw.get("scan_hit_count"),
        "hz": raw.get("hz"),
        "frozen_pbstream": os.path.isfile(FROZEN_STATE_PATH),
        "baseline_grid": os.path.isfile(BASELINE_PATH),
    }
    baseline = _read_json(BASELINE_PATH)
    if baseline:
        ctx["baseline_frozen_at"] = baseline.get("frozen_at")
    return ctx


def scan_context() -> dict[str, Any]:
    raw = _read_json(SCAN_PATH)
    if not raw:
        return {"loaded": False}
    points = raw.get("points") if isinstance(raw.get("points"), list) else []
    return {
        "loaded": True,
        "stamp": raw.get("stamp"),
        "frame_id": raw.get("frame_id"),
        "count": raw.get("count"),
        "valid": raw.get("valid"),
        "nearest_m": raw.get("nearest"),
        "farthest_m": raw.get("farthest"),
        "hz": raw.get("hz"),
        "octants": _octant_ranges(points),
    }


def nav_config_context() -> dict[str, Any]:
    return {
        "nav2_params_file": NAV2_PARAMS_FILE,
        "bt_xml": NAV_BT_XML,
        "max_linear_mps": float(os.environ.get("NAV_MAX_LINEAR_MPS", "0.28")),
        "max_angular_rps": float(os.environ.get("NAV_MAX_ANGULAR_RPS", "0.55")),
        "align_angular_rps": float(os.environ.get("NAV_ALIGN_ANGULAR_RPS", "0.55")),
        "turn_pulse_on_pure_s": float(
            os.environ.get("NAV_TURN_PULSE_ON_PURE_SEC", "0.45")
        ),
        "turn_pulse_off_pure_s": float(
            os.environ.get("NAV_TURN_PULSE_OFF_PURE_SEC", "3.0")
        ),
        "observe_settle_s": float(os.environ.get("NAV_OBSERVE_SETTLE_SEC", "3.0")),
        "turn_pure_wz_deadband": float(
            os.environ.get("NAV_TURN_PURE_WZ_DEADBAND", "0.05")
        ),
        "fine_dock_xy_tol_m": float(os.environ.get("NAV_FINE_DOCK_XY_TOL_M", "0.16")),
        "fine_dock_yaw_tol_rad": float(
            os.environ.get("NAV_FINE_DOCK_YAW_TOL_RAD", str(math.radians(5.0)))
        ),
        "progress_period_s": float(os.environ.get("NAV_PROGRESS_PERIOD_SEC", "2.0")),
        "stall_warn_s": float(os.environ.get("NAV_STALL_WARN_SEC", "8.0")),
        "stall_event_s": float(os.environ.get("NAV_STALL_EVENT_SEC", "20.0")),
        "scan_topic": os.environ.get("NAV_SCAN_TOPIC", "/scan_nav"),
        "cmd_vel_topic": os.environ.get("NAV_CMD_VEL_TOPIC", "/cmd_vel"),
    }


def _compact_drive_assist(raw: dict[str, Any] | None) -> dict[str, Any]:
    if not raw:
        return {"loaded": False}
    obstacle = raw.get("obstacle") if isinstance(raw.get("obstacle"), dict) else {}
    closest = obstacle.get("closest") if isinstance(obstacle.get("closest"), dict) else {}
    prohibited = []
    for entry in raw.get("prohibitedDirections") or []:
        if isinstance(entry, dict) and entry.get("direction"):
            prohibited.append(str(entry["direction"]))
    return {
        "loaded": True,
        "enabled": raw.get("enabled"),
        "assist_ui_state": raw.get("assistUiState"),
        "assist_phase": raw.get("assistPhase"),
        "blocked": raw.get("blocked"),
        "forward_hold": raw.get("forwardHold"),
        "wheels_stopped": raw.get("wheelsStopped"),
        "closest_range_m": closest.get("rangeM") or obstacle.get("minRangeM"),
        "closest_angle_deg": closest.get("angleDeg"),
        "prohibited": prohibited,
        "updated_at": raw.get("updated_at") or raw.get("updatedAt"),
    }


def fetch_drive_assist_info(timeout_sec: float = 1.5) -> dict[str, Any] | None:
    if not DRIVE_ASSIST_INFO_URL:
        return None
    req = urllib.request.Request(DRIVE_ASSIST_INFO_URL, method="GET")
    token = os.environ.get("NAVIGATION_API_TOKEN", "")
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    try:
        with urllib.request.urlopen(req, timeout=timeout_sec) as resp:
            raw = json.loads(resp.read().decode("utf-8"))
        return raw if isinstance(raw, dict) else None
    except (OSError, urllib.error.URLError, json.JSONDecodeError, TimeoutError):
        return None


def drive_assist_context(*, refresh_remote: bool = False) -> dict[str, Any]:
    raw = _read_json(DRIVE_ASSIST_SNAPSHOT_PATH)
    if refresh_remote:
        remote = fetch_drive_assist_info()
        if remote:
            raw = remote
            try:
                directory = os.path.dirname(DRIVE_ASSIST_SNAPSHOT_PATH)
                if directory:
                    os.makedirs(directory, exist_ok=True)
                tmp = f"{DRIVE_ASSIST_SNAPSHOT_PATH}.tmp"
                with open(tmp, "w", encoding="utf-8") as handle:
                    json.dump(remote, handle, separators=(",", ":"))
                os.replace(tmp, DRIVE_ASSIST_SNAPSHOT_PATH)
            except OSError:
                pass
    return _compact_drive_assist(raw)


def drive_assist_blocking(info: dict[str, Any] | None) -> bool:
    if not info or not info.get("loaded"):
        return False
    if info.get("enabled") is False:
        return False
    if info.get("blocked") or info.get("forward_hold") or info.get("wheels_stopped"):
        return True
    state = str(info.get("assist_ui_state") or "")
    return state in ("warning", "maneuvering")


def nav_context(*, full: bool = False, refresh_assist: bool = False) -> dict[str, Any]:
    ctx: dict[str, Any] = {
        "ts": time.time(),
        "map": map_context(),
        "scan": scan_context(),
        "drive_assist": drive_assist_context(refresh_remote=refresh_assist),
    }
    if full:
        ctx["nav_config"] = nav_config_context()
    return ctx
