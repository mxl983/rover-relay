#!/usr/bin/env python3
"""ROS2 plant bridge: sim world as Cartographer stand-in for real Nav2.

Publishes the same interfaces Nav2 expects from SLAM:
  /map, /scan_nav, TF map→odom→base_link

Consumes the same motor path as the Pi:
  HTTP POST /api/navigation/drive {drive:{x,y}}  (NAV_DRIVE_URL)

Run with real ros2-nav (slam:=True) on the same ROS_DOMAIN_ID so sim issues
track physical rover Nav2 issues.
"""

from __future__ import annotations

import json
import math
import os
import threading
import time
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

import rclpy
from geometry_msgs.msg import TransformStamped
from nav_msgs.msg import OccupancyGrid, Path
from rclpy.node import Node
from rclpy.qos import (
    DurabilityPolicy,
    HistoryPolicy,
    QoSProfile,
    ReliabilityPolicy,
    qos_profile_sensor_data,
)
from sensor_msgs.msg import LaserScan
from tf2_ros import TransformBroadcaster

from .engine import (
    FREE,
    GRID_RESOLUTION_M,
    LIDAR_ANGULAR_RES_DEG,
    LIDAR_MIN_RANGE_M,
    LIDAR_RANGE_M,
    LIDAR_RAY_COUNT,
    OCCUPIED,
    UNKNOWN,
    SlamNavSimulation,
)
from .gui import SimulatorServer

MAP_TOPIC = os.environ.get("SIM_MAP_TOPIC", "/map")
SCAN_TOPIC = os.environ.get("SIM_SCAN_TOPIC", "/scan_nav")
PLAN_TOPIC = os.environ.get("SIM_PLAN_TOPIC", "/plan")
BASE_FRAME = os.environ.get("SIM_BASE_FRAME", "base_link")
ODOM_FRAME = os.environ.get("SIM_ODOM_FRAME", "odom")
MAP_FRAME = os.environ.get("SIM_MAP_FRAME", "map")
DRIVE_HOST = os.environ.get("SIM_DRIVE_HOST", "0.0.0.0")
DRIVE_PORT = int(os.environ.get("SIM_DRIVE_PORT", "8878"))
GUI_HOST = os.environ.get("SIM_GUI_HOST", "127.0.0.1")
GUI_PORT = int(os.environ.get("SIM_GUI_PORT", "8877"))
GOAL_URL = os.environ.get("SIM_NAV_GOAL_URL", "http://127.0.0.1:8768/goto")
CANCEL_URL = os.environ.get("SIM_NAV_CANCEL_URL", "http://127.0.0.1:8768/cancel")
STEP_HZ = float(os.environ.get("SIM_STEP_HZ", "30"))
MAP_HZ = float(os.environ.get("SIM_MAP_HZ", "5.0"))
REVEAL_ON_START = os.environ.get("SIM_REVEAL_MAP", "true").lower() in (
    "1",
    "true",
    "yes",
    "on",
)
SCENARIO = os.environ.get("SIM_SCENARIO", "").strip() or None
ENABLE_GUI = os.environ.get("SIM_GUI", "true").lower() in ("1", "true", "yes", "on")


def _yaw_to_quat(yaw: float) -> tuple[float, float, float, float]:
    half = 0.5 * float(yaw)
    return (0.0, 0.0, math.sin(half), math.cos(half))


def _post_json(url: str, payload: dict[str, Any], timeout: float = 2.0) -> dict[str, Any]:
    import urllib.error
    import urllib.request

    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=data,
        method="POST",
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8")
            return json.loads(raw) if raw else {"ok": True}
    except (urllib.error.URLError, TimeoutError, OSError, json.JSONDecodeError) as err:
        return {"success": False, "error": str(err)}


class SimPlantNode(Node):
    """Owns the kinematic world and publishes SLAM-shaped ROS interfaces."""

    def __init__(self, sim: SlamNavSimulation, lock: threading.Lock) -> None:
        super().__init__("sim_plant")
        self._sim = sim
        self._lock = lock
        self._tf = TransformBroadcaster(self)
        map_qos = QoSProfile(
            depth=1,
            durability=DurabilityPolicy.TRANSIENT_LOCAL,
            reliability=ReliabilityPolicy.RELIABLE,
            history=HistoryPolicy.KEEP_LAST,
        )
        self._map_pub = self.create_publisher(OccupancyGrid, MAP_TOPIC, map_qos)
        self._scan_pub = self.create_publisher(
            LaserScan, SCAN_TOPIC, qos_profile_sensor_data
        )
        plan_qos = QoSProfile(
            depth=1,
            durability=DurabilityPolicy.VOLATILE,
            reliability=ReliabilityPolicy.RELIABLE,
            history=HistoryPolicy.KEEP_LAST,
        )
        self.create_subscription(Path, PLAN_TOPIC, self._on_plan, plan_qos)
        self._last_scan_count = -1
        self._map_seq = 0
        self._nav_status_url = os.environ.get(
            "SIM_NAV_STATUS_URL", "http://127.0.0.1:8769/status"
        )
        dt = 1.0 / max(1.0, STEP_HZ)
        self.create_timer(dt, self._on_step)
        self.create_timer(1.0 / max(0.1, MAP_HZ), self._on_map)
        self.create_timer(0.5, self._on_nav_status)
        self.get_logger().info(
            f"sim plant: map={MAP_TOPIC} scan={SCAN_TOPIC} plan={PLAN_TOPIC} "
            f"drive=:{DRIVE_PORT} gui=:{GUI_PORT} scenario={sim.scenario.id}"
        )
        # Publish once immediately so Nav2 costmaps can start.
        self._on_map()
        self._publish_tf()

    def _on_nav_status(self) -> None:
        """Mirror Nav2 goal status into sim so the GUI unsticks after abort."""
        try:
            import urllib.request

            with urllib.request.urlopen(self._nav_status_url, timeout=0.4) as resp:
                status = json.loads(resp.read().decode("utf-8"))
        except Exception:  # noqa: BLE001
            return
        if not isinstance(status, dict):
            return
        result = status.get("result")
        phase = status.get("status")
        with self._lock:
            if phase == "navigating":
                self._sim.nav_complete = False
                self._sim.goal_reachable = True
                if self._sim.status.startswith("nav2"):
                    self._sim.status = "nav2 navigating"
            elif result in ("aborted", "canceled", "failed"):
                self._sim.goal_reachable = False
                self._sim.nav_complete = False
                self._sim.path = []
                self._sim.status = f"nav2 {result}"
            elif result == "succeeded":
                self._sim.goal_reachable = True
                self._sim.nav_complete = True
                self._sim.status = "nav2 arrived"

    def _on_plan(self, msg: Path) -> None:
        """Mirror Nav2 global plan into sim.path so the GUI can draw it."""
        points = [
            {"x": float(p.pose.position.x), "y": float(p.pose.position.y)}
            for p in msg.poses
        ]
        with self._lock:
            self._sim.path = points
            self._sim.path_cursor = 0
            if points:
                self._sim.goal_reachable = True
                self._sim.nav_complete = False
            self._sim.last_plan_length_m = 0.0
            if len(points) >= 2:
                length = 0.0
                for i in range(1, len(points)):
                    dx = points[i]["x"] - points[i - 1]["x"]
                    dy = points[i]["y"] - points[i - 1]["y"]
                    length += math.hypot(dx, dy)
                self._sim.last_plan_length_m = length

    def _on_step(self) -> None:
        with self._lock:
            self._sim.step(1.0 / max(1.0, STEP_HZ))
            scan_changed = self._sim.scan_count != self._last_scan_count
            if scan_changed:
                self._last_scan_count = self._sim.scan_count
        # Publish outside the sim lock so GUI snapshots aren't blocked by DDS.
        self._publish_tf()
        if scan_changed:
            self._publish_scan()

    def _on_map(self) -> None:
        with self._lock:
            # Snapshot grid under lock, publish after release.
            grid = list(self._sim.slam_grid)
            width = int(self._sim.width_cells)
            height = int(self._sim.height_cells)
        self._publish_map_data(grid, width, height)

    def _publish_tf(self) -> None:
        with self._lock:
            pose = dict(self._sim.estimated_pose)
        now = self.get_clock().now().to_msg()
        # Fake Cartographer: identity map→odom; pose lives in odom→base_link.
        map_odom = TransformStamped()
        map_odom.header.stamp = now
        map_odom.header.frame_id = MAP_FRAME
        map_odom.child_frame_id = ODOM_FRAME
        map_odom.transform.rotation.w = 1.0
        self._tf.sendTransform(map_odom)

        qx, qy, qz, qw = _yaw_to_quat(pose["yaw"])
        odom_base = TransformStamped()
        odom_base.header.stamp = now
        odom_base.header.frame_id = ODOM_FRAME
        odom_base.child_frame_id = BASE_FRAME
        odom_base.transform.translation.x = float(pose["x"])
        odom_base.transform.translation.y = float(pose["y"])
        odom_base.transform.rotation.x = qx
        odom_base.transform.rotation.y = qy
        odom_base.transform.rotation.z = qz
        odom_base.transform.rotation.w = qw
        self._tf.sendTransform(odom_base)

    def _publish_map_data(self, grid: list[int], width: int, height: int) -> None:
        msg = OccupancyGrid()
        msg.header.stamp = self.get_clock().now().to_msg()
        msg.header.frame_id = MAP_FRAME
        msg.info.map_load_time = msg.header.stamp
        msg.info.resolution = float(GRID_RESOLUTION_M)
        msg.info.width = width
        msg.info.height = height
        msg.info.origin.orientation.w = 1.0
        data: list[int] = []
        for value in grid:
            if value == UNKNOWN:
                data.append(-1)
            elif value >= OCCUPIED or value >= 65:
                data.append(100)
            elif value == FREE:
                data.append(0)
            else:
                data.append(0 if value < 65 else 100)
        msg.data = data
        self._map_seq += 1
        self._map_pub.publish(msg)

    def _publish_map(self) -> None:
        with self._lock:
            grid = list(self._sim.slam_grid)
            width = int(self._sim.width_cells)
            height = int(self._sim.height_cells)
        self._publish_map_data(grid, width, height)

    def _publish_scan(self) -> None:
        with self._lock:
            rays = list(self._sim.lidar or [])
        if not rays:
            return
        msg = LaserScan()
        msg.header.stamp = self.get_clock().now().to_msg()
        msg.header.frame_id = BASE_FRAME
        msg.angle_min = 0.0
        msg.angle_increment = math.radians(LIDAR_ANGULAR_RES_DEG)
        msg.angle_max = msg.angle_min + msg.angle_increment * (LIDAR_RAY_COUNT - 1)
        msg.time_increment = 0.0
        msg.scan_time = 0.1
        msg.range_min = float(LIDAR_MIN_RANGE_M)
        msg.range_max = float(LIDAR_RANGE_M)
        ranges: list[float] = []
        for ray in rays:
            if ray.get("blind") or ray.get("invalid") or not ray.get("hit"):
                ranges.append(float("inf"))
            else:
                ranges.append(float(ray["distance"]))
        if len(ranges) < LIDAR_RAY_COUNT:
            ranges.extend([float("inf")] * (LIDAR_RAY_COUNT - len(ranges)))
        msg.ranges = ranges[:LIDAR_RAY_COUNT]
        self._scan_pub.publish(msg)

def _make_drive_handler(sim: SlamNavSimulation, lock: threading.Lock):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, format: str, *args: Any) -> None:  # noqa: A003
            return

        def _send(self, code: int, payload: dict[str, Any]) -> None:
            body = json.dumps(payload).encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_POST(self) -> None:  # noqa: N802
            path = urllib.parse.urlparse(self.path).path
            length = int(self.headers.get("Content-Length") or 0)
            raw: dict[str, Any] = {}
            if length:
                try:
                    raw = json.loads(self.rfile.read(length).decode("utf-8"))
                except (json.JSONDecodeError, UnicodeDecodeError):
                    self._send(400, {"error": "invalid json"})
                    return
            if path not in (
                "/api/navigation/drive",
                "/api/navigation/drive/",
                "/drive",
            ):
                self._send(404, {"error": "not found"})
                return
            drive = raw.get("drive") if isinstance(raw.get("drive"), dict) else raw
            x = float(drive.get("x") or 0.0)
            y = float(drive.get("y") or 0.0)
            with lock:
                sim.set_pi_stick(x, y, enabled=True)
            self._send(200, {"ok": True, "drive": {"x": x, "y": y}})

        def do_GET(self) -> None:  # noqa: N802
            path = urllib.parse.urlparse(self.path).path
            if path in ("/health", "/"):
                with lock:
                    pose = dict(sim.pose)
                    status = sim.status
                self._send(200, {"ok": True, "status": status, "pose": pose})
                return
            self._send(404, {"error": "not found"})

    return Handler


class CosimGuiServer(SimulatorServer):
    """GUI that observes the plant; goals go to real Nav2, not sim autopilot."""

    def __init__(
        self,
        sim: SlamNavSimulation,
        lock: threading.Lock,
        host: str,
        port: int,
        goal_url: str,
        cancel_url: str,
    ) -> None:
        self.host = host
        self.port = port
        self.sim = sim
        self.lock = lock
        self._goal_url = goal_url
        self._cancel_url = cancel_url
        self._last_client_tick = 0.0
        self._stop = threading.Event()
        self._autostep_thread = None

    def _with_cosim(self, snap: dict) -> dict:
        snap["cosim"] = True
        return snap

    def _handle_command(self, payload: dict) -> dict:
        cmd = payload.get("cmd")
        if cmd == "goal":
            body = {
                "x": float(payload["x"]),
                "y": float(payload["y"]),
                "yaw": float(payload["yaw"] or 0.0)
                if payload.get("yaw") is not None
                else 0.0,
                "label": "sim-gui",
                "fine_docking": bool(payload.get("fine_docking")),
            }
            with self.lock:
                # Show goal marker immediately; Nav2 goto is async so the UI
                # never blocks on planning / cancel races.
                self.sim.goal = {"x": body["x"], "y": body["y"]}
                self.sim.goal_yaw = body["yaw"]
                self.sim.fine_docking = bool(body.get("fine_docking"))
                self.sim.nav_complete = False
                self.sim.goal_reachable = True
                self.sim.autopilot = False
                self.sim.path = []
                self.sim.status = "nav2 goal sent"
                snap = self._with_cosim(self.sim.snapshot())
            snap["nav2_goto"] = {"success": True, "status": "pending"}

            def _send() -> None:
                result = _post_json(self._goal_url, body)
                with self.lock:
                    if not result.get("success"):
                        self.sim.goal_reachable = False
                        self.sim.status = (
                            f"nav2 goal failed: {result.get('error', result)}"
                        )
                    else:
                        self.sim.status = "nav2 navigating"

            threading.Thread(target=_send, name="cosim-goto", daemon=True).start()
            return snap
        if cmd == "stop":
            threading.Thread(
                target=lambda: _post_json(self._cancel_url, {}),
                name="cosim-cancel",
                daemon=True,
            ).start()
            with self.lock:
                self.sim.set_pi_stick(0.0, 0.0, enabled=True)
                self.sim.path = []
                self.sim.autopilot = False
                self.sim.status = "nav2 cancel requested"
                return self._with_cosim(self.sim.snapshot())
        if cmd in ("default_goal",):
            with self.lock:
                # Marker only — do not arm sim autopilot.
                if not self.sim.goal and hasattr(self.sim.scenario, "default_goal"):
                    g = getattr(self.sim.scenario, "default_goal", None)
                    if g:
                        self.sim.goal = {"x": float(g["x"]), "y": float(g["y"])}
                        self.sim.goal_yaw = float(g.get("yaw") or 0.0)
                elif self.sim.goal is None:
                    # Fall back to engine helper then immediately disarm autopilot.
                    self.sim.set_default_goal()
                self.sim.autopilot = False
                self.sim.pi_drive_enabled = True
                self.sim.path = []
                self.sim.nav_complete = False
                goal = self.sim.goal or {}
                yaw = self.sim.goal_yaw or 0.0
            result = _post_json(
                self._goal_url,
                {
                    "x": float(goal.get("x") or 0.0),
                    "y": float(goal.get("y") or 0.0),
                    "yaw": float(yaw),
                    "label": "sim-default",
                },
            )
            with self.lock:
                self.sim.goal_reachable = True if result.get("success") else False
                self.sim.autopilot = False
                snap = self._with_cosim(self.sim.snapshot())
            snap["nav2_goto"] = result
            return snap
        # Map build / reset / kidnap stay local plant ops.
        if cmd == "build_map":
            with self.lock:
                self.sim.stop_auto_map(freeze=False)
                self.sim.reveal_map()
                self.sim.freeze_map()
                self.sim.set_pi_stick(0.0, 0.0, enabled=True)
                self.sim.status = "map built · frozen · cosim"
                return self._with_cosim(self.sim.snapshot())
        if cmd == "reset":
            with self.lock:
                self.sim.reset(payload.get("scenario") or self.sim.scenario.id)
                if REVEAL_ON_START:
                    self.sim.reveal_map()
                    self.sim.freeze_map()
                self.sim.set_pi_stick(0.0, 0.0, enabled=True)
                return self._with_cosim(self.sim.snapshot())
        if cmd in ("auto_map", "step"):
            with self.lock:
                self.sim.status = "cosim: use Nav2 for motion"
                return self._with_cosim(self.sim.snapshot())
        if cmd == "kidnap":
            with self.lock:
                yaw = payload.get("yaw")
                self.sim.kidnap_rover(
                    float(payload["x"]),
                    float(payload["y"]),
                    None if yaw is None else float(yaw),
                    keep_estimate=False,
                )
                return self._with_cosim(self.sim.snapshot())
        if cmd == "move_prop":
            with self.lock:
                self.sim.move_prop(
                    str(payload.get("id") or ""),
                    float(payload.get("x") or 0.0),
                    float(payload.get("y") or 0.0),
                )
                return self._with_cosim(self.sim.snapshot())
        if cmd == "speed":
            with self.lock:
                self.sim.speed_multiplier = float(payload.get("value") or 1.0)
                return self._with_cosim(self.sim.snapshot())
        with self.lock:
            self.sim.status = f"cosim: ignored cmd {cmd}"
            return self._with_cosim(self.sim.snapshot())
    def maybe_autostep(self, now: float | None = None, dt: float = 1.0 / 30.0) -> bool:
        # Plant timer owns the clock in co-sim.
        return False

    def make_handler(self):
        server = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, format: str, *args: Any) -> None:  # noqa: A003
                return

            def _send(self, code: int, body: bytes, content_type: str) -> None:
                self.send_response(code)
                self.send_header("Content-Type", content_type)
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                self.wfile.write(body)

            def _read_json(self) -> dict:
                length = int(self.headers.get("Content-Length", "0"))
                if length <= 0:
                    return {}
                raw = self.rfile.read(length)
                return json.loads(raw.decode("utf-8"))

            def do_GET(self) -> None:  # noqa: N802
                from .gui import HTML_PAGE

                path = urllib.parse.urlparse(self.path).path
                if path in {"/", "/index.html"}:
                    self._send(200, HTML_PAGE.encode("utf-8"), "text/html; charset=utf-8")
                    return
                if path == "/api/state":
                    with server.lock:
                        payload = server.sim.snapshot()
                    payload["cosim"] = True
                    self._send(
                        200,
                        json.dumps(payload).encode("utf-8"),
                        "application/json",
                    )
                    return
                self._send(404, b"not found", "text/plain")

            def do_POST(self) -> None:  # noqa: N802
                path = urllib.parse.urlparse(self.path).path
                payload = self._read_json()
                if path == "/api/tick":
                    with server.lock:
                        server._last_client_tick = time.monotonic()
                        body = server.sim.snapshot()
                    body["cosim"] = True
                elif path == "/api/command":
                    body = server._handle_command(payload)
                    body["cosim"] = True
                else:
                    self._send(404, b"not found", "text/plain")
                    return
                self._send(
                    200,
                    json.dumps(body).encode("utf-8"),
                    "application/json",
                )

        return Handler


def main() -> int:
    rclpy.init()
    lock = threading.Lock()
    sim = SlamNavSimulation(scenario_id=SCENARIO, noise_enabled=False)
    if REVEAL_ON_START:
        sim.reveal_map()
        sim.freeze_map()
    sim.set_pi_stick(0.0, 0.0, enabled=True)
    sim.status = "cosim ready"

    drive_httpd = ThreadingHTTPServer(
        (DRIVE_HOST, DRIVE_PORT), _make_drive_handler(sim, lock)
    )
    drive_thread = threading.Thread(
        target=drive_httpd.serve_forever, name="sim-drive-http", daemon=True
    )
    drive_thread.start()
    print(f"sim drive API: http://{DRIVE_HOST}:{DRIVE_PORT}/api/navigation/drive")

    gui_server = None
    if ENABLE_GUI:
        gui_server = CosimGuiServer(
            sim, lock, GUI_HOST, GUI_PORT, GOAL_URL, CANCEL_URL
        )
        gui_httpd = ThreadingHTTPServer((GUI_HOST, GUI_PORT), gui_server.make_handler())
        threading.Thread(
            target=gui_httpd.serve_forever, name="sim-gui-http", daemon=True
        ).start()
        print(f"sim GUI (observe + Nav2 goals): http://{GUI_HOST}:{GUI_PORT}/")

    node = SimPlantNode(sim, lock)
    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        print("\nsim plant stopped.")
    finally:
        node.destroy_node()
        drive_httpd.shutdown()
        rclpy.shutdown()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
