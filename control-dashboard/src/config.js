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
export const PI_DOCKING_ENDPOINT = `https://${PI_SERVER_IP}:3000/api/control/docking`;
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

/** Set VITE_VOICE_DRIVE_DEBUG=true to log assistant actions and control payloads in the browser console. */
export const VOICE_DRIVE_DEBUG =
  import.meta.env.VITE_VOICE_DRIVE_DEBUG === "true";

/** Allowed origin for capture URL (same host as API). Used to validate redirects. */
export function getAllowedCaptureOrigin() {
  try {
    return new URL(PI_CONTROL_ENDPOINT).origin;
  } catch {
    return "";
  }
}
