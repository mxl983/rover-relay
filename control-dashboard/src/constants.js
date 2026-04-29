/**
 * @deprecated Prefer importing from "./config" for new code.
 * Re-exports from config for backward compatibility.
 */
export {
  PI_SERVER_IP,
  MQTT_HOST,
  RELAY_BASE_URL,
  CAMERA_SECRET,
  AUDIO_STREAM_HOST,
  VIDEO_STREAM_HOST,
  AUDIO_TALK_HOST,
  PI_CONTROL_ENDPOINT,
  PI_DOCKING_ENDPOINT,
  PI_SYSTEM_ENDPOINT,
  PI_CAMERA_ENDPOINT,
  PI_WEBSOCKET,
  PI_HI_RES_CAPTURE_ENDPOINT,
  BACKUP_STREAM_ENDPOINT,
  ROVER_STATE_ENDPOINT,
  getAllowedCaptureOrigin,
} from "./config.js";
