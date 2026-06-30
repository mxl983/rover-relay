import { useEffect, useRef, useState } from "react";
import {
  bodyProximityPointColor,
  bearingToCanvasPx,
  distanceToRoverBodyM,
  forwardViewConeEdgesDeg,
  isAngleInDisplayArc,
  laserBearingToCanvas,
  laserToCanvas,
  lidarMinimapMarkedAnglesDeg,
  LIDAR_FORWARD_DEG,
  LIDAR_MINIMAP_ARC_DEG,
  nearestPointWithAngle,
  pointToLaserXY,
  ROVER_BODY_LENGTH_M,
  ROVER_BODY_WIDTH_M,
  roverBodyFootprintCornersM,
  viewHeadingFromPan,
} from "../utils/lidarCoords";

const DEFAULT_RANGE_M = 4;
const MIN_RANGE_M = 1.5;
const MAX_RANGE_M = 10;
const ZOOM_FACTOR = 1.25;
const MAX_DPR = 3;
const POINT_COLOR = "rgba(235, 248, 255";

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
  ctx.imageSmoothingEnabled = true;
  ctx.imageSmoothingQuality = "high";
  ctx.lineCap = "round";
  ctx.lineJoin = "round";

  return { ctx, cssW, cssH };
}

function drawFrostVignette(ctx, cx, cy, maxR) {
  const frost = ctx.createRadialGradient(cx, cy, maxR * 0.1, cx, cy, maxR * 1.05);
  frost.addColorStop(0, "rgba(255, 255, 255, 0.04)");
  frost.addColorStop(0.55, "rgba(220, 235, 255, 0.02)");
  frost.addColorStop(1, "rgba(180, 210, 235, 0.08)");
  ctx.fillStyle = frost;
  ctx.beginPath();
  ctx.arc(cx, cy, maxR, 0, Math.PI * 2);
  ctx.fill();
}

function drawPoint(ctx, x, y, range, scale, rangeM, proximityRgb = null) {
  const alpha = Math.max(0.5, 0.95 - (range / rangeM) * 0.35);
  const r = Math.max(0.85, 1 * scale);
  const colorBase = proximityRgb ?? POINT_COLOR;
  const pointAlpha = proximityRgb ? Math.max(0.85, alpha) : alpha;
  ctx.fillStyle = `${colorBase}, ${pointAlpha})`;
  ctx.beginPath();
  ctx.arc(x, y, r, 0, Math.PI * 2);
  ctx.fill();
}

function drawRadarRim(ctx, cx, cy, maxR, scale) {
  ctx.strokeStyle = "rgba(255, 255, 255, 0.2)";
  ctx.lineWidth = Math.max(0.5, 0.75 * scale);
  ctx.beginPath();
  ctx.arc(cx, cy, maxR, 0, Math.PI * 2);
  ctx.stroke();
}

/**
 * Rim ticks and labels using scan data bearings (`a_deg`, 0° = +x forward).
 */
function drawAngleMarks(ctx, cx, cy, maxR, scale) {
  const { cardinal, assistBounds, minor } = lidarMinimapMarkedAnglesDeg();
  const fontSize = Math.max(7, 7.5 * scale);
  ctx.font = `600 ${fontSize}px ui-monospace, SFMono-Regular, Menlo, monospace`;
  ctx.textAlign = "center";
  ctx.textBaseline = "middle";

  const drawTick = (
    angleDeg,
    { tickLen = 0.05, color = "rgba(255, 255, 255, 0.35)", label = null, labelColor = null } = {},
  ) => {
    if (!isAngleInDisplayArc(angleDeg)) return;
    const innerR = maxR * (1 - tickLen);
    const p0 = bearingToCanvasPx(cx, cy, angleDeg, innerR);
    const p1 = bearingToCanvasPx(cx, cy, angleDeg, maxR);
    ctx.strokeStyle = color;
    ctx.lineWidth = Math.max(0.5, 0.75 * scale);
    ctx.beginPath();
    ctx.moveTo(p0.x, p0.y);
    ctx.lineTo(p1.x, p1.y);
    ctx.stroke();

    if (label != null) {
      const lp = bearingToCanvasPx(cx, cy, angleDeg, maxR - 14 * scale);
      ctx.fillStyle = labelColor ?? color;
      ctx.fillText(`${label}°`, lp.x, lp.y);
    }
  };

  for (const angleDeg of minor) drawTick(angleDeg, { tickLen: 0.035 });
  for (const angleDeg of assistBounds) {
    drawTick(angleDeg, {
      tickLen: 0.09,
      color: "rgba(255, 185, 90, 0.85)",
      label: angleDeg,
      labelColor: "rgba(255, 200, 120, 0.95)",
    });
  }
  for (const angleDeg of cardinal) {
    drawTick(angleDeg, {
      tickLen: 0.065,
      color: "rgba(235, 248, 255, 0.55)",
      label: angleDeg,
      labelColor: "rgba(220, 240, 255, 0.88)",
    });
  }
}

function drawForwardViewCone(ctx, cx, cy, maxR, rangeM, scale, viewHeadingDeg) {
  const { edgeADeg, edgeBDeg } = forwardViewConeEdgesDeg(viewHeadingDeg);
  const tipA = laserBearingToCanvas(cx, cy, edgeADeg, maxR, rangeM);
  const tipB = laserBearingToCanvas(cx, cy, edgeBDeg, maxR, rangeM);

  ctx.strokeStyle = "rgba(255, 255, 255, 0.24)";
  ctx.lineWidth = Math.max(0.75, 1 * scale);
  ctx.beginPath();
  ctx.moveTo(cx, cy);
  ctx.lineTo(tipA.x, tipA.y);
  ctx.moveTo(cx, cy);
  ctx.lineTo(tipB.x, tipB.y);
  ctx.stroke();
}

function drawCarBody(ctx, cx, cy, maxR, rangeM, uiScale) {
  const pxPerM = maxR / rangeM;
  const corners = roverBodyFootprintCornersM();
  const plot = corners.map(([lx, ly]) => laserToCanvas(cx, cy, lx, ly, maxR, rangeM));

  ctx.fillStyle = "rgba(200, 220, 240, 0.2)";
  ctx.strokeStyle = "rgba(235, 248, 255, 0.5)";
  ctx.lineWidth = Math.max(0.75, 0.05 * pxPerM);
  ctx.beginPath();
  ctx.moveTo(plot[0].x, plot[0].y);
  for (let i = 1; i < plot.length; i += 1) {
    ctx.lineTo(plot[i].x, plot[i].y);
  }
  ctx.closePath();
  ctx.fill();
  ctx.stroke();

  const centerR = Math.max(1.25, Math.min(ROVER_BODY_WIDTH_M * pxPerM * 0.14, 4 * uiScale));
  ctx.fillStyle = "rgba(0, 242, 255, 0.8)";
  ctx.beginPath();
  ctx.arc(cx, cy, centerR, 0, Math.PI * 2);
  ctx.fill();
}

/**
 * @param {CanvasRenderingContext2D} ctx
 * @param {number} w
 * @param {number} h
 * @param {{ points?: { x?: number; y?: number; r?: number; a?: number; a_deg?: number }[] }} scan
 * @param {number} rangeM
 * @param {number} [viewHeadingDeg]
 */
export function drawLidarMinimap(
  ctx,
  w,
  h,
  scan,
  rangeM = DEFAULT_RANGE_M,
  viewHeadingDeg = LIDAR_FORWARD_DEG,
) {
  const cx = w / 2;
  const cy = h / 2;
  const scale = w / 180;
  const maxR = Math.min(cx, cy) - 14 * scale;
  const points = scan?.points ?? [];

  ctx.clearRect(0, 0, w, h);
  drawFrostVignette(ctx, cx, cy, maxR);
  drawRadarRim(ctx, cx, cy, maxR, scale);
  drawAngleMarks(ctx, cx, cy, maxR, scale);
  drawForwardViewCone(ctx, cx, cy, maxR, rangeM, scale, viewHeadingDeg);

  for (const point of points) {
    const { lx, ly, range, angleDeg } = pointToLaserXY(point);
    if (!isAngleInDisplayArc(angleDeg)) continue;
    const plot = laserToCanvas(cx, cy, lx, ly, maxR, rangeM);
    const bodyDist = distanceToRoverBodyM(lx, ly, ROVER_BODY_LENGTH_M, ROVER_BODY_WIDTH_M);
    const proximityRgb = bodyProximityPointColor(bodyDist);
    drawPoint(ctx, plot.x, plot.y, range, scale, rangeM, proximityRgb);
  }

  drawCarBody(ctx, cx, cy, maxR, rangeM, scale);
}

/**
 * @param {{
 *   scan: import("../hooks/useLidarScan").LidarScan | null;
 *   isLive: boolean;
 *   error: string | null;
 *   pan?: number | null;
 * }} props
 */
export function LidarMinimap({ scan, isLive, error, pan }) {
  const canvasRef = useRef(null);
  const scanRef = useRef(scan);
  const rangeRef = useRef(DEFAULT_RANGE_M);
  const panRef = useRef(viewHeadingFromPan(pan));
  const [rangeM, setRangeM] = useState(DEFAULT_RANGE_M);

  scanRef.current = scan;
  rangeRef.current = rangeM;
  panRef.current = viewHeadingFromPan(pan);

  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas) return undefined;

    let frame = 0;
    const render = () => {
      const prepared = prepareCanvasContext(canvas);
      if (prepared) {
        drawLidarMinimap(
          prepared.ctx,
          prepared.cssW,
          prepared.cssH,
          scanRef.current,
          rangeRef.current,
          panRef.current,
        );
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

  const zoomIn = () => {
    setRangeM((prev) => clampRange(prev / ZOOM_FACTOR));
  };

  const zoomOut = () => {
    setRangeM((prev) => clampRange(prev * ZOOM_FACTOR));
  };

  const statusClass = error ? "stale" : isLive ? "live" : "stale";
  const nearest = nearestPointWithAngle(scan?.points ?? [], {
    displayArcDeg: LIDAR_MINIMAP_ARC_DEG,
  });
  const nearestLabel =
    nearest && Number.isFinite(nearest.range)
      ? `near ${nearest.range.toFixed(2)}m${
          Number.isFinite(nearest.angleDeg) ? ` @ ${nearest.angleDeg.toFixed(0)}°` : ""
        }`
      : "near —";

  const atMinZoom = rangeM <= MIN_RANGE_M + 0.01;
  const atMaxZoom = rangeM >= MAX_RANGE_M - 0.01;

  return (
    <div className="lidar-minimap lidar-minimap--embedded" aria-label="LiDAR minimap">
      <div className="lidar-minimap-header">
        <span className="lidar-minimap-title">LiDAR</span>
        <div className="lidar-minimap-zoom">
          <button
            type="button"
            className="lidar-minimap-zoom-btn"
            onClick={zoomIn}
            disabled={atMinZoom}
            aria-label="Zoom in"
          >
            +
          </button>
          <button
            type="button"
            className="lidar-minimap-zoom-btn"
            onClick={zoomOut}
            disabled={atMaxZoom}
            aria-label="Zoom out"
          >
            −
          </button>
        </div>
        <span className={`lidar-minimap-status ${statusClass}`} aria-hidden="true" />
      </div>
      <canvas ref={canvasRef} className="lidar-minimap-canvas" />
      <div className="lidar-minimap-stats">
        <span>{scan?.hz ? `${scan.hz} Hz` : "—"}</span>
        <span>{`${rangeM.toFixed(1)}m`}</span>
        <span className="lidar-minimap-nearest">{nearestLabel}</span>
      </div>
    </div>
  );
}
