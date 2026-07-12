import React, { useEffect, useRef, useState } from "react";
import PropTypes from "prop-types";
import { readActiveGamepadState } from "../utils/gamepadInput.js";

/** Stick-as-mouse → drive (Legion Go desktop / FPS mode with pointer lock). */
const DRIVE_SENSITIVITY = 0.065;
const DRIVE_CLAMP = 1;
const MOVE_EPS_PX = 0.8;
/** Stop shortly after stick recenters (no more relative mouse deltas). */
const STOP_DELAY_MS = 80;
const GAMEPAD_STICK_ACTIVE = 0.12;

function clamp1(v) {
  return Math.max(-DRIVE_CLAMP, Math.min(DRIVE_CLAMP, v));
}

function gamepadSticksActive() {
  const active = readActiveGamepadState();
  if (!active?.sticks) return false;
  const { lx, ly, rx, ry } = active.sticks;
  return (
    Math.hypot(lx, ly) >= GAMEPAD_STICK_ACTIVE ||
    Math.hypot(rx, ry) >= GAMEPAD_STICK_ACTIVE
  );
}

function isUiTarget(target) {
  if (!target || typeof target.closest !== "function") return false;
  return Boolean(
    target.closest(
      ".hud-overlay, .joystick-hud-container, .handheld-stick-mouse-hint, .handheld-stick-mouse-cta, button, a, input, select, textarea, [role='menu'], [data-radix-menu-content]",
    ),
  );
}

/**
 * Legion Go desktop/FPS mode turns the left stick into a mouse.
 * Relative mouse only pulses while the cursor is moving — holding the stick
 * stops generating moves once the cursor hits the screen edge.
 *
 * Pointer lock fixes that: the OS keeps sending relative deltas while the stick
 * is held, so left-stick drive works continuously.
 */
export function HandheldStickMouseLayer({
  enabled,
  viewportRef,
  onDrive,
  lastGimbalRef,
}) {
  const [locked, setLocked] = useState(false);
  const onDriveRef = useRef(onDrive);
  const lastGimbal = useRef(lastGimbalRef);
  const stopTimerRef = useRef(null);
  const drivingRef = useRef(false);
  const driveVecRef = useRef({ x: 0, y: 0 });

  useEffect(() => {
    onDriveRef.current = onDrive;
  }, [onDrive]);

  useEffect(() => {
    lastGimbal.current = lastGimbalRef;
  }, [lastGimbalRef]);

  useEffect(() => {
    if (!enabled || !viewportRef?.current) return undefined;
    const root = viewportRef.current;
    const lockTarget = () => root.querySelector("video") || root;

    const requestLock = () => {
      const el = lockTarget();
      const req = el.requestPointerLock || el.webkitRequestPointerLock;
      if (!req) return;
      Promise.resolve(req.call(el)).catch(() => {});
    };

    const onPointerUp = (e) => {
      if (e.button !== 0) return;
      if (isUiTarget(e.target)) return;
      const el = lockTarget();
      if (document.pointerLockElement === el || document.webkitPointerLockElement === el) {
        return;
      }
      requestLock();
    };

    const onLockChange = () => {
      const el = lockTarget();
      const isLocked =
        document.pointerLockElement === el ||
        document.webkitPointerLockElement === el ||
        document.pointerLockElement === root ||
        document.webkitPointerLockElement === root;
      setLocked(isLocked);
      if (!isLocked) {
        drivingRef.current = false;
        driveVecRef.current = { x: 0, y: 0 };
        onDriveRef.current?.({
          drive: { x: 0, y: 0 },
          gimbal: lastGimbal.current?.current ?? { x: 0, y: 0 },
        });
      }
    };

    document.addEventListener("pointerlockchange", onLockChange);
    document.addEventListener("webkitpointerlockchange", onLockChange);
    root.addEventListener("pointerup", onPointerUp);

    return () => {
      document.removeEventListener("pointerlockchange", onLockChange);
      document.removeEventListener("webkitpointerlockchange", onLockChange);
      root.removeEventListener("pointerup", onPointerUp);
    };
  }, [enabled, viewportRef]);

  useEffect(() => {
    if (!enabled || !locked) return undefined;

    const sendDrive = (x, y) => {
      driveVecRef.current = { x, y };
      drivingRef.current = x !== 0 || y !== 0;
      onDriveRef.current?.({
        drive: { x, y },
        gimbal: lastGimbal.current?.current ?? { x: 0, y: 0 },
      });
    };

    const stopDrive = () => {
      if (!drivingRef.current && driveVecRef.current.x === 0 && driveVecRef.current.y === 0) {
        return;
      }
      sendDrive(0, 0);
    };

    // Hold last non-zero drive vector while stick is held (continuous relative deltas
    // arrive while locked). EMA softens Legion stick mouse acceleration spikes.
    let filteredX = 0;
    let filteredY = 0;

    const onMove = (e) => {
      if (gamepadSticksActive()) {
        stopDrive();
        return;
      }

      const dx = e.movementX ?? e.mozMovementX ?? 0;
      const dy = e.movementY ?? e.mozMovementY ?? 0;
      if (Math.hypot(dx, dy) < MOVE_EPS_PX) return;

      // Stick forward (screen up / negative movementY) → rover forward (negative y).
      const rawX = clamp1(dx * DRIVE_SENSITIVITY);
      const rawY = clamp1(dy * DRIVE_SENSITIVITY);
      filteredX = filteredX * 0.35 + rawX * 0.65;
      filteredY = filteredY * 0.35 + rawY * 0.65;
      const x = clamp1(filteredX);
      const y = clamp1(filteredY);
      sendDrive(x, y);

      clearTimeout(stopTimerRef.current);
      stopTimerRef.current = setTimeout(() => {
        filteredX = 0;
        filteredY = 0;
        stopDrive();
        stopTimerRef.current = null;
      }, STOP_DELAY_MS);
    };

    const onBlur = () => {
      clearTimeout(stopTimerRef.current);
      filteredX = 0;
      filteredY = 0;
      stopDrive();
    };

    document.addEventListener("pointermove", onMove, { passive: true });
    window.addEventListener("blur", onBlur);
    document.addEventListener("visibilitychange", onBlur);

    return () => {
      document.removeEventListener("pointermove", onMove);
      window.removeEventListener("blur", onBlur);
      document.removeEventListener("visibilitychange", onBlur);
      clearTimeout(stopTimerRef.current);
      stopDrive();
    };
  }, [enabled, locked]);

  if (!enabled) return null;

  return (
    <div className="handheld-stick-mouse-layer" aria-live="polite">
      {!locked ? (
        <div className="handheld-stick-mouse-cta glass-card">
          <strong>Enable left stick drive</strong>
          <span>
            Tap the video once, then push the left stick to drive. (Your sticks are
            in mouse mode — for native gamepad, switch to XInput in Legion Space:
            Legion + RB.)
          </span>
        </div>
      ) : (
        <div className="handheld-stick-mouse-locked" role="status">
          Left stick → drive · Esc to release
        </div>
      )}
    </div>
  );
}

HandheldStickMouseLayer.propTypes = {
  enabled: PropTypes.bool,
  viewportRef: PropTypes.object,
  onDrive: PropTypes.func,
  lastGimbalRef: PropTypes.object,
};
