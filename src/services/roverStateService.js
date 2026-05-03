import config from "../config.js";
import { getDb } from "./db.js";
import { getLatestTelemetryEvent } from "./telemetryService.js";
import { inferChargingFromLedWebcam } from "./ledWebcamChargingService.js";

function parseTs(row) {
  if (!row?.created_at) return null;
  const t = Date.parse(row.created_at.replace(" ", "T") + "Z") || Date.parse(row.created_at);
  return Number.isFinite(t) ? t : null;
}

/** @param {string} iso */
function parseIso(iso) {
  if (!iso) return null;
  const t = Date.parse(iso);
  return Number.isFinite(t) ? t : null;
}

/**
 * @param {object} body
 * @param {string} [body.phase] booting | ready
 * @param {string} [body.bootStartedAt] ISO
 * @param {object} [body.health]
 */
export function recordHeartbeat(body = {}) {
  const db = getDb();
  const health = body.health || {};
  const videoSignal = typeof health.video === "string" ? health.video.toLowerCase() : health.video;
  const usbPowerSignal =
    typeof health.usbPower === "string" ? health.usbPower.toLowerCase() : health.usbPower;
  const phase = body.phase === "booting" || body.phase === "ready" ? body.phase : null;
  const bootStartedAt = body.bootStartedAt || null;
  const videoOn =
    health.videoOn === true ||
    health.videoOn === 1 ||
    health.videoOn === "1" ||
    health.videoOn === "on" ||
    videoSignal === "on" ||
    health.video === "on" ||
    health.streamActive === true ||
    health.streamActive === 1 ||
    health.streamActive === "1" ||
    health.streamActive === "true" ||
    usbPowerSignal === "on"
      ? 1
      : 0;
  const battery =
    health.battery != null && health.battery !== ""
      ? parseFloat(health.battery)
      : null;
  const voltage = health.voltage != null ? parseFloat(health.voltage) : null;

  db.prepare(
    `INSERT INTO rover_heartbeat (phase, boot_started_at, battery_pct, video_on, voltage, raw_health)
     VALUES (?, ?, ?, ?, ?, ?)`,
  ).run(
    phase,
    bootStartedAt,
    Number.isFinite(battery) ? battery : null,
    videoOn,
    Number.isFinite(voltage) ? voltage : null,
    JSON.stringify(health),
  );
}

function latestHeartbeat() {
  const db = getDb();
  return db
    .prepare("SELECT * FROM rover_heartbeat ORDER BY id DESC LIMIT 1")
    .get();
}

function recentHeartbeats(limit = 5000) {
  const db = getDb();
  const rows = db.prepare("SELECT * FROM rover_heartbeat ORDER BY id DESC LIMIT ?").all(limit);
  return rows.reverse();
}

function quantile(sorted, q) {
  if (!sorted.length) return null;
  const pos = (sorted.length - 1) * q;
  const base = Math.floor(pos);
  const rest = pos - base;
  const next = sorted[base + 1];
  if (next === undefined) return sorted[base];
  return sorted[base] + rest * (next - sorted[base]);
}

function filterIqrOutliers(values) {
  if (values.length < 4) return values.slice();
  const sorted = values.slice().sort((a, b) => a - b);
  const q1 = quantile(sorted, 0.25);
  const q3 = quantile(sorted, 0.75);
  if (q1 == null || q3 == null) return sorted;
  const iqr = q3 - q1;
  if (!Number.isFinite(iqr) || iqr <= 0) return sorted;
  const min = q1 - 1.5 * iqr;
  const max = q3 + 1.5 * iqr;
  return sorted.filter((v) => v >= min && v <= max);
}

/**
 * Estimates active-video battery drain from historical online intervals.
 * Ignores inactive gaps and outlier rates.
 * @returns {{ drainPctPerMinute: number, samples: number } | null}
 */
function estimateBatteryDrainPctPerMinute(samples, staleMs) {
  const points = samples
    .map((r) => ({ t: parseTs(r), b: Number(r.battery_pct), videoOn: r.video_on }))
    .filter((p) => p.t != null && Number.isFinite(p.b));

  if (points.length < 2) return null;
  const maxActiveGapMs = Math.max(30_000, staleMs);
  const rates = [];
  for (let i = 1; i < points.length; i += 1) {
    const prev = points[i - 1];
    const cur = points[i];
    const gapMs = cur.t - prev.t;
    if (gapMs <= 0 || gapMs > maxActiveGapMs) continue;
    if (prev.videoOn !== 1 || cur.videoOn !== 1) continue;
    const drop = prev.b - cur.b;
    if (drop <= 0) continue;
    const pctPerMinute = drop / (gapMs / 60_000);
    if (!Number.isFinite(pctPerMinute) || pctPerMinute <= 0 || pctPerMinute > 20) continue;
    rates.push(pctPerMinute);
  }
  if (!rates.length) return null;

  const filtered = filterIqrOutliers(rates);
  if (!filtered.length) return null;
  const median = quantile(filtered, 0.5);
  if (!Number.isFinite(median) || median <= 0) return null;
  return { drainPctPerMinute: -median, samples: filtered.length };
}

export async function getRoverState() {
  const now = Date.now();
  const last = latestHeartbeat();
  const lastSeenMs = last ? parseTs(last) : null;
  const staleMs = config.rover.heartbeatStaleMs;
  const online = lastSeenMs != null && now - lastSeenMs < staleMs;

  let lastOnlineAt = null;
  if (lastSeenMs != null) {
    lastOnlineAt = new Date(lastSeenMs).toISOString();
  }

  let bootStartedAtIso = null;
  let phase = null;
  if (last) {
    phase = last.phase;
    bootStartedAtIso = last.boot_started_at || null;
  }

  const bootStartMs = parseIso(bootStartedAtIso);
  const mqttBoot = getLatestTelemetryEvent("mqtt_power_on");
  const mqttPowerOff = getLatestTelemetryEvent("mqtt_power_off");
  const latestMqttPower =
    mqttBoot && mqttPowerOff ? (mqttBoot.id > mqttPowerOff.id ? mqttBoot : mqttPowerOff) : mqttBoot || mqttPowerOff;
  const mqttBootMs = mqttBoot
    ? Date.parse(mqttBoot.created_at.replace(" ", "T") + "Z") || Date.parse(mqttBoot.created_at)
    : null;
  const latestMqttIsOff = latestMqttPower?.event === "mqtt_power_off";
  const bootTotal = config.rover.bootTotalMs;
  let bootProgressPct = null;
  let booting = false;

  if (latestMqttIsOff) {
    booting = false;
    bootProgressPct = 0;
  } else if (online && phase === "booting" && bootStartMs != null) {
    booting = true;
    bootProgressPct = Math.min(99, Math.max(0, ((now - bootStartMs) / bootTotal) * 100));
  } else if (online && phase === "booting") {
    booting = true;
    bootProgressPct = null;
  } else if (mqttBootMs != null) {
    const elapsed = now - mqttBootMs;
    if (elapsed < bootTotal) {
      booting = true;
      bootProgressPct = Math.max(0, Math.min(99, (elapsed / bootTotal) * 100));
      phase = "booting";
    } else {
      booting = false;
      bootProgressPct = 100;
      if (!phase) phase = "ready";
    }
  } else if (online && phase === "ready") {
    bootProgressPct = 100;
    booting = false;
  } else if (online && phase == null) {
    bootProgressPct = 100;
    booting = false;
  } else {
    bootProgressPct = null;
    booting = false;
  }

  const history = recentHeartbeats(5000);
  const slopeInfo = estimateBatteryDrainPctPerMinute(history, staleMs);
  const currentBattery = last?.battery_pct;

  let estimatedMinutesRemaining = null;
  let drainPctPerMinute = null;
  if (
    slopeInfo &&
    currentBattery != null &&
    currentBattery > 0 &&
    slopeInfo.drainPctPerMinute < 0
  ) {
    const pctPerMinute = slopeInfo.drainPctPerMinute;
    drainPctPerMinute = pctPerMinute;
    estimatedMinutesRemaining = currentBattery / -pctPerMinute;
    if (!Number.isFinite(estimatedMinutesRemaining) || estimatedMinutesRemaining < 0) {
      estimatedMinutesRemaining = null;
    } else if (estimatedMinutesRemaining > 24 * 60) {
      estimatedMinutesRemaining = null;
    }
  }

  let lastBootAt = null;
  const readyRows = getDb()
    .prepare(
      `SELECT created_at FROM rover_heartbeat WHERE phase = 'ready' ORDER BY id DESC LIMIT 1`,
    )
    .get();
  if (readyRows?.created_at) {
    lastBootAt = new Date(
      Date.parse(readyRows.created_at.replace(" ", "T") + "Z") || Date.parse(readyRows.created_at),
    ).toISOString();
  }

  const charging = await inferChargingFromLedWebcam();

  return {
    online,
    /** Last successful heartbeat time (when offline, this is last time rover was online). */
    lastSeenAt: lastOnlineAt,
    lastOnlineAt: lastOnlineAt,
    lastBootAt,
    mqttBootSignalAt: mqttBootMs != null ? new Date(mqttBootMs).toISOString() : null,
    phase: phase || (online ? "ready" : null),
    booting,
    bootProgressPct: bootProgressPct != null ? Math.round(bootProgressPct * 10) / 10 : null,
    bootTotalMs: bootTotal,
    heartbeatStaleMs: staleMs,
    battery: {
      currentPct: currentBattery,
      /** Negative % per minute while discharging under video-on samples. */
      drainPctPerMinute,
      estimatedMinutesRemainingActiveVideo:
        estimatedMinutesRemaining != null
          ? Math.round(estimatedMinutesRemaining * 10) / 10
          : null,
      drainSampleCount: slopeInfo?.samples ?? 0,
      drainWindowMs: config.rover.batteryDrainWindowMs,
    },
    charging,
  };
}
