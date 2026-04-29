import { useEffect, useState } from "react";

/** Same rules as the hook; safe for `useState` initializers and control-mode defaults. */
export function getIsMobileSnapshot() {
  if (typeof window === "undefined") return false;
  const hasTouch = "ontouchstart" in window || navigator.maxTouchPoints > 0;
  const isSmallScreen = window.innerWidth <= 1024;
  return hasTouch || isSmallScreen;
}

export const useIsMobile = () => {
  const [isMobile, setIsMobile] = useState(getIsMobileSnapshot);

  useEffect(() => {
    const checkMobile = () => {
      setIsMobile(getIsMobileSnapshot());
    };

    checkMobile();
    window.addEventListener("resize", checkMobile);
    return () => window.removeEventListener("resize", checkMobile);
  }, []);

  return isMobile;
};
