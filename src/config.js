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

const parseOrigins = (value, fallback) => {
  const raw = value && value.length ? value : fallback;
  return raw
    .split(",")
    .map((o) => o.trim())
    .filter(Boolean);
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
      "http://localhost:5173,http://127.0.0.1:5173",
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
    /** Runtime sampling interval for live model inference. */
    runtimeSampleIntervalMs: parseNumber(process.env.TELEMETRY_RUNTIME_SAMPLE_INTERVAL_MS, 500),
    /** Optional override for high-volume runtime model samples (default 3 days). */
    runtimeRetentionDays: parseNumber(process.env.TELEMETRY_RUNTIME_RETENTION_DAYS, 3),
    /** Optional override for experiment/training samples (default 30 days). */
    experimentRetentionDays: parseNumber(process.env.TELEMETRY_EXPERIMENT_RETENTION_DAYS, 30),
  },
  rover: {
    /** Consider rover offline if no heartbeat within this window (ms). */
    heartbeatStaleMs: parseNumber(process.env.ROVER_HEARTBEAT_STALE_MS, 90_000),
    /** Expected full boot duration for progress bar (ms). */
    bootTotalMs: parseNumber(process.env.ROVER_BOOT_TOTAL_MS, 50_000),
    /** Window for battery drain slope (ms). */
    batteryDrainWindowMs: parseNumber(process.env.BATTERY_DRAIN_WINDOW_MS, 120_000),
    /** Telemetry lookback for charge inference (ms). */
    chargingWindowMs: parseNumber(process.env.CHARGING_WINDOW_MS, 45 * 60 * 1000),
    /** Ignore adjacent samples farther apart than this when computing slopes (ms). */
    chargingMaxGapMs: parseNumber(process.env.CHARGING_MAX_GAP_MS, 120_000),
    /** Ignore likely startup/invalid low-voltage samples (e.g. transient 0V). */
    chargingMinTrustedVoltage: parseNumber(process.env.CHARGING_MIN_TRUSTED_VOLTAGE, 6.0),
    /**
     * Adjacent rates with a larger absolute V/min magnitude are discarded as slope outliers.
     * Fast plug/unplug transients are handled separately via transition delta rules.
     */
    chargingSpikeAbsVoltPerMin: parseNumber(process.env.CHARGING_SPIKE_ABS_VOLT_PER_MIN, 0.6),
    /** Minimum median "slow rise" V/min to count as charging. */
    chargingMinPositiveVoltPerMin: parseNumber(process.env.CHARGING_MIN_POSITIVE_VOLT_PER_MIN, 0.0015),
    /** Above this V/min, treat as non-charging / anomaly even if positive. */
    chargingMaxPositiveVoltPerMin: parseNumber(process.env.CHARGING_MAX_POSITIVE_VOLT_PER_MIN, 0.08),
    /** One segment at or below this V/min counts as a strong "not charging" tick. */
    chargingDischargeClearVoltPerMin: parseNumber(process.env.CHARGING_DISCHARGE_CLEAR_VOLT_PER_MIN, -0.02),
    /** Two ticks at or below this softer threshold clear "charging" (unplug detection). */
    chargingSoftDischargeVoltPerMin: parseNumber(process.env.CHARGING_SOFT_DISCHARGE_VOLT_PER_MIN, -0.008),
    /** Time window for immediate post plug/unplug transition detection (ms). */
    chargingTransitionMaxGapMs: parseNumber(process.env.CHARGING_TRANSITION_MAX_GAP_MS, 45_000),
    /** Immediate transition threshold in volts (about +/-0.2V). */
    chargingTransitionDeltaV: parseNumber(process.env.CHARGING_TRANSITION_DELTA_V, 0.18),
    /** How many recent trustworthy segment slopes participate in voting. */
    chargingRecentRatesTail: parseNumber(process.env.CHARGING_RECENT_RATES_TAIL, 8),
    /** Minimum trustworthy slopes required before asserting charging or sustained not-charging. */
    chargingMinGoodRates: parseNumber(process.env.CHARGING_MIN_GOOD_RATES, 2),
  },
  backupCam: {
    /** Upstream MJPEG or raw stream URL reachable from this relay host. */
    streamUrl:
      process.env.BACKUP_CAM_STREAM_URL ||
      "http://192.168.1.220:81/stream",
    /** Optional ESP voltage endpoint; defaults to stream host with /voltage path. */
    voltageUrl:
      process.env.BACKUP_CAM_VOLTAGE_URL ||
      "http://192.168.1.220:82/voltage",
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
