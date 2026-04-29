import { describe, it, expect, beforeEach, afterEach } from "vitest";
import { renderHook, act } from "@testing-library/react";
import { useIsMobile } from "./useIsMobile.jsx";

describe("useIsMobile", () => {
  const origWidth = window.innerWidth;
  const origTouch = navigator.maxTouchPoints;

  beforeEach(() => {
    Object.defineProperty(window, "innerWidth", { value: 1920, configurable: true });
    Object.defineProperty(navigator, "maxTouchPoints", { value: 0, configurable: true });
  });

  afterEach(() => {
    Object.defineProperty(window, "innerWidth", { value: origWidth, configurable: true });
    Object.defineProperty(navigator, "maxTouchPoints", { value: origTouch, configurable: true });
  });

  it("true when viewport narrow", () => {
    const { result } = renderHook(() => useIsMobile());
    act(() => {
      Object.defineProperty(window, "innerWidth", { value: 500, configurable: true });
      window.dispatchEvent(new Event("resize"));
    });
    expect(result.current).toBe(true);
  });
});
