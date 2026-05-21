const EARTH_RADIUS_M = 6_371_000;

function toRad(deg) {
  return (deg * Math.PI) / 180;
}

/**
 * Haversine distance in meters between two WGS84 points.
 * @param {{ latitude: number, longitude: number }} a
 * @param {{ latitude: number, longitude: number }} b
 * @returns {number|null}
 */
export function distanceMeters(a, b) {
  const lat1 = Number(a?.latitude);
  const lon1 = Number(a?.longitude);
  const lat2 = Number(b?.latitude);
  const lon2 = Number(b?.longitude);
  if (![lat1, lon1, lat2, lon2].every(Number.isFinite)) return null;

  const dLat = toRad(lat2 - lat1);
  const dLon = toRad(lon2 - lon1);
  const s1 = Math.sin(dLat / 2);
  const s2 = Math.sin(dLon / 2);
  const h =
    s1 * s1 + Math.cos(toRad(lat1)) * Math.cos(toRad(lat2)) * s2 * s2;
  const c = 2 * Math.atan2(Math.sqrt(h), Math.sqrt(1 - h));
  return EARTH_RADIUS_M * c;
}

/**
 * @param {unknown} latitude
 * @param {unknown} longitude
 * @returns {{ latitude: number, longitude: number } | null}
 */
export function parseCoordinates(latitude, longitude) {
  const lat = Number(latitude);
  const lon = Number(longitude);
  if (!Number.isFinite(lat) || !Number.isFinite(lon)) return null;
  if (lat < -90 || lat > 90 || lon < -180 || lon > 180) return null;
  return { latitude: lat, longitude: lon };
}
