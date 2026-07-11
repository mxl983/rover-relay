#!/usr/bin/env python3
"""2D occupancy-grid SLAM from live LaserScan — scan-to-map pose, live scan wins."""

from __future__ import annotations

import base64
import json
import math
import os
import ssl
import threading
import time
import urllib.error
import urllib.request
import zlib
from typing import Any

import rclpy
from rclpy.node import Node
from rclpy.qos import qos_profile_sensor_data
from sensor_msgs.msg import Imu
from sensor_msgs.msg import LaserScan

SLAM_MAP_FILE = os.environ.get("SLAM_MAP_FILE_PATH", "/app/lidar/slam_map.json")
SLAM_LIVE_FILE = os.environ.get("SLAM_LIVE_FILE_PATH", "/app/lidar/slam_live.json")
SLAM_TOPIC = os.environ.get("SLAM_SCAN_TOPIC", "/scan")
SLAM_RESOLUTION = float(os.environ.get("SLAM_RESOLUTION", "0.08"))
SLAM_GRID_SIZE_M = float(os.environ.get("SLAM_GRID_SIZE_M", "48"))
SLAM_SAVE_INTERVAL_S = float(os.environ.get("SLAM_SAVE_INTERVAL_S", "3"))
SLAM_DISPLAY_RANGE_M = float(os.environ.get("SLAM_DISPLAY_RANGE_M", "10"))
SLAM_DISPLAY_MAX_POINTS = int(os.environ.get("SLAM_DISPLAY_MAX_POINTS", "500"))
SLAM_LOG_ODDS_HIT = float(os.environ.get("SLAM_LOG_ODDS_HIT", "1.1"))
SLAM_LOG_ODDS_MISS = float(os.environ.get("SLAM_LOG_ODDS_MISS", "-0.75"))
SLAM_LOG_ODDS_CLAMP = float(os.environ.get("SLAM_LOG_ODDS_CLAMP", "8"))
SLAM_LIVE_CORRECTION_MISS = float(os.environ.get("SLAM_LIVE_CORRECTION_MISS", "-1.1"))
SLAM_MATCH_MAX_DIST_M = float(os.environ.get("SLAM_MATCH_MAX_DIST_M", "0.28"))
SLAM_SCAN_MATCH_MAX_FORWARD_M = float(os.environ.get("SLAM_SCAN_MATCH_MAX_FORWARD_M", "0.65"))
SLAM_MIN_SCAN_INTERVAL_S = float(os.environ.get("SLAM_MIN_SCAN_INTERVAL_S", "0.08"))
SLAM_PURGE_ON_START = os.environ.get("SLAM_PURGE_ON_START", "false").lower() in ("1", "true", "yes")
SLAM_IMU_ENABLED = os.environ.get("SLAM_IMU_ENABLED", "true").lower() in ("1", "true", "yes")
SLAM_IMU_TOPIC = os.environ.get("SLAM_IMU_TOPIC", "/imu/data")
SLAM_IMU_HTTP_URL = os.environ.get("SLAM_IMU_HTTP_URL", "")
SLAM_IMU_HTTP_POLL_S = float(os.environ.get("SLAM_IMU_HTTP_POLL_S", "0.05"))
SLAM_IMU_HTTP_TIMEOUT_S = float(os.environ.get("SLAM_IMU_HTTP_TIMEOUT_S", "0.35"))
SLAM_IMU_HTTP_INSECURE_TLS = os.environ.get("SLAM_IMU_HTTP_INSECURE_TLS", "true").lower() in ("1", "true", "yes")
SLAM_IMU_MAX_STALE_S = float(os.environ.get("SLAM_IMU_MAX_STALE_S", "0.4"))
SLAM_IMU_MAX_YAW_RATE_RAD_S = float(os.environ.get("SLAM_IMU_MAX_YAW_RATE_RAD_S", "5.5"))
SLAM_IMU_MATCH_HINT_WEIGHT = float(os.environ.get("SLAM_IMU_MATCH_HINT_WEIGHT", "0.55"))

CELL_UNKNOWN = 0
CELL_FREE = 1
CELL_OCCUPIED = 2


class OccupancyGrid:
    def __init__(self, size_m: float, resolution: float, origin_x: float, origin_y: float) -> None:
        self.size_m = size_m
        self.resolution = resolution
        self.origin_x = origin_x
        self.origin_y = origin_y
        self.width = max(1, int(round(size_m / resolution)))
        self.height = max(1, int(round(size_m / resolution)))
        self.log_odds = [[0.0] * self.width for _ in range(self.height)]

    def world_to_cell(self, wx: float, wy: float) -> tuple[int, int]:
        ix = int(math.floor((wx - self.origin_x) / self.resolution))
        iy = int(math.floor((wy - self.origin_y) / self.resolution))
        return ix, iy

    def cell_center_world(self, ix: int, iy: int) -> tuple[float, float]:
        wx = self.origin_x + (ix + 0.5) * self.resolution
        wy = self.origin_y + (iy + 0.5) * self.resolution
        return wx, wy

    def in_bounds(self, ix: int, iy: int) -> bool:
        return 0 <= ix < self.width and 0 <= iy < self.height

    def log_odds_at(self, ix: int, iy: int) -> float:
        if not self.in_bounds(ix, iy):
            return 0.0
        return self.log_odds[iy][ix]

    def cell_class(self, ix: int, iy: int) -> int:
        value = self.log_odds_at(ix, iy)
        if value > 0.45:
            return CELL_OCCUPIED
        if value < -0.45:
            return CELL_FREE
        return CELL_UNKNOWN

    def set_log_odds(self, ix: int, iy: int, delta: float) -> None:
        if not self.in_bounds(ix, iy):
            return
        value = self.log_odds[iy][ix] + delta
        self.log_odds[iy][ix] = max(-SLAM_LOG_ODDS_CLAMP, min(SLAM_LOG_ODDS_CLAMP, value))

    def set_log_odds_absolute(self, ix: int, iy: int, value: float) -> None:
        if not self.in_bounds(ix, iy):
            return
        self.log_odds[iy][ix] = max(-SLAM_LOG_ODDS_CLAMP, min(SLAM_LOG_ODDS_CLAMP, value))

    def raycast_cells(self, ox: float, oy: float, tx: float, ty: float) -> list[tuple[int, int]]:
        sx, sy = self.world_to_cell(ox, oy)
        ex, ey = self.world_to_cell(tx, ty)
        cells: list[tuple[int, int]] = []
        if not self.in_bounds(sx, sy):
            return cells

        dx = abs(ex - sx)
        dy = abs(ey - sy)
        x, y = sx, sy
        n = max(dx, dy)
        if n <= 0:
            return cells

        x_inc = 1 if ex > sx else -1 if ex < sx else 0
        y_inc = 1 if ey > sy else -1 if ey < sy else 0
        error = dx - dy
        steps = int(n)

        for step in range(steps):
            cells.append((x, y))
            if step >= steps - 1:
                break
            error2 = 2 * error
            if error2 > -dy:
                error -= dy
                x += x_inc
            if error2 < dx:
                error += dx
                y += y_inc
            if not self.in_bounds(x, y):
                break

        if self.in_bounds(ex, ey):
            cells.append((ex, ey))
        return cells

    def update_scan_live(self, ox: float, oy: float, local_points: list[tuple[float, float]], theta: float) -> None:
        """Live scan overrides map: clear along rays, reinforce endpoints."""
        cos_t = math.cos(theta)
        sin_t = math.sin(theta)
        for lx, ly in local_points:
            wx = ox + lx * cos_t - ly * sin_t
            wy = oy + lx * sin_t + ly * cos_t
            ray_cells = self.raycast_cells(ox, oy, wx, wy)
            if not ray_cells:
                continue
            for ix, iy in ray_cells[:-1]:
                if self.cell_class(ix, iy) == CELL_OCCUPIED:
                    self.set_log_odds(ix, iy, SLAM_LIVE_CORRECTION_MISS)
                else:
                    self.set_log_odds(ix, iy, SLAM_LOG_ODDS_MISS)
            end_ix, end_iy = ray_cells[-1]
            self.set_log_odds(end_ix, end_iy, SLAM_LOG_ODDS_HIT)

    def occupied_near(self, wx: float, wy: float, radius_cells: int = 1) -> bool:
        ix, iy = self.world_to_cell(wx, wy)
        for dix in range(-radius_cells, radius_cells + 1):
            for diy in range(-radius_cells, radius_cells + 1):
                if self.cell_class(ix + dix, iy + diy) == CELL_OCCUPIED:
                    return True
        return False

    def encode_cells(self) -> str:
        packed = bytearray(self.width * self.height)
        index = 0
        for row in self.log_odds:
            for value in row:
                if value > 0.45:
                    packed[index] = CELL_OCCUPIED
                elif value < -0.45:
                    packed[index] = CELL_FREE
                else:
                    packed[index] = CELL_UNKNOWN
                index += 1
        return base64.b64encode(zlib.compress(bytes(packed), level=6)).decode("ascii")

    def decode_cells(self, cells_b64: str) -> None:
        raw = zlib.decompress(base64.b64decode(cells_b64))
        if len(raw) != self.width * self.height:
            raise ValueError("grid cell count mismatch")
        index = 0
        for iy in range(self.height):
            for ix in range(self.width):
                cell = raw[index]
                if cell == CELL_OCCUPIED:
                    self.log_odds[iy][ix] = SLAM_LOG_ODDS_CLAMP * 0.5
                elif cell == CELL_FREE:
                    self.log_odds[iy][ix] = -SLAM_LOG_ODDS_CLAMP * 0.5
                else:
                    self.log_odds[iy][ix] = 0.0
                index += 1


def scan_to_local_points(msg: LaserScan) -> list[tuple[float, float]]:
    points: list[tuple[float, float]] = []
    for index, distance in enumerate(msg.ranges):
        if not math.isfinite(distance):
            continue
        if distance < msg.range_min or distance > msg.range_max:
            continue
        angle = msg.angle_min + index * msg.angle_increment
        points.append((distance * math.cos(angle), distance * math.sin(angle)))
    return points


def decimate_points(points: list[tuple[float, float]], max_points: int) -> list[tuple[float, float]]:
    if len(points) <= max_points:
        return points
    step = len(points) / max_points
    return [points[int(i * step)] for i in range(max_points)]


def normalize_angle(theta: float) -> float:
    while theta > math.pi:
        theta -= 2 * math.pi
    while theta < -math.pi:
        theta += 2 * math.pi
    return theta


class ImuTracker:
    """Tracks latest yaw-rate sample from ROS Imu or HTTP payload."""

    def __init__(self) -> None:
        self.yaw_rate_rad_s = 0.0
        self.sample_stamp = 0.0
        self.sample_monotonic = 0.0

    def update_from_ros(self, msg: Imu) -> None:
        rate = float(msg.angular_velocity.z)
        if not math.isfinite(rate):
            return
        self.yaw_rate_rad_s = max(-SLAM_IMU_MAX_YAW_RATE_RAD_S, min(SLAM_IMU_MAX_YAW_RATE_RAD_S, rate))
        self.sample_stamp = msg.header.stamp.sec + msg.header.stamp.nanosec * 1e-9
        self.sample_monotonic = time.monotonic()

    def update_from_http(self, payload: dict[str, Any]) -> None:
        gyro = payload.get("gyro")
        if not isinstance(gyro, dict):
            return
        rate = float(gyro.get("z", 0.0))
        if not math.isfinite(rate):
            return
        self.yaw_rate_rad_s = max(-SLAM_IMU_MAX_YAW_RATE_RAD_S, min(SLAM_IMU_MAX_YAW_RATE_RAD_S, rate))
        stamp = payload.get("stamp")
        self.sample_stamp = float(stamp) if isinstance(stamp, (int, float)) else 0.0
        self.sample_monotonic = time.monotonic()

    def is_live(self) -> bool:
        if self.sample_monotonic <= 0:
            return False
        return (time.monotonic() - self.sample_monotonic) <= SLAM_IMU_MAX_STALE_S

    def dtheta_hint(self, from_stamp: float, to_stamp: float) -> float | None:
        if not self.is_live():
            return None
        dt = to_stamp - from_stamp if to_stamp > from_stamp > 0 else 0.0
        if dt <= 0:
            return None
        return self.yaw_rate_rad_s * dt


def scan_match_score(
    reference: list[tuple[float, float]],
    current: list[tuple[float, float]],
    dx: float,
    dy: float,
    dtheta: float,
    max_dist_m: float,
) -> float:
    """Score how well `current` scan aligns to `reference` after robot motion (dx, dy, dtheta)."""
    if not reference or not current:
        return 0.0
    cos_t = math.cos(dtheta)
    sin_t = math.sin(dtheta)
    max_dist_sq = max_dist_m * max_dist_m
    hits = 0
    for qx, qy in current:
        tx = cos_t * qx + sin_t * qy + dx
        ty = -sin_t * qx + cos_t * qy + dy
        for rx, ry in reference:
            if (tx - rx) ** 2 + (ty - ry) ** 2 <= max_dist_sq:
                hits += 1
                break
    return hits / len(current)


def apply_robot_motion_to_pose(
    pose_x: float,
    pose_y: float,
    pose_theta: float,
    dx: float,
    dy: float,
    dtheta: float,
) -> tuple[float, float, float]:
    """Apply incremental robot-frame motion to world pose."""
    cos_t = math.cos(pose_theta)
    sin_t = math.sin(pose_theta)
    world_dx = cos_t * dx - sin_t * dy
    world_dy = sin_t * dx + cos_t * dy
    return pose_x + world_dx, pose_y + world_dy, normalize_angle(pose_theta + dtheta)


class SlamEngine:
    def __init__(self, imu_tracker: ImuTracker | None = None) -> None:
        half = SLAM_GRID_SIZE_M / 2.0
        self.grid = OccupancyGrid(SLAM_GRID_SIZE_M, SLAM_RESOLUTION, -half, -half)
        self.pose_x = 0.0
        self.pose_y = 0.0
        self.pose_theta = 0.0
        self.last_stamp = 0.0
        self.last_process_monotonic = 0.0
        self.last_persist_monotonic = 0.0
        self.scan_count = 0
        self.loaded_from_disk = False
        self.last_match_points: list[tuple[float, float]] = []
        self.imu_tracker = imu_tracker

    def reset(self) -> None:
        half = SLAM_GRID_SIZE_M / 2.0
        self.grid = OccupancyGrid(SLAM_GRID_SIZE_M, SLAM_RESOLUTION, -half, -half)
        self.pose_x = 0.0
        self.pose_y = 0.0
        self.pose_theta = 0.0
        self.last_stamp = 0.0
        self.scan_count = 0
        self.loaded_from_disk = False
        self.last_match_points = []

    def purge_disk(self) -> None:
        try:
            if os.path.isfile(SLAM_MAP_FILE):
                os.remove(SLAM_MAP_FILE)
            if os.path.isfile(SLAM_LIVE_FILE):
                os.remove(SLAM_LIVE_FILE)
        except OSError:
            pass

    def score_pose_against_map(
        self,
        local_points: list[tuple[float, float]],
        px: float,
        py: float,
        ptheta: float,
    ) -> float:
        if not local_points:
            return 0.0
        cos_t = math.cos(ptheta)
        sin_t = math.sin(ptheta)
        hits = 0
        for lx, ly in local_points:
            wx = px + lx * cos_t - ly * sin_t
            wy = py + lx * sin_t + ly * cos_t
            if self.grid.occupied_near(wx, wy, radius_cells=1):
                hits += 1
        return hits / len(local_points)

    def estimate_pose_scan_to_scan(
        self,
        local_points: list[tuple[float, float]],
        imu_dtheta_hint: float | None = None,
    ) -> None:
        """Primary motion: match current scan to previous scan (no odometry)."""
        if len(self.last_match_points) < 8:
            return

        best_score = -1.0
        best_motion = (0.0, 0.0, 0.0)
        forward_steps = [
            round(x * 0.05, 2)
            for x in range(
                int(-0.05 / 0.05),
                int(SLAM_SCAN_MATCH_MAX_FORWARD_M / 0.05) + 1,
            )
        ]
        lateral_steps = [-0.25, -0.2, -0.15, -0.1, -0.05, 0.0, 0.05, 0.1, 0.15, 0.2, 0.25]

        if imu_dtheta_hint is not None and math.isfinite(imu_dtheta_hint):
            imu_deg = math.degrees(imu_dtheta_hint)
            imu_deg = max(-20.0, min(20.0, imu_deg))
            dtheta_candidates_deg = [imu_deg + step for step in (-8, -6, -4, -2, 0, 2, 4, 6, 8)]
        else:
            dtheta_candidates_deg = list(range(-14, 15, 2))

        for dtheta_deg in dtheta_candidates_deg:
            dtheta = math.radians(dtheta_deg)
            for dx in forward_steps:
                for dy in lateral_steps:
                    score = scan_match_score(
                        self.last_match_points,
                        local_points,
                        dx,
                        dy,
                        dtheta,
                        SLAM_MATCH_MAX_DIST_M,
                    )
                    if score > best_score:
                        best_score = score
                        best_motion = (dx, dy, dtheta)

        dx, dy, dtheta = best_motion
        if best_score < 0.12:
            return

        if imu_dtheta_hint is not None and math.isfinite(imu_dtheta_hint):
            weight = max(0.0, min(1.0, SLAM_IMU_MATCH_HINT_WEIGHT))
            dtheta = (1.0 - weight) * dtheta + weight * imu_dtheta_hint

        self.pose_x, self.pose_y, self.pose_theta = apply_robot_motion_to_pose(
            self.pose_x,
            self.pose_y,
            self.pose_theta,
            dx,
            dy,
            dtheta,
        )

    def estimate_pose_scan_to_map(self, local_points: list[tuple[float, float]]) -> None:
        """Light map correction after scan-to-scan."""
        if self.scan_count < 5:
            return

        best_score = -1.0
        best_pose = (self.pose_x, self.pose_y, self.pose_theta)

        for dtheta_deg in (-6, -3, 0, 3, 6):
            dtheta = math.radians(dtheta_deg)
            for dx in (-0.12, -0.06, 0.0, 0.06, 0.12):
                for dy in (-0.12, -0.06, 0.0, 0.06, 0.12):
                    trial_theta = normalize_angle(self.pose_theta + dtheta)
                    trial_x = self.pose_x + dx
                    trial_y = self.pose_y + dy
                    score = self.score_pose_against_map(local_points, trial_x, trial_y, trial_theta)
                    if score > best_score:
                        best_score = score
                        best_pose = (trial_x, trial_y, trial_theta)

        if best_score >= 0.18:
            self.pose_x, self.pose_y, self.pose_theta = best_pose

    def build_robot_frame_map_points(self) -> list[dict[str, float]]:
        cos_t = math.cos(-self.pose_theta)
        sin_t = math.sin(-self.pose_theta)
        max_range_sq = SLAM_DISPLAY_RANGE_M * SLAM_DISPLAY_RANGE_M
        points: list[tuple[float, float, float]] = []

        for iy in range(self.grid.height):
            for ix in range(self.grid.width):
                if self.grid.cell_class(ix, iy) != CELL_OCCUPIED:
                    continue
                wx, wy = self.grid.cell_center_world(ix, iy)
                dx = wx - self.pose_x
                dy = wy - self.pose_y
                if dx * dx + dy * dy > max_range_sq:
                    continue
                lx = dx * cos_t - dy * sin_t
                ly = dx * sin_t + dy * cos_t
                dist = math.hypot(lx, ly)
                points.append((lx, ly, dist))

        points.sort(key=lambda item: item[2])
        decimated = decimate_points([(lx, ly) for lx, ly, _dist in points], SLAM_DISPLAY_MAX_POINTS)
        return [{"x": round(lx, 3), "y": round(ly, 3)} for lx, ly in decimated]

    def process_scan(self, msg: LaserScan) -> None:
        stamp = msg.header.stamp.sec + msg.header.stamp.nanosec * 1e-9
        now = time.monotonic()
        if now - self.last_process_monotonic < SLAM_MIN_SCAN_INTERVAL_S and self.scan_count > 0:
            return

        local_points = scan_to_local_points(msg)
        if len(local_points) < 8:
            return

        match_points = decimate_points(local_points, 90)
        imu_dtheta_hint = (
            self.imu_tracker.dtheta_hint(self.last_stamp, stamp)
            if self.imu_tracker is not None
            else None
        )
        if self.scan_count >= 1:
            self.estimate_pose_scan_to_scan(match_points, imu_dtheta_hint=imu_dtheta_hint)
            self.estimate_pose_scan_to_map(match_points)

        self.grid.update_scan_live(self.pose_x, self.pose_y, local_points, self.pose_theta)
        self.last_match_points = match_points

        self.last_stamp = stamp
        self.scan_count += 1
        self.last_process_monotonic = now

        self.save_live()

        if self.scan_count == 1 or now - self.last_persist_monotonic >= SLAM_SAVE_INTERVAL_S:
            self.last_persist_monotonic = now
            self.save_to_disk()

    def to_live_payload(self) -> dict[str, Any]:
        return {
            "version": 2,
            "stamp": self.last_stamp,
            "updated_at": time.time(),
            "scan_count": self.scan_count,
            "pose": {
                "x": round(self.pose_x, 4),
                "y": round(self.pose_y, 4),
                "theta_deg": round(math.degrees(self.pose_theta), 2),
            },
            "map_points": self.build_robot_frame_map_points(),
        }

    def save_live(self) -> None:
        payload = self.to_live_payload()
        try:
            os.makedirs(os.path.dirname(SLAM_LIVE_FILE), exist_ok=True)
            tmp_path = f"{SLAM_LIVE_FILE}.tmp"
            with open(tmp_path, "w", encoding="utf-8") as handle:
                json.dump(payload, handle, separators=(",", ":"))
            os.replace(tmp_path, SLAM_LIVE_FILE)
        except OSError:
            pass

    def to_payload(self) -> dict[str, Any]:
        return {
            "version": 2,
            "stamp": self.last_stamp,
            "updated_at": time.time(),
            "scan_count": self.scan_count,
            "pose": {
                "x": round(self.pose_x, 4),
                "y": round(self.pose_y, 4),
                "theta_deg": round(math.degrees(self.pose_theta), 2),
            },
            "resolution": self.grid.resolution,
            "width": self.grid.width,
            "height": self.grid.height,
            "origin_x": self.grid.origin_x,
            "origin_y": self.grid.origin_y,
            "cells_b64": self.grid.encode_cells(),
            "map_points": self.build_robot_frame_map_points(),
        }

    def load_from_disk(self) -> bool:
        if not os.path.isfile(SLAM_MAP_FILE):
            return False
        try:
            with open(SLAM_MAP_FILE, encoding="utf-8") as handle:
                data = json.load(handle)
            self.pose_x = float(data.get("pose", {}).get("x", 0.0))
            self.pose_y = float(data.get("pose", {}).get("y", 0.0))
            self.pose_theta = math.radians(float(data.get("pose", {}).get("theta_deg", 0.0)))
            self.last_stamp = float(data.get("stamp", 0.0))
            self.scan_count = int(data.get("scan_count", 0))
            resolution = float(data.get("resolution", SLAM_RESOLUTION))
            width = int(data.get("width", self.grid.width))
            height = int(data.get("height", self.grid.height))
            origin_x = float(data.get("origin_x", self.grid.origin_x))
            origin_y = float(data.get("origin_y", self.grid.origin_y))
            size_m = max(SLAM_GRID_SIZE_M, width * resolution, height * resolution)
            self.grid = OccupancyGrid(size_m, resolution, origin_x, origin_y)
            if data.get("cells_b64"):
                self.grid.decode_cells(data["cells_b64"])
            self.loaded_from_disk = True
            return True
        except (OSError, ValueError, json.JSONDecodeError, TypeError):
            return False

    def save_to_disk(self) -> None:
        payload = self.to_payload()
        try:
            os.makedirs(os.path.dirname(SLAM_MAP_FILE), exist_ok=True)
            tmp_path = f"{SLAM_MAP_FILE}.tmp"
            with open(tmp_path, "w", encoding="utf-8") as handle:
                json.dump(payload, handle, separators=(",", ":"))
            os.replace(tmp_path, SLAM_MAP_FILE)
        except OSError:
            pass


_engine_lock = threading.Lock()
_imu_tracker = ImuTracker()
_engine = SlamEngine(imu_tracker=_imu_tracker)


class SlamMapperNode(Node):
    def __init__(self) -> None:
        super().__init__("slam_mapper")
        with _engine_lock:
            if SLAM_PURGE_ON_START:
                _engine.purge_disk()
                _engine.reset()
                self.get_logger().info("Purged SLAM map on start")
            elif _engine.load_from_disk():
                self.get_logger().info(f"Loaded SLAM map from {SLAM_MAP_FILE}")
            else:
                self.get_logger().info("Starting new SLAM map")
        self.create_subscription(LaserScan, SLAM_TOPIC, self._on_scan, qos_profile_sensor_data)
        self._imu_ros_sub = None
        self._imu_http_timer = None
        if SLAM_IMU_ENABLED:
            if SLAM_IMU_TOPIC:
                self._imu_ros_sub = self.create_subscription(
                    Imu,
                    SLAM_IMU_TOPIC,
                    self._on_imu,
                    qos_profile_sensor_data,
                )
            if SLAM_IMU_HTTP_URL:
                self._imu_http_timer = self.create_timer(SLAM_IMU_HTTP_POLL_S, self._poll_imu_http)

        imu_sources: list[str] = []
        if SLAM_IMU_ENABLED and SLAM_IMU_TOPIC:
            imu_sources.append(f"ROS:{SLAM_IMU_TOPIC}")
        if SLAM_IMU_ENABLED and SLAM_IMU_HTTP_URL:
            imu_sources.append(f"HTTP:{SLAM_IMU_HTTP_URL}")
        imu_note = ", ".join(imu_sources) if imu_sources else "disabled"
        self.get_logger().info(f"SLAM scan-to-scan + map on {SLAM_TOPIC}; IMU sources: {imu_note}")

    def _on_scan(self, msg: LaserScan) -> None:
        with _engine_lock:
            _engine.process_scan(msg)

    def _on_imu(self, msg: Imu) -> None:
        with _engine_lock:
            _imu_tracker.update_from_ros(msg)

    def _poll_imu_http(self) -> None:
        if not SLAM_IMU_HTTP_URL:
            return
        context = None
        if SLAM_IMU_HTTP_INSECURE_TLS:
            context = ssl._create_unverified_context()

        try:
            request = urllib.request.Request(SLAM_IMU_HTTP_URL, headers={"Accept": "application/json"})
            with urllib.request.urlopen(
                request,
                timeout=SLAM_IMU_HTTP_TIMEOUT_S,
                context=context,
            ) as response:
                body = response.read().decode("utf-8")
            parsed = json.loads(body)
            payload = parsed.get("data") if isinstance(parsed, dict) else None
            if isinstance(payload, dict):
                with _engine_lock:
                    _imu_tracker.update_from_http(payload)
        except (urllib.error.URLError, TimeoutError, ValueError, json.JSONDecodeError):
            # Best-effort source, keep SLAM running even if endpoint is down.
            return

    def destroy_node(self) -> bool:
        with _engine_lock:
            _engine.save_live()
            _engine.save_to_disk()
        return super().destroy_node()


def main() -> None:
    rclpy.init()
    node = SlamMapperNode()
    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    finally:
        node.destroy_node()
        rclpy.shutdown()


if __name__ == "__main__":
    main()
