import { describe, it, expect } from "vitest";
import { remapReportedBatteryPct } from "./batteryPctScale.js";

describe("remapReportedBatteryPct", () => {
  it("maps degradation anchors", () => {
    expect(remapReportedBatteryPct(60)).toBe(20);
    expect(remapReportedBatteryPct(40)).toBe(0);
    expect(remapReportedBatteryPct(100)).toBe(100);
  });
});
