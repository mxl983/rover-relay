import { describe, expect, it } from "vitest";
import {
  applyDriveCurve,
  prepareDriveVector,
  quantizeAnalog,
  snapAnalogPair,
  touchStickToDriveRaw,
} from "./JoystickControlCluster.jsx";

describe("touch stick mapping", () => {
  it("maps nipple 12 o'clock to forward (negative y)", () => {
    const v = applyDriveCurve(
      touchStickToDriveRaw({ vector: { x: 0, y: 1 }, force: 1 }),
    );
    expect(v).toEqual({ x: 0, y: -1 });
  });

  it("maps nipple 6 o'clock to back (positive y)", () => {
    const v = applyDriveCurve(
      touchStickToDriveRaw({ vector: { x: 0, y: -1 }, force: 1 }),
    );
    expect(v).toEqual({ x: 0, y: 1 });
  });
});

describe("joystick drive curve", () => {
  it("maps cardinals at full deflection", () => {
    expect(applyDriveCurve({ x: 0, y: -1 })).toEqual({ x: 0, y: -1 });
    expect(applyDriveCurve({ x: 0, y: 1 })).toEqual({ x: 0, y: 1 });
    expect(applyDriveCurve({ x: 1, y: 0 })).toEqual({ x: 1, y: 0 });
    expect(applyDriveCurve({ x: -1, y: 0 })).toEqual({ x: -1, y: 0 });
  });

  it("blends x and y proportionally between cardinals", () => {
    const towardNoon = applyDriveCurve({ x: 0.5, y: -0.5 });
    expect(towardNoon.x).toBeCloseTo(0.5, 5);
    expect(towardNoon.y).toBeCloseTo(-0.5, 5);

    const towardSix = applyDriveCurve({ x: -0.5, y: 0.5 });
    expect(towardSix.x).toBeCloseTo(-0.5, 5);
    expect(towardSix.y).toBeCloseTo(0.5, 5);
  });

  it("scales speed with partial pull force", () => {
    expect(applyDriveCurve({ x: 0, y: -0.3 })).toEqual({ x: 0, y: -0.3 });
    expect(applyDriveCurve({ x: 0.4, y: 0 })).toEqual({ x: 0.4, y: 0 });
  });

  it("zeros vectors inside the radial dead zone", () => {
    expect(applyDriveCurve({ x: 0.02, y: 0.02 })).toEqual({ x: 0, y: 0 });
  });

  it("clamps vectors outside the unit circle", () => {
    const v = applyDriveCurve({ x: 0.8, y: -0.8 });
    expect(Math.hypot(v.x, v.y)).toBeCloseTo(1, 5);
  });

  it("does not quantize prepared drive vectors", () => {
    expect(prepareDriveVector({ x: 0.123, y: -0.456 })).toEqual({
      x: 0.123,
      y: -0.456,
    });
  });
});

describe("gimbal analog quantization", () => {
  it("snaps small noise to zero", () => {
    expect(quantizeAnalog(0.02)).toBe(0);
    expect(quantizeAnalog(-0.02)).toBe(0);
  });

  it("snaps held gimbal values to steps", () => {
    expect(quantizeAnalog(0.52)).toBe(0.5);
    expect(quantizeAnalog(0.53)).toBe(0.55);
    expect(snapAnalogPair({ x: 0.52, y: -0.38 }, 0.05)).toEqual({ x: 0.5, y: -0.4 });
  });
});
