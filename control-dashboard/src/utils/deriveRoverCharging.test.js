import { describe, it, expect } from "vitest";
import { deriveRoverCharging } from "./deriveRoverCharging.js";

describe("deriveRoverCharging", () => {
  it("returns true when charging.isCharging is true", () => {
    expect(deriveRoverCharging({ charging: { isCharging: true } })).toBe(true);
  });

  it("returns false when charging.isCharging is false", () => {
    expect(deriveRoverCharging({ charging: { isCharging: false } })).toBe(false);
  });

  it("falls back to lastCharging charging_start", () => {
    expect(
      deriveRoverCharging({
        charging: { isCharging: null },
        lastCharging: { event: "charging_start" },
      }),
    ).toBe(true);
  });

  it("uses negative drain as charging hint", () => {
    expect(
      deriveRoverCharging({
        charging: {},
        battery: { drainPctPerMinute: -6 },
      }),
    ).toBe(true);
  });
});
