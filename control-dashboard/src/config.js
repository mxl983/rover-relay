/**
 * Central config — dashboard runs on another Tailscale device; always use FQDNs.
 *
 * Video (WebRTC): pi-server MediaMTX on the Pi
 * Drive / status / gimbal: MentorPi web_car (Flask) → ROS topics
 *
 * Override with VITE_* in control-dashboard/.env.local
 */
const PI_FQDN = (
  import.meta.env.VITE_PI_FQDN ??
  import.meta.env.VITE_PI_SERVER_IP ??
  "raspberrypi.tail9d0237.ts.net"
).replace(/^https?:\/\//i, "").replace(/\/$/, "");

const MQTT_HOST =
  import.meta.env.VITE_MQTT_HOST ??
  "wss://a4ad037d8b304c56a8e60e1a1e163842.s1.eu.hivemq.cloud:8884/mqtt";

/** Optional camera API secret (unused by MentorPi path). */
export const CAMERA_SECRET = import.meta.env.VITE_CAMERA_SECRET ?? "";

export { PI_FQDN, MQTT_HOST };
/** @deprecated use PI_FQDN — kept for older imports */
export const PI_SERVER_IP = PI_FQDN;

const WEBRTC_PORT = import.meta.env.VITE_WEBRTC_PORT ?? "8889";
const MENTOR_API_PORT = import.meta.env.VITE_MENTOR_API_PORT ?? "5000";

/** pi-server MediaMTX WebRTC (HTTPS + Tailscale cert). */
export const WEBRTC_BASE =
  import.meta.env.VITE_WEBRTC_BASE ?? `https://${PI_FQDN}:${WEBRTC_PORT}`;

/** MentorPi web_car via HTTPS pi-server proxy (:3000). Phones already reach :3000. */
export const MENTOR_API_BASE =
  import.meta.env.VITE_MENTOR_API_BASE ?? `https://${PI_FQDN}:3000`;

export const AUDIO_STREAM_HOST = `${WEBRTC_BASE.replace(/\/$/, "")}/mic/whep`;
export const VIDEO_STREAM_HOST = `${WEBRTC_BASE.replace(/\/$/, "")}/cam/whep`;
export const AUDIO_TALK_HOST = `${WEBRTC_BASE.replace(/\/$/, "")}/talk/whip`;

/** MentorPi continuous drive (maps stick → /controller/cmd_vel). */
export const MENTOR_CMD_VEL_ENDPOINT = `${MENTOR_API_BASE.replace(/\/$/, "")}/api/cmd_vel`;
export const MENTOR_DRIVE_ENDPOINT = `${MENTOR_API_BASE.replace(/\/$/, "")}/api/drive`;
export const MENTOR_STATUS_ENDPOINT = `${MENTOR_API_BASE.replace(/\/$/, "")}/api/status`;
export const MENTOR_GIMBAL_ENDPOINT = `${MENTOR_API_BASE.replace(/\/$/, "")}/api/gimbal`;
export const MENTOR_PHOTO_ENDPOINT = `${MENTOR_API_BASE.replace(/\/$/, "")}/api/photo`;
export const MENTOR_BEEP_ENDPOINT = `${MENTOR_API_BASE.replace(/\/$/, "")}/api/beep`;
export const MENTOR_SCAN_ENDPOINT = `${MENTOR_API_BASE.replace(/\/$/, "")}/api/scan`;
export const MENTOR_EXPOSURE_ENDPOINT = `${MENTOR_API_BASE.replace(/\/$/, "")}/api/exposure`;

/**
 * Stick → Twist scale (m/s, rad/s). Match web_car SPEED_LEVELS.
 * Dashboard: y=-1 forward, x=+1 turn right.
 *
 * DRIVE_CMD_SCALE must stay in sync with web_car `DRIVE_CMD_SCALE`
 * (temporary 7.4 V motor safety on ~12 V pack).
 */
export const DRIVE_CMD_SCALE =
  Number(import.meta.env.VITE_DRIVE_CMD_SCALE) || 1;

const _BASE_MENTOR_SPEED_LEVELS = {
  slow: { linear: 0.1, angular: 0.3 },
  normal: { linear: 0.2, angular: 0.5 },
  fast: { linear: 0.35, angular: 0.9 },
};

export const MENTOR_SPEED_LEVELS = Object.fromEntries(
  Object.entries(_BASE_MENTOR_SPEED_LEVELS).map(([name, vals]) => [
    name,
    {
      linear: vals.linear * DRIVE_CMD_SCALE,
      angular: vals.angular * DRIVE_CMD_SCALE,
    },
  ]),
);

/** @deprecated prefer MENTOR_SPEED_LEVELS — kept as fast-tier defaults / env overrides */
export const MENTOR_MAX_LINEAR =
  Number(import.meta.env.VITE_MENTOR_MAX_LINEAR) || MENTOR_SPEED_LEVELS.fast.linear;
export const MENTOR_MAX_ANGULAR =
  Number(import.meta.env.VITE_MENTOR_MAX_ANGULAR) || MENTOR_SPEED_LEVELS.fast.angular;

export function resolveMentorSpeedLimits(speed = "fast") {
  if (speed && typeof speed === "object") {
    const linear = Number(speed.linear);
    const angular = Number(speed.angular);
    if (Number.isFinite(linear) && Number.isFinite(angular)) {
      return { linear, angular };
    }
  }
  const key = String(speed || "fast").toLowerCase();
  return MENTOR_SPEED_LEVELS[key] || MENTOR_SPEED_LEVELS.fast;
}

/** Map dashboard stick {x,y,strafe} → MentorPi Twist fields. */
export function stickToCmdVel(drive, speedOrLimits = "fast") {
  const { linear: maxLin, angular: maxAng } = resolveMentorSpeedLimits(speedOrLimits);
  const x = Number(drive?.x ?? 0) || 0;
  const y = Number(drive?.y ?? 0) || 0;
  const strafe = Number(drive?.strafe ?? 0) || 0;
  // Dashboard: y=-1 at 12 o'clock (forward); MentorPi: +linear_x = forward.
  const linear_x = -y * maxLin;
  // Dashboard strafe: -1 = left, +1 = right; MentorPi: +linear_y = strafe left.
  const linear_y = -strafe * maxLin;
  // Dashboard: x=+1 turn right; MentorPi: -angular_z = turn right.
  const angular_z = -x * maxAng;
  const scrub = (v) => (Object.is(v, -0) || Math.abs(v) < 1e-12 ? 0 : v);
  return {
    linear_x: scrub(linear_x),
    linear_y: scrub(linear_y),
    angular_z: scrub(angular_z),
  };
}

/** Legacy aliases — panel/NV are rover-control on :3000; drive stays MentorPi. */
export const PI_CONTROL_ENDPOINT = MENTOR_CMD_VEL_ENDPOINT;
export const PI_SYSTEM_ENDPOINT =
  import.meta.env.VITE_PI_SYSTEM_ENDPOINT ??
  `https://${PI_FQDN}:3000/api/system`;
export const PI_CAMERA_ENDPOINT =
  import.meta.env.VITE_PI_CAMERA_ENDPOINT ??
  `https://${PI_FQDN}:3000/api/camera`;
export const PI_VOICE_ENDPOINT = "";
export const PI_WEBSOCKET = "";
export const PI_HI_RES_CAPTURE_ENDPOINT =
  import.meta.env.VITE_PI_HI_RES_CAPTURE_ENDPOINT ??
  `${PI_CAMERA_ENDPOINT.replace(/\/$/, "")}/capture`;
export const PI_IMU_ENDPOINT = "";
export const PI_SENSORS_STATUS_ENDPOINT = MENTOR_STATUS_ENDPOINT;
export const PI_PANEL_ENDPOINT = `${PI_SYSTEM_ENDPOINT.replace(/\/$/, "")}/panel`;
export const PI_POWER_SAVING_ENDPOINT = `${PI_SYSTEM_ENDPOINT.replace(/\/$/, "")}/power-saving`;
export const PI_ACTIVITY_ENDPOINT = `${PI_SYSTEM_ENDPOINT.replace(/\/$/, "")}/activity`;
/** Connected dashboard viewers (read-only; also returned on activity POST). */
export const PI_PRESENCE_ENDPOINT = `${PI_SYSTEM_ENDPOINT.replace(/\/$/, "")}/presence`;
/** Soft night vision (V4L2 gain/exposure) via pi-server rover-control API. */
export const PI_NIGHTVISION_ENDPOINT =
  import.meta.env.VITE_PI_NIGHTVISION_ENDPOINT ??
  `${PI_CAMERA_ENDPOINT.replace(/\/$/, "")}/nightvision`;
/** Day-mode camera auto exposure (aperture priority) via rover-control. */
export const PI_AUTO_EXPOSURE_ENDPOINT =
  import.meta.env.VITE_PI_AUTO_EXPOSURE_ENDPOINT ??
  `${PI_CAMERA_ENDPOINT.replace(/\/$/, "")}/auto-exposure`;
/** Stream resolution (MediaMTX ffmpeg -video_size) via rover-control. */
export const PI_RESOLUTION_ENDPOINT =
  import.meta.env.VITE_PI_RESOLUTION_ENDPOINT ??
  `${PI_CAMERA_ENDPOINT.replace(/\/$/, "")}/resolution`;

export const VOICE_RECOGNITION_LANG =
  import.meta.env.VITE_VOICE_RECOGNITION_LANG ||
  (typeof navigator !== "undefined" && /^zh/i.test(navigator.language || "")
    ? "zh-CN"
    : "en-US");
export const VOICE_RECOGNITION_LIVE_LANG =
  import.meta.env.VITE_VOICE_RECOGNITION_LIVE_LANG || "zh-CN";

export const VOICE_DRIVE_DEBUG =
  import.meta.env.VITE_VOICE_DRIVE_DEBUG === "true";
export const DRIVE_ASSIST_DEBUG =
  import.meta.env.VITE_DRIVE_ASSIST_DEBUG !== "false";
export const JOYSTICK_DRIVE_DEBUG =
  import.meta.env.VITE_JOYSTICK_DRIVE_DEBUG !== "false";
export const IMU_DEBUG = import.meta.env.VITE_IMU_DEBUG !== "false";

export function getAllowedCaptureOrigin() {
  try {
    return new URL(PI_CAMERA_ENDPOINT).origin;
  } catch {
    return "";
  }
}
