"""Local browser GUI for the internal SLAM/Nav simulator.

Big canvas = ground truth. Mini map = perceived SLAM (rover-centered, heading-up).
Uses only the Python standard library. Not part of the control dashboard.
"""

from __future__ import annotations

import json
import threading
import time
import urllib.parse
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from .engine import SCENARIOS, SlamNavSimulation, run_regressions

HTML_PAGE = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8" />
<meta name="viewport" content="width=device-width, initial-scale=1" />
<title>SLAM + Nav Simulator</title>
<style>
  :root {
    --bg: #071016;
    --panel: #121c24;
    --line: #2d4654;
    --text: #d9eef6;
    --muted: #8eacb8;
    --accent: #4bc8ff;
    --good: #63eda9;
  }
  * { box-sizing: border-box; }
  html, body { height: 100%; }
  body {
    margin: 0;
    display: flex;
    flex-direction: column;
    overflow: hidden;
    color: var(--text);
    background: linear-gradient(150deg, #050a0e, #0b1218 55%, #100d12);
    font: 13px/1.4 ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
  }
  header {
    flex: 0 0 auto;
    display: flex;
    justify-content: space-between;
    align-items: center;
    gap: 12px;
    padding: 12px 16px;
    border-bottom: 1px solid var(--line);
    background: rgba(3, 8, 12, 0.85);
  }
  header h1 { margin: 0; font-size: 16px; letter-spacing: 0.08em; }
  header .eyebrow { color: var(--accent); font-size: 10px; letter-spacing: 0.16em; }
  main {
    flex: 1 1 auto;
    min-height: 0;
    display: grid;
    grid-template-columns: minmax(0, 1fr) 300px;
    gap: 12px;
    padding: 12px;
  }
  .stage {
    position: relative;
    min-width: 0;
    min-height: 0;
    height: 100%;
    border: 1px solid var(--line);
    border-radius: 10px;
    overflow: hidden;
    background: #071016;
  }
  #world { width: 100%; height: 100%; display: block; cursor: crosshair; }
  .hint, .label-gt {
    position: absolute; z-index: 2; pointer-events: none;
    padding: 6px 8px; border-radius: 6px; font-size: 11px;
    background: rgba(3, 9, 13, 0.78);
    border: 1px solid rgba(155, 215, 230, 0.14);
  }
  .hint { left: 12px; top: 12px; color: var(--muted); }
  .label-gt {
    left: 12px; bottom: 12px; color: var(--good);
    letter-spacing: 0.08em; font-size: 10px;
  }
  aside { display: flex; flex-direction: column; gap: 8px; overflow: auto; }
  .card {
    padding: 10px;
    border: 1px solid var(--line);
    border-radius: 8px;
    background: rgba(19, 30, 38, 0.78);
  }
  .card h2 {
    margin: 0 0 8px;
    color: #bfeefa;
    font-size: 11px;
    letter-spacing: 0.08em;
    text-transform: uppercase;
  }
  .grid { display: grid; grid-template-columns: 1fr 1fr; gap: 6px; }
  button, select {
    min-height: 30px;
    border: 1px solid var(--line);
    border-radius: 5px;
    color: var(--text);
    background: #0c1920;
    font: inherit;
    cursor: pointer;
  }
  button:hover { border-color: var(--accent); }
  select { width: 100%; }
  label {
    display: flex; gap: 6px; align-items: center;
    color: var(--muted); font-size: 12px; margin-top: 8px;
  }
  .metric {
    padding: 6px;
    border: 1px solid rgba(115, 191, 208, 0.14);
    border-radius: 5px;
    background: rgba(5, 14, 19, 0.45);
  }
  .metric span { display: block; color: var(--muted); font-size: 10px; }
  .metric strong { font-size: 13px; }
  .status { color: var(--good); font-weight: 600; text-align: right; max-width: 420px; }
  .status.is-bad { color: #ff8d7a; }
  .status.is-ok { color: #73ffba; }
  .status.is-busy { color: #ffd35c; }
  .nav-banner {
    display: none;
    margin-top: 8px;
    padding: 8px 10px;
    border-radius: 6px;
    font-size: 12px;
    font-weight: 600;
    letter-spacing: 0.02em;
  }
  .nav-banner.show { display: block; }
  .nav-banner.reachable { background: rgba(80, 180, 120, 0.18); color: #73ffba; border: 1px solid rgba(115,255,186,0.35); }
  .nav-banner.unreachable { background: rgba(255, 100, 80, 0.16); color: #ff9b8a; border: 1px solid rgba(255,140,110,0.4); }
  .nav-banner.complete { background: rgba(80, 200, 255, 0.16); color: #7ad7ff; border: 1px solid rgba(120,210,255,0.4); }
  .mini-wrap {
    position: relative;
    aspect-ratio: 1;
    width: 100%;
    border: 1px solid var(--line);
    border-radius: 8px;
    overflow: hidden;
    background: #050b10;
  }
  #perceived { width: 100%; height: 100%; display: block; }
  .mini-caption {
    position: absolute; left: 8px; top: 8px;
    font-size: 10px; letter-spacing: 0.08em; color: var(--accent);
    pointer-events: none;
  }
  @media (max-width: 900px) {
    main {
      grid-template-columns: 1fr;
      grid-template-rows: minmax(260px, 1fr) auto;
    }
  }
</style>
</head>
<body>
<header>
  <div>
    <div class="eyebrow">INTERNAL TEST BED</div>
    <h1>SLAM + NAV SIMULATOR</h1>
  </div>
  <div class="status" id="status">connecting…</div>
</header>
<main>
  <section class="stage">
    <canvas id="world"></canvas>
    <div class="hint">click-drag goal = pose (xy+yaw) · scroll = zoom · right-drag = pan · double-click = follow · green = true, yellow = estimate · co-sim drive = Nav2 Twist → Pi stick (not WASD)</div>
    <div class="label-gt">GROUND TRUTH</div>
  </section>
  <aside>
    <section class="card">
      <h2>Perceived map</h2>
      <div class="mini-wrap">
        <canvas id="perceived"></canvas>
        <div class="mini-caption">EGO · HEADING UP · map + live lidar</div>
      </div>
    </section>
    <section class="card">
      <h2>Controls</h2>
      <select id="scenario"></select>
      <p id="description" style="color:var(--muted);font-size:11px;margin:8px 0 0"></p>
      <div class="grid" style="margin-top:8px">
        <button data-cmd="toggle_run">Run / Pause</button>
        <button data-cmd="reset">Reset</button>
        <button data-cmd="auto_map">Auto Map</button>
        <button data-cmd="build_map">Build Map</button>
        <button data-cmd="default_goal">Default Goal</button>
        <button data-cmd="stop">Stop</button>
      </div>
      <div class="nav-banner" id="navBanner"></div>
      <label>
        <input type="checkbox" id="follow" checked /> follow rover (zoom stays on robot)
      </label>
      <label>
        <input type="checkbox" id="kidnap" /> next click kidnaps ground truth
      </label>
      <label>
        speed
        <input type="range" id="speed" min="0.25" max="4" step="0.25" value="1" />
        <span id="speedLabel">1.00×</span>
      </label>
    </section>
    <section class="card">
      <h2>Metrics</h2>
      <div class="grid" id="metrics"></div>
    </section>
  </aside>
</main>
<script>
const worldCanvas = document.getElementById("world");
const worldCtx = worldCanvas.getContext("2d");
const miniCanvas = document.getElementById("perceived");
const miniCtx = miniCanvas.getContext("2d");
const MINI_RANGE_M = 8;
let state = null;
let mapViewPose = null; // smoothed pose for occupancy (stops map swimming)
let keys = new Set();
let running = true;
let drag = null;
let panDrag = null;
let goalDrag = null;
// Camera for the ground-truth canvas (large saved maps need zoom to see motion).
let cam = { zoom: 1, cx: null, cy: null, follow: true };

function resetCameraForScenario(sim) {
  const diag = Math.hypot(sim.scenario.width, sim.scenario.height);
  cam.follow = true;
  cam.zoom = diag > 12 ? 6 : 1.4;
  const pose = sim.pose || sim.estimated_pose;
  if (pose) {
    cam.cx = pose.x;
    cam.cy = pose.y;
  } else {
    cam.cx = sim.scenario.width / 2;
    cam.cy = sim.scenario.height / 2;
  }
  const followEl = document.getElementById("follow");
  if (followEl) followEl.checked = cam.follow;
}

function worldTransform(sim, width, height) {
  const worldW = sim.scenario.width;
  const worldH = sim.scenario.height;
  const inset = { left: 16, right: 16, top: 48, bottom: 28 };
  const availW = Math.max(1, width - inset.left - inset.right);
  const availH = Math.max(1, height - inset.top - inset.bottom);
  const fit = Math.min(availW / worldW, availH / worldH);
  const scale = Math.max(8, fit * cam.zoom);  // px/m; zoomed saved maps need >= ~8

  if (cam.follow && sim.pose) {
    cam.cx = sim.pose.x;
    cam.cy = sim.pose.y;
  }
  if (cam.cx == null || cam.cy == null) {
    cam.cx = worldW / 2;
    cam.cy = worldH / 2;
  }

  // Focus (cx,cy) at the canvas center so zoomed views stay on the rover.
  const originX = width / 2 - cam.cx * scale;
  const originY = height / 2 - cam.cy * scale;
  return {
    scale,
    offsetX: originX,
    offsetY: originY,
    point(x, y) {
      return { x: originX + x * scale, y: height / 2 + (cam.cy - y) * scale };
    },
    world(sx, sy) {
      return {
        x: (sx - originX) / scale,
        y: cam.cy - (sy - height / 2) / scale,
      };
    },
  };
}

function fitCanvas(canvas, ctx) {
  const parent = canvas.parentElement;
  const w = Math.max(1, (parent && parent.clientWidth) || canvas.clientWidth);
  const h = Math.max(1, (parent && parent.clientHeight) || canvas.clientHeight);
  const dpr = Math.min(window.devicePixelRatio || 1, 2);
  canvas.style.width = w + "px";
  canvas.style.height = h + "px";
  canvas.width = Math.round(w * dpr);
  canvas.height = Math.round(h * dpr);
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
}

function resize() {
  fitCanvas(worldCanvas, worldCtx);
  fitCanvas(miniCanvas, miniCtx);
  draw();
}
window.addEventListener("resize", resize);
if (typeof ResizeObserver !== "undefined") {
  new ResizeObserver(() => resize()).observe(worldCanvas.parentElement || worldCanvas);
  new ResizeObserver(() => resize()).observe(miniCanvas.parentElement || miniCanvas);
}

function drawPoseMarker(ctx, t, pose, color, label) {
  const g = t.point(pose.x, pose.y);
  const yaw = Number(pose.yaw) || 0;
  const len = 18;
  ctx.strokeStyle = color;
  ctx.fillStyle = color;
  ctx.lineWidth = 2;
  ctx.beginPath();
  ctx.arc(g.x, g.y, 8, 0, Math.PI * 2);
  ctx.stroke();
  // Heading arrow (screen: +x right, +y down; world yaw CCW from +x).
  const tipX = g.x + Math.cos(yaw) * len;
  const tipY = g.y - Math.sin(yaw) * len;
  ctx.beginPath();
  ctx.moveTo(g.x, g.y);
  ctx.lineTo(tipX, tipY);
  ctx.stroke();
  const leftX = tipX - Math.cos(yaw - 0.4) * 7;
  const leftY = tipY + Math.sin(yaw - 0.4) * 7;
  const rightX = tipX - Math.cos(yaw + 0.4) * 7;
  const rightY = tipY + Math.sin(yaw + 0.4) * 7;
  ctx.beginPath();
  ctx.moveTo(tipX, tipY);
  ctx.lineTo(leftX, leftY);
  ctx.lineTo(rightX, rightY);
  ctx.closePath();
  ctx.fill();
  if (label) {
    ctx.font = "600 11px ui-sans-serif, system-ui, sans-serif";
    ctx.fillText(label, g.x + 12, g.y - 10);
  }
}

function drawRover(ctx, t, pose, color, alpha) {
  const c = t.point(pose.x, pose.y);
  const size = 0.35 * t.scale;
  ctx.save();
  ctx.globalAlpha = alpha;
  ctx.translate(c.x, c.y);
  ctx.rotate(-(pose.yaw || 0));
  ctx.strokeStyle = color;
  ctx.lineWidth = 2;
  ctx.strokeRect(-size / 2, -size / 2, size, size);
  ctx.beginPath();
  ctx.moveTo(0, 0);
  ctx.lineTo(size * 0.72, 0);
  ctx.stroke();
  ctx.restore();
}

function drawLidarBeams(ctx, t) {
  const rays = state.lidar || [];
  if (!rays.length || !state.pose) return;
  const pose = state.pose;
  const origin = t.point(pose.x, pose.y);
  const spec = state.lidar_spec || {};
  const minRange = spec.min_range_m != null ? spec.min_range_m : 0.1;

  // Visible FOV beams from true pose (LD19: 360° instrument, 270° after body blind).
  for (const ray of rays) {
    if (ray.blind) continue;
    const dist = Math.max(Number(ray.distance) || 0, minRange);
    const end = t.point(
      pose.x + Math.cos(ray.angle) * dist,
      pose.y + Math.sin(ray.angle) * dist
    );
    ctx.strokeStyle = ray.hit
      ? "rgba(97, 255, 187, 0.32)"
      : "rgba(97, 220, 255, 0.10)";
    ctx.lineWidth = 1;
    ctx.beginPath();
    ctx.moveTo(origin.x, origin.y);
    ctx.lineTo(end.x, end.y);
    ctx.stroke();
    if (ray.hit) {
      ctx.fillStyle = "rgba(140, 255, 210, 0.9)";
      ctx.beginPath();
      ctx.arc(end.x, end.y, 1.6, 0, Math.PI * 2);
      ctx.fill();
    }
  }

  // 90° rear body occlusion wedge (matches production / lidar_spec).
  const yaw = pose.yaw || 0;
  const blindWidthDeg = spec.blind_width_deg != null ? spec.blind_width_deg : 90;
  const blindCenterDeg = spec.blind_center_body_deg != null
    ? spec.blind_center_body_deg
    : 180;
  const blindHalf = (blindWidthDeg * Math.PI) / 360;
  const rear = yaw + (blindCenterDeg * Math.PI) / 180;
  const reach = 0.65;
  ctx.fillStyle = "rgba(255, 120, 90, 0.18)";
  ctx.strokeStyle = "rgba(255, 150, 120, 0.55)";
  ctx.lineWidth = 1;
  ctx.beginPath();
  ctx.moveTo(origin.x, origin.y);
  for (let a = rear - blindHalf; a <= rear + blindHalf + 1e-6; a += 0.035) {
    const p = t.point(pose.x + Math.cos(a) * reach, pose.y + Math.sin(a) * reach);
    ctx.lineTo(p.x, p.y);
  }
  ctx.closePath();
  ctx.fill();
  ctx.stroke();
}

function drawGroundTruth() {
  if (!state) return;
  const width = worldCanvas.clientWidth;
  const height = worldCanvas.clientHeight;
  if (width < 2 || height < 2) return;
  const t = worldTransform(state, width, height);
  const ctx = worldCtx;
  ctx.clearRect(0, 0, width, height);
  ctx.fillStyle = "#071016";
  ctx.fillRect(0, 0, width, height);

  // Map bounds in camera space (zoom/pan aware).
  const bl = t.point(0, 0);
  const tr = t.point(state.scenario.width, state.scenario.height);
  const mapX = Math.min(bl.x, tr.x);
  const mapY = Math.min(bl.y, tr.y);
  const mapW = Math.abs(tr.x - bl.x);
  const mapH = Math.abs(tr.y - bl.y);
  ctx.fillStyle = "rgba(8,16,23,0.95)";
  ctx.strokeStyle = "rgba(130,200,225,0.35)";
  ctx.lineWidth = 2;
  ctx.fillRect(mapX, mapY, mapW, mapH);
  ctx.strokeRect(mapX, mapY, mapW, mapH);

  // Only draw walls near the camera to keep large saved maps snappy.
  const margin = 2.5;
  const viewMinX = Math.min(t.world(0, 0).x, t.world(width, height).x) - margin;
  const viewMaxX = Math.max(t.world(0, 0).x, t.world(width, height).x) + margin;
  const viewMinY = Math.min(t.world(0, 0).y, t.world(width, height).y) - margin;
  const viewMaxY = Math.max(t.world(0, 0).y, t.world(width, height).y) + margin;
  for (const o of state.obstacles || []) {
    if (o.x + o.width < viewMinX || o.x > viewMaxX || o.y + o.height < viewMinY || o.y > viewMaxY) {
      continue;
    }
    const p = t.point(o.x, o.y + o.height);
    const isDyn = o.kind === "dynamic";
    ctx.fillStyle = isDyn ? "rgba(255,108,80,0.42)" : "rgba(220,235,242,0.14)";
    ctx.strokeStyle = isDyn ? "rgba(255,130,100,0.95)" : "rgba(205,225,235,0.45)";
    ctx.lineWidth = isDyn ? 2 : 1;
    ctx.fillRect(p.x, p.y, o.width * t.scale, o.height * t.scale);
    ctx.strokeRect(p.x, p.y, o.width * t.scale, o.height * t.scale);
  }

  drawLidarBeams(ctx, t);

  if (state.path && state.path.length > 1) {
    ctx.strokeStyle = "rgba(75,200,255,0.9)";
    ctx.lineWidth = 3;
    ctx.setLineDash([7, 5]);
    ctx.beginPath();
    state.path.forEach((p, i) => {
      const s = t.point(p.x, p.y);
      if (i === 0) ctx.moveTo(s.x, s.y); else ctx.lineTo(s.x, s.y);
    });
    ctx.stroke();
    ctx.setLineDash([]);
  }

  if (state.goal) {
    let color = "rgba(255,211,92,1)";
    if (state.nav_complete) color = "rgba(120,220,255,1)";
    else if (state.goal_reachable === false) color = "rgba(255,120,100,1)";
    else if (state.goal_reachable === true) color = "rgba(115,255,186,1)";
    const gyaw = state.goal.yaw != null ? state.goal.yaw : state.goal_yaw;
    drawPoseMarker(
      ctx,
      t,
      { x: state.goal.x, y: state.goal.y, yaw: gyaw || 0 },
      color,
      state.nav_complete ? "ARRIVED" : (state.goal_reachable === false ? "UNREACHABLE" : null)
    );
  }
  if (goalDrag) {
    const yaw = goalDrag.yaw != null
      ? goalDrag.yaw
      : (state.pose
          ? Math.atan2(goalDrag.y - state.pose.y, goalDrag.x - state.pose.x)
          : 0);
    drawPoseMarker(
      ctx,
      t,
      { x: goalDrag.x, y: goalDrag.y, yaw },
      "rgba(255,211,92,0.75)",
      "GOAL"
    );
  }

  drawRover(ctx, t, state.estimated_pose, "#ffcf66", 0.55);
  drawRover(ctx, t, state.pose, "#73ffba", 1);
}

function worldToHeadingUp(wx, wy, pose, cx, cy, pxPerM) {
  const dx = wx - pose.x;
  const dy = wy - pose.y;
  const cos = Math.cos(pose.yaw || 0);
  const sin = Math.sin(pose.yaw || 0);
  const forward = dx * cos + dy * sin;
  const left = -dx * sin + dy * cos;
  return { sx: cx - left * pxPerM, sy: cy - forward * pxPerM };
}

function drawPerceived() {
  if (!state) return;
  const width = miniCanvas.clientWidth;
  const height = miniCanvas.clientHeight;
  if (width < 2 || height < 2) return;
  const ctx = miniCtx;
  ctx.clearRect(0, 0, width, height);
  ctx.fillStyle = "#050b10";
  ctx.fillRect(0, 0, width, height);

  const cx = width / 2;
  const cy = height / 2;
  const maxR = Math.min(width, height) * 0.46;
  const pxPerM = maxR / MINI_RANGE_M;
  const est = state.estimated_pose || state.pose;
  const mapPose = stabilizeMapPose(est) || est;
  const res = state.resolution || 0.1;

  ctx.strokeStyle = "rgba(255,255,255,0.08)";
  ctx.beginPath();
  ctx.arc(cx, cy, maxR, 0, Math.PI * 2);
  ctx.stroke();

  // Accumulated SLAM occupied cells — use stabilized pose so match chatter
  // does not make the map constantly shift in the ego view.
  ctx.fillStyle = "rgba(140, 180, 230, 0.92)";
  const occ = state.occupied || [];
  for (let i = 0; i + 1 < occ.length; i += 2) {
    const wx = (occ[i] + 0.5) * res;
    const wy = (occ[i + 1] + 0.5) * res;
    const p = worldToHeadingUp(wx, wy, mapPose, cx, cy, pxPerM);
    if (p.sx < -2 || p.sy < -2 || p.sx > width + 2 || p.sy > height + 2) continue;
    ctx.fillRect(p.sx - 0.5, p.sy - 0.5, 1, 1);
  }

  // Live lidar in body frame — shows obstacles the scan sees right now even when
  // the map is frozen (e.g. after Default Goal), which is why GT beams can hit a
  // wall that is still missing from the blue occupancy layer.
  const rays = state.lidar || [];
  for (const ray of rays) {
    if (ray.blind || ray.invalid || !ray.hit) continue;
    const bearing = Number(ray.relative_angle);
    if (!Number.isFinite(bearing)) continue;
    const dist = Number(ray.distance) || 0;
    if (dist <= 0.05 || dist > MINI_RANGE_M) continue;
    // Heading-up: forward = up, left = left (matches worldToHeadingUp body axes).
    const forward = Math.cos(bearing) * dist;
    const left = Math.sin(bearing) * dist;
    const sx = cx - left * pxPerM;
    const sy = cy - forward * pxPerM;
    ctx.fillStyle = "rgba(97, 255, 187, 0.95)";
    ctx.beginPath();
    ctx.arc(sx, sy, 2.0, 0, Math.PI * 2);
    ctx.fill();
  }

  if (state.path && state.path.length > 1) {
    ctx.strokeStyle = "rgba(75,200,255,0.85)";
    ctx.lineWidth = 2;
    ctx.setLineDash([4, 3]);
    ctx.beginPath();
    state.path.forEach((p, i) => {
      const s = worldToHeadingUp(p.x, p.y, mapPose, cx, cy, pxPerM);
      if (i === 0) ctx.moveTo(s.sx, s.sy); else ctx.lineTo(s.sx, s.sy);
    });
    ctx.stroke();
    ctx.setLineDash([]);
  }

  if (state.goal) {
    const g = worldToHeadingUp(state.goal.x, state.goal.y, mapPose, cx, cy, pxPerM);
    let color = "rgba(255,211,92,0.95)";
    if (state.nav_complete) color = "rgba(120,220,255,0.95)";
    else if (state.goal_reachable === false) color = "rgba(255,120,100,0.95)";
    else if (state.goal_reachable === true) color = "rgba(115,255,186,0.95)";
    ctx.strokeStyle = color;
    ctx.beginPath();
    ctx.arc(g.sx, g.sy, 5, 0, Math.PI * 2);
    ctx.stroke();
  }

  const half = 0.175 * pxPerM;
  ctx.strokeStyle = "rgba(125, 255, 179, 0.95)";
  ctx.lineWidth = 1.5;
  ctx.strokeRect(cx - half, cy - half, half * 2, half * 2);
  ctx.beginPath();
  ctx.moveTo(cx, cy);
  ctx.lineTo(cx, cy - half - 4);
  ctx.stroke();
}

function draw() {
  if (!state) return;
  try {
    drawGroundTruth();
  } catch (err) {
    console.warn("ground-truth draw failed", err);
  }
  try {
    drawPerceived();
  } catch (err) {
    console.warn("perceived draw failed", err);
  }
}

async function api(path, body) {
  const opts = body == null ? {} : {
    method: "POST",
    headers: {"Content-Type": "application/json"},
    body: JSON.stringify(body),
  };
  const res = await fetch(path, opts);
  return res.json();
}

function renderMetrics(m) {
  const entries = [
    ["pose error", (m.localization_error_m || 0).toFixed(3) + "m"],
    ["map known", (m.known_percent || 0).toFixed(0) + "%"],
    ["free recall", ((m.free_recall || 0) * 100).toFixed(0) + "%"],
    ["occ IoU", ((m.occupied_iou || 0) * 100).toFixed(0) + "%"],
    ["frontiers", String(m.frontiers ?? 0)],
    ["distance", (m.distance_m || 0).toFixed(2) + "m"],
  ];
  document.getElementById("metrics").innerHTML = entries.map(([k, v]) =>
    `<div class="metric"><span>${k}</span><strong>${v}</strong></div>`
  ).join("");
}

function renderNavBanner(s) {
  const el = document.getElementById("navBanner");
  if (!el) return;
  el.className = "nav-banner";
  if (s.nav_complete) {
    el.textContent = "Navigation complete — goal reached";
    el.classList.add("show", "complete");
  } else if (s.goal && s.goal_reachable === false) {
    el.textContent = "Goal unreachable — no clear path in the map";
    el.classList.add("show", "unreachable");
  } else if (s.goal && s.goal_reachable === true && (s.autopilot || s.cosim)) {
    el.textContent = s.cosim ? "Nav2 navigating (co-sim)" : "Goal reachable — navigating";
    el.classList.add("show", "reachable");
  } else if (s.goal && s.goal_reachable === true) {
    el.textContent = "Goal reachable — path ready";
    el.classList.add("show", "reachable");
  } else {
    el.textContent = "";
  }
}

function stabilizeMapPose(est) {
  // Occupancy is drawn in the estimate frame; raw scan-match chatter made the
  // whole blue map swim. Heavy EMA (snap only on large jumps / kidnap).
  if (!est) return null;
  if (!mapViewPose) {
    mapViewPose = { x: est.x, y: est.y, yaw: est.yaw || 0 };
    return mapViewPose;
  }
  const dx = est.x - mapViewPose.x;
  const dy = est.y - mapViewPose.y;
  const jump = Math.hypot(dx, dy);
  let dyaw = (est.yaw || 0) - mapViewPose.yaw;
  while (dyaw > Math.PI) dyaw -= Math.PI * 2;
  while (dyaw < -Math.PI) dyaw += Math.PI * 2;
  const a = jump > 0.55 || Math.abs(dyaw) > 0.75 ? 1.0 : 0.12;
  mapViewPose = {
    x: mapViewPose.x + a * dx,
    y: mapViewPose.y + a * dy,
    yaw: mapViewPose.yaw + a * dyaw,
  };
  return mapViewPose;
}

function applyState(next) {
  if (!next) return;
  if (!state || next.scenario.id !== state.scenario.id) {
    mapViewPose = null;
    resetCameraForScenario(next);
  }
  state = next;
  const statusEl = document.getElementById("status");
  const drive = state.drive || {};
  const body = drive.body || {};
  const stick = drive.stick || {};
  let driveTxt = "·";
  if (state.cosim) {
    // Continuous Nav2 path: show body Twist (and Pi stick), never fake WASD.
    const vx = Number(body.linear);
    const wz = Number(body.angular);
    const sx = Number(stick.x);
    const sy = Number(stick.y);
    const twistOk = Number.isFinite(vx) || Number.isFinite(wz);
    const stickOk = Number.isFinite(sx) || Number.isFinite(sy);
    if (twistOk || stickOk) {
      driveTxt =
        `cmd(${(vx || 0).toFixed(2)} m/s, ${(wz || 0).toFixed(2)} rad/s)` +
        (stickOk ? ` · stick(${(sx || 0).toFixed(2)}, ${(sy || 0).toFixed(2)})` : "");
    }
  } else {
    const keyTxt = (drive.keys && drive.keys.length) ? drive.keys.join("").toUpperCase() : "·";
    const stickTxt = (stick.x != null)
      ? ` stick(${Number(stick.x).toFixed(2)},${Number(stick.y).toFixed(2)})`
      : "";
    driveTxt = `${keyTxt}${stickTxt}`;
  }
  const phaseTxt = drive.phase && drive.phase !== "idle" ? ` · ${drive.phase}` : "";
  statusEl.textContent = `${state.mode} · ${state.status} · ${driveTxt}${phaseTxt}`;
  statusEl.className = "status";
  if (state.nav_complete) statusEl.classList.add("is-ok");
  else if (state.goal_reachable === false) statusEl.classList.add("is-bad");
  else if (state.autopilot) statusEl.classList.add("is-busy");
  renderNavBanner(state);
  document.getElementById("description").textContent = state.scenario.description || "";
  document.getElementById("speed").value = state.speed_multiplier;
  document.getElementById("speedLabel").textContent =
    Number(state.speed_multiplier).toFixed(2) + "×";
  const select = document.getElementById("scenario");
  if (!select.options.length) {
    for (const s of state.scenarios) {
      const opt = document.createElement("option");
      opt.value = s.id;
      opt.textContent = s.label;
      select.appendChild(opt);
    }
  }
  select.value = state.scenario.id;
  renderMetrics(state.metrics || {});
  draw();
}

async function refresh() {
  applyState(await api("/api/state"));
}

async function tick() {
  // Keep stepping while autopilot/explore is active even if Pause is on
  // (otherwise a reachable path is drawn but the rover never drives).
  // try/finally so a single failed fetch cannot kill the loop forever.
  try {
    const auto = state && (state.autopilot || state.exploring);
    const cosim = !!(state && state.cosim);
    if (cosim) {
      // Plant owns physics; always refresh so the rover animates under Nav2.
      applyState(await api("/api/state"));
    } else if (running || auto) {
      let linear = 0, angular = 0;
      // Manual keys only while not auto-driving — WASD would cancel autopilot.
      if (!auto) {
        const left = keys.has("a") || keys.has("arrowleft");
        const right = keys.has("d") || keys.has("arrowright");
        const forward = keys.has("w") || keys.has("arrowup");
        const back = keys.has("s") || keys.has("arrowdown");
        if (forward || back || left || right) {
          // Holding a drive key always unpauses — otherwise WASD looks "broken".
          running = true;
          const runBtn = document.querySelector('button[data-cmd="toggle_run"]');
          if (runBtn) runBtn.textContent = "Run / Pause";
        }
        if (forward) linear = 0.55;
        if (back) linear = -0.4;
        // Pure A/D → strong yaw (align gate drops W). Arc WA/WD → mild yaw
        // so cmd_vel_to_keys keeps both W and A/D (tank pivot turn).
        if (left) angular = (forward || back) ? 0.35 : 1.25;
        if (right) angular = (forward || back) ? -0.35 : -1.25;
      }
      applyState(await api("/api/tick", {linear, angular, dt: 1 / 30}));
    } else if (state && (state.autopilot || state.exploring || state.goal)) {
      // Paused UI still refreshes so banners/path stay live; server may autostep.
      applyState(await api("/api/state"));
    }
  } catch (err) {
    console.warn("sim tick failed", err);
  }
  setTimeout(tick, 33);
}

document.querySelectorAll("button[data-cmd]").forEach((btn) => {
  btn.addEventListener("click", async () => {
    const cmd = btn.dataset.cmd;
    if (cmd === "toggle_run") {
      running = !running;
      btn.textContent = running ? "Run / Pause" : "Paused — click to run";
      btn.blur();
      return;
    }
    const next = await api("/api/command", {cmd});
    applyState(next);
    // Auto-resume when a navigable goal is armed so path-following isn't stuck paused.
    if (next && next.autopilot) running = true;
    btn.blur();
  });
});

document.getElementById("scenario").addEventListener("change", async (e) => {
  applyState(await api("/api/command", {cmd: "reset", scenario: e.target.value}));
  e.target.blur();
});
document.getElementById("speed").addEventListener("input", async (e) => {
  applyState(await api("/api/command", {cmd: "speed", value: Number(e.target.value)}));
});
document.getElementById("speed").addEventListener("change", (e) => e.target.blur());


document.getElementById("follow").addEventListener("change", (e) => {
  cam.follow = !!e.target.checked;
  if (cam.follow && state && state.pose) {
    cam.cx = state.pose.x;
    cam.cy = state.pose.y;
  }
  draw();
});

worldCanvas.addEventListener("wheel", (e) => {
  if (!state) return;
  e.preventDefault();
  const rect = worldCanvas.getBoundingClientRect();
  const t = worldTransform(state, worldCanvas.clientWidth, worldCanvas.clientHeight);
  const before = t.world(e.clientX - rect.left, e.clientY - rect.top);
  const factor = e.deltaY < 0 ? 1.15 : 1 / 1.15;
  cam.zoom = Math.min(40, Math.max(0.6, cam.zoom * factor));
  cam.follow = false;
  const followEl = document.getElementById("follow");
  if (followEl) followEl.checked = false;
  const t2 = worldTransform(state, worldCanvas.clientWidth, worldCanvas.clientHeight);
  const after = t2.world(e.clientX - rect.left, e.clientY - rect.top);
  cam.cx += before.x - after.x;
  cam.cy += before.y - after.y;
  draw();
}, { passive: false });

worldCanvas.addEventListener("dblclick", (e) => {
  e.preventDefault();
  cam.follow = true;
  const followEl = document.getElementById("follow");
  if (followEl) followEl.checked = true;
  if (state && state.pose) {
    cam.cx = state.pose.x;
    cam.cy = state.pose.y;
  }
  draw();
});

worldCanvas.setAttribute("tabindex", "0");
worldCanvas.style.outline = "none";

worldCanvas.addEventListener("pointerdown", async (e) => {
  worldCanvas.focus();
  if (!state) return;
  // Right / middle button pans the ground-truth map.
  if (e.button === 2 || e.button === 1) {
    panDrag = { x: e.clientX, y: e.clientY, cx: cam.cx, cy: cam.cy };
    cam.follow = false;
    const followEl = document.getElementById("follow");
    if (followEl) followEl.checked = false;
    worldCanvas.setPointerCapture(e.pointerId);
    e.preventDefault();
    return;
  }
  const rect = worldCanvas.getBoundingClientRect();
  const t = worldTransform(state, worldCanvas.clientWidth, worldCanvas.clientHeight);
  const world = t.world(e.clientX - rect.left, e.clientY - rect.top);
  const hit = (state.props || []).find((p) =>
    world.x >= p.x && world.x <= p.x + p.width &&
    world.y >= p.y && world.y <= p.y + p.height
  );
  if (hit) {
    drag = { id: hit.id };
    worldCanvas.setPointerCapture(e.pointerId);
    e.preventDefault();
    return;
  }
  const kidnapMode = document.getElementById("kidnap").checked || e.shiftKey;
  if (kidnapMode) {
    applyState(await api("/api/command", {cmd: "kidnap", x: world.x, y: world.y}));
    document.getElementById("kidnap").checked = false;
    return;
  }
  // Click-drag places a PoseStamped goal: down = xy, drag = yaw.
  goalDrag = { x: world.x, y: world.y, yaw: null };
  worldCanvas.setPointerCapture(e.pointerId);
  e.preventDefault();
  draw();
});
worldCanvas.addEventListener("pointermove", async (e) => {
  if (panDrag && state) {
    const t = worldTransform(state, worldCanvas.clientWidth, worldCanvas.clientHeight);
    cam.cx = panDrag.cx - (e.clientX - panDrag.x) / t.scale;
    cam.cy = panDrag.cy + (e.clientY - panDrag.y) / t.scale;
    draw();
    return;
  }
  if (goalDrag && state) {
    const rect = worldCanvas.getBoundingClientRect();
    const t = worldTransform(state, worldCanvas.clientWidth, worldCanvas.clientHeight);
    const world = t.world(e.clientX - rect.left, e.clientY - rect.top);
    const dx = world.x - goalDrag.x;
    const dy = world.y - goalDrag.y;
    if (Math.hypot(dx, dy) > 0.12) {
      goalDrag.yaw = Math.atan2(dy, dx);
    }
    draw();
    return;
  }
  if (!drag || !state) return;
  const rect = worldCanvas.getBoundingClientRect();
  const t = worldTransform(state, worldCanvas.clientWidth, worldCanvas.clientHeight);
  const world = t.world(e.clientX - rect.left, e.clientY - rect.top);
  applyState(await api("/api/command", {
    cmd: "move_prop", id: drag.id, x: world.x, y: world.y,
  }));
});
worldCanvas.addEventListener("pointerup", async (e) => {
  if (goalDrag) {
    const payload = { cmd: "goal", x: goalDrag.x, y: goalDrag.y };
    if (goalDrag.yaw != null) payload.yaw = goalDrag.yaw;
    goalDrag = null;
    const next = await api("/api/command", payload);
    applyState(next);
    if (next && (next.autopilot || next.cosim)) {
      running = true;
      const runBtn = document.querySelector('button[data-cmd="toggle_run"]');
      if (runBtn) runBtn.textContent = "Run / Pause";
    }
  }
  drag = null;
  panDrag = null;
});
worldCanvas.addEventListener("pointercancel", () => {
  drag = null;
  panDrag = null;
  goalDrag = null;
});
worldCanvas.addEventListener("contextmenu", (e) => e.preventDefault());

function isTypingTarget(el) {
  if (!el) return false;
  const tag = (el.tagName || "").toLowerCase();
  return tag === "input" || tag === "select" || tag === "textarea" || el.isContentEditable;
}

window.addEventListener("keydown", (e) => {
  const key = e.key.toLowerCase();
  keys.add(key);
  if (isTypingTarget(e.target)) return;
  if (["w", "a", "s", "d", "arrowup", "arrowdown", "arrowleft", "arrowright"].includes(key)) {
    e.preventDefault();
  }
});
window.addEventListener("keyup", (e) => {
  keys.delete(e.key.toLowerCase());
});

window.addEventListener("blur", () => keys.clear());

resize();
refresh().then(tick);
</script>
</body>
</html>
"""


class SimulatorServer:
    def __init__(self, host: str = "127.0.0.1", port: int = 8877) -> None:
        self.host = host
        self.port = port
        # Noise off by default: painting from a noisy estimate walked walls and
        # made the perceived map constantly shift. Toggle via API if needed.
        self.sim = SlamNavSimulation(noise_enabled=False)
        self.lock = threading.Lock()
        # If the browser tick loop stalls, autonomy must still advance.
        self._last_client_tick = 0.0
        self._stop = threading.Event()
        self._autostep_thread: threading.Thread | None = None

    def _handle_command(self, payload: dict) -> dict:
        cmd = payload.get("cmd")
        if cmd == "reset":
            self.sim.reset(payload.get("scenario") or self.sim.scenario.id)
        elif cmd == "step":
            self.sim.step(1 / 15)
        elif cmd == "build_map":
            # One-click fixture map, then freeze so pose uses map-match (not mapping drift).
            self.sim.stop_auto_map(freeze=False)
            self.sim.reveal_map()
            self.sim.freeze_map()
            self.sim.status = "map built · frozen"
        elif cmd == "auto_map":
            self.sim.start_auto_map()
        elif cmd == "default_goal":
            self.sim.set_default_goal()
        elif cmd == "stop":
            self.sim.emergency_stop()
        elif cmd == "move_prop":
            self.sim.move_prop(
                str(payload.get("id") or ""),
                float(payload.get("x") or 0.0),
                float(payload.get("y") or 0.0),
            )
        elif cmd == "speed":
            self.sim.speed_multiplier = float(payload.get("value") or 1.0)
        elif cmd == "goal":
            self.sim.set_goal(
                float(payload["x"]),
                float(payload["y"]),
                None if payload.get("yaw") is None else float(payload["yaw"]),
                fine_docking=bool(payload.get("fine_docking")),
            )
        elif cmd == "kidnap":
            yaw = payload.get("yaw")
            self.sim.kidnap_rover(
                float(payload["x"]),
                float(payload["y"]),
                None if yaw is None else float(yaw),
                keep_estimate=True,
            )
        return self.sim.snapshot()

    def make_handler(self):
        server = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, format: str, *args) -> None:  # noqa: A003
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
                path = urllib.parse.urlparse(self.path).path
                if path in {"/", "/index.html"}:
                    self._send(200, HTML_PAGE.encode("utf-8"), "text/html; charset=utf-8")
                    return
                if path == "/api/state":
                    with server.lock:
                        payload = server.sim.snapshot()
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
                with server.lock:
                    if path == "/api/tick":
                        server._last_client_tick = time.monotonic()
                        server.sim.set_manual(
                            float(payload.get("linear") or 0.0),
                            float(payload.get("angular") or 0.0),
                        )
                        server.sim.step(float(payload.get("dt") or 1 / 30))
                        body = server.sim.snapshot()
                    elif path == "/api/command":
                        body = server._handle_command(payload)
                    elif path == "/api/regressions":
                        body = run_regressions()
                    else:
                        self._send(404, b"not found", "text/plain")
                        return
                self._send(
                    200,
                    json.dumps(body).encode("utf-8"),
                    "application/json",
                )

        return Handler

    def maybe_autostep(self, now: float | None = None, dt: float = 1.0 / 30.0) -> bool:
        """Step once if autonomy is active and the browser tick is stale.

        Returns True when a server-side step ran. Extracted for unit tests so we
        can lock the "reachable but frozen" regression without spinning threads.
        """
        now = time.monotonic() if now is None else now
        with self.lock:
            auto = self.sim.autopilot or self.sim.exploring
            if not auto:
                return False
            # Client is healthy — let it own the clock so we don't double-step.
            if now - self._last_client_tick < 0.20:
                return False
            self.sim.set_manual(0.0, 0.0)
            self.sim.step(dt)
            return True

    def _autostep_loop(self) -> None:
        """Advance nav/explore if the browser tick loop stalls (hung fetch, bg tab)."""
        dt = 1.0 / 30.0
        while not self._stop.wait(dt):
            self.maybe_autostep(dt=dt)

    def serve(self, open_browser: bool = True) -> None:
        httpd = ThreadingHTTPServer((self.host, self.port), self.make_handler())
        url = f"http://{self.host}:{self.port}/"
        print(f"SLAM/Nav internal simulator: {url}")
        print("Scenarios:", ", ".join(SCENARIOS))
        print("Ctrl+C to stop.")
        self._stop.clear()
        self._autostep_thread = threading.Thread(
            target=self._autostep_loop, name="sim-autostep", daemon=True
        )
        self._autostep_thread.start()
        if open_browser:
            threading.Timer(0.4, lambda: webbrowser.open(url)).start()
        try:
            httpd.serve_forever()
        except KeyboardInterrupt:
            print("\nStopped.")
        finally:
            self._stop.set()
            httpd.server_close()


def run_gui(host: str = "127.0.0.1", port: int = 8877, open_browser: bool = True) -> None:
    SimulatorServer(host=host, port=port).serve(open_browser=open_browser)
