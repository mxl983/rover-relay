import React, { useState, useEffect, useCallback, useRef } from "react";

const CONTROL_CONFIG = [
  { key: "q", label: "Q", grid: 1, hint: "←" },
  { key: "w", label: "W", grid: 2 },
  { key: "e", label: "E", grid: 3, hint: "→" },
  { key: "a", label: "A", grid: 5 },
  { key: "s", label: "S", grid: 6 },
  { key: "d", label: "D", grid: 7 },
  { key: "c", label: "📸", grid: 9, type: "action", hint: "C" },
  { key: "arrowup", label: "▲", grid: 10, python: "ArrowUp", hint: "UP" },
  { key: "r", label: "⟲", grid: 11, type: "action", hint: "RST" },
  { key: "arrowleft", label: "◀", grid: 13, python: "ArrowLeft", hint: "L" },
  { key: "arrowdown", label: "▼", grid: 14, python: "ArrowDown", hint: "DN" },
  { key: "arrowright", label: "▶", grid: 15, python: "ArrowRight", hint: "R" },
];

const KEYBOARD_DRIVE_CONFIG = CONTROL_CONFIG.filter((config) => config.type !== "action");

/** Normalize the persistent Pi keyboard payload without changing direction semantics. */
export function keyboardDrivePayload(keys) {
  return [...new Set(Array.from(keys || [], (key) => String(key).toLowerCase()))]
    .filter((key) => KEYBOARD_DRIVE_CONFIG.some((config) => config.key === key))
    .map((key) => KEYBOARD_DRIVE_CONFIG.find((config) => config.key === key)?.python || key)
    .sort();
}

export const KeyboardControlCluster = ({
  onDrive,
  onCapture,
  onReset,
  isCapturing: _isCapturing,
}) => {
  const [activeKeys, setActiveKeys] = useState(new Set());
  const prevKeysRef = useRef("");
  const activeKeysRef = useRef(activeKeys);
  const onDriveRef = useRef(onDrive);

  useEffect(() => {
    onDriveRef.current = onDrive;
  }, [onDrive]);

  useEffect(() => {
    activeKeysRef.current = activeKeys;
  }, [activeKeys]);

  const publishKeys = useCallback((keys) => {
    const activeList = keyboardDrivePayload(keys);
    const keysString = activeList.join("");
    onDriveRef.current(activeList);
    prevKeysRef.current = keysString;
  }, []);

  const updateAction = useCallback(
    (key, isDown) => {
      const conf = CONTROL_CONFIG.find((c) => c.key === key);
      if (!conf) return;

      // Handle Action Buttons (Toggles/Captures)
      if (conf.type === "action") {
        if (!isDown) return;
        if (key === "c") onCapture();
        if (key === "r") onReset();
        return;
      }

      // Handle Drive / Gimbal Keys
      setActiveKeys((prev) => {
        const next = new Set(prev);
        isDown ? next.add(key) : next.delete(key);

        const activeList = keyboardDrivePayload(next);
        const keysString = activeList.join("");
        if (keysString !== prevKeysRef.current) {
          onDriveRef.current(activeList);
          prevKeysRef.current = keysString;
        }
        return next;
      });
    },
    [onCapture, onReset],
  );

  const updateActionRef = useRef(updateAction);
  useEffect(() => {
    updateActionRef.current = updateAction;
  }, [updateAction]);

  // MentorPi cmd_vel / gimbal expire quickly — re-send while keys stay held.
  useEffect(() => {
    if (activeKeys.size === 0) return undefined;
    const id = setInterval(() => {
      const held = activeKeysRef.current;
      if (!held || held.size === 0) return;
      publishKeys(held);
    }, 200);
    return () => clearInterval(id);
  }, [activeKeys, publishKeys]);

  useEffect(() => {
    const handleKeyEvent = (e) => {
      if (e.repeat) return;
      const key = e.key.toLowerCase();
      if (key.startsWith("arrow")) e.preventDefault();
      updateActionRef.current(key, e.type === "keydown");
    };

    // Drive commands persist on the Pi, so every focus/page transition must
    // explicitly publish the empty keyboard payload.
    const handleSafetyStop = () => {
      setActiveKeys(new Set());
      onDriveRef.current([]);
      prevKeysRef.current = "";
    };
    const onVisibility = () => {
      if (document.hidden || document.visibilityState !== "visible") {
        handleSafetyStop();
      }
    };

    window.addEventListener("keydown", handleKeyEvent);
    window.addEventListener("keyup", handleKeyEvent);
    window.addEventListener("blur", handleSafetyStop);
    window.addEventListener("pagehide", handleSafetyStop);
    document.addEventListener("visibilitychange", onVisibility);

    return () => {
      window.removeEventListener("keydown", handleKeyEvent);
      window.removeEventListener("keyup", handleKeyEvent);
      window.removeEventListener("blur", handleSafetyStop);
      window.removeEventListener("pagehide", handleSafetyStop);
      document.removeEventListener("visibilitychange", onVisibility);
      handleSafetyStop();
    };
  }, []);

  return (
    <div className="wasd-controls">
      <style>{`
        .wasd-controls { 
          display: grid; 
          grid-template-columns: repeat(4, 46px); 
          gap: 6px; 
          background: rgba(0,0,0,0.4); 
          padding: 12px;
          user-select: none;
          touch-action: none; /* Prevents page scrolling/zooming while driving */
        }
        .btn { 
          width: 46px; 
          height: 46px; 
          background: rgba(0,0,0,0.8); 
          color: #ffffff; 
          border: 1px solid #ffffff; 
          cursor: pointer; 
          display: flex; 
          flex-direction: column; 
          align-items: center; 
          justify-content: center; 
          font-size: 16px; 
          font-weight: bold;
          outline: none;
          /* Critical Mobile Fixes: */
          -webkit-tap-highlight-color: transparent;
          -webkit-touch-callout: none;
          touch-action: none; 
          -webkit-tap-highlight-color: rgba(0,0,0,0); 
          -webkit-touch-callout: none; 
          -webkit-user-select: none; 
          user-select: none;
          touch-action: none;
        }
        .active { background: #ffffff !important; color: #000 !important; }
        .light-on { background: #ffea00 !important; color: #000; border-color: #ffea00; }
        .laser-on { background: #ff4444 !important; color: #000; border-color: #ff4444; }
        .hint { font-size: 8px; opacity: 0.5; margin-top: 1px; pointer-events: none; pointer-events: none; -webkit-user-select: none;}
      `}</style>

      {Array.from({ length: 16 }).map((_, i) => {
        const conf = CONTROL_CONFIG.find((c) => c.grid === i + 1);
        if (!conf) return <div key={i} />;

        return (
          <button
            key={i}
            className={`btn ${activeKeys.has(conf.key) ? "active" : ""}`}
            // Mouse Handlers
            onMouseDown={() => updateAction(conf.key, true)}
            onMouseUp={() => updateAction(conf.key, false)}
            onMouseLeave={() => updateAction(conf.key, false)} // Safety: stop if mouse slips off
            // Touch Handlers (Snappy for Tablet/Phone)
            onTouchStart={(e) => {
              e.preventDefault(); // Stop ghost clicks and scroll
              updateAction(conf.key, true);
            }}
            onTouchEnd={(e) => {
              e.preventDefault();
              updateAction(conf.key, false);
            }}
            onTouchCancel={(e) => {
              e.preventDefault();
              updateAction(conf.key, false);
            }}
          >
            {conf.label}
            {conf.hint && <span className="hint">{conf.hint}</span>}
          </button>
        );
      })}
    </div>
  );
};
