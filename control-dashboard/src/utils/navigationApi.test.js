import { describe, expect, it } from "vitest";
import { readNavigationEnabled } from "./navigationApi.js";

describe("navigationApi", () => {
  it("reads enabled from API responses", () => {
    expect(readNavigationEnabled({ success: true, enabled: true })).toBe(true);
    expect(readNavigationEnabled({ enabled: false })).toBe(false);
    expect(readNavigationEnabled({})).toBeNull();
  });
});
