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

  it("snaps slight reverse past 3 o'clock to pure right turn", () => {
    // 3.2 o'clock ≈ 6° past east toward south (reverse).
    const rad = (6 * Math.PI) / 180;
    const v = applyDriveCurve({ x: Math.cos(rad), y: Math.sin(rad) });
    expect(v.y).toBe(0);
    expect(v.x).toBeCloseTo(1, 5);
  });

  it("snaps slight reverse past 9 o'clock to pure left turn", () => {
    // 8.8 o'clock ≈ 6° before west from the south side.
    const rad = Math.PI - (6 * Math.PI) / 180;
    const v = applyDriveCurve({ x: Math.cos(rad), y: Math.sin(rad) });
    expect(v.y).toBe(0);
    expect(v.x).toBeCloseTo(-1, 5);
  });

  it("does not snap diagonals or forward/back arcs", () => {
    const diag = applyDriveCurve({ x: 0.5, y: 0.5 });
    expect(diag.y).not.toBe(0);

    const fwd = applyDriveCurve({ x: 0.05, y: -0.9 });
    expect(fwd.y).toBeLessThan(0);
    expect(Math.abs(fwd.x)).toBeGreaterThan(0);
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
