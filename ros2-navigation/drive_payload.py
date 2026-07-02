"""Map planner output to Pi drive vectors (forward = negative y)."""

from __future__ import annotations

from typing import Any

from local_planner import DriveCommand


def drive_to_payload(cmd: DriveCommand) -> dict[str, Any]:
    # Pi motor mix: forward = -y, turn = x → left = -y + x, right = -y - x.
    linear = max(0.0, min(1.0, cmd.linear))
    x = max(-1.0, min(1.0, cmd.angular))
    if linear > 0:
        x = max(-linear, min(linear, x))
        y = -linear
    else:
        y = 0.0
    return {"drive": {"x": round(x, 3), "y": round(y, 3)}}
