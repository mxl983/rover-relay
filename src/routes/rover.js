import { Router } from "express";
import { recordHeartbeat, getRoverState } from "../services/roverStateService.js";
import { readEnvironmentFromBackupCam } from "../services/roverEnvironmentService.js";
import {
  recordClientLocation,
  computeClientDistance,
} from "../services/clientRoverDistanceService.js";
import { requireToken } from "../middleware/auth.js";
import { success, error } from "../utils/apiResponse.js";

const router = Router();

router.post("/heartbeat", requireToken, (req, res) => {
  const body = req.body || {};
  if (body.phase && body.phase !== "booting" && body.phase !== "ready") {
    return error(res, "phase must be booting, ready, or omitted", 400);
  }
  recordHeartbeat(body);
  success(res, { ok: true });
});

/**
 * Client shares WGS84 coords (browser geolocation); relay returns distance to fixed rover site (meters).
 * POST updates the cached location included in /state and WebSocket heartbeats.
 */
router.post("/client-distance", (req, res) => {
  const { latitude, longitude, accuracy } = req.body || {};
  const result = recordClientLocation({ latitude, longitude, accuracy });
  if (!result) {
    return error(res, "latitude and longitude required (valid WGS84 degrees)", 400);
  }
  success(res, result);
});

router.get("/client-distance", (req, res) => {
  const { latitude, longitude } = req.query || {};
  const result = computeClientDistance({ latitude, longitude });
  if (!result) {
    return error(res, "latitude and longitude query params required (valid WGS84 degrees)", 400);
  }
  success(res, result);
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
