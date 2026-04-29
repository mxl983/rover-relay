import { describe, it, expect, afterEach } from "vitest";
import { renderHook, act } from "@testing-library/react";
import { useFullscreen } from "./useFullscreen.js";

describe("useFullscreen", () => {
  afterEach(() => {
    try {
      delete document.fullscreenElement;
    } catch {
      // ignore
    }
  });

  it("starts false", () => {
    const { result } = renderHook(() => useFullscreen());
    expect(result.current).toBe(false);
  });

  it("updates on fullscreenchange", () => {
    const { result } = renderHook(() => useFullscreen());
    act(() => {
      Object.defineProperty(document, "fullscreenElement", {
        configurable: true,
        value: document.body,
      });
      document.dispatchEvent(new Event("fullscreenchange"));
    });
    expect(result.current).toBe(true);
    act(() => {
      Object.defineProperty(document, "fullscreenElement", {
        configurable: true,
        value: null,
      });
      document.dispatchEvent(new Event("fullscreenchange"));
    });
    expect(result.current).toBe(false);
  });
});
