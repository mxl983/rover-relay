#!/usr/bin/env python3
"""Live 2D LiDAR radar viewer over HTTP."""

from __future__ import annotations

import json
import math
import os
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

import rclpy
from rclpy.node import Node
from rclpy.qos import qos_profile_sensor_data
from sensor_msgs.msg import LaserScan

VIEWER_PORT = int(os.environ.get("LIDAR_VIEWER_PORT", "8765"))
VIEWER_HOST = os.environ.get("LIDAR_VIEWER_HOST", "0.0.0.0")
SCAN_FILE_PATH = os.environ.get("LIDAR_SCAN_FILE_PATH", "/app/lidar/scan.json")
MAX_POINTS = int(os.environ.get("LIDAR_WS_MAX_POINTS", "120"))
VIEW_TOPIC = os.environ.get("LIDAR_VIEW_TOPIC", "/stabilized_scan")

_latest: dict[str, Any] = {
    "stamp": 0.0,
    "frame_id": "",
    "points": [],
    "count": 0,
    "valid": 0,
    "nearest": None,
    "farthest": None,
    "hz": 0.0,
}
_lock = threading.Lock()
_scan_times: list[float] = []


def decimate_points(points: list[dict[str, float]], max_points: int) -> list[dict[str, float]]:
    if max_points <= 0 or len(points) <= max_points:
        return points
    step = len(points) / max_points
    return [points[int(i * step)] for i in range(max_points)]


def scan_to_payload(msg: LaserScan) -> dict[str, Any]:
    points: list[dict[str, float]] = []
    valid_ranges: list[float] = []

    for index, distance in enumerate(msg.ranges):
        if not math.isfinite(distance):
            continue
        if distance < msg.range_min or distance > msg.range_max:
            continue
        valid_ranges.append(distance)
        angle = msg.angle_min + index * msg.angle_increment
        lx = distance * math.cos(angle)
        ly = distance * math.sin(angle)
        confidence = None
        if index < len(msg.intensities) and math.isfinite(msg.intensities[index]):
            intensity = msg.intensities[index]
            # Stabilizer encodes 0–1 confidence in intensities; raw LD19 intensities are much larger.
            if 0 < intensity <= 1.0:
                confidence = round(intensity, 3)
        point: dict[str, float] = {
            "x": round(lx, 3),
            "y": round(ly, 3),
            "r": round(distance, 3),
            "a_deg": round(math.degrees(angle), 1),
        }
        if confidence is not None:
            point["confidence"] = confidence
        points.append(point)

    points = decimate_points(points, MAX_POINTS)

    stamp = msg.header.stamp.sec + msg.header.stamp.nanosec * 1e-9
    now = time.monotonic()
    global _scan_times
    _scan_times = [t for t in _scan_times if now - t <= 2.0]
    _scan_times.append(now)
    hz = len(_scan_times) / 2.0 if _scan_times else 0.0

    return {
        "stamp": stamp,
        "frame_id": msg.header.frame_id,
        "points": points,
        "count": len(msg.ranges),
        "valid": len(valid_ranges),
        "nearest": round(min(valid_ranges), 2) if valid_ranges else None,
        "farthest": round(max(valid_ranges), 2) if valid_ranges else None,
        "hz": round(hz, 1),
        "angle_min_deg": round(math.degrees(msg.angle_min), 1),
        "angle_max_deg": round(math.degrees(msg.angle_max), 1),
        "angle_increment_deg": round(math.degrees(msg.angle_increment), 2),
    }


HTML_PAGE = """<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>Rover LiDAR</title>
  <style>
    :root { color-scheme: dark; }
    * { box-sizing: border-box; }
    body {
      margin: 0;
      min-height: 100vh;
      font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
      background: #0b1020;
      color: #d7e3ff;
      display: grid;
      place-items: center;
      padding: 16px;
    }
    .panel {
      width: min(92vw, 720px);
      background: #121a2f;
      border: 1px solid #2a3b66;
      border-radius: 14px;
      padding: 16px;
      box-shadow: 0 12px 40px rgba(0,0,0,.35);
    }
    h1 {
      margin: 0 0 8px;
      font-size: 1rem;
      letter-spacing: .08em;
      text-transform: uppercase;
      color: #8fb4ff;
    }
    .stats {
      display: flex;
      flex-wrap: wrap;
      gap: 10px 18px;
      font-size: .82rem;
      color: #9eb5e8;
      margin-bottom: 12px;
    }
    .stats strong { color: #e8f0ff; }
    canvas {
      width: 100%;
      aspect-ratio: 1;
      display: block;
      background: radial-gradient(circle at center, #0f1a33 0%, #070b16 70%);
      border-radius: 10px;
      border: 1px solid #22345d;
    }
    .status { margin-top: 10px; font-size: .78rem; color: #6f86b8; }
    .status.live { color: #5dffa8; }
    .status.stale { color: #ffb35d; }
  </style>
</head>
<body>
  <div class="panel">
    <h1>LD19 LiDAR — live</h1>
    <div class="stats" id="stats">Connecting…</div>
    <canvas id="radar" width="640" height="640"></canvas>
    <div class="status" id="status">Waiting for /scan…</div>
  </div>
  <script>
    const canvas = document.getElementById('radar');
    const ctx = canvas.getContext('2d');
    const statsEl = document.getElementById('stats');
    const statusEl = document.getElementById('status');
    let lastStamp = 0;

    function draw(scan) {
      const w = canvas.width, h = canvas.height;
      const cx = w / 2, cy = h / 2;
      const maxR = Math.min(cx, cy) - 18;
      const rangeMax = 6;

      ctx.clearRect(0, 0, w, h);

      ctx.strokeStyle = '#22345d';
      ctx.lineWidth = 1;
      for (let ring = 1; ring <= 3; ring++) {
        const r = (maxR * ring) / 3;
        ctx.beginPath();
        ctx.arc(cx, cy, r, 0, Math.PI * 2);
        ctx.stroke();
      }
      ctx.beginPath();
      ctx.moveTo(cx, cy - maxR); ctx.lineTo(cx, cy + maxR);
      ctx.moveTo(cx - maxR, cy); ctx.lineTo(cx + maxR, cy);
      ctx.stroke();

      ctx.fillStyle = 'rgba(93, 255, 168, 0.65)';
      ctx.font = '12px ui-monospace, monospace';
      ctx.textAlign = 'center';
      ctx.textBaseline = 'middle';
      const inset = 20;
      [['90°', cx, cy - maxR + inset], ['180°', cx - maxR + inset, cy],
       ['270°', cx, cy + maxR - inset], ['0°', cx + maxR - inset, cy]].forEach(([label, x, y]) => {
        ctx.fillText(label, x, y);
      });

      ctx.fillStyle = '#5dffa8';
      ctx.beginPath();
      ctx.moveTo(cx, cy - 10);
      ctx.lineTo(cx - 7, cy + 8);
      ctx.lineTo(cx + 7, cy + 8);
      ctx.closePath();
      ctx.fill();

      for (const p of scan.points) {
        const lx = p.x ?? 0;
        const ly = p.y ?? 0;
        const range = p.r ?? Math.hypot(lx, ly);
        const scaled = (range / rangeMax) * maxR;
        const unitX = range > 0 ? lx / range : 0;
        const unitY = range > 0 ? ly / range : 0;
        const x = cx + unitX * scaled;
        const y = cy - unitY * scaled;
        const intensity = Math.max(0.25, 1 - range / rangeMax);
        ctx.fillStyle = `rgba(93, 255, 168, ${intensity})`;
        ctx.fillRect(x - 1.5, y - 1.5, 3, 3);
      }
    }

    async function poll() {
      try {
        const res = await fetch('/scan.json', { cache: 'no-store' });
        const scan = await res.json();
        draw(scan);
        statsEl.innerHTML = [
          `<span>rate <strong>${scan.hz || 0} Hz</strong></span>`,
          `<span>points <strong>${scan.valid}/${scan.count}</strong></span>`,
          `<span>nearest <strong>${scan.nearest ?? '—'} m</strong></span>`,
          `<span>θ <strong>${scan.angle_min_deg ?? '—'}°→${scan.angle_max_deg ?? '—'}°</strong></span>`,
          `<span>Δ <strong>${scan.angle_increment_deg ?? '—'}°</strong></span>`,
          `<span>frame <strong>${scan.frame_id || '—'}</strong></span>`,
        ].join('');
        const fresh = scan.valid > 0 && scan.stamp !== lastStamp;
        if (fresh) lastStamp = scan.stamp;
        statusEl.textContent = fresh ? 'Live — receiving /scan from rover' : 'Connected — waiting for new scan';
        statusEl.className = 'status ' + (fresh ? 'live' : 'stale');
      } catch (err) {
        statusEl.textContent = 'Disconnected — retrying…';
        statusEl.className = 'status stale';
      }
      setTimeout(poll, 100);
    }
    poll();
  </script>
</body>
</html>
"""


class ViewerHandler(BaseHTTPRequestHandler):
    def log_message(self, format: str, *args: Any) -> None:
        return

    def do_GET(self) -> None:
        if self.path in ("/", "/index.html"):
            body = HTML_PAGE.encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return

        if self.path == "/scan.json":
            with _lock:
                body = json.dumps(_latest).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return

        self.send_response(404)
        self.end_headers()


class ScanViewer(Node):
    def __init__(self) -> None:
        super().__init__("scan_viewer")
        topic = os.environ.get("LIDAR_VIEW_TOPIC", VIEW_TOPIC)
        self.create_subscription(LaserScan, topic, self._on_scan, qos_profile_sensor_data)
        self.get_logger().info(f"Viewer listening on {topic}")

    def _on_scan(self, msg: LaserScan) -> None:
        payload = scan_to_payload(msg)
        with _lock:
            _latest.update(payload)
        try:
            os.makedirs(os.path.dirname(SCAN_FILE_PATH), exist_ok=True)
            tmp_path = f"{SCAN_FILE_PATH}.tmp"
            with open(tmp_path, "w", encoding="utf-8") as handle:
                json.dump(payload, handle)
            os.replace(tmp_path, SCAN_FILE_PATH)
        except OSError:
            pass


def start_http_server() -> ThreadingHTTPServer:
    server = ThreadingHTTPServer((VIEWER_HOST, VIEWER_PORT), ViewerHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server


def main() -> None:
    server = start_http_server()
    print(f"ros2-lidar: viewer http://{VIEWER_HOST}:{VIEWER_PORT}/", flush=True)

    rclpy.init()
    node = ScanViewer()
    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    finally:
        node.destroy_node()
        rclpy.shutdown()
        server.shutdown()


if __name__ == "__main__":
    main()
