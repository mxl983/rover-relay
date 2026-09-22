/** Pack empty / full for the current ~12 V (3S) battery. */
export const BATTERY_V_MIN = 9.6;
export const BATTERY_V_MAX = 12.6;

/**
 * Map pack voltage (V) to 0–100% using a 9.6V (empty) → 12.6V (full) range.
 * Returns a number or null. Prefer this over vendor % when millivolts are known.
 */
export function getBatteryPercentage(voltage) {
  if (voltage === null || voltage === undefined || voltage === "") return null;
  const v = Number(voltage);
  if (!Number.isFinite(v)) return null;
  let percentage = ((v - BATTERY_V_MIN) / (BATTERY_V_MAX - BATTERY_V_MIN)) * 100;
  if (percentage > 100) percentage = 100;
  if (percentage < 0) percentage = 0;
  return Math.round(percentage * 10) / 10;
}
