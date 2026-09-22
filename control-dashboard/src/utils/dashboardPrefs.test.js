import { describe, it, expect, beforeEach, afterEach, vi } from "vitest";
import {
  PREF_KEYS,
  readInitialControlMode,
  writeControlMode,
  readPrefBool,
  writePrefBool,
  readInitialResMode,
  writeResMode,
} from "./dashboardPrefs.js";

describe("dashboardPrefs", () => {
  beforeEach(() => {
    localStorage.clear();
  });

  afterEach(() => {
    localStorage.clear();
  });

  it("defaults mobile control mode to joystick", () => {
    expect(readInitialControlMode(true)).toBe("joystick");
    expect(readInitialControlMode(false)).toBe("keyboard");
  });

  it("keeps separate mobile and desktop control modes", () => {
    writeControlMode("keyboard", false);
    writeControlMode("joystick", true);
    expect(readInitialControlMode(false)).toBe("keyboard");
    expect(readInitialControlMode(true)).toBe("joystick");
  });

  it("does not force desktop keyboard onto mobile default", () => {
    localStorage.setItem(PREF_KEYS.controlModeLegacy, "keyboard");
    expect(readInitialControlMode(true)).toBe("joystick");
  });

  it("persists boolean prefs", () => {
    writePrefBool(PREF_KEYS.quietMode, false);
    expect(readPrefBool(PREF_KEYS.quietMode, true)).toBe(false);
    writePrefBool(PREF_KEYS.driveAssist, true);
    expect(readPrefBool(PREF_KEYS.driveAssist, false)).toBe(true);
  });

  it("persists resolution preference", () => {
    expect(readInitialResMode()).toBe("720p");
    writeResMode("1080p");
    expect(readInitialResMode()).toBe("1080p");
  });
});
