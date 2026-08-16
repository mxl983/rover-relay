import { Router } from "express";
import { recordTelemetry } from "../services/telemetryService.js";
import { recordHeartbeat } from "../services/roverStateService.js";
import { requireToken } from "../middleware/auth.js";
import { success, error } from "../utils/apiResponse.js";

const router = Router();

/**
 * Single call from the Pi: store telemetry row + heartbeat (state / battery drain).
 */
router.post("/", requireToken, (req, res) => {
  const { health, event, phase, bootStartedAt } = req.body || {};
  if (!health || typeof health !== "object") {
    return error(res, "body.health object required", 400);
  }
  if (phase && phase !== "booting" && phase !== "ready") {
    return error(res, "phase must be booting, ready, or omitted", 400);
  }
  recordTelemetry(health, event || "health_report");
  recordHeartbeat({ phase, bootStartedAt, health });
  success(res, { recorded: true });
});

export default router;
