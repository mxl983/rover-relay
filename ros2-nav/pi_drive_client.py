"""Pi drive transport shared by the Nav2 bridges.

Live rover navigation uses the same persistent WebSocket protocol as the
dashboard. HTTP remains available explicitly for co-sim and legacy setups.
"""

from __future__ import annotations

import json
import ssl
import threading
import urllib.request
from typing import Any


class PiDriveClient:
    """Send persistent analog drive objects to the Pi."""

    def __init__(
        self,
        *,
        transport: str,
        ws_url: str,
        http_url: str,
        token: str = "",
        ssl_verify: bool = False,
        timeout: float = 1.0,
    ) -> None:
        self.transport = transport.lower().strip()
        if self.transport not in {"ws", "http"}:
            raise ValueError(f"unsupported drive transport: {transport!r}")
        self.ws_url = ws_url
        self.http_url = http_url
        self.token = token
        self.timeout = timeout
        self._ssl = None if ssl_verify else ssl._create_unverified_context()
        self._ws: Any = None
        self._lock = threading.Lock()

    def send(
        self,
        drive: dict[str, float],
        *,
        gimbal: dict[str, float] | None = None,
    ) -> None:
        payload = {
            "drive": {
                "x": float(drive.get("x", 0.0)),
                "y": float(drive.get("y", 0.0)),
            },
            "gimbal": gimbal
            or {
                "x": 0.0,
                "y": 0.0,
            },
        }
        if self.transport == "http":
            self._send_http(payload)
        else:
            self._send_ws(payload)

    def close(self) -> None:
        with self._lock:
            self._close_ws_locked()

    def _send_http(self, payload: dict[str, Any]) -> None:
        data = json.dumps(payload).encode("utf-8")
        headers = {"Content-Type": "application/json"}
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        request = urllib.request.Request(
            self.http_url,
            data=data,
            method="POST",
            headers=headers,
        )
        with urllib.request.urlopen(
            request,
            timeout=self.timeout,
            context=self._ssl,
        ):
            return

    def _send_ws(self, payload: dict[str, Any]) -> None:
        # Import lazily so unit tests and non-live tooling do not need the
        # optional client until the live WS transport is actually selected.
        try:
            import websocket
        except ImportError as exc:
            raise RuntimeError(
                "WebSocket transport requires the websocket-client package"
            ) from exc

        with self._lock:
            for attempt in range(2):
                try:
                    if self._ws is None:
                        sslopt = (
                            {"cert_reqs": ssl.CERT_NONE}
                            if self._ssl is not None
                            else {}
                        )
                        connection_kwargs = {
                            "timeout": self.timeout,
                            "sslopt": sslopt,
                        }
                        if self.token:
                            connection_kwargs["header"] = [
                                f"Authorization: Bearer {self.token}"
                            ]
                        self._ws = websocket.create_connection(
                            self.ws_url,
                            **connection_kwargs,
                        )
                    self._ws.send(json.dumps({"type": "DRIVE", **payload}))
                    return
                except Exception:
                    self._close_ws_locked()
                    if attempt == 1:
                        raise

    def _close_ws_locked(self) -> None:
        if self._ws is not None:
            try:
                self._ws.close()
            except Exception:
                pass
            self._ws = None
