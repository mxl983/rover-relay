import { describe, expect, it, vi, beforeEach, afterEach } from "vitest";
import { toggleDocumentFullscreen } from "./fullscreen.js";

describe("toggleDocumentFullscreen", () => {
  beforeEach(() => {
    vi.stubGlobal("document", {
      fullscreenElement: null,
      webkitFullscreenElement: null,
      documentElement: {
        requestFullscreen: vi.fn(() => Promise.resolve()),
        webkitRequestFullscreen: undefined,
      },
      exitFullscreen: vi.fn(() => Promise.resolve()),
      webkitExitFullscreen: undefined,
    });
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("requests fullscreen when not active", () => {
    toggleDocumentFullscreen();
    expect(document.documentElement.requestFullscreen).toHaveBeenCalled();
  });

  it("exits fullscreen when active", () => {
    document.fullscreenElement = document.documentElement;
    toggleDocumentFullscreen();
    expect(document.exitFullscreen).toHaveBeenCalled();
  });
});
