import { spawn } from "child_process";
import config from "../config.js";
import { enforceConservativeChargingDecision } from "./chargingDetectionService.js";

/** Append operator hints for common V4L2 failures (missing device, Docker, busy). */
function formatWebcamCaptureErrorHint(raw, device) {
  const d = String(device || "/dev/video0");
  const t = String(raw || "").trim();
  if (/No such file|Cannot open video device/i.test(t)) {
    return `${t} — Set CHARGING_LED_WEBCAM_DEVICE (currently ${d}); in Docker pass the device into the relay container (compose relay.devices).`;
  }
  if (/Device or resource busy|EBUSY/i.test(t)) {
    return `${t} — Only one capture at a time on many USB cameras; concurrent API calls now share one grab. If this persists, another app may hold ${d}.`;
  }
  return t;
}

function rgbToHsv(r, g, b) {
  const rn = r / 255;
  const gn = g / 255;
  const bn = b / 255;
  const max = Math.max(rn, gn, bn);
  const min = Math.min(rn, gn, bn);
  const d = max - min;
  let h = 0;
  const s = max === 0 ? 0 : d / max;
  const v = max;
  if (d !== 0) {
    if (max === rn) h = ((gn - bn) / d + (gn < bn ? 6 : 0)) / 6;
    else if (max === gn) h = ((bn - rn) / d + 2) / 6;
    else h = ((rn - gn) / d + 4) / 6;
  }
  return { h: h * 360, s, v };
}

/**
 * Dominant hue among saturated, bright, non-black pixels in the center ROI (LED-focused).
 * @param {Buffer} rgb
 * @param {number} width
 * @param {number} height
 * @param {{ minRgbMax?: number }} [opts] — minRgbMax: ignore pixels with max(R,G,B) &lt; this (default 12).
 */
export function medianHueFromRgbFrame(rgb, width, height, opts = {}) {
  const w = Math.floor(width);
  const h = Math.floor(height);
  const expected = w * h * 3;
  if (rgb.length < expected) return { medianHue: null, samplePixels: 0, meanV: null };

  const minRgbMax = opts.minRgbMax ?? 12;

  const x0 = Math.floor(w * 0.3);
  const x1 = Math.floor(w * 0.7);
  const y0 = Math.floor(h * 0.3);
  const y1 = Math.floor(h * 0.7);

  const hues = [];
  let vSumLed = 0;

  function tryPixel(r, gch, b, minV, minS) {
    if (Math.max(r, gch, b) < minRgbMax) return;
    const { h, s, v } = rgbToHsv(r, gch, b);
    if (v < minV || s < minS) return;
    hues.push(h);
    vSumLed += v;
  }

  for (let y = y0; y < y1; y += 2) {
    const row = y * w * 3;
    for (let x = x0; x < x1; x += 2) {
      const i = row + x * 3;
      tryPixel(rgb[i], rgb[i + 1], rgb[i + 2], 0.12, 0.12);
    }
  }

  if (hues.length < 8) {
    for (let y = y0; y < y1; y += 3) {
      const row = y * w * 3;
      for (let x = x0; x < x1; x += 3) {
        const i = row + x * 3;
        tryPixel(rgb[i], rgb[i + 1], rgb[i + 2], 0.06, 0.06);
      }
    }
  }

  if (!hues.length) return { medianHue: null, samplePixels: 0, meanV: null };

  hues.sort((a, b) => a - b);
  const medianHue = hues.length % 2 ? hues[(hues.length - 1) / 2] : (hues[hues.length / 2 - 1] + hues[hues.length / 2]) / 2;
  return {
    medianHue,
    samplePixels: hues.length,
    /** Mean HSV V among pixels that contributed to the hue median (excludes black background). */
    meanV: vSumLed / hues.length,
  };
}

/** True if hue lies in [min,max], or in the wrap segment when min > max (red across 0°/360°). */
export function hueInBand(hue, min, max) {
  if (min <= max) return hue >= min && hue <= max;
  return hue >= min || hue <= max;
}

/**
 * Red (charging) vs green (idle / not charging). Charger-specific hue bands from config.
 * Legacy env names CHARGING_LED_YELLOW_* still map to the charging band in config.
 */
export function classifyHueForLed(hue, cfg) {
  if (hue == null || !Number.isFinite(hue)) {
    return { label: "unknown", confidence: "none" };
  }
  const chargeMin = cfg.chargingHueMin ?? cfg.yellowHueMin;
  const chargeMax = cfg.chargingHueMax ?? cfg.yellowHueMax;
  const { greenHueMin, greenHueMax } = cfg;
  const inChargingBand = hueInBand(hue, chargeMin, chargeMax);
  const inIdleBand = hue >= greenHueMin && hue <= greenHueMax;
  if (inChargingBand && !inIdleBand) return { label: "charging", confidence: "high" };
  if (inIdleBand && !inChargingBand) return { label: "idle", confidence: "high" };
  if (inChargingBand && inIdleBand) {
    const chMid =
      chargeMin <= chargeMax
        ? (chargeMin + chargeMax) / 2
        : ((chargeMin + chargeMax + 360) / 2) % 360;
    const gMid = (greenHueMin + greenHueMax) / 2;
    const dCh = angularHueDistance(hue, chMid);
    const dG = Math.abs(hue - gMid);
    if (dCh + 4 < dG) return { label: "charging", confidence: "medium" };
    if (dG + 4 < dCh) return { label: "idle", confidence: "medium" };
    return { label: "unknown", confidence: "low" };
  }
  /**
   * Dead zone between charging band and green idle (e.g. ~56°–72°): bloomed red LEDs often meter
   * as orange. If we are clearly not in the green idle band but hue sits below it, treat as
   * charging (amber/red bloom), not “unknown”.
   */
  if (!inIdleBand && hue < greenHueMin) {
    return { label: "charging", confidence: "medium" };
  }
  return { label: "unknown", confidence: "low" };
}

function angularHueDistance(hue, centerDeg) {
  const d = Math.abs(hue - centerDeg);
  return d > 180 ? 360 - d : d;
}

/** @type {{ expires: number, result: object } | null} */
let webcamInferenceCache = null;

/**
 * One in-flight webcam inference at a time. Many V4L2 devices return EBUSY if ffmpeg opens
 * the device while another capture is still running (e.g. /charging every 1s + /state every 2s).
 * @type {Promise<object> | null}
 */
let webcamInferenceInflight = null;

function captureRgbFrameWithFfmpeg() {
  const cam = config.rover.ledWebcam;
  const { device, captureTimeoutMs, frameWidth, frameHeight, inputFormat, captureVideoSize } = cam;
  const sizeAtDevice = captureVideoSize || `${frameWidth}x${frameHeight}`;

  const args = [
    "-hide_banner",
    "-loglevel",
    "error",
    "-fflags",
    "nobuffer",
    "-flags",
    "low_delay",
    "-analyzeduration",
    "0",
    "-probesize",
    "32768",
    "-f",
    "v4l2",
  ];
  if (inputFormat && inputFormat.length) {
    args.push("-input_format", inputFormat);
  }
  args.push("-video_size", sizeAtDevice, "-i", device, "-frames:v", "1");
  if (sizeAtDevice !== `${frameWidth}x${frameHeight}`) {
    args.push("-vf", `scale=${frameWidth}:${frameHeight}`);
  }
  args.push("-f", "rawvideo", "-pix_fmt", "rgb24", "pipe:1");

  return new Promise((resolve, reject) => {
    const proc = spawn("ffmpeg", args, { stdio: ["ignore", "pipe", "pipe"] });
    const chunks = [];
    const errChunks = [];

    const timer = setTimeout(() => {
      proc.kill("SIGKILL");
      reject(new Error("webcam_capture_timeout"));
    }, captureTimeoutMs);

    proc.stdout.on("data", (d) => chunks.push(d));
    proc.stderr.on("data", (d) => errChunks.push(d));

    proc.on("error", (err) => {
      clearTimeout(timer);
      reject(err?.code === "ENOENT" ? new Error("ffmpeg_not_found") : err);
    });

    proc.on("close", (code) => {
      clearTimeout(timer);
      if (code !== 0 && code !== null) {
        const msg = Buffer.concat(errChunks).toString("utf8").trim() || `ffmpeg_exit_${code}`;
        reject(new Error(msg));
        return;
      }
      resolve(Buffer.concat(chunks));
    });
  });
}

/**
 * Physical charger LED via USB webcam (red = charging, green = idle).
 * Independent of rover heartbeat — the adapter LED is valid even if the rover is offline.
 */
export async function inferChargingFromLedWebcam() {
  const cam = config.rover.ledWebcam;
  const ttlMs = Number(cam.cacheTtlMs);
  const stub = String(cam.stubMode || "")
    .trim()
    .toLowerCase();

  if (stub !== "charging" && stub !== "idle" && stub !== "error" && ttlMs > 0 && webcamInferenceCache) {
    if (Date.now() < webcamInferenceCache.expires) {
      return { ...webcamInferenceCache.result, webcamCached: true };
    }
  }

  if (stub === "charging") {
    return enforceConservativeChargingDecision({
      isCharging: true,
      confidence: "high",
      reason: "webcam_stub_charging",
      detectionSource: "webcam",
      sampleCount: 0,
      goodRateCount: 0,
    });
  }
  if (stub === "idle") {
    return enforceConservativeChargingDecision({
      isCharging: false,
      confidence: "high",
      reason: "webcam_stub_idle",
      detectionSource: "webcam",
      sampleCount: 0,
      goodRateCount: 0,
    });
  }
  if (stub === "error") {
    return enforceConservativeChargingDecision({
      isCharging: null,
      confidence: "none",
      reason: "webcam_stub_error",
      detectionSource: "webcam",
      sampleCount: 0,
      goodRateCount: 0,
    });
  }

  if (webcamInferenceInflight) {
    return webcamInferenceInflight;
  }

  const p = runLedWebcamInferenceLocked(cam, ttlMs);
  webcamInferenceInflight = p;
  void p.finally(() => {
    if (webcamInferenceInflight === p) webcamInferenceInflight = null;
  });
  return p;
}

async function runLedWebcamInferenceLocked(cam, ttlMs) {
  const t0 = Date.now();
  let rgb;
  try {
    rgb = await captureRgbFrameWithFfmpeg();
  } catch (e) {
    const raw = e instanceof Error ? e.message : String(e);
    const msg = formatWebcamCaptureErrorHint(raw, cam.device);
    return enforceConservativeChargingDecision({
      isCharging: null,
      confidence: "none",
      reason: "webcam_capture_failed",
      detectionSource: "webcam",
      webcamError: msg,
      sampleCount: 0,
      goodRateCount: 0,
    });
  }

  const { medianHue, samplePixels, meanV } = medianHueFromRgbFrame(
    rgb,
    cam.frameWidth,
    cam.frameHeight,
    { minRgbMax: cam.ignoreBelowRgbMax },
  );
  const classified = classifyHueForLed(medianHue, cam);
  const captureMs = Date.now() - t0;

  let isCharging = false;
  let confidence = classified.confidence;
  let reason = "webcam_led_hue";

  if (classified.label === "charging") {
    isCharging = true;
  } else if (classified.label === "idle") {
    isCharging = false;
  } else {
    isCharging = null;
    confidence = "none";
    reason = "webcam_led_ambiguous_hue";
  }

  const out = enforceConservativeChargingDecision({
    isCharging,
    confidence,
    reason,
    detectionSource: "webcam",
    medianHue: medianHue != null ? Math.round(medianHue * 10) / 10 : null,
    ledSamplePixels: samplePixels,
    ledMeanV: meanV != null ? Math.round(meanV * 1000) / 1000 : null,
    webcamCaptureMs: captureMs,
    sampleCount: samplePixels,
    goodRateCount: samplePixels,
  });

  if (ttlMs > 0) {
    webcamInferenceCache = { expires: Date.now() + ttlMs, result: out };
  }

  return out;
}
