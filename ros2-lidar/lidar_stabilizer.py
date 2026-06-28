#!/usr/bin/env python3
"""Temporal LiDAR stabilizer — Task 1: rolling buffer + confidence decay."""

from __future__ import annotations

import math
import os
import time
from dataclasses import dataclass, field
from typing import Any

import rclpy
from rclpy.node import Node
from rclpy.qos import qos_profile_sensor_data
from sensor_msgs.msg import LaserScan

try:
    import tf2_geometry_msgs  # noqa: F401 — registers PointStamped with tf2
    import tf2_ros
    from geometry_msgs.msg import PointStamped
    from tf2_ros import TransformException

    TF2_AVAILABLE = True
except ImportError:
    TF2_AVAILABLE = False


def _env_float(name: str, default: float) -> float:
    raw = os.environ.get(name)
    if raw is None or raw == "":
        return default
    return float(raw)


def _env_int(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if raw is None or raw == "":
        return default
    return int(raw)


HISTORY_FRAMES = _env_int("LIDAR_STABILIZER_FRAMES", 8)
CONFIDENCE_DECAY = _env_float("LIDAR_STABILIZER_DECAY", 0.75)
HIT_BOOST = _env_float("LIDAR_STABILIZER_HIT_BOOST", 0.35)
MIN_CONFIDENCE = _env_float("LIDAR_STABILIZER_MIN_CONFIDENCE", 0.25)
BIN_DEG = _env_float("LIDAR_STABILIZER_BIN_DEG", 2.0)
POSITION_ALPHA = _env_float("LIDAR_STABILIZER_POSITION_ALPHA", 0.35)
TARGET_FRAME = os.environ.get("LIDAR_STABILIZER_TARGET_FRAME", "")
INPUT_TOPIC = os.environ.get("LIDAR_TOPIC", "/scan")
OUTPUT_TOPIC = os.environ.get("LIDAR_STABILIZED_TOPIC", "/stabilized_scan")
LOG_INTERVAL_S = _env_float("LIDAR_STABILIZER_LOG_INTERVAL_S", 2.0)


def normalize_deg(deg: float) -> float:
    return ((deg % 360.0) + 360.0) % 360.0


def bin_key_for_angle(angle_deg: float, bin_deg: float = BIN_DEG) -> int:
    snapped = round(normalize_deg(angle_deg) / bin_deg) * int(bin_deg)
    return int(((snapped % 360) + 360) % 360)


@dataclass
class RawPoint:
    x: float
    y: float
    range: float
    angle_deg: float


@dataclass
class TrackedPoint:
    x: float
    y: float
    range: float
    angle_deg: float
    confidence: float
    last_seen: float
    hit_count: int = 0


@dataclass
class StabilizerStats:
    raw_points: int = 0
    filtered_points: int = 0
    tracked_bins: int = 0
    history_frames: int = 0
    tf_ok: bool = True


class TemporalAccumulator:
    """Rolling temporal buffer with per-angle confidence decay."""

    def __init__(
        self,
        *,
        history_frames: int = HISTORY_FRAMES,
        confidence_decay: float = CONFIDENCE_DECAY,
        hit_boost: float = HIT_BOOST,
        min_confidence: float = MIN_CONFIDENCE,
        bin_deg: float = BIN_DEG,
        position_alpha: float = POSITION_ALPHA,
    ) -> None:
        self.history_frames = max(1, history_frames)
        self.confidence_decay = confidence_decay
        self.hit_boost = hit_boost
        self.min_confidence = min_confidence
        self.bin_deg = bin_deg
        self.position_alpha = position_alpha
        self._bins: dict[int, TrackedPoint] = {}
        self._frame_count = 0

    def ingest(self, points: list[RawPoint], now: float | None = None) -> StabilizerStats:
        now = time.monotonic() if now is None else now
        self._frame_count += 1
        hit_keys: set[int] = set()

        for point in points:
            # Bin by original laser-frame angle (stable per beam), not post-TF cartesian angle.
            key = bin_key_for_angle(point.angle_deg, self.bin_deg)
            hit_keys.add(key)
            existing = self._bins.get(key)
            if existing is None:
                self._bins[key] = TrackedPoint(
                    x=point.x,
                    y=point.y,
                    range=point.range,
                    angle_deg=float(key),
                    confidence=min(1.0, self.hit_boost),
                    last_seen=now,
                    hit_count=1,
                )
                continue

            alpha = self.position_alpha
            # Smooth range only; keep x/y derived from bin angle + smoothed range.
            existing.range = existing.range + (point.range - existing.range) * alpha
            rad = math.radians(float(key))
            existing.x = existing.range * math.cos(rad)
            existing.y = existing.range * math.sin(rad)
            existing.confidence = min(1.0, existing.confidence + self.hit_boost)
            existing.last_seen = now
            existing.hit_count += 1

        for key, tracked in list(self._bins.items()):
            if key in hit_keys:
                continue
            tracked.confidence *= self.confidence_decay
            if tracked.confidence < self.min_confidence:
                del self._bins[key]

        filtered = self.filtered_points()
        return StabilizerStats(
            raw_points=len(points),
            filtered_points=len(filtered),
            tracked_bins=len(self._bins),
            history_frames=min(self._frame_count, self.history_frames),
            tf_ok=True,
        )

    def filtered_points(self) -> list[TrackedPoint]:
        return [
            point
            for point in self._bins.values()
            if point.confidence >= self.min_confidence
        ]

    def to_payload_points(self) -> list[dict[str, float]]:
        points = sorted(self.filtered_points(), key=lambda p: p.angle_deg)
        return [
            {
                "x": round(p.x, 3),
                "y": round(p.y, 3),
                "r": round(p.range, 3),
                "a_deg": round(p.angle_deg, 1),
                "confidence": round(p.confidence, 3),
            }
            for p in points
        ]


def laser_scan_to_raw_points(msg: LaserScan) -> list[RawPoint]:
    points: list[RawPoint] = []
    for index, distance in enumerate(msg.ranges):
        if not math.isfinite(distance):
            continue
        if distance < msg.range_min or distance > msg.range_max:
            continue
        angle = msg.angle_min + index * msg.angle_increment
        points.append(
            RawPoint(
                x=distance * math.cos(angle),
                y=distance * math.sin(angle),
                range=distance,
                angle_deg=math.degrees(angle),
            )
        )
    return points


def filtered_points_to_laser_scan(
    source: LaserScan,
    tracked: list[TrackedPoint],
    bin_deg: float = BIN_DEG,
) -> LaserScan:
    out = LaserScan()
    out.header = source.header
    out.angle_min = source.angle_min
    out.angle_max = source.angle_max
    out.angle_increment = math.radians(bin_deg)
    out.time_increment = source.time_increment
    out.scan_time = source.scan_time
    out.range_min = source.range_min
    out.range_max = source.range_max

    bin_count = int(round((out.angle_max - out.angle_min) / out.angle_increment)) + 1
    out.ranges = [float("inf")] * bin_count
    out.intensities = [0.0] * bin_count

    for point in tracked:
        angle = math.radians(point.angle_deg)
        index = int(round((angle - out.angle_min) / out.angle_increment))
        if index < 0 or index >= bin_count:
            continue
        out.ranges[index] = point.range
        out.intensities[index] = point.confidence

    return out


@dataclass
class TfTransformer:
    buffer: Any = field(default=None)
    target_frame: str = TARGET_FRAME
    warned: bool = False

    def transform_points(
        self,
        msg: LaserScan,
        points: list[RawPoint],
        logger: Any | None = None,
    ) -> tuple[list[RawPoint], bool]:
        if not TF2_AVAILABLE or self.buffer is None:
            return points, False

        source_frame = msg.header.frame_id
        if not self.target_frame or not source_frame or source_frame == self.target_frame:
            return points, True

        transformed: list[RawPoint] = []
        stamp = msg.header.stamp
        for point in points:
            stamped = PointStamped()
            stamped.header.stamp = stamp
            stamped.header.frame_id = source_frame
            stamped.point.x = point.x
            stamped.point.y = point.y
            stamped.point.z = 0.0
            try:
                out = self.buffer.transform(stamped, self.target_frame)
            except (TransformException, Exception):
                if logger is not None and not self.warned:
                    logger.warning(
                        f"TF {source_frame}->{self.target_frame} unavailable; using laser frame"
                    )
                    self.warned = True
                return points, False
            transformed.append(
                RawPoint(
                    x=out.point.x,
                    y=out.point.y,
                    range=math.hypot(out.point.x, out.point.y),
                    angle_deg=math.degrees(math.atan2(out.point.y, out.point.x)),
                )
            )
        return transformed, True


class LidarStabilizerNode(Node):
    def __init__(self) -> None:
        super().__init__("lidar_stabilizer")
        self._accumulator = TemporalAccumulator()
        self._publisher = self.create_publisher(LaserScan, OUTPUT_TOPIC, 10)
        self._last_log = 0.0
        self._last_stats = StabilizerStats()

        self._tf = TfTransformer(target_frame=TARGET_FRAME)
        if TF2_AVAILABLE:
            self._tf.buffer = tf2_ros.Buffer()
            self._tf_listener = tf2_ros.TransformListener(self._tf.buffer, self)

        self.create_subscription(
            LaserScan,
            INPUT_TOPIC,
            self._on_scan,
            qos_profile_sensor_data,
        )
        self.get_logger().info(
            f"Stabilizer {INPUT_TOPIC} -> {OUTPUT_TOPIC} "
            f"(frames={HISTORY_FRAMES}, decay={CONFIDENCE_DECAY}, "
            f"min_conf={MIN_CONFIDENCE}, bin={BIN_DEG}°)"
        )

    @property
    def accumulator(self) -> TemporalAccumulator:
        return self._accumulator

    @property
    def last_stats(self) -> StabilizerStats:
        return self._last_stats

    def _on_scan(self, msg: LaserScan) -> None:
        raw_points = laser_scan_to_raw_points(msg)
        points, tf_ok = self._tf.transform_points(msg, raw_points, self.get_logger())
        stats = self._accumulator.ingest(points)
        stats.tf_ok = tf_ok
        self._last_stats = stats

        stabilized = filtered_points_to_laser_scan(msg, self._accumulator.filtered_points())
        if tf_ok and TARGET_FRAME:
            stabilized.header.frame_id = TARGET_FRAME
        self._publisher.publish(stabilized)

        now = time.monotonic()
        if now - self._last_log >= LOG_INTERVAL_S:
            self._last_log = now
            self.get_logger().info(
                "stabilizer "
                f"raw={stats.raw_points} filtered={stats.filtered_points} "
                f"bins={stats.tracked_bins} frames={stats.history_frames} "
                f"tf={'ok' if stats.tf_ok else 'laser-frame'}"
            )


def main() -> None:
    rclpy.init()
    node = LidarStabilizerNode()
    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    finally:
        node.destroy_node()
        rclpy.shutdown()


if __name__ == "__main__":
    main()
