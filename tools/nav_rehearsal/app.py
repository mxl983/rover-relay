"""Browser UI for stepping through a recorded navigation run."""

from __future__ import annotations

import json
import threading
import webbrowser
from dataclasses import asdict
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

from .mapdata import MapLayer, load_best_map
from .parse import (
    RunSummary,
    build_frames,
    latest_nav_id,
    list_runs,
    load_jsonl,
    parse_docker_log_lines,
)

HTML_PAGE = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8" />
<meta name="viewport" content="width=device-width, initial-scale=1" />
<title>Nav Rehearsal</title>
<style>
  :root {
    --bg: #0a1014;
    --panel: #121a20;
    --line: #2a3c48;
    --text: #e4eef4;
    --muted: #8aa0ad;
    --accent: #5ec8ff;
    --good: #6eebb0;
    --warn: #ffc857;
    --hot: #ff7a66;
    --path: #5ec8ff;
    --seg: #ffd166;
    --trail: #7ad7c7;
  }
  * { box-sizing: border-box; }
  html, body { height: 100%; margin: 0; }
  body {
    display: flex; flex-direction: column; overflow: hidden;
    color: var(--text);
    background: radial-gradient(1200px 700px at 20% -10%, #14202a, var(--bg));
    font: 13px/1.45 ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
  }
  header {
    display: flex; align-items: center; justify-content: space-between; gap: 12px;
    padding: 10px 14px; border-bottom: 1px solid var(--line);
    background: rgba(6, 10, 14, 0.9);
  }
  header h1 { margin: 0; font-size: 15px; letter-spacing: 0.06em; }
  header .sub { color: var(--muted); font-size: 11px; }
  main {
    flex: 1; min-height: 0;
    display: grid; grid-template-columns: minmax(0, 1fr) 340px; gap: 10px;
    padding: 10px;
  }
  .stage {
    position: relative; min-width: 0; min-height: 0;
    border: 1px solid var(--line); border-radius: 10px; overflow: hidden;
    background: #070c10;
  }
  #map { width: 100%; height: 100%; display: block; }
  .hud {
    position: absolute; left: 10px; top: 10px; z-index: 2;
    padding: 8px 10px; border-radius: 8px;
    background: rgba(5, 10, 14, 0.82); border: 1px solid rgba(120,160,180,0.2);
    font-size: 11px; color: var(--muted); max-width: 55%;
  }
  .hud strong { color: var(--text); }
  aside {
    display: flex; flex-direction: column; gap: 10px; min-height: 0;
  }
  .card {
    background: var(--panel); border: 1px solid var(--line);
    border-radius: 10px; padding: 10px 12px;
  }
  .card h2 {
    margin: 0 0 8px; font-size: 11px; letter-spacing: 0.12em;
    text-transform: uppercase; color: var(--accent);
  }
  .row { display: flex; gap: 8px; flex-wrap: wrap; align-items: center; }
  button, select {
    appearance: none; border: 1px solid var(--line); background: #18232b;
    color: var(--text); border-radius: 8px; padding: 7px 10px; cursor: pointer;
    font: inherit;
  }
  button:hover { border-color: var(--accent); }
  button.primary { background: #1a3340; border-color: #3a6d82; }
  input[type=range] { width: 100%; }
  .keys {
    display: grid; grid-template-columns: repeat(3, 42px); gap: 6px;
    justify-content: center; margin-top: 6px;
  }
  .key {
    width: 42px; height: 36px; border-radius: 8px; display: grid; place-items: center;
    border: 1px solid var(--line); color: var(--muted); background: #0e151a;
  }
  .key.on { color: #081018; background: var(--accent); border-color: var(--accent); font-weight: 700; }
  .kv { display: grid; grid-template-columns: 110px 1fr; gap: 4px 8px; font-size: 12px; }
  .kv span:first-child { color: var(--muted); }
  .decision {
    padding: 8px 10px; border-radius: 8px; background: #0d161c;
    border: 1px solid rgba(255, 209, 102, 0.25); color: var(--warn);
    white-space: pre-wrap; word-break: break-word; min-height: 3.2em;
  }
  .logs {
    flex: 1; min-height: 120px; overflow: auto; font-size: 11px;
    background: #0a1116; border-radius: 8px; padding: 8px;
    border: 1px solid var(--line); color: var(--muted);
  }
  .logs div { margin-bottom: 6px; border-bottom: 1px dashed rgba(80,100,110,0.35); padding-bottom: 4px; }
  .meta { color: var(--muted); font-size: 11px; }
  footer {
    padding: 8px 14px 12px; border-top: 1px solid var(--line);
    display: flex; gap: 10px; align-items: center;
  }
  footer .grow { flex: 1; }
</style>
</head>
<body>
<header>
  <div>
    <h1>NAV REHEARSAL</h1>
    <div class="sub" id="runMeta">loading…</div>
  </div>
  <div class="row">
    <select id="runSelect"></select>
    <button id="reloadBtn">Reload</button>
  </div>
</header>
<main>
  <section class="stage">
    <canvas id="map"></canvas>
    <div class="hud" id="hud">—</div>
  </section>
  <aside>
    <div class="card">
      <h2>Stepper</h2>
      <div class="row">
        <button id="prevBtn">◀ Prev</button>
        <button id="nextBtn" class="primary">Next ▶</button>
        <button id="playBtn">Play</button>
      </div>
      <div class="meta" id="stepLabel" style="margin-top:8px">0 / 0</div>
      <input type="range" id="scrub" min="0" max="0" value="0" />
    </div>
    <div class="card">
      <h2>Decision</h2>
      <div class="decision" id="decision">—</div>
      <div class="keys" aria-label="keys">
        <div></div><div class="key" id="kW">W</div><div></div>
        <div class="key" id="kA">A</div><div class="key" id="kS">S</div><div class="key" id="kD">D</div>
      </div>
    </div>
    <div class="card">
      <h2>State</h2>
      <div class="kv" id="stateKv"></div>
    </div>
    <div class="card" style="flex:1; min-height:0; display:flex; flex-direction:column;">
      <h2>Log / actions</h2>
      <div class="logs" id="logs"></div>
    </div>
  </aside>
</main>
<footer>
  <span class="meta">← → step · Space play/pause · Home/End</span>
  <div class="grow"></div>
  <span class="meta" id="footerHint"></span>
</footer>
<script>
const state = {
  runs: [],
  frames: [],
  map: null,
  index: 0,
  playing: false,
  playTimer: null,
};

const $ = (id) => document.getElementById(id);

async function api(path) {
  const r = await fetch(path, { cache: "no-store" });
  if (!r.ok) throw new Error(await r.text());
  return r.json();
}

function setKeys(keys) {
  const set = new Set((keys || []).map((k) => String(k).toLowerCase()));
  for (const [id, k] of [["kW","w"],["kA","a"],["kS","s"],["kD","d"]]) {
    $(id).classList.toggle("on", set.has(k));
  }
}

function kv(rows) {
  const el = $("stateKv");
  el.innerHTML = "";
  for (const [k, v] of rows) {
    const a = document.createElement("span"); a.textContent = k;
    const b = document.createElement("span"); b.textContent = v == null ? "—" : String(v);
    el.append(a, b);
  }
}

function worldBounds(frames, map) {
  // Fit the run (trail/goal/segments), not the whole SLAM grid — that zoomed
  // the rover into a speck and made walls look "broken".
  let minx = Infinity, miny = Infinity, maxx = -Infinity, maxy = -Infinity;
  const add = (x, y) => {
    if (!Number.isFinite(x) || !Number.isFinite(y)) return;
    minx = Math.min(minx, x); miny = Math.min(miny, y);
    maxx = Math.max(maxx, x); maxy = Math.max(maxy, y);
  };
  for (const f of frames) {
    if (f.pose) add(f.pose.x, f.pose.y);
    if (f.goal) add(f.goal.x, f.goal.y);
    for (const p of f.trail || []) add(p[0], p[1]);
    for (const s of f.segments || []) {
      add(s.x0, s.y0); add(s.x1, s.y1);
    }
    if (f.active_segment) {
      add(f.active_segment.x0, f.active_segment.y0);
      add(f.active_segment.x1, f.active_segment.y1);
    }
  }
  if (!Number.isFinite(minx) && map?.bounds) {
    add(map.bounds[0], map.bounds[1]);
    add(map.bounds[2], map.bounds[3]);
  }
  if (!Number.isFinite(minx)) return { minx: -2, miny: -2, maxx: 2, maxy: 2 };
  // Keep a usable minimum span so short docks still have context.
  const pad = 0.8;
  let loX = minx - pad, loY = miny - pad, hiX = maxx + pad, hiY = maxy + pad;
  const minSpan = 3.0;
  if (hiX - loX < minSpan) {
    const mid = (loX + hiX) / 2;
    loX = mid - minSpan / 2; hiX = mid + minSpan / 2;
  }
  if (hiY - loY < minSpan) {
    const mid = (loY + hiY) / 2;
    loY = mid - minSpan / 2; hiY = mid + minSpan / 2;
  }
  return { minx: loX, miny: loY, maxx: hiX, maxy: hiY };
}

function draw() {
  const canvas = $("map");
  const dpr = window.devicePixelRatio || 1;
  const cssW = canvas.clientWidth;
  const cssH = canvas.clientHeight;
  canvas.width = Math.floor(cssW * dpr);
  canvas.height = Math.floor(cssH * dpr);
  const ctx = canvas.getContext("2d");
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  ctx.clearRect(0, 0, cssW, cssH);

  const frames = state.frames;
  const f = frames[state.index];
  const map = state.map;
  const b = worldBounds(frames, map);
  const worldW = Math.max(0.5, b.maxx - b.minx);
  const worldH = Math.max(0.5, b.maxy - b.miny);
  const scale = Math.min(cssW / worldW, cssH / worldH) * 0.92;
  const ox = cssW / 2 - ((b.minx + b.maxx) / 2) * scale;
  const oy = cssH / 2 + ((b.miny + b.maxy) / 2) * scale;
  const toScreen = (x, y) => ({ sx: ox + x * scale, sy: oy - y * scale });

  // grid
  ctx.strokeStyle = "rgba(80,110,130,0.18)";
  ctx.lineWidth = 1;
  const g0 = Math.floor(b.minx);
  const g1 = Math.ceil(b.maxx);
  for (let gx = g0; gx <= g1; gx++) {
    const a = toScreen(gx, b.miny), c = toScreen(gx, b.maxy);
    ctx.beginPath(); ctx.moveTo(a.sx, a.sy); ctx.lineTo(c.sx, c.sy); ctx.stroke();
  }
  for (let gy = Math.floor(b.miny); gy <= Math.ceil(b.maxy); gy++) {
    const a = toScreen(b.minx, gy), c = toScreen(b.maxx, gy);
    ctx.beginPath(); ctx.moveTo(a.sx, a.sy); ctx.lineTo(c.sx, c.sy); ctx.stroke();
  }

  // map occupied (only cells visible in the run viewport)
  if (map?.occupied_xy?.length) {
    const cell = Math.max(1.5, (map.resolution || 0.05) * scale);
    ctx.fillStyle = "rgba(140, 180, 230, 0.85)";
    for (const p of map.occupied_xy) {
      if (p[0] < b.minx - 0.5 || p[0] > b.maxx + 0.5) continue;
      if (p[1] < b.miny - 0.5 || p[1] > b.maxy + 0.5) continue;
      const s = toScreen(p[0], p[1]);
      ctx.fillRect(s.sx - cell / 2, s.sy - cell / 2, cell, cell);
    }
  }

  if (!f) return;

  // trail
  if (f.trail?.length > 1) {
    ctx.strokeStyle = "rgba(122, 215, 199, 0.75)";
    ctx.lineWidth = 2;
    ctx.beginPath();
    f.trail.forEach((p, i) => {
      const s = toScreen(p[0], p[1]);
      if (i === 0) ctx.moveTo(s.sx, s.sy); else ctx.lineTo(s.sx, s.sy);
    });
    ctx.stroke();
  }

  // segments
  for (const seg of f.segments || []) {
    const a = toScreen(seg.x0, seg.y0);
    const c = toScreen(seg.x1, seg.y1);
    ctx.strokeStyle = "rgba(94, 200, 255, 0.35)";
    ctx.lineWidth = 2;
    ctx.setLineDash([4, 4]);
    ctx.beginPath(); ctx.moveTo(a.sx, a.sy); ctx.lineTo(c.sx, c.sy); ctx.stroke();
    ctx.setLineDash([]);
  }

  // active segment
  if (f.active_segment) {
    const a = toScreen(f.active_segment.x0, f.active_segment.y0);
    const c = toScreen(f.active_segment.x1, f.active_segment.y1);
    ctx.strokeStyle = "rgba(255, 209, 102, 0.95)";
    ctx.lineWidth = 4;
    ctx.beginPath(); ctx.moveTo(a.sx, a.sy); ctx.lineTo(c.sx, c.sy); ctx.stroke();
    ctx.fillStyle = "#ffd166";
    for (const p of [a, c]) {
      ctx.beginPath(); ctx.arc(p.sx, p.sy, 3.5, 0, Math.PI * 2); ctx.fill();
    }
  }

  // goal
  if (f.goal) {
    const g = toScreen(f.goal.x, f.goal.y);
    ctx.strokeStyle = "#ff7a66";
    ctx.lineWidth = 2;
    ctx.beginPath(); ctx.arc(g.sx, g.sy, 8, 0, Math.PI * 2); ctx.stroke();
    ctx.fillStyle = "#ff7a66";
    ctx.fillRect(g.sx - 2, g.sy - 2, 4, 4);
    if (f.goal.yaw != null) {
      const yaw = Number(f.goal.yaw);
      ctx.beginPath();
      ctx.moveTo(g.sx, g.sy);
      ctx.lineTo(g.sx + Math.cos(yaw) * 16, g.sy - Math.sin(yaw) * 16);
      ctx.stroke();
    }
  }

  // rover
  if (f.pose) {
    const p = toScreen(f.pose.x, f.pose.y);
    const yaw = Number(f.pose.yaw || 0);
    ctx.save();
    ctx.translate(p.sx, p.sy);
    ctx.rotate(-yaw);
    ctx.fillStyle = "#5ec8ff";
    ctx.beginPath();
    ctx.moveTo(12, 0); ctx.lineTo(-8, 7); ctx.lineTo(-8, -7); ctx.closePath();
    ctx.fill();
    ctx.strokeStyle = "rgba(255,255,255,0.55)";
    ctx.stroke();
    ctx.restore();
  }
}

function render() {
  const frames = state.frames;
  const f = frames[state.index];
  $("stepLabel").textContent = frames.length
    ? `frame ${state.index + 1} / ${frames.length} · ${f?.event || ""} · ${f?.iso || ""}`
    : "no frames";
  $("scrub").max = Math.max(0, frames.length - 1);
  $("scrub").value = String(state.index);
  if (!f) {
    $("decision").textContent = "No frames for this run.";
    setKeys([]);
    kv([]);
    $("logs").innerHTML = "";
    draw();
    return;
  }
  $("decision").textContent = f.decision || f.event;
  setKeys(f.action_keys);
  const ui = f.nav_ui || {};
  const phaseLabel = ui.label
    || (ui.phase === 1 ? "Phase 1 · Align"
      : ui.phase === 2 ? "Phase 2 · Segments"
      : ui.phase === 3 ? "Phase 3 · Dock"
      : f.action_phase || "—");
  kv([
    ["phase", phaseLabel],
    ["segment", ui.segment != null ? `${ui.segment}/${ui.segments_total || "?"}` : "—"],
    ["seg / dock", ui.segment_phase || ui.dock_phase || "—"],
    ["pose", f.pose ? `(${Number(f.pose.x).toFixed(2)}, ${Number(f.pose.y).toFixed(2)}) ${Number(f.pose.yaw_deg ?? (f.pose.yaw*180/Math.PI)).toFixed(1)}°` : "—"],
    ["Δ goal", f.distance_remaining != null ? `${Number(f.distance_remaining).toFixed(3)} m` : "—"],
    ["yaw gap", ui.yaw_gap_deg ?? ui.yaw_remaining_deg ?? ui.yaw_error_deg ?? "—"],
    ["fwd / left", (ui.fwd_m != null || ui.left_m != null) ? `${ui.fwd_m ?? "—"} / ${ui.left_m ?? "—"}` : "—"],
    ["aim", ui.aim_heading_deg ?? "—"],
    ["drive rem", ui.drive_remaining_m ?? "—"],
    ["keys", (f.action_keys || []).join("+").toUpperCase() || "∅"],
    ["drive phase", f.action_phase || "—"],
    ["elapsed", f.elapsed_s != null ? `${f.elapsed_s}s` : "—"],
    ["stall", f.stall_s != null ? `${f.stall_s}s` : "—"],
    ["scan near", f.scan?.nearest_m ?? "—"],
    ["path len", f.path_meta?.path_length_m ?? "—"],
  ]);
  const logs = $("logs");
  logs.innerHTML = "";
  for (const line of (f.log_lines || [])) {
    const d = document.createElement("div");
    d.textContent = line;
    logs.appendChild(d);
  }
  if (!(f.log_lines || []).length) {
    const d = document.createElement("div");
    d.textContent = "(no attached docker decision lines for this step)";
    logs.appendChild(d);
  }
  $("hud").innerHTML = `<strong>${f.event}</strong> · ${f.decision}`;
  draw();
}

function goto(i) {
  if (!state.frames.length) return;
  state.index = Math.max(0, Math.min(state.frames.length - 1, i));
  render();
}

function stopPlay() {
  state.playing = false;
  $("playBtn").textContent = "Play";
  if (state.playTimer) clearInterval(state.playTimer);
  state.playTimer = null;
}

function togglePlay() {
  if (state.playing) { stopPlay(); return; }
  state.playing = true;
  $("playBtn").textContent = "Pause";
  state.playTimer = setInterval(() => {
    if (state.index >= state.frames.length - 1) { stopPlay(); return; }
    goto(state.index + 1);
  }, 450);
}

async function loadRun(navId) {
  stopPlay();
  const q = navId ? `?nav_id=${encodeURIComponent(navId)}` : "";
  const data = await api("/api/run" + q);
  state.frames = data.frames || [];
  state.map = data.map || null;
  state.index = 0;
  const phases = (data.summary?.phases || []).join("") || "?";
  $("runMeta").textContent = `${data.summary?.nav_id || "—"} · ${data.summary?.label || ""} · phases ${phases} · ${data.frames?.length || 0} frames · result ${data.summary?.result || "?"}`;
  $("footerHint").textContent = data.map
    ? `map ${data.map.source || "ok"} · ${data.map.occupied_count} cells`
    : "no slam map loaded";
  render();
}

async function boot() {
  const meta = await api("/api/meta");
  state.runs = meta.runs || [];
  const sel = $("runSelect");
  sel.innerHTML = "";
  for (const r of state.runs.slice().reverse()) {
    const opt = document.createElement("option");
    opt.value = r.nav_id;
    const ph = (r.phases || []).length ? `P${(r.phases || []).join("")}` : "P?";
    opt.textContent = `${r.nav_id} · ${r.label || "?"} · ${ph} · ${r.event_count}ev · ${r.result || "?"}`;
    sel.appendChild(opt);
  }
  const initial = meta.latest_nav_id || state.runs.at(-1)?.nav_id;
  if (initial) {
    sel.value = initial;
    await loadRun(initial);
  }
}

$("prevBtn").onclick = () => goto(state.index - 1);
$("nextBtn").onclick = () => goto(state.index + 1);
$("playBtn").onclick = togglePlay;
$("scrub").oninput = (e) => goto(Number(e.target.value));
$("runSelect").onchange = (e) => loadRun(e.target.value);
$("reloadBtn").onclick = () => boot();
window.addEventListener("resize", draw);
window.addEventListener("keydown", (e) => {
  if (e.key === "ArrowRight") { e.preventDefault(); goto(state.index + 1); }
  if (e.key === "ArrowLeft") { e.preventDefault(); goto(state.index - 1); }
  if (e.key === " ") { e.preventDefault(); togglePlay(); }
  if (e.key === "Home") goto(0);
  if (e.key === "End") goto(state.frames.length - 1);
});

boot().catch((err) => {
  $("runMeta").textContent = String(err);
});
</script>
</body>
</html>
"""


class RehearsalState:
    def __init__(
        self,
        *,
        events: list[dict[str, Any]],
        map_layer: MapLayer | None,
        log_text: str = "",
    ) -> None:
        self.events = events
        self.map_layer = map_layer
        self.log_text = log_text
        self.runs: list[RunSummary] = list_runs(events)

    def frames_for(self, nav_id: str) -> list[dict[str, Any]]:
        logs = parse_docker_log_lines(self.log_text, nav_id) if self.log_text else []
        frames = build_frames(
            self.events,
            nav_id,
            log_lines=logs,
            docker_log_text=self.log_text or None,
        )
        return [f.to_dict() for f in frames]

    def map_for(
        self, nav_id: str, frames: list[dict[str, Any]] | None = None
    ) -> dict[str, Any] | None:
        if self.map_layer is None:
            return None
        frames = frames if frames is not None else self.frames_for(nav_id)
        xs: list[float] = []
        ys: list[float] = []
        for f in frames:
            pose = f.get("pose") or {}
            if pose.get("x") is not None and pose.get("y") is not None:
                xs.append(float(pose["x"]))
                ys.append(float(pose["y"]))
            goal = f.get("goal") or {}
            if goal.get("x") is not None and goal.get("y") is not None:
                xs.append(float(goal["x"]))
                ys.append(float(goal["y"]))
            for p in f.get("trail") or []:
                if isinstance(p, (list, tuple)) and len(p) >= 2:
                    xs.append(float(p[0]))
                    ys.append(float(p[1]))
        if xs and ys:
            cropped = self.map_layer.crop(min(xs), min(ys), max(xs), max(ys), pad=2.5)
            return cropped.to_dict()
        return self.map_layer.to_dict()


def run_app(
    *,
    run_jsonl: Path,
    slam_map: Path | None = None,
    slam_live: Path | None = None,
    docker_log: Path | None = None,
    host: str = "127.0.0.1",
    port: int = 8765,
    open_browser: bool = True,
) -> None:
    events = load_jsonl(run_jsonl)
    map_layer = load_best_map(slam_live, slam_map)
    log_text = ""
    if docker_log and docker_log.is_file():
        log_text = docker_log.read_text(encoding="utf-8", errors="replace")
    state = RehearsalState(events=events, map_layer=map_layer, log_text=log_text)

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

        def do_GET(self) -> None:  # noqa: N802
            parsed = urlparse(self.path)
            if parsed.path in ("/", "/index.html"):
                body = HTML_PAGE.encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
                return
            if parsed.path == "/api/meta":
                self._json(
                    200,
                    {
                        "runs": [asdict(r) for r in state.runs],
                        "latest_nav_id": latest_nav_id(state.events),
                        "event_total": len(state.events),
                        "has_map": state.map_layer is not None,
                        "has_docker_log": bool(state.log_text),
                    },
                )
                return
            if parsed.path == "/api/run":
                qs = parse_qs(parsed.query)
                nav_id = (qs.get("nav_id") or [None])[0] or latest_nav_id(state.events)
                if not nav_id:
                    self._json(404, {"error": "no runs found"})
                    return
                summary = next((r for r in state.runs if r.nav_id == nav_id), None)
                frames = state.frames_for(nav_id)
                # Annotate summary with phases actually present in frames.
                if summary is not None:
                    seen = set(summary.phases)
                    for fr in frames:
                        ph = (fr.get("nav_ui") or {}).get("phase")
                        if isinstance(ph, int):
                            seen.add(ph)
                    summary.phases = sorted(seen)
                self._json(
                    200,
                    {
                        "nav_id": nav_id,
                        "summary": asdict(summary) if summary else {"nav_id": nav_id},
                        "frames": frames,
                        "map": state.map_for(nav_id, frames),
                    },
                )
                return
            self.send_error(404)

    class ReuseHTTPServer(ThreadingHTTPServer):
        allow_reuse_address = True

    bound_port = port
    server: ReuseHTTPServer | None = None
    last_err: OSError | None = None
    for candidate in range(port, port + 20):
        try:
            server = ReuseHTTPServer((host, candidate), Handler)
            bound_port = candidate
            break
        except OSError as err:
            last_err = err
            server = None
            continue
    if server is None:
        raise SystemExit(
            f"Could not bind {host}:{port}–{port + 19}: {last_err}\n"
            f"Try: python3 -m tools.nav_rehearsal --from-docker --port 8766"
        )
    if bound_port != port:
        print(f"port {port} busy — using {bound_port}")

    url = f"http://{host}:{bound_port}/"
    print(f"Nav rehearsal at {url}")
    print(f"  run log: {run_jsonl}")
    if map_layer:
        print(
            f"  slam map: {map_layer.source} · {len(map_layer.occupied_xy)} cells "
            f"(live={slam_live} grid={slam_map})"
        )
    else:
        print(f"  slam map: missing (tried live={slam_live} grid={slam_map})")
    if docker_log:
        print(f"  docker log: {docker_log}")
    if open_browser:
        threading.Timer(0.4, lambda: webbrowser.open(url)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nstopped")
    finally:
        server.server_close()
