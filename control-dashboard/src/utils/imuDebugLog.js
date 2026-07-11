import { forwardAccelG, horizontalAccelG, readYawRateRadS } from "./imuData.js";

const MIN_LOG_INTERVAL_MS = 500;
let lastLogAt = 0;
let lastSeq = null;

/**
 * Throttled console log for IMU debugging (~2 Hz max).
 * @param {import("./imuData").ImuSample} sample
 */
export function logImuDebug(sample) {
  if (!sample) return;
  const now = Date.now();
  if (sample.seq === lastSeq && now - lastLogAt < MIN_LOG_INTERVAL_MS) return;
  if (now - lastLogAt < MIN_LOG_INTERVAL_MS) return;

  lastLogAt = now;
  lastSeq = sample.seq;

  const ax = sample.accel.x.toFixed(3);
  const ay = sample.accel.y.toFixed(3);
  const az = sample.accel.z.toFixed(3);
  const gx = sample.gyro.x.toFixed(3);
  const gy = sample.gyro.y.toFixed(3);
  const gz = sample.gyro.z.toFixed(3);
  const yawDegS = ((readYawRateRadS(sample) * 180) / Math.PI).toFixed(1);
  const horizG = horizontalAccelG(sample).toFixed(3);
  const fwdG = forwardAccelG(sample).toFixed(3);

  // eslint-disable-next-line no-console
  console.log(
    `[imu] seq=${sample.seq ?? "—"} stamp=${sample.stamp?.toFixed(2) ?? "—"} ` +
      `accel_g=(x=${ax},y=${ay},z=${az}) gyro_rad_s=(${gx},${gy},${gz}) ` +
      `yaw=${yawDegS}°/s plane=${horizG}g fwd_y=${fwdG}g connected=${sample.connected}`,
  );
}

export function resetImuDebugLog() {
  lastLogAt = 0;
  lastSeq = null;
}
