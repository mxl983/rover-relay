import { describe, it, expect } from "vitest";
import { medianHueFromRgbFrame, classifyHueForLed } from "../src/services/ledWebcamChargingService.js";

describe("LED webcam hue helpers", () => {
  it("classifies red (charging) and green (idle)", () => {
    const cfg = {
      chargingHueMin: 0,
      chargingHueMax: 72,
      greenHueMin: 73,
      greenHueMax: 165,
    };
    expect(classifyHueForLed(5, cfg).label).toBe("charging");
    expect(classifyHueForLed(48, cfg).label).toBe("charging");
    expect(classifyHueForLed(110, cfg).label).toBe("idle");
  });

  it("fills bloom dead zone: orange-red ~58° with a tight primary band", () => {
    const cfg = {
      chargingHueMin: 0,
      chargingHueMax: 55,
      greenHueMin: 73,
      greenHueMax: 165,
    };
    const r = classifyHueForLed(58, cfg);
    expect(r.label).toBe("charging");
    expect(r.confidence).toBe("medium");
  });

  it("wrap band: red hues near 360° count as charging", () => {
    const cfg = {
      chargingHueMin: 350,
      chargingHueMax: 12,
      greenHueMin: 73,
      greenHueMax: 165,
    };
    expect(classifyHueForLed(355, cfg).label).toBe("charging");
    expect(classifyHueForLed(8, cfg).label).toBe("charging");
  });

  it("legacy yellowHue* keys still work as charging band", () => {
    const cfg = {
      yellowHueMin: 0,
      yellowHueMax: 20,
      greenHueMin: 73,
      greenHueMax: 165,
    };
    expect(classifyHueForLed(10, cfg).label).toBe("charging");
  });

  it("skips near-black pixels so hue reflects the LED, not background", () => {
    const w = 80;
    const h = 80;
    const buf = Buffer.alloc(w * h * 3, 0);
    const x0 = Math.floor(w * 0.3);
    const x1 = Math.floor(w * 0.7);
    const y0 = Math.floor(h * 0.3);
    const y1 = Math.floor(h * 0.7);
    for (let y = y0; y < y1; y += 1) {
      for (let x = x0; x < x1; x += 1) {
        const i = (y * w + x) * 3;
        buf[i] = 255;
        buf[i + 1] = 45;
        buf[i + 2] = 20;
      }
    }
    const { medianHue, samplePixels } = medianHueFromRgbFrame(buf, w, h, { minRgbMax: 14 });
    expect(samplePixels).toBeGreaterThan(8);
    expect(medianHue).toBeLessThan(40);
  });

  it("reads red-ish from a flat RGB frame buffer", () => {
    const w = 40;
    const h = 40;
    const buf = Buffer.alloc(w * h * 3);
    for (let i = 0; i < w * h; i += 1) {
      buf[i * 3] = 255;
      buf[i * 3 + 1] = 20;
      buf[i * 3 + 2] = 10;
    }
    const { medianHue, samplePixels } = medianHueFromRgbFrame(buf, w, h);
    expect(samplePixels).toBeGreaterThan(8);
    expect(medianHue).toBeGreaterThanOrEqual(0);
    expect(medianHue).toBeLessThan(35);
  });
});
