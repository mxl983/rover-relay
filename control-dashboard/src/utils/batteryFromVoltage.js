const V_MAX = 12.3;
const V_MIN = 9.0;

/**
 * Map pack voltage (V) to 0–100% using a 9.0V (empty) → 12.3V (full) range.
 * Returns a number or null. Used for realtime WebSocket health display.
 */
export function getBatteryPercentage(voltage) {
  if (voltage === null || voltage === undefined || voltage === "") return null;
  const v = Number(voltage);
  if (!Number.isFinite(v)) return null;
  let percentage = ((v - V_MIN) / (V_MAX - V_MIN)) * 100;
  if (percentage > 100) percentage = 100;
  if (percentage < 0) percentage = 0;
  return Math.round(percentage * 10) / 10;
}
