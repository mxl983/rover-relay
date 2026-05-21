import "dotenv/config";

const parseNumber = (value, fallback) => {
  if (value === undefined || value === null || value === "") return fallback;
  const parsed = Number(value);
  return Number.isNaN(parsed) ? fallback : parsed;
};

const parseBoolean = (value, fallback) => {
  if (value === undefined) return fallback;
  if (value === "true") return true;
  if (value === "false") return false;
  return fallback;
};

/** Browsers send `Origin` as scheme+host+port only (no path). Normalize so `.env` may list a full site URL. */
const normalizeCorsOrigin = (entry) => {
  const s = entry.trim();
  if (!s) return "";
  try {
    return new URL(s).origin;
  } catch {
    return s;
  }
};

const parseOrigins = (value, fallback) => {
  const raw = value && value.length ? value : fallback;
  const list = raw
    .split(",")
    .map((o) => normalizeCorsOrigin(o))
    .filter(Boolean);
  return [...new Set(list)];
};

const config = {
  env: process.env.NODE_ENV || "development",
  server: {
    port: parseNumber(process.env.PORT, 8787),
    host: process.env.HOST || "0.0.0.0",
  },
  ssl: {
    enabled: parseBoolean(process.env.SSL_ENABLED, true),
    certPath: process.env.SSL_CERT_PATH || "/certs/relay.crt",
    keyPath: process.env.SSL_KEY_PATH || "/certs/relay.key",
    /**
     * Optional plain HTTP listener that redirects all requests to HTTPS.
     * Keep disabled when not exposing a separate HTTP port.
     */
    redirectHttpEnabled: parseBoolean(process.env.HTTP_REDIRECT_ENABLED, false),
    redirectHttpPort: parseNumber(process.env.HTTP_REDIRECT_PORT, 8080),
  },
  cors: {
    origins: parseOrigins(
      process.env.CORS_ORIGINS,
      "http://localhost:5173,http://127.0.0.1:5173,https://mxl983.github.io",
    ),
  },
  auth: {
    /** If set, ingest + heartbeat require Authorization: Bearer <token> */
    token: process.env.ROVER_API_TOKEN || "",
  },
  logging: {
    requestEnabled: parseBoolean(process.env.REQUEST_LOG_ENABLED, true),
  },
  mqttBoot: {
    enabled: parseBoolean(process.env.MQTT_BOOT_ENABLED, false),
    /**
     * URL form preferred for WSS, e.g.
     * wss://84f09906a62e42c78c5d9b0555aa71f1.s1.eu.hivemq.cloud:8884/mqtt
     */
    url:
      process.env.MQTT_BOOT_URL ||
      "wss://84f09906a62e42c78c5d9b0555aa71f1.s1.eu.hivemq.cloud:8884/mqtt",
    username: process.env.MQTT_BOOT_USER || "",
    password: process.env.MQTT_BOOT_PASS || "",
    bootTopic: process.env.MQTT_BOOT_TOPIC || "rover/power/pi",
    /** If payload starts with this prefix (case-insensitive), we count it as wake. */
    bootPayloadPrefix: process.env.MQTT_BOOT_PAYLOAD_PREFIX || "on",
  },
  telemetry: {
    enabled: parseBoolean(process.env.TELEMETRY_ENABLED, true),
    dbPath: process.env.TELEMETRY_DB_PATH || "/app/data/relay.db",
    retentionDays: parseNumber(process.env.TELEMETRY_RETENTION_DAYS, 14),
    /** USB charger LED poll interval for charging_start / charging_end telemetry events. */
    chargingPollMs: parseNumber(process.env.CHARGING_TELEMETRY_POLL_MS, 25_000),
  },
  rover: {
    /** Consider rover offline if no heartbeat within this window (ms). */
    heartbeatStaleMs: parseNumber(process.env.ROVER_HEARTBEAT_STALE_MS, 90_000),
    /** Expected full boot duration for progress bar (ms). */
    bootTotalMs: parseNumber(process.env.ROVER_BOOT_TOTAL_MS, 50_000),
    /** Window for battery drain slope (ms). */
    batteryDrainWindowMs: parseNumber(process.env.BATTERY_DRAIN_WINDOW_MS, 120_000),
    /**
     * Minimum elapsed time between two heartbeat samples when inferring %/minute drain.
     * Very short gaps (e.g. 5s heartbeat cadence) amplify SOC quantization/noise into unrealistic rates.
     */
    batteryDrainMinPairGapMs: parseNumber(process.env.BATTERY_DRAIN_MIN_PAIR_GAP_MS, 45_000),
    /** Fixed WGS84 rover site for client distance — set ROVER_LATITUDE / ROVER_LONGITUDE in .env only (never commit). */
    location: {
      latitude: parseNumber(process.env.ROVER_LATITUDE, Number.NaN),
      longitude: parseNumber(process.env.ROVER_LONGITUDE, Number.NaN),
    },
    /**
     * Charger LED via USB webcam (`ffmpeg` + hue classification). Red ≈ charging, green ≈ idle.
     */
    ledWebcam: {
      /** Linux V4L2 device path */
      device: process.env.CHARGING_LED_WEBCAM_DEVICE || "/dev/video0",
      /** Hard cap on ffmpeg capture; keep low for fast API responses. */
      captureTimeoutMs: parseNumber(process.env.CHARGING_LED_WEBCAM_TIMEOUT_MS, 2500),
      /** Small frames = less USB bandwidth and faster decode (LED fills frame when close). */
      frameWidth: parseNumber(process.env.CHARGING_LED_WEBCAM_WIDTH, 320),
      frameHeight: parseNumber(process.env.CHARGING_LED_WEBCAM_HEIGHT, 240),
      /** Optional direct capture size at device open (matches supported modes, e.g. 320x240). Overrides width/height for -video_size before -i. */
      captureVideoSize:
        process.env.CHARGING_LED_WEBCAM_CAPTURE_SIZE &&
        String(process.env.CHARGING_LED_WEBCAM_CAPTURE_SIZE).trim().length
          ? String(process.env.CHARGING_LED_WEBCAM_CAPTURE_SIZE).trim()
          : null,
      /**
       * Reuse last detection for this many ms (concurrent / rapid dashboard polls).
       * Set 0 to disable (always grab a fresh frame).
       */
      cacheTtlMs: parseNumber(process.env.CHARGING_LED_WEBCAM_CACHE_TTL_MS, 200),
      /** Empty string = let ffmpeg negotiate; common values: mjpeg, yuyv422 */
      inputFormat: process.env.CHARGING_LED_WEBCAM_INPUT_FORMAT || "mjpeg",
      /**
       * “Charging” LED hue band (HSV 0–360). Default: red through orange (~0–72°), touching but not
       * overlapping green idle at 73°+. Values between this max and greenMin still classify as
       * charging via bloom gap-fill in classifyHueForLed.
       * If min > max, the band wraps across 0° (e.g. 350–12).
       * Legacy: CHARGING_LED_YELLOW_* still accepted as aliases for these values.
       */
      chargingHueMin: parseNumber(
        process.env.CHARGING_LED_CHARGING_HUE_MIN ?? process.env.CHARGING_LED_YELLOW_HUE_MIN,
        0,
      ),
      chargingHueMax: parseNumber(
        process.env.CHARGING_LED_CHARGING_HUE_MAX ?? process.env.CHARGING_LED_YELLOW_HUE_MAX,
        72,
      ),
      greenHueMin: parseNumber(process.env.CHARGING_LED_GREEN_HUE_MIN, 73),
      greenHueMax: parseNumber(process.env.CHARGING_LED_GREEN_HUE_MAX, 165),
      /**
       * Ignore pixels with max(R,G,B) below this (0–255). Drops pure black frame background
       * before hue stats so the LED blob dominates.
       */
      ignoreBelowRgbMax: parseNumber(process.env.CHARGING_LED_IGNORE_BELOW_RGB_MAX, 14),
      /**
       * Until the camera is mounted: `charging` | `idle` | `error` to fake detector output,
       * or leave unset for real ffmpeg capture (will fail gracefully if no device).
       */
      stubMode: process.env.CHARGING_LED_WEBCAM_STUB || "",
    },
  },
  backupCam: {
    /** Upstream MJPEG or raw stream URL reachable from this relay host. */
    streamUrl:
      process.env.BACKUP_CAM_STREAM_URL ||
      "http://192.168.1.220:81/stream",
    /** Optional ESP realtime environment endpoint; defaults to stream host with /realtime path. */
    realtimeUrl:
      process.env.BACKUP_CAM_REALTIME_URL ||
      "http://192.168.1.220:82/realtime",
    /** If true, require same ROVER_API_TOKEN to read the stream. */
    streamAuth: parseBoolean(process.env.BACKUP_CAM_STREAM_AUTH, false),
  },
  controlDashboard: {
    /** If true, relay proxies the UI under basePath (e.g. /mangomate). */
    proxyEnabled: parseBoolean(process.env.CONTROL_DASHBOARD_PROXY_ENABLED, true),
    /** Public mount path on relay. */
    basePath: process.env.CONTROL_DASHBOARD_PROXY_BASE_PATH || "/mangomate",
    /** Internal service URL reachable from relay container. */
    targetUrl: process.env.CONTROL_DASHBOARD_PROXY_TARGET || "http://control-dashboard:80",
  },
};

export default config;
