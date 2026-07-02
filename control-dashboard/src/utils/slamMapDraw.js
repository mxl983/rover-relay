import { laserToCanvas } from "./lidarCoords";

const MAP_POINT_COLOR = "rgba(130, 140, 155, 0.72)";

/**
 * Draw memorized SLAM points in the rover frame (server transforms world → robot).
 * @param {CanvasRenderingContext2D} ctx
 * @param {number} cx
 * @param {number} cy
 * @param {number} maxR
 * @param {number} rangeM
 * @param {Array<{ x?: number; y?: number }>|null|undefined} mapPoints
 * @param {number} scale
 */
export function drawSlamMapPoints(ctx, cx, cy, maxR, rangeM, mapPoints, scale) {
  if (!mapPoints?.length) return;

  const pointR = Math.max(0.75, 1 * scale);
  ctx.fillStyle = MAP_POINT_COLOR;

  for (const point of mapPoints) {
    const lx = point.x ?? 0;
    const ly = point.y ?? 0;
    const plot = laserToCanvas(cx, cy, lx, ly, maxR, rangeM);
    ctx.beginPath();
    ctx.arc(plot.x, plot.y, pointR, 0, Math.PI * 2);
    ctx.fill();
  }
}
