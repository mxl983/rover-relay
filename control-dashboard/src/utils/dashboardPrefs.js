/** Dashboard UI prefs persisted in localStorage across sessions. */

export const PREF_KEYS = {
  controlModeDesktop: "rover-dashboard-control-mode-desktop",
  controlModeMobile: "rover-dashboard-control-mode-mobile",
  /** Legacy single key — migrated into desktop/mobile on first read. */
  controlModeLegacy: "rover-dashboard-control-mode",
  metricsPanel: "rover-dashboard-metrics-panel",
  roverSpeaker: "rover-dashboard-rover-speaker",
  dashMic: "rover-dashboard-dash-mic",
  quietMode: "rover-dashboard-quiet-mode",
  driveAssist: "rover-dashboard-drive-assist",
  resMode: "rover-dashboard-res-mode",
};

export function readPrefString(key) {
  if (typeof window === "undefined") return null;
  try {
    return window.localStorage.getItem(key);
  } catch {
    return null;
  }
}

export function writePrefString(key, value) {
  if (typeof window === "undefined") return;
  try {
    window.localStorage.setItem(key, value);
  } catch {
    /* ignore quota / private mode */
  }
}

export function readPrefBool(key, defaultValue) {
  const v = readPrefString(key);
  if (v === "true") return true;
  if (v === "false") return false;
  return defaultValue;
}

export function writePrefBool(key, value) {
  writePrefString(key, value ? "true" : "false");
}

const CONTROL_MODES = new Set(["keyboard", "joystick", "immersive"]);

/**
 * Control mode: separate mobile/desktop prefs so a desktop "keyboard" choice
 * does not force keyboard on phones. Mobile default = joystick.
 */
export function readInitialControlMode(isMobile) {
  if (typeof window === "undefined") {
    return isMobile ? "joystick" : "keyboard";
  }
  const key = isMobile
    ? PREF_KEYS.controlModeMobile
    : PREF_KEYS.controlModeDesktop;
  const stored = readPrefString(key);
  if (CONTROL_MODES.has(stored)) return stored;

  const legacy = readPrefString(PREF_KEYS.controlModeLegacy);
  if (CONTROL_MODES.has(legacy)) {
    // Migrate once into the form-factor key; mobile still defaults to joystick
    // unless they already used the legacy key on a phone (then honor it).
    if (isMobile) {
      if (legacy === "joystick" || legacy === "immersive") {
        writePrefString(key, legacy);
        return legacy;
      }
      return "joystick";
    }
    writePrefString(key, legacy);
    return legacy;
  }

  return isMobile ? "joystick" : "keyboard";
}

export function writeControlMode(mode, isMobile) {
  if (!CONTROL_MODES.has(mode)) return;
  const key = isMobile
    ? PREF_KEYS.controlModeMobile
    : PREF_KEYS.controlModeDesktop;
  writePrefString(key, mode);
  // Keep legacy key updated for older builds / tests.
  writePrefString(PREF_KEYS.controlModeLegacy, mode);
}

export function readInitialResMode() {
  const v = readPrefString(PREF_KEYS.resMode);
  if (v === "480p" || v === "720p" || v === "1080p") return v;
  return "720p";
}

export function writeResMode(mode) {
  if (mode === "480p" || mode === "720p" || mode === "1080p") {
    writePrefString(PREF_KEYS.resMode, mode);
  }
}
