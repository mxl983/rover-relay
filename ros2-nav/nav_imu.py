#!/usr/bin/env python3
"""Soft Pi IMU assist for navigation (advisory only — never replaces SLAM pose).

Drive experiment: gyro.z ≈ yaw rate, gyro.y ≈ forward/back + stop decel.
Rest gyro often has a large constant bias — we subtract a tracked bias.

Fail-open: if fetch fails or readings are unusable, helpers return None /
inactive and callers behave exactly like lidar-only.

This module is intentionally independent of Cartographer ``use_imu_data``.
"""

from __future__ import annotations

import json
import math
import os
import ssl
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any

IMU_URL = os.environ.get(
    "NAV_IMU_URL",
    os.environ.get(
        "SLAM_IMU_URL",
        "https://rover.tail9d0237.ts.net:3000/api/sensors/imu",
    ),
)
SSL_INSECURE = os.environ.get("NAV_IMU_SSL_INSECURE", "1") not in (
    "0",
    "false",
    "False",
)
TOKEN = (
    os.environ.get("NAV_IMU_TOKEN")
    or os.environ.get("NAVIGATION_API_TOKEN")
    or os.environ.get("ROVER_API_TOKEN")
    or ""
)
# Soft assist on by default; set NAV_USE_IMU=0 to disable without touching SLAM.
NAV_USE_IMU = os.environ.get("NAV_USE_IMU", "true").lower() not in (
    "0",
    "false",
    "no",
    "off",
)
BIAS_ALPHA = float(os.environ.get("NAV_IMU_BIAS_ALPHA", "0.03"))
# Below this |gz| after bias remove → treat as still for bias tracking.
STILL_GZ = float(os.environ.get("NAV_IMU_STILL_GZ", "0.08"))
# Min |gz| to count as rotating during a commanded turn pulse.
TURN_ACTIVE_GZ = float(os.environ.get("NAV_IMU_TURN_GZ", "0.15"))
# Min |gy| spike to count as linear accel/decel hint.
LIN_ACTIVE_GY = float(os.environ.get("NAV_IMU_LIN_GY", "0.02"))
FETCH_TIMEOUT_S = float(os.environ.get("NAV_IMU_TIMEOUT_S", "0.35"))
STALE_S = float(os.environ.get("NAV_IMU_STALE_S", "0.75"))


@dataclass(frozen=True)
class ImuHint:
    """One sample of soft assist — all rates are bias-corrected rad/s."""

    ok: bool
    gy: float  # pitch / fwd-back related
    gz: float  # yaw rate (+ = CCW, chip Z)
    age_s: float
    note: str = ""


def parse_pi_imu(raw: Any) -> tuple[float, float, float] | None:
    """Return (gx, gy, gz) rad/s from Pi HTTP payload, or None."""
    if not isinstance(raw, dict):
        return None
    status = raw.get("status") if isinstance(raw.get("status"), dict) else {}
    if raw.get("connected") is False or status.get("connected") is False:
        return None
    body = raw.get("sample") if isinstance(raw.get("sample"), dict) else None
    if body is None and isinstance(raw.get("data"), dict):
        body = raw["data"]
    if body is None:
        body = raw
    if not isinstance(body, dict):
        return None
    gyro = body.get("gyro") or {}
    try:
        return float(gyro["x"]), float(gyro["y"]), float(gyro["z"])
    except (KeyError, TypeError, ValueError):
        return None


def fetch_pi_gyro(
    url: str = IMU_URL,
    *,
    token: str = TOKEN,
    insecure: bool = SSL_INSECURE,
    timeout: float = FETCH_TIMEOUT_S,
) -> tuple[float, float, float] | None:
    headers = {"Accept": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    req = urllib.request.Request(url, headers=headers, method="GET")
    context = None
    if url.startswith("https://") and insecure:
        context = ssl._create_unverified_context()  # noqa: S323
    try:
        with urllib.request.urlopen(req, timeout=timeout, context=context) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, OSError):
        return None
    return parse_pi_imu(payload)


class NavImuAssist:
    """Poll + bias-track Pi gyro for soft nav hints. Always fail-open."""

    def __init__(self) -> None:
        self._bias = [0.0, 0.0, 0.0]
        self._bias_init = False
        self._last_ok_at = 0.0
        self._last_hint = ImuHint(ok=False, gy=0.0, gz=0.0, age_s=1e9, note="init")
        self._yaw_integ = 0.0
        self._yaw_integ_t0 = 0.0
        self.enabled = NAV_USE_IMU

    def reset_yaw_integration(self) -> None:
        self._yaw_integ = 0.0
        self._yaw_integ_t0 = time.monotonic()

    @property
    def integrated_yaw_rad(self) -> float:
        return self._yaw_integ

    def poll(self) -> ImuHint:
        if not self.enabled:
            hint = ImuHint(ok=False, gy=0.0, gz=0.0, age_s=1e9, note="disabled")
            self._last_hint = hint
            return hint
        now = time.monotonic()
        raw = fetch_pi_gyro()
        if raw is None:
            age = now - self._last_ok_at if self._last_ok_at else 1e9
            hint = ImuHint(
                ok=False,
                gy=self._last_hint.gy,
                gz=self._last_hint.gz,
                age_s=age,
                note="fetch_failed",
            )
            self._last_hint = hint
            return hint

        gx, gy, gz = raw
        if not self._bias_init:
            self._bias = [gx, gy, gz]
            self._bias_init = True
        else:
            # Track bias only when quiet (no strong yaw rate vs current bias).
            if abs(gz - self._bias[2]) < STILL_GZ and abs(gy - self._bias[1]) < STILL_GZ:
                a = BIAS_ALPHA
                self._bias[0] += a * (gx - self._bias[0])
                self._bias[1] += a * (gy - self._bias[1])
                self._bias[2] += a * (gz - self._bias[2])

        dgy = gy - self._bias[1]
        dgz = gz - self._bias[2]

        # Integrate yaw only while we have a fresh sample cadence.
        if self._yaw_integ_t0 > 0.0:
            dt = now - self._yaw_integ_t0
            if 0.0 < dt < 0.5:
                self._yaw_integ += dgz * dt
        self._yaw_integ_t0 = now
        self._last_ok_at = now

        hint = ImuHint(ok=True, gy=dgy, gz=dgz, age_s=0.0, note="ok")
        self._last_hint = hint
        return hint

    def last(self) -> ImuHint:
        if not self._last_hint.ok:
            return self._last_hint
        age = time.monotonic() - self._last_ok_at
        if age > STALE_S:
            return ImuHint(
                ok=False,
                gy=self._last_hint.gy,
                gz=self._last_hint.gz,
                age_s=age,
                note="stale",
            )
        return ImuHint(
            ok=True,
            gy=self._last_hint.gy,
            gz=self._last_hint.gz,
            age_s=age,
            note=self._last_hint.note,
        )


def yaw_pulse_early_stop(
    *,
    approach_sign: int,
    err_at_pulse_start: float,
    integrated_yaw_rad: float,
    live_gz: float,
    yaw_tol_rad: float,
    fraction: float = 0.72,
) -> bool:
    """True if gyro suggests this yaw pulse has closed enough of the error.

    Does NOT declare goal done — only ends the pulse early so SLAM can settle.
    Requires rotation in the commanded sense and integrated Δyaw covering
    ``fraction`` of |err_at_pulse_start|, capped so we still leave room for SLAM.
    """
    if approach_sign == 0 or abs(err_at_pulse_start) < 1e-6:
        return False
    # approach_sign +1 (need CCW): expect integrated_yaw > 0
    expected = 1 if approach_sign > 0 else -1
    if integrated_yaw_rad * expected <= 0.0:
        return False
    # Still spinning the right way (or nearly stopped after a burst).
    if live_gz * expected < -TURN_ACTIVE_GZ:
        return False  # spinning opposite — don't early-stop
    covered = abs(integrated_yaw_rad)
    target = abs(err_at_pulse_start) * fraction
    # Never early-stop until we've rotated at least ~tol (noise floor).
    min_cover = max(yaw_tol_rad * 0.8, math.radians(4.0))
    return covered >= max(target, min_cover)


def turn_seems_stuck(*, approach_sign: int, live_gz: float) -> bool:
    """Commanded a turn but gyro shows no rotation in that direction."""
    if approach_sign == 0:
        return False
    expected = 1 if approach_sign > 0 else -1
    return live_gz * expected < TURN_ACTIVE_GZ * 0.35


def forward_decel_hint(*, live_gy: float, commanded_forward: bool) -> bool:
    """Weak hint: |gy| spike during forward often marks stop/decel (experiment)."""
    if not commanded_forward:
        return False
    return abs(live_gy) >= LIN_ACTIVE_GY
