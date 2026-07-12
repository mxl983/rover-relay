import { describe, expect, it, vi, beforeEach, afterEach } from "vitest";
import {
  anyGamepadPhysicalInput,
  releasePointerLockIfHeld,
  wakeGamepadInput,
} from "./gamepadInput.js";

describe("gamepad activation helpers", () => {
  beforeEach(() => {
    vi.stubGlobal("window", { focus: vi.fn() });
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("detects button or stick activity", () => {
    vi.stubGlobal("navigator", {
      getGamepads: () => [
        {
          connected: true,
          buttons: [{ pressed: false, value: 0 }],
          axes: [0.2, 0, 0, 0],
        },
      ],
    });
    expect(anyGamepadPhysicalInput()).toBe(true);
  });

  it("wakeGamepadInput focuses and requests pointer lock", () => {
    const requestPointerLock = vi.fn(() => Promise.resolve());
    vi.stubGlobal("document", {
      body: { requestPointerLock },
    });
    wakeGamepadInput(document.body);
    expect(window.focus).toHaveBeenCalled();
    expect(requestPointerLock).toHaveBeenCalled();
  });

  it("releasePointerLockIfHeld exits lock", () => {
    const exitPointerLock = vi.fn();
    vi.stubGlobal("document", {
      pointerLockElement: {},
      exitPointerLock,
    });
    releasePointerLockIfHeld();
    expect(exitPointerLock).toHaveBeenCalled();
  });
});
