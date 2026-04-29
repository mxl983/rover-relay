import { describe, it, expect } from "vitest";
import { isAllowedCaptureUrl } from "./capture";
import { getAllowedCaptureOrigin } from "../config.js";

describe("isAllowedCaptureUrl", () => {
  it("returns false for empty or non-string", () => {
    expect(isAllowedCaptureUrl("")).toBe(false);
    expect(isAllowedCaptureUrl(null)).toBe(false);
    expect(isAllowedCaptureUrl(undefined)).toBe(false);
  });

  it("returns false for URL with different origin", () => {
    expect(isAllowedCaptureUrl("https://evil.com/path")).toBe(false);
    expect(isAllowedCaptureUrl("https://other.com/capture.jpg")).toBe(false);
  });

  it("returns true for same origin URL", () => {
    const origin = getAllowedCaptureOrigin();
    expect(isAllowedCaptureUrl(`${origin}/photos/capture_1.jpg`)).toBe(true);
    expect(isAllowedCaptureUrl(`${origin}/anything`)).toBe(true);
  });
});
