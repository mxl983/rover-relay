import { Router } from "express";
import { recordHeartbeat, getRoverState } from "../services/roverStateService.js";
import { requireToken } from "../middleware/auth.js";
import config from "../config.js";
import { success, error } from "../utils/apiResponse.js";

const router = Router();

async function readEnvironmentFromBackupCam() {
  const primaryUrl = config.backupCam.realtimeUrl || "http://192.168.1.220:82/realtime";
  const fallbackUrl = primaryUrl.endsWith("/realtime")
    ? primaryUrl.replace(/\/realtime$/, "/environment")
    : null;
  const candidates = fallbackUrl ? [primaryUrl, fallbackUrl] : [primaryUrl];

  let lastError = "upstream_failed";
  for (const url of candidates) {
    const controller = new AbortController();
    const timeout = setTimeout(() => controller.abort(), 2500);
    try {
      const upstream = await fetch(url, {
        method: "GET",
        headers: { accept: "application/json", "user-agent": "rover-relay/1.0" },
        signal: controller.signal,
      });
      if (!upstream.ok) {
        lastError = `upstream HTTP ${upstream.status}`;
        continue;
      }
      const data = await upstream.json();
      if (!data || data.ok !== true) {
        lastError = data?.error || "sensor_not_ready";
        continue;
      }
      const temperatureC = Number(data.temperature_c);
      const pressureHpa = Number(data.pressure_hpa);
      if (!Number.isFinite(temperatureC) || !Number.isFinite(pressureHpa)) {
        lastError = "invalid_upstream_payload";
        continue;
      }
      return {
        environment: {
          temperatureC,
          pressureHpa,
          sensor: typeof data.sensor === "string" ? data.sensor : null,
          i2cAddr: typeof data.i2c_addr === "string" ? data.i2c_addr : null,
          source: "backup_cam_realtime",
        },
        error: null,
      };
    } catch (e) {
      lastError = e?.name === "AbortError" ? "upstream_timeout" : e?.message || "upstream_failed";
    } finally {
      clearTimeout(timeout);
    }
  }
  return { environment: null, error: lastError };
}

router.post("/heartbeat", requireToken, (req, res) => {
  const body = req.body || {};
  if (body.phase && body.phase !== "booting" && body.phase !== "ready") {
    return error(res, "phase must be booting, ready, or omitted", 400);
  }
  recordHeartbeat(body);
  success(res, { ok: true });
});

router.get("/state", async (req, res) => {
  const rover = await getRoverState();
  const { environment, error: environmentError } = await readEnvironmentFromBackupCam();
  success(res, {
    rover: {
      ...rover,
      environment,
      environmentError,
    },
  });
});

/** Battery-derived charging detection (same object as `rover.charging` on `/state`). */
router.get("/charging", async (req, res) => {
  const rover = await getRoverState();
  success(res, { charging: rover.charging });
});

/** Relay-side passthrough for ESP realtime temp/pressure (dashboard-friendly). */
router.get("/environment", async (req, res) => {
  const { environment, error: environmentError } = await readEnvironmentFromBackupCam();
  if (!environment) return error(res, environmentError || "upstream_failed", 502);
  return success(res, { environment });
});

export default router;
