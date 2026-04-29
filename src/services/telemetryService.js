import config from "../config.js";
import { getDb } from "./db.js";

function cleanup() {
  const days = config.telemetry.retentionDays;
  if (!days || days <= 0) return;
  const db = getDb();
  const cutoff = new Date(Date.now() - days * 24 * 60 * 60 * 1000).toISOString();
  const runtimeDays = config.telemetry.runtimeRetentionDays || days;
  const experimentDays = config.telemetry.experimentRetentionDays || days;
  const runtimeCutoff = new Date(Date.now() - runtimeDays * 24 * 60 * 60 * 1000).toISOString();
  const experimentCutoff = new Date(Date.now() - experimentDays * 24 * 60 * 60 * 1000).toISOString();
  try {
    const t = db.prepare("DELETE FROM telemetry WHERE created_at < ?").run(cutoff);
    const c = db.prepare("DELETE FROM client_connections WHERE created_at < ?").run(cutoff);
    const h = db.prepare("DELETE FROM rover_heartbeat WHERE created_at < ?").run(cutoff);
    const m = db.prepare("DELETE FROM mqtt_boot_events WHERE created_at < ?").run(cutoff);
    const e = db
      .prepare("DELETE FROM experiment_voltage_samples WHERE created_at < ?")
      .run(experimentCutoff);
    const rv = db
      .prepare("DELETE FROM runtime_voltage_samples WHERE created_at < ?")
      .run(runtimeCutoff);
    if (t.changes + c.changes + h.changes + m.changes + e.changes + rv.changes > 0) {
      console.log(
        `Relay retention: telemetry ${t.changes}, client_connections ${c.changes}, rover_heartbeat ${h.changes}, mqtt_boot_events ${m.changes}, experiment_voltage_samples ${e.changes}, runtime_voltage_samples ${rv.changes} rows removed`,
      );
    }
  } catch (e) {
    console.warn("Relay cleanup failed:", e.message);
  }
}

let cleanupInterval = null;

export function initTelemetry() {
  if (!config.telemetry.enabled) return;
  try {
    getDb();
    cleanup();
    cleanupInterval = setInterval(cleanup, 60 * 60 * 1000);
  } catch (e) {
    console.warn("Relay telemetry init failed:", e.message);
  }
}

export function closeTelemetry() {
  if (cleanupInterval) {
    clearInterval(cleanupInterval);
    cleanupInterval = null;
  }
}

/**
 * @param {object} health - rover health object (same keys as Pi server).
 * @param {string} [event]
 */
export function recordTelemetry(health, event = "health_report") {
  if (!config.telemetry.enabled || !health) return;
  const db = getDb();
  try {
    const stmt = db.prepare(`
      INSERT INTO telemetry (event, voltage, battery_pct, distance, pan, tilt, cpu_temp, cpu_load, wifi_signal, usb_power)
      VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    `);
    stmt.run(
      event || "health_report",
      health.voltage ?? null,
      health.battery != null ? parseFloat(health.battery) : null,
      health.distance ?? null,
      health.pan ?? null,
      health.tilt ?? null,
      health.cpuTemp ?? null,
      health.cpuLoad ?? null,
      health.wifiSignal ?? null,
      health.usbPower === "on" ? 1 : 0,
    );
  } catch (e) {
    console.warn("Relay telemetry record failed:", e.message);
  }
}

/** Write event-only row into telemetry (all metric fields null). */
export function recordTelemetryEvent(event) {
  if (!config.telemetry.enabled || !event) return;
  const db = getDb();
  try {
    db.prepare("INSERT INTO telemetry (event) VALUES (?)").run(event);
  } catch (e) {
    console.warn("Relay telemetry event record failed:", e.message);
  }
}

/**
 * Returns latest telemetry event row, optionally restricted to a recent window.
 * @param {string} event
 * @param {{ withinMs?: number }} [options]
 */
export function getLatestTelemetryEvent(event, options = {}) {
  if (!config.telemetry.enabled || !event) return null;
  const { withinMs } = options;
  const db = getDb();
  try {
    if (withinMs && withinMs > 0) {
      const cutoff = new Date(Date.now() - withinMs)
        .toISOString()
        .replace("T", " ")
        .replace("Z", "");
      return db
        .prepare(
          "SELECT id, created_at, event FROM telemetry WHERE event = ? AND created_at >= ? ORDER BY id DESC LIMIT 1",
        )
        .get(event, cutoff);
    }
    return db
      .prepare("SELECT id, created_at, event FROM telemetry WHERE event = ? ORDER BY id DESC LIMIT 1")
      .get(event);
  } catch (e) {
    console.warn("Relay telemetry latest event query failed:", e.message);
    return null;
  }
}

export function getTelemetry(options = {}) {
  const { limit = 100, since } = options;
  if (!config.telemetry.enabled) return [];
  const db = getDb();
  try {
    if (since) {
      return db
        .prepare("SELECT * FROM telemetry WHERE created_at >= ? ORDER BY created_at DESC LIMIT ?")
        .all(since, limit);
    }
    return db.prepare("SELECT * FROM telemetry ORDER BY created_at DESC LIMIT ?").all(limit);
  } catch (e) {
    console.warn("Relay telemetry query failed:", e.message);
    return [];
  }
}

export function getTelemetryPage(options = {}) {
  const { page = 1, pageSize = 50, since } = options;
  if (!config.telemetry.enabled) {
    return { telemetry: [], total: 0, page: 1, pageSize };
  }
  const db = getDb();
  const safePage = Math.max(1, page);
  const safePageSize = Math.max(1, pageSize);
  const offset = (safePage - 1) * safePageSize;
  try {
    if (since) {
      const totalRow = db
        .prepare("SELECT COUNT(*) AS count FROM telemetry WHERE created_at >= ?")
        .get(since);
      const rows = db
        .prepare(
          "SELECT * FROM telemetry WHERE created_at >= ? ORDER BY created_at DESC, id DESC LIMIT ? OFFSET ?",
        )
        .all(since, safePageSize, offset);
      return { telemetry: rows, total: totalRow?.count ?? 0, page: safePage, pageSize: safePageSize };
    }
    const totalRow = db.prepare("SELECT COUNT(*) AS count FROM telemetry").get();
    const rows = db
      .prepare("SELECT * FROM telemetry ORDER BY created_at DESC, id DESC LIMIT ? OFFSET ?")
      .all(safePageSize, offset);
    return { telemetry: rows, total: totalRow?.count ?? 0, page: safePage, pageSize: safePageSize };
  } catch (e) {
    console.warn("Relay telemetry paged query failed:", e.message);
    return { telemetry: [], total: 0, page: safePage, pageSize: safePageSize };
  }
}

export function recordClientConnection(payload) {
  if (!config.telemetry.enabled) return;
  const db = getDb();
  try {
    const stmt = db.prepare(`
      INSERT INTO client_connections (event, client_ip, user_agent, device_info, location_info)
      VALUES (?, ?, ?, ?, ?)
    `);
    stmt.run(
      payload.event ?? "connect",
      payload.clientIp ?? null,
      payload.userAgent ?? null,
      payload.deviceInfo != null ? JSON.stringify(payload.deviceInfo) : null,
      payload.locationInfo != null ? JSON.stringify(payload.locationInfo) : null,
    );
  } catch (e) {
    console.warn("Relay client_connection record failed:", e.message);
  }
}

export function recordExperimentVoltageSample(sample) {
  if (!config.telemetry.enabled) return null;
  const db = getDb();
  const labelCharging = sample.labelCharging ? 1 : 0;
  const voltage = Number(sample.voltage);
  if (!Number.isFinite(voltage) || voltage <= 0) return null;
  const telemetryVoltage = Number(sample.telemetryVoltage);
  if (!Number.isFinite(telemetryVoltage) || telemetryVoltage <= 0) return null;
  try {
    const info = db
      .prepare(
        `INSERT INTO experiment_voltage_samples
         (session_id, label_charging, voltage, telemetry_voltage, voltage_1dp, adc_mv_avg, adc_raw_min, adc_raw_max, source)
         VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)`,
      )
      .run(
        sample.sessionId || null,
        labelCharging,
        voltage,
        telemetryVoltage,
        Number.isFinite(Number(sample.voltage1dp)) ? Number(sample.voltage1dp) : null,
        Number.isFinite(Number(sample.adcMvAvg)) ? Number(sample.adcMvAvg) : null,
        Number.isFinite(Number(sample.adcRawMin)) ? Number(sample.adcRawMin) : null,
        Number.isFinite(Number(sample.adcRawMax)) ? Number(sample.adcRawMax) : null,
        sample.source || null,
      );
    return info.lastInsertRowid;
  } catch (e) {
    console.warn("Relay experiment voltage record failed:", e.message);
    return null;
  }
}

export function getExperimentVoltageSamples({ limit = 3000 } = {}) {
  if (!config.telemetry.enabled) return [];
  const db = getDb();
  try {
    return db
      .prepare(
        `SELECT id, created_at, session_id, label_charging, voltage, telemetry_voltage, voltage_1dp, adc_mv_avg, adc_raw_min, adc_raw_max, source
         FROM experiment_voltage_samples
         ORDER BY id DESC
         LIMIT ?`,
      )
      .all(limit);
  } catch (e) {
    console.warn("Relay experiment voltage query failed:", e.message);
    return [];
  }
}

export function getExperimentVoltageSummary() {
  if (!config.telemetry.enabled) {
    return { totalSamples: 0, charging: 0, notCharging: 0, byTelemetryVoltage: [] };
  }
  const db = getDb();
  try {
    const totals = db
      .prepare(
        `SELECT
           COUNT(*) AS total_samples,
           SUM(CASE WHEN label_charging = 1 THEN 1 ELSE 0 END) AS charging,
           SUM(CASE WHEN label_charging = 0 THEN 1 ELSE 0 END) AS not_charging
         FROM experiment_voltage_samples`,
      )
      .get();
    const bins = db
      .prepare(
        `SELECT
           ROUND(telemetry_voltage, 2) AS telemetry_voltage_2dp,
           SUM(CASE WHEN label_charging = 1 THEN 1 ELSE 0 END) AS charging,
           SUM(CASE WHEN label_charging = 0 THEN 1 ELSE 0 END) AS not_charging
         FROM experiment_voltage_samples
         WHERE telemetry_voltage IS NOT NULL
         GROUP BY ROUND(telemetry_voltage, 2)
         ORDER BY telemetry_voltage_2dp ASC`,
      )
      .all();
    return {
      totalSamples: Number(totals?.total_samples) || 0,
      charging: Number(totals?.charging) || 0,
      notCharging: Number(totals?.not_charging) || 0,
      byTelemetryVoltage: bins.map((b) => ({
        telemetryVoltage: Number(b.telemetry_voltage_2dp),
        charging: Number(b.charging) || 0,
        notCharging: Number(b.not_charging) || 0,
      })),
    };
  } catch (e) {
    console.warn("Relay experiment voltage summary failed:", e.message);
    return { totalSamples: 0, charging: 0, notCharging: 0, byTelemetryVoltage: [] };
  }
}

function evaluateThreshold(values, labels, threshold, chargingIfLeq) {
  let correct = 0;
  for (let i = 0; i < values.length; i += 1) {
    const pred = chargingIfLeq ? values[i] <= threshold : values[i] >= threshold;
    if ((pred ? 1 : 0) === labels[i]) correct += 1;
  }
  return correct / values.length;
}

function sigmoid(z) {
  if (z > 30) return 1;
  if (z < -30) return 0;
  return 1 / (1 + Math.exp(-z));
}

function mean(values) {
  if (!values.length) return 0;
  return values.reduce((s, v) => s + v, 0) / values.length;
}

function std(values, mu) {
  if (!values.length) return 1;
  const variance = values.reduce((s, v) => s + (v - mu) ** 2, 0) / values.length;
  return Math.sqrt(Math.max(variance, 1e-12));
}

function featureVector(espValues, i) {
  const e = espValues[i];
  const e1 = espValues[i - 1];
  const e3 = espValues.slice(i - 2, i + 1);
  const e8 = espValues.slice(i - 7, i + 1);
  const eMean3 = mean(e3);
  const eMean8 = mean(e8);
  const eMin8 = Math.min(...e8);
  const eMax8 = Math.max(...e8);
  const eSlope3 = (e - espValues[i - 2]) / 2;
  const eSlope8 = (e - espValues[i - 7]) / 7;
  const eStd8 = std(e8, eMean8);

  return [
    e,
    e - e1,
    eMean3,
    eMean8,
    eSlope3,
    eSlope8,
    eMax8 - eMin8,
    eStd8,
  ];
}

function evaluateLogistic(rows, w, b) {
  let tp = 0;
  let tn = 0;
  let fp = 0;
  let fn = 0;
  for (const r of rows) {
    let z = b;
    for (let j = 0; j < w.length; j += 1) z += w[j] * r.x[j];
    const pred = sigmoid(z) >= 0.5 ? 1 : 0;
    if (pred === 1 && r.y === 1) tp += 1;
    else if (pred === 0 && r.y === 0) tn += 1;
    else if (pred === 1 && r.y === 0) fp += 1;
    else fn += 1;
  }
  const total = tp + tn + fp + fn;
  const accuracy = total ? (tp + tn) / total : 0;
  const precision = tp + fp ? tp / (tp + fp) : 0;
  const recall = tp + fn ? tp / (tp + fn) : 0;
  const f1 = precision + recall ? (2 * precision * recall) / (precision + recall) : 0;
  return {
    accuracy,
    precision,
    recall,
    f1,
    confusion: { tp, tn, fp, fn },
  };
}

export function analyzeExperimentVoltageDataset() {
  const samples = getExperimentVoltageSamples({ limit: 10_000 }).slice().reverse();
  const labeled = samples.filter((s) => s.label_charging === 0 || s.label_charging === 1);
  if (labeled.length < 40) {
    return {
      ok: false,
      reason: "need_more_data",
      message: "Collect at least 40 labeled samples to run analysis.",
      totals: { all: labeled.length, charging: 0, notCharging: 0 },
    };
  }

  const espVoltage = [];
  const telemVoltage = [];
  const labels = [];
  let chargingCount = 0;
  let notChargingCount = 0;
  for (const s of labeled) {
    const ev = Number(s.voltage);
    const tv = Number(s.telemetry_voltage);
    if (!Number.isFinite(ev) || !Number.isFinite(tv)) continue;
    espVoltage.push(ev);
    telemVoltage.push(tv);
    labels.push(s.label_charging);
    if (s.label_charging === 1) chargingCount += 1;
    else notChargingCount += 1;
  }

  if (espVoltage.length < 40 || chargingCount < 10 || notChargingCount < 10) {
    return {
      ok: false,
      reason: "imbalanced_data",
      message: "Need at least 10 samples for both charging and not-charging labels.",
      totals: { all: espVoltage.length, charging: chargingCount, notCharging: notChargingCount },
    };
  }

  // Build temporal feature rows (8-sample window) for a richer classifier than a single threshold.
  const rows = [];
  for (let i = 7; i < espVoltage.length; i += 1) {
    rows.push({ x: featureVector(espVoltage, i), y: labels[i] });
  }
  if (rows.length < 30) {
    return {
      ok: false,
      reason: "need_more_windowed_data",
      message: "Need at least 30 windowed samples (8-point windows) for temporal model.",
      totals: { all: espVoltage.length, charging: chargingCount, notCharging: notChargingCount },
    };
  }

  function trainWithBundleSize(bundleSize) {
    const bundledRows = [];
    for (let i = 0; i < rows.length; i += bundleSize) {
      const chunk = rows.slice(i, i + bundleSize);
      if (chunk.length < bundleSize) continue;
      const x = Array.from({ length: rows[0].x.length }, (_, j) => mean(chunk.map((r) => r.x[j])));
      const positives = chunk.reduce((s, r) => s + (r.y === 1 ? 1 : 0), 0);
      bundledRows.push({ x, y: positives >= Math.ceil(bundleSize / 2) ? 1 : 0 });
    }
    if (bundledRows.length < 6) return null;

    const featureCount = bundledRows[0].x.length;
    const mu = Array.from({ length: featureCount }, () => 0);
    const sigma = Array.from({ length: featureCount }, () => 1);
    for (let j = 0; j < featureCount; j += 1) {
      const col = bundledRows.map((r) => r.x[j]);
      mu[j] = mean(col);
      sigma[j] = std(col, mu[j]);
      if (sigma[j] < 1e-6) sigma[j] = 1;
    }
    for (const r of bundledRows) {
      for (let j = 0; j < featureCount; j += 1) {
        r.x[j] = (r.x[j] - mu[j]) / sigma[j];
      }
    }

    const train = [];
    const valid = [];
    for (let i = 0; i < bundledRows.length; i += 1) {
      if (i % 5 === 0) valid.push(bundledRows[i]);
      else train.push(bundledRows[i]);
    }
    if (!valid.length || !train.length) return null;

    const w = Array.from({ length: featureCount }, () => 0);
    let b = 0;
    const lr = 0.08;
    const epochs = 900;
    const l2 = 0.002;
    for (let epoch = 0; epoch < epochs; epoch += 1) {
      const gradW = Array.from({ length: featureCount }, () => 0);
      let gradB = 0;
      for (const r of train) {
        let z = b;
        for (let j = 0; j < featureCount; j += 1) z += w[j] * r.x[j];
        const p = sigmoid(z);
        const diff = p - r.y;
        gradB += diff;
        for (let j = 0; j < featureCount; j += 1) gradW[j] += diff * r.x[j];
      }
      const invN = 1 / train.length;
      gradB *= invN;
      for (let j = 0; j < featureCount; j += 1) {
        gradW[j] = gradW[j] * invN + l2 * w[j];
        w[j] -= lr * gradW[j];
      }
      b -= lr * gradB;
    }

    const trainMetrics = evaluateLogistic(train, w, b);
    const validMetrics = evaluateLogistic(valid, w, b);
    return { bundleSize, bundledRows, mu, sigma, w, b, trainMetrics, validMetrics };
  }

  // Auto-search best interval bundle size.
  const candidateBundleSizes = [3, 4, 5, 6, 8, 10];
  const candidates = candidateBundleSizes.map((size) => trainWithBundleSize(size)).filter(Boolean);
  if (!candidates.length) {
    return {
      ok: false,
      reason: "need_more_bundled_data",
      message: "Need at least 6 interval bundles for chunk-based validation.",
      totals: { all: espVoltage.length, charging: chargingCount, notCharging: notChargingCount },
    };
  }
  const bestCandidate = candidates.sort((a, b) => {
    if (b.validMetrics.f1 !== a.validMetrics.f1) return b.validMetrics.f1 - a.validMetrics.f1;
    if (b.validMetrics.accuracy !== a.validMetrics.accuracy) {
      return b.validMetrics.accuracy - a.validMetrics.accuracy;
    }
    return b.bundleSize - a.bundleSize;
  })[0];
  const { bundleSize, bundledRows, mu, sigma, w, b, trainMetrics, validMetrics } = bestCandidate;

  const uniq = Array.from(new Set(espVoltage.map((v) => Number(v.toFixed(3))))).sort((a, b) => a - b);
  if (uniq.length < 2) {
    return {
      ok: false,
      reason: "no_variation",
      message: "Voltage values have no variation yet.",
      totals: { all: voltage.length, charging: chargingCount, notCharging: notChargingCount },
    };
  }
  const thresholds = [];
  for (let i = 1; i < uniq.length; i += 1) thresholds.push((uniq[i - 1] + uniq[i]) / 2);

  let best = { threshold: thresholds[0], chargingIfLeq: true, accuracy: 0 };
  for (const t of thresholds) {
    const a1 = evaluateThreshold(espVoltage, labels, t, true);
    if (a1 > best.accuracy) best = { threshold: t, chargingIfLeq: true, accuracy: a1 };
    const a2 = evaluateThreshold(espVoltage, labels, t, false);
    if (a2 > best.accuracy) best = { threshold: t, chargingIfLeq: false, accuracy: a2 };
  }

  let sumEspCharging = 0;
  let sumEspNotCharging = 0;
  let sumTelCharging = 0;
  let sumTelNotCharging = 0;
  let cntCharging = 0;
  let cntNotCharging = 0;
  for (let i = 0; i < labels.length; i += 1) {
    if (labels[i] === 1) {
      sumEspCharging += espVoltage[i];
      sumTelCharging += telemVoltage[i];
      cntCharging += 1;
    } else {
      sumEspNotCharging += espVoltage[i];
      sumTelNotCharging += telemVoltage[i];
      cntNotCharging += 1;
    }
  }
  const meanCharging = cntCharging ? sumEspCharging / cntCharging : 0;
  const meanNotCharging = cntNotCharging ? sumEspNotCharging / cntNotCharging : 0;
  const meanTelemetryCharging = cntCharging ? sumTelCharging / cntCharging : 0;
  const meanTelemetryNotCharging = cntNotCharging ? sumTelNotCharging / cntNotCharging : 0;

  return {
    ok: true,
    totals: { all: espVoltage.length, charging: chargingCount, notCharging: notChargingCount },
    stats: {
      meanChargingEspVoltage: Number(meanCharging.toFixed(3)),
      meanNotChargingEspVoltage: Number(meanNotCharging.toFixed(3)),
      meanChargingTelemetryVoltage: Number(meanTelemetryCharging.toFixed(3)),
      meanNotChargingTelemetryVoltage: Number(meanTelemetryNotCharging.toFixed(3)),
    },
    model: {
      type: "logistic_temporal_v1",
      decisionThreshold: 0.5,
      features: [
        "esp_v_now",
        "esp_delta_1",
        "esp_mean_3",
        "esp_mean_8",
        "esp_slope_3",
        "esp_slope_8",
        "esp_range_8",
        "esp_std_8",
      ],
      intercept: Number(b.toFixed(5)),
      coefficients: {
        esp_v_now: Number(w[0].toFixed(5)),
        esp_delta_1: Number(w[1].toFixed(5)),
        esp_mean_3: Number(w[2].toFixed(5)),
        esp_mean_8: Number(w[3].toFixed(5)),
        esp_slope_3: Number(w[4].toFixed(5)),
        esp_slope_8: Number(w[5].toFixed(5)),
        esp_range_8: Number(w[6].toFixed(5)),
        esp_std_8: Number(w[7].toFixed(5)),
      },
      standardization: {
        mean: mu.map((v) => Number(v.toFixed(5))),
        std: sigma.map((v) => Number(v.toFixed(5))),
      },
      bundling: {
        autoSelected: true,
        selectedBundleSize: bundleSize,
        bundleCount: bundledRows.length,
        candidates: candidates.map((c) => ({
          bundleSize: c.bundleSize,
          bundleCount: c.bundledRows.length,
          validationAccuracy: Number((c.validMetrics.accuracy * 100).toFixed(1)),
          validationF1: Number((c.validMetrics.f1 * 100).toFixed(1)),
        })),
      },
      validation: {
        accuracy: Number((validMetrics.accuracy * 100).toFixed(1)),
        precision: Number((validMetrics.precision * 100).toFixed(1)),
        recall: Number((validMetrics.recall * 100).toFixed(1)),
        f1: Number((validMetrics.f1 * 100).toFixed(1)),
        confusion: validMetrics.confusion,
      },
      training: {
        accuracy: Number((trainMetrics.accuracy * 100).toFixed(1)),
      },
      fallbackThresholdRule: {
        threshold: Number(best.threshold.toFixed(3)),
        chargingIf: best.chargingIfLeq ? "voltage <= threshold" : "voltage >= threshold",
        estimatedAccuracy: Number((best.accuracy * 100).toFixed(1)),
      },
    },
    suggestion: {
      minConsecutiveSamples: 3,
      sampleIntervalSec: 1,
      note: "Use model probability with debounce/hysteresis to avoid rapid flapping.",
    },
  };
}

export function clearExperimentVoltageSamples() {
  if (!config.telemetry.enabled) return { deleted: 0 };
  const db = getDb();
  try {
    const result = db.prepare("DELETE FROM experiment_voltage_samples").run();
    return { deleted: result.changes || 0 };
  } catch (e) {
    console.warn("Relay experiment voltage clear failed:", e.message);
    return { deleted: 0, error: e.message };
  }
}

export function recordRuntimeVoltageSample({ espVoltage, telemetryVoltage }) {
  if (!config.telemetry.enabled) return null;
  const ev = Number(espVoltage);
  const tv = Number(telemetryVoltage);
  if (!Number.isFinite(ev) || !Number.isFinite(tv) || ev <= 0 || tv <= 0) return null;
  const db = getDb();
  try {
    const info = db
      .prepare(
        `INSERT INTO runtime_voltage_samples (esp_voltage, telemetry_voltage)
         VALUES (?, ?)`,
      )
      .run(ev, tv);
    return info.lastInsertRowid;
  } catch (e) {
    console.warn("Relay runtime voltage record failed:", e.message);
    return null;
  }
}

export function getRuntimeVoltageSamples({ limit = 64 } = {}) {
  if (!config.telemetry.enabled) return [];
  const db = getDb();
  try {
    const rows = db
      .prepare(
        `SELECT id, created_at, esp_voltage, telemetry_voltage
         FROM runtime_voltage_samples
         ORDER BY id DESC
         LIMIT ?`,
      )
      .all(limit);
    return rows.reverse();
  } catch (e) {
    console.warn("Relay runtime voltage query failed:", e.message);
    return [];
  }
}
