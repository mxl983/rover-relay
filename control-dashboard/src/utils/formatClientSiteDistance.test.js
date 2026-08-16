import { describe, it, expect } from "vitest";
import { formatClientSiteDistance } from "./formatClientSiteDistance.js";

describe("formatClientSiteDistance", () => {
  it("shows near rover under 500m", () => {
    expect(formatClientSiteDistance(120)).toBe("near rover");
    expect(formatClientSiteDistance(499)).toBe("near rover");
  });

  it("shows km away at or above 500m", () => {
    expect(formatClientSiteDistance(500)).toBe("0.5 km away");
    expect(formatClientSiteDistance(1500)).toBe("1.5 km away");
  });

  it("returns null when unknown", () => {
    expect(formatClientSiteDistance(null)).toBeNull();
  });
});
