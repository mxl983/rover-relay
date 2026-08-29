#!/usr/bin/env python3
"""Browser GUI: pose-continuity scenarios on real floorplans.

Stdlib only. Opens http://127.0.0.1:8771/

  python3 ros2-slam/visualize_pose_continuity.py
"""

from __future__ import annotations

import json
import math
import os
import sys
import threading
import webbrowser
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from continuity_maps import MAPS, map_to_json  # noqa: E402
from pose_continuity import (  # noqa: E402
    PoseCandidate,
    accept_pose_jump,
    continuity_adjusted_score,
    select_continuous_match,
    select_nearest_strong_match,
)

HOST = os.environ.get("POSE_VIZ_HOST", "127.0.0.1")
PORT = int(os.environ.get("POSE_VIZ_PORT", "8771"))


@dataclass
class Scenario:
    id: str
    title: str
    description: str
    map_id: str
    last_label: str
    candidates: list[dict[str, Any]]
    expected_pick: str | None
    jump_checks: list[dict[str, Any]]
    tags: list[str]
    policy: str = "nearest"  # nearest | continuous


def _scenarios() -> list[Scenario]:
    return [
        Scenario(
            id="office_meeting_lookalikes",
            title="Office — identical meeting rooms",
            description=(
                "Meet A and Meet B have the same furniture layout, so lidar scores "
                "tie (0.95). Rover was last in the hall by door_b. Continuity must "
                "pick Meet B (nearest strong lookalike), not Meet A across the building."
            ),
            map_id="office",
            last_label="door_b",
            candidates=[
                {"label": "meet_a", "score": 0.95},
                {"label": "meet_b", "score": 0.95},
                {"label": "lobby", "score": 0.40},
            ],
            expected_pick="meet_b",
            jump_checks=[
                {
                    "to": "meet_a",
                    "expect_accept": False,
                    "score_near": 0.90,
                    "score_far": 0.95,
                }
            ],
            tags=["office", "lookalike"],
            policy="nearest",
        ),
        Scenario(
            id="office_stay_in_hall",
            title="Office — stay in hall mid",
            description=(
                "All of hall_west / hall_mid / hall_east score well. Last pose was "
                "hall_mid → stay there (no jump to the ends)."
            ),
            map_id="office",
            last_label="hall_mid",
            candidates=[
                {"label": "hall_west", "score": 0.92},
                {"label": "hall_mid", "score": 0.93},
                {"label": "hall_east", "score": 0.92},
            ],
            expected_pick="hall_mid",
            jump_checks=[],
            tags=["office"],
            policy="nearest",
        ),
        Scenario(
            id="warehouse_aisle_teleport_trap",
            title="Warehouse — identical aisles",
            description=(
                "Three rack aisles look the same. Rover was at aisle2_mid. Strong "
                "matches also appear at aisle1_mid and aisle3_mid. Pick aisle2_mid "
                "(nearest), do not snap to a parallel aisle."
            ),
            map_id="warehouse",
            last_label="aisle2_mid",
            candidates=[
                {"label": "aisle1_mid", "score": 0.94},
                {"label": "aisle2_mid", "score": 0.94},
                {"label": "aisle3_mid", "score": 0.94},
            ],
            expected_pick="aisle2_mid",
            jump_checks=[
                {
                    "to": "aisle1_mid",
                    "expect_accept": False,
                    "score_near": 0.94,
                    "score_far": 0.94,
                },
                {
                    "to": "aisle3_mid",
                    "expect_accept": False,
                    "score_near": 0.94,
                    "score_far": 0.94,
                },
            ],
            tags=["warehouse", "lookalike"],
            policy="nearest",
        ),
        Scenario(
            id="warehouse_temp_pallet",
            title="Warehouse — temp pallet blocks scan",
            description=(
                "Continuous op, no kidnap. A pallet/person drops the score at "
                "aisle2_mid (0.95→0.70). Clean lookalike aisle3_mid still scores "
                "0.95. Must stay in aisle 2."
            ),
            map_id="warehouse",
            last_label="aisle2_mid",
            candidates=[
                {"label": "aisle2_mid", "score": 0.70},
                {"label": "aisle1_mid", "score": 0.95},
                {"label": "aisle3_mid", "score": 0.95},
            ],
            expected_pick="aisle2_mid",
            jump_checks=[
                {
                    "to": "aisle3_mid",
                    "expect_accept": False,
                    "score_near": 0.70,
                    "score_far": 0.95,
                }
            ],
            tags=["warehouse", "temp-object", "continuity"],
            policy="continuous",
        ),
        Scenario(
            id="apartment_temp_chair",
            title="Apartment — chair moved in living room",
            description=(
                "Someone dragged a chair; living-room match drops to 0.68 but is "
                "still healthy. Kitchen/bedroom look different enough, but a weak "
                "false peak at entry must not yank the pose across the L."
            ),
            map_id="apartment",
            last_label="living",
            candidates=[
                {"label": "living", "score": 0.68},
                {"label": "kitchen", "score": 0.55},
                {"label": "bedroom", "score": 0.50},
                {"label": "entry", "score": 0.88},
            ],
            expected_pick="living",
            jump_checks=[
                {
                    "to": "entry",
                    "expect_accept": False,
                    "score_near": 0.68,
                    "score_far": 0.88,
                }
            ],
            tags=["apartment", "temp-object", "continuity"],
            policy="continuous",
        ),
        Scenario(
            id="apartment_walk_to_kitchen",
            title="Apartment — walk living → kitchen",
            description=(
                "Normal motion: last in living, best local score is now kitchen "
                "(within continuity radius). Accept the step; do not jump to bedroom."
            ),
            map_id="apartment",
            last_label="living",
            candidates=[
                # Left the living room — local match collapsed; kitchen is next.
                {"label": "living", "score": 0.40},
                {"label": "kitchen", "score": 0.91},
                {"label": "bedroom", "score": 0.40},
            ],
            expected_pick="kitchen",
            jump_checks=[
                {
                    "to": "kitchen",
                    "expect_accept": True,
                    "score_near": 0.40,
                    "score_far": 0.91,
                }
            ],
            tags=["apartment", "motion"],
            policy="continuous",
        ),
        Scenario(
            id="yard_car_lookalikes",
            title="Yard — two similar parked cars",
            description=(
                "Beside car A and beside car B produce nearly identical scans. "
                "Last pose was beside_car_a → pick that, not the lookalike north bay."
            ),
            map_id="yard",
            last_label="beside_car_a",
            candidates=[
                {"label": "beside_car_a", "score": 0.93},
                {"label": "beside_car_b", "score": 0.93},
                {"label": "shed", "score": 0.35},
            ],
            expected_pick="beside_car_a",
            jump_checks=[
                {
                    "to": "beside_car_b",
                    "expect_accept": False,
                    "score_near": 0.93,
                    "score_far": 0.93,
                }
            ],
            tags=["yard", "lookalike"],
            policy="nearest",
        ),
        Scenario(
            id="yard_genuine_kidnap",
            title="Yard — true reloc to shed",
            description=(
                "Local match collapsed (0.18) after the rover was carried; shed "
                "scores 0.91. Treat as kidnap and accept the far pose."
            ),
            map_id="yard",
            last_label="gate",
            candidates=[
                {"label": "gate", "score": 0.18},
                {"label": "shed", "score": 0.91},
                {"label": "bay_mid", "score": 0.25},
            ],
            expected_pick="shed",
            jump_checks=[
                {
                    "to": "shed",
                    "expect_accept": True,
                    "score_near": 0.18,
                    "score_far": 0.91,
                }
            ],
            tags=["yard", "kidnap"],
            policy="nearest",
        ),
    ]


def run_scenario(sc: Scenario) -> dict[str, Any]:
    plan = MAPS[sc.map_id]
    last = plan.poses[sc.last_label]
    cands = [
        PoseCandidate(
            x=plan.poses[c["label"]][0],
            y=plan.poses[c["label"]][1],
            score=float(c["score"]),
            label=c["label"],
        )
        for c in sc.candidates
    ]
    if sc.policy == "continuous" or "temp-object" in sc.tags:
        # Larger local radius for room-scale maps (meters).
        pick = select_continuous_match(
            last[0], last[1], cands, min_score=0.55, local_radius_m=3.0
        )
    else:
        pick = select_nearest_strong_match(last[0], last[1], cands, min_score=0.55)

    ranked = sorted(
        (
            {
                "label": c.label,
                "score": c.score,
                "adjusted": round(continuity_adjusted_score(c, last[0], last[1]), 4),
                "dist_m": round(math.hypot(c.x - last[0], c.y - last[1]), 3),
                "x": c.x,
                "y": c.y,
            }
            for c in cands
        ),
        key=lambda r: r["adjusted"],
        reverse=True,
    )

    jump_results = []
    for jc in sc.jump_checks:
        to = jc["to"]
        tx, ty = plan.poses[to]
        jump = float(
            jc.get("force_jump_m", math.hypot(tx - last[0], ty - last[1]))
        )
        accepted = accept_pose_jump(
            jump_m=jump,
            score_near=float(jc["score_near"]),
            score_far=float(jc["score_far"]),
            teleport_m=0.8,
            strong_score=0.55,
            margin=0.12,
            global_reloc=False,
        )
        expect = bool(jc["expect_accept"])
        jump_results.append(
            {
                "to": to,
                "jump_m": round(jump, 3),
                "score_near": jc["score_near"],
                "score_far": jc["score_far"],
                "accepted": accepted,
                "expect_accept": expect,
                "pass": accepted == expect,
            }
        )

    pick_label = pick.label if pick else None
    pick_ok = pick_label == sc.expected_pick
    jumps_ok = all(j["pass"] for j in jump_results) if jump_results else True
    return {
        "id": sc.id,
        "title": sc.title,
        "description": sc.description,
        "tags": sc.tags,
        "map": map_to_json(plan),
        "last_label": sc.last_label,
        "last_xy": {"x": last[0], "y": last[1]},
        "candidates": ranked,
        "expected_pick": sc.expected_pick,
        "picked": pick_label,
        "pick_pass": pick_ok,
        "jumps": jump_results,
        "jumps_pass": jumps_ok,
        "pass": pick_ok and jumps_ok,
        "policy": sc.policy,
    }


HTML = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8"/>
<title>Pose continuity — real maps</title>
<style>
  :root {
    --bg:#14161a; --panel:#1e2229; --ink:#e8eaed; --muted:#9aa0a6;
    --ok:#3dd68c; --bad:#f07178; --accent:#7aa2f7; --last:#ffcc66;
    --wall:#c5cad3; --obs:#4a5568;
  }
  *{box-sizing:border-box}
  body{
    margin:0;font-family:"IBM Plex Sans","Segoe UI",sans-serif;
    background:var(--bg);color:var(--ink);min-height:100vh;
    display:grid;grid-template-columns:340px 1fr;grid-template-rows:auto 1fr;
  }
  header{
    grid-column:1/-1;padding:12px 18px;border-bottom:1px solid #2c313a;
    display:flex;gap:10px;align-items:center;flex-wrap:wrap;
  }
  header h1{font-size:1.05rem;margin:0;font-weight:600}
  .meta{color:var(--muted);font-size:.85rem}
  button{
    background:var(--panel);color:var(--ink);border:1px solid #3a4150;
    border-radius:6px;padding:8px 12px;cursor:pointer;font:inherit
  }
  button:hover{border-color:var(--accent)}
  button.primary{background:#243356;border-color:var(--accent)}
  aside{border-right:1px solid #2c313a;overflow:auto;padding:10px;background:#101217}
  .case{
    padding:10px 12px;margin-bottom:6px;border-radius:8px;
    background:var(--panel);cursor:pointer;border:1px solid transparent
  }
  .case.active{border-color:var(--accent)}
  .case .t{font-size:.88rem;font-weight:560}
  .tag{
    display:inline-block;font-size:.68rem;padding:1px 6px;border-radius:999px;
    background:#2a303c;color:var(--muted);margin:4px 4px 0 0
  }
  .badge{float:right;font-size:.75rem;font-weight:600}
  .badge.ok{color:var(--ok)} .badge.bad{color:var(--bad)}
  main{display:grid;grid-template-rows:1fr auto;min-height:0}
  #canvas-wrap{
    margin:14px;background:#181b21;border-radius:12px;border:1px solid #2c313a;
    overflow:hidden;min-height:420px;position:relative
  }
  canvas{display:block;width:100%;height:100%;min-height:420px}
  #detail{
    padding:14px 18px 18px;border-top:1px solid #2c313a;background:#101217;
    max-height:40vh;overflow:auto
  }
  #detail h2{margin:0 0 6px;font-size:1.08rem}
  #detail p{color:var(--muted);margin:0 0 10px;line-height:1.45}
  table{border-collapse:collapse;width:100%;font-size:.84rem}
  th,td{text-align:left;padding:6px 8px;border-bottom:1px solid #2c313a}
  th{color:var(--muted);font-weight:500}
  .pass{color:var(--ok)} .fail{color:var(--bad)}
  .legend{display:flex;gap:14px;flex-wrap:wrap;font-size:.8rem;color:var(--muted);margin-bottom:8px}
  .sw{width:10px;height:10px;border-radius:50%;display:inline-block;margin-right:4px}
</style>
</head>
<body>
<header>
  <h1>Pose continuity on real floorplans</h1>
  <span class="meta" id="summary">loading…</span>
  <span style="flex:1"></span>
  <button id="btn-prev">← Prev</button>
  <button id="btn-next">Next →</button>
  <button class="primary" id="btn-run">Re-run all</button>
</header>
<aside id="list"></aside>
<main>
  <div id="canvas-wrap"><canvas id="cv"></canvas></div>
  <section id="detail"></section>
</main>
<script>
let scenarios = [];
let idx = 0;

async function loadAll() {
  const r = await fetch('/api/scenarios');
  scenarios = await r.json();
  document.getElementById('summary').textContent =
    scenarios.filter(s => s.pass).length + '/' + scenarios.length + ' passed';
  renderList();
  show(idx);
}

function renderList() {
  const el = document.getElementById('list');
  el.innerHTML = scenarios.map((s, i) => `
    <div class="case ${i===idx?'active':''}" data-i="${i}">
      <span class="badge ${s.pass?'ok':'bad'}">${s.pass?'PASS':'FAIL'}</span>
      <div class="t">${s.title}</div>
      <div>${(s.tags||[]).map(t=>`<span class="tag">${t}</span>`).join('')}
        <span class="tag">${s.map.title}</span></div>
    </div>`).join('');
  el.querySelectorAll('.case').forEach(n => n.onclick = () => show(+n.dataset.i));
}

function show(i) {
  idx = (i + scenarios.length) % scenarios.length;
  renderList();
  const s = scenarios[idx];
  draw(s);
  const jumps = (s.jumps||[]).map(j => `
    <tr>
      <td>${s.last_label} → ${j.to}</td>
      <td>${j.jump_m} m</td>
      <td>${j.score_near} / ${j.score_far}</td>
      <td>${j.accepted ? 'accept' : 'reject'}</td>
      <td class="${j.pass?'pass':'fail'}">${j.pass?'ok':'expected '+(j.expect_accept?'accept':'reject')}</td>
    </tr>`).join('');
  document.getElementById('detail').innerHTML = `
    <h2>${s.title}</h2>
    <p>${s.description}</p>
    <div class="legend">
      <span><i class="sw" style="background:var(--last)"></i>last pose</span>
      <span><i class="sw" style="background:var(--ok)"></i>picked</span>
      <span><i class="sw" style="background:var(--accent)"></i>candidate</span>
      <span><i class="sw" style="background:var(--wall)"></i>walls</span>
      <span class="${s.pass?'pass':'fail'}">result: ${s.pass?'PASS':'FAIL'}
        (pick <b>${s.picked}</b> / expected <b>${s.expected_pick}</b>)</span>
    </div>
    <h3 style="margin:12px 0 6px;font-size:.95rem">Candidates</h3>
    <table>
      <tr><th>pose</th><th>raw</th><th>adjusted</th><th>dist</th></tr>
      ${s.candidates.map(c => `<tr>
        <td>${c.label}${c.label===s.picked?' ← pick':''}${c.label===s.last_label?' (last)':''}</td>
        <td>${c.score}</td><td>${c.adjusted}</td><td>${c.dist_m} m</td>
      </tr>`).join('')}
    </table>
    ${jumps ? `<h3 style="margin:14px 0 6px;font-size:.95rem">Teleport gate</h3>
    <table>
      <tr><th>jump</th><th>dist</th><th>near / far</th><th>decision</th><th>check</th></tr>
      ${jumps}
    </table>` : ''}
  `;
}

function bounds(map) {
  let minX=1e9,minY=1e9,maxX=-1e9,maxY=-1e9;
  const hit = (x,y)=>{minX=Math.min(minX,x);minY=Math.min(minY,y);maxX=Math.max(maxX,x);maxY=Math.max(maxY,y)};
  (map.walls||[]).forEach(poly => poly.forEach(([x,y]) => hit(x,y)));
  (map.obstacles||[]).forEach(([x0,y0,x1,y1]) => {hit(x0,y0);hit(x1,y1)});
  Object.values(map.poses||{}).forEach(p => hit(p.x,p.y));
  if (!isFinite(minX)) {minX=0;minY=0;maxX=10;maxY=10}
  return {minX,minY,maxX,maxY};
}

function draw(s) {
  const canvas = document.getElementById('cv');
  const wrap = document.getElementById('canvas-wrap');
  const dpr = window.devicePixelRatio || 1;
  const w = wrap.clientWidth, h = Math.max(420, wrap.clientHeight);
  canvas.width = w * dpr; canvas.height = h * dpr;
  const ctx = canvas.getContext('2d');
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  ctx.clearRect(0, 0, w, h);

  const map = s.map;
  const b = bounds(map);
  const pad = 36;
  const bw = b.maxX - b.minX || 1, bh = b.maxY - b.minY || 1;
  const scale = Math.min((w - 2*pad) / bw, (h - 2*pad) / bh);
  // Y up in map → canvas Y down
  const X = x => pad + (x - b.minX) * scale;
  const Y = y => h - pad - (y - b.minY) * scale;

  // floor fill
  ctx.fillStyle = '#1a1e26';
  ctx.fillRect(0,0,w,h);

  // obstacles
  ctx.fillStyle = '#3a4456';
  (map.obstacles||[]).forEach(([x0,y0,x1,y1]) => {
    ctx.fillRect(X(x0), Y(y1), X(x1)-X(x0), Y(y0)-Y(y1));
  });

  // walls
  ctx.strokeStyle = '#c5cad3';
  ctx.lineWidth = 3;
  ctx.lineJoin = 'round';
  (map.walls||[]).forEach(poly => {
    if (!poly.length) return;
    ctx.beginPath();
    poly.forEach(([x,y], i) => i ? ctx.lineTo(X(x), Y(y)) : ctx.moveTo(X(x), Y(y)));
    ctx.stroke();
  });

  // room labels
  ctx.fillStyle = '#6b7280';
  ctx.font = '600 12px IBM Plex Sans, sans-serif';
  ctx.textAlign = 'left';
  (map.labels||[]).forEach(l => ctx.fillText(l.text, X(l.x), Y(l.y)));

  // all named poses (faint)
  const candMap = Object.fromEntries(s.candidates.map(c => [c.label, c]));
  Object.entries(map.poses||{}).forEach(([lab, p]) => {
    if (lab in candMap || lab === s.last_label) return;
    ctx.beginPath();
    ctx.arc(X(p.x), Y(p.y), 3.5, 0, Math.PI*2);
    ctx.fillStyle = '#3a414f';
    ctx.fill();
  });

  // candidates
  s.candidates.forEach(c => {
    const isLast = c.label === s.last_label;
    const isPick = c.label === s.picked;
    let r = 8, fill = '#7aa2f7';
    if (isLast) { fill = '#ffcc66'; r = 10; }
    if (isPick) { fill = '#3dd68c'; r = 12; }
    ctx.beginPath();
    ctx.arc(X(c.x), Y(c.y), r, 0, Math.PI*2);
    ctx.fillStyle = fill;
    ctx.fill();
    ctx.fillStyle = '#e8eaed';
    ctx.font = '600 12px IBM Plex Sans, sans-serif';
    ctx.textAlign = 'center';
    ctx.fillText(c.label, X(c.x), Y(c.y) - 16);
    ctx.fillStyle = '#9aa0a6';
    ctx.font = '11px IBM Plex Sans, sans-serif';
    ctx.fillText(c.score.toFixed(2), X(c.x), Y(c.y) + 22);
  });

  // temp-object marker near last pose
  if ((s.tags||[]).includes('temp-object')) {
    const lx = X(s.last_xy.x), ly = Y(s.last_xy.y);
    ctx.fillStyle = '#f07178';
    ctx.fillRect(lx + 12, ly - 16, 12, 18);
    ctx.font = '10px sans-serif';
    ctx.textAlign = 'left';
    ctx.fillText('temp object', lx + 12, ly - 20);
  }

  // map title
  ctx.fillStyle = '#9aa0a6';
  ctx.font = '600 13px IBM Plex Sans, sans-serif';
  ctx.textAlign = 'left';
  ctx.fillText(map.title, 16, 22);
}

document.getElementById('btn-prev').onclick = () => show(idx - 1);
document.getElementById('btn-next').onclick = () => show(idx + 1);
document.getElementById('btn-run').onclick = async () => {
  document.getElementById('summary').textContent = 'running…';
  await fetch('/api/run', {method:'POST'});
  await loadAll();
};
window.addEventListener('resize', () => show(idx));
loadAll();
</script>
</body>
</html>
"""


class Handler(BaseHTTPRequestHandler):
    results: list[dict[str, Any]] = []

    def log_message(self, fmt: str, *args: Any) -> None:
        sys.stderr.write("pose-viz: " + (fmt % args) + "\n")

    def _json(self, code: int, payload: Any) -> None:
        body = json.dumps(payload).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _html(self, body: str) -> None:
        data = body.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self) -> None:  # noqa: N802
        path = urlparse(self.path).path
        if path in ("/", "/index.html"):
            self._html(HTML)
            return
        if path == "/api/scenarios":
            self._json(200, Handler.results)
            return
        self.send_error(404)

    def do_POST(self) -> None:  # noqa: N802
        path = urlparse(self.path).path
        if path == "/api/run":
            Handler.results = [run_scenario(s) for s in _scenarios()]
            self._json(200, {"ok": True, "n": len(Handler.results)})
            return
        self.send_error(404)


def main() -> int:
    Handler.results = [run_scenario(s) for s in _scenarios()]
    passed = sum(1 for r in Handler.results if r["pass"])
    print(f"pose-viz: {passed}/{len(Handler.results)} scenarios passed", flush=True)
    for r in Handler.results:
        mark = "PASS" if r["pass"] else "FAIL"
        print(
            f"  [{mark}] {r['id']}: pick={r['picked']} expected={r['expected_pick']}",
            flush=True,
        )

    httpd = ThreadingHTTPServer((HOST, PORT), Handler)
    url = f"http://{HOST}:{PORT}/"
    print(f"pose-viz: open {url}", flush=True)
    threading.Timer(0.4, lambda: webbrowser.open(url)).start()
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\npose-viz: stopped", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
