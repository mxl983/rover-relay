import config from "../config.js";
import { getDb } from "./db.js";
import { recordRuntimeVoltageSample } from "./telemetryService.js";

let intervalHandle = null;
let running = false;

function latestTelemetryVoltage() {
  const db = getDb();
  const row = db
    .prepare("SELECT voltage FROM telemetry WHERE voltage IS NOT NULL ORDER BY id DESC LIMIT 1")
    .get();
  const v = Number(row?.voltage);
  return Number.isFinite(v) ? v : null;
}

async function sampleOnce() {
  const telemetryVoltage = latestTelemetryVoltage();
  if (!Number.isFinite(telemetryVoltage)) return;
  try {
    const response = await fetch(config.backupCam.voltageUrl, {
      method: "GET",
      headers: { accept: "application/json", "user-agent": "rover-relay-runtime-sampler/1.0" },
    });
    if (!response.ok) return;
    const data = await response.json();
    const espVoltage = Number(data?.voltage);
    if (!Number.isFinite(espVoltage)) return;
    recordRuntimeVoltageSample({ espVoltage, telemetryVoltage });
  } catch {
    // Non-fatal; runtime model can fall back to heuristic charging detection.
  }
}

export const runtimeVoltageService = {
  start() {
    if (running || !config.telemetry.enabled) return;
    running = true;
    sampleOnce();
    const intervalMs = Math.max(250, Number(config.telemetry.runtimeSampleIntervalMs) || 500);
    intervalHandle = setInterval(sampleOnce, intervalMs);
  },
  stop() {
    running = false;
    if (intervalHandle) {
      clearInterval(intervalHandle);
      intervalHandle = null;
    }
  },
};

