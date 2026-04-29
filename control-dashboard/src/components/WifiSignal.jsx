import React from "react";
import { WifiHigh, Wifi, WifiLow, WifiZero, WifiOff } from "lucide-react";

export const WifiSignal = ({ dbm }) => {
  // 1. Convert dBm to a strength level (0 to 4)
  const getLevel = (val) => {
    if (val > -55) return 4; // Excellent
    if (val > -65) return 3; // Good
    if (val > -75) return 2; // Fair
    if (val > -85) return 1; // Weak
    return 0; // Unusable
  };

  const level = getLevel(dbm);

  // 2. Determine color based on strength
  const getColor = (lvl) => {
    if (lvl >= 3) return "#4caf50"; // Green
    if (lvl === 2) return "#ffeb3b"; // Yellow
    if (lvl === 1) return "#ff9800"; // Orange
    return "#f44336"; // Red
  };

  const activeColor = getColor(level);
  const Icon =
    level >= 4
      ? Wifi
      : level === 3
        ? WifiHigh
        : level === 2
          ? WifiLow
          : level === 1
            ? WifiZero
            : WifiOff;

  return (
    <div
      style={{
        display: "flex",
        alignItems: "center",
        gap: "8px",
        fontFamily: "sans-serif",
      }}
    >
      <Icon size={19} color={activeColor} strokeWidth={2.1} aria-label={`Wi-Fi ${level} of 4`} />
    </div>
  );
};

export default WifiSignal;
