import { Router } from "express";
import { recordHeartbeat, getRoverState } from "../services/roverStateService.js";
import { readEnvironmentFromBackupCam } from "../services/roverEnvironmentService.js";
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
