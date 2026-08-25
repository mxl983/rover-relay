"""Load and normalize navigation_run.jsonl into steppable frames."""

from __future__ import annotations

import json
import math
import re
from collections import defaultdict
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any


@dataclass
class RunSummary:
    nav_id: str
    label: str = ""
    start_iso: str = ""
    end_iso: str = ""
    event_count: int = 0
    result: str | None = None
    target: dict[str, Any] | None = None
    start_pose: dict[str, Any] | None = None
    phases: list[int] = field(default_factory=list)


@dataclass
class Frame:
    """One inspectable state in a rehearsal timeline."""

    index: int
    ts: float
    iso: str
    event: str
    nav_id: str
    pose: dict[str, Any] | None = None
    goal: dict[str, Any] | None = None
    drive: dict[str, Any] = field(default_factory=dict)
    nav_ui: dict[str, Any] = field(default_factory=dict)
    path_meta: dict[str, Any] = field(default_factory=dict)
    scan: dict[str, Any] | None = None
    distance_remaining: float | None = None
    elapsed_s: float | None = None
    heading_err_deg: float | None = None
    stall_s: float | None = None
    decision: str = ""
    action_keys: list[str] = field(default_factory=list)
    action_phase: str = ""
    log_lines: list[str] = field(default_factory=list)
    trail: list[list[float]] = field(default_factory=list)  # [[x,y], ...] up to here
    segments: list[dict[str, Any]] = field(default_factory=list)
    active_segment: dict[str, Any] | None = None
    raw: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


_FINE_DOCK_FIELD_RE = {
    "phase": re.compile(r"\bphase=(\S+)"),
    "keys": re.compile(r"\bkeys=([A-Za-z]*)"),
    "dxy": re.compile(r"(?:Δxy|dxy)=([-+]?\d+(?:\.\d+)?)m"),
    "fwd": re.compile(r"\bfwd=([-+]?\d+(?:\.\d+)?)m?"),
    "left": re.compile(r"\bleft=([-+]?\d+(?:\.\d+)?)m?"),
    "dyaw": re.compile(r"(?:Δyaw|dyaw)=([-+]?\d+(?:\.\d+)?)(?:°|deg)?"),
    "hold": re.compile(r"\bhold=([-+]?\d+(?:\.\d+)?)s"),
    "note": re.compile(r"(?:—|-)\s+(.*)$"),
    "result": re.compile(
        r"Fine dock finished:\s+(succeeded|canceled|docking_timeout|failed)"
    ),
}


def _parse_fine_dock_fields(msg: str) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for key, pat in _FINE_DOCK_FIELD_RE.items():
        m = pat.search(msg)
        if m:
            out[key] = m.group(1)
    return out


def parse_fine_dock_log_events(
    text: str, nav_id: str
) -> list[dict[str, Any]]:
    """Turn Fine dock docker log lines into synthetic dock_step / bookend events."""
    out: list[dict[str, Any]] = []
    pat = re.compile(
        r"\[(?:INFO|WARN|ERROR)\]\s+\[(\d+(?:\.\d+)?)\]\s+\[[^\]]+\]:\s+(.*)$"
    )
    for line in text.splitlines():
        if nav_id not in line or "Fine dock" not in line:
            continue
        m = pat.search(line)
        if not m:
            continue
        ts = float(m.group(1))
        msg = m.group(2).strip()
        fields = _parse_fine_dock_fields(msg)
        if "Fine dock start" in msg:
            out.append(
                {
                    "ts": ts,
                    "iso": "",
                    "event": "fine_dock_start",
                    "nav_id": nav_id,
                    "source": "docker_log",
                    "dock_note": msg,
                    "nav_ui": {
                        "phase": 3,
                        "label": "Phase 3 · Dock",
                        "dock_phase": "start",
                        "note": msg,
                    },
                }
            )
            continue
        if "Fine dock finished" in msg:
            result = fields.get("result") or "succeeded"
            if "canceled" in msg:
                result = "canceled"
            elif "timeout" in msg:
                result = "docking_timeout"
            out.append(
                {
                    "ts": ts,
                    "iso": "",
                    "event": "dock_finished",
                    "nav_id": nav_id,
                    "result": result,
                    "source": "docker_log",
                    "dock_note": msg,
                    "nav_ui": {
                        "phase": 3,
                        "label": "Phase 3 · Dock",
                        "dock_phase": "done",
                        "note": msg,
                    },
                }
            )
            continue
        keys_raw = fields.get("keys") or ""
        keys = [c.lower() for c in keys_raw if c.lower() in "wasd"]
        dock_phase = fields.get("phase") or "dock"
        note = fields.get("note") or msg
        dxy = _as_float(fields.get("dxy"))
        dyaw = _as_float(fields.get("dyaw"))
        out.append(
            {
                "ts": ts,
                "iso": "",
                "event": "dock_step",
                "nav_id": nav_id,
                "source": "docker_log",
                "distance_remaining": dxy,
                "keys": keys,
                "drive": {
                    "phase": f"dock_{dock_phase}",
                    "keys": keys,
                    "nav_ui": {
                        "phase": 3,
                        "label": "Phase 3 · Dock",
                        "dock_phase": dock_phase,
                        "position_error_m": dxy,
                        "yaw_remaining_deg": dyaw,
                        "fwd_m": _as_float(fields.get("fwd")),
                        "left_m": _as_float(fields.get("left")),
                        "note": note,
                        "dock_note": note,
                    },
                },
                "dock_note": note,
            }
        )
    out.sort(key=lambda e: float(e.get("ts") or 0.0))
    return out


def load_jsonl(path: str | Path) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    with open(path, encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(obj, dict) and obj.get("event"):
                events.append(obj)
    return events


def list_runs(events: list[dict[str, Any]]) -> list[RunSummary]:
    by_id: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for ev in events:
        nid = str(ev.get("nav_id") or "")
        if nid:
            by_id[nid].append(ev)
    summaries: list[RunSummary] = []
    for nid, evs in by_id.items():
        evs_sorted = sorted(evs, key=lambda e: float(e.get("ts") or 0.0))
        goto = next((e for e in evs_sorted if e.get("event") == "goto"), None)
        dock = next(
            (
                e
                for e in reversed(evs_sorted)
                if e.get("event") in ("dock_finished", "finished", "canceled")
            ),
            None,
        )
        result = None
        if dock:
            result = str(dock.get("result") or dock.get("event") or "")
        phases = _detect_phases(evs_sorted)
        summaries.append(
            RunSummary(
                nav_id=nid,
                label=str((goto or {}).get("label") or ""),
                start_iso=str(evs_sorted[0].get("iso") or ""),
                end_iso=str(evs_sorted[-1].get("iso") or ""),
                event_count=len(evs_sorted),
                result=result,
                target=(goto or {}).get("target")
                if isinstance((goto or {}).get("target"), dict)
                else None,
                start_pose=(goto or {}).get("start_pose")
                if isinstance((goto or {}).get("start_pose"), dict)
                else None,
                phases=phases,
            )
        )
    summaries.sort(key=lambda s: s.start_iso or s.nav_id)
    return summaries


def _detect_phases(evs: list[dict[str, Any]]) -> list[int]:
    seen: set[int] = set()
    for ev in evs:
        ui = _nav_ui_of(ev)
        phase = ui.get("phase")
        if isinstance(phase, int):
            seen.add(phase)
        event = str(ev.get("event") or "")
        if event in ("fine_dock_start", "dock_start", "final_yaw_start", "dock_step"):
            seen.add(3)
        if event == "dock_finished":
            seen.add(3)
        if event in ("segments_done", "coarse_done") or (
            isinstance(ui.get("label"), str) and "Phase 2" in ui["label"]
        ):
            seen.add(2)
        if isinstance(ui.get("label"), str) and "Phase 1" in ui["label"]:
            seen.add(1)
    return sorted(seen)


def latest_nav_id(events: list[dict[str, Any]]) -> str | None:
    runs = list_runs(events)
    return runs[-1].nav_id if runs else None


def _nav_ui_of(ev: dict[str, Any]) -> dict[str, Any]:
    drive = ev.get("drive") if isinstance(ev.get("drive"), dict) else {}
    ui = drive.get("nav_ui") if isinstance(drive.get("nav_ui"), dict) else None
    if ui:
        return dict(ui)
    ui2 = ev.get("nav_ui")
    return dict(ui2) if isinstance(ui2, dict) else {}


def _decision_text(ev: dict[str, Any], ui: dict[str, Any], drive: dict[str, Any]) -> str:
    event = str(ev.get("event") or "")
    note = str(ui.get("note") or "")
    label = str(ui.get("label") or "")
    dock_note = str(
        ev.get("dock_note")
        or ui.get("dock_note")
        or (ev.get("feedback") or {}).get("dock_note")
        or ""
    )
    if event == "goto":
        t = ev.get("target") or {}
        return f"GOTO {ev.get('label') or ''} → ({t.get('x')}, {t.get('y')})"
    if event.startswith("replan"):
        return f"REPLAN ({event}) — requesting new Nav2 path"
    if event in ("segments_done", "fine_dock_start", "final_yaw_start", "dock_start"):
        return f"{event}: handoff to fine dock / yaw"
    if event == "dock_step":
        return note or dock_note or f"Phase 3 · {ui.get('dock_phase') or 'dock'}"
    if event == "dock_finished":
        return f"dock finished: {ev.get('result')}"
    if note:
        return note
    if dock_note:
        return dock_note
    if label:
        extra = []
        if ui.get("yaw_remaining_deg") is not None:
            extra.append(f"yaw gap {ui['yaw_remaining_deg']}°")
        if ui.get("yaw_gap_deg") is not None:
            extra.append(f"yaw gap {ui['yaw_gap_deg']}°")
        if ui.get("position_error_m") is not None:
            extra.append(f"Δxy {ui['position_error_m']}m")
        return f"{label}" + (f" — {', '.join(extra)}" if extra else "")
    keys = drive.get("keys") or []
    if keys:
        return f"holding keys {''.join(str(k).upper() for k in keys)} ({drive.get('phase')})"
    return event


def parse_docker_log_lines(text: str, nav_id: str) -> list[tuple[float, str]]:
    """Extract (approx_ts, message) lines mentioning nav_id from docker logs."""
    out: list[tuple[float, str]] = []
    pat = re.compile(
        r"\[(?:INFO|WARN|ERROR)\]\s+\[(\d+(?:\.\d+)?)\]\s+\[[^\]]+\]:\s+(.*)$"
    )
    for line in text.splitlines():
        if nav_id not in line:
            continue
        m = pat.search(line)
        if not m:
            continue
        msg = m.group(2).strip()
        if "nav progress" in msg and "nav_ui=" in msg:
            short = msg
            if len(short) > 220:
                short = short[:220] + "…"
            out.append((float(m.group(1)), short))
            continue
        out.append((float(m.group(1)), msg))
    out.sort(key=lambda x: x[0])
    return out


def build_frames(
    events: list[dict[str, Any]],
    nav_id: str,
    *,
    log_lines: list[tuple[float, str]] | None = None,
    docker_log_text: str | None = None,
) -> list[Frame]:
    """Build steppable frames for one nav_id across phases 1–3."""
    selected = sorted(
        [e for e in events if str(e.get("nav_id") or "") == nav_id],
        key=lambda e: float(e.get("ts") or 0.0),
    )
    if not selected:
        return []

    # Merge Fine-dock docker pulses so phase 3 is steppable even on older logs.
    if docker_log_text:
        dock_events = parse_fine_dock_log_events(docker_log_text, nav_id)
        selected = _merge_dock_events(selected, dock_events)

    goal: dict[str, Any] | None = None
    trail: list[list[float]] = []
    last_segments: list[dict[str, Any]] = []
    last_active: dict[str, Any] | None = None
    last_pose: dict[str, Any] | None = None
    in_phase3 = False
    frames: list[Frame] = []
    log_i = 0
    logs = log_lines or []

    for idx, ev in enumerate(selected):
        ts = float(ev.get("ts") or 0.0)
        event_name = str(ev.get("event") or "")
        if event_name in ("fine_dock_start", "dock_start", "final_yaw_start", "dock_step"):
            in_phase3 = True
        if event_name == "dock_finished":
            in_phase3 = True

        drive = ev.get("drive") if isinstance(ev.get("drive"), dict) else {}
        ui = _nav_ui_of(ev)
        pose = ev.get("pose") if isinstance(ev.get("pose"), dict) else None
        if pose is None and isinstance(ev.get("start_pose"), dict):
            sp = ev["start_pose"]
            pose = {
                "x": sp.get("x"),
                "y": sp.get("y"),
                "yaw": sp.get("yaw"),
                "yaw_deg": None,
            }
            if pose["yaw"] is not None:
                pose["yaw_deg"] = round(math.degrees(float(pose["yaw"])), 1)

        # Carry pose forward through sparse dock events.
        if pose is None and last_pose is not None and in_phase3:
            pose = dict(last_pose)
        if pose is not None:
            last_pose = pose

        if isinstance(ev.get("goal"), dict):
            goal = ev["goal"]
        elif isinstance(ev.get("target"), dict):
            goal = ev["target"]
        elif isinstance(ev.get("target"), list) and len(ev["target"]) >= 2:
            goal = {
                "x": ev["target"][0],
                "y": ev["target"][1],
                "yaw": ev["target"][2] if len(ev["target"]) > 2 else None,
            }

        if pose and pose.get("x") is not None and pose.get("y") is not None:
            pt = [float(pose["x"]), float(pose["y"])]
            if not trail or trail[-1] != pt:
                trail.append(pt)

        segs = ui.get("segments")
        if isinstance(segs, list) and segs:
            last_segments = [s for s in segs if isinstance(s, dict)]
        active = ui.get("active_segment")
        if isinstance(active, dict):
            last_active = active

        # Synthesize phase-3 UI when recording omitted it.
        if in_phase3 and not ui.get("phase"):
            ui = {
                **ui,
                "phase": 3,
                "label": ui.get("label") or "Phase 3 · Dock",
            }
            if ev.get("dock_note"):
                ui["note"] = ev.get("dock_note")
                ui["dock_note"] = ev.get("dock_note")
            if isinstance(ev.get("feedback"), dict):
                fb = ev["feedback"]
                for k in (
                    "dock_phase",
                    "position_error_m",
                    "yaw_error_deg",
                    "fwd_m",
                    "left_m",
                    "dock_note",
                ):
                    if fb.get(k) is not None and ui.get(k) is None:
                        ui[k] = fb[k]
                if fb.get("yaw_error_deg") is not None and ui.get("yaw_remaining_deg") is None:
                    ui["yaw_remaining_deg"] = fb["yaw_error_deg"]

        keys = list(drive.get("keys") or [])
        if not keys and drive.get("latched_turn"):
            keys = list(drive.get("latched_turn") or [])
        # Dock keys may live on the event itself (newer recordings).
        if not keys and isinstance(ev.get("keys"), list):
            keys = list(ev["keys"])

        attached: list[str] = []
        next_ts = (
            float(selected[idx + 1].get("ts") or ts)
            if idx + 1 < len(selected)
            else ts + 1e9
        )
        while log_i < len(logs) and logs[log_i][0] <= next_ts + 0.05:
            if logs[log_i][0] >= ts - 0.5:
                msg = logs[log_i][1]
                if "nav progress" not in msg or "Fine dock" in msg:
                    attached.append(msg)
                elif not attached:
                    attached.append(msg)
            log_i += 1

        frames.append(
            Frame(
                index=idx,
                ts=ts,
                iso=str(ev.get("iso") or ""),
                event=event_name,
                nav_id=nav_id,
                pose=pose,
                goal=goal,
                drive=drive,
                nav_ui=ui,
                path_meta=ev.get("path") if isinstance(ev.get("path"), dict) else {},
                scan=ev.get("scan") if isinstance(ev.get("scan"), dict) else None,
                distance_remaining=_as_float(
                    ev.get("distance_remaining")
                    if ev.get("distance_remaining") is not None
                    else ui.get("position_error_m")
                ),
                elapsed_s=_as_float(ev.get("elapsed_s")),
                heading_err_deg=_as_float(ev.get("heading_err_deg")),
                stall_s=_as_float(ev.get("stall_s")),
                decision=_decision_text(ev, ui, drive),
                action_keys=[str(k).lower() for k in keys],
                action_phase=str(
                    drive.get("phase")
                    or ui.get("dock_phase")
                    or ui.get("segment_phase")
                    or ""
                ),
                log_lines=attached[:12],
                trail=list(trail),
                segments=list(last_segments),
                active_segment=dict(last_active) if last_active else None,
                raw={
                    "event": ev.get("event"),
                    "generation": ev.get("generation"),
                    "best_distance_m": ev.get("best_distance_m"),
                    "result": ev.get("result"),
                    "source": ev.get("source"),
                },
            )
        )
    # Re-index after merge
    for i, fr in enumerate(frames):
        fr.index = i
    return frames


def _merge_dock_events(
    selected: list[dict[str, Any]],
    dock_events: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Insert docker Fine-dock pulses; skip duplicates of bookend events."""
    if not dock_events:
        return selected
    has_dock_steps = any(e.get("event") == "dock_step" for e in selected)
    if has_dock_steps:
        # Newer recordings already include structured dock steps.
        return selected

    # Drop bare progress frames during docking when we have richer log pulses.
    dock_start_ts = None
    dock_end_ts = None
    for e in selected:
        if e.get("event") == "fine_dock_start":
            dock_start_ts = float(e.get("ts") or 0.0)
        if e.get("event") == "dock_finished":
            dock_end_ts = float(e.get("ts") or 0.0)
    for e in dock_events:
        if e.get("event") == "fine_dock_start" and dock_start_ts is None:
            dock_start_ts = float(e.get("ts") or 0.0)
        if e.get("event") == "dock_finished" and dock_end_ts is None:
            dock_end_ts = float(e.get("ts") or 0.0)

    filtered: list[dict[str, Any]] = []
    for e in selected:
        ev = str(e.get("event") or "")
        ts = float(e.get("ts") or 0.0)
        if (
            ev == "progress"
            and dock_start_ts is not None
            and ts >= dock_start_ts - 0.05
            and (dock_end_ts is None or ts <= dock_end_ts + 0.05)
        ):
            # Keep pose continuity by folding pose onto nearest dock_step later;
            # skip empty docking progress shells.
            ui = _nav_ui_of(e)
            if not ui.get("phase"):
                continue
        filtered.append(e)

    # Only inject mid-dock pulses (and missing bookends).
    existing_bookend_types = {
        e.get("event")
        for e in filtered
        if e.get("event") in ("fine_dock_start", "dock_finished")
    }
    extras: list[dict[str, Any]] = []
    last_pose: dict[str, Any] | None = None
    # Seed last pose from selected before dock.
    for e in selected:
        if isinstance(e.get("pose"), dict):
            last_pose = e["pose"]
        if e.get("event") == "fine_dock_start":
            break

    pose_samples = [
        (float(e.get("ts") or 0.0), e["pose"])
        for e in selected
        if isinstance(e.get("pose"), dict)
    ]

    def nearest_pose(ts: float) -> dict[str, Any] | None:
        if not pose_samples:
            return last_pose
        best = min(pose_samples, key=lambda p: abs(p[0] - ts))
        return best[1]

    for e in dock_events:
        ev = str(e.get("event") or "")
        ts = float(e.get("ts") or 0.0)
        if ev in ("fine_dock_start", "dock_finished") and ev in existing_bookend_types:
            continue
        if "pose" not in e or e.get("pose") is None:
            e = {**e, "pose": nearest_pose(ts)}
        extras.append(e)

    merged = filtered + extras
    merged.sort(key=lambda e: float(e.get("ts") or 0.0))
    return merged


def _as_float(v: Any) -> float | None:
    try:
        if v is None:
            return None
        return float(v)
    except (TypeError, ValueError):
        return None
