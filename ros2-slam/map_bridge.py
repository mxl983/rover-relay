#!/usr/bin/env python3
"""SLAM bridge: Cartographer /map + TF pose → slam.json + waypoints HTTP.

Live map comes from Cartographer's occupancy grid (scan-matched). DIY lidar
raycasting was stacking misaligned hits and looked nothing like the clean
lidar minimap. Persistence saves the latest Cartographer grid for reboot.
"""

from __future__ import annotations

import json
import math
import os
import signal
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.parse import urlparse

import rclpy
from cartographer_ros_msgs.srv import WriteState
from geometry_msgs.msg import PoseStamped
from nav_msgs.msg import OccupancyGrid
from rclpy.duration import Duration
from rclpy.node import Node
from rclpy.qos import DurabilityPolicy, HistoryPolicy, QoSProfile, ReliabilityPolicy, qos_profile_sensor_data
from rclpy.time import Time
from sensor_msgs.msg import LaserScan
from tf2_ros import Buffer, TransformException, TransformListener

from map_layers import (
    BaselineGrid,
    WorkingCopy,
    pose_in_baseline_map,
    scan_baseline_match_score,
    scan_hits_world,
)
from persistent_map import (
    PersistentOccupancyMap,
    add_waypoint,
    delete_waypoint,
    load_waypoints,
)

MAP_FILE_PATH = os.environ.get("SLAM_MAP_FILE_PATH", "/app/lidar/slam.json")
VIEWER_PORT = int(os.environ.get("SLAM_VIEWER_PORT", "8767"))
VIEWER_HOST = os.environ.get("SLAM_VIEWER_HOST", "0.0.0.0")
MAP_TOPIC = os.environ.get("SLAM_MAP_TOPIC", "/map")
POSE_TOPIC = os.environ.get("SLAM_POSE_TOPIC", "/tracked_pose")
SCAN_TOPIC = os.environ.get("SLAM_FILTERED_TOPIC", "/scan_filtered")
MAP_FRAME = os.environ.get("SLAM_MAP_FRAME", "map")
TRACKING_FRAME = os.environ.get("SLAM_TRACKING_FRAME", "base_link")
LASER_FRAME = os.environ.get("SLAM_LASER_FRAME", "")
MAX_OCCUPIED = int(os.environ.get("SLAM_MAX_OCCUPIED_CELLS", "12000"))
WINDOW_M = float(os.environ.get("SLAM_WINDOW_M", "12"))
OCCUPIED_THRESHOLD = int(os.environ.get("SLAM_OCCUPIED_THRESHOLD", "65"))
SAVE_EVERY_MAPS = int(os.environ.get("SLAM_SAVE_EVERY_MAPS", "20"))
PURGE_REQUEST_PATH = os.environ.get("SLAM_PURGE_REQUEST_PATH", "/app/lidar/.purge_slam")
FREEZE_REQUEST_PATH = os.environ.get("SLAM_FREEZE_REQUEST_PATH", "/app/lidar/.freeze_slam")
MODE_PATH = os.environ.get("SLAM_MODE_PATH", "/app/lidar/maps/slam_mode")
FROZEN_STATE_PATH = os.environ.get(
    "SLAM_FROZEN_STATE_PATH", "/app/lidar/maps/frozen.pbstream"
)
FREEZE_POSE_PATH = os.environ.get(
    "SLAM_FREEZE_POSE_PATH", "/app/lidar/maps/freeze_pose.json"
)
REPOSITION_REQUEST_PATH = os.environ.get(
    "SLAM_REPOSITION_REQUEST_PATH", "/app/lidar/.reposition_slam"
)
GLOBAL_LOCALIZATION_PATH = os.environ.get(
    "SLAM_GLOBAL_LOCALIZATION_PATH", "/app/lidar/.global_localization"
)
GLOBAL_LOCALIZATION_ACTIVE_PATH = os.environ.get(
    "SLAM_GLOBAL_LOCALIZATION_ACTIVE_PATH",
    "/app/lidar/.global_localization_active",
)
GLOBAL_LOCALIZATION_SETTLE_SEC = float(
    os.environ.get("SLAM_GLOBAL_LOCALIZATION_SETTLE_SEC", "15")
)
# UI snapshot rate (slam.json + dashboard pose/scan overlay). Was 2 Hz (0.5 s).
SLAM_LIVE_PUBLISH_HZ = float(os.environ.get("SLAM_LIVE_PUBLISH_HZ", "10"))
SLAM_LIVE_PUBLISH_HZ = max(2.0, min(SLAM_LIVE_PUBLISH_HZ, 30.0))
LIVE_PUBLISH_PERIOD_SEC = 1.0 / SLAM_LIVE_PUBLISH_HZ
SLAM_TF_POLL_HZ = float(os.environ.get("SLAM_TF_POLL_HZ", "20"))
SLAM_TF_POLL_HZ = max(5.0, min(SLAM_TF_POLL_HZ, 50.0))
AUTO_REPOSITION = os.environ.get("SLAM_AUTO_REPOSITION", "1") not in (
    "0",
    "false",
    "False",
)
AUTO_REPOSITION_MIN_SCORE = float(
    os.environ.get("SLAM_AUTO_REPOSITION_MIN_SCORE", "0.12")
)
AUTO_REPOSITION_SEVERE_SCORE = float(
    os.environ.get("SLAM_AUTO_REPOSITION_SEVERE_SCORE", "0.08")
)
AUTO_REPOSITION_MIN_HITS = int(os.environ.get("SLAM_AUTO_REPOSITION_MIN_HITS", "24"))
AUTO_REPOSITION_BAD_SCANS = int(os.environ.get("SLAM_AUTO_REPOSITION_BAD_SCANS", "15"))
AUTO_REPOSITION_SEVERE_SCANS = int(
    os.environ.get("SLAM_AUTO_REPOSITION_SEVERE_SCANS", "8")
)
AUTO_REPOSITION_BOOT_DELAY_SEC = float(
    os.environ.get("SLAM_AUTO_REPOSITION_BOOT_DELAY_SEC", "20")
)
AUTO_REPOSITION_COOLDOWN_SEC = float(
    os.environ.get("SLAM_AUTO_REPOSITION_COOLDOWN_SEC", "300")
)
AUTO_REPOSITION_COOLDOWN_PATH = os.environ.get(
    "SLAM_AUTO_REPOSITION_COOLDOWN_PATH",
    "/app/lidar/.auto_reposition_cooldown",
)
NAV_GOAL_STATUS_PATH = os.environ.get(
    "NAV_GOAL_STATUS_PATH", "/app/lidar/navigation_goal.json"
)
SCAN_MATCH_SEARCH_CELLS = int(os.environ.get("SLAM_SCAN_MATCH_SEARCH_CELLS", "2"))
PROMOTE_REQUEST_PATH = os.environ.get(
    "SLAM_PROMOTE_REQUEST_PATH", "/app/lidar/.promote_slam"
)

_latest: dict[str, Any] = {
    "stamp": 0.0,
    "frame_id": "map",
    "resolution": 0.05,
    "width": 0,
    "height": 0,
    "origin": {"x": 0.0, "y": 0.0, "yaw": 0.0},
    "pose": {"x": 0.0, "y": 0.0, "yaw": 0.0, "stamp": 0.0},
    "occupied": [],
    "occupied_count": 0,
    "hz": 0.0,
    "waypoints": [],
}
_lock = threading.Lock()
_pose: dict[str, float] = {"x": 0.0, "y": 0.0, "yaw": 0.0, "stamp": 0.0}
_tracking_frame = TRACKING_FRAME
_scan_times: list[float] = []
_persist = PersistentOccupancyMap()
_baseline = BaselineGrid()
_working = WorkingCopy(_baseline)
_scan_hits: list[dict[str, float]] = []
_scan_match_score: float | None = None
_have_pose = False


def navigation_active() -> bool:
    try:
        with open(NAV_GOAL_STATUS_PATH, encoding="utf-8") as handle:
            state = json.load(handle)
        return str(state.get("status") or "") in ("navigating", "docking")
    except OSError:
        return False


def slam_mode() -> str:
    try:
        with open(MODE_PATH, encoding="utf-8") as handle:
            return "localization" if handle.read().strip() == "localization" else "mapping"
    except OSError:
        return "mapping"


def write_freeze_pose(x: float, y: float, yaw: float) -> None:
    os.makedirs(os.path.dirname(FREEZE_POSE_PATH), exist_ok=True)
    tmp = f"{FREEZE_POSE_PATH}.tmp"
    with open(tmp, "w", encoding="utf-8") as handle:
        json.dump({"x": x, "y": y, "yaw": yaw, "updated_at": time.time()}, handle)
    os.replace(tmp, FREEZE_POSE_PATH)
_carto: dict[str, Any] | None = None
_map_msgs = 0


def yaw_from_quaternion(x: float, y: float, z: float, w: float) -> float:
    siny_cosp = 2.0 * (w * z + x * y)
    cosy_cosp = 1.0 - 2.0 * (y * y + z * z)
    return math.atan2(siny_cosp, cosy_cosp)


def write_snapshot(payload: dict[str, Any]) -> None:
    directory = os.path.dirname(MAP_FILE_PATH)
    if directory:
        os.makedirs(directory, exist_ok=True)
    tmp_path = f"{MAP_FILE_PATH}.tmp"
    with open(tmp_path, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, separators=(",", ":"))
    os.replace(tmp_path, MAP_FILE_PATH)


def grid_snapshot_payload(
    *,
    resolution: float,
    origin_x: float,
    origin_y: float,
    width: int,
    height: int,
    data: list[int],
    pose: dict[str, float],
    hz: float,
    source: str,
    waypoints: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    px = float(pose.get("x", 0.0))
    py = float(pose.get("y", 0.0))
    yaw = float(pose.get("yaw", 0.0))
    res = float(resolution)
    ox, oy = float(origin_x), float(origin_y)

    map_max_x = ox + width * res
    map_max_y = oy + height * res
    pose_in_map = width > 0 and height > 0 and ox <= px <= map_max_x and oy <= py <= map_max_y
    # Crop around the real pose (not a clamped view) so walls stay registered
    # with waypoint world coordinates.
    view_x, view_y = px, py

    centers: list[tuple[float, float]] = [(view_x, view_y)]
    for wp in waypoints or []:
        try:
            centers.append((float(wp["x"]), float(wp["y"])))
        except (KeyError, TypeError, ValueError):
            continue

    hit_set: set[tuple[int, int]] = set()
    halo_m = min(WINDOW_M, 4.0)
    for i, (cx, cy) in enumerate(centers):
        radius = WINDOW_M if i == 0 else halo_m
        ix0 = max(0, int((cx - radius - ox) / res) - 1)
        ix1 = min(width, int((cx + radius - ox) / res) + 2)
        iy0 = max(0, int((cy - radius - oy) / res) - 1)
        iy1 = min(height, int((cy + radius - oy) / res) + 2)
        for iy in range(iy0, iy1):
            row = iy * width
            for ix in range(ix0, ix1):
                if data[row + ix] >= OCCUPIED_THRESHOLD:
                    hit_set.add((ix, iy))

    hits: list[tuple[int, int]] = []
    for ix, iy in hit_set:
        neighbors = 0
        for dy in (-1, 0, 1):
            for dx in (-1, 0, 1):
                if dx == 0 and dy == 0:
                    continue
                if (ix + dx, iy + dy) in hit_set:
                    neighbors += 1
        if neighbors >= 1:
            hits.append((ix, iy))

    occupied: list[int] = []
    map_points: list[dict[str, float]] = []
    for ix, iy in hits:
        occupied.extend((ix, iy))
        map_points.append(
            {
                "x": round(ox + (ix + 0.5) * res, 3),
                "y": round(oy + (iy + 0.5) * res, 3),
            }
        )

    if MAX_OCCUPIED > 0 and len(occupied) // 2 > MAX_OCCUPIED:
        step = (len(occupied) // 2) / MAX_OCCUPIED
        slim_occ: list[int] = []
        slim_pts: list[dict[str, float]] = []
        for i in range(MAX_OCCUPIED):
            idx = int(i * step)
            slim_occ.extend(occupied[idx * 2 : idx * 2 + 2])
            if idx < len(map_points):
                slim_pts.append(map_points[idx])
        occupied = slim_occ
        map_points = slim_pts

    return {
        "stamp": float(pose.get("stamp", time.time())),
        "updated_at": time.time(),
        "frame_id": "map",
        "resolution": round(res, 4),
        "width": width,
        "height": height,
        "origin": {"x": round(ox, 3), "y": round(oy, 3), "yaw": 0.0},
        "pose": {
            "x": round(px, 3),
            "y": round(py, 3),
            "yaw": round(yaw, 4),
            "theta_deg": round(math.degrees(yaw), 2),
            "stamp": float(pose.get("stamp", 0.0)),
        },
        "view": {
            "x": round(view_x, 3),
            "y": round(view_y, 3),
            "yaw": round(yaw, 4),
        },
        "pose_in_map": pose_in_map,
        "occupied": occupied,
        "occupied_count": len(occupied) // 2,
        "map_points": map_points,
        "hz": round(hz, 1),
        "version": 5,
        "enabled": True,
        "mode": slam_mode(),
        "persistent": source == "disk",
        "persisted": _persist.loaded_from_disk or os.path.isfile(
            os.environ.get("SLAM_PERSISTENT_MAP_PATH", "/app/lidar/maps/persistent_grid.json")
        ),
        "source": source,
        "update_count": _map_msgs,
    }



def publish_live(hz: float) -> None:
    global _latest, _scan_hits
    with _lock:
        pose = dict(_pose)
        carto = dict(_carto) if _carto else None
        scan_hits = list(_scan_hits)

    waypoints = load_waypoints()
    mode = slam_mode()
    use_baseline = mode == "localization" and _baseline.loaded

    if use_baseline:
        with _baseline.lock:
            payload = grid_snapshot_payload(
                resolution=_baseline.resolution,
                origin_x=_baseline.origin_x,
                origin_y=_baseline.origin_y,
                width=_baseline.width,
                height=_baseline.height,
                data=_baseline.data,
                pose=pose,
                hz=hz,
                source="baseline",
                waypoints=waypoints,
            )
    elif carto and carto.get("data"):
        payload = grid_snapshot_payload(
            resolution=carto["resolution"],
            origin_x=carto["origin_x"],
            origin_y=carto["origin_y"],
            width=carto["width"],
            height=carto["height"],
            data=carto["data"],
            pose=pose,
            hz=hz,
            source="cartographer",
            waypoints=waypoints,
        )
    else:
        payload = _persist.snapshot_payload(pose, hz, MAX_OCCUPIED, WINDOW_M)
        payload["source"] = "disk"

    payload["waypoints"] = waypoints
    payload["mode"] = mode
    payload["scan_hits"] = scan_hits
    payload["scan_hit_count"] = len(scan_hits)
    with _lock:
        payload["scan_match_score"] = _scan_match_score
        _latest = payload
    try:
        write_snapshot(payload)
    except OSError:
        pass


class SlamBridge(Node):
    def __init__(self) -> None:
        super().__init__("rover_slam_bridge")
        self._tf_buffer = Buffer()
        self._tf_listener = TransformListener(self._tf_buffer, self)
        map_qos = QoSProfile(
            reliability=ReliabilityPolicy.RELIABLE,
            durability=DurabilityPolicy.TRANSIENT_LOCAL,
            history=HistoryPolicy.KEEP_LAST,
            depth=1,
        )
        pose_qos = QoSProfile(
            reliability=ReliabilityPolicy.RELIABLE,
            history=HistoryPolicy.KEEP_LAST,
            depth=5,
        )
        self.create_subscription(OccupancyGrid, MAP_TOPIC, self._on_map, map_qos)
        self.create_subscription(PoseStamped, POSE_TOPIC, self._on_pose, pose_qos)
        self.create_subscription(LaserScan, SCAN_TOPIC, self._on_scan, qos_profile_sensor_data)
        self._last_live_publish_mono = 0.0
        self.create_timer(1.0 / SLAM_TF_POLL_HZ, self._poll_tf_pose)
        self.create_timer(LIVE_PUBLISH_PERIOD_SEC, self._tick_publish)
        self._purge_restart_requested = False
        self.create_timer(0.25, self._check_purge_request)
        self._freeze_requested = False
        self._write_state_client = self.create_client(WriteState, "/write_state")
        self.create_timer(0.25, self._check_freeze_request)
        self._reposition_requested = False
        self._bad_match_streak = 0
        self._auto_repos_cooldown_until = self._load_auto_repos_cooldown()
        self._boot_mono = time.monotonic()
        self.create_timer(0.25, self._check_reposition_request)
        self._promote_requested = False
        self.create_timer(0.25, self._check_promote_request)
        self._global_result_saved = False
        self.create_timer(0.5, self._check_global_localization_result)
        self.get_logger().info(
            f"Cartographer SLAM bridge map={MAP_TOPIC} scan={SCAN_TOPIC} "
            f"live={MAP_FILE_PATH} live_hz={SLAM_LIVE_PUBLISH_HZ} tf_hz={SLAM_TF_POLL_HZ} "
            f"disk_fallback={_persist.loaded_from_disk} baseline={_baseline.loaded}"
        )
        if _baseline.load():
            _working.reset_from_baseline()
            self.get_logger().info("Loaded frozen baseline grid for localization UI")
        elif slam_mode() == "localization" and _persist.loaded_from_disk:
            with _persist.lock:
                _baseline.replace_from_occupancy_grid(
                    _persist.resolution,
                    _persist.origin_x,
                    _persist.origin_y,
                    _persist.width,
                    _persist.height,
                    list(_persist.data),
                )
            _baseline.save()
            _working.reset_from_baseline()
            self.get_logger().info(
                "Seeded baseline_grid.json from persistent_grid.json"
            )
        elif slam_mode() == "localization":
            self.get_logger().warning(
                "Localization mode without baseline_grid.json; will seed on first /map"
            )

    def _set_pose(self, x: float, y: float, yaw: float, stamp: float) -> None:
        global _pose, _have_pose
        with _lock:
            _pose = {"x": x, "y": y, "yaw": yaw, "stamp": stamp}
            _have_pose = True

    def _on_pose(self, msg: PoseStamped) -> None:
        p = msg.pose.position
        q = msg.pose.orientation
        stamp = msg.header.stamp.sec + msg.header.stamp.nanosec * 1e-9
        self._set_pose(float(p.x), float(p.y), yaw_from_quaternion(q.x, q.y, q.z, q.w), stamp)

    def _poll_tf_pose(self) -> None:
        global _tracking_frame
        # Always use base_link (drive frame), never the laser frame. Laser is
        # yaw-offset −90° from base; using it for pose makes the map slide
        # sideways when driving forward.
        frame = _tracking_frame or TRACKING_FRAME or "base_link"
        for candidate in (frame, "base_link"):
            if not candidate:
                continue
            try:
                transform = self._tf_buffer.lookup_transform(
                    MAP_FRAME,
                    candidate,
                    Time(),
                    timeout=Duration(seconds=0.05),
                )
            except TransformException:
                continue
            t = transform.transform.translation
            q = transform.transform.rotation
            stamp = (
                transform.header.stamp.sec + transform.header.stamp.nanosec * 1e-9
                if transform.header.stamp.sec or transform.header.stamp.nanosec
                else time.time()
            )
            self._set_pose(
                float(t.x),
                float(t.y),
                yaw_from_quaternion(q.x, q.y, q.z, q.w),
                stamp,
            )
            self._maybe_publish_live()
            return

    def _seed_baseline_from_carto(self, grid: dict[str, Any]) -> None:
        if _baseline.loaded:
            return
        _baseline.replace_from_occupancy_grid(
            grid["resolution"],
            grid["origin_x"],
            grid["origin_y"],
            grid["width"],
            grid["height"],
            grid["data"],
        )
        _baseline.save()
        _working.reset_from_baseline()
        self.get_logger().info("Seeded baseline_grid.json from live Cartographer /map")

    def _save_baseline_from_carto(self) -> None:
        with _lock:
            carto = dict(_carto) if _carto else None
        if not carto or not carto.get("data"):
            return
        _baseline.replace_from_occupancy_grid(
            carto["resolution"],
            carto["origin_x"],
            carto["origin_y"],
            carto["width"],
            carto["height"],
            carto["data"],
        )
        _baseline.save()
        _working.reset_from_baseline()

    def _on_map(self, msg: OccupancyGrid) -> None:
        """Live display + periodic disk snapshot from Cartographer."""
        global _carto, _map_msgs, _tracking_frame
        if not _tracking_frame and TRACKING_FRAME:
            _tracking_frame = TRACKING_FRAME
        info = msg.info
        data = [int(v) for v in msg.data]
        if len(data) != int(info.width) * int(info.height):
            return
        if not any(v >= 0 for v in data):
            return

        grid = {
            "resolution": float(info.resolution),
            "origin_x": float(info.origin.position.x),
            "origin_y": float(info.origin.position.y),
            "width": int(info.width),
            "height": int(info.height),
            "data": data,
            "stamp": time.time(),
        }
        with _lock:
            _carto = grid
            _map_msgs += 1
            map_n = _map_msgs

        if slam_mode() == "localization" and not _baseline.loaded:
            self._seed_baseline_from_carto(grid)

        # Persist Cartographer grid (replace DIY raycast mess).
        if map_n == 1 or map_n % max(1, SAVE_EVERY_MAPS) == 0:
            _persist.replace_from_occupancy_grid(
                grid["resolution"],
                grid["origin_x"],
                grid["origin_y"],
                grid["width"],
                grid["height"],
                data,
            )
            try:
                _persist.save(force=True)
            except OSError:
                pass

        times = [t for t in _scan_times if time.monotonic() - t <= 2.0]
        hz = len(times) / 2.0 if times else 0.0
        publish_live(hz)

    def _on_scan(self, msg: LaserScan) -> None:
        """Green live hits every scan; working-copy edits only after freeze."""
        global _scan_times, _scan_hits
        now = time.monotonic()
        _scan_times = [t for t in _scan_times if now - t <= 2.0]
        _scan_times.append(now)

        with _lock:
            if not _have_pose:
                return
            pose = dict(_pose)

        px = float(pose.get("x", 0.0))
        py = float(pose.get("y", 0.0))
        yaw = float(pose.get("yaw", 0.0))
        ranges = [float(r) for r in msg.ranges]
        angle_min = float(msg.angle_min)
        angle_increment = float(msg.angle_increment)
        range_min = float(msg.range_min)
        range_max = float(msg.range_max)

        # Always publish current scan endpoints for the green UI overlay.
        hits = scan_hits_world(
            px,
            py,
            yaw,
            ranges,
            angle_min,
            angle_increment,
            range_min,
            range_max,
        )
        with _lock:
            _scan_hits = hits

        self._maybe_auto_reposition(pose, hits)
        self._maybe_publish_live()

    def _maybe_publish_live(self, *, force: bool = False) -> None:
        now = time.monotonic()
        if not force and now - self._last_live_publish_mono < LIVE_PUBLISH_PERIOD_SEC:
            return
        self._last_live_publish_mono = now
        times = [t for t in _scan_times if now - t <= 2.0]
        hz = len(times) / 2.0 if times else 0.0
        publish_live(hz)

    def _load_auto_repos_cooldown(self) -> float:
        """Wall-clock cooldown survives process restart after a repos."""
        try:
            with open(AUTO_REPOSITION_COOLDOWN_PATH, encoding="utf-8") as handle:
                until = float(handle.read().strip())
        except (OSError, ValueError):
            return 0.0
        remaining = until - time.time()
        if remaining <= 0:
            try:
                os.remove(AUTO_REPOSITION_COOLDOWN_PATH)
            except FileNotFoundError:
                pass
            return 0.0
        return time.monotonic() + remaining

    def _save_auto_repos_cooldown(self, until_mono: float) -> None:
        remaining = max(0.0, until_mono - time.monotonic())
        until_wall = time.time() + remaining
        try:
            directory = os.path.dirname(AUTO_REPOSITION_COOLDOWN_PATH)
            if directory:
                os.makedirs(directory, exist_ok=True)
            tmp = f"{AUTO_REPOSITION_COOLDOWN_PATH}.tmp"
            with open(tmp, "w", encoding="utf-8") as handle:
                handle.write(f"{until_wall}\n")
            os.replace(tmp, AUTO_REPOSITION_COOLDOWN_PATH)
        except OSError as exc:
            self.get_logger().warning(f"Could not persist auto-repos cooldown: {exc}")

    def _reposition_blocked(self) -> bool:
        return (
            self._reposition_requested
            or slam_mode() != "localization"
            or os.path.isfile(GLOBAL_LOCALIZATION_PATH)
            or os.path.isfile(GLOBAL_LOCALIZATION_ACTIVE_PATH)
        )

    def _trigger_reposition(self, reason: str) -> bool:
        if self._reposition_blocked():
            return False
        self._reposition_requested = True
        try:
            with open(GLOBAL_LOCALIZATION_PATH, "w", encoding="utf-8") as handle:
                handle.write(f"{time.time()}\n")
            try:
                os.remove(REPOSITION_REQUEST_PATH)
            except FileNotFoundError:
                pass
        except Exception as exc:  # noqa: BLE001
            self._reposition_requested = False
            self.get_logger().error(f"Could not reposition: {exc}")
            return False

        self.get_logger().info(
            f"Reposition triggered ({reason}); restarting for global scan-match localization"
        )

        def restart() -> None:
            time.sleep(0.5)
            os._exit(0)

        threading.Thread(target=restart, daemon=True).start()
        return True

    def _maybe_auto_reposition(
        self, pose: dict[str, float], hits: list[dict[str, float]]
    ) -> None:
        global _scan_match_score
        if not AUTO_REPOSITION or not _baseline.loaded:
            return
        if navigation_active():
            return
        if self._reposition_blocked():
            return
        now = time.monotonic()
        if now - self._boot_mono < AUTO_REPOSITION_BOOT_DELAY_SEC:
            return
        if now < self._auto_repos_cooldown_until:
            return

        px = float(pose.get("x", 0.0))
        py = float(pose.get("y", 0.0))
        in_map = pose_in_baseline_map(_baseline, px, py)
        score = scan_baseline_match_score(
            _baseline,
            hits,
            search_cells=SCAN_MATCH_SEARCH_CELLS,
            min_hits=AUTO_REPOSITION_MIN_HITS,
            occupied_threshold=OCCUPIED_THRESHOLD,
        )
        _scan_match_score = score

        reason: str | None = None
        if not in_map:
            reason = "pose outside frozen map"
        elif score is not None and score < AUTO_REPOSITION_SEVERE_SCORE:
            self._bad_match_streak += 1
            if self._bad_match_streak >= AUTO_REPOSITION_SEVERE_SCANS:
                reason = f"severe scan-map mismatch score={score:.2f}"
        else:
            self._bad_match_streak = max(0, self._bad_match_streak - 1)

        if reason and self._trigger_reposition(reason):
            self._auto_repos_cooldown_until = now + AUTO_REPOSITION_COOLDOWN_SEC
            self._save_auto_repos_cooldown(self._auto_repos_cooldown_until)
            self._bad_match_streak = 0

    def _tick_publish(self) -> None:
        self._maybe_publish_live(force=True)

    def _check_purge_request(self) -> None:
        if self._purge_restart_requested or not os.path.isfile(PURGE_REQUEST_PATH):
            return
        self._purge_restart_requested = True
        self.get_logger().warning("Purge marker detected; restarting SLAM cleanly")
        try:
            _persist.save(force=True)
        finally:
            # entrypoint.sh waits for any child to exit, then terminates the
            # remaining Cartographer processes. Avoid re-entrant rclpy shutdown.
            os._exit(0)

    def _check_freeze_request(self) -> None:
        if (
            self._freeze_requested
            or slam_mode() == "localization"
            or not os.path.isfile(FREEZE_REQUEST_PATH)
        ):
            return
        if not self._write_state_client.service_is_ready():
            self.get_logger().warning(
                "Freeze requested; waiting for Cartographer write_state service",
                throttle_duration_sec=2.0,
            )
            return

        self._freeze_requested = True
        os.makedirs(os.path.dirname(FROZEN_STATE_PATH), exist_ok=True)
        try:
            os.remove(FROZEN_STATE_PATH)
        except FileNotFoundError:
            pass
        request = WriteState.Request()
        request.filename = FROZEN_STATE_PATH
        request.include_unfinished_submaps = True
        with _lock:
            pose = dict(_pose)
        write_freeze_pose(
            float(pose.get("x", 0.0)),
            float(pose.get("y", 0.0)),
            float(pose.get("yaw", 0.0)),
        )
        future = self._write_state_client.call_async(request)
        future.add_done_callback(self._finish_freeze)
        self.get_logger().info(f"Writing frozen Cartographer state to {FROZEN_STATE_PATH}")

    def _finish_freeze(self, future: Any) -> None:
        try:
            future.result()
            if not os.path.isfile(FROZEN_STATE_PATH):
                raise RuntimeError("Cartographer returned without creating the state file")
            tmp = f"{MODE_PATH}.tmp"
            with open(tmp, "w", encoding="utf-8") as handle:
                handle.write("localization\n")
            os.replace(tmp, MODE_PATH)
            os.remove(FREEZE_REQUEST_PATH)
        except Exception as exc:  # noqa: BLE001
            self._freeze_requested = False
            self.get_logger().error(f"Could not freeze map: {exc}")
            return

        self._save_baseline_from_carto()
        self.get_logger().info("Map frozen; restarting Cartographer in localization mode")

        def restart() -> None:
            time.sleep(0.5)
            os._exit(0)

        threading.Thread(target=restart, daemon=True).start()

    def _check_global_localization_result(self) -> None:
        if self._global_result_saved or not os.path.isfile(
            GLOBAL_LOCALIZATION_ACTIVE_PATH
        ):
            return
        try:
            age = time.time() - os.path.getmtime(GLOBAL_LOCALIZATION_ACTIVE_PATH)
        except OSError:
            return
        if age < GLOBAL_LOCALIZATION_SETTLE_SEC:
            return
        with _lock:
            pose = dict(_pose)
        if float(pose.get("stamp", 0.0)) <= 0:
            return

        self._global_result_saved = True
        write_freeze_pose(
            float(pose.get("x", 0.0)),
            float(pose.get("y", 0.0)),
            float(pose.get("yaw", 0.0)),
        )
        try:
            os.remove(GLOBAL_LOCALIZATION_ACTIVE_PATH)
        except FileNotFoundError:
            pass
        self.get_logger().info(
            "Global relocalization converged; saving pose and returning to normal localization"
        )

        def restart() -> None:
            time.sleep(0.5)
            os._exit(0)

        threading.Thread(target=restart, daemon=True).start()

    def _check_promote_request(self) -> None:
        if (
            self._promote_requested
            or slam_mode() != "localization"
            or not os.path.isfile(PROMOTE_REQUEST_PATH)
            or not _baseline.loaded
            or _working.delta_count <= 0
        ):
            return
        if not self._write_state_client.service_is_ready():
            self.get_logger().warning(
                "Promote requested; waiting for Cartographer write_state service",
                throttle_duration_sec=2.0,
            )
            return

        self._promote_requested = True
        _working.adopt_baseline()
        _baseline.save()
        _persist.replace_from_occupancy_grid(
            _baseline.resolution,
            _baseline.origin_x,
            _baseline.origin_y,
            _baseline.width,
            _baseline.height,
            _baseline.data,
        )
        try:
            _persist.save(force=True)
        except OSError:
            pass
        _working.reset_from_baseline()

        os.makedirs(os.path.dirname(FROZEN_STATE_PATH), exist_ok=True)
        try:
            os.remove(FROZEN_STATE_PATH)
        except FileNotFoundError:
            pass
        request = WriteState.Request()
        request.filename = FROZEN_STATE_PATH
        request.include_unfinished_submaps = True
        future = self._write_state_client.call_async(request)
        future.add_done_callback(self._finish_promote)
        self.get_logger().info("Promoting working copy into frozen baseline")

    def _finish_promote(self, future: Any) -> None:
        try:
            future.result()
            if not os.path.isfile(FROZEN_STATE_PATH):
                raise RuntimeError("Cartographer returned without creating the state file")
            os.remove(PROMOTE_REQUEST_PATH)
        except Exception as exc:  # noqa: BLE001
            self._promote_requested = False
            self.get_logger().error(f"Could not promote working copy: {exc}")
            return

        self.get_logger().info("Working copy promoted; restarting in localization mode")

        def restart() -> None:
            time.sleep(0.5)
            os._exit(0)

        threading.Thread(target=restart, daemon=True).start()

    def _check_reposition_request(self) -> None:
        if self._reposition_blocked() or not os.path.isfile(REPOSITION_REQUEST_PATH):
            return
        self._trigger_reposition("manual request")

HTML_PAGE = """<!doctype html>
<html lang="en"><head><meta charset="utf-8"/><title>Rover SLAM</title>
<style>html,body{margin:0;height:100%;background:#0b1020;color:#dce6f5;font:14px system-ui}
#meta{position:absolute;left:12px;top:12px;background:rgba(0,0,0,.45);padding:8px 10px;border-radius:8px}
canvas{display:block;width:100vw;height:100vh}</style></head>
<body><div id="meta">cartographer map…</div><canvas id="c"></canvas>
<script>
const meta=document.getElementById('meta'),canvas=document.getElementById('c'),ctx=canvas.getContext('2d');
function resize(){const d=Math.min(devicePixelRatio||1,2);canvas.width=innerWidth*d;canvas.height=innerHeight*d;ctx.setTransform(d,0,0,d,0,0)}
addEventListener('resize',resize);resize();
async function tick(){try{const r=await fetch('/slam.json',{cache:'no-store'});if(r.ok){const m=await r.json();meta.textContent=`cells=${m.occupied_count||0} src=${m.source||'?'} wp=${(m.waypoints||[]).length}`}}catch(_){}
setTimeout(tick,500)} tick();
</script></body></html>
"""


class Handler(BaseHTTPRequestHandler):
    def log_message(self, format: str, *args: Any) -> None:  # noqa: A003
        return

    def _send_json(self, code: int, obj: Any) -> None:
        body = json.dumps(obj, separators=(",", ":")).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)
        self.wfile.flush()

    def _read_json(self) -> dict[str, Any]:
        length = int(self.headers.get("Content-Length", "0") or 0)
        if length <= 0:
            return {}
        raw = self.rfile.read(length)
        try:
            data = json.loads(raw.decode("utf-8"))
            return data if isinstance(data, dict) else {}
        except json.JSONDecodeError:
            return {}

    def do_GET(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        path = parsed.path
        if path in ("/", "/index.html"):
            body = HTML_PAGE.encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        if path in ("/slam.json", "/map.json"):
            with _lock:
                payload = dict(_latest)
            self._send_json(200, payload)
            return
        if path == "/waypoints":
            self._send_json(200, {"success": True, "waypoints": load_waypoints()})
            return
        self.send_error(404)

    def do_POST(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        path = parsed.path
        if path == "/waypoints":
            body = self._read_json()
            label = str(body.get("label") or "mark")
            with _lock:
                pose = dict(_pose)
            item = add_waypoint(label, pose.get("x", 0.0), pose.get("y", 0.0), pose.get("yaw", 0.0))
            publish_live(0.0)
            self._send_json(200, {"success": True, "waypoint": item})
            return
        if path == "/map/save":
            ok = _persist.save(force=True)
            self._send_json(200, {"success": True, "saved": ok})
            return
        if path == "/map/purge":
            # The entrypoint consumes this marker before Cartographer restarts,
            # even when normal wipe-on-start persistence is disabled.
            try:
                directory = os.path.dirname(PURGE_REQUEST_PATH)
                if directory:
                    os.makedirs(directory, exist_ok=True)
                with open(PURGE_REQUEST_PATH, "w", encoding="utf-8") as handle:
                    handle.write(f"{time.time()}\n")
            except OSError as exc:
                self._send_json(500, {"success": False, "error": str(exc)})
                return
            self._send_json(202, {"success": True, "status": "purging"})

            def restart_container() -> None:
                # Let the 202 response reach the relay before terminating this
                # process; otherwise fetch reports AbortError despite success.
                time.sleep(1.0)
                os.kill(os.getppid(), signal.SIGTERM)

            threading.Thread(target=restart_container, daemon=True).start()
            return
        self.send_error(404)

    def do_DELETE(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        if parsed.path.startswith("/waypoints/"):
            wid = parsed.path.split("/waypoints/", 1)[-1]
            ok = delete_waypoint(wid)
            publish_live(0.0)
            self._send_json(200 if ok else 404, {"success": ok})
            return
        self.send_error(404)


def main() -> None:
    global _tracking_frame
    rclpy.init()
    if TRACKING_FRAME:
        _tracking_frame = TRACKING_FRAME
    frame_file = os.environ.get("SLAM_FRAME_FILE", "/tmp/ros2-slam-tracking-frame")
    if os.path.isfile(frame_file):
        try:
            with open(frame_file, encoding="utf-8") as handle:
                detected = handle.read().strip()
            if detected:
                _tracking_frame = detected
                os.environ["SLAM_TRACKING_FRAME"] = detected
        except OSError:
            pass
    laser_file = os.environ.get("SLAM_LASER_FRAME_FILE", "/tmp/ros2-slam-laser-frame")
    if os.path.isfile(laser_file) and not os.environ.get("SLAM_LASER_FRAME"):
        try:
            with open(laser_file, encoding="utf-8") as handle:
                detected_laser = handle.read().strip()
            if detected_laser:
                os.environ["SLAM_LASER_FRAME"] = detected_laser
        except OSError:
            pass

    node = SlamBridge()
    publish_live(0.0)

    def _shutdown_save(*_args: Any) -> None:
        try:
            _persist.save(force=True)
            node.get_logger().info("Persistent map saved on shutdown")
        except OSError as exc:
            node.get_logger().warn(f"Shutdown save failed: {exc}")

    def _signal_shutdown(*args: Any) -> None:
        _shutdown_save(*args)
        if rclpy.ok():
            rclpy.shutdown()

    signal.signal(signal.SIGTERM, _signal_shutdown)
    signal.signal(signal.SIGINT, _signal_shutdown)

    server = None
    try:
        server = ThreadingHTTPServer((VIEWER_HOST, VIEWER_PORT), Handler)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        node.get_logger().info(f"SLAM HTTP on http://{VIEWER_HOST}:{VIEWER_PORT}/")
    except OSError as exc:
        node.get_logger().warn(f"SLAM HTTP disabled: {exc}")

    try:
        rclpy.spin(node)
    finally:
        _shutdown_save()
        if server is not None:
            server.shutdown()
        node.destroy_node()
        if rclpy.ok():
            rclpy.shutdown()


if __name__ == "__main__":
    main()
