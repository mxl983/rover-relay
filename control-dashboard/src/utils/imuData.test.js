import { describe, expect, it } from "vitest";
import {
  forwardAccelG,
  horizontalAccelG,
  integrateYawRad,
  isLikelyMoving,
  normalizeImuSample,
  readYawRateRadS,
} from "./imuData.js";

const SAMPLE = {
  stamp: 100,
  seq: 1,
  connected: true,
  accel: { x: 0.66, y: -0.004, z: -0.003, unit: "g" },
  gyro: { x: -0.01, y: 0.003, z: 1.69, unit: "rad_s" },
  aux: { x: -7, y: -20, z: 2056 },
};

describe("imuData", () => {
  it("normalizes WebSocket-style payload", () => {
    const sample = normalizeImuSample(SAMPLE);
    expect(sample?.seq).toBe(1);
    expect(sample?.accel.unit).toBe("g");
    expect(sample?.gyro.z).toBeCloseTo(1.69);
    expect(sample?.aux?.z).toBe(2056);
    expect(sample?.receivedAt).toBeGreaterThan(0);
  });

  it("reads yaw rate and integrates between stamps", () => {
    const a = normalizeImuSample({ ...SAMPLE, stamp: 0, gyro: { ...SAMPLE.gyro, z: 1 } });
    const b = normalizeImuSample({ ...SAMPLE, stamp: 0.5, gyro: { ...SAMPLE.gyro, z: 3 } });
    expect(readYawRateRadS(a)).toBe(1);
    expect(integrateYawRad(a, b)).toBeCloseTo(1, 5);
  });

  it("detects horizontal motion from accel", () => {
    const sample = normalizeImuSample(SAMPLE);
    expect(horizontalAccelG(sample)).toBeCloseTo(0.66, 3);
    expect(forwardAccelG(sample)).toBeCloseTo(-0.004, 4);
    expect(isLikelyMoving(sample)).toBe(false);

    const moving = normalizeImuSample({
      ...SAMPLE,
      accel: { ...SAMPLE.accel, y: 0.21, z: 0.9 },
    });
    expect(isLikelyMoving(moving)).toBe(true);
  });
});
