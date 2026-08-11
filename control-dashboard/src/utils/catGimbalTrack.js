/**
 * Map cat bbox (normalized 0–1) → gimbal velocity {-1..1} to center the subject.
 * Commands are rate-style (same as sticks), not absolute pan/tilt degrees.
 *
 * Settling uses hysteresis so we stop near center and do not endlessly hunt:
 *   - settle when offset radius < SETTLE_RADIUS
 *   - resume only when offset radius > RESUME_RADIUS
 */

/** Stop adjusting once cat center is within this fraction of frame from image center. */
export const CAT_GIMBAL_SETTLE_RADIUS = 0.09;
/** After settled, only start adjusting again if offset exceeds this (hysteresis). */
export const CAT_GIMBAL_RESUME_RADIUS = 0.15;

/** @deprecated alias — per-axis soft zero near settle band */
export const CAT_GIMBAL_DEADZONE = CAT_GIMBAL_SETTLE_RADIUS;

/** Gentle but still enough to move servos (was over-damped at 0.055). */
export const CAT_GIMBAL_GAIN = 0.45;
export const CAT_GIMBAL_MAX = 0.14;
/** EMA toward the raw command each tick (lower = softer). */
export const CAT_GIMBAL_SMOOTH_UP = 0.4;
/** EMA when coasting back to zero. */
export const CAT_GIMBAL_SMOOTH_DOWN = 0.22;
/**
 * Apply non-zero slew this long after a *meaningful* bbox move, then coast.
 * Prevents overshooting while waiting for the next detect.
 */
export const CAT_GIMBAL_PULSE_MS = 280;
/**
 * Ignore bbox center jitter smaller than this (fraction of frame).
 * Stops re-pulsing on every ~15 Hz vision tick when the box is stable.
 */
export const CAT_GIMBAL_MOVE_EPS = 0.02;
/** Skip re-sending gimbal if command changed by less than this. */
export const CAT_GIMBAL_CMD_EPS = 0.015;

/** +1: cat right of center → pan right; flip if hardware is inverted. */
export const CAT_GIMBAL_SIGN_X = 1;
/** +1: cat below center → tilt down; flip if hardware is inverted. */
export const CAT_GIMBAL_SIGN_Y = 1;

function clamp(v, lo, hi) {
  return Math.max(lo, Math.min(hi, v));
}

/**
 * Normalized offset of bbox center from frame center.
 * @returns {{ errX: number, errY: number, radius: number, cx: number, cy: number }|null}
 */
export function getCatOffset(cat) {
  const box = cat?.bbox_norm;
  if (!cat?.cat_present || !Array.isArray(box) || box.length !== 4) {
    return null;
  }
  const [x1, y1, x2, y2] = box.map(Number);
  if (![x1, y1, x2, y2].every(Number.isFinite)) return null;
  const cx = (x1 + x2) / 2;
  const cy = (y1 + y2) / 2;
  const errX = cx - 0.5;
  const errY = cy - 0.5;
  return { errX, errY, radius: Math.hypot(errX, errY), cx, cy };
}

/**
 * True when bbox center moved enough to warrant a new slew pulse.
 * @param {{cx:number,cy:number}|null} prev
 * @param {{cx:number,cy:number}|null} next
 * @param {number} [eps]
 */
export function bboxMovedEnough(prev, next, eps = CAT_GIMBAL_MOVE_EPS) {
  if (!next) return false;
  if (!prev) return true;
  return Math.hypot(next.cx - prev.cx, next.cy - prev.cy) >= eps;
}

/**
 * @param {{x?:number,y?:number}|null} a
 * @param {{x?:number,y?:number}|null} b
 * @param {number} [eps]
 */
export function gimbalCommandsEqual(a, b, eps = CAT_GIMBAL_CMD_EPS) {
  const ax = Number(a?.x) || 0;
  const ay = Number(a?.y) || 0;
  const bx = Number(b?.x) || 0;
  const by = Number(b?.y) || 0;
  return Math.hypot(ax - bx, ay - by) < eps;
}

/**
 * Hysteresis for "centering complete".
 * @param {boolean} wasSettled
 * @param {number} radius
 * @param {number} [settleRadius]
 * @param {number} [resumeRadius]
 */
export function nextCenteredSettled(
  wasSettled,
  radius,
  settleRadius = CAT_GIMBAL_SETTLE_RADIUS,
  resumeRadius = CAT_GIMBAL_RESUME_RADIUS,
) {
  if (!Number.isFinite(radius)) return false;
  if (wasSettled) {
    return radius <= resumeRadius;
  }
  return radius <= settleRadius;
}

/**
 * @param {{ cat_present?: boolean, bbox_norm?: number[]|null }|null|undefined} cat
 * @param {{
 *   deadzone?: number,
 *   gain?: number,
 *   max?: number,
 *   signX?: number,
 *   signY?: number,
 *   settled?: boolean,
 * }} [opts]
 * @returns {{ x: number, y: number }}
 */
export function computeCatGimbalCommand(cat, opts = {}) {
  const gain = opts.gain ?? CAT_GIMBAL_GAIN;
  const max = opts.max ?? CAT_GIMBAL_MAX;
  const signX = opts.signX ?? CAT_GIMBAL_SIGN_X;
  const signY = opts.signY ?? CAT_GIMBAL_SIGN_Y;

  if (opts.settled) {
    return { x: 0, y: 0 };
  }

  const offset = getCatOffset(cat);
  if (!offset) {
    return { x: 0, y: 0 };
  }

  // Soft per-axis zero inside settle band (avoids tiny axis chatter).
  const deadzone = opts.deadzone ?? CAT_GIMBAL_SETTLE_RADIUS * 0.65;
  let { errX, errY } = offset;
  if (Math.abs(errX) < deadzone) errX = 0;
  if (Math.abs(errY) < deadzone) errY = 0;

  return {
    x: clamp(errX * gain * signX, -max, max),
    y: clamp(errY * gain * signY, -max, max),
  };
}

/**
 * Soften step-to-step jumps. Uses a faster rise / slower fall by default.
 * @param {{x:number,y:number}} prev
 * @param {{x:number,y:number}} next
 * @param {number} [alpha]
 */
export function smoothGimbalCommand(prev, next, alpha) {
  const prevMag = Math.hypot(prev?.x ?? 0, prev?.y ?? 0);
  const nextMag = Math.hypot(next?.x ?? 0, next?.y ?? 0);
  const a = clamp(
    alpha ?? (nextMag >= prevMag ? CAT_GIMBAL_SMOOTH_UP : CAT_GIMBAL_SMOOTH_DOWN),
    0,
    1,
  );
  return {
    x: (prev?.x ?? 0) * (1 - a) + (next?.x ?? 0) * a,
    y: (prev?.y ?? 0) * (1 - a) + (next?.y ?? 0) * a,
  };
}

/** Coarse key for UI dedupe (2 decimals ≈ 0.5% of frame). */
export function bboxKey(cat) {
  const box = cat?.bbox_norm;
  if (!cat?.cat_present || !Array.isArray(box) || box.length !== 4) return "";
  return box.map((v) => Number(v).toFixed(2)).join(",");
}

export function isGimbalActive(gimbal, eps = 1e-4) {
  if (!gimbal) return false;
  return Math.hypot(Number(gimbal.x) || 0, Number(gimbal.y) || 0) > eps;
}
