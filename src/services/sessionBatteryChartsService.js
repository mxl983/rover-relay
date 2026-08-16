import config from "../config.js";
import { getDb } from "./db.js";
import { remapReportedBatteryPctRounded } from "../utils/batteryPctScale.js";

/** Parse telemetry created_at to epoch ms (UTC Z suffix). */
function parseTs(createdAt) {
  if (!createdAt) return null;
  const raw = String(createdAt);
  const normalized = raw.includes("T") ? raw : raw.replace(" ", "T");
  const t =
    Date.parse(normalized.endsWith("Z") ? normalized : `${normalized}Z`) ||
    Date.parse(raw);
  return Number.isFinite(t) ? t : null;
}

function toIso(createdAt) {
  const t = parseTs(createdAt);
  return t == null ? null : new Date(t).toISOString();
}

function isValidBatteryPct(value) {
  const n = Number(value);
  return Number.isFinite(n) && n >= 0 && n <= 100;
}

/** Same anchor as rover state `lastCharging.at` (latest charging_start or charging_end). */
function getLastChargingAnchor(db) {
  const row = db
    .prepare(
      `SELECT created_at, event
       FROM telemetry
       WHERE event IN ('charging_start', 'charging_end')
       ORDER BY datetime(created_at) DESC, id DESC
       LIMIT 1`,
    )
    .get();
  if (!row) return null;
  const ms = parseTs(row.created_at);
  if (ms == null) return null;
  return { ms, event: row.event, atIso: new Date(ms).toISOString() };
}

/**
 * Latest telemetry segments (session_id) with battery % time series for dashboard charts.
 * X-axis values are minutes elapsed since last charging (0 at anchor).
 * @param {{ limit?: number }} [options]
 */
export function getLatestSessionBatteryCharts(options = {}) {
  const limit = Math.min(Math.max(1, Number(options.limit) || 5), 10);
  if (!config.telemetry.enabled) {
    return { sessions: [], lastCharging: null };
  }

  const db = getDb();
  const lastCharging = getLastChargingAnchor(db);
  const anchorMs = lastCharging?.ms ?? null;

  let sessionRows = [];
  try {
    sessionRows = db
      .prepare(
        `SELECT session_id,
                MAX(session_active) AS session_active,
                MIN(created_at) AS start_at,
                MAX(created_at) AS end_at,
                MIN(battery_pct) AS min_battery_pct,
                MAX(battery_pct) AS max_battery_pct,
                SUM(CASE WHEN battery_pct IS NOT NULL AND battery_pct >= 0 AND battery_pct <= 100 THEN 1 ELSE 0 END) AS battery_points,
                MAX(id) AS last_id
         FROM telemetry
         WHERE session_id IS NOT NULL AND session_id != ''
         GROUP BY session_id
         HAVING battery_points >= 2
         ORDER BY last_id DESC
         LIMIT ?`,
      )
      .all(limit);
  } catch (e) {
    console.warn("Relay latest session query failed:", e.message);
    return { sessions: [], lastCharging };
  }

  const pointsStmt = db.prepare(
    `SELECT created_at, battery_pct, charging
     FROM telemetry
     WHERE session_id = ?
       AND battery_pct IS NOT NULL
       AND battery_pct >= 0
       AND battery_pct <= 100
     ORDER BY created_at ASC, id ASC`,
  );

  const sessions = [];
  for (const row of sessionRows) {
    try {
      const points = pointsStmt.all(row.session_id);
      if (points.length < 2) continue;

      const sessionAnchorMs = anchorMs ?? parseTs(row.start_at);
      const timestampsIso = [];
      const batteryPct = [];
      const elapsedMin = [];
      const charging = [];

      for (const p of points) {
        if (!isValidBatteryPct(p.battery_pct)) continue;
        const iso = toIso(p.created_at);
        const t = parseTs(p.created_at);
        if (!iso || t == null) continue;
        if (sessionAnchorMs != null && t < sessionAnchorMs) continue;

        const elapsedSec =
          sessionAnchorMs != null ? Math.max(0, (t - sessionAnchorMs) / 1000) : 0;
        timestampsIso.push(iso);
        const mapped = remapReportedBatteryPctRounded(p.battery_pct);
        if (mapped == null) continue;
        batteryPct.push(mapped);
        elapsedMin.push(elapsedSec / 60);
        charging.push(p.charging === 1 || p.charging === "1" ? 1 : p.charging === 0 || p.charging === "0" ? 0 : null);
      }

      if (batteryPct.length < 2) continue;

      const minBatteryPct = Math.min(...batteryPct);
      const maxBatteryPct = Math.max(...batteryPct);

      const startIso = toIso(row.start_at);
      const endIso = toIso(row.end_at);
      const startMs = parseTs(row.start_at);
      const endMs = parseTs(row.end_at);
      const durationSec =
        startMs != null && endMs != null && endMs > startMs ? (endMs - startMs) / 1000 : null;

      sessions.push({
        sessionId: row.session_id,
        sessionActive: row.session_active === 1 ? 1 : 0,
        startIso,
        endIso,
        durationSec,
        minBatteryPct,
        maxBatteryPct,
        batteryDropPct: maxBatteryPct - minBatteryPct,
        series: { timestampsIso, batteryPct, elapsedMin, charging },
      });
    } catch (e) {
      console.warn(`Relay session series failed for ${row.session_id}:`, e.message);
    }
  }

  return {
    sessions,
    lastCharging: lastCharging
      ? { at: lastCharging.atIso, event: lastCharging.event }
      : null,
  };
}
