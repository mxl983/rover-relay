import { describe, it, expect } from "vitest";
import { getBatteryPercentage } from "./batteryFromVoltage.js";

describe("getBatteryPercentage", () => {
  it("maps mid voltage to 75.8%", () => {
    expect(getBatteryPercentage(11.5)).toBe(75.8);
  });
  it("clamps to 0–100", () => {
    expect(getBatteryPercentage(8)).toBe(0);
    expect(getBatteryPercentage(13)).toBe(100);
  });
  it("returns null for non-finite input", () => {
    expect(getBatteryPercentage(null)).toBeNull();
    expect(getBatteryPercentage(NaN)).toBeNull();
  });
});
