import { useEffect, useRef, useState } from "react";
import { getRelayHttpOrigin } from "../config";
import { compactDriveAssistSnapshot } from "../utils/driveAssistApi.js";
import { waypointCompactLabel, waypointDisplayLabel } from "../utils/waypointLabels.js";

const SLAM_EXPANDED_STORAGE_KEY = "slam_panel_expanded";

const DEFAULT_RANGE_M = 8;
const MIN_RANGE_M = 3;
const MAX_RANGE_M = 40;
const ZOOM_FACTOR = 1.25;
const MAX_DPR = 3;
/** Hit radius (px) when tapping a mark on the canvas. */
const WAYPOINT_HIT_PX = 14;
/** Physical rover footprint used for collision judgment on the map. */
export const ROVER_FOOTPRINT_M = 0.32;

function clampRange(rangeM) {
  return Math.max(MIN_RANGE_M, Math.min(MAX_RANGE_M, rangeM));
}

function prepareCanvasContext(canvas) {
  const ctx = canvas.getContext("2d");
  if (!ctx) return null;

  const dpr = Math.min(window.devicePixelRatio || 1, MAX_DPR);
  const cssW = canvas.clientWidth;
  const cssH = canvas.clientHeight;
  if (cssW <= 0 || cssH <= 0) return null;

  const pixelW = Math.round(cssW * dpr);
  const pixelH = Math.round(cssH * dpr);
  if (canvas.width !== pixelW || canvas.height !== pixelH) {
    canvas.width = pixelW;
    canvas.height = pixelH;
  }

  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  ctx.imageSmoothingEnabled = false;
  return { ctx, cssW, cssH };
}

function readPose(map) {
  // Always use the real TF pose — never the clamped `view` center. Using view
  // made marks appear to float whenever the robot neared/left the map edge.
  const pose = map?.pose || {};
  const x = Number(pose.x) || 0;
  const y = Number(pose.y) || 0;
  let yaw = Number(pose.yaw);
  if (!Number.isFinite(yaw) && Number.isFinite(Number(pose.theta_deg))) {
    yaw = (Number(pose.theta_deg) * Math.PI) / 180;
  }
  if (!Number.isFinite(yaw)) yaw = 0;
  return { x, y, yaw };
}

/**
 * Camera used for world→screen. When localization pose drifts outside the
 * occupancy grid, keep the frozen walls on-canvas by framing map contents.
 */
function readCameraPose(map) {
  const pose = readPose(map);
  if (!map || map.pose_in_map !== false) return pose;

  const res = Number(map.resolution) || 0.05;
  const ox = Number(map.origin?.x) || 0;
  const oy = Number(map.origin?.y) || 0;
  const occ = map.occupied;
  if (Array.isArray(occ) && occ.length >= 2) {
    let sx = 0;
    let sy = 0;
    let n = 0;
    for (let i = 0; i + 1 < occ.length; i += 2) {
      sx += ox + (Number(occ[i]) + 0.5) * res;
      sy += oy + (Number(occ[i + 1]) + 0.5) * res;
      n += 1;
    }
    if (n > 0) return { x: sx / n, y: sy / n, yaw: pose.yaw };
  }
  const w = Number(map.width) || 0;
  const h = Number(map.height) || 0;
  if (w > 0 && h > 0) {
    return { x: ox + (w * res) / 2, y: oy + (h * res) / 2, yaw: pose.yaw };
  }
  return pose;
}

function countVisibleCells(map) {
  if (!map) return 0;
  if (Array.isArray(map.occupied) && map.occupied.length) {
    return map.occupied_count ?? Math.floor(map.occupied.length / 2);
  }
  if (Array.isArray(map.map_points)) return map.map_points.length;
  return 0;
}

function listWaypoints(map) {
  return Array.isArray(map?.waypoints) ? map.waypoints : [];
}

/** Display forward = base_link +x = drive forward (laser is −90° vs base). */
function displayYawRad(poseYaw) {
  return poseYaw;
}

/**
 * Map-frame point → heading-up screen (robot at center, forward always up).
 * @returns {{ sx: number, sy: number }}
 */
function worldToHeadingUpScreen(wx, wy, pose, cx, cy, pxPerM) {
  const dx = wx - pose.x;
  const dy = wy - pose.y;
  const theta = displayYawRad(pose.yaw);
  const cos = Math.cos(theta);
  const sin = Math.sin(theta);
  const forward = dx * cos + dy * sin;
  const left = -dx * sin + dy * cos;
  return {
    sx: cx - left * pxPerM,
    sy: cy - forward * pxPerM,
  };
}

/** Heading-up screen point → map-frame point. */
function headingUpScreenToWorld(sx, sy, pose, cx, cy, pxPerM) {
  const left = (cx - sx) / pxPerM;
  const forward = (cy - sy) / pxPerM;
  const theta = displayYawRad(pose.yaw);
  const cos = Math.cos(theta);
  const sin = Math.sin(theta);
  const dx = forward * cos - left * sin;
  const dy = forward * sin + left * cos;
  return { x: pose.x + dx, y: pose.y + dy, yaw: pose.yaw };
}


/** Body-frame (forward, left) → screen. Heading-up: forward is always up. */
function bodyToScreen(forwardM, leftM, cx, cy, pxPerM) {
  return {
    sx: cx - leftM * pxPerM,
    sy: cy - forwardM * pxPerM,
  };
}

/** Draw rover footprint; (cx,cy) is base_link center — same frame as nav / dock. */
function drawRoverRectangle(ctx, cx, cy, pxPerM) {
  const half = ROVER_FOOTPRINT_M / 2;
  const corners = [
    bodyToScreen(half, half, cx, cy, pxPerM),
    bodyToScreen(half, -half, cx, cy, pxPerM),
    bodyToScreen(-half, -half, cx, cy, pxPerM),
    bodyToScreen(-half, half, cx, cy, pxPerM),
  ];
  ctx.fillStyle = "rgba(200, 220, 240, 0.18)";
  ctx.strokeStyle = "rgba(125, 255, 179, 0.75)";
  ctx.lineWidth = 1;
  ctx.beginPath();
  ctx.moveTo(corners[0].sx, corners[0].sy);
  for (let i = 1; i < corners.length; i += 1) {
    ctx.lineTo(corners[i].sx, corners[i].sy);
  }
  ctx.closePath();
  ctx.fill();
  ctx.stroke();

  // Heading wedge — forward (+x body) points up in heading-up view.
  const noseLen = half + 0.08;
  const wingSpan = 0.1;
  const wingBack = half * 0.15;
  const tip = bodyToScreen(noseLen, 0, cx, cy, pxPerM);
  const wingL = bodyToScreen(wingBack, wingSpan, cx, cy, pxPerM);
  const wingR = bodyToScreen(wingBack, -wingSpan, cx, cy, pxPerM);
  ctx.fillStyle = "rgba(125, 255, 179, 0.95)";
  ctx.strokeStyle = "rgba(255, 255, 255, 0.95)";
  ctx.lineWidth = 1.75;
  ctx.beginPath();
  ctx.moveTo(wingL.sx, wingL.sy);
  ctx.lineTo(tip.sx, tip.sy);
  ctx.lineTo(wingR.sx, wingR.sy);
  ctx.closePath();
  ctx.fill();
  ctx.stroke();

  // base_link origin — center used for goto, dock, and mark XY.
  ctx.fillStyle = "rgba(255, 255, 255, 0.98)";
  ctx.strokeStyle = "rgba(8, 20, 32, 0.9)";
  ctx.lineWidth = 1;
  ctx.beginPath();
  ctx.arc(cx, cy, 3, 0, Math.PI * 2);
  ctx.fill();
  ctx.stroke();
}

function drawRoverAtWorld(ctx, pose, camera, cx, cy, pxPerM, cssW, cssH) {
  const { sx, sy } = worldToHeadingUpScreen(pose.x, pose.y, camera, cx, cy, pxPerM);
  if (sx < -20 || sy < -20 || sx > cssW + 20 || sy > cssH + 20) return;
  drawRoverRectangle(ctx, sx, sy, pxPerM);
}

function drawWaypoints(ctx, map, pose, cx, cy, pxPerM, cssW, cssH, selectedId, goalId) {
  const waypoints = listWaypoints(map);
  if (!waypoints.length) return;

  for (const wp of waypoints) {
    const wx = Number(wp.x);
    const wy = Number(wp.y);
    if (!Number.isFinite(wx) || !Number.isFinite(wy)) continue;
    const { sx, sy } = worldToHeadingUpScreen(wx, wy, pose, cx, cy, pxPerM);
    if (sx < -8 || sy < -8 || sx > cssW + 8 || sy > cssH + 8) continue;

    const isSelected = selectedId && String(wp.id) === String(selectedId);
    const isGoal = goalId && String(wp.id) === String(goalId);
    const stroke = isGoal
      ? "rgba(125, 255, 179, 0.95)"
      : "rgba(255, 220, 120, 0.95)";
    const fill = isGoal
      ? "rgba(125, 255, 179, 0.95)"
      : "rgba(255, 196, 90, 0.95)";

    if (isSelected || isGoal) {
      ctx.strokeStyle = stroke;
      ctx.lineWidth = 1.5;
      ctx.beginPath();
      ctx.arc(sx, sy, 6, 0, Math.PI * 2);
      ctx.stroke();
    }

    ctx.fillStyle = fill;
    ctx.beginPath();
    ctx.arc(sx, sy, isSelected || isGoal ? 4 : 3, 0, Math.PI * 2);
    ctx.fill();

    // Pose chevron — marks store docking heading, not just XY.
    let wpYaw = Number(wp.yaw);
    if (!Number.isFinite(wpYaw) && Number.isFinite(Number(wp.theta_deg))) {
      wpYaw = (Number(wp.theta_deg) * Math.PI) / 180;
    }
    if (Number.isFinite(wpYaw)) {
      const delta = displayYawRad(wpYaw) - displayYawRad(pose.yaw);
      const len = isSelected || isGoal ? 14 : 11;
      const wing = isSelected || isGoal ? 5 : 4;
      const back = isSelected || isGoal ? 4 : 3;
      const tipX = sx - Math.sin(delta) * len;
      const tipY = sy - Math.cos(delta) * len;
      const wingLX = sx - Math.sin(delta) * back + Math.cos(delta) * wing;
      const wingLY = sy - Math.cos(delta) * back - Math.sin(delta) * wing;
      const wingRX = sx - Math.sin(delta) * back - Math.cos(delta) * wing;
      const wingRY = sy - Math.cos(delta) * back + Math.sin(delta) * wing;
      ctx.fillStyle = fill;
      ctx.strokeStyle = "rgba(255, 255, 255, 0.85)";
      ctx.lineWidth = isSelected || isGoal ? 1.75 : 1.25;
      ctx.beginPath();
      ctx.moveTo(wingLX, wingLY);
      ctx.lineTo(tipX, tipY);
      ctx.lineTo(wingRX, wingRY);
      ctx.closePath();
      ctx.fill();
      ctx.stroke();
    }

    // Mark XY is base_link center (same as nav goal).
    ctx.fillStyle = "rgba(255, 255, 255, 0.9)";
    ctx.beginPath();
    ctx.arc(sx, sy, isSelected || isGoal ? 2 : 1.5, 0, Math.PI * 2);
    ctx.fill();
  }
}

function drawNavPath(ctx, pose, cx, cy, pxPerM, cssW, cssH, points, style) {
  if (!Array.isArray(points) || points.length < 2) return;
  ctx.save();
  ctx.strokeStyle = style.stroke;
  ctx.lineWidth = style.width;
  ctx.lineJoin = "round";
  ctx.lineCap = "round";
  if (style.dash) ctx.setLineDash(style.dash);
  ctx.beginPath();
  let started = false;
  for (const pt of points) {
    if (!Array.isArray(pt) || pt.length < 2) continue;
    const wx = Number(pt[0]);
    const wy = Number(pt[1]);
    if (!Number.isFinite(wx) || !Number.isFinite(wy)) continue;
    const { sx, sy } = worldToHeadingUpScreen(wx, wy, pose, cx, cy, pxPerM);
    if (sx < -40 || sy < -40 || sx > cssW + 40 || sy > cssH + 40) {
      started = false;
      continue;
    }
    if (!started) {
      ctx.moveTo(sx, sy);
      started = true;
    } else {
      ctx.lineTo(sx, sy);
    }
  }
  ctx.stroke();
  ctx.restore();
}

/** Highlight the active drive segment (map-frame endpoints). */
function drawActiveSegment(ctx, pose, cx, cy, pxPerM, cssW, cssH, active) {
  if (!active) return;
  const x0 = Number(active.x0);
  const y0 = Number(active.y0);
  const x1 = Number(active.x1);
  const y1 = Number(active.y1);
  if (![x0, y0, x1, y1].every(Number.isFinite)) return;
  const a = worldToHeadingUpScreen(x0, y0, pose, cx, cy, pxPerM);
  const b = worldToHeadingUpScreen(x1, y1, pose, cx, cy, pxPerM);
  ctx.save();
  ctx.strokeStyle = "rgba(255, 220, 80, 0.95)";
  ctx.lineWidth = 4.5;
  ctx.lineCap = "round";
  ctx.setLineDash([]);
  ctx.beginPath();
  ctx.moveTo(a.sx, a.sy);
  ctx.lineTo(b.sx, b.sy);
  ctx.stroke();
  // Endpoint dots
  ctx.fillStyle = "rgba(255, 230, 120, 1)";
  for (const p of [a, b]) {
    ctx.beginPath();
    ctx.arc(p.sx, p.sy, 3.5, 0, Math.PI * 2);
    ctx.fill();
  }
  ctx.restore();
}

function drawCellLayer(ctx, map, layer, color, pose, cx, cy, pxPerM, cssW, cssH) {
  if (!layer) return;
  ctx.fillStyle = color;
  const plotWorld = (wx, wy) => {
    const { sx, sy } = worldToHeadingUpScreen(wx, wy, pose, cx, cy, pxPerM);
    if (sx < -2 || sy < -2 || sx > cssW + 2 || sy > cssH + 2) return;
    ctx.fillRect(sx - 1, sy - 1, 2, 2);
  };
  const occ = layer.occupied;
  if (Array.isArray(occ) && occ.length >= 2) {
    const res = layer.resolution || map?.resolution || 0.05;
    const ox = layer.origin?.x ?? map?.origin?.x ?? 0;
    const oy = layer.origin?.y ?? map?.origin?.y ?? 0;
    for (let i = 0; i + 1 < occ.length; i += 2) {
      plotWorld(ox + (occ[i] + 0.5) * res, oy + (occ[i + 1] + 0.5) * res);
    }
    return;
  }
  if (Array.isArray(layer.map_points)) {
    for (const pt of layer.map_points) {
      if (!pt) continue;
      const wx = Number(pt.x);
      const wy = Number(pt.y);
      if (!Number.isFinite(wx) || !Number.isFinite(wy)) continue;
      plotWorld(wx, wy);
    }
  }
}

function drawScanHits(ctx, hits, pose, cx, cy, pxPerM, cssW, cssH) {
  if (!Array.isArray(hits) || !hits.length) return;
  const dotR = Math.max(2.5, pxPerM * 0.045);
  ctx.fillStyle = "rgba(70, 255, 150, 0.98)";
  ctx.strokeStyle = "rgba(30, 180, 95, 0.9)";
  ctx.lineWidth = 1;
  for (const pt of hits) {
    const wx = Number(pt.x);
    const wy = Number(pt.y);
    if (!Number.isFinite(wx) || !Number.isFinite(wy)) continue;
    const { sx, sy } = worldToHeadingUpScreen(wx, wy, pose, cx, cy, pxPerM);
    if (sx < -dotR || sy < -dotR || sx > cssW + dotR || sy > cssH + dotR) continue;
    ctx.beginPath();
    ctx.arc(sx, sy, dotR, 0, Math.PI * 2);
    ctx.fill();
    ctx.stroke();
  }
}

/**
 * @param {CanvasRenderingContext2D} ctx
 * @param {number} cssW
 * @param {number} cssH
 * @param {import("../hooks/useSlamMap").SlamMap | null} map
 * @param {number} rangeM
 * @param {string | null} selectedId
 * @param {string | null} goalId
 * @param {{ global?: number[][], local?: number[][] } | null} navPath
 * @param {{ x0?: number, y0?: number, x1?: number, y1?: number } | null} [activeSegment]
 */
export function drawSlamMap(
  ctx,
  cssW,
  cssH,
  map,
  rangeM,
  selectedId,
  goalId,
  navPath,
  activeSegment = null,
) {
  ctx.clearRect(0, 0, cssW, cssH);
  const cx = cssW / 2;
  const cy = cssH / 2;
  const maxR = Math.min(cssW, cssH) * 0.46;
  const pxPerM = maxR / rangeM;

  if (!map) return;

  const pose = readPose(map);
  const camera = readCameraPose(map);
  const mapFrozen = map.mode === "localization";

  // Frozen baseline = blue; live mapping build = green walls.
  drawCellLayer(
    ctx,
    map,
    map,
    mapFrozen ? "rgba(140, 180, 230, 0.72)" : "rgba(125, 255, 179, 0.92)",
    camera,
    cx,
    cy,
    pxPerM,
    cssW,
    cssH,
  );

  // Ephemeral local corrections (chair moved, etc.).
  if (mapFrozen && map.overlay) {
    if (map.overlay.removed?.occupied_count > 0) {
      drawCellLayer(
        ctx,
        map,
        map.overlay.removed,
        "rgba(90, 110, 140, 0.55)",
        camera,
        cx,
        cy,
        pxPerM,
        cssW,
        cssH,
      );
    }
    if (map.overlay.added?.occupied_count > 0) {
      drawCellLayer(
        ctx,
        map,
        map.overlay.added,
        "rgba(255, 196, 90, 0.88)",
        camera,
        cx,
        cy,
        pxPerM,
        cssW,
        cssH,
      );
    }
  }

  // Live lidar sweep — green over frozen walls where the beam hits now.
  drawScanHits(ctx, map.scan_hits, camera, cx, cy, pxPerM, cssW, cssH);

  drawNavPath(ctx, camera, cx, cy, pxPerM, cssW, cssH, navPath?.global, {
    stroke: "rgba(90, 200, 255, 0.85)",
    width: 2,
    dash: [5, 4],
  });
  drawNavPath(ctx, camera, cx, cy, pxPerM, cssW, cssH, navPath?.local, {
    stroke: "rgba(255, 170, 70, 0.95)",
    width: 2.25,
    dash: null,
  });
  drawActiveSegment(ctx, camera, cx, cy, pxPerM, cssW, cssH, activeSegment);

  drawWaypoints(ctx, map, camera, cx, cy, pxPerM, cssW, cssH, selectedId, goalId);
  drawRoverAtWorld(ctx, pose, camera, cx, cy, pxPerM, cssW, cssH);
}

function hitTestWaypoint(map, rangeM, cssW, cssH, clientX, clientY, canvas) {
  const rect = canvas.getBoundingClientRect();
  const x = clientX - rect.left;
  const y = clientY - rect.top;
  const camera = readCameraPose(map);
  const cx = cssW / 2;
  const cy = cssH / 2;
  const maxR = Math.min(cssW, cssH) * 0.46;
  const pxPerM = maxR / rangeM;

  let best = null;
  let bestDist = WAYPOINT_HIT_PX;
  for (const wp of listWaypoints(map)) {
    const wx = Number(wp.x);
    const wy = Number(wp.y);
    if (!Number.isFinite(wx) || !Number.isFinite(wy)) continue;
    const { sx, sy } = worldToHeadingUpScreen(wx, wy, camera, cx, cy, pxPerM);
    const d = Math.hypot(sx - x, sy - y);
    if (d <= bestDist) {
      bestDist = d;
      best = wp;
    }
  }
  return best;
}

/**
 * @param {{
 *   map: import("../hooks/useSlamMap").SlamMap | null;
 *   isLive: boolean;
 *   error: string | null;
 * }} props
 */
export function SlamMap({
  map,
  isLive,
  error,
  driveAssistEnabled = false,
  driveAssistUpdate = null,
}) {
  const canvasRef = useRef(/** @type {HTMLCanvasElement | null} */ (null));
  const [rangeM, setRangeM] = useState(DEFAULT_RANGE_M);
  const [markBusy, setMarkBusy] = useState(false);
  const [navBusy, setNavBusy] = useState(false);
  const [flashMsg, setFlashMsg] = useState("");
  const [selectedId, setSelectedId] = useState(/** @type {string | null} */ (null));
  const [navPhase, setNavPhase] = useState("idle");
  const [navResult, setNavResult] = useState(/** @type {string | null} */ (null));
  const [activeGoalId, setActiveGoalId] = useState(/** @type {string | null} */ (null));
  const [activeGoalLabel, setActiveGoalLabel] = useState("");
  const [activeNavId, setActiveNavId] = useState(/** @type {string | null} */ (null));
  const [navIdCopied, setNavIdCopied] = useState(false);
  const [distRemaining, setDistRemaining] = useState(/** @type {number | null} */ (null));
  const [navFeedback, setNavFeedback] = useState(
    /** @type {Record<string, unknown> | null} */ (null),
  );
  const [navPath, setNavPath] = useState(
    /** @type {{ global: number[][], local: number[][] } | null} */ (null),
  );
  const [fineDocking, setFineDocking] = useState(() => {
    if (typeof window === "undefined") return false;
    return window.localStorage.getItem("slam_fine_docking") === "1";
  });
  const [arrivalFeedback, setArrivalFeedback] = useState(
    /** @type {{ position_error_m?: number, yaw_error_deg?: number } | null} */ (null),
  );
  const [expanded, setExpanded] = useState(() => {
    if (typeof window === "undefined") return false;
    return window.localStorage.getItem(SLAM_EXPANDED_STORAGE_KEY) === "1";
  });
  const [dockFeedback, setDockFeedback] = useState(
    /** @type {{ position_error_m?: number, yaw_error_deg?: number, fwd_m?: number, left_m?: number } | null} */ (
      null
    ),
  );
  const [navUi, setNavUi] = useState(
    /** @type {Record<string, unknown> | null} */ (null),
  );
  const [navDrive, setNavDrive] = useState(
    /** @type {{ drive?: { x?: number, y?: number }, cmd_vx?: number, cmd_wz?: number, phase?: string } | null} */ (
      null
    ),
  );

  useEffect(() => {
    if (typeof window !== "undefined") {
      window.localStorage.setItem("slam_fine_docking", fineDocking ? "1" : "0");
    }
  }, [fineDocking]);

  useEffect(() => {
    if (typeof window !== "undefined") {
      window.localStorage.setItem(SLAM_EXPANDED_STORAGE_KEY, expanded ? "1" : "0");
    }
  }, [expanded]);

  const waypoints = listWaypoints(map);
  const selected =
    waypoints.find((w) => String(w.id) === String(selectedId)) || null;
  const pose = map ? readPose(map) : null;
  const mapFrozen = map?.mode === "localization";
  const workingActive = Boolean(map?.working_active || map?.overlay?.occupied_count > 0);
  const displayRangeM = rangeM;

  useEffect(() => {
    if (!selectedId && waypoints.length) {
      setSelectedId(String(waypoints[waypoints.length - 1].id));
    } else if (
      selectedId &&
      waypoints.length &&
      !waypoints.some((w) => String(w.id) === String(selectedId))
    ) {
      setSelectedId(waypoints.length ? String(waypoints[waypoints.length - 1].id) : null);
    }
  }, [waypoints, selectedId]);

  useEffect(() => {
    if (!expanded) return undefined;
    let raf = 0;
    raf = requestAnimationFrame(() => {
      const canvas = canvasRef.current;
      if (!canvas) return;
      const prepared = prepareCanvasContext(canvas);
      if (!prepared) return;
      drawSlamMap(
        prepared.ctx,
        prepared.cssW,
        prepared.cssH,
        map,
        rangeM,
        selectedId,
        activeGoalId,
        navPath,
        navUi?.active_segment && typeof navUi.active_segment === "object"
          ? /** @type {{ x0?: number, y0?: number, x1?: number, y1?: number }} */ (
              navUi.active_segment
            )
          : null,
      );
    });
    return () => cancelAnimationFrame(raf);
  }, [expanded, map, rangeM, selectedId, activeGoalId, navPath, navUi]);

  useEffect(() => {
    if (!isLive) return undefined;
    let cancelled = false;
    const poll = async () => {
      try {
        const res = await fetch(`${getRelayHttpOrigin()}/api/navigation/status`, {
          cache: "no-store",
        });
        const json = await res.json().catch(() => ({}));
        if (cancelled || !json?.success) return;
        const goal = json.goal || {};
        const drive = json.drive || {};
        const status = String(goal.status || "idle");
        setNavPhase(status);
        setNavResult(goal.result != null ? String(goal.result) : null);
        setNavDrive({
          drive:
            drive.drive && typeof drive.drive === "object"
              ? {
                  x: Number(drive.drive.x) || 0,
                  y: Number(drive.drive.y) || 0,
                }
              : { x: 0, y: 0 },
          cmd_vx: Number(drive.cmd_vx) || 0,
          cmd_wz: Number(drive.cmd_wz) || 0,
          phase: String(drive.phase || "idle"),
        });
        setNavUi(drive.nav_ui && typeof drive.nav_ui === "object" ? drive.nav_ui : null);
        setNavFeedback(goal.feedback && typeof goal.feedback === "object" ? goal.feedback : null);
        const dist = Number(goal.feedback?.distance_remaining);
        setDistRemaining(Number.isFinite(dist) ? dist : null);
        if (goal.goal?.label) setActiveGoalLabel(String(goal.goal.label));
        if (goal.nav_id) setActiveNavId(String(goal.nav_id));
        if (status === "navigating" && goal.goal) {
          // Prefer matching waypoint by coords if id missing.
          const gx = Number(goal.goal.x);
          const gy = Number(goal.goal.y);
          const match = waypoints.find(
            (w) =>
              Math.abs(Number(w.x) - gx) < 0.05 && Math.abs(Number(w.y) - gy) < 0.05,
          );
          if (match?.id) setActiveGoalId(String(match.id));
        }
        if (status === "idle") {
          setActiveGoalId(null);
          setNavUi(null);
          setNavFeedback(null);
        }
        if (
          goal.feedback?.position_error_m != null ||
          goal.feedback?.yaw_error_deg != null ||
          goal.feedback?.fwd_m != null ||
          goal.feedback?.left_m != null
        ) {
          setDockFeedback({
            position_error_m: goal.feedback.position_error_m,
            yaw_error_deg: goal.feedback.yaw_error_deg,
            fwd_m: goal.feedback.fwd_m,
            left_m: goal.feedback.left_m,
            dock_phase: goal.feedback.dock_phase,
          });
        }
        if (status === "succeeded" && goal.feedback) {
          setArrivalFeedback({
            position_error_m: goal.feedback.position_error_m,
            yaw_error_deg: goal.feedback.yaw_error_deg,
          });
        }
        const path = json.path;
        if (path && (Array.isArray(path.global) || Array.isArray(path.local))) {
          setNavPath({
            global: Array.isArray(path.global) ? path.global : [],
            local: Array.isArray(path.local) ? path.local : [],
          });
        } else if (status === "idle") {
          setNavPath(null);
        }
      } catch {
        /* ignore */
      }
    };
    poll();
    const id = setInterval(poll, 500);
    return () => {
      cancelled = true;
      clearInterval(id);
    };
  }, [isLive, waypoints]);

  const statusClass = error ? "stale" : isLive ? "live" : "stale";
  const cells = countVisibleCells(map);
  const slamScore = Number(map?.scan_match_score);
  const slamScoreClass =
    !Number.isFinite(slamScore) || slamScore < 0.32
      ? "low"
      : slamScore < 0.7
        ? "mid"
        : "good";
  const poseLabel = pose
    ? `${pose.x.toFixed(2)} , ${pose.y.toFixed(2)}`
    : "—";
  const navigating =
    navPhase === "navigating" || navPhase === "docking" || navPhase === "settling";
  const settling = navPhase === "settling";
  const docking = navPhase === "docking";
  const arrived = navResult === "succeeded";
  const navUiStatus = navigating ? "navigating" : arrived ? "arrived" : "standby";

  let destHint = null;
  if (pose && selected) {
    const dx = Number(selected.x) - pose.x;
    const dy = Number(selected.y) - pose.y;
    const range = Math.hypot(dx, dy);
    const bearing = Math.atan2(dy, dx);
    let rel = bearing - pose.yaw;
    while (rel > Math.PI) rel -= 2 * Math.PI;
    while (rel < -Math.PI) rel += 2 * Math.PI;
    destHint = { rangeM: range, relDeg: (rel * 180) / Math.PI };
  }

  const formatDockDeltas = (fb) => {
    if (!fb) return null;
    const parts = [];
    if (fb.fwd_m != null) parts.push(`Δx ${Number(fb.fwd_m).toFixed(2)}m`);
    if (fb.left_m != null) parts.push(`Δy ${Number(fb.left_m).toFixed(2)}m`);
    if (fb.yaw_error_deg != null) {
      parts.push(`Δyaw ${Math.abs(Number(fb.yaw_error_deg)).toFixed(0)}°`);
    }
    if (!parts.length && fb.position_error_m != null) {
      parts.push(`${Number(fb.position_error_m).toFixed(2)}m`);
    }
    return parts.length ? parts.join(" · ") : null;
  };

  const formatDeltas = (fb) => {
    if (!fb) return null;
    const parts = [];
    if (fb.position_error_m != null) {
      parts.push(`Δpos ${Number(fb.position_error_m).toFixed(2)}m`);
    }
    if (fb.yaw_error_deg != null) {
      parts.push(`Δyaw ${Math.abs(Number(fb.yaw_error_deg)).toFixed(0)}°`);
    }
    return parts.length ? parts.join(" · ") : null;
  };

  const phaseNavLabel = (() => {
    if (settling) return "SLAM settling…";
    if (docking || navPhase === "docking") return "Final dock";
    const phase = Number(navUi?.phase);
    if (phase === 1) return "Nav2 · Approach";
    if (phase === 2) return "Marker acquire";
    if (phase === 3) return "Final dock";
    if (navUi?.label) return String(navUi.label);
    return null;
  })();

  const phaseNavDetail = (() => {
    if (docking || navPhase === "docking") {
      const phase = dockFeedback?.dock_phase || dockFeedback?.phase;
      const xy = dockFeedback?.position_error_m;
      const yaw = dockFeedback?.yaw_error_deg;
      const left = dockFeedback?.left_m;
      const fwd = dockFeedback?.fwd_m;
      if (phase && String(phase).startsWith("lat")) {
        return `lateral · Δxy ${(Number(xy) || 0).toFixed(2)}m`;
      }
      if (phase === "fwd" || phase === "shift_fwd" || phase === "shift_back") {
        return `close XY · fwd ${(Number(fwd) || 0).toFixed(2)}m`;
      }
      if (yaw != null && (phase === "yaw" || xy == null || Number(xy) <= 0.16)) {
        return `yaw ${Math.abs(Number(yaw)).toFixed(0)}° to face`;
      }
      if (xy != null) {
        return `Δxy ${Number(xy).toFixed(2)}m · L ${(Number(left) || 0).toFixed(2)}`;
      }
      return "close XY then yaw";
    }
    const phase = Number(navUi?.phase);
    if (phase === 1) {
      return "Nav2 controller · continuous /cmd_vel";
    }
    if (phase === 2) {
      return "waiting for marker (not enabled yet)";
    }
    return null;
  })();

  const statusDetail = (() => {
    if (error) return error;
    if (flashMsg) return flashMsg;
    if (navUiStatus === "arrived") {
      return formatDeltas(arrivalFeedback) || "at destination";
    }
    if (settling) {
      const ss = navFeedback?.settle_s;
      const stable = navFeedback?.pose_stable_s;
      const sm = navFeedback?.scan_match_score;
      const parts = ["holding still for stable pose"];
      if (ss != null) parts.push(`${Number(ss).toFixed(1)}s`);
      if (stable != null) parts.push(`stable ${Number(stable).toFixed(1)}s`);
      if (sm != null) parts.push(`match ${Math.round(Number(sm) * 100)}%`);
      return parts.join(" · ");
    }
    if (navUiStatus === "navigating") {
      if (phaseNavDetail) return phaseNavDetail;
      const dest = activeGoalLabel || selected?.label || "destination";
      if (docking) {
        const yaw = dockFeedback?.yaw_error_deg;
        if (yaw != null) return `yaw ${Math.abs(Number(yaw)).toFixed(0)}° → ${dest}`;
        return `final yaw → ${dest}`;
      }
      const dist =
        distRemaining != null
          ? `${distRemaining.toFixed(1)}m remaining`
          : destHint
            ? `${destHint.rangeM.toFixed(1)}m to ${dest}`
            : `→ ${dest}`;
      return dist;
    }
    if (navResult === "docking_timeout") return "yaw align timeout";
    if (navResult === "canceled") return "navigation canceled";
    if (navResult === "aborted") return "navigation aborted";
    if (map?.pose_in_map === false) return "pose outside map · use Repos";
    if (
      mapFrozen &&
      map?.scan_match_score != null &&
      map.scan_match_score < 0.32
    ) {
      return `scan mismatch ${Math.round(map.scan_match_score * 100)}% · use Repos`;
    }
    if (mapFrozen) {
      const corr = map?.overlay?.occupied_count || 0;
      const live = map?.scan_hit_count || 0;
      return `blue=frozen · amber=moved · green=live · ${corr} corr · ${live} pts`;
    }
    return `${cells} cells · ${waypoints.length} mk · freeze to navigate`;
  })();

  const statusLabel =
    navUiStatus === "navigating"
      ? phaseNavLabel ||
        (docking ? "Phase 3 · Yaw" : "In navigation")
      : navUiStatus === "arrived"
        ? "Arrived"
        : "Standby";
  const piDrive = navDrive?.drive || { x: 0, y: 0 };
  const velocityLabel = `ROVER vx ${(Number(piDrive.x) || 0).toFixed(2)} · vy ${(Number(piDrive.y) || 0).toFixed(2)}`;
  const velocityDisplay = [velocityLabel, formatDeltas(navFeedback || arrivalFeedback)]
    .filter(Boolean)
    .join(" · ");

  const navCanGo = isLive && mapFrozen && !navBusy && !navigating && !settling;

  const createWaypoint = async (coords = null) => {
    const body = {};
    if (coords) {
      body.x = coords.x;
      body.y = coords.y;
      body.yaw = coords.yaw;
    }
    const res = await fetch(`${getRelayHttpOrigin()}/api/slam/waypoints`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    const json = await res.json().catch(() => ({}));
    if (!res.ok || json.success === false) {
      throw new Error(json.error || `HTTP ${res.status}`);
    }
    return json.waypoint;
  };

  const goToWaypoint = async (wp) => {
    if (navBusy || !wp?.id) return;
    setNavBusy(true);
    setArrivalFeedback(null);
    const destLabel = waypointDisplayLabel(
      wp,
      waypoints.findIndex((w) => String(w.id) === String(wp.id)),
    );
    setFlashMsg(`going → ${destLabel}`);
    try {
      const res = await fetch(
        `${getRelayHttpOrigin()}/api/navigation/goto/${encodeURIComponent(wp.id)}`,
        {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            fine_docking: true,
            drive_assist: compactDriveAssistSnapshot(
              driveAssistEnabled,
              driveAssistUpdate,
            ),
          }),
        },
      );
      const json = await res.json().catch(() => ({}));
      if (!res.ok || json.success === false) {
        throw new Error(json.error || `HTTP ${res.status}`);
      }
      setSelectedId(String(wp.id));
      setActiveGoalId(String(wp.id));
      setActiveGoalLabel(destLabel);
      if (json.nav_id) setActiveNavId(String(json.nav_id));
      setNavPhase("navigating");
      setNavResult(null);
      setNavIdCopied(false);
      setFlashMsg("");
    } catch (e) {
      setFlashMsg(e instanceof Error ? e.message : "goto failed");
      setTimeout(() => setFlashMsg(""), 3500);
    } finally {
      setNavBusy(false);
    }
  };

  const goToSelected = async () => {
    if (!selected) return;
    await goToWaypoint(selected);
  };

  const copyNavId = async () => {
    if (!activeNavId || typeof navigator === "undefined" || !navigator.clipboard) {
      return;
    }
    try {
      await navigator.clipboard.writeText(activeNavId);
      setNavIdCopied(true);
      setTimeout(() => setNavIdCopied(false), 2000);
    } catch {
      setFlashMsg("copy failed");
      setTimeout(() => setFlashMsg(""), 2000);
    }
  };

  const freezeMap = async () => {
    if (navBusy || mapFrozen) return;
    if (
      typeof window !== "undefined" &&
      !window.confirm(
        "Freeze this structural map for stable marks? Live lidar will still detect moved chairs; local corrections show in amber until you Promote or restart.",
      )
    ) {
      return;
    }
    setNavBusy(true);
    setFlashMsg("freezing map…");
    try {
      await fetch(`${getRelayHttpOrigin()}/api/navigation/cancel`, { method: "POST" }).catch(() => null);
      const res = await fetch(`${getRelayHttpOrigin()}/api/slam/map/freeze`, { method: "POST" });
      const json = await res.json().catch(() => ({}));
      if (!res.ok || json.success === false) {
        throw new Error(json.error || `HTTP ${res.status}`);
      }
      setNavPhase("idle");
      setActiveGoalId(null);
      setFlashMsg("map frozen · switching to localization");
    } catch (e) {
      setFlashMsg(e instanceof Error ? e.message : "freeze failed");
    } finally {
      setNavBusy(false);
      setTimeout(() => setFlashMsg(""), 6000);
    }
  };

  const repositionRover = async () => {
    if (navBusy || !mapFrozen) return;
    if (
      typeof window !== "undefined" &&
      !window.confirm(
        "Reposition by matching the current lidar scan against the frozen map? Keep the rover stationary while localization runs.",
      )
    ) {
      return;
    }
    setNavBusy(true);
    setFlashMsg("repositioning…");
    try {
      await fetch(`${getRelayHttpOrigin()}/api/navigation/cancel`, { method: "POST" }).catch(() => null);
      const res = await fetch(`${getRelayHttpOrigin()}/api/slam/map/reposition`, { method: "POST" });
      const json = await res.json().catch(() => ({}));
      if (!res.ok || json.success === false) {
        throw new Error(json.error || `HTTP ${res.status}`);
      }
      setNavPhase("idle");
      setActiveGoalId(null);
      setFlashMsg("matching lidar against frozen map…");
    } catch (e) {
      setFlashMsg(e instanceof Error ? e.message : "reposition failed");
    } finally {
      setNavBusy(false);
      setTimeout(() => setFlashMsg(""), 6000);
    }
  };

  const promoteWorkingCopy = async () => {
    if (navBusy || !mapFrozen || !workingActive) return;
    if (
      typeof window !== "undefined" &&
      !window.confirm(
        "Promote local map corrections into the frozen baseline? Localization still uses the Cartographer frozen map until restart completes.",
      )
    ) {
      return;
    }
    setNavBusy(true);
    setFlashMsg("promoting local corrections…");
    try {
      await fetch(`${getRelayHttpOrigin()}/api/navigation/cancel`, { method: "POST" }).catch(() => null);
      const res = await fetch(`${getRelayHttpOrigin()}/api/slam/map/promote`, { method: "POST" });
      const json = await res.json().catch(() => ({}));
      if (!res.ok || json.success === false) {
        throw new Error(json.error || `HTTP ${res.status}`);
      }
      setFlashMsg("promoting · restarting SLAM…");
    } catch (e) {
      setFlashMsg(e instanceof Error ? e.message : "promote failed");
    } finally {
      setNavBusy(false);
      setTimeout(() => setFlashMsg(""), 6000);
    }
  };

  const purgeMap = async () => {
    if (navBusy) return;
    if (
      typeof window !== "undefined" &&
      !window.confirm("Purge the entire SLAM map and all marks? This cannot be undone.")
    ) {
      return;
    }
    setNavBusy(true);
    setFlashMsg("purging map…");
    try {
      await fetch(`${getRelayHttpOrigin()}/api/navigation/cancel`, { method: "POST" }).catch(() => null);
      const res = await fetch(`${getRelayHttpOrigin()}/api/slam/map/purge`, { method: "POST" });
      const json = await res.json().catch(() => ({}));
      if (!res.ok || json.success === false) {
        throw new Error(json.error || `HTTP ${res.status}`);
      }
      setSelectedId(null);
      setActiveGoalId(null);
      setActiveGoalLabel("");
      setNavPath(null);
      setNavPhase("idle");
      setNavResult("canceled");
      setFlashMsg("map purged · rebuilding");
    } catch (e) {
      setFlashMsg(e instanceof Error ? e.message : "purge failed");
    } finally {
      setNavBusy(false);
      setTimeout(() => setFlashMsg(""), 5000);
    }
  };

  const markHere = async () => {
    if (markBusy || !mapFrozen) return;
    setMarkBusy(true);
    setFlashMsg("");
    try {
      const item = await createWaypoint(null);
      if (item?.id) setSelectedId(String(item.id));
      const here = readPose(map);
      const deg = Math.round((((here.yaw * 180) / Math.PI) % 360 + 360) % 360);
      setFlashMsg(`marked ${item?.label || ""} · ${deg}°`.trim());
    } catch (e) {
      setFlashMsg(e instanceof Error ? e.message : "mark failed");
    } finally {
      setMarkBusy(false);
      setTimeout(() => setFlashMsg(""), 2500);
    }
  };

  const cancelNav = async () => {
    setNavBusy(true);
    try {
      await fetch(`${getRelayHttpOrigin()}/api/navigation/kill`, {
        method: "POST",
      });
      setNavPhase("idle");
      setActiveGoalId(null);
      setFlashMsg("stopped");
    } catch (e) {
      setFlashMsg(e instanceof Error ? e.message : "stop failed");
    } finally {
      setNavBusy(false);
      setTimeout(() => setFlashMsg(""), 2000);
    }
  };

  const deleteSelected = async () => {
    if (navBusy || !selected?.id || navigating) return;
    const label = waypointDisplayLabel(
      selected,
      waypoints.findIndex((w) => String(w.id) === String(selected.id)),
    );
    if (typeof window !== "undefined" && !window.confirm(`Delete “${label}”?`)) {
      return;
    }
    setNavBusy(true);
    setFlashMsg("");
    try {
      const res = await fetch(
        `${getRelayHttpOrigin()}/api/slam/waypoints/${encodeURIComponent(selected.id)}`,
        { method: "DELETE" },
      );
      const json = await res.json().catch(() => ({}));
      if (!res.ok || json.success === false) {
        throw new Error(json.error || `HTTP ${res.status}`);
      }
      const remaining = Array.isArray(json.waypoints) ? json.waypoints : [];
      setSelectedId(remaining.length ? String(remaining[remaining.length - 1].id) : null);
      setFlashMsg("deleted");
    } catch (e) {
      setFlashMsg(e instanceof Error ? e.message : "delete failed");
    } finally {
      setNavBusy(false);
      setTimeout(() => setFlashMsg(""), 2500);
    }
  };

  const onCanvasClick = async (ev) => {
    const canvas = canvasRef.current;
    if (!canvas || !map) return;
    const cssW = canvas.clientWidth;
    const cssH = canvas.clientHeight;
    const hit = hitTestWaypoint(
      map,
      displayRangeM,
      cssW,
      cssH,
      ev.clientX,
      ev.clientY,
      canvas,
    );
    if (hit?.id) {
      setSelectedId(String(hit.id));
      setFlashMsg(`sel ${hit.label || "mark"}`);
      setTimeout(() => setFlashMsg(""), 1500);
      return;
    }
    if (!mapFrozen || markBusy) return;

    const rect = canvas.getBoundingClientRect();
    const maxR = Math.min(cssW, cssH) * 0.46;
    const pxPerM = maxR / displayRangeM;
    const world = headingUpScreenToWorld(
      ev.clientX - rect.left,
      ev.clientY - rect.top,
      readCameraPose(map),
      cssW / 2,
      cssH / 2,
      pxPerM,
    );
    if (
      typeof window !== "undefined" &&
      !window.confirm(`Create mark at (${world.x.toFixed(2)}, ${world.y.toFixed(2)})?`)
    ) {
      return;
    }
    setMarkBusy(true);
    try {
      const item = await createWaypoint({ x: world.x, y: world.y, yaw: world.yaw });
      if (item?.id) setSelectedId(String(item.id));
      setFlashMsg(`marked ${item?.label || ""}`);
    } catch (e) {
      setFlashMsg(e instanceof Error ? e.message : "mark failed");
    } finally {
      setMarkBusy(false);
      setTimeout(() => setFlashMsg(""), 2500);
    }
  };

  if (!expanded) {
    return (
      <div
        className="lidar-minimap lidar-minimap--floating lidar-minimap--slam lidar-minimap--slam-compact"
        aria-label="SLAM navigation"
      >
        <div className="slam-compact-bar">
          <span className="lidar-minimap-title">
            SLAM{mapFrozen ? " · FRZ" : ""}
          </span>
          <span className={`lidar-minimap-status ${statusClass}`} aria-hidden="true" />
          <div className="slam-compact-dests" role="group" aria-label="Go to mark">
            {!waypoints.length ? (
              <span className="slam-compact-empty">no marks</span>
            ) : (
              waypoints.map((wp, index) => {
                const label = waypointCompactLabel(wp, index);
                const isActive =
                  String(activeGoalId) === String(wp.id) ||
                  (navigating && String(selectedId) === String(wp.id));
                return (
                  <button
                    key={wp.id}
                    type="button"
                    className={`slam-compact-dest-btn${isActive ? " slam-compact-dest-btn--active" : ""}`}
                    onClick={() => void goToWaypoint(wp)}
                    disabled={!navCanGo}
                    title={`Navigate to ${waypointDisplayLabel(wp, index)}`}
                    aria-label={`Go to ${waypointDisplayLabel(wp, index)}`}
                  >
                    {label}
                  </button>
                );
              })
            )}
          </div>
          <button
            type="button"
            className="slam-compact-expand"
            onClick={() => setExpanded(true)}
            title="Expand SLAM map and controls"
            aria-label="Expand SLAM panel"
          >
            MAP
          </button>
        </div>
        <div
          className={`slam-nav-indicator slam-nav-indicator--compact slam-nav-indicator--${navUiStatus}${navigating ? " slam-nav-indicator--active" : ""}`}
          aria-live="polite"
        >
          <span className="slam-nav-phase">
            <span
              className={`slam-nav-dot${navigating ? " slam-nav-dot--live" : ""}${navUiStatus === "arrived" ? " slam-nav-dot--arrived" : ""}`}
              aria-hidden="true"
            />
            {statusLabel}
          </span>
          <span className="slam-nav-detail">{statusDetail}</span>
        </div>
        <div
          className="slam-nav-velocity"
          title="Converted rover drive vector: vx is the turn/x axis, vy is the forward/y axis; vy<0 forward, vx<0 left."
          aria-label={`Translated drive velocity and navigation deltas: ${velocityDisplay}`}
        >
          {velocityDisplay}
        </div>
      </div>
    );
  }

  return (
    <div className="lidar-minimap lidar-minimap--floating lidar-minimap--slam" aria-label="SLAM map">
      <div className="lidar-minimap-header">
        <span className="lidar-minimap-title">SLAM{mapFrozen ? " · FROZEN" : " · MAPPING"}</span>
        <div className="lidar-minimap-zoom" role="group" aria-label="Zoom">
          <button
            type="button"
            className="lidar-minimap-zoom-btn"
            onClick={() => setExpanded(false)}
            aria-label="Collapse SLAM panel"
            title="Compact view"
          >
            −
          </button>
          <button
            type="button"
            className="lidar-minimap-zoom-btn"
            onClick={() => setRangeM((r) => clampRange(r * ZOOM_FACTOR))}
            aria-label="Zoom out"
          >
            −
          </button>
          <button
            type="button"
            className="lidar-minimap-zoom-btn"
            onClick={() => setRangeM((r) => clampRange(r / ZOOM_FACTOR))}
            aria-label="Zoom in"
          >
            +
          </button>
        </div>
        <span className={`lidar-minimap-status ${statusClass}`} aria-hidden="true" />
      </div>
      <div className="slam-map-canvas-wrap">
        <canvas
          ref={canvasRef}
          className="lidar-minimap-canvas"
          onClick={onCanvasClick}
          style={{ cursor: waypoints.length ? "pointer" : "default" }}
        />
        {Number.isFinite(slamScore) && (
          <span
            className={`slam-map-score slam-map-score--${slamScoreClass}`}
            title="Live LiDAR scan-match score"
            aria-label={`SLAM scan match ${Math.round(slamScore * 100)} percent`}
          >
            {Math.round(slamScore * 100)}%
          </span>
        )}
      </div>

      <div className="slam-nav-panel">
        <div className="slam-nav-row" role="group" aria-label="Destination">
          <select
            className="slam-nav-select"
            value={selectedId || ""}
            onChange={(e) => setSelectedId(e.target.value || null)}
            disabled={!waypoints.length}
            aria-label="Select destination mark"
          >
            {!waypoints.length ? (
              <option value="">No marks — use MARK</option>
            ) : (
              waypoints.map((wp) => {
                const yaw = Number(wp.yaw);
                const yawTxt = Number.isFinite(yaw)
                  ? ` · ${Math.round((((yaw * 180) / Math.PI) % 360 + 360) % 360)}°`
                  : "";
                const xyTxt =
                  Number.isFinite(Number(wp.x)) && Number.isFinite(Number(wp.y))
                    ? ` (${Number(wp.x).toFixed(1)}, ${Number(wp.y).toFixed(1)})`
                    : "";
                return (
                  <option key={wp.id} value={String(wp.id)}>
                    {wp.label || "mark"}
                    {xyTxt}
                    {yawTxt}
                  </option>
                );
              })
            )}
          </select>
        </div>
        <div className="slam-nav-toolbar" role="toolbar" aria-label="Map and navigation">
          <button
            type="button"
            className="slam-nav-btn slam-nav-btn--abbr slam-nav-btn--go"
            onClick={() => void goToSelected()}
            disabled={navBusy || !isLive || !mapFrozen || !selected || navigating}
            title={
              mapFrozen
                ? "Go to selected mark"
                : "Freeze map before navigation"
            }
            aria-label="Go to mark"
          >
            GO
          </button>
          <button
            type="button"
            className="slam-nav-btn slam-nav-btn--abbr slam-nav-btn--del"
            onClick={() => void deleteSelected()}
            disabled={navBusy || !selected || navigating}
            title="Delete selected mark"
            aria-label="Delete mark"
          >
            DEL
          </button>
          <button
            type="button"
            className={`slam-nav-btn slam-nav-btn--abbr slam-nav-btn--freeze${mapFrozen ? " slam-nav-btn--active" : ""}`}
            onClick={() => void freezeMap()}
            disabled={navBusy || mapFrozen || !isLive}
            title={
              mapFrozen
                ? "Map frozen (localization mode)"
                : "Freeze structural map for navigation"
            }
            aria-label={mapFrozen ? "Map frozen" : "Freeze map"}
          >
            FRZ
          </button>
          <button
            type="button"
            className="slam-nav-btn slam-nav-btn--abbr slam-nav-btn--mark"
            onClick={() => void markHere()}
            disabled={markBusy || !isLive || !mapFrozen}
            title="Mark current pose"
            aria-label="Mark current pose"
          >
            MARK
          </button>
          <button
            type="button"
            className="slam-nav-btn slam-nav-btn--abbr slam-nav-btn--purge"
            onClick={() => void purgeMap()}
            disabled={navBusy}
            title="Purge map and all marks"
            aria-label="Purge map"
          >
            PURG
          </button>
          <button
            type="button"
            className="slam-nav-btn slam-nav-btn--abbr slam-nav-btn--kill"
            onClick={() => void cancelNav()}
            disabled={navBusy || !isLive}
            title="Terminate navigation and stop motors"
            aria-label="Terminate navigation"
          >
            STOP
          </button>
          <button
            type="button"
            className="slam-nav-btn slam-nav-btn--abbr slam-nav-btn--repos"
            onClick={() => void repositionRover()}
            disabled={navBusy || !mapFrozen || !isLive}
            title="Infer rover position from live lidar and the frozen map"
            aria-label="Reposition"
          >
            REPO
          </button>
          <button
            type="button"
            className="slam-nav-btn slam-nav-btn--abbr slam-nav-btn--promote"
            onClick={() => void promoteWorkingCopy()}
            disabled={navBusy || !mapFrozen || !workingActive}
            title={
              workingActive
                ? "Save local corrections into the frozen baseline"
                : "Local corrections appear after live scans differ from the frozen map"
            }
            aria-label="Promote working copy"
          >
            PROM
          </button>
        </div>

        <div
          className={`slam-nav-indicator slam-nav-indicator--${navUiStatus}${navigating ? " slam-nav-indicator--active" : ""}`}
          aria-live="polite"
        >
          <span className="slam-nav-phase">
            <span
              className={`slam-nav-dot${navigating ? " slam-nav-dot--live" : ""}${navUiStatus === "arrived" ? " slam-nav-dot--arrived" : ""}`}
              aria-hidden="true"
            />
            {statusLabel}
          </span>
          <span className="slam-nav-detail">{statusDetail}</span>
        </div>
        <div
          className="slam-nav-velocity"
          title="Converted rover drive vector: vx is the turn/x axis, vy is the forward/y axis; vy<0 forward, vx<0 left."
          aria-label={`Translated drive velocity and navigation deltas: ${velocityDisplay}`}
        >
          {velocityDisplay}
        </div>
      </div>
    </div>
  );
}
