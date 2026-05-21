import config from "../config.js";
import { inferChargingFromLedWebcam } from "./ledWebcamChargingService.js";
import { recordTelemetryEvent } from "./telemetryService.js";
import { setChargingLedSnapshot } from "./chargingSnapshot.js";

let timer = null;
/** @type {boolean | null} last definite charging state for edge detection */
let lastDefiniteCharging = null;
let initialized = false;

function logTransition(nowCharging) {
  if (nowCharging) {
    recordTelemetryEvent("charging_start", 1);
    console.log("[charging-telemetry] charging_start");
  } else {
    recordTelemetryEvent("charging_end", 0);
    console.log("[charging-telemetry] charging_end");
  }
}

async function pollOnce() {
  if (!config.telemetry.enabled) return;
  try {
    const ch = await inferChargingFromLedWebcam();
    const definite =
      ch.isCharging === true ? true : ch.isCharging === false ? false : null;

    if (definite === true) setChargingLedSnapshot(true);
    else if (definite === false) setChargingLedSnapshot(false);
    /* unknown: keep previous cache — do not clear snapshot */

    if (definite === null) return;

    if (!initialized) {
      initialized = true;
      lastDefiniteCharging = definite;
      return;
    }

    if (lastDefiniteCharging !== definite) {
      logTransition(definite);
      lastDefiniteCharging = definite;
    }
  } catch (e) {
    console.warn("[charging-telemetry] poll failed:", e.message);
  }
}

export function startChargingTelemetryLogger() {
  if (timer != null) return;
  if (!config.telemetry.enabled) return;
  const ms = Math.max(10_000, config.telemetry.chargingPollMs);
  void pollOnce();
  timer = setInterval(() => {
    void pollOnce();
  }, ms);
}

export function stopChargingTelemetryLogger() {
  if (timer != null) {
    clearInterval(timer);
    timer = null;
  }
}
