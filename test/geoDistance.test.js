import { describe, it, expect } from "vitest";
import { distanceMeters, parseCoordinates } from "../src/utils/geoDistance.js";

describe("geoDistance", () => {
  it("returns 0 for identical points", () => {
    const p = { latitude: 45, longitude: -75 };
    expect(distanceMeters(p, p)).toBe(0);
  });

  it("parses valid coordinates", () => {
    expect(parseCoordinates(49.1, -123.1)).toEqual({ latitude: 49.1, longitude: -123.1 });
    expect(parseCoordinates(91, 0)).toBeNull();
  });
});
