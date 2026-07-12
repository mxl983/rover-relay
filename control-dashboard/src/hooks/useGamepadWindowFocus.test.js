import { describe, expect, it, vi, afterEach } from "vitest";
import { renderHook, act } from "@testing-library/react";
import { useGamepadWindowFocus } from "./useGamepadWindowFocus.js";

describe("useGamepadWindowFocus", () => {
  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("does nothing when disabled", () => {
    vi.spyOn(document, "hasFocus").mockReturnValue(false);

    const { result } = renderHook(() => useGamepadWindowFocus(false));

    expect(result.current.needsFocus).toBe(false);
  });

  it("shows hint when the window is not focused", () => {
    vi.spyOn(document, "hasFocus").mockReturnValue(false);
    vi.spyOn(window, "focus").mockImplementation(() => {});

    const { result } = renderHook(() => useGamepadWindowFocus(true));

    expect(result.current.needsFocus).toBe(true);
  });

  it("claimFocus focuses the window and clears the hint", () => {
    const focus = vi.spyOn(window, "focus").mockImplementation(() => {});
    vi.spyOn(document, "hasFocus")
      .mockReturnValueOnce(false)
      .mockReturnValue(true);

    const { result } = renderHook(() => useGamepadWindowFocus(true));

    act(() => {
      result.current.claimFocus();
    });

    expect(focus).toHaveBeenCalled();
    expect(result.current.needsFocus).toBe(false);
  });
});
