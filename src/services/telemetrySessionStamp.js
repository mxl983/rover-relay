import { randomUUID } from "node:crypto";
import { getDb } from "./db.js";

const ON = new Set(["mqtt_power_on", "power_on"]);
const OFF = new Set(["mqtt_power_off", "power_off"]);

/**
 * Tracks MQTT link phase for telemetry rows (server-side only).
 * session_id is a UUID per segment; new UUID when crossing idle↔active via power events.
 * session_active: 1 = MQTT active segment, 0 = idle segment.
 */
let hydrated = false;
/** @type {{ segmentId: string | null, active: boolean }} */
let sessionState = { segmentId: null, active: true };

function newSegmentId() {
  return randomUUID();
}

function applyBoundaryFromHistory(event) {
  const e = event || "";
  if (ON.has(e)) {
    sessionState.active = true;
  } else if (OFF.has(e)) {
    sessionState.active = false;
  }
}

/** For vitest module resets — internal */
export function resetTelemetrySessionStampForTests() {
  hydrated = false;
  sessionState = { segmentId: null, active: true };
}

function assignCurrentSegmentIdAfterHydrate() {
  if (!sessionState.segmentId) {
    sessionState.segmentId = newSegmentId();
  }
}

function ensureHydrated() {
  if (hydrated) return;
  hydrated = true;
  const db = getDb();
  const last = db
    .prepare(
      `SELECT session_id, session_active FROM telemetry WHERE session_id IS NOT NULL AND session_id != '' ORDER BY id DESC LIMIT 1`,
    )
    .get();
  if (last) {
    sessionState = {
      segmentId: String(last.session_id),
      active: last.session_active === 1,
    };
    return;
  }
  const rows = db
    .prepare(
      `SELECT event FROM telemetry WHERE event IN ('mqtt_power_on','mqtt_power_off','power_on','power_off') ORDER BY id ASC`,
    )
    .all();
  sessionState = { segmentId: null, active: true };
  for (const row of rows) {
    applyBoundaryFromHistory(row.event);
  }
  assignCurrentSegmentIdAfterHydrate();
}

/**
 * Run once at relay startup so the current active or idle segment already has a UUID
 * before any telemetry row is inserted.
 */
export function warmTelemetrySessionStamp() {
  ensureHydrated();
}

/** Current segment UUID and phase (after warm / hydrate). */
export function getTelemetrySessionSnapshot() {
  warmTelemetrySessionStamp();
  return {
    session_id: sessionState.segmentId,
    session_active: sessionState.active ? 1 : 0,
  };
}

/**
 * Compute columns for the row about to be inserted (mutates session on boundary events).
 * @param {string} [event]
 * @returns {{ sessionId: string, sessionActive: number }}
 */
export function stampTelemetrySession(event) {
  ensureHydrated();
  const e = event || "";
  if (ON.has(e)) {
    if (!sessionState.active) {
      sessionState.segmentId = newSegmentId();
    }
    sessionState.active = true;
    return { sessionId: sessionState.segmentId, sessionActive: 1 };
  }
  if (OFF.has(e)) {
    if (sessionState.active) {
      sessionState.segmentId = newSegmentId();
    }
    sessionState.active = false;
    return { sessionId: sessionState.segmentId, sessionActive: 0 };
  }
  return { sessionId: sessionState.segmentId, sessionActive: sessionState.active ? 1 : 0 };
}
