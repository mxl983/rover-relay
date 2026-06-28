import { useEffect, useRef } from "react";
import {
  laserToCanvas,
  nearestPointWithAngle,
  pointToLaserXY,
} from "../utils/lidarCoords";

const MINIMAP_RANGE_M = 4;
const FILTER_MIN_DEG = 190;
const FILTER_MAX_DEG = 350;
const MAX_DPR = 3;

function normalizeDeg(deg) {
  if (!Number.isFinite(deg)) return null;
  const n = ((deg % 360) + 360) % 360;
  return n;
}

function shouldIgnoreByAngle(angleDeg) {
  const n = normalizeDeg(angleDeg);
  if (!Number.isFinite(n)) return false;
  return n >= FILTER_MIN_DEG && n <= FILTER_MAX_DEG;
}

function clamp01(v) {
  return Math.max(0, Math.min(1, v));
}

function pointColorByRange(range) {
  const t = clamp01(range / MINIMAP_RANGE_M);
  const hue = 120 * t;
  return `hsl(${hue.toFixed(1)} 95% 55%)`;
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
  ctx.imageSmoothingEnabled = true;
  ctx.imageSmoothingQuality = "high";
  ctx.lineCap = "round";
  ctx.lineJoin = "round";

  return { ctx, cssW, cssH };
}

function drawAngleLabels(ctx, cx, cy, maxR, scale = 1) {
  const fontPx = Math.max(8, 7 * scale);
  ctx.fillStyle = "rgba(0, 242, 255, 0.65)";
  ctx.font = `${fontPx}px ui-monospace, SFMono-Regular, Menlo, Consolas, monospace`;
  ctx.textAlign = "center";
  ctx.textBaseline = "middle";
  const inset = 12 * scale;
  const labels = [
    { label: "90°", x: cx, y: cy - maxR + inset },
    { label: "180°", x: cx - maxR + inset, y: cy },
    { label: "270°", x: cx, y: cy + maxR - inset },
    { label: "0°", x: cx + maxR - inset, y: cy },
  ];
  for (const { label, x, y } of labels) {
    ctx.fillText(label, x, y);
  }
}

function drawRangeLegend(ctx, cx, cy, maxR, scale = 1) {
  const fontPx = Math.max(8, 7 * scale);
  ctx.fillStyle = "rgba(0, 242, 255, 0.6)";
  ctx.font = `${fontPx}px ui-monospace, SFMono-Regular, Menlo, Consolas, monospace`;
  ctx.textAlign = "left";
  ctx.textBaseline = "middle";
  for (let ring = 1; ring <= 3; ring += 1) {
    const r = (maxR * ring) / 3;
    const meters = ((MINIMAP_RANGE_M * ring) / 3).toFixed(1);
    ctx.fillText(`${meters}m`, cx + 5 * scale, cy - r);
  }
}

function drawSmoothPoint(ctx, x, y, color, alpha, radius) {
  ctx.save();
  ctx.globalAlpha = alpha;
  const glow = ctx.createRadialGradient(x, y, 0, x, y, radius);
  glow.addColorStop(0, color);
  glow.addColorStop(0.45, color);
  glow.addColorStop(1, "rgba(0,0,0,0)");
  ctx.fillStyle = glow;
  ctx.beginPath();
  ctx.arc(x, y, radius, 0, Math.PI * 2);
  ctx.fill();
  ctx.restore();
}

/**
 * @param {CanvasRenderingContext2D} ctx
 * @param {number} w
 * @param {number} h
 * @param {{ points?: { x?: number; y?: number; r?: number; a?: number; a_deg?: number }[] }} scan
 */
export function drawLidarMinimap(ctx, w, h, scan) {
  const cx = w / 2;
  const cy = h / 2;
  const scale = w / 200;
  const maxR = Math.min(cx, cy) - 14 * scale;
  const gridLine = Math.max(0.75, 1 * scale);

  ctx.clearRect(0, 0, w, h);

  ctx.strokeStyle = "rgba(0, 242, 255, 0.22)";
  ctx.lineWidth = gridLine;
  for (let ring = 1; ring <= 3; ring += 1) {
    const r = (maxR * ring) / 3;
    ctx.beginPath();
    ctx.arc(cx, cy, r, 0, Math.PI * 2);
    ctx.stroke();
  }

  ctx.beginPath();
  ctx.moveTo(cx, cy - maxR);
  ctx.lineTo(cx, cy + maxR);
  ctx.moveTo(cx - maxR, cy);
  ctx.lineTo(cx + maxR, cy);
  ctx.stroke();

  drawAngleLabels(ctx, cx, cy, maxR, scale);
  drawRangeLegend(ctx, cx, cy, maxR, scale);

  ctx.fillStyle = "rgba(0, 242, 255, 0.4)";
  ctx.beginPath();
  ctx.moveTo(cx, cy - 4 * scale);
  ctx.lineTo(cx - 3 * scale, cy + 3 * scale);
  ctx.lineTo(cx + 3 * scale, cy + 3 * scale);
  ctx.closePath();
  ctx.fill();

  const pointRadius = Math.max(1.8, 2.2 * scale);
  for (const point of scan?.points ?? []) {
    const { lx, ly, range, angleDeg } = pointToLaserXY(point);
    if (shouldIgnoreByAngle(angleDeg)) continue;

    const { x, y } = laserToCanvas(cx, cy, lx, ly, maxR, MINIMAP_RANGE_M);
    const alpha = Math.max(0.3, 1 - range / MINIMAP_RANGE_M);
    drawSmoothPoint(ctx, x, y, pointColorByRange(range), alpha, pointRadius);
  }
}

function formatAngleRange(scan) {
  if (!scan) return "θ —";
  const min = scan.angle_min_deg;
  const max = scan.angle_max_deg;
  const step = scan.angle_increment_deg;
  if (!Number.isFinite(min) || !Number.isFinite(max)) return "θ —";
  const stepLabel = Number.isFinite(step) ? ` Δ${step}°` : "";
  return `θ ${min}°→${max}°${stepLabel}`;
}

function formatNearestAngle(scan) {
  const nearest = nearestPointWithAngle(scan?.points ?? []);
  if (!nearest || !Number.isFinite(nearest.range)) return "near —";
  const deg = Number.isFinite(nearest.angleDeg)
    ? `${nearest.angleDeg.toFixed(0)}°`
    : "—";
  return `near ${nearest.range.toFixed(2)}m @ ${deg}`;
}

/**
 * @param {{
 *   scan: import("../hooks/useLidarScan").LidarScan | null;
 *   isLive: boolean;
 *   error: string | null;
 *   onClose: () => void;
 * }} props
 */
export function LidarMinimap({ scan, isLive, error, onClose }) {
  const canvasRef = useRef(null);
  const scanRef = useRef(scan);

  scanRef.current = scan;

  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas) return undefined;

    let frame = 0;
    const render = () => {
      const prepared = prepareCanvasContext(canvas);
      if (prepared) {
        drawLidarMinimap(prepared.ctx, prepared.cssW, prepared.cssH, scanRef.current);
      }
      frame = requestAnimationFrame(render);
    };

    const observer = new ResizeObserver(() => {
      prepareCanvasContext(canvas);
    });
    observer.observe(canvas);

    render();
    return () => {
      cancelAnimationFrame(frame);
      observer.disconnect();
    };
  }, []);

  const statusClass = error ? "stale" : isLive ? "live" : "stale";
  const statusText = error
    ? "LiDAR offline"
    : isLive
      ? "Live"
      : "Waiting";

  return (
    <div className="lidar-minimap glass-card" aria-label="LiDAR minimap">
      <div className="lidar-minimap-header">
        <span className="lidar-minimap-title">LiDAR</span>
        <span className={`lidar-minimap-status ${statusClass}`}>{statusText}</span>
        <button
          type="button"
          className="lidar-minimap-close"
          onClick={onClose}
          aria-label="Hide LiDAR minimap"
        >
          ×
        </button>
      </div>
      <canvas ref={canvasRef} className="lidar-minimap-canvas" />
      <div className="lidar-minimap-stats">
        <span>{scan?.hz ? `${scan.hz} Hz` : "—"}</span>
        <span>{scan?.valid ? `${scan.valid} pts` : "—"}</span>
        <span>{scan?.nearest != null ? `${scan.nearest}m` : "—"}</span>
      </div>
      <div className="lidar-minimap-debug">
        <span>{formatAngleRange(scan)}</span>
        <span>{formatNearestAngle(scan)}</span>
        <span>{`range 0–${MINIMAP_RANGE_M}m · hide ${FILTER_MIN_DEG}°–${FILTER_MAX_DEG}° · raw`}</span>
      </div>
      <div className="lidar-minimap-color-legend" aria-hidden="true">
        <span className="lidar-legend-dot near" /> <span>near</span>
        <span className="lidar-legend-gradient" />
        <span>far</span> <span className="lidar-legend-dot far" />
      </div>
    </div>
  );
}
