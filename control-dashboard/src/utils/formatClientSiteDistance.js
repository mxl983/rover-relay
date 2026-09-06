/**
 * Proximity label for HUD (browser distance to fixed rover site).
 * @param {number|null|undefined} meters
 * @returns {string|null}
 */
export function formatClientSiteDistance(meters) {
  if (meters === null || meters === undefined || meters === "") return null;
  const m = Number(meters);
  if (!Number.isFinite(m) || m < 0) return null;
  if (m < 100) return "nearby";
  return `${(m / 1000).toFixed(1)} km away`;
}
