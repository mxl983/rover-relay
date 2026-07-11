/**
 * Normalize Pi IMU payloads (REST or WebSocket IMU_UPDATE).
 *
 * Robot frame (flat-floor driving):
 * - accel.x → lateral (left/right)
 * - accel.y → forward/back (chip reports negative when accelerating forward)
 * - accel.z → up/down (ignored for drive-plane G / collision heuristics)
 *
 * SLAM integration ideas (server-side or future dashboard fusion):
 * - gyro.z (yaw rate) → deskew LiDAR scans between stamps; integrate for short-term heading
 * - accel → detect bumps / tilt; pause map updates when motion is chaotic
 * - stamp + seq → align IMU samples with relay.lidar.scan frames for motion compensation
 * - aux magnetometer → long-term yaw drift correction when fused with gyro
 * - fuse IMU yaw delta with pan gimbal + wheel odometry for minimap view heading
 */

const STALE_MS = 500;

/**
 * @param {unknown} raw
 * @returns {import("./imuData").ImuSample | null}
 */
export function normalizeImuSample(raw) {
  if (!raw || typeof raw !== "object") return null;

  const stamp = Number(raw.stamp);
  const seq = Number(raw.seq);
  const accel = normalizeAxisTriple(raw.accel, "g");
  const gyro = normalizeAxisTriple(raw.gyro, "rad_s");
  if (!accel || !gyro) return null;

  const aux = normalizeAxisTriple(raw.aux, null);
  const connected = raw.connected !== false;

  return {
    stamp: Number.isFinite(stamp) ? stamp : null,
    seq: Number.isFinite(seq) ? seq : null,
    connected,
    accel,
    gyro,
    aux,
    receivedAt: Date.now(),
  };
}

function normalizeAxisTriple(obj, unit) {
  if (!obj || typeof obj !== "object") return null;
  const x = Number(obj.x);
  const y = Number(obj.y);
  const z = Number(obj.z);
  if (![x, y, z].every(Number.isFinite)) return null;
  return {
    x,
    y,
    z,
    ...(unit ? { unit } : {}),
  };
}

/** @param {import("./imuData").ImuSample | null} sample */
export function isImuLive(sample) {
  if (!sample?.connected) return false;
  const age = Date.now() - (sample.receivedAt ?? 0);
  return age <= STALE_MS;
}

/** Yaw rate in rad/s (robot frame, +z = CCW). */
export function readYawRateRadS(sample) {
  return Number(sample?.gyro?.z) || 0;
}

/** Integrate yaw between two samples using gyro.z and stamp delta (rad). */
export function integrateYawRad(prev, next) {
  if (!prev || !next) return 0;
  const dt =
    Number.isFinite(prev.stamp) && Number.isFinite(next.stamp)
      ? Math.max(0, next.stamp - prev.stamp)
      : Math.max(0, (next.receivedAt - prev.receivedAt) / 1000);
  if (dt <= 0) return 0;
  const rate = (readYawRateRadS(prev) + readYawRateRadS(next)) / 2;
  return rate * dt;
}

/** Drive-plane acceleration magnitude in g (x=lateral, y=forward/back; z ignored). */
export function horizontalAccelG(sample) {
  const ax = Number(sample?.accel?.x) || 0;
  const ay = Number(sample?.accel?.y) || 0;
  return Math.hypot(ax, ay);
}

/** Lateral acceleration in g (IMU x-axis). */
export function lateralAccelG(sample) {
  return Number(sample?.accel?.x) || 0;
}

/** Forward/back acceleration in g (IMU y-axis; + = forward, − = back). */
export function forwardAccelG(sample) {
  return Number(sample?.accel?.y) || 0;
}

/**
 * @param {import("./imuData").ImuSample} sample
 * @param {number} [thresholdG]
 */
export function isLikelyMoving(sample, thresholdG = 0.08) {
  return Math.abs(forwardAccelG(sample)) > thresholdG;
}
