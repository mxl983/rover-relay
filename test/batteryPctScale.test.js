import { describe, it, expect } from "vitest";
import { remapReportedBatteryPct, remapReportedBatteryPctRounded } from "../src/utils/batteryPctScale.js";

describe("remapReportedBatteryPct", () => {
  it("maps anchor points", () => {
    expect(remapReportedBatteryPct(100)).toBe(100);
    expect(remapReportedBatteryPct(60)).toBe(20);
    expect(remapReportedBatteryPct(40)).toBe(0);
  });

  it("stretches between anchors", () => {
    expect(remapReportedBatteryPct(50)).toBe(10);
    expect(remapReportedBatteryPct(80)).toBe(60);
    expect(remapReportedBatteryPct(70)).toBe(40);
  });

  it("clamps outside range", () => {
    expect(remapReportedBatteryPct(30)).toBe(0);
    expect(remapReportedBatteryPct(105)).toBe(100);
  });

  it("returns null for invalid input", () => {
    expect(remapReportedBatteryPct(null)).toBeNull();
    expect(remapReportedBatteryPct("")).toBeNull();
    expect(remapReportedBatteryPct(NaN)).toBeNull();
  });

  it("rounds", () => {
    expect(remapReportedBatteryPctRounded(75.75)).toBe(51.5);
  });
});
