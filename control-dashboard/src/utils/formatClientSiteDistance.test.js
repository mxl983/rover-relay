import { describe, it, expect } from "vitest";
import { formatClientSiteDistance } from "./formatClientSiteDistance.js";

describe("formatClientSiteDistance", () => {
  it("shows nearby under 100m", () => {
    expect(formatClientSiteDistance(0)).toBe("nearby");
    expect(formatClientSiteDistance(99)).toBe("nearby");
  });

  it("shows km away at or above 100m", () => {
    expect(formatClientSiteDistance(100)).toBe("0.1 km away");
    expect(formatClientSiteDistance(1500)).toBe("1.5 km away");
  });

  it("returns null when unknown", () => {
    expect(formatClientSiteDistance(null)).toBeNull();
  });
});
