import { describe, it, expect } from "vitest";
import { getWifiLevel, isWifiWeak } from "./wifiSignal.js";

describe("wifiSignal helpers", () => {
  it("classifies signal levels", () => {
    expect(getWifiLevel(-40)).toBe(4);
    expect(getWifiLevel(-60)).toBe(3);
    expect(getWifiLevel(-70)).toBe(2);
    expect(getWifiLevel(-80)).toBe(1);
    expect(getWifiLevel(-90)).toBe(0);
  });

  it("treats level 1 and 0 as weak", () => {
    expect(isWifiWeak(-80)).toBe(true);
    expect(isWifiWeak(-90)).toBe(true);
    expect(isWifiWeak(-70)).toBe(false);
    expect(isWifiWeak(-50)).toBe(false);
  });
});
