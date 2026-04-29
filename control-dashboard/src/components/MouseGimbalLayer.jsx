import React, { useEffect, useRef, useCallback } from "react";
import PropTypes from "prop-types";

const MOUSE_SENSITIVITY = 0.035;
const GIMBAL_CLAMP = 3.5;
/** After this many ms without pointer move, send gimbal 0,0 so the cam stops */
const MOUSE_STOP_DELAY_MS = 35;

export const MouseGimbalLayer = ({
  viewportRef,
  isFullscreen,
  isPointerLocked,
  onPointerLockChange,
  onDrive,
  lastDriveRef,
}) => {
  const sendGimbal = useCallback(
    (gx, gy) => {
      const drive = lastDriveRef?.current ?? { x: 0, y: 0 };
      onDrive({ drive, gimbal: { x: gx, y: gy } });
    },
    [onDrive, lastDriveRef],
  );

  useEffect(() => {
    if (!isFullscreen || !viewportRef?.current) return;

    const el = viewportRef.current;

    const requestLock = () => {
      const req = el.requestPointerLock || el.webkitRequestPointerLock;
      if (req) req.call(el).catch(() => {});
    };

    const handleClick = () => {
      if (document.pointerLockElement === el || document.webkitPointerLockElement === el) return;
      requestLock();
    };

    const handlePointerLockChange = () => {
      const locked =
        document.pointerLockElement === el || document.webkitPointerLockElement === el;
      onPointerLockChange?.(locked);
      if (!locked) sendGimbal(0, 0);
    };

    document.addEventListener("pointerlockchange", handlePointerLockChange);
    document.addEventListener("webkitpointerlockchange", handlePointerLockChange);
    el.addEventListener("click", handleClick);

    return () => {
      document.removeEventListener("pointerlockchange", handlePointerLockChange);
      document.removeEventListener("webkitpointerlockchange", handlePointerLockChange);
      el.removeEventListener("click", handleClick);
    };
  }, [isFullscreen, viewportRef, onPointerLockChange, sendGimbal]);

  useEffect(() => {
    if (!isPointerLocked) return;

    let stopTimer = null;

    const onMove = (e) => {
      const dx = e.movementX ?? e.mozMovementX ?? 0;
      const dy = e.movementY ?? e.mozMovementY ?? 0;
      const gx = Math.max(-GIMBAL_CLAMP, Math.min(GIMBAL_CLAMP, dx * MOUSE_SENSITIVITY));
      const gy = Math.max(-GIMBAL_CLAMP, Math.min(GIMBAL_CLAMP, dy * MOUSE_SENSITIVITY));
      if (gx !== 0 || gy !== 0) sendGimbal(gx, gy);

      clearTimeout(stopTimer);
      stopTimer = setTimeout(() => {
        sendGimbal(0, 0);
        stopTimer = null;
      }, MOUSE_STOP_DELAY_MS);
    };

    document.addEventListener("pointermove", onMove, { passive: true });
    return () => {
      document.removeEventListener("pointermove", onMove);
      if (stopTimer) clearTimeout(stopTimer);
    };
  }, [isPointerLocked, sendGimbal]);

  if (!isFullscreen) return null;

  return (
    <>
      {!isPointerLocked && (
        <div
          style={{
            position: "absolute",
            inset: 0,
            zIndex: 10,
            display: "flex",
            alignItems: "center",
            justifyContent: "center",
            pointerEvents: "none",
          }}
        >
          <span
            style={{
              padding: "8px 14px",
              background: "rgba(0,0,0,0.7)",
              color: "#00f2ff",
              fontSize: 13,
              borderRadius: 6,
              border: "1px solid rgba(0,242,255,0.4)",
            }}
          >
            Click to enable mouse look (FPS-style gimbal)
          </span>
        </div>
      )}
    </>
  );
};

MouseGimbalLayer.propTypes = {
  viewportRef: PropTypes.object,
  isFullscreen: PropTypes.bool.isRequired,
  isPointerLocked: PropTypes.bool.isRequired,
  onPointerLockChange: PropTypes.func,
  onDrive: PropTypes.func.isRequired,
  lastDriveRef: PropTypes.object,
};
