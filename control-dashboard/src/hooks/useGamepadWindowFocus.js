import { useCallback, useEffect, useState } from "react";

/**
 * Keep the dashboard focused so Chromium exposes controller input (SteamOS / Chrome
 * gate the Gamepad API until the tab has focus).
 *
 * @param {boolean} enabled
 */
export function useGamepadWindowFocus(enabled) {
  const [needsFocus, setNeedsFocus] = useState(false);

  const syncFocusState = useCallback(() => {
    if (typeof document === "undefined") return;
    setNeedsFocus(!document.hasFocus());
  }, []);

  const claimFocus = useCallback(() => {
    if (typeof window !== "undefined") {
      window.focus();
    }
    syncFocusState();
  }, [syncFocusState]);

  useEffect(() => {
    if (!enabled) {
      setNeedsFocus(false);
      return undefined;
    }

    const onVisibility = () => {
      if (document.visibilityState === "visible") {
        claimFocus();
      } else {
        syncFocusState();
      }
    };

    window.addEventListener("focus", syncFocusState);
    window.addEventListener("blur", syncFocusState);
    document.addEventListener("visibilitychange", onVisibility);
    window.addEventListener("pointerdown", claimFocus, { capture: true, passive: true });
    window.addEventListener("keydown", claimFocus, { capture: true, passive: true });

    claimFocus();

    return () => {
      window.removeEventListener("focus", syncFocusState);
      window.removeEventListener("blur", syncFocusState);
      document.removeEventListener("visibilitychange", onVisibility);
      window.removeEventListener("pointerdown", claimFocus, { capture: true });
      window.removeEventListener("keydown", claimFocus, { capture: true });
    };
  }, [enabled, claimFocus, syncFocusState]);

  return { needsFocus, claimFocus };
}
