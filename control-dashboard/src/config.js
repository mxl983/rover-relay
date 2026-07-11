/**
 * Central config derived from environment.
 * Vite exposes only VITE_* variables to the client; use .env / .env.local for overrides.
 */
const PI_SERVER_IP =
  import.meta.env.VITE_PI_SERVER_IP ?? "rover.tail9d0237.ts.net";
const MQTT_HOST =
  import.meta.env.VITE_MQTT_HOST ??
  "wss://84f09906a62e42c78c5d9b0555aa71f1.s1.eu.hivemq.cloud:8884/mqtt";
const RELAY_BASE_URL =
  import.meta.env.VITE_RELAY_BASE_URL ?? "https://jjcloud.tail9d0237.ts.net";

/** Camera API secret – optional; if unset, camera endpoints may reject or use server-side default. */
export const CAMERA_SECRET = import.meta.env.VITE_CAMERA_SECRET ?? "";

export { PI_SERVER_IP, MQTT_HOST, RELAY_BASE_URL };

export const AUDIO_STREAM_HOST = `https://${PI_SERVER_IP}:8889/mic/whep`;
export const VIDEO_STREAM_HOST = `https://${PI_SERVER_IP}:8889/cam/whep`;
export const AUDIO_TALK_HOST = `https://${PI_SERVER_IP}:8889/talk/whip`;

export const PI_CONTROL_ENDPOINT = `https://${PI_SERVER_IP}:3000/api/control/drive`;
export const PI_SYSTEM_ENDPOINT = `https://${PI_SERVER_IP}:3000/api/system`;
export const PI_CAMERA_ENDPOINT = `https://${PI_SERVER_IP}:3000/api/camera`;
export const PI_VOICE_ENDPOINT = `https://${PI_SERVER_IP}:3000/api/voice/interpret`;
/** Web Speech API BCP-47 tag. Set VITE_VOICE_RECOGNITION_LANG=en-US for English questions; default follows browser (zh→zh-CN). */
export const VOICE_RECOGNITION_LANG =
  import.meta.env.VITE_VOICE_RECOGNITION_LANG ||
  (typeof navigator !== "undefined" && /^zh/i.test(navigator.language || "")
    ? "zh-CN"
    : "en-US");
/** Live mode can force a dedicated ASR language; default zh-CN for Chinese conversational use. */
export const VOICE_RECOGNITION_LIVE_LANG =
  import.meta.env.VITE_VOICE_RECOGNITION_LIVE_LANG || "zh-CN";
export const PI_WEBSOCKET = `wss://${PI_SERVER_IP}:3000`;

export const PI_HI_RES_CAPTURE_ENDPOINT = `https://${PI_SERVER_IP}:3000/api/camera/capture`;
export const BACKUP_STREAM_ENDPOINT =
  import.meta.env.VITE_BACKUP_STREAM_URL ||
  "https://jjcloud.tail9d0237.ts.net:8787/api/cams/backup/stream";
export const ROVER_STATE_ENDPOINT =
  import.meta.env.VITE_ROVER_STATE_URL ||
  "https://jjcloud.tail9d0237.ts.net:8787/api/rover/state";

/** Relay HTTP origin (host + port) aligned with ROVER_STATE_ENDPOINT. */
export function getRelayHttpOrigin() {
  try {
    return new URL(ROVER_STATE_ENDPOINT).origin;
  } catch {
    return RELAY_BASE_URL.replace(/\/$/, "");
  }
}

function relayWebSocketOrigin() {
  return getRelayHttpOrigin().replace(/^http/i, "ws");
}

/** POST client geolocation; distance to fixed rover site (meters). */
export const ROVER_CLIENT_DISTANCE_ENDPOINT =
  import.meta.env.VITE_ROVER_CLIENT_DISTANCE_URL ||
  `${RELAY_BASE_URL.replace(/\/$/, "")}/api/rover/client-distance`;

/** Fast charging-only path (webcam LED); avoids backup-cam latency on `/api/rover/state`. */
export const ROVER_CHARGING_ENDPOINT =
  import.meta.env.VITE_ROVER_CHARGING_URL ||
  ROVER_STATE_ENDPOINT.replace(/\/state\/?$/, "/charging");

/** Latest LiDAR scan JSON proxied by relay (HTTPS same-origin). */
export const LIDAR_SCAN_ENDPOINT =
  import.meta.env.VITE_LIDAR_SCAN_URL ||
  `${RELAY_BASE_URL.replace(/\/$/, "")}/api/lidar/scan`;

/** Memorized SLAM map overlay on the LiDAR minimap (grey points). */
export const SLAM_ENABLED = import.meta.env.VITE_SLAM_ENABLED === "true";

/** Persistent SLAM map JSON proxied by relay. */
export const SLAM_MAP_ENDPOINT =
  import.meta.env.VITE_SLAM_MAP_URL ||
  `${RELAY_BASE_URL.replace(/\/$/, "")}/api/lidar/map`;

/** Live LiDAR scan WebSocket (relay pushes decimated scans). */
export function getLidarWebSocketUrl() {
  const configured = import.meta.env.VITE_LIDAR_WS_URL;
  if (configured) return configured;
  return `${relayWebSocketOrigin()}/ws/lidar`;
}

/** Live SLAM map WebSocket (relay pushes map updates). */
export function getSlamWebSocketUrl() {
  const configured = import.meta.env.VITE_SLAM_WS_URL;
  if (configured) return configured;
  return `${relayWebSocketOrigin()}/ws/slam`;
}

/** Relay WebSocket: rover heartbeat incl. charging — 5s default, `?backup=1` for 1s (backup camera UI). */
export function getRelayRoverHeartbeatWebSocketUrl(backupViewEnabled) {
  const q = backupViewEnabled ? "?backup=1" : "";
  return `${relayWebSocketOrigin()}/ws/rover${q}`;
}

/** Set VITE_VOICE_DRIVE_DEBUG=true to log assistant actions and control payloads in the browser console. */
export const VOICE_DRIVE_DEBUG =
  import.meta.env.VITE_VOICE_DRIVE_DEBUG === "true";

/** Drive-assist /info polling logs to the console (default on unless explicitly false). */
export const DRIVE_ASSIST_DEBUG =
  import.meta.env.VITE_DRIVE_ASSIST_DEBUG !== "false";

/** Log joystick drive vectors in the browser console (default on unless explicitly false). */
export const JOYSTICK_DRIVE_DEBUG =
  import.meta.env.VITE_JOYSTICK_DRIVE_DEBUG !== "false";

/** Log IMU samples in the browser console (default on unless explicitly false). */
export const IMU_DEBUG = import.meta.env.VITE_IMU_DEBUG !== "false";

/** Latest IMU sample from the Pi (REST). */
export const PI_IMU_ENDPOINT = `https://${PI_SERVER_IP}:3000/api/sensors/imu`;
export const PI_SENSORS_STATUS_ENDPOINT = `https://${PI_SERVER_IP}:3000/api/sensors/status`;

/** Allowed origin for capture URL (same host as API). Used to validate redirects. */
export function getAllowedCaptureOrigin() {
  try {
    return new URL(PI_CONTROL_ENDPOINT).origin;
  } catch {
    return "";
  }
}
