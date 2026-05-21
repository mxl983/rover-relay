import { describe, it, expect } from "vitest";
import { formatRoverDistance } from "./formatRoverDistance.js";

describe("formatRoverDistance", () => {
  it("shows near rover under 500m", () => {
    expect(formatRoverDistance(120)).toBe("near rover");
    expect(formatRoverDistance(499)).toBe("near rover");
  });

  it("shows km away at or above 500m", () => {
    expect(formatRoverDistance(500)).toBe("0.5 km away");
    expect(formatRoverDistance(1500)).toBe("1.5 km away");
  });

  it("returns null when unknown", () => {
    expect(formatRoverDistance(null)).toBeNull();
  });
});
