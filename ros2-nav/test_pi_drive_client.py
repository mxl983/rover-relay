#!/usr/bin/env python3
"""Tests for the Nav2 Pi drive transport."""

from __future__ import annotations

import json
import sys
import types
import unittest
from unittest.mock import patch

from pi_drive_client import PiDriveClient


class FakeWebSocket:
    def __init__(self) -> None:
        self.messages: list[str] = []
        self.closed = False

    def send(self, message: str) -> None:
        self.messages.append(message)

    def close(self) -> None:
        self.closed = True


class PiDriveClientTests(unittest.TestCase):
    def test_ws_sends_dashboard_compatible_drive_message(self) -> None:
        socket = FakeWebSocket()
        websocket_module = types.SimpleNamespace(
            create_connection=lambda *args, **kwargs: socket
        )
        with patch.dict(sys.modules, {"websocket": websocket_module}):
            client = PiDriveClient(
                transport="ws",
                ws_url="wss://rover.test:3000",
                http_url="http://unused",
            )
            client.send({"x": -0.25, "y": -0.6})

        self.assertEqual(
            json.loads(socket.messages[0]),
            {
                "type": "DRIVE",
                "drive": {"x": -0.25, "y": -0.6},
                "gimbal": {"x": 0.0, "y": 0.0},
            },
        )

    def test_ws_reuses_connection_for_persistent_commands(self) -> None:
        sockets: list[FakeWebSocket] = []

        def connect(*args, **kwargs) -> FakeWebSocket:
            socket = FakeWebSocket()
            sockets.append(socket)
            return socket

        websocket_module = types.SimpleNamespace(create_connection=connect)
        with patch.dict(sys.modules, {"websocket": websocket_module}):
            client = PiDriveClient(
                transport="ws",
                ws_url="wss://rover.test:3000",
                http_url="http://unused",
            )
            client.send({"x": 0.0, "y": -0.3})
            client.send({"x": 0.0, "y": 0.0})

        self.assertEqual(len(sockets), 1)
        self.assertEqual(len(sockets[0].messages), 2)
        self.assertEqual(json.loads(sockets[0].messages[1])["drive"]["y"], 0.0)


if __name__ == "__main__":
    unittest.main()
