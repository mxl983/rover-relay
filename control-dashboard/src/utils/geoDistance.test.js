import { describe, it, expect } from "vitest";
import { distanceMeters } from "./geoDistance.js";

describe("geoDistance", () => {
  it("returns 0 for identical points", () => {
    const p = { latitude: 45, longitude: -75 };
    expect(distanceMeters(p, p)).toBe(0);
  });

  it("computes a positive distance between nearby points", () => {
    const a = { latitude: 49.0, longitude: -123.0 };
    const b = { latitude: 49.19, longitude: -123.12 };
    const d = distanceMeters(a, b);
    expect(d).toBeGreaterThan(1000);
    expect(d).toBeLessThan(50_000);
  });

  it("returns null for invalid coords", () => {
    expect(distanceMeters({ latitude: 1, longitude: 2 }, null)).toBeNull();
  });
});
