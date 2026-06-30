import { describe, expect, it } from "vitest";
import { quantizeAnalog, snapAnalogPair } from "./JoystickControlCluster.jsx";

describe("joystick analog quantization", () => {
  it("snaps small noise to zero", () => {
    expect(quantizeAnalog(0.02)).toBe(0);
    expect(quantizeAnalog(-0.02)).toBe(0);
  });

  it("snaps held stick values to 0.05 steps", () => {
    expect(quantizeAnalog(0.52)).toBe(0.5);
    expect(quantizeAnalog(0.53)).toBe(0.55);
    expect(snapAnalogPair({ x: 0.52, y: -0.38 })).toEqual({ x: 0.5, y: -0.4 });
  });
});
