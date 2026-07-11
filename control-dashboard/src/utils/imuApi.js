import { PI_IMU_ENDPOINT } from "../config";
import { normalizeImuSample } from "./imuData.js";

/**
 * GET /api/sensors/imu — latest sample (REST fallback / bootstrap).
 * @returns {Promise<import("./imuData").ImuSample | null>}
 */
export async function fetchImuSample() {
  const res = await fetch(PI_IMU_ENDPOINT, { cache: "no-store" });
  if (!res.ok) {
    throw new Error(`IMU HTTP ${res.status}`);
  }
  const body = await res.json();
  const raw = body?.data && typeof body.data === "object" ? body.data : body;
  return normalizeImuSample(raw);
}
