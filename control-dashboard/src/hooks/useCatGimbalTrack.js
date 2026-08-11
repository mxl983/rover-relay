import { useEffect, useRef } from "react";
import {
  bboxMovedEnough,
  computeCatGimbalCommand,
  getCatOffset,
  gimbalCommandsEqual,
  isGimbalActive,
  nextCenteredSettled,
  smoothGimbalCommand,
  CAT_GIMBAL_PULSE_MS,
} from "../utils/catGimbalTrack.js";

/**
 * Re-send while *actively* slewing so the Pi gimbal watchdog does not time out.
 * Idle/settled tracking does not keepalive.
 */
export const CAT_GIMBAL_KEEPALIVE_MS = 180;

/**
 * While cat vision is on, steer gimbal velocity toward the detected cat.
 * Stops (settled) once the cat is near frame center; resumes only if it
 * drifts outside the hysteresis band.
 *
 * Important: vision WS pushes ~15 Hz even when the box is stable. We only
 * start a new slew pulse when the bbox center moves enough, and we skip
 * duplicate gimbal sends.
 */
export function useCatGimbalTrack({
  enabled,
  cat,
  gimbalRef,
  onGimbal,
  isPaused,
}) {
  const catRef = useRef(cat);
  const onGimbalRef = useRef(onGimbal);
  const isPausedRef = useRef(isPaused);
  const wasActiveRef = useRef(false);
  const smoothedRef = useRef({ x: 0, y: 0 });
  const lastSentRef = useRef({ x: 0, y: 0 });
  const settledRef = useRef(false);
  const lastCenterRef = useRef(null);
  const lastPulseAtRef = useRef(0);

  catRef.current = cat;
  onGimbalRef.current = onGimbal;
  isPausedRef.current = isPaused;

  const publish = (out, { force = false } = {}) => {
    if (gimbalRef) gimbalRef.current = out;
    const active = isGimbalActive(out);
    const changed = !gimbalCommandsEqual(out, lastSentRef.current);
    // Send when command changed, or keepalive while still slewing.
    // After settling to zero, send one stop then stay quiet.
    if (!force && !changed && !(active && wasActiveRef.current)) {
      wasActiveRef.current = active;
      return;
    }
    if (!changed && !active && !wasActiveRef.current) {
      return;
    }
    lastSentRef.current = { x: out.x, y: out.y };
    onGimbalRef.current?.(out);
    wasActiveRef.current = active;
  };

  const step = (current, { fromVision = false } = {}) => {
    if (isPausedRef.current?.()) {
      settledRef.current = false;
      smoothedRef.current = { x: 0, y: 0 };
      lastCenterRef.current = null;
      publish({ x: 0, y: 0 }, { force: true });
      return;
    }

    const offset = getCatOffset(current);
    const now = Date.now();

    if (!offset) {
      settledRef.current = false;
      lastCenterRef.current = null;
      const cmd = smoothGimbalCommand(smoothedRef.current, { x: 0, y: 0 });
      smoothedRef.current = isGimbalActive(cmd, 0.004) ? cmd : { x: 0, y: 0 };
      publish(smoothedRef.current);
      return;
    }

    const center = { cx: offset.cx, cy: offset.cy };
    if (fromVision && bboxMovedEnough(lastCenterRef.current, center)) {
      lastCenterRef.current = center;
      lastPulseAtRef.current = now;
    } else if (!lastCenterRef.current) {
      lastCenterRef.current = center;
      lastPulseAtRef.current = now;
    }

    settledRef.current = nextCenteredSettled(
      settledRef.current,
      offset.radius,
    );

    let raw = { x: 0, y: 0 };
    const age = now - lastPulseAtRef.current;
    const inPulse = age <= CAT_GIMBAL_PULSE_MS;

    if (!settledRef.current && inPulse) {
      raw = computeCatGimbalCommand(current, { settled: false });
    }

    const cmd = smoothGimbalCommand(smoothedRef.current, raw);
    if (!isGimbalActive(raw) && !isGimbalActive(cmd, 0.004)) {
      smoothedRef.current = { x: 0, y: 0 };
    } else {
      smoothedRef.current = cmd;
    }
    publish(smoothedRef.current);
  };

  useEffect(() => {
    if (!enabled) {
      smoothedRef.current = { x: 0, y: 0 };
      settledRef.current = false;
      lastCenterRef.current = null;
      lastPulseAtRef.current = 0;
      if (gimbalRef) gimbalRef.current = { x: 0, y: 0 };
      if (wasActiveRef.current || !gimbalCommandsEqual(lastSentRef.current, { x: 0, y: 0 })) {
        lastSentRef.current = { x: 0, y: 0 };
        wasActiveRef.current = false;
        onGimbalRef.current?.({ x: 0, y: 0 });
      }
      return undefined;
    }

    const tick = () => step(catRef.current, { fromVision: false });
    tick();
    const id = setInterval(tick, CAT_GIMBAL_KEEPALIVE_MS);
    return () => {
      clearInterval(id);
      smoothedRef.current = { x: 0, y: 0 };
      settledRef.current = false;
      if (gimbalRef) gimbalRef.current = { x: 0, y: 0 };
      if (wasActiveRef.current || !gimbalCommandsEqual(lastSentRef.current, { x: 0, y: 0 })) {
        lastSentRef.current = { x: 0, y: 0 };
        wasActiveRef.current = false;
        onGimbalRef.current?.({ x: 0, y: 0 });
      }
    };
  }, [enabled, gimbalRef]);

  useEffect(() => {
    if (!enabled) return;
    step(cat, { fromVision: true });
  }, [enabled, cat, gimbalRef]);
}
