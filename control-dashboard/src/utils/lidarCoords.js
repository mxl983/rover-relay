/** Max range shown on the minimap (meters). */
export const LIDAR_DISPLAY_RANGE_M = 6;

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
 */
export function nearestPointWithAngle(points) {
  let best = null;
  for (const point of points) {
    const { range, angleDeg } = pointToLaserXY(point);
    if (!best || range < best.range) {
      best = { range, angleDeg };
    }
  }
  return best;
}
