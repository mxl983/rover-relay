import { describe, it, expect } from "vitest";
import { getBatteryPercentage } from "./batteryFromVoltage.js";

describe("getBatteryPercentage", () => {
  it("maps voltage linearly from 9V to 12.3V", () => {
    expect(getBatteryPercentage(9)).toBe(0);
    expect(getBatteryPercentage(12.3)).toBe(100);
    expect(getBatteryPercentage(11.5)).toBe(75.8);
  });
  it("clamps to 0–100 outside range", () => {
    expect(getBatteryPercentage(8)).toBe(0);
    expect(getBatteryPercentage(13)).toBe(100);
  });
  it("returns null for non-finite input", () => {
    expect(getBatteryPercentage(null)).toBeNull();
    expect(getBatteryPercentage(NaN)).toBeNull();
  });
});
