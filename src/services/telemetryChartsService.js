import config from "../config.js";
import { getDb } from "./db.js";
import { remapReportedBatteryPctRounded } from "../utils/batteryPctScale.js";

const MQTT_ON = "mqtt_power_on";
const MQTT_OFF = "mqtt_power_off";
const POWER_ON = "power_on";
const POWER_OFF = "power_off";
const CHARGING_ON = "charging_start";
const CHARGING_OFF = "charging_end";

/** Parse telemetry / heartbeat created_at to epoch ms (UTC Z suffix). */
function parseTs(row) {
  if (!row?.created_at) return null;
  const raw = String(row.created_at);
  const normalized = raw.includes("T") ? raw : raw.replace(" ", "T");
  const t =
    Date.parse(normalized.endsWith("Z") ? normalized : `${normalized}Z`) ||
    Date.parse(raw);
  return Number.isFinite(t) ? t : null;
}

function startOfLocalDay(d) {
  const x = new Date(d);
  x.setHours(0, 0, 0, 0);
  return x.getTime();
}

function formatDayLabel(ts) {
  return new Date(ts).toLocaleDateString(undefined, {
    weekday: "short",
    year: "numeric",
    month: "short",
    day: "numeric",
  });
}

/** SQLite TEXT comparison friendly UTC stamp (no TZ in string). */
function toSqlUtc(ms) {
  return new Date(ms).toISOString().replace("T", " ").slice(0, 19);
}

/**
 * MQTT-defined sessions: open on mqtt_power_on, close on mqtt_power_off.
 * Unclosed session ends at `nowOpenMs` (typically Date.now()).
 */
function buildMqttSessions(rows, nowOpenMs) {
  const sessions = [];
  let openStart = null;

  for (const row of rows) {
    const t = parseTs(row);
    if (t == null) continue;
    if (row.event === MQTT_ON || row.event === POWER_ON) {
      if (openStart == null) openStart = t;
    } else if (row.event === MQTT_OFF || row.event === POWER_OFF) {
      if (openStart != null) {
        if (t > openStart) sessions.push({ startMs: openStart, endMs: t });
        openStart = null;
      }
    }
  }
  if (openStart != null && nowOpenMs > openStart) {
    sessions.push({ startMs: openStart, endMs: nowOpenMs });
  }
  return sessions;
}

/**
 * Idle period is between explicit power-off and next power-on.
 * Open idle period is closed at `nowOpenMs`.
 */
function buildIdleSessions(rows, nowOpenMs) {
  const sessions = [];
  let openStart = null;

  for (const row of rows) {
    const t = parseTs(row);
    if (t == null) continue;
    if (row.event === MQTT_OFF || row.event === POWER_OFF) {
      if (openStart == null) openStart = t;
    } else if (row.event === MQTT_ON || row.event === POWER_ON) {
      if (openStart != null) {
        if (t > openStart) sessions.push({ startMs: openStart, endMs: t });
        openStart = null;
      }
    }
  }

  if (openStart != null && nowOpenMs > openStart) {
    sessions.push({ startMs: openStart, endMs: nowOpenMs });
  }

  return sessions;
}

function isChargingTrue(value) {
  return value === 1 || value === true || value === "1";
}

function buildChargingIntervals(rows, nowMs) {
  let open = null;
  const intervals = [];
  for (const row of rows) {
    const t = parseTs(row);
    if (t == null) continue;
    if (row.event === CHARGING_ON) {
      if (open == null) open = t;
    } else if (row.event === CHARGING_OFF) {
      if (open != null && t > open) intervals.push({ startMs: open, endMs: t });
      open = null;
    }
  }
  if (open != null && nowMs > open) {
    intervals.push({ startMs: open, endMs: nowMs });
  }
  return intervals;
}

/** True if [t0,t1] overlaps any charging interval (plugged-in LED window). */
function segmentOverlapsChargingWindow(t0, t1, intervals) {
  if (!intervals?.length || t1 <= t0) return false;
  for (const iv of intervals) {
    const s = Math.max(t0, iv.startMs);
    const e = Math.min(t1, iv.endMs);
    if (e > s) return true;
  }
  return false;
}

/** Add duration [t0,t1) across local calendar days into map keyed by startOfLocalDay. */
function addMsAcrossLocalDays(map, t0, t1) {
  let a = t0;
  const end = t1;
  if (end <= a) return;
  while (a < end) {
    const dayStart = startOfLocalDay(a);
    const dayEnd = dayStart + 86400000;
    const segEnd = Math.min(end, dayEnd);
    const dt = segEnd - a;
    map.set(dayStart, (map.get(dayStart) || 0) + dt);
    a = segEnd;
  }
}

/** Local calendar days from startOfLocalDay(startMs) through startOfLocalDay(endMs), inclusive. */
function enumerateLocalDays(startMs, endMs) {
  const labels = [];
  let d = startOfLocalDay(startMs);
  const last = startOfLocalDay(endMs);
  while (d <= last) {
    labels.push({ dayKey: d, label: formatDayLabel(d) });
    d += 86400000;
  }
  return labels;
}

function parseWindow(options) {
  const retentionMs = Math.max(86400000, (config.telemetry.retentionDays || 14) * 86400000);
  const now = Date.now();
  let endMs = Number.isFinite(options.endMs) ? options.endMs : now;
  let startMs = Number.isFinite(options.startMs) ? options.startMs : endMs - 3 * 86400000;
  if (endMs > now) endMs = now;
  if (startMs >= endMs) startMs = endMs - 3600000;
  if (endMs - startMs > retentionMs) startMs = endMs - retentionMs;
  if (endMs - startMs < 60000) endMs = startMs + 60000;
  return { startMs, endMs, retentionMs };
}

function isValidBatteryPct(value) {
  return Number.isFinite(value) && value > 0 && value <= 100;
}

/** Sorted by t asc. Returns index of first point where t >= targetMs. */
function lowerBoundPointIndex(points, targetMs) {
  let lo = 0;
  let hi = points.length;
  while (lo < hi) {
    const mid = Math.floor((lo + hi) / 2);
    if (points[mid].t < targetMs) lo = mid + 1;
    else hi = mid;
  }
  return lo;
}

/** Sorted by t asc. Returns index of last point where t <= targetMs, else -1. */
function upperBoundPointIndex(points, targetMs) {
  let lo = 0;
  let hi = points.length;
  while (lo < hi) {
    const mid = Math.floor((lo + hi) / 2);
    if (points[mid].t <= targetMs) lo = mid + 1;
    else hi = mid;
  }
  return lo - 1;
}

/** Sorted by t asc. Returns nearest valid battery point to target time. */
function findNearestBatteryPoint(points, targetMs) {
  if (!points.length || !Number.isFinite(targetMs)) return null;
  let lo = 0;
  let hi = points.length - 1;
  while (lo <= hi) {
    const mid = Math.floor((lo + hi) / 2);
    const mt = points[mid].t;
    if (mt === targetMs) return points[mid];
    if (mt < targetMs) lo = mid + 1;
    else hi = mid - 1;
  }
  const right = lo < points.length ? points[lo] : null;
  const left = hi >= 0 ? points[hi] : null;
  if (!left) return right;
  if (!right) return left;
  return Math.abs(left.t - targetMs) <= Math.abs(right.t - targetMs) ? left : right;
}

function getDashboardCharts(options = {}) {
  const windowed = parseWindow(options);

  if (!config.telemetry.enabled) {
    return emptyChartsPayload(windowed.startMs, windowed.endMs);
  }

  const { startMs, endMs } = windowed;

  const db = getDb();
  const nowOpenMs = Date.now();
  const retentionCutMs =
    nowOpenMs - Math.max(1, config.telemetry.retentionDays || 14) * 86400000;
  const wantMqttLookbackMs = startMs - 45 * 86400000;
  const mqttLookbackMs = Math.max(retentionCutMs, wantMqttLookbackMs);

  const mqttRows = db
    .prepare(
      `SELECT id, created_at, event FROM telemetry
      WHERE event IN (?, ?, ?, ?)
       AND datetime(created_at) >= datetime(?)
       ORDER BY datetime(created_at) ASC, id ASC`,
    )
    .all(MQTT_ON, MQTT_OFF, POWER_ON, POWER_OFF, toSqlUtc(mqttLookbackMs));

  const activeSessions = buildMqttSessions(mqttRows, nowOpenMs);
  const idleSessions = buildIdleSessions(mqttRows, nowOpenMs);

  const chargingRows = db
    .prepare(
      `SELECT created_at, event FROM telemetry
       WHERE event IN (?, ?)
       AND datetime(created_at) >= datetime(?)
       ORDER BY datetime(created_at) ASC, id ASC`,
    )
    .all(CHARGING_ON, CHARGING_OFF, toSqlUtc(mqttLookbackMs));

  const chargingIntervals = buildChargingIntervals(chargingRows, nowOpenMs);

  /** Include heartbeats back to each *currently open* MQTT session start so ongoing-session drain uses anchor→latest (denominator grows with wall clock). */
  let hbRangeStartMs = startMs;
  for (const s of activeSessions) {
    if (s.endMs >= nowOpenMs) hbRangeStartMs = Math.min(hbRangeStartMs, s.startMs);
  }
  hbRangeStartMs = Math.max(hbRangeStartMs, retentionCutMs);

  const hbRows = db
    .prepare(
      `SELECT created_at, battery_pct, video_on, charging
       FROM rover_heartbeat
       WHERE datetime(created_at) >= datetime(?)
       AND datetime(created_at) <= datetime(?)
       ORDER BY datetime(created_at) ASC, id ASC`,
    )
    .all(toSqlUtc(hbRangeStartMs), toSqlUtc(endMs));

  const points = hbRows
    .map((r) => ({
      t: parseTs(r),
      // Keep null/empty as NaN so it cannot be mistaken for 0%.
      b:
        r.battery_pct == null || r.battery_pct === ""
          ? Number.NaN
          : remapReportedBatteryPctRounded(r.battery_pct) ?? Number.NaN,
      v: r.video_on === 1 ? 1 : 0,
      charging: r.charging,
    }))
    .filter((p) => p.t != null);

  // Ignore invalid SOC readings (notably startup false 0% spikes) in drain math.
  const batteryPoints = points.filter((p) => isValidBatteryPct(p.b));

  const dayAxes = enumerateLocalDays(startMs, endMs);
  const activeVideoMsByDay = new Map();
  const idleVideoMsByDay = new Map();

  for (const s of activeSessions) {
    const i0 = Math.max(s.startMs, startMs);
    const i1 = Math.min(s.endMs, endMs);
    if (i1 > i0) addMsAcrossLocalDays(activeVideoMsByDay, i0, i1);
  }
  for (const s of idleSessions) {
    const i0 = Math.max(s.startMs, startMs);
    const i1 = Math.min(s.endMs, endMs);
    if (i1 > i0) addMsAcrossLocalDays(idleVideoMsByDay, i0, i1);
  }

  const activeSecPerDay = dayAxes.map((d) => (activeVideoMsByDay.get(d.dayKey) || 0) / 1000);
  const idleSecPerDay = dayAxes.map((d) => (idleVideoMsByDay.get(d.dayKey) || 0) / 1000);

  /** Per-bin sum of (minutes / Δ%) so we can report mean minutes per 1% SOC in each decile. */
  const batterySumMinPerPct = new Array(10).fill(0);
  const batteryBinSegments = new Array(10).fill(0);

  const batteryLabels = [
    "0–10",
    "10–20",
    "20–30",
    "30–40",
    "40–50",
    "50–60",
    "60–70",
    "70–80",
    "80–90",
    "90–100",
  ];

  const telCpuRows = db
    .prepare(
      `SELECT created_at, cpu_load FROM telemetry
       WHERE cpu_load IS NOT NULL
       AND (event IS NULL OR event NOT IN (?, ?, ?, ?))
       AND datetime(created_at) >= datetime(?)
       AND datetime(created_at) <= datetime(?)`,
    )
    .all(MQTT_ON, MQTT_OFF, POWER_ON, POWER_OFF, toSqlUtc(startMs), toSqlUtc(endMs));

  const cpuBuckets = new Map();
  for (const row of telCpuRows) {
    const t = parseTs(row);
    if (t == null || t < startMs || t > endMs) continue;
    const hourSlot = Math.floor(t / 3600000);
    const load = Number(row.cpu_load);
    if (!Number.isFinite(load)) continue;
    if (!cpuBuckets.has(hourSlot)) cpuBuckets.set(hourSlot, { sum: 0, n: 0 });
    const b = cpuBuckets.get(hourSlot);
    b.sum += load;
    b.n += 1;
  }

  const cpuLabels = [];
  const cpuValues = [];
  const slotStart = Math.floor(startMs / 3600000);
  const slotEnd = Math.floor(endMs / 3600000);
  for (let h = slotStart; h <= slotEnd; h += 1) {
    const bucket = cpuBuckets.get(h);
    cpuLabels.push(
      new Date(h * 3600000).toLocaleString(undefined, {
        month: "numeric",
        day: "numeric",
        hour: "numeric",
      }),
    );
    cpuValues.push(bucket && bucket.n > 0 ? bucket.sum / bucket.n : null);
  }

  /** Total discharged % and total elapsed minutes — weighted daily average = sumDrop / sumMin. */
  const activeDropSumByDay = new Map();
  const activeMinSumByDay = new Map();
  const idleDropSumByDay = new Map();
  const idleMinSumByDay = new Map();
  const consumptionTotals = {
    active: { drop: 0, min: 0 },
    idle: { drop: 0, min: 0 },
  };

  function accumulateDailyConsumption(drop, spanMin, dayKey, dropMap, minMap) {
    if (!Number.isFinite(drop) || !Number.isFinite(spanMin) || spanMin <= 0) return;
    const pctPerMin = drop / spanMin;
    if (!Number.isFinite(pctPerMin) || pctPerMin <= 0 || pctPerMin > 30) return;
    dropMap.set(dayKey, (dropMap.get(dayKey) || 0) + drop);
    minMap.set(dayKey, (minMap.get(dayKey) || 0) + spanMin);
  }

  function processSessionConsumption(
    sessions,
    dropMap,
    minMap,
    includeSocBands,
    strictInsideWindow,
    totalsBucket,
  ) {
    for (const session of sessions) {
      if (!Number.isFinite(session.startMs) || !Number.isFinite(session.endMs) || session.endMs <= session.startMs) continue;
      if (session.endMs < startMs || session.startMs > endMs) continue;
      if (segmentOverlapsChargingWindow(session.startMs, session.endMs, chargingIntervals)) continue;
      let startPt = null;
      let endPt = null;
      if (strictInsideWindow) {
        // For active sessions, use only telemetry points inside exact session window.
        // This prevents attributing idle/charging drift to active consumption.
        const startIdx = lowerBoundPointIndex(batteryPoints, session.startMs);
        const endIdx = upperBoundPointIndex(batteryPoints, session.endMs);
        if (startIdx < 0 || endIdx < 0 || startIdx >= batteryPoints.length || endIdx >= batteryPoints.length) continue;
        if (startIdx >= endIdx) continue;
        startPt = batteryPoints[startIdx];
        endPt = batteryPoints[endIdx];
      } else {
        // For idle, keep nearest-point pairing so sparse heartbeat telemetry still
        // yields a useful idle baseline instead of disappearing.
        startPt = findNearestBatteryPoint(batteryPoints, session.startMs);
        endPt = findNearestBatteryPoint(batteryPoints, session.endMs);
      }
      if (!startPt || !endPt) continue;
      if (endPt.t <= startPt.t) continue;
      if (isChargingTrue(startPt.charging) || isChargingTrue(endPt.charging)) continue;
      const drop = startPt.b - endPt.b;
      if (drop <= 0.05) continue;
      if (endPt.b > startPt.b + 0.05) continue;
      const spanMin = (session.endMs - session.startMs) / 60000;
      if (!Number.isFinite(spanMin) || spanMin <= 0) continue;
      if (totalsBucket) {
        totalsBucket.drop += drop;
        totalsBucket.min += spanMin;
      }
      const dayKey = startOfLocalDay(Math.min(session.endMs, endMs, nowOpenMs));
      if (dayKey < startOfLocalDay(startMs) || dayKey > startOfLocalDay(endMs)) continue;
      accumulateDailyConsumption(drop, spanMin, dayKey, dropMap, minMap);

      if (includeSocBands) {
        const minPerPct = spanMin / drop;
        if (!Number.isFinite(minPerPct) || minPerPct <= 0 || minPerPct > 1440) continue;
        const midSoc = (startPt.b + endPt.b) / 2;
        const bin = Math.min(9, Math.max(0, Math.floor(midSoc / 10)));
        batterySumMinPerPct[bin] += minPerPct;
        batteryBinSegments[bin] += 1;
      }
    }
  }

  processSessionConsumption(
    activeSessions,
    activeDropSumByDay,
    activeMinSumByDay,
    true,
    true,
    consumptionTotals.active,
  );
  processSessionConsumption(
    idleSessions,
    idleDropSumByDay,
    idleMinSumByDay,
    false,
    false,
    consumptionTotals.idle,
  );

  const avgMinutesPerPercent = batterySumMinPerPct.map((sum, idx) =>
    batteryBinSegments[idx] > 0 ? sum / batteryBinSegments[idx] : null,
  );

  const allTimeActivePctPerMin =
    consumptionTotals.active.min > 0 ? consumptionTotals.active.drop / consumptionTotals.active.min : null;
  const allTimeIdlePctPerMin =
    consumptionTotals.idle.min > 0 ? consumptionTotals.idle.drop / consumptionTotals.idle.min : null;

  const consLabels = dayAxes.map((x) => x.label);
  const consActive = dayAxes.map((d) => {
    const dropSum = activeDropSumByDay.get(d.dayKey) || 0;
    const minSum = activeMinSumByDay.get(d.dayKey) || 0;
    return minSum > 0 ? dropSum / minSum : null;
  });
  const consIdle = dayAxes.map((d) => {
    const dropSum = idleDropSumByDay.get(d.dayKey) || 0;
    const minSum = idleMinSumByDay.get(d.dayKey) || 0;
    return minSum > 0 ? dropSum / minSum : null;
  });

  return {
    meta: {
      rangeStartMs: startMs,
      rangeEndMs: endMs,
      rangeStartIso: new Date(startMs).toISOString(),
      rangeEndIso: new Date(endMs).toISOString(),
      mqttSessionCount: activeSessions.length,
      allTimeConsumptionPctPerMin: {
        active: allTimeActivePctPerMin,
        idle: allTimeIdlePctPerMin,
      },
      generatedAt: new Date().toISOString(),
    },
    activeSessionsPerDay: {
      labels: dayAxes.map((x) => x.label),
      /** Active period time (power_on -> next mqtt_power_off), seconds per local day. */
      valuesActiveSec: activeSecPerDay,
      /** Idle period time (mqtt_power_off -> next power_on), seconds per local day. */
      valuesIdleSec: idleSecPerDay,
    },
    batteryTimePerBand: {
      labels: batteryLabels,
      /** Mean minutes per 1% SOC drop observed in each decile (lower = faster drain). */
      avgMinutesPerPercent,
      segmentCounts: batteryBinSegments,
    },
    cpuLoadOverTime: {
      labels: cpuLabels,
      avgLoadPct: cpuValues,
    },
    consumptionPctPerMin: {
      labels: consLabels,
      activeAvg: consActive,
      idleAvg: consIdle,
    },
  };
}

function emptyChartsPayload(startMs, endMs) {
  const dayAxes = enumerateLocalDays(
    Number.isFinite(startMs) ? startMs : Date.now() - 3 * 86400000,
    Number.isFinite(endMs) ? endMs : Date.now(),
  );
  const labels = dayAxes.map((x) => x.label);
  const zeros = labels.map(() => 0);
  return {
    meta: {
      rangeStartMs: startMs,
      rangeEndMs: endMs,
      mqttSessionCount: 0,
    },
    activeSessionsPerDay: {
      labels,
      valuesActiveSec: zeros,
      valuesIdleSec: zeros,
    },
    batteryTimePerBand: {
      labels: ["0–10", "10–20", "20–30", "30–40", "40–50", "50–60", "60–70", "70–80", "80–90", "90–100"],
      avgMinutesPerPercent: new Array(10).fill(null),
      segmentCounts: new Array(10).fill(0),
    },
    cpuLoadOverTime: { labels: [], avgLoadPct: [] },
    consumptionPctPerMin: { labels: [], activeAvg: [], idleAvg: [] },
  };
}

export { getDashboardCharts };
