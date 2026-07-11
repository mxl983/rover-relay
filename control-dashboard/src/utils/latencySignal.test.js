import { describe, expect, it } from "vitest";
import { HIGH_LATENCY_MS, isHighLatency } from "./latencySignal.js";

describe("latencySignal", () => {
  it("treats latency above 300ms as high", () => {
    expect(HIGH_LATENCY_MS).toBe(300);
    expect(isHighLatency(301)).toBe(true);
    expect(isHighLatency(300)).toBe(false);
    expect(isHighLatency(120)).toBe(false);
    expect(isHighLatency(null)).toBe(false);
  });
});
