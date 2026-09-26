import { useEffect, useState } from "react";

/**
 * Haversine distance in meters between two WGS84 points.
 * @param {number} lat1
 * @param {number} lon1
 * @param {number} lat2
 * @param {number} lon2
 */
export function haversineMeters(lat1, lon1, lat2, lon2) {
  const toRad = (d) => (d * Math.PI) / 180;
  const φ1 = toRad(lat1);
  const φ2 = toRad(lat2);
  const Δφ = toRad(lat2 - lat1);
  const Δλ = toRad(lon2 - lon1);
  const a =
    Math.sin(Δφ / 2) * Math.sin(Δφ / 2) +
    Math.cos(φ1) * Math.cos(φ2) * Math.sin(Δλ / 2) * Math.sin(Δλ / 2);
  const c = 2 * Math.atan2(Math.sqrt(a), Math.sqrt(1 - a));
  return 6371e3 * c;
}

/**
 * Watch browser geolocation and report distance (m) to a fixed rover point.
 * Returns null while permission is denied / unavailable / rover coords missing.
 *
 * @param {{ lat: number | null, lon: number | null, enabled?: boolean }} rover
 * @returns {number | null}
 */
export function useClientToRoverDistance(rover) {
  const [distanceM, setDistanceM] = useState(null);
  const lat = rover?.lat;
  const lon = rover?.lon;
  const enabled = rover?.enabled !== false;
  const configured =
    enabled &&
    Number.isFinite(lat) &&
    Number.isFinite(lon) &&
    Math.abs(lat) <= 90 &&
    Math.abs(lon) <= 180;

  useEffect(() => {
    if (!configured) {
      setDistanceM(null);
      return undefined;
    }
    if (typeof navigator === "undefined" || !navigator.geolocation) {
      setDistanceM(null);
      return undefined;
    }

    let cancelled = false;
    const onPos = (pos) => {
      if (cancelled) return;
      const clat = pos?.coords?.latitude;
      const clon = pos?.coords?.longitude;
      if (!Number.isFinite(clat) || !Number.isFinite(clon)) return;
      setDistanceM(haversineMeters(clat, clon, lat, lon));
    };
    const onErr = () => {
      if (!cancelled) setDistanceM(null);
    };

    const watchId = navigator.geolocation.watchPosition(onPos, onErr, {
      enableHighAccuracy: true,
      maximumAge: 15_000,
      timeout: 20_000,
    });

    return () => {
      cancelled = true;
      try {
        navigator.geolocation.clearWatch(watchId);
      } catch {
        /* ignore */
      }
    };
  }, [configured, lat, lon]);

  return configured ? distanceM : null;
}
