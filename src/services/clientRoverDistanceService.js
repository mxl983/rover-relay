import config from "../config.js";
import { distanceMeters, parseCoordinates } from "../utils/geoDistance.js";

/** @type {{ latitude: number, longitude: number, accuracy: number|null, updatedAt: string } | null} */
let lastClientLocation = null;

export function resetClientLocationForTests() {
  lastClientLocation = null;
}

function roverCoordinates() {
  const lat = config.rover.location.latitude;
  const lon = config.rover.location.longitude;
  if (!Number.isFinite(lat) || !Number.isFinite(lon)) return null;
  return { latitude: lat, longitude: lon };
}

/**
 * @param {{ latitude: number, longitude: number, accuracy?: number|null }} coords
 * @returns {{ distanceMeters: number, updatedAt: string } | null}
 */
export function recordClientLocation(coords) {
  const client = parseCoordinates(coords.latitude, coords.longitude);
  if (!client) return null;

  const accuracy =
    coords.accuracy != null && Number.isFinite(Number(coords.accuracy))
      ? Number(coords.accuracy)
      : null;

  const rover = roverCoordinates();
  if (!rover) return null;
  const distance = distanceMeters(client, rover);
  if (distance == null || !Number.isFinite(distance)) return null;

  const updatedAt = new Date().toISOString();
  lastClientLocation = {
    latitude: client.latitude,
    longitude: client.longitude,
    accuracy,
    updatedAt,
  };

  const distanceMetersRounded = Math.round(distance * 10) / 10;

  return {
    distanceMeters: distanceMetersRounded,
    updatedAt,
  };
}

/**
 * One-shot distance without updating the cached client location.
 * @param {{ latitude: number, longitude: number }} coords
 */
export function computeClientDistance(coords) {
  const client = parseCoordinates(coords.latitude, coords.longitude);
  if (!client) return null;
  const rover = roverCoordinates();
  if (!rover) return null;
  const distance = distanceMeters(client, rover);
  if (distance == null || !Number.isFinite(distance)) return null;
  return {
    distanceMeters: Math.round(distance * 10) / 10,
  };
}

/**
 * Public snapshot for /state and WebSocket — distance only (rover coords stay server-side).
 */
export function getClientLocationSnapshot() {
  if (!lastClientLocation) return null;
  const rover = roverCoordinates();
  if (!rover) return null;
  const distance = distanceMeters(lastClientLocation, rover);
  if (distance == null || !Number.isFinite(distance)) return null;
  return {
    distanceMeters: Math.round(distance * 10) / 10,
    updatedAt: lastClientLocation.updatedAt,
  };
}
