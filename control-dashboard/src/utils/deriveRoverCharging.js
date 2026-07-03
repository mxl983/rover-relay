/**
 * @param {object | null | undefined} rover relay `rover` snapshot (WS or /api/rover/state)
 */
export function deriveRoverCharging(rover) {
  if (!rover || typeof rover !== "object") return false;
  if (rover.charging?.isCharging === true) return true;
  if (rover.charging?.isCharging === false) return false;
  if (rover.lastCharging?.event === "charging_start") return true;
  if (rover.lastCharging?.event === "charging_end") return false;
  const drain = Number(rover.battery?.drainPctPerMinute);
  if (Number.isFinite(drain) && drain < -0.3) return true;
  return false;
}
