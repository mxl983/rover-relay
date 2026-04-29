import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { FullscreenButton } from "./FullscreenButton.jsx";

describe("FullscreenButton", () => {
  beforeEach(() => {
    Object.defineProperty(document, "fullscreenEnabled", {
      value: true,
      configurable: true,
    });
    vi.spyOn(navigator, "userAgent", "get").mockReturnValue("Mozilla/5.0 (Windows NT 10.0)");
  });

  it("calls requestFullscreen when entering fullscreen", async () => {
    const user = userEvent.setup();
    const req = vi.fn().mockResolvedValue(undefined);
    Object.defineProperty(document.documentElement, "requestFullscreen", {
      value: req,
      configurable: true,
    });
    render(<FullscreenButton />);
    await user.click(screen.getByTitle("Enter Fullscreen"));
    expect(req).toHaveBeenCalled();
  });
});
