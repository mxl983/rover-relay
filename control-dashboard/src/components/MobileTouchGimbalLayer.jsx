import { useCallback, useRef } from "react";
import PropTypes from "prop-types";

const TOUCH_SENSITIVITY = 0.11;
const GIMBAL_CLAMP = 5.0;
const TOUCH_STOP_DELAY_MS = 28;
const TOP_EXCLUDE_PX_PORTRAIT = 72;
const BOTTOM_EXCLUDE_PX_PORTRAIT = 170;
const TOP_EXCLUDE_PX_LANDSCAPE = 48;
const BOTTOM_EXCLUDE_PX_LANDSCAPE = 104;

export function MobileTouchGimbalLayer({ onGimbal }) {
  const activePointerIdRef = useRef(null);
  const activeTouchIdRef = useRef(null);
  const lastPosRef = useRef({ x: 0, y: 0 });
  const stopTimerRef = useRef(null);
  const rafRef = useRef(null);
  const pendingRef = useRef({ gx: 0, gy: 0, dirty: false });

  const getExcludeBounds = () => {
    const w = window.innerWidth || 0;
    const h = window.innerHeight || 0;
    const landscape = w > h;
    return landscape
      ? { top: TOP_EXCLUDE_PX_LANDSCAPE, bottom: BOTTOM_EXCLUDE_PX_LANDSCAPE }
      : { top: TOP_EXCLUDE_PX_PORTRAIT, bottom: BOTTOM_EXCLUDE_PX_PORTRAIT };
  };

  const isInCaptureZone = (y) => {
    const h = window.innerHeight || 0;
    const { top, bottom } = getExcludeBounds();
    return y >= top && y <= h - bottom;
  };

  const clearStopTimer = () => {
    if (stopTimerRef.current) {
      clearTimeout(stopTimerRef.current);
      stopTimerRef.current = null;
    }
  };

  const sendGimbal = useCallback(
    (gx, gy) => {
      // Send gimbal-only payload so current drive state is not overwritten.
      onGimbal({ x: gx, y: gy });
    },
    [onGimbal],
  );

  const stopGimbalSoon = useCallback(() => {
    clearStopTimer();
    stopTimerRef.current = setTimeout(() => {
      sendGimbal(0, 0);
      stopTimerRef.current = null;
    }, TOUCH_STOP_DELAY_MS);
  }, [sendGimbal]);

  const flushPending = useCallback(() => {
    rafRef.current = null;
    const p = pendingRef.current;
    if (!p.dirty) return;
    p.dirty = false;
    sendGimbal(p.gx, p.gy);
    stopGimbalSoon();
  }, [sendGimbal, stopGimbalSoon]);

  const scheduleFlush = useCallback(() => {
    if (rafRef.current != null) return;
    rafRef.current = requestAnimationFrame(flushPending);
  }, [flushPending]);

  const onPointerDown = (e) => {
    if (activePointerIdRef.current !== null) return;
    const y = e.clientY ?? 0;
    if (!isInCaptureZone(y)) return;

    activePointerIdRef.current = e.pointerId;
    lastPosRef.current = { x: e.clientX ?? 0, y: e.clientY ?? 0 };
    e.currentTarget.setPointerCapture?.(e.pointerId);
    e.preventDefault();
  };

  const onPointerMove = (e) => {
    if (activePointerIdRef.current !== e.pointerId) return;

    const prev = lastPosRef.current;
    const coalesced = e.nativeEvent?.getCoalescedEvents?.();
    const latest = coalesced?.length ? coalesced[coalesced.length - 1] : e;
    const x = latest.clientX ?? prev.x;
    const y = latest.clientY ?? prev.y;
    const dx = x - prev.x;
    const dy = y - prev.y;
    lastPosRef.current = { x, y };

    const gx = Math.max(-GIMBAL_CLAMP, Math.min(GIMBAL_CLAMP, dx * TOUCH_SENSITIVITY));
    const gy = Math.max(-GIMBAL_CLAMP, Math.min(GIMBAL_CLAMP, dy * TOUCH_SENSITIVITY));
    if (gx !== 0 || gy !== 0) {
      pendingRef.current.gx = gx;
      pendingRef.current.gy = gy;
      pendingRef.current.dirty = true;
      scheduleFlush();
    }
    e.preventDefault();
  };

  const endPointer = (e) => {
    if (activePointerIdRef.current !== e.pointerId) return;
    activePointerIdRef.current = null;
    clearStopTimer();
    if (rafRef.current != null) {
      cancelAnimationFrame(rafRef.current);
      rafRef.current = null;
    }
    pendingRef.current.dirty = false;
    sendGimbal(0, 0);
    e.currentTarget.releasePointerCapture?.(e.pointerId);
    e.preventDefault();
  };

  // Fallback path for browsers/devices that deliver touch events more reliably than pointer events.
  const onTouchStart = (e) => {
    if (activeTouchIdRef.current !== null) return;
    const t = e.changedTouches?.[0];
    if (!t) return;
    if (!isInCaptureZone(t.clientY ?? 0)) return;
    activeTouchIdRef.current = t.identifier;
    lastPosRef.current = { x: t.clientX ?? 0, y: t.clientY ?? 0 };
    if (e.cancelable) e.preventDefault();
  };

  const onTouchMove = (e) => {
    const id = activeTouchIdRef.current;
    if (id == null) return;
    const touch = Array.from(e.changedTouches || []).find((t) => t.identifier === id);
    if (!touch) return;

    const prev = lastPosRef.current;
    const x = touch.clientX ?? prev.x;
    const y = touch.clientY ?? prev.y;
    const dx = x - prev.x;
    const dy = y - prev.y;
    lastPosRef.current = { x, y };

    const gx = Math.max(-GIMBAL_CLAMP, Math.min(GIMBAL_CLAMP, dx * TOUCH_SENSITIVITY));
    const gy = Math.max(-GIMBAL_CLAMP, Math.min(GIMBAL_CLAMP, dy * TOUCH_SENSITIVITY));
    if (gx !== 0 || gy !== 0) {
      pendingRef.current.gx = gx;
      pendingRef.current.gy = gy;
      pendingRef.current.dirty = true;
      scheduleFlush();
    }
    if (e.cancelable) e.preventDefault();
  };

  const onTouchEnd = (e) => {
    const id = activeTouchIdRef.current;
    if (id == null) return;
    const ended = Array.from(e.changedTouches || []).some((t) => t.identifier === id);
    if (!ended) return;
    activeTouchIdRef.current = null;
    clearStopTimer();
    if (rafRef.current != null) {
      cancelAnimationFrame(rafRef.current);
      rafRef.current = null;
    }
    pendingRef.current.dirty = false;
    sendGimbal(0, 0);
    if (e.cancelable) e.preventDefault();
  };

  return (
    <div
      className="mobile-touch-gimbal-layer"
      onPointerDown={onPointerDown}
      onPointerMove={onPointerMove}
      onPointerUp={endPointer}
      onPointerCancel={endPointer}
      onTouchStart={onTouchStart}
      onTouchMove={onTouchMove}
      onTouchEnd={onTouchEnd}
      onTouchCancel={onTouchEnd}
      onContextMenu={(e) => e.preventDefault()}
      aria-hidden="true"
    />
  );
}

MobileTouchGimbalLayer.propTypes = {
  onGimbal: PropTypes.func.isRequired,
};

