import config from "../config.js";
import { getDb } from "./db.js";
import { getRoverState } from "./roverStateService.js";
import { recordExperimentVoltageSample } from "./telemetryService.js";

let intervalHandle = null;
let running = false;
let inFlight = false;

function sanitizeState(row) {
  const forced = row?.forced_label_charging;
  return {
    enabled: row?.enabled === 1,
    sessionId: row?.session_id || null,
    savedCount: Number(row?.saved_count) || 0,
    droppedCount: Number(row?.dropped_count) || 0,
    lastError: row?.last_error || null,
    lastSampleAt: row?.last_sample_at || null,
    labelMode: row?.label_mode || (forced === 0 || forced === 1 ? "manual" : "auto"),
    forcedLabelCharging: forced === 1 ? true : forced === 0 ? false : null,
    running,
    intervalMs: 1000,
  };
}

function readState() {
  const db = getDb();
  const row = db.prepare("SELECT * FROM experiment_collection_state WHERE id = 1").get();
  return sanitizeState(row);
}

function patchState(fields) {
  const db = getDb();
  const updates = [];
  const values = [];
  for (const [key, value] of Object.entries(fields)) {
    updates.push(`${key} = ?`);
    values.push(value);
  }
  updates.push("updated_at = datetime('now')");
  db.prepare(`UPDATE experiment_collection_state SET ${updates.join(", ")} WHERE id = 1`).run(...values);
  return readState();
}

function latestTelemetryVoltage() {
  const db = getDb();
  const row = db
    .prepare("SELECT voltage FROM telemetry WHERE voltage IS NOT NULL ORDER BY id DESC LIMIT 1")
    .get();
  const v = Number(row?.voltage);
  return Number.isFinite(v) ? v : null;
}

async function readEspVoltage() {
  const ctl = new AbortController();
  const timer = setTimeout(() => ctl.abort(), 1800);
  try {
    const response = await fetch(config.backupCam.voltageUrl, {
      method: "GET",
      headers: { accept: "application/json", "user-agent": "rover-relay-experiment-collector/1.0" },
      signal: ctl.signal,
    });
    if (!response.ok) return { ok: false, error: `ESP HTTP ${response.status}` };
    const json = await response.json();
    const ev = Number(json?.voltage);
    if (!Number.isFinite(ev)) return { ok: false, error: "ESP voltage invalid" };
    return { ok: true, espVoltage: ev, voltage1dp: json?.voltage_1dp, adcMvAvg: json?.adc_mv_avg };
  } catch (e) {
    return { ok: false, error: e?.name === "AbortError" ? "ESP voltage timeout" : e?.message || "ESP failed" };
  } finally {
    clearTimeout(timer);
  }
}

async function sampleOnce() {
  if (inFlight) return;
  inFlight = true;
  try {
    const telemetryVoltage = latestTelemetryVoltage();
    if (!Number.isFinite(telemetryVoltage) || telemetryVoltage <= 0) {
      patchState({
        dropped_count: readState().droppedCount + 1,
        last_error: "Dropped sample: telemetry voltage invalid",
      });
      return;
    }

    const esp = await readEspVoltage();
    if (!esp.ok || !Number.isFinite(esp.espVoltage) || esp.espVoltage <= 0) {
      patchState({
        dropped_count: readState().droppedCount + 1,
        last_error: esp.error || "Dropped sample: ESP voltage invalid",
      });
      return;
    }

    const state = readState();
    const charging =
      state.forcedLabelCharging === true || state.forcedLabelCharging === false
        ? state.forcedLabelCharging
        : getRoverState()?.charging?.isCharging;
    if (!(charging === true || charging === false)) {
      patchState({
        dropped_count: readState().droppedCount + 1,
        last_error: "Dropped sample: charging label unknown",
      });
      return;
    }

    const id = recordExperimentVoltageSample({
      sessionId: state.sessionId,
      labelCharging: charging,
      voltage: esp.espVoltage,
      telemetryVoltage,
      voltage1dp: esp.voltage1dp,
      adcMvAvg: esp.adcMvAvg,
      source: "continuous_collector",
    });

    if (!id) {
      patchState({
        dropped_count: readState().droppedCount + 1,
        last_error: "Dropped sample: DB write failed",
      });
      return;
    }

    patchState({
      saved_count: readState().savedCount + 1,
      last_error: null,
      last_sample_at: new Date().toISOString(),
    });
  } finally {
    inFlight = false;
  }
}

function startRuntimeLoop() {
  if (running || !config.telemetry.enabled) return;
  running = true;
  sampleOnce();
  intervalHandle = setInterval(sampleOnce, 1000);
}

function stopRuntimeLoop() {
  running = false;
  if (intervalHandle) {
    clearInterval(intervalHandle);
    intervalHandle = null;
  }
}

export const experimentCollectionService = {
  startFromPersistedState() {
    const state = readState();
    if (state.enabled) startRuntimeLoop();
  },
  startCollection({ labelCharging = null } = {}) {
    const hasManualLabel = labelCharging === true || labelCharging === false;
    const next = patchState({
      enabled: 1,
      session_id: `continuous-${Date.now().toString(36)}`,
      forced_label_charging: hasManualLabel ? (labelCharging ? 1 : 0) : null,
      label_mode: hasManualLabel ? "manual" : "auto",
      last_error: null,
    });
    startRuntimeLoop();
    return { ...next, running };
  },
  stopCollection() {
    const next = patchState({ enabled: 0 });
    stopRuntimeLoop();
    return { ...next, running };
  },
  getStatus() {
    return readState();
  },
  resetStats() {
    return patchState({
      saved_count: 0,
      dropped_count: 0,
      last_error: null,
      last_sample_at: null,
    });
  },
  stop() {
    stopRuntimeLoop();
  },
};
