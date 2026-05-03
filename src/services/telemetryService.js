import config from "../config.js";
import { getDb } from "./db.js";

function cleanup() {
  const days = config.telemetry.retentionDays;
  if (!days || days <= 0) return;
  const db = getDb();
  const cutoff = new Date(Date.now() - days * 24 * 60 * 60 * 1000).toISOString();
  try {
    const t = db.prepare("DELETE FROM telemetry WHERE created_at < ?").run(cutoff);
    const c = db.prepare("DELETE FROM client_connections WHERE created_at < ?").run(cutoff);
    const h = db.prepare("DELETE FROM rover_heartbeat WHERE created_at < ?").run(cutoff);
    const m = db.prepare("DELETE FROM mqtt_boot_events WHERE created_at < ?").run(cutoff);
    if (t.changes + c.changes + h.changes + m.changes > 0) {
      console.log(
        `Relay retention: telemetry ${t.changes}, client_connections ${c.changes}, rover_heartbeat ${h.changes}, mqtt_boot_events ${m.changes} rows removed`,
      );
    }
  } catch (e) {
    console.warn("Relay cleanup failed:", e.message);
  }
}

let cleanupInterval = null;

export function initTelemetry() {
  if (!config.telemetry.enabled) return;
  try {
    getDb();
    cleanup();
    cleanupInterval = setInterval(cleanup, 60 * 60 * 1000);
  } catch (e) {
    console.warn("Relay telemetry init failed:", e.message);
  }
}

export function closeTelemetry() {
  if (cleanupInterval) {
    clearInterval(cleanupInterval);
    cleanupInterval = null;
  }
}

/**
 * @param {object} health - rover health object (same keys as Pi server).
 * @param {string} [event]
 */
export function recordTelemetry(health, event = "health_report") {
  if (!config.telemetry.enabled || !health) return;
  const db = getDb();
  try {
    const stmt = db.prepare(`
      INSERT INTO telemetry (event, voltage, battery_pct, distance, pan, tilt, cpu_temp, cpu_load, wifi_signal, usb_power)
      VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    `);
    stmt.run(
      event || "health_report",
      health.voltage ?? null,
      health.battery != null ? parseFloat(health.battery) : null,
      health.distance ?? null,
      health.pan ?? null,
      health.tilt ?? null,
      health.cpuTemp ?? null,
      health.cpuLoad ?? null,
      health.wifiSignal ?? null,
      health.usbPower === "on" ? 1 : 0,
    );
  } catch (e) {
    console.warn("Relay telemetry record failed:", e.message);
  }
}

/** Write event-only row into telemetry (all metric fields null). */
export function recordTelemetryEvent(event) {
  if (!config.telemetry.enabled || !event) return;
  const db = getDb();
  try {
    db.prepare("INSERT INTO telemetry (event) VALUES (?)").run(event);
  } catch (e) {
    console.warn("Relay telemetry event record failed:", e.message);
  }
}

/**
 * Returns latest telemetry event row, optionally restricted to a recent window.
 * @param {string} event
 * @param {{ withinMs?: number }} [options]
 */
export function getLatestTelemetryEvent(event, options = {}) {
  if (!config.telemetry.enabled || !event) return null;
  const { withinMs } = options;
  const db = getDb();
  try {
    if (withinMs && withinMs > 0) {
      const cutoff = new Date(Date.now() - withinMs)
        .toISOString()
        .replace("T", " ")
        .replace("Z", "");
      return db
        .prepare(
          "SELECT id, created_at, event FROM telemetry WHERE event = ? AND created_at >= ? ORDER BY id DESC LIMIT 1",
        )
        .get(event, cutoff);
    }
    return db
      .prepare("SELECT id, created_at, event FROM telemetry WHERE event = ? ORDER BY id DESC LIMIT 1")
      .get(event);
  } catch (e) {
    console.warn("Relay telemetry latest event query failed:", e.message);
    return null;
  }
}

export function getTelemetry(options = {}) {
  const { limit = 100, since } = options;
  if (!config.telemetry.enabled) return [];
  const db = getDb();
  try {
    if (since) {
      return db
        .prepare("SELECT * FROM telemetry WHERE created_at >= ? ORDER BY created_at DESC LIMIT ?")
        .all(since, limit);
    }
    return db.prepare("SELECT * FROM telemetry ORDER BY created_at DESC LIMIT ?").all(limit);
  } catch (e) {
    console.warn("Relay telemetry query failed:", e.message);
    return [];
  }
}

export function getTelemetryPage(options = {}) {
  const { page = 1, pageSize = 50, since } = options;
  if (!config.telemetry.enabled) {
    return { telemetry: [], total: 0, page: 1, pageSize };
  }
  const db = getDb();
  const safePage = Math.max(1, page);
  const safePageSize = Math.max(1, pageSize);
  const offset = (safePage - 1) * safePageSize;
  try {
    if (since) {
      const totalRow = db
        .prepare("SELECT COUNT(*) AS count FROM telemetry WHERE created_at >= ?")
        .get(since);
      const rows = db
        .prepare(
          "SELECT * FROM telemetry WHERE created_at >= ? ORDER BY created_at DESC, id DESC LIMIT ? OFFSET ?",
        )
        .all(since, safePageSize, offset);
      return { telemetry: rows, total: totalRow?.count ?? 0, page: safePage, pageSize: safePageSize };
    }
    const totalRow = db.prepare("SELECT COUNT(*) AS count FROM telemetry").get();
    const rows = db
      .prepare("SELECT * FROM telemetry ORDER BY created_at DESC, id DESC LIMIT ? OFFSET ?")
      .all(safePageSize, offset);
    return { telemetry: rows, total: totalRow?.count ?? 0, page: safePage, pageSize: safePageSize };
  } catch (e) {
    console.warn("Relay telemetry paged query failed:", e.message);
    return { telemetry: [], total: 0, page: safePage, pageSize: safePageSize };
  }
}

export function recordClientConnection(payload) {
  if (!config.telemetry.enabled) return;
  const db = getDb();
  try {
    const stmt = db.prepare(`
      INSERT INTO client_connections (event, client_ip, user_agent, device_info, location_info)
      VALUES (?, ?, ?, ?, ?)
    `);
    stmt.run(
      payload.event ?? "connect",
      payload.clientIp ?? null,
      payload.userAgent ?? null,
      payload.deviceInfo != null ? JSON.stringify(payload.deviceInfo) : null,
      payload.locationInfo != null ? JSON.stringify(payload.locationInfo) : null,
    );
  } catch (e) {
    console.warn("Relay client_connection record failed:", e.message);
  }
}
