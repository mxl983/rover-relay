#!/usr/bin/env python3
"""HTTP goal server: send / cancel Nav2 NavigateToPose from saved map waypoints."""

from __future__ import annotations

import json
import math
import os
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.parse import urlparse

import rclpy
from action_msgs.msg import GoalStatus
from geometry_msgs.msg import PoseStamped
from nav2_msgs.action import NavigateToPose
from rclpy.action import ActionClient
from rclpy.node import Node

HOST = os.environ.get("NAV_GOAL_HOST", "0.0.0.0")
PORT = int(os.environ.get("NAV_GOAL_PORT", "8768"))
MAP_FRAME = os.environ.get("SLAM_MAP_FRAME", "map")
STATUS_PATH = os.environ.get(
    "NAV_GOAL_STATUS_PATH",
    os.environ.get("NAV_GOAL_STATUS_FILE", "/app/lidar/navigation_goal.json"),
)
COMMAND_PATH = os.environ.get(
    "NAV_COMMAND_PATH", "/app/lidar/navigation_command.json"
)

_state: dict[str, Any] = {
    "status": "idle",
    "goal": None,
    "result": None,
    "feedback": None,
    "updated_at": 0.0,
    "cmd_seq": 0,
    "cmd_error": None,
}
_lock = threading.Lock()
_node: "GoalNode | None" = None
_last_cmd_seq = 0


def yaw_to_quat(yaw: float) -> tuple[float, float, float, float]:
    half = yaw * 0.5
    return (0.0, 0.0, math.sin(half), math.cos(half))


def write_goal_status() -> None:
    with _lock:
        payload = dict(_state)
    try:
        directory = os.path.dirname(STATUS_PATH)
        if directory:
            os.makedirs(directory, exist_ok=True)
        tmp = f"{STATUS_PATH}.tmp"
        with open(tmp, "w", encoding="utf-8") as handle:
            json.dump(payload, handle)
        os.replace(tmp, STATUS_PATH)
    except OSError:
        pass


class GoalNode(Node):
    def __init__(self) -> None:
        super().__init__("rover_nav_goal_server")
        self._client = ActionClient(self, NavigateToPose, "navigate_to_pose")
        self._goal_handle = None
        self._send_lock = threading.Lock()
        self.get_logger().info("Nav2 NavigateToPose action client ready (waiting for server)")

    def wait_server(self, timeout_sec: float = 120.0) -> bool:
        return self._client.wait_for_server(timeout_sec=timeout_sec)

    def goto(
        self,
        x: float,
        y: float,
        yaw: float,
        label: str = "",
        *,
        fine_docking: bool = False,
    ) -> dict[str, Any]:
        with self._send_lock:
            if not self._client.server_is_ready():
                if not self.wait_server(5.0):
                    return {"success": False, "error": "Nav2 navigate_to_pose not ready"}

            # Cancel any in-flight goal first.
            self.cancel()

            pose = PoseStamped()
            pose.header.frame_id = MAP_FRAME
            pose.header.stamp = self.get_clock().now().to_msg()
            pose.pose.position.x = float(x)
            pose.pose.position.y = float(y)
            pose.pose.position.z = 0.0
            qx, qy, qz, qw = yaw_to_quat(float(yaw))
            pose.pose.orientation.x = qx
            pose.pose.orientation.y = qy
            pose.pose.orientation.z = qz
            pose.pose.orientation.w = qw

            goal = NavigateToPose.Goal()
            goal.pose = pose

            goal_meta = {
                "x": round(float(x), 3),
                "y": round(float(y), 3),
                "yaw": round(float(yaw), 4),
                "label": label or "",
                "fine_docking": bool(fine_docking),
            }
            with _lock:
                _state.update(
                    {
                        "status": "navigating",
                        "goal": goal_meta,
                        "result": None,
                        "feedback": None,
                        "updated_at": time.time(),
                    }
                )
            write_goal_status()

            send_future = self._client.send_goal_async(
                goal, feedback_callback=self._on_feedback
            )
            send_future.add_done_callback(self._on_goal_response)
            return {"success": True, "goal": goal_meta, "status": "navigating"}

    def cancel(self) -> dict[str, Any]:
        handle = self._goal_handle
        if handle is not None:
            try:
                handle.cancel_goal_async()
            except Exception:  # noqa: BLE001
                pass
        self._goal_handle = None
        with _lock:
            _state.update(
                {
                    "status": "idle",
                    "result": "canceled",
                    "updated_at": time.time(),
                }
            )
        write_goal_status()
        return {"success": True, "status": "idle"}

    def _on_goal_response(self, future: Any) -> None:
        try:
            handle = future.result()
        except Exception as exc:  # noqa: BLE001
            with _lock:
                _state.update(
                    {"status": "failed", "result": str(exc), "updated_at": time.time()}
                )
            write_goal_status()
            return
        if not handle.accepted:
            with _lock:
                _state.update(
                    {
                        "status": "rejected",
                        "result": "goal rejected",
                        "updated_at": time.time(),
                    }
                )
            write_goal_status()
            return
        self._goal_handle = handle
        result_future = handle.get_result_async()
        result_future.add_done_callback(self._on_result)

    def _on_feedback(self, feedback_msg: Any) -> None:
        fb = feedback_msg.feedback
        # NavigateToPose feedback includes current pose / distance remaining depending on version.
        info: dict[str, Any] = {}
        try:
            dist = getattr(fb, "distance_remaining", None)
            if dist is not None:
                info["distance_remaining"] = round(float(dist), 3)
        except Exception:  # noqa: BLE001
            pass
        with _lock:
            _state["feedback"] = info or None
            _state["updated_at"] = time.time()
        write_goal_status()

    def _on_result(self, future: Any) -> None:
        try:
            wrapped = future.result()
            status = int(wrapped.status)
        except Exception as exc:  # noqa: BLE001
            with _lock:
                _state.update(
                    {"status": "failed", "result": str(exc), "updated_at": time.time()}
                )
            write_goal_status()
            self._goal_handle = None
            return

        status_name = {
            GoalStatus.STATUS_SUCCEEDED: "succeeded",
            GoalStatus.STATUS_CANCELED: "canceled",
            GoalStatus.STATUS_ABORTED: "aborted",
        }.get(status, f"status_{status}")
        with _lock:
            _state.update(
                {
                    "status": "idle" if status_name in ("succeeded", "canceled") else status_name,
                    "result": status_name,
                    "updated_at": time.time(),
                }
            )
        write_goal_status()
        self._goal_handle = None
        self.get_logger().info(f"NavigateToPose finished: {status_name}")


class Handler(BaseHTTPRequestHandler):
    def log_message(self, format: str, *args: Any) -> None:  # noqa: A003
        return

    def _json(self, code: int, obj: Any) -> None:
        body = json.dumps(obj).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _read(self) -> dict[str, Any]:
        length = int(self.headers.get("Content-Length", "0") or 0)
        if length <= 0:
            return {}
        try:
            data = json.loads(self.rfile.read(length).decode("utf-8"))
            return data if isinstance(data, dict) else {}
        except json.JSONDecodeError:
            return {}

    def do_GET(self) -> None:  # noqa: N802
        path = urlparse(self.path).path
        if path in ("/status", "/goal/status"):
            with _lock:
                payload = dict(_state)
            self._json(200, {"success": True, **payload})
            return
        if path in ("/", "/health"):
            self._json(200, {"success": True, "service": "nav-goal"})
            return
        self.send_error(404)

    def do_POST(self) -> None:  # noqa: N802
        global _node
        path = urlparse(self.path).path
        body = self._read()
        if path in ("/goto", "/goal"):
            if _node is None:
                self._json(503, {"success": False, "error": "goal node not ready"})
                return
            try:
                x = float(body["x"])
                y = float(body["y"])
                yaw = float(body.get("yaw", 0.0))
            except (KeyError, TypeError, ValueError):
                self._json(400, {"success": False, "error": "need x,y[,yaw]"})
                return
            label = str(body.get("label") or body.get("id") or "")
            fine_docking = bool(body.get("fine_docking"))
            result = _node.goto(x, y, yaw, label=label, fine_docking=fine_docking)
            code = 200 if result.get("success") else 503
            self._json(code, result)
            return
        if path in ("/cancel", "/goal/cancel"):
            if _node is None:
                self._json(503, {"success": False, "error": "goal node not ready"})
                return
            self._json(200, _node.cancel())
            return
        self.send_error(404)


def main() -> None:
    global _node, _last_cmd_seq
    rclpy.init()
    _node = GoalNode()
    write_goal_status()

    # Spin ROS in background; serve HTTP in main thread.
    spin_thread = threading.Thread(target=rclpy.spin, args=(_node,), daemon=True)
    spin_thread.start()

    if not _node.wait_server(180.0):
        _node.get_logger().warn(
            "navigate_to_pose not available yet — goals will retry on demand"
        )
    else:
        _node.get_logger().info("navigate_to_pose action server connected")

    def poll_command_file() -> None:
        """Relay writes commands to a shared volume (Docker bridge can't reach host HTTP)."""
        global _last_cmd_seq
        while True:
            time.sleep(0.15)
            if _node is None:
                continue
            try:
                if not os.path.isfile(COMMAND_PATH):
                    continue
                with open(COMMAND_PATH, encoding="utf-8") as handle:
                    raw = json.load(handle)
            except (OSError, json.JSONDecodeError, TypeError):
                continue
            if not isinstance(raw, dict):
                continue
            seq = int(raw.get("seq") or 0)
            if seq <= 0 or seq == _last_cmd_seq:
                continue
            _last_cmd_seq = seq
            op = str(raw.get("op") or "").strip().lower()
            err: str | None = None
            try:
                if op == "goto":
                    result = _node.goto(
                        float(raw["x"]),
                        float(raw["y"]),
                        float(raw.get("yaw") or 0.0),
                        label=str(raw.get("label") or raw.get("id") or ""),
                        fine_docking=bool(raw.get("fine_docking")),
                    )
                    if not result.get("success"):
                        err = str(result.get("error") or "goto failed")
                elif op == "cancel":
                    _node.cancel()
                else:
                    err = f"unknown op: {op}"
            except Exception as exc:  # noqa: BLE001
                err = str(exc)
            with _lock:
                _state["cmd_seq"] = seq
                _state["cmd_error"] = err
                _state["updated_at"] = time.time()
            write_goal_status()
            _node.get_logger().info(
                f"command seq={seq} op={op} err={err or 'ok'}"
            )

    threading.Thread(target=poll_command_file, daemon=True).start()
    _node.get_logger().info(f"Polling nav commands from {COMMAND_PATH}")

    server = ThreadingHTTPServer((HOST, PORT), Handler)
    _node.get_logger().info(f"Nav goal HTTP on http://{HOST}:{PORT}/")
    try:
        server.serve_forever()
    finally:
        server.shutdown()
        _node.destroy_node()
        rclpy.shutdown()


if __name__ == "__main__":
    main()
