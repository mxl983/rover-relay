import { useCallback, useEffect, useRef, useState } from "react";
import {
  anyGamepadPhysicalInput,
  listConnectedGamepads,
  releasePointerLockIfHeld,
  wakeGamepadInput,
} from "../utils/gamepadInput.js";
import { isSteamOS } from "../utils/platform.js";

/** TEMP: show activation overlay on every platform for layout debugging. */
const FORCE_ACTIVATION_OVERLAY = true;

/**
 * SteamOS / Chrome gate: Gamepad API stays empty until the tab is focused and
 * the user presses a controller button (or taps to request pointer lock).
 *
 * @param {boolean} enabled
 */
export function useGamepadActivation(enabled) {
  const needsGate = enabled && (FORCE_ACTIVATION_OVERLAY || isSteamOS());
  const [ready, setReady] = useState(() => !needsGate);
  const readyRef = useRef(ready);
  const rafRef = useRef(null);

  useEffect(() => {
    readyRef.current = ready;
  }, [ready]);

  const markReady = useCallback(() => {
    readyRef.current = true;
    setReady(true);
    releasePointerLockIfHeld();
    if (rafRef.current != null) {
      cancelAnimationFrame(rafRef.current);
      rafRef.current = null;
    }
  }, []);

  const activate = useCallback(() => {
    wakeGamepadInput(document.body);
  }, []);

  useEffect(() => {
    if (!needsGate) {
      readyRef.current = true;
      setReady(true);
      return undefined;
    }

    readyRef.current = false;
    setReady(false);

    const poll = () => {
      if (anyGamepadPhysicalInput()) {
        markReady();
        return;
      }
      rafRef.current = requestAnimationFrame(poll);
    };

    const kick = () => {
      if (readyRef.current) return;
      if (rafRef.current != null) return;
      rafRef.current = requestAnimationFrame(poll);
    };

    const onFocus = () => {
      window.focus();
      kick();
    };

    const onVisibility = () => {
      if (document.visibilityState !== "visible") return;
      window.focus();
      if (listConnectedGamepads().length === 0) {
        readyRef.current = false;
        setReady(false);
      }
      kick();
    };

    window.addEventListener("focus", onFocus);
    window.addEventListener("gamepadconnected", kick);
    window.addEventListener("pointerdown", kick);
    window.addEventListener("keydown", kick);
    document.addEventListener("visibilitychange", onVisibility);
    kick();

    return () => {
      window.removeEventListener("focus", onFocus);
      window.removeEventListener("gamepadconnected", kick);
      window.removeEventListener("pointerdown", kick);
      window.removeEventListener("keydown", kick);
      document.removeEventListener("visibilitychange", onVisibility);
      if (rafRef.current != null) {
        cancelAnimationFrame(rafRef.current);
        rafRef.current = null;
      }
    };
  }, [needsGate, markReady]);

  return {
    ready,
    needsActivation: needsGate && !ready,
    activate,
    markReady,
  };
}
