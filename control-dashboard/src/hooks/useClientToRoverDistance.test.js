import { describe, expect, it } from "vitest";
import { haversineMeters } from "./useClientToRoverDistance.js";

describe("haversineMeters", () => {
  it("is ~0 for identical points", () => {
    expect(haversineMeters(49.17, -123.14, 49.17, -123.14)).toBeLessThan(1);
  });

  it("matches a known short baseline (~111m per 0.001° lat)", () => {
    const d = haversineMeters(49.0, -123.0, 49.001, -123.0);
    expect(d).toBeGreaterThan(100);
    expect(d).toBeLessThan(120);
  });
});
