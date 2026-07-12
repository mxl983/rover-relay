import { describe, expect, it, vi, afterEach } from "vitest";
import { renderHook, act } from "@testing-library/react";
import { useGamepadActivation } from "./useGamepadActivation.js";

const LINUX_UA =
  "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36";
const WINDOWS_UA =
  "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36";

describe("useGamepadActivation", () => {
  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("skips activation gate on non-Linux platforms", () => {
    vi.spyOn(navigator, "userAgent", "get").mockReturnValue(WINDOWS_UA);
    vi.spyOn(navigator, "platform", "get").mockReturnValue("Win32");

    const { result } = renderHook(() => useGamepadActivation(true));

    expect(result.current.needsActivation).toBe(false);
    expect(result.current.ready).toBe(true);
  });

  it("requires activation on Linux when enabled", () => {
    vi.spyOn(navigator, "userAgent", "get").mockReturnValue(LINUX_UA);
    vi.spyOn(navigator, "platform", "get").mockReturnValue("Linux x86_64");

    const { result } = renderHook(() => useGamepadActivation(true));

    expect(result.current.needsActivation).toBe(true);
    expect(result.current.ready).toBe(false);
  });

  it("marks ready when markReady is called", () => {
    vi.spyOn(navigator, "userAgent", "get").mockReturnValue(LINUX_UA);
    vi.spyOn(navigator, "platform", "get").mockReturnValue("Linux x86_64");

    const { result } = renderHook(() => useGamepadActivation(true));

    act(() => {
      result.current.markReady();
    });

    expect(result.current.needsActivation).toBe(false);
    expect(result.current.ready).toBe(true);
  });
});
