import React, { useState, useEffect } from "react";
import { Maximize, Minimize, Smartphone } from "lucide-react";

const isIOS = () =>
  /iPad|iPhone|iPod/.test(navigator.userAgent) ||
  (navigator.platform === "MacIntel" && navigator.maxTouchPoints > 1);

const isStandalone = () =>
  window.navigator.standalone === true ||
  window.matchMedia("(display-mode: standalone)").matches;

export const FullscreenButton = () => {
  const [isFullscreen, setIsFullscreen] = useState(false);
  const [isSupported, setIsSupported] = useState(true);
  const [showIOSTip, setShowIOSTip] = useState(false);
  const iosNotStandalone = isIOS() && !isStandalone();

  useEffect(() => {
    // Fullscreen API (standard or webkit/Safari). iOS in Safari doesn't support it;
    // when added to Home Screen (standalone) the app is already fullscreen-like, so we hide the button there too.
    const canFs =
      document.fullscreenEnabled || document.webkitFullscreenEnabled;
    setIsSupported(!!canFs);

    const handleFsChange = () => {
      setIsFullscreen(
        !!(document.fullscreenElement || document.webkitFullscreenElement),
      );
    };

    document.addEventListener("fullscreenchange", handleFsChange);
    document.addEventListener("webkitfullscreenchange", handleFsChange);

    return () => {
      document.removeEventListener("fullscreenchange", handleFsChange);
      document.removeEventListener("webkitfullscreenchange", handleFsChange);
    };
  }, []);

  const toggleFullscreen = () => {
    if (!document.fullscreenElement && !document.webkitFullscreenElement) {
      const el = document.documentElement;
      const requestFs = el.requestFullscreen || el.webkitRequestFullscreen;
      if (requestFs) {
        requestFs.call(el).catch((e) => console.error(e));
      }
    } else {
      const exitFs = document.exitFullscreen || document.webkitExitFullscreen;
      if (exitFs) exitFs.call(document);
    }
  };

  // iOS in Safari: no Fullscreen API — show "Add to Home Screen" tip instead of button
  if (iosNotStandalone) {
    return (
      <span
        style={styles.bareIcon}
        onClick={() => setShowIOSTip((v) => !v)}
        title="Add to Home Screen for fullscreen"
        role="button"
        aria-label="Fullscreen tip"
      >
        <Smartphone size={16} />
        {showIOSTip && (
          <span style={styles.iosTip}>
            Add to Home Screen (Share → Add to Home Screen) for fullscreen.
          </span>
        )}
      </span>
    );
  }

  if (!isSupported) return null;

  return isFullscreen ? (
    <Minimize
      size={16}
      style={styles.bareIcon}
      onClick={toggleFullscreen}
      title="Exit Fullscreen"
    />
  ) : (
    <Maximize
      size={16}
      style={styles.bareIcon}
      onClick={toggleFullscreen}
      title="Enter Fullscreen"
    />
  );
};

const styles = {
  bareIcon: {
    color: "#00f2ff",
    cursor: "pointer",
    opacity: 0.7,
    transition: "all 0.2s ease",
    padding: "4px",
    position: "relative",
  },
  iosTip: {
    position: "absolute",
    top: "100%",
    right: 0,
    marginTop: 4,
    padding: "6px 10px",
    background: "rgba(0,0,0,0.85)",
    color: "#fff",
    fontSize: 12,
    whiteSpace: "nowrap",
    borderRadius: 6,
    boxShadow: "0 2px 8px rgba(0,0,0,0.3)",
    zIndex: 1000,
  },
};
