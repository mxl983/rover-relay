import { useEffect, useRef, useState } from "react";
import {
  forwardViewConeEdgesDeg,
  isAngleInDisplayArc,
  laserBearingToCanvas,
  laserToCanvas,
  LIDAR_FORWARD_DEG,
  nearestPointWithAngle,
  pointToLaserXY,
  ROVER_BODY_LENGTH_M,
  ROVER_BODY_WIDTH_M,
  roverBodyFootprintCornersM,
  viewHeadingFromPan,
} from "../utils/lidarCoords";
import { drawSlamMapPoints } from "../utils/slamMapDraw";
import { drawLidarWallMerged } from "../utils/lidarWallDraw";
import { SLAM_ENABLED } from "../config";

const DEFAULT_RANGE_M = 4;
const MIN_RANGE_M = 1.5;
const MAX_RANGE_M = 10;
const ZOOM_FACTOR = 1.25;
const MAX_DPR = 3;

function isMinimapPointVisible(angleDeg) {
  return isAngleInDisplayArc(angleDeg);
}

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
  ctx.beginPath();
  ctx.moveTo(plot[0].x, plot[0].y);
  for (let i = 1; i < plot.length; i += 1) {
    ctx.lineTo(plot[i].x, plot[i].y);
  }
  ctx.closePath();
  ctx.fill();

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
 * @param {object|null} [slamMap]
 */
export function drawLidarMinimap(
  ctx,
  w,
  h,
  scan,
  rangeM = DEFAULT_RANGE_M,
  viewHeadingDeg = LIDAR_FORWARD_DEG,
  slamMap = null,
) {
  const cx = w / 2;
  const cy = h / 2;
  const scale = w / 180;
  const maxR = Math.min(cx, cy);
  const points = scan?.points ?? [];

  ctx.clearRect(0, 0, w, h);
  if (SLAM_ENABLED) {
    drawSlamMapPoints(ctx, cx, cy, maxR, rangeM, slamMap?.map_points, scale);
  }
  drawForwardViewCone(ctx, cx, cy, maxR, rangeM, scale, viewHeadingDeg);

  drawLidarWallMerged(
    ctx,
    cx,
    cy,
    maxR,
    rangeM,
    scale,
    points,
    isMinimapPointVisible,
  );

  drawCarBody(ctx, cx, cy, maxR, rangeM, scale);
}

/**
 * @param {{
 *   scan: import("../hooks/useLidarScan").LidarScan | null;
 *   isLive: boolean;
 *   error: string | null;
 *   pan?: number | null;
 *   slamMap?: object | null;
 *   slamLive?: boolean;
 *   slamError?: string | null;
 * }} props
 */
export function LidarMinimap({
  scan,
  isLive,
  error,
  pan,
  slamMap,
  slamLive,
  slamError,
}) {
  const canvasRef = useRef(null);
  const scanRef = useRef(scan);
  const rangeRef = useRef(DEFAULT_RANGE_M);
  const panRef = useRef(viewHeadingFromPan(pan));
  const slamMapRef = useRef(slamMap);
  const [rangeM, setRangeM] = useState(DEFAULT_RANGE_M);

  scanRef.current = scan;
  rangeRef.current = rangeM;
  panRef.current = viewHeadingFromPan(pan);
  slamMapRef.current = slamMap;

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
          slamMapRef.current,
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
  const mapStatusClass = slamError ? "stale" : slamLive ? "live" : "stale";
  const visiblePoints = (scan?.points ?? []).filter((point) => {
    const { angleDeg } = pointToLaserXY(point);
    return isMinimapPointVisible(angleDeg);
  });
  const nearest = nearestPointWithAngle(visiblePoints);
  const nearestLabel =
    nearest && Number.isFinite(nearest.range)
      ? `near ${nearest.range.toFixed(2)}m${Number.isFinite(nearest.angleDeg) ? ` @ ${nearest.angleDeg.toFixed(0)}°` : ""
      }`
      : "near —";

  const atMinZoom = rangeM <= MIN_RANGE_M + 0.01;
  const atMaxZoom = rangeM >= MAX_RANGE_M - 0.01;

  return (
    <div className="lidar-minimap lidar-minimap--floating" aria-label="LiDAR minimap">
      <div className="lidar-minimap-header">
        <span className="lidar-minimap-title">LiDAR</span>
        <div className="lidar-minimap-zoom" role="group" aria-label="Zoom">
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
        {SLAM_ENABLED ? (
          <span
            className={`lidar-minimap-status lidar-minimap-status--map ${mapStatusClass}`}
            aria-hidden="true"
            title={slamError || (slamLive ? "SLAM map live" : "SLAM map stale")}
          />
        ) : null}
      </div>
      <canvas ref={canvasRef} className="lidar-minimap-canvas" />
      <div className="lidar-minimap-stats">
        <span>{scan?.hz ? `${scan.hz} Hz` : "—"}</span>
        <span>{`${rangeM.toFixed(1)}m`}</span>
        {SLAM_ENABLED ? (
          <span>{slamMap?.map_points?.length ? `map ${slamMap.map_points.length}` : "map —"}</span>
        ) : null}
        <span className="lidar-minimap-nearest">{nearestLabel}</span>
      </div>
    </div>
  );
}
