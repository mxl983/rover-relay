import { Router } from "express";
import {
  recordTelemetry,
  getTelemetry,
  getTelemetryPage,
  recordClientConnection,
  recordExperimentVoltageSample,
  getExperimentVoltageSamples,
  getExperimentVoltageSummary,
  analyzeExperimentVoltageDataset,
  clearExperimentVoltageSamples,
} from "../services/telemetryService.js";
import { experimentCollectionService } from "../services/experimentCollectionService.js";
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

router.post("/experiments/voltage-sample", (req, res) => {
  const {
    voltage,
    telemetryVoltage,
    labelCharging,
    sessionId,
    voltage1dp,
    adcMvAvg,
    adcRawMin,
    adcRawMax,
    source,
  } =
    req.body || {};
  if (!Number.isFinite(Number(voltage))) {
    return error(res, "body.voltage must be a number", 400);
  }
  if (!Number.isFinite(Number(telemetryVoltage))) {
    return error(res, "body.telemetryVoltage must be a number", 400);
  }
  if (!(labelCharging === true || labelCharging === false)) {
    return error(res, "body.labelCharging must be boolean", 400);
  }
  const id = recordExperimentVoltageSample({
    voltage: Number(voltage),
    telemetryVoltage: Number(telemetryVoltage),
    labelCharging,
    sessionId,
    voltage1dp,
    adcMvAvg,
    adcRawMin,
    adcRawMax,
    source,
  });
  if (!id) return error(res, "failed to record sample", 500);
  return success(res, { recorded: true, id });
});

router.get("/experiments/voltage-samples", (req, res) => {
  const limit = Math.min(Math.max(1, parseInt(req.query.limit, 10) || 1000), 10000);
  const samples = getExperimentVoltageSamples({ limit });
  return success(res, { samples });
});

router.get("/experiments/voltage-summary", (req, res) => {
  const summary = getExperimentVoltageSummary();
  return success(res, { summary });
});

router.get("/experiments/analysis", (req, res) => {
  const analysis = analyzeExperimentVoltageDataset();
  return success(res, { analysis });
});

router.delete("/experiments/voltage-samples", (req, res) => {
  const result = clearExperimentVoltageSamples();
  if (result.error) return error(res, result.error, 500);
  const collection = experimentCollectionService.resetStats();
  return success(res, { cleared: true, deleted: result.deleted, collection });
});

router.get("/experiments/collection", (req, res) => {
  return success(res, { collection: experimentCollectionService.getStatus() });
});

router.post("/experiments/collection/start", (req, res) => {
  const { labelCharging } = req.body || {};
  if (labelCharging !== undefined && !(labelCharging === true || labelCharging === false)) {
    return error(res, "body.labelCharging must be boolean when provided", 400);
  }
  const collection = experimentCollectionService.startCollection({ labelCharging });
  return success(res, { started: true, collection });
});

router.post("/experiments/collection/stop", (req, res) => {
  const collection = experimentCollectionService.stopCollection();
  return success(res, { stopped: true, collection });
});

export default router;
