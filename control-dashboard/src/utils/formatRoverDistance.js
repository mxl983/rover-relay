/**
 * Proximity label for HUD (distance to fixed rover site).
 * @param {number|null|undefined} meters
 * @returns {string|null}
 */
export function formatRoverDistance(meters) {
  if (meters === null || meters === undefined || meters === "") return null;
  const m = Number(meters);
  if (!Number.isFinite(m) || m < 0) return null;
  if (m < 500) return "near rover";
  return `${(m / 1000).toFixed(1)} km away`;
}
