/**
 * Degraded pack: stretch reported SOC to usable capacity.
 * Reported 40% → 0%, 60% → 20%, 100% → 100%; linear between anchors.
 * @param {number|string|null|undefined} reported
 * @returns {number|null}
 */
export function remapReportedBatteryPct(reported) {
  if (reported === null || reported === undefined || reported === "") return null;
  const n = Number(reported);
  if (!Number.isFinite(n)) return null;
  if (n >= 100) return 100;
  if (n <= 40) return 0;
  if (n >= 60) return 20 + ((n - 60) * 80) / 40;
  return n - 40;
}

/**
 * @param {number|string|null|undefined} reported
 * @param {number} [decimals=1]
 * @returns {number|null}
 */
export function remapReportedBatteryPctRounded(reported, decimals = 1) {
  const v = remapReportedBatteryPct(reported);
  if (v == null) return null;
  const f = 10 ** decimals;
  return Math.round(v * f) / f;
}
