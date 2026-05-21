import { Router } from "express";
import {
  recordTelemetry,
  getTelemetry,
  getTelemetryPage,
  recordClientConnection,
} from "../services/telemetryService.js";
import { getTelemetrySessionSnapshot } from "../services/telemetrySessionStamp.js";
import { getDashboardCharts } from "../services/telemetryChartsService.js";
import { getLatestSessionBatteryCharts } from "../services/telemetrySessionsService.js";
import { requireToken } from "../middleware/auth.js";
import { success, error } from "../utils/apiResponse.js";

const router = Router();

router.post("/ingest", requireToken, (req, res) => {
  const { health, event } = req.body || {};
  if (!health || typeof health !== "object") {
    return error(res, "body.health object required", 400);
  }
  recordTelemetry(health, event || "health_report");
  success(res, { recorded: true });
});

router.post("/client-connection", requireToken, (req, res) => {
  recordClientConnection(req.body || {});
  success(res, { recorded: true });
});

router.get("/session", (req, res) => {
  const snap = getTelemetrySessionSnapshot();
  success(res, snap);
});

router.get("/sessions/latest", (req, res) => {
  const limit = Math.min(Math.max(1, parseInt(req.query.limit, 10) || 5), 10);
  const data = getLatestSessionBatteryCharts({ limit });
  success(res, data);
});

router.get("/charts", (req, res) => {
  let startMs;
  let endMs;
  if (req.query.from) {
    const t = Date.parse(String(req.query.from));
    if (Number.isFinite(t)) startMs = t;
  }
  if (req.query.to) {
    const t = Date.parse(String(req.query.to));
    if (Number.isFinite(t)) endMs = t;
  }
  const charts = getDashboardCharts({ startMs, endMs });
  success(res, charts);
});

router.get("/", (req, res) => {
  const limit = Math.min(Math.max(1, parseInt(req.query.limit, 10) || 100), 2000);
  const since = req.query.since || null;
  const rawPage = Math.max(1, parseInt(req.query.page, 10) || 1);
  const page = Math.min(rawPage, 25);
  const pageSize = Math.min(Math.max(1, parseInt(req.query.pageSize, 10) || 50), 50);

  if (req.query.page !== undefined || req.query.pageSize !== undefined) {
    const result = getTelemetryPage({ page, pageSize, since });
    const totalPages = Math.min(25, Math.max(1, Math.ceil(result.total / pageSize)));
    success(res, {
      telemetry: result.telemetry,
      pagination: {
        page: Math.min(page, totalPages),
        pageSize,
        totalItems: result.total,
        totalPages,
        maxPages: 25,
      },
    });
    return;
  }

  const data = getTelemetry({ limit, since });
  success(res, { telemetry: data, pagination: null });
});

export default router;
