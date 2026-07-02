import {
  bodyProximityPointColor,
  distanceToRoverBodyM,
  laserToCanvas,
  pointToLaserXY,
} from "./lidarCoords.js";

/** Max bearing step between adjacent returns on the same surface (degrees). */
export const LIDAR_WALL_MAX_ANGLE_GAP_DEG = 6;
/** Max range difference for two returns to be treated as the same wall (m). */
export const LIDAR_WALL_MAX_RANGE_GAP_M = 0.22;
/** Max chord length between adjacent returns on the same wall (m). */
export const LIDAR_WALL_MAX_CHORD_M = 0.35;

/**
 * @param {number} a
 * @param {number} b
 */
export function angularGapDeg(a, b) {
  let d = Math.abs(a - b) % 360;
  if (d > 180) d = 360 - d;
  return d;
}

/**
 * @param {{
 *   angleDeg: number;
 *   range: number;
 *   lx: number;
 *   ly: number;
 *   x: number;
 *   y: number;
 *   bodyDistM: number;
 * }} a
 * @param {{
 *   angleDeg: number;
 *   range: number;
 *   lx: number;
 *   ly: number;
 * }} b
 * @param {{
 *   maxAngleGapDeg?: number;
 *   maxRangeGapM?: number;
 *   maxChordM?: number;
 * }} [options]
 */
export function canLinkLidarWallPoints(a, b, options = {}) {
  const {
    maxAngleGapDeg = LIDAR_WALL_MAX_ANGLE_GAP_DEG,
    maxRangeGapM = LIDAR_WALL_MAX_RANGE_GAP_M,
    maxChordM = LIDAR_WALL_MAX_CHORD_M,
  } = options;

  if (angularGapDeg(a.angleDeg, b.angleDeg) > maxAngleGapDeg) return false;
  if (Math.abs(a.range - b.range) > maxRangeGapM) return false;
  const chord = Math.hypot(a.lx - b.lx, a.ly - b.ly);
  return chord <= maxChordM;
}

/**
 * Group sorted scan returns into wall polylines.
 *
 * @param {Array<{
 *   angleDeg: number;
 *   range: number;
 *   lx: number;
 *   ly: number;
 *   x: number;
 *   y: number;
 *   bodyDistM: number;
 * }>} sortedPoints
 * @param {Parameters<typeof canLinkLidarWallPoints>[2]} [linkOptions]
 * @returns {typeof sortedPoints[]}
 */
export function buildLidarWallSegments(sortedPoints, linkOptions = {}) {
  if (sortedPoints.length === 0) return [];

  const segments = [];
  let current = [sortedPoints[0]];

  for (let i = 1; i < sortedPoints.length; i += 1) {
    const prev = sortedPoints[i - 1];
    const next = sortedPoints[i];
    if (canLinkLidarWallPoints(prev, next, linkOptions)) {
      current.push(next);
    } else {
      segments.push(current);
      current = [next];
    }
  }
  segments.push(current);
  return segments;
}

/**
 * @param {CanvasRenderingContext2D} ctx
 * @param {typeof sortedPoints} segment
 * @param {number} scale
 * @param {number} rangeM
 * @param {{ lineWidth?: number; alpha?: number; glow?: boolean }} [style]
 */
function strokeWallSegment(ctx, segment, scale, rangeM, style = {}) {
  if (segment.length < 2) return;

  const lineWidth = style.lineWidth ?? Math.max(1.4, 2.2 * scale);
  const alpha = style.alpha ?? 0.82;
  const avgRange =
    segment.reduce((sum, p) => sum + p.range, 0) / segment.length;
  const fade = Math.max(0.45, 0.95 - (avgRange / rangeM) * 0.35);
  const minBodyDist = Math.min(...segment.map((p) => p.bodyDistM));
  const proximity = bodyProximityPointColor(minBodyDist);

  ctx.lineWidth = lineWidth;
  if (proximity) {
    ctx.strokeStyle = `${proximity}, ${Math.min(1, alpha * fade + 0.1)})`;
  } else {
    ctx.strokeStyle = `rgba(255, 255, 255, ${alpha * fade})`;
  }

  ctx.beginPath();
  ctx.moveTo(segment[0].x, segment[0].y);
  for (let i = 1; i < segment.length; i += 1) {
    ctx.lineTo(segment[i].x, segment[i].y);
  }
  ctx.stroke();
}

/**
 * Draw LiDAR returns as individual points (no wall line connections).
 *
 * @param {CanvasRenderingContext2D} ctx
 * @param {number} cx
 * @param {number} cy
 * @param {number} maxR
 * @param {number} rangeM
 * @param {number} scale
 * @param {Array<{ x?: number; y?: number; r?: number; a?: number; a_deg?: number }>} points
 * @param {(angleDeg: number) => boolean} isVisible
 */
export function drawLidarWallMerged(
  ctx,
  cx,
  cy,
  maxR,
  rangeM,
  scale,
  points,
  isVisible,
) {
  const plotPoints = [];
  for (const point of points) {
    const { lx, ly, range, angleDeg } = pointToLaserXY(point);
    if (!Number.isFinite(range) || range <= 0) continue;
    if (!Number.isFinite(angleDeg)) continue;
    if (!isVisible(angleDeg)) continue;
    const plot = laserToCanvas(cx, cy, lx, ly, maxR, rangeM);
    plotPoints.push({
      angleDeg,
      range,
      lx,
      ly,
      x: plot.x,
      y: plot.y,
      bodyDistM: distanceToRoverBodyM(lx, ly),
    });
  }

  if (plotPoints.length === 0) return;

  plotPoints.sort((a, b) => a.angleDeg - b.angleDeg);

  ctx.save();

  const capR = Math.max(0.9, 1.15 * scale);
  for (const p of plotPoints) {
    const proximity = bodyProximityPointColor(p.bodyDistM);
    const fade = Math.max(0.5, 0.92 - (p.range / rangeM) * 0.28);
    ctx.fillStyle = proximity
      ? `${proximity}, ${fade})`
      : `rgba(255, 255, 255, ${fade * 0.75})`;
    ctx.beginPath();
    ctx.arc(p.x, p.y, capR, 0, Math.PI * 2);
    ctx.fill();
  }

  ctx.restore();
}
