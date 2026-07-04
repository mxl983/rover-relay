/** @returns {0|1|2|3|4|null} */
export function getWifiLevel(dbm) {
  const val = Number(dbm);
  if (!Number.isFinite(val)) return null;
  if (val > -55) return 4;
  if (val > -65) return 3;
  if (val > -75) return 2;
  if (val > -85) return 1;
  return 0;
}

export function isWifiWeak(dbm) {
  const level = getWifiLevel(dbm);
  return level !== null && level <= 1;
}
