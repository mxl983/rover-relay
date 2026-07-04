import React, { useEffect, useRef } from "react";
import PropTypes from "prop-types";
import nipplejs from "nipplejs";

const ZONE_SIZE_PX = 100;
const RESET_BTN_SIZE = 20; 
const NEUTRAL_BORDER = "rgba(255, 255, 255, 0.2)";
const NEUTRAL_LABEL = "rgba(255, 255, 255, 0.75)";
const NEUTRAL_BTN = "rgba(10, 10, 10, 0.9)"; 

function clamp1(v) {
  return Math.max(-1, Math.min(1, v));
}

/** Minimum |y| once the stick leaves neutral — overcomes motor deadband. */
const MIN_DRIVE_THROTTLE = 0.42;
/** Minimum |x| for turn-in-place (little/no throttle). */
const MIN_TURN = 0.28;
/** Stick magnitudes below this are treated as centered. */
const STICK_IDLE = 0.04;

/**
 * Map stick deflection to a responsive drive vector.
 * - Lateral (x) is proportional — no quantization / attenuation for fine turns.
 * - Forward/back (y) starts at MIN_DRIVE_THROTTLE and ramps to full.
 */
export function applyDriveCurve(raw) {
  let x = clamp1(Number(raw?.x) || 0);
  let y = clamp1(Number(raw?.y) || 0);
  if (Math.abs(x) < STICK_IDLE) x = 0;
  if (Math.abs(y) < STICK_IDLE) y = 0;

  const absY = Math.abs(y);
  if (absY > 0) {
    // stick 0→1 maps to MIN_DRIVE_THROTTLE→1 (half stick ≈ 0.71)
    y = Math.sign(y) * (MIN_DRIVE_THROTTLE + (1 - MIN_DRIVE_THROTTLE) * absY);
  }

  const absX = Math.abs(x);
  if (absX > 0) {
    // Keep fine turns proportional while driving; floor only for spin-in-place.
    if (absY < 0.12) {
      x = Math.sign(x) * (MIN_TURN + (1 - MIN_TURN) * absX);
    }
  }

  return { x: clamp1(x), y: clamp1(y) };
}

/** Radial dead zone; axes expected in range ~[-1, 1]. */
function deadzone2d(x, y, dead) {
  const m = Math.hypot(x, y);
  if (m < dead) return { x: 0, y: 0 };
  return { x, y };
}

const GAMEPAD_DEAD_ZONE = 0.14;
const GIMBAL_LINEAR_SCALE = 0.58;
const TRIGGER_HELD_THRESHOLD = 0.45;
/** Cap drive/gimbal WS updates while sticks are held (~25 Hz). Stops are always immediate. */
const ANALOG_SEND_MIN_INTERVAL_MS = 40;
/** Gimbal only — drive is continuous for fine steering. */
const GIMBAL_ANALOG_STEP = 0.03;
const DRIVE_CHANGE_THRESHOLD = 0.015;
const GIMBAL_CHANGE_THRESHOLD = 0.02;

/** @deprecated kept for tests; drive path no longer quantizes. */
export function quantizeAnalog(v, step = 0.05) {
  if (!Number.isFinite(v) || Math.abs(v) < step * 0.45) return 0;
  const q = Math.round(v / step) * step;
  return Math.max(-1, Math.min(1, q));
}

export function snapAnalogPair({ x = 0, y = 0 }, step = GIMBAL_ANALOG_STEP) {
  return { x: quantizeAnalog(x, step), y: quantizeAnalog(y, step) };
}

/** Continuous drive vector (no step quantization on turn or throttle). */
export function prepareDriveVector(raw) {
  const x = clamp1(Number(raw?.x) || 0);
  const y = clamp1(Number(raw?.y) || 0);
  return {
    x: Math.abs(x) < 1e-4 ? 0 : x,
    y: Math.abs(y) < 1e-4 ? 0 : y,
  };
}

/** Standard mapping: LB 4, RB 5, LT 6, RT 7 (analog value 0–1 when supported). */
function triggerHeld(button) {
  if (!button) return false;
  if (button.pressed) return true;
  const v = typeof button.value === "number" ? button.value : 0;
  return v >= TRIGGER_HELD_THRESHOLD;
}

function bumperHeld(button) {
  return Boolean(button?.pressed);
}

function getFirstConnectedGamepad() {
  const pads = typeof navigator !== "undefined" ? navigator.getGamepads?.() : null;
  if (!pads) return null;
  for (let i = 0; i < pads.length; i++) {
    const g = pads[i];
    if (g?.connected) return g;
  }
  return null;
}

function readGamepadSticks(gp) {
  const a = gp.axes;
  if (!a?.length) {
    return { lx: 0, ly: 0, rx: 0, ry: 0 };
  }
  let lx = a[0] ?? 0;
  let ly = a[1] ?? 0;
  let rx = a[2] ?? 0;
  let ry = a[3] ?? 0;
  // Firefox / some mappings expose the right stick on axes 4–5 when 2–3 are triggers.
  if (a.length >= 6 && (Math.abs(rx) < 0.02 && Math.abs(ry) < 0.02)) {
    rx = a[4] ?? 0;
    ry = a[5] ?? 0;
  }
  return { lx, ly, rx, ry };
}

function sticksPhysicallyCentered(gp) {
  if (!gp) return true;
  const { lx, ly, rx, ry } = readGamepadSticks(gp);
  const left = deadzone2d(lx, ly, GAMEPAD_DEAD_ZONE);
  const right = deadzone2d(rx, ry, GAMEPAD_DEAD_ZONE);
  return Math.hypot(left.x, left.y) < 0.001 && Math.hypot(right.x, right.y) < 0.001;
}

/**
 * Touch + first connected gamepad. Gamepad stick outside the dead zone overrides that axis pair.
 * When ignoreGamepadRef is true (tab blur / safety), gamepad is ignored until both sticks are centered.
 */
function mergeTouchAndGamepad(touch, ignoreGamepadRef) {
  const gp = getFirstConnectedGamepad();
  if (ignoreGamepadRef.current) {
    if (sticksPhysicallyCentered(gp)) {
      ignoreGamepadRef.current = false;
    }
    return {
      drive: { ...touch.drive },
      gimbal: { ...touch.gimbal },
    };
  }
  if (!gp) {
    return {
      drive: { ...touch.drive },
      gimbal: { ...touch.gimbal },
    };
  }
  const { lx, ly, rx, ry } = readGamepadSticks(gp);
  const leftRaw = deadzone2d(lx, ly, GAMEPAD_DEAD_ZONE);
  const rightRaw = deadzone2d(rx, ry, GAMEPAD_DEAD_ZONE);
  const leftMag = Math.hypot(leftRaw.x, leftRaw.y);
  const rightMag = Math.hypot(rightRaw.x, rightRaw.y);

  let drive = { ...touch.drive };
  if (leftMag > 0) {
    drive = applyDriveCurve(leftRaw);
  }

  let gimbal = { ...touch.gimbal };
  if (rightMag > 0) {
    gimbal = {
      x: clamp1(rightRaw.x * GIMBAL_LINEAR_SCALE),
      y: clamp1(rightRaw.y * GIMBAL_LINEAR_SCALE),
    };
  }

  return { drive, gimbal };
}

export const DualJoystickControls = ({
  onDrive,
  onReset,
  onLookDown,
  onLaserToggle,
  laserOn,
  onHeadlightToggle,
  headlightOn,
  onVoiceStart: _onVoiceStart,
  onVoiceStop: _onVoiceStop,
  voiceSupported: _voiceSupported,
  voiceListening: _voiceListening,
  onToggleBackupView,
  backupViewEnabled,
  onTreat,
  immersive = false,
  children,
}) => {
  const leftZoneRef = useRef(null);
  const rightZoneRef = useRef(null);
  const managersRef = useRef({ drive: null, look: null });

  const onDriveRef = useRef(onDrive);
  const touchAnalogRef = useRef({
    drive: { x: 0, y: 0 },
    gimbal: { x: 0, y: 0 },
  });
  const analogState = useRef({
    drive: { x: 0, y: 0 },
    gimbal: { x: 0, y: 0 },
  });
  const ignoreGamepadRef = useRef(false);
  const lastSentRef = useRef({ drive: null, gimbal: null });
  const lastSendAtRef = useRef(0);
  const gimbalRafRef = useRef(null);
  const syncMergedRef = useRef(() => {});
  const gamepadRafRef = useRef(null);

  useEffect(() => {
    onDriveRef.current = onDrive;
  }, [onDrive]);

  const onResetRef = useRef(onReset);
  const onLookDownRef = useRef(onLookDown);
  const onLaserToggleRef = useRef(onLaserToggle);
  const onHeadlightToggleRef = useRef(onHeadlightToggle);
  const onToggleBackupViewRef = useRef(onToggleBackupView);
  const onTreatRef = useRef(onTreat);
  useEffect(() => {
    onResetRef.current = onReset;
    onLookDownRef.current = onLookDown;
    onLaserToggleRef.current = onLaserToggle;
    onHeadlightToggleRef.current = onHeadlightToggle;
    onToggleBackupViewRef.current = onToggleBackupView;
    onTreatRef.current = onTreat;
  }, [onReset, onLookDown, onLaserToggle, onHeadlightToggle, onToggleBackupView, onTreat]);

  const gamepadButtonsPrevRef = useRef({
    lt: false,
    rt: false,
    lb: false,
    rb: false,
    l3: false,
    /** Xbox Y / north face (index 3) — treat shortcut */
    faceY: false,
  });

  const driveStateChanged = (a, b) =>
    a === null ||
    b === null ||
    Math.abs((a.x ?? 0) - (b.x ?? 0)) > DRIVE_CHANGE_THRESHOLD ||
    Math.abs((a.y ?? 0) - (b.y ?? 0)) > DRIVE_CHANGE_THRESHOLD;

  const sendState = (drive, gimbal, updateLast = true) => {
    if (updateLast) lastSentRef.current = { drive: { ...drive }, gimbal: { ...gimbal } };
    if (onDriveRef.current) onDriveRef.current({ drive, gimbal });
  };

  const sendDriveStopWithRetries = () => {
    touchAnalogRef.current.drive = { x: 0, y: 0 };
    const merged = mergeTouchAndGamepad(touchAnalogRef.current, ignoreGamepadRef);
    analogState.current = merged;
    sendState(merged.drive, merged.gimbal, true);
    const retry = () => {
      const m = mergeTouchAndGamepad(touchAnalogRef.current, ignoreGamepadRef);
      analogState.current = m;
      onDriveRef.current?.({ drive: m.drive, gimbal: m.gimbal });
    };
    setTimeout(retry, 45);
    setTimeout(retry, 110);
  };

  const sendGimbalStopWithRetries = () => {
    touchAnalogRef.current.gimbal = { x: 0, y: 0 };
    const merged = mergeTouchAndGamepad(touchAnalogRef.current, ignoreGamepadRef);
    analogState.current = merged;
    sendState(merged.drive, merged.gimbal, true);
    const retry = () => {
      const m = mergeTouchAndGamepad(touchAnalogRef.current, ignoreGamepadRef);
      analogState.current = m;
      onDriveRef.current?.({ drive: m.drive, gimbal: m.gimbal });
    };
    setTimeout(retry, 45);
    setTimeout(retry, 110);
  };

  const sendAllStopWithRetries = () => {
    ignoreGamepadRef.current = true;
    touchAnalogRef.current = { drive: { x: 0, y: 0 }, gimbal: { x: 0, y: 0 } };
    analogState.current = { drive: { x: 0, y: 0 }, gimbal: { x: 0, y: 0 } };
    sendState({ x: 0, y: 0 }, { x: 0, y: 0 }, true);
    const retry = () => {
      const m = mergeTouchAndGamepad(touchAnalogRef.current, ignoreGamepadRef);
      analogState.current = m;
      onDriveRef.current?.({ drive: m.drive, gimbal: m.gimbal });
    };
    setTimeout(retry, 45);
    setTimeout(retry, 110);
  };

  const sendIfChanged = (isStop = false) => {
    const drive = prepareDriveVector(analogState.current.drive);
    const gimbal = snapAnalogPair(analogState.current.gimbal, GIMBAL_ANALOG_STEP);
    const last = lastSentRef.current;
    const driveChanged = isStop || driveStateChanged(drive, last.drive);
    const gimbalChanged =
      isStop ||
      last.gimbal === null ||
      Math.abs((gimbal.x ?? 0) - (last.gimbal.x ?? 0)) > GIMBAL_CHANGE_THRESHOLD ||
      Math.abs((gimbal.y ?? 0) - (last.gimbal.y ?? 0)) > GIMBAL_CHANGE_THRESHOLD;
    if (!driveChanged && !gimbalChanged) return;
    const now = performance.now();
    if (!isStop && now - lastSendAtRef.current < ANALOG_SEND_MIN_INTERVAL_MS) return;
    lastSendAtRef.current = now;
    sendState(drive, gimbal);
  };

  const syncMergedAndSend = (isStop = false) => {
    const merged = mergeTouchAndGamepad(touchAnalogRef.current, ignoreGamepadRef);
    analogState.current = merged;
    sendIfChanged(isStop);
  };
  syncMergedRef.current = syncMergedAndSend;

  const startGimbalRaf = () => {
    if (gimbalRafRef.current) return;
    const tick = () => {
      syncMergedRef.current(false);
      const gimbal = analogState.current.gimbal;
      const mag = Math.sqrt((gimbal.x ?? 0) ** 2 + (gimbal.y ?? 0) ** 2);
      if (mag < 0.02) {
        gimbalRafRef.current = null;
        return;
      }
      gimbalRafRef.current = requestAnimationFrame(tick);
    };
    gimbalRafRef.current = requestAnimationFrame(tick);
  };

  const stopGimbalRaf = () => {
    if (gimbalRafRef.current) {
      cancelAnimationFrame(gimbalRafRef.current);
      gimbalRafRef.current = null;
    }
  };

  useEffect(() => {
    if (immersive) return undefined;

    const leftEl = leftZoneRef.current;
    const rightEl = rightZoneRef.current;
    if (!leftEl || !rightEl) return;

    const commonOptions = {
      mode: "static",
      position: { left: "50%", top: "50%" },
      size: 110,
      threshold: 0.05,
      catchDistance: 150,
    };

    // Drive stick: larger zone, lower threshold, bigger catch for easier straight-line fwd/back
    const driveOptions = {
      ...commonOptions,
      zone: leftEl,
      color: "rgba(255, 255, 255, 0.3)",
      size: 110,
      threshold: 0.03,
      catchDistance: 200,
    };

    const driveManager = nipplejs.create(driveOptions);
    const lookManager = nipplejs.create({
      ...commonOptions,
      zone: rightEl,
      color: "rgba(255, 255, 255, 0.3)",
    });

    managersRef.current.drive = driveManager;
    managersRef.current.look = lookManager;

    const toAnalog = (data) => {
      const force = typeof data.force === "number" ? data.force : (data.distance ? Math.min(1, data.distance / 50) : 1);
      if (data.vector && typeof data.vector.x === "number" && typeof data.vector.y === "number") {
        return { x: data.vector.x * force, y: -data.vector.y * force };
      }
      const rad = data.angle?.radian ?? 0;
      return { x: Math.cos(rad) * force, y: -Math.sin(rad) * force };
    };

    // Gimbal: linear and less sensitive (scale down so small drag = proportional movement)
    const toGimbalAnalog = (data) => {
      const raw = toAnalog(data);
      return {
        x: clamp1(raw.x * GIMBAL_LINEAR_SCALE),
        y: clamp1(raw.y * GIMBAL_LINEAR_SCALE),
      };
    };

    const toDriveAnalog = (data) => applyDriveCurve(toAnalog(data));

    driveManager.on("move", (evt, data) => {
      touchAnalogRef.current.drive = toDriveAnalog(data);
      syncMergedRef.current(false);
    });

    driveManager.on("end", () => {
      sendDriveStopWithRetries();
    });

    lookManager.on("move", (evt, data) => {
      touchAnalogRef.current.gimbal = toGimbalAnalog(data);
      startGimbalRaf();
    });

    lookManager.on("end", () => {
      stopGimbalRaf();
      sendGimbalStopWithRetries();
    });

    const handleSafetyStop = () => {
      stopGimbalRaf();
      sendAllStopWithRetries();
    };
    const onVisibility = () => {
      if (document.hidden || document.visibilityState !== "visible") {
        handleSafetyStop();
      }
    };
    window.addEventListener("blur", handleSafetyStop);
    document.addEventListener("visibilitychange", onVisibility);

    return () => {
      if (gimbalRafRef.current) {
        cancelAnimationFrame(gimbalRafRef.current);
        gimbalRafRef.current = null;
      }
      window.removeEventListener("blur", handleSafetyStop);
      document.removeEventListener("visibilitychange", onVisibility);
      sendAllStopWithRetries();
      driveManager.destroy();
      lookManager.destroy();
    };
  }, [immersive]);

  useEffect(() => {
    if (!immersive) return undefined;

    const handleSafetyStop = () => {
      sendAllStopWithRetries();
    };
    const onVisibility = () => {
      if (document.hidden || document.visibilityState !== "visible") {
        handleSafetyStop();
      }
    };
    window.addEventListener("blur", handleSafetyStop);
    document.addEventListener("visibilitychange", onVisibility);
    return () => {
      window.removeEventListener("blur", handleSafetyStop);
      document.removeEventListener("visibilitychange", onVisibility);
      handleSafetyStop();
    };
  }, [immersive]);

  useEffect(() => {
    if (typeof navigator === "undefined" || !navigator.getGamepads) return undefined;

    const pump = () => {
      syncMergedRef.current(false);

      const gp = getFirstConnectedGamepad();
      const prev = gamepadButtonsPrevRef.current;
      if (!gp) {
        gamepadButtonsPrevRef.current = {
          lt: false,
          rt: false,
          lb: false,
          rb: false,
          l3: false,
          faceY: false,
        };
      } else {
        const lt = triggerHeld(gp.buttons?.[6]);
        const rt = triggerHeld(gp.buttons?.[7]);
        const lb = bumperHeld(gp.buttons?.[4]);
        const rb = bumperHeld(gp.buttons?.[5]);
        // Xbox L3 / left stick click (standard mapping button index 10).
        const l3 = bumperHeld(gp.buttons?.[10]);
        const faceY = bumperHeld(gp.buttons?.[3]);
        const allowActions = !ignoreGamepadRef.current;
        if (allowActions) {
          if (lt && !prev.lt) onResetRef.current?.();
          if (rt && !prev.rt) onLookDownRef.current?.();
          if (lb && !prev.lb) onLaserToggleRef.current?.();
          if (rb && !prev.rb) onHeadlightToggleRef.current?.();
          if (l3 && !prev.l3) onToggleBackupViewRef.current?.();
          if (faceY && !prev.faceY) onTreatRef.current?.();
        }
        gamepadButtonsPrevRef.current = { lt, rt, lb, rb, l3, faceY };
      }

      const pads = navigator.getGamepads();
      let anyConnected = false;
      for (let i = 0; i < pads.length; i++) {
        if (pads[i]?.connected) {
          anyConnected = true;
          break;
        }
      }
      if (anyConnected) {
        gamepadRafRef.current = requestAnimationFrame(pump);
      } else {
        gamepadRafRef.current = null;
      }
    };

    const kick = () => {
      if (gamepadRafRef.current != null) return;
      gamepadRafRef.current = requestAnimationFrame(pump);
    };

    window.addEventListener("gamepadconnected", kick);
    window.addEventListener("gamepaddisconnected", kick);
    kick();

    return () => {
      window.removeEventListener("gamepadconnected", kick);
      window.removeEventListener("gamepaddisconnected", kick);
      if (gamepadRafRef.current != null) {
        cancelAnimationFrame(gamepadRafRef.current);
        gamepadRafRef.current = null;
      }
    };
  }, []);

  if (immersive) {
    return null;
  }

  return (
    <div
      className="joystick-hud-container"
      onContextMenu={(e) => e.preventDefault()}
    >
      <style>{`
        .joystick-hud-container {
          position: fixed;
          bottom: 0;
          left: 0;
          right: 0;
          width: 100%;
          height: 220px;
          display: flex;
          justify-content: space-between;
          align-items: self-end;
          padding: 0 5vw 20px 5vw;
          box-sizing: border-box;
          pointer-events: none;
          z-index: 9999;
          -webkit-user-select: none;
          user-select: none;
          -webkit-touch-callout: none;
          -webkit-tap-highlight-color: transparent;
          touch-action: none;
        }

        .joystick-hud-container * {
          -webkit-user-select: none;
          user-select: none;
          -webkit-touch-callout: none;
          -webkit-tap-highlight-color: transparent;
        }

        /* Fixed container size prevents shifting layout */
        .joystick-wrapper {
          position: relative;
          width: ${ZONE_SIZE_PX}px;
          height: ${ZONE_SIZE_PX}px;
          pointer-events: none;
          flex-shrink: 0;
        }

        .j-zone {
          position: absolute;
          top: 0;
          left: 0;
          width: 100%;
          height: 100%;
          background: rgba(0, 0, 0, 0.3);
          border: 1px solid ${NEUTRAL_BORDER};
          border-radius: 50%;
          pointer-events: auto;
          touch-action: none;
          box-shadow: inset 0 0 20px rgba(0, 0, 0, 0.4);
        }

        .j-label {
          position: absolute;
          top: -24px;
          left: 0;
          right: 0;
          text-align: center;
          font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
          font-size: 10px;
          letter-spacing: 0.12em;
          text-transform: uppercase;
          font-weight: 700;
          pointer-events: none;
          color: ${NEUTRAL_LABEL};
        }

        .reset-btn-sibling {
          position: absolute;
          /* Fixed offset outside the circle */
          top: -8px;
          left: -8px;
          width: ${RESET_BTN_SIZE}px;
          height: ${RESET_BTN_SIZE}px;
          border-radius: 20px;
          background: ${NEUTRAL_BTN};
          border: 1.5px solid #00f2ff;
          color: #00f2ff;
          font-size: 10px;
          font-weight: 800;
          display: flex;
          align-items: center;
          justify-content: center;
          cursor: pointer;
          pointer-events: auto;
          z-index: 10001; 
          box-shadow: 0 4px 12px rgba(0, 0, 0, 0.5), 0 0 10px rgba(0, 242, 255, 0.2);
          /* Transitioning only non-layout properties for stability */
          transition: transform 0.1s, background 0.15s, color 0.15s;
          user-select: none;
          -webkit-tap-highlight-color: transparent;
          touch-action: manipulation;
        }
        
        .reset-btn-sibling:active {
          transform: scale(0.9);
          background: #00f2ff;
          color: #000;
        }

        .sibling-btn-right {
          left: auto;
          right: -8px;
        }

        .drive-bottom-center {
          top: auto;
          bottom: -8px;
          left: 50%;
          transform: translateX(-50%);
        }
        .drive-bottom-center:active {
          transform: translateX(-50%) scale(0.9);
        }

        .drive-top-center {
          top: -8px;
          bottom: auto;
          left: 50%;
          transform: translateX(-50%);
        }
        .drive-top-center:active {
          transform: translateX(-50%) scale(0.9);
        }

        .gimbal-bottom-left {
          top: auto;
          bottom: -8px;
          left: -8px;
        }

        .gimbal-bottom-right {
          top: auto;
          bottom: -8px;
          left: auto;
          right: -8px;
        }
        .gimbal-bottom-center {
          top: auto;
          bottom: -8px;
          left: 50%;
          transform: translateX(-50%);
        }

        /* Schematic sits bottom-center between sticks (compact HUD layout). */
        .center-slot {
          flex: 1;
          display: flex;
          flex-direction: column;
          justify-content: flex-end;
          align-items: center;
          pointer-events: none;
          gap: 12px;
          min-height: 0;
        }
        .center-slot > * {
          pointer-events: auto;
        }
        .voice-ptt {
          width: 58px;
          height: 24px;
          border-radius: 20px;
          border: 1.5px solid #ff8a00;
          background: linear-gradient(135deg, rgba(255,138,0,0.22), rgba(255,62,116,0.22));
          color: #ffd180;
          font-size: 9px;
          font-weight: 800;
          cursor: pointer;
          user-select: none;
          -webkit-touch-callout: none;
          -webkit-tap-highlight-color: transparent;
          touch-action: none;
          box-shadow: 0 4px 12px rgba(0, 0, 0, 0.5), 0 0 12px rgba(255, 138, 0, 0.35);
        }
        .voice-ptt-below-gimbal {
          position: absolute;
          z-index: 10002;
        }
        .voice-ptt.listening {
          border-color: #22c55e;
          color: #b9ffc2;
          background: linear-gradient(135deg, rgba(34,197,94,0.36), rgba(0,242,255,0.24));
          box-shadow: 0 0 14px rgba(34, 197, 94, 0.55);
        }
        .voice-ptt:active {
          transform: translateX(-50%) scale(0.94);
        }

        .backup-on {
          border-color: #8b5cf6 !important;
          color: #f3e8ff !important;
          background: rgba(139, 92, 246, 0.8) !important;
        }
        .laser-on {
          border-color: #8b5cf6 !important;
          color: #f3e8ff !important;
          background: rgba(139, 92, 246, 0.8) !important;
        }
        .headlight-on {
          border-color: #8b5cf6 !important;
          color: #f3e8ff !important;
          background: rgba(139, 92, 246, 0.8) !important;
        }
      `}</style>

      {/* LEFT JOYSTICK: DRIVE */}
      <div className="joystick-wrapper">
        <div ref={leftZoneRef} className="j-zone">
          <div className="j-label">Drive</div>
        </div>

        {onTreat && (
          <button
            type="button"
            className="reset-btn-sibling drive-top-center"
            onClick={(e) => {
              e.preventDefault();
              e.stopPropagation();
              onTreat();
            }}
            style={{ borderRadius: "20px" }}
            onPointerDown={(e) => e.stopPropagation()}
            aria-label="Dispense treat"
            title="Treat (keyboard T · gamepad Y)"
          >
            TRT
          </button>
        )}

        <button
          type="button"
          className={`reset-btn-sibling drive-bottom-center${backupViewEnabled ? " backup-on" : ""}`}
          onClick={(e) => {
            e.preventDefault();
            e.stopPropagation();
            onToggleBackupView?.();
          }}
          style={{ borderRadius: "20px" }}
          onPointerDown={(e) => e.stopPropagation()}
          aria-label="Toggle backup camera view"
          title="Backup camera view"
        >
          BKP
        </button>
      </div>

      {/* HUD CENTER: (Schematics, Status, etc.) */}
      <div className="center-slot">
        {children}
      </div>

      {/* RIGHT JOYSTICK: GIMBAL + RST (left) + PRK (right) */}
      <div className="joystick-wrapper">
        <div ref={rightZoneRef} className="j-zone">
          <div className="j-label">Gimbal</div>
        </div>

        <button
          type="button"
          className="reset-btn-sibling"
          onClick={(e) => {
            e.preventDefault();
            e.stopPropagation();
            onReset?.();
          }}
          style={{ borderRadius: "20px" }}
          onPointerDown={(e) => e.stopPropagation()}
          aria-label="Center camera"
        >
          RST
        </button>

        <button
          type="button"
          className="reset-btn-sibling sibling-btn-right"
          onClick={(e) => {
            e.preventDefault();
            e.stopPropagation();
            onLookDown?.();
          }}
          style={{ borderRadius: "20px" }}
          onPointerDown={(e) => e.stopPropagation()}
          aria-label="Park camera (downward)"
          title="PRK (park mode: look down)"
        >
          PRK
        </button>

        {onLaserToggle && (
          <button
            type="button"
            className={`reset-btn-sibling gimbal-bottom-left${laserOn ? " laser-on" : ""}`}
            onClick={(e) => {
              e.preventDefault();
              e.stopPropagation();
              onLaserToggle();
            }}
            style={{ borderRadius: "20px" }}
            onPointerDown={(e) => e.stopPropagation()}
            aria-label={laserOn ? "Laser on" : "Laser off"}
            title="Laser (KY-008 on GPIO17)"
          >
            LZR
          </button>
        )}

        {onHeadlightToggle && (
          <button
            type="button"
            className={`reset-btn-sibling gimbal-bottom-right${headlightOn ? " headlight-on" : ""}`}
            onClick={(e) => {
              e.preventDefault();
              e.stopPropagation();
              onHeadlightToggle();
            }}
            style={{ borderRadius: "20px" }}
            onPointerDown={(e) => e.stopPropagation()}
            aria-label={headlightOn ? "Headlight on" : "Headlight off"}
            title="Headlight"
          >
            HL
          </button>
        )}
      </div>
    </div>
  );
};

DualJoystickControls.propTypes = {
  onDrive: PropTypes.func.isRequired,
  onReset: PropTypes.func,
  onLookDown: PropTypes.func,
  onLaserToggle: PropTypes.func,
  laserOn: PropTypes.bool,
  onHeadlightToggle: PropTypes.func,
  headlightOn: PropTypes.bool,
  onVoiceStart: PropTypes.func,
  onVoiceStop: PropTypes.func,
  voiceSupported: PropTypes.bool,
  voiceListening: PropTypes.bool,
  onToggleBackupView: PropTypes.func,
  backupViewEnabled: PropTypes.bool,
  onTreat: PropTypes.func,
  immersive: PropTypes.bool,
  children: PropTypes.node,
};