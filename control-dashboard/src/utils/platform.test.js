import { describe, expect, it, vi, afterEach } from "vitest";
import { isSteamOS } from "./platform.js";

describe("isSteamOS", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("returns true for explicit SteamOS user agents", () => {
    vi.stubGlobal("navigator", {
      userAgent: "Mozilla/5.0 (SteamOS) AppleWebKit/537.36 Chrome/120.0.0.0",
      platform: "Linux x86_64",
    });
    expect(isSteamOS()).toBe(true);
  });

  it("returns true for Linux desktop Chrome (Steam Deck desktop mode)", () => {
    vi.stubGlobal("navigator", {
      userAgent:
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
      platform: "Linux x86_64",
    });
    expect(isSteamOS()).toBe(true);
  });

  it("returns false for Windows and macOS", () => {
    vi.stubGlobal("navigator", {
      userAgent:
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
      platform: "Win32",
    });
    expect(isSteamOS()).toBe(false);

    vi.stubGlobal("navigator", {
      userAgent:
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
      platform: "MacIntel",
    });
    expect(isSteamOS()).toBe(false);
  });
});
