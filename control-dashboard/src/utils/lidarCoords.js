/** Max range shown on the minimap (meters). */
export const LIDAR_DISPLAY_RANGE_M = 6;
export const LIDAR_MINIMAP_ARC_DEG = 270;
export const LIDAR_MINIMAP_REAR_CENTER_DEG = 270;
/** Driving-forward bearing in laser degrees. */
export const LIDAR_FORWARD_DEG = 90;
/** Total spread of the forward view cone indicator (two reference lines). */
export const LIDAR_VIEW_CONE_DEG = 60;
/** Points within this distance (m) of the rover body get yellow→red coloring. */
export const LIDAR_BODY_PROXIMITY_M = 0.3;
/** Scan-frame bearing for driving forward (0° = +x). */
export const LIDAR_DRIVE_FORWARD_DEG = 0;
export const ROVER_BODY_LENGTH_M = 0.305;
export const ROVER_BODY_WIDTH_M = 0.26;

export function normalizeDeg(deg) {
  if (!Number.isFinite(deg)) return null;
  return ((deg % 360) + 360) % 360;
}

/**
 * @param {number|null|undefined} angleDeg
 * @param {number} [displayArcDeg]
 * @param {number} [rearCenterDeg]
 */
export function isAngleInDisplayArc(
  angleDeg,
  displayArcDeg = LIDAR_MINIMAP_ARC_DEG,
  rearCenterDeg = LIDAR_MINIMAP_REAR_CENTER_DEG,
) {
  if (displayArcDeg >= 360) return true;
  const n = normalizeDeg(angleDeg);
  if (!Number.isFinite(n)) return true;
  const halfHidden = (360 - displayArcDeg) / 2;
  let delta = Math.abs(n - rearCenterDeg);
  if (delta > 180) delta = 360 - delta;
  return delta >= halfHidden;
}

/**
 * Map ROS laser-frame meters to canvas pixels.
 * Laser REP-103: +x forward, +y left. Display: 90° = up (driving view), 0° = right.
 *
 * @param {number} cx
 * @param {number} cy
 * @param {number} lx forward (m)
 * @param {number} ly left (m)
 * @param {number} maxRadiusPx
 * @param {number} [rangeM]
 */
export function laserToCanvas(cx, cy, lx, ly, maxRadiusPx, rangeM = LIDAR_DISPLAY_RANGE_M) {
  const scale = maxRadiusPx / rangeM;
  return {
    x: cx + lx * scale,
    y: cy - ly * scale,
  };
}

/**
 * @param {number} cx
 * @param {number} cy
 * @param {number} angleDeg laser bearing (0° = +x, 90° = +y)
 * @param {number} maxRadiusPx
 * @param {number} [rangeM]
 */
export function laserBearingToCanvas(cx, cy, angleDeg, maxRadiusPx, rangeM = LIDAR_DISPLAY_RANGE_M) {
  const rad = (angleDeg * Math.PI) / 180;
  return laserToCanvas(
    cx,
    cy,
    Math.cos(rad) * rangeM,
    Math.sin(rad) * rangeM,
    maxRadiusPx,
    rangeM,
  );
}

/**
 * Map a data bearing (0° = +x, 90° = +y) to canvas pixels at a fixed radius.
 * @param {number} cx
 * @param {number} cy
 * @param {number} angleDeg scan `a_deg` / atan2(y, x) bearing
 * @param {number} radiusPx
 */
export function bearingToCanvasPx(cx, cy, angleDeg, radiusPx) {
  const rad = (angleDeg * Math.PI) / 180;
  return {
    x: cx + Math.cos(rad) * radiusPx,
    y: cy - Math.sin(rad) * radiusPx,
  };
}

/**
 * Angles drawn on the minimap rim (data frame, same as point `a_deg`).
 * @returns {{ cardinal: number[]; minor: number[] }}
 */
export function lidarMinimapMarkedAnglesDeg() {
  return {
    cardinal: [0, 90, 180, 270],
    minor: [60, 120, 240, 300],
  };
}

/**
 * Edge bearings for the forward view cone (two lines from center).
 * Uses the hidden-sector width so the V opens toward the view heading (not backward).
 * @param {number} [viewHeadingDeg] camera pan / forward bearing in laser degrees
 * @param {number} [coneDeg]
 */
export function forwardViewConeEdgesDeg(
  viewHeadingDeg = LIDAR_FORWARD_DEG,
  coneDeg = LIDAR_VIEW_CONE_DEG,
) {
  const halfSpread = coneDeg / 2;
  return {
    edgeADeg: normalizeDeg(viewHeadingDeg - halfSpread),
    edgeBDeg: normalizeDeg(viewHeadingDeg + halfSpread),
  };
}

/**
 * @param {number|null|undefined} panDeg gimbal pan from rover telemetry (90° = straight ahead)
 */
export function viewHeadingFromPan(panDeg) {
  return Number.isFinite(panDeg) ? normalizeDeg(panDeg) : LIDAR_FORWARD_DEG;
}

/**
 * Whether a laser bearing lies inside the forward view cone (half-spread each side of heading).
 * @param {number} angleDeg
 * @param {number} [forwardDeg]
 * @param {number} [coneDeg]
 */
export function isAngleInForwardCone(
  angleDeg,
  forwardDeg = LIDAR_DRIVE_FORWARD_DEG,
  coneDeg = LIDAR_VIEW_CONE_DEG,
) {
  return isAngleInArc(angleDeg, forwardDeg, coneDeg);
}

/**
 * Whether a bearing lies within a circular arc centered on `centerDeg`.
 * @param {number} angleDeg
 * @param {number} centerDeg
 * @param {number} spreadDeg total arc width in degrees
 */
export function isAngleInArc(angleDeg, centerDeg, spreadDeg) {
  const n = normalizeDeg(angleDeg);
  const c = normalizeDeg(centerDeg);
  if (!Number.isFinite(n) || !Number.isFinite(c) || !Number.isFinite(spreadDeg)) return false;
  let delta = Math.abs(n - c);
  if (delta > 180) delta = 360 - delta;
  return delta <= spreadDeg / 2;
}

/**
 * @param {number} lengthM
 * @param {number} widthM
 */
export function roverBodyBoundsM(lengthM, widthM) {
  const halfW = widthM / 2;
  return { minX: -halfW, maxX: halfW, minY: -lengthM, maxY: 0 };
}

/**
 * Rover footprint corners in laser meters (26 cm wide × 30.5 cm long).
 * @param {number} [lengthM]
 * @param {number} [widthM]
 * @returns {[number, number][]}
 */
export function roverBodyFootprintCornersM(
  lengthM = ROVER_BODY_LENGTH_M,
  widthM = ROVER_BODY_WIDTH_M,
) {
  const halfW = widthM / 2;
  return [
    [-halfW, 0],
    [halfW, 0],
    [halfW, -lengthM],
    [-halfW, -lengthM],
  ];
}

/**
 * @param {number} px
 * @param {number} py
 * @param {number} minX
 * @param {number} maxX
 * @param {number} minY
 * @param {number} maxY
 */
export function distancePointToAxisAlignedRectM(px, py, minX, maxX, minY, maxY) {
  const dx = Math.max(minX - px, 0, px - maxX);
  const dy = Math.max(minY - py, 0, py - maxY);
  return Math.hypot(dx, dy);
}

/**
 * @param {number} lx laser x (m)
 * @param {number} ly laser y (m)
 * @param {number} [lengthM]
 * @param {number} [widthM]
 */
export function distanceToRoverBodyM(
  lx,
  ly,
  lengthM = ROVER_BODY_LENGTH_M,
  widthM = ROVER_BODY_WIDTH_M,
) {
  const { minX, maxX, minY, maxY } = roverBodyBoundsM(lengthM, widthM);
  return distancePointToAxisAlignedRectM(lx, ly, minX, maxX, minY, maxY);
}

/**
 * Yellow at the threshold edge, red at the body. Returns null when beyond threshold.
 * @param {number} distM
 * @param {number} [thresholdM]
 * @returns {string|null} `rgba(r,g,b` prefix (alpha appended by caller)
 */
export function bodyProximityPointColor(distM, thresholdM = LIDAR_BODY_PROXIMITY_M) {
  if (!Number.isFinite(distM) || distM >= thresholdM) return null;
  const t = Math.max(0, Math.min(1, 1 - distM / thresholdM));
  const g = Math.round(220 - 160 * t);
  const b = Math.round(80 - 40 * t);
  return `rgba(255, ${g}, ${b}`;
}

/**
 * @param {Array<{ x?: number; y?: number; r?: number; a?: number; a_deg?: number }>} points
 * @param {{
 *   lengthM?: number;
 *   widthM?: number;
 *   displayArcDeg?: number;
 *   rearCenterDeg?: number;
 * }} [options]
 * @returns {number|null} minimum distance to body (m), or null if no valid points
 */
export function minBodyProximityFromPoints(points, options = {}) {
  const {
    lengthM = ROVER_BODY_LENGTH_M,
    widthM = ROVER_BODY_WIDTH_M,
    displayArcDeg = 360,
    rearCenterDeg = LIDAR_MINIMAP_REAR_CENTER_DEG,
  } = options;
  let min = Infinity;
  for (const point of points ?? []) {
    const { lx, ly, range, angleDeg } = pointToLaserXY(point);
    if (!Number.isFinite(range) || range <= 0) continue;
    if (!isAngleInDisplayArc(angleDeg, displayArcDeg, rearCenterDeg)) continue;
    const d = distanceToRoverBodyM(lx, ly, lengthM, widthM);
    if (d < min) min = d;
  }
  return Number.isFinite(min) ? min : null;
}

/**
 * @param {{ x?: number; y?: number; r?: number; a?: number; a_deg?: number }} point
 * @returns {number|null} bearing in degrees (ROS laser frame, 0° = forward)
 */
export function pointAngleDeg(point) {
  if (Number.isFinite(point.a_deg)) return point.a_deg;
  if (Number.isFinite(point.a)) return (point.a * 180) / Math.PI;
  if (Number.isFinite(point.x) && Number.isFinite(point.y)) {
    return (Math.atan2(point.y, point.x) * 180) / Math.PI;
  }
  return null;
}

/**
 * @param {{ x: number; y: number; r?: number; a?: number; a_deg?: number }} point
 * @returns {{ lx: number; ly: number; range: number; angleDeg: number|null }}
 */
export function pointToLaserXY(point) {
  if (Number.isFinite(point.x) && Number.isFinite(point.y)) {
    const range = Math.hypot(point.x, point.y);
    return {
      lx: point.x,
      ly: point.y,
      range,
      angleDeg: pointAngleDeg(point),
    };
  }
  const range = point.r ?? 0;
  const angle = point.a ?? 0;
  return {
    lx: range * Math.cos(angle),
    ly: range * Math.sin(angle),
    range,
    angleDeg: pointAngleDeg(point),
  };
}

/**
 * @param {Array<{ x?: number; y?: number; r?: number; a?: number; a_deg?: number }>} points
 * @param {{ displayArcDeg?: number; rearCenterDeg?: number }} [options]
 */
export function nearestPointWithAngle(points, options = {}) {
  const {
    displayArcDeg = 360,
    rearCenterDeg = LIDAR_MINIMAP_REAR_CENTER_DEG,
  } = options;
  let best = null;
  for (const point of points) {
    const { range, angleDeg } = pointToLaserXY(point);
    if (!isAngleInDisplayArc(angleDeg, displayArcDeg, rearCenterDeg)) continue;
    if (!Number.isFinite(range) || range <= 0) continue;
    if (!best || range < best.range) {
      best = { range, angleDeg };
    }
  }
  return best;
}
