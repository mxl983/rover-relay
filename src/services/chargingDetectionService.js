import config from "../config.js";
import { getDb } from "./db.js";
import { analyzeExperimentVoltageDataset, getRuntimeVoltageSamples } from "./telemetryService.js";

function parseTs(row) {
  if (!row?.created_at) return null;
  const t = Date.parse(row.created_at.replace(" ", "T") + "Z") || Date.parse(row.created_at);
  return Number.isFinite(t) ? t : null;
}

function median(values) {
  if (!values.length) return null;
  const s = values.slice().sort((a, b) => a - b);
  const m = Math.floor(s.length / 2);
  return s.length % 2 ? s[m] : (s[m - 1] + s[m]) / 2;
}

/**
 * Recent voltage samples from telemetry (oldest first).
 * @param {number} windowMs
 */
function fetchVoltageSeries(windowMs) {
  const cutoff = new Date(Date.now() - windowMs)
    .toISOString()
    .replace("T", " ")
    .replace(/\.\d{3}Z$/, "")
    .replace("Z", "");
  const db = getDb();
  const maxRows = 1200;
  return db
    .prepare(
      `SELECT created_at, voltage, usb_power
       FROM telemetry
       WHERE voltage IS NOT NULL
         AND created_at >= ?
       ORDER BY created_at ASC, id ASC
       LIMIT ?`,
    )
    .all(cutoff, maxRows);
}

/**
 * Adjacent-sample rates (V change per minute). Pairs with |rate| above spikeMax are dropped
 * as slope outliers. Pairs with gaps larger than maxGapMs are skipped.
 * @returns {{ rate: number, tEnd: number }[]}
 */
function trustworthyRates(points, maxGapMs, spikeAbsVoltPerMin) {
  const out = [];
  for (let i = 1; i < points.length; i += 1) {
    const prev = points[i - 1];
    const cur = points[i];
    const gap = cur.t - prev.t;
    if (gap <= 0 || gap > maxGapMs) continue;
    const dtMin = gap / 60_000;
    const rate = (cur.voltage - prev.voltage) / dtMin;
    if (!Number.isFinite(rate)) continue;
    if (Math.abs(rate) > spikeAbsVoltPerMin) continue;
    out.push({ rate, tEnd: cur.t });
  }
  return out;
}

/**
 * Scan very recent adjacent deltas for immediate plug/unplug transient (~+/-0.2V).
 * Startup/invalid low-voltage points should be filtered before calling this.
 * @returns {number|null} latest qualifying delta in volts
 */
function recentTransitionDelta(points, maxGapMs, minAbsDeltaV) {
  for (let i = points.length - 1; i > 0; i -= 1) {
    const prev = points[i - 1];
    const cur = points[i];
    const gap = cur.t - prev.t;
    if (gap <= 0 || gap > maxGapMs) continue;
    const dv = cur.voltage - prev.voltage;
    if (Math.abs(dv) >= minAbsDeltaV) return dv;
  }
  return null;
}

function recentUsbOnRatio(rowsTail) {
  const withUsb = rowsTail.filter((r) => r.usb_power === 0 || r.usb_power === 1);
  if (!withUsb.length) return null;
  const onCount = withUsb.filter((r) => r.usb_power === 1).length;
  return onCount / withUsb.length;
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

function inferChargingFromLearnedModel(candidateDirection = null) {
  const analysis = analyzeExperimentVoltageDataset();
  if (!analysis?.ok || analysis?.model?.type !== "logistic_temporal_v1") return null;
  const runtime = getRuntimeVoltageSamples({ limit: 16 });
  if (runtime.length < 8) return null;
  const esp = runtime.map((r) => Number(r.esp_voltage)).filter((v) => Number.isFinite(v));
  if (esp.length !== runtime.length || esp.length < 8) return null;

  const i = esp.length - 1;
  const raw = featureVector(esp, i);
  const mu = analysis.model.standardization?.mean;
  const sigma = analysis.model.standardization?.std;
  if (!Array.isArray(mu) || !Array.isArray(sigma) || mu.length !== raw.length || sigma.length !== raw.length) {
    return null;
  }

  const featureNames = analysis.model.features || [];
  const standardized = raw.map((v, idx) => (v - mu[idx]) / (Math.abs(sigma[idx]) > 1e-8 ? sigma[idx] : 1));
  const coefs = featureNames.map((name) => Number(analysis.model.coefficients?.[name]));
  if (coefs.some((c) => !Number.isFinite(c))) return null;
  const intercept = Number(analysis.model.intercept);
  if (!Number.isFinite(intercept)) return null;

  let z = intercept;
  for (let k = 0; k < standardized.length; k += 1) z += standardized[k] * coefs[k];
  const probabilityCharging = sigmoid(z);
  const isCharging = probabilityCharging >= 0.5;
  const distance = Math.abs(probabilityCharging - 0.5) * 2;
  const confidence = distance >= 0.8 ? "high" : distance >= 0.45 ? "medium" : "low";
  return {
    isCharging,
    confidence,
    reason:
      candidateDirection === "up"
        ? "learned_model_after_telemetry_voltage_rise_candidate"
        : candidateDirection === "down"
          ? "learned_model_after_telemetry_voltage_drop_candidate"
          : "learned_temporal_voltage_model",
    modelType: "logistic_temporal_v1",
    modelProbabilityCharging: Math.round(probabilityCharging * 1000) / 1000,
    runtimeWindowSize: runtime.length,
    candidateDirection,
  };
}

function enforceConservativeChargingDecision(result) {
  const confidence = result?.confidence || "low";
  const isStrongEnough = confidence === "medium" || confidence === "high";
  const shouldBeYes = result?.isCharging === true && isStrongEnough;
  if (shouldBeYes) return result;
  return {
    ...result,
    isCharging: false,
    reason:
      result?.reason === "learned_temporal_voltage_model" ||
      String(result?.reason || "").startsWith("learned_model_after_")
        ? "conservative_gate_not_confident_yes"
        : result?.reason || "conservative_gate_not_confident_yes",
  };
}

/**
 * Infer whether the rover battery is charging from telemetry.
 * @param {{ online: boolean, booting: boolean }} rover
 */
export function inferChargingFromTelemetry(rover) {
  const { online, booting } = rover;
  const c = config.rover;

  if (!config.telemetry.enabled) {
    return enforceConservativeChargingDecision({
      isCharging: null,
      confidence: "none",
      reason: "telemetry_disabled",
      chargeVoltPerHour: null,
      usbPowerLikelyOn: null,
      sampleCount: 0,
      goodRateCount: 0,
    });
  }

  if (!online) {
    return enforceConservativeChargingDecision({
      isCharging: false,
      confidence: "high",
      reason: "rover_offline",
      chargeVoltPerHour: null,
      usbPowerLikelyOn: null,
      sampleCount: 0,
      goodRateCount: 0,
    });
  }

  const windowMs = c.chargingWindowMs;
  const maxGapMs = Math.max(c.heartbeatStaleMs, c.chargingMaxGapMs);
  const transitionMaxGapMs = c.chargingTransitionMaxGapMs;
  const transitionDeltaV = c.chargingTransitionDeltaV;
  const minTrustedVoltage = c.chargingMinTrustedVoltage;
  const spike = c.chargingSpikeAbsVoltPerMin;
  const minPos = c.chargingMinPositiveVoltPerMin;
  const maxPos = c.chargingMaxPositiveVoltPerMin;
  const dischargeClear = c.chargingDischargeClearVoltPerMin;
  const softDischarge = c.chargingSoftDischargeVoltPerMin;
  const tailN = c.chargingRecentRatesTail;
  const minGood = c.chargingMinGoodRates;

  const rawRows = fetchVoltageSeries(windowMs);
  const points = rawRows
    .map((r) => ({
      t: parseTs(r),
      voltage: Number(r.voltage),
      usb_power: r.usb_power,
    }))
    .filter((p) => p.t != null && Number.isFinite(p.voltage) && p.voltage >= minTrustedVoltage);

  const rates = trustworthyRates(points, maxGapMs, spike);
  const tailRates = rates.slice(-tailN).map((x) => x.rate);
  const medTail = tailRates.length ? median(tailRates) : null;
  const lastRate = rates.length ? rates[rates.length - 1].rate : null;
  const transitionDelta = recentTransitionDelta(points, transitionMaxGapMs, transitionDeltaV);
  const candidateDeltaV = 0.1;
  const candidateDirection =
    transitionDelta != null && transitionDelta >= candidateDeltaV
      ? "up"
      : transitionDelta != null && transitionDelta <= -candidateDeltaV
        ? "down"
        : null;

  const usbRatio = rawRows.length ? recentUsbOnRatio(rawRows.slice(-12)) : null;
  const usbLikelyOn = usbRatio != null && usbRatio >= 0.6;

  // Hybrid strategy:
  // 1) telemetry 0.1V bump/drop creates a candidate event
  // 2) learned temporal model performs final decision when candidate exists
  if (candidateDirection) {
    const learned = inferChargingFromLearnedModel(candidateDirection);
    if (learned) {
      return enforceConservativeChargingDecision({
        ...learned,
        chargeVoltPerHour: null,
        usbPowerLikelyOn: usbLikelyOn,
        sampleCount: learned.runtimeWindowSize,
        goodRateCount: learned.runtimeWindowSize,
      });
    }
  }

  const posInBand = (r) => r > minPos && r <= maxPos;
  const stronglyDischarging = (r) => r <= dischargeClear;
  const softlyDischarging = (r) => r <= softDischarge;

  const posVotes = tailRates.filter(posInBand).length;
  const negVotes = tailRates.filter(stronglyDischarging).length;
  const softNegVotes = tailRates.filter(softlyDischarging).length;

  let isCharging = false;
  let confidence = "low";
  let reason = "insufficient_signal";

  if (rates.length < minGood) {
    if (booting && usbLikelyOn) {
      return enforceConservativeChargingDecision({
        isCharging: null,
        confidence: "low",
        reason: "booting_wait_for_stable_voltage",
        chargeVoltPerHour: null,
        usbPowerLikelyOn: usbLikelyOn,
        sampleCount: points.length,
        goodRateCount: rates.length,
      });
    }
    if (usbLikelyOn && points.length >= 1) {
      return enforceConservativeChargingDecision({
        isCharging: null,
        confidence: "low",
        reason: "need_more_samples_usb_may_be_on",
        chargeVoltPerHour: null,
        usbPowerLikelyOn: true,
        sampleCount: points.length,
        goodRateCount: rates.length,
      });
    }
    return enforceConservativeChargingDecision({
      isCharging: null,
      confidence: "low",
      reason: "insufficient_samples",
      chargeVoltPerHour: null,
      usbPowerLikelyOn: usbLikelyOn || null,
      sampleCount: points.length,
      goodRateCount: rates.length,
    });
  }

  // Immediate unplug transient: sudden recent voltage drop (~0.2V) in a short window.
  const abruptDrop = transitionDelta != null && transitionDelta <= -transitionDeltaV;
  if (abruptDrop) {
    return enforceConservativeChargingDecision({
      isCharging: false,
      confidence: "high",
      reason: "voltage_drop_after_unplug",
      chargeVoltPerHour: null,
      usbPowerLikelyOn: usbLikelyOn,
      sampleCount: points.length,
      goodRateCount: rates.length,
      medianRecentVoltPerMinute:
        medTail != null && Number.isFinite(medTail) ? Math.round(medTail * 1000) / 1000 : null,
    });
  }

  // Unplug / drain: one strong downward slope, or two softer ticks in a row.
  const lastTwo = rates.slice(-2).map((x) => x.rate);
  const strongDrop = lastRate != null && lastRate <= dischargeClear;
  const sustainedSoftDrain =
    softNegVotes >= 2 || (lastTwo.length === 2 && lastTwo.every((r) => r <= softDischarge));

  if (
    strongDrop ||
    sustainedSoftDrain ||
    (negVotes >= 2 && medTail != null && medTail < minPos * 0.5)
  ) {
    isCharging = false;
    confidence = strongDrop ? "high" : medTail != null && medTail <= dischargeClear ? "high" : "medium";
    reason = strongDrop
      ? "voltage_slope_dropped_sharply"
      : "voltage_falling_or_flat_after_charge";
  } else if (
    transitionDelta != null &&
    transitionDelta >= transitionDeltaV &&
    (usbLikelyOn || (medTail != null && medTail >= 0))
  ) {
    // Immediate plug-in bump (~+0.2V) then settle into slow rise.
    isCharging = true;
    confidence = medTail != null && medTail > minPos ? "high" : "medium";
    reason = "voltage_bump_after_plug_in";
  } else if (
    medTail != null &&
    medTail > minPos &&
    medTail <= maxPos &&
    posVotes >= minGood
  ) {
    isCharging = true;
    confidence = posVotes >= minGood + 1 && tailRates.length >= minGood + 1 ? "high" : "medium";
    reason = "steady_slow_voltage_rise";
  } else if (posVotes >= minGood && lastRate != null && lastRate > minPos && lastRate <= maxPos) {
    isCharging = true;
    confidence = "medium";
    reason = "recent_positive_voltage_slopes";
  } else if (
    !booting &&
    usbLikelyOn &&
    lastRate != null &&
    lastRate > minPos &&
    lastRate <= maxPos
  ) {
    // One trustworthy upward tick while wall power is reported — faster “just plugged in” signal
    isCharging = true;
    confidence = "medium";
    reason = "usb_power_corroborated_rising_voltage";
  } else if (!booting && usbLikelyOn && medTail != null && medTail > -minPos && medTail < minPos * 3) {
    // Wall power present but pack not climbing yet (or very flat) — not enough to assert charging
    isCharging = false;
    confidence = "low";
    reason = "usb_power_no_voltage_rise_yet";
  } else {
    isCharging = false;
    confidence = tailRates.length >= 3 ? "medium" : "low";
    reason = "no_trustworthy_charge_pattern";
  }

  const chargeVoltPerHour =
    isCharging && medTail != null && medTail > 0 ? Math.round(medTail * 60 * 1000) / 1000 : null;

  return enforceConservativeChargingDecision({
    isCharging,
    confidence,
    reason,
    chargeVoltPerHour,
    usbPowerLikelyOn: usbLikelyOn,
    sampleCount: points.length,
    goodRateCount: rates.length,
    medianRecentVoltPerMinute:
      medTail != null && Number.isFinite(medTail) ? Math.round(medTail * 1000) / 1000 : null,
  });
}
