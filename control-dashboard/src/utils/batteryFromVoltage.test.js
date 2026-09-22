import { describe, it, expect } from "vitest";
import { getBatteryPercentage } from "./batteryFromVoltage.js";

describe("getBatteryPercentage", () => {
  it("maps voltage linearly from 9.6V to 12.6V", () => {
    expect(getBatteryPercentage(9.6)).toBe(0);
    expect(getBatteryPercentage(12.6)).toBe(100);
    expect(getBatteryPercentage(11.1)).toBe(50);
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
