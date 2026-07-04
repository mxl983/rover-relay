import { describe, expect, it } from "vitest";
import {
  applyDriveCurve,
  prepareDriveVector,
  quantizeAnalog,
  snapAnalogPair,
} from "./JoystickControlCluster.jsx";

describe("joystick drive curve", () => {
  it("keeps small lateral inputs proportional (no turn attenuation)", () => {
    const out = applyDriveCurve({ x: 0.12, y: -0.5 });
    expect(out.x).toBeCloseTo(0.12, 3);
    expect(Math.abs(out.y)).toBeGreaterThan(0.5);
  });

  it("boosts partial forward stick to a responsive starting throttle", () => {
    const half = applyDriveCurve({ x: 0, y: -0.3 });
    expect(half.y).toBeLessThan(-0.5);
    expect(half.y).toBeGreaterThanOrEqual(-1);

    const tiny = applyDriveCurve({ x: 0, y: -0.08 });
    expect(tiny.y).toBeLessThan(-0.4);
  });

  it("maps full forward to -1", () => {
    expect(applyDriveCurve({ x: 0, y: -1 }).y).toBeCloseTo(-1, 5);
  });

  it("floors turn-in-place but leaves fine turns alone while driving", () => {
    const spin = applyDriveCurve({ x: 0.2, y: 0 });
    expect(Math.abs(spin.x)).toBeGreaterThan(0.28);

    const cruiseTurn = applyDriveCurve({ x: 0.08, y: -0.4 });
    expect(cruiseTurn.x).toBeCloseTo(0.08, 3);
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
