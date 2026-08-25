/** Letter mark ids: A, B, C, … Z, AA, … */
export function letterFromIndex(index) {
  let n = Math.max(0, index);
  let s = "";
  do {
    s = String.fromCharCode(65 + (n % 26)) + s;
    n = Math.floor(n / 26) - 1;
  } while (n >= 0);
  return s;
}

/** Human-readable mark label for UI. */
export function waypointDisplayLabel(wp, index = 0) {
  const label = String(wp?.label || "").trim();
  if (/^[A-Z]+$/i.test(label)) return label.toUpperCase();
  return letterFromIndex(index);
}

/** Compact button text — single letters fit as-is. */
export function waypointCompactLabel(wp, index = 0) {
  return waypointDisplayLabel(wp, index);
}
