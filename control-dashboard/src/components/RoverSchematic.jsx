import React, { useCallback } from "react";
import PropTypes from "prop-types";
const TOUCH_TARGET_MIN = 44;
const SIZE = 84;

const palette = {
  green: "#00f2ff",
  greenCharging: "#22c55e", // true green for charging blink
  yellow: "#ffd60a",
  red: "#ff453a",
  blue: "#0a84ff",
  grey: "#636366",
  text: "rgba(255,255,255,0.9)",
  panelBg: "rgba(30, 41, 59, 0.24)",
  panelBorder: "rgba(255,255,255,0.16)",
  label: "rgba(255,255,255,0.62)",
};

const clamp01 = (v) => Math.min(1, Math.max(0, v));

/** Horizontal rev strip: number of discrete dashes */
const THROTTLE_SEGMENTS = 26;

function lerpChannel(a, b, t) {
  return Math.round(a + (b - a) * t);
}

/** Position t in [0,1]: left green → right red (via yellow). */
function throttleDashColor(t) {
  const u = clamp01(t);
  const parseHex = (h) => {
    const n = parseInt(h.slice(1), 16);
    return [(n >> 16) & 255, (n >> 8) & 255, n & 255];
  };
  const toHex = (r, g, b) =>
    "#" + [r, g, b].map((x) => Math.round(x).toString(16).padStart(2, "0")).join("");
  const blend = (c1, c2, x) => {
    const [r1, g1, b1] = parseHex(c1);
    const [r2, g2, b2] = parseHex(c2);
    return toHex(
      lerpChannel(r1, r2, x),
      lerpChannel(g1, g2, x),
      lerpChannel(b1, b2, x),
    );
  };
  if (u <= 0.5) return blend("#22c55e", "#ffd60a", u * 2);
  return blend("#ffd60a", "#ff453a", (u - 0.5) * 2);
}

function bandColor(value, { good, warn }, invert = false) {
  if (value == null) return palette.grey;
  if (invert) {
    if (value >= good) return palette.green;
    if (value >= warn) return palette.yellow;
    return palette.red;
  } else {
    if (value <= good) return palette.green;
    if (value <= warn) return palette.yellow;
    return palette.red;
  }
}

function formatMetricValue(value, unit, isOffline) {
  if (value == null) return isOffline ? "--" : "…";
  return `${Math.round(value)}${unit}`;
}

export const RoverSchematic = ({
  pan = 90,
  battery = null,
  cpuTemp = null,
  ambientTempC = null,
  latencyMs = null,
  throttle = null,
  isOffline = false,
  isCharging = false,
  handleClick,
}) => {
  const hasBatteryData = battery !== null && battery !== undefined;
  const chargeLevel = hasBatteryData ? Math.min(Math.max(battery, 0), 100) : 0;

  const batteryColor = isOffline
    ? palette.grey
    : bandColor(chargeLevel, { good: 60, warn: 30 }, true);
    
  const cpuColor = isOffline
    ? palette.grey
    : bandColor(cpuTemp, { good: 60, warn: 75 });
    
  const latencyColor = isOffline
    ? palette.grey
    : bandColor(latencyMs, { good: 80, warn: 200 });
  const ambientColor = isOffline
    ? palette.grey
    : bandColor(ambientTempC, { good: 32, warn: 40 });

  const throttlePct = throttle != null ? Math.min(100, Math.max(0, throttle)) : 0;
  const batteryText = hasBatteryData
    ? `${Math.round(chargeLevel)}%`
    : isOffline
      ? "--"
      : "…";
  const cpuText = formatMetricValue(cpuTemp, "°", isOffline);
  const ambientText = formatMetricValue(ambientTempC, "°", isOffline);
  const latencyText = formatMetricValue(latencyMs, "ms", isOffline);

  const labelParts = [];
  if (hasBatteryData) labelParts.push(`battery ${Math.round(chargeLevel)}%`);
  if (cpuTemp != null) labelParts.push(`CPU ${Math.round(cpuTemp)}°C`);
  if (ambientTempC != null) labelParts.push(`ambient ${Math.round(ambientTempC)}°C`);
  if (latencyMs != null) labelParts.push(`latency ${Math.round(latencyMs)}ms`);
  labelParts.push(`throttle ${Math.round(throttlePct)}%`);
  if (pan != null) labelParts.push(`pan ${Math.round(pan)}°`);
  labelParts.push(isOffline ? "offline" : "online");

  const onClick = useCallback(
    (e) => {
      if (typeof handleClick !== "function") return;
      e.stopPropagation();
      handleClick();
    },
    [handleClick],
  );
  const isInteractive = typeof handleClick === "function";

  const throttleFrac = clamp01(throttlePct / 100);

  return (
    <div
      style={{
        display: "flex",
        flexDirection: "column",
        alignItems: "center",
        gap: 6,
        pointerEvents: "auto",
        zIndex: 10,
      }}
    >
      <div
        role={isInteractive ? "button" : undefined}
        tabIndex={isInteractive ? 0 : undefined}
        aria-label={`${labelParts.join(", ")}. Tap to expand.`}
        style={{
          width: SIZE,
          height: SIZE,
          minWidth: TOUCH_TARGET_MIN,
          minHeight: TOUCH_TARGET_MIN,
          boxSizing: "border-box",
          display: "flex",
          alignItems: "center",
          justifyContent: "center",
          cursor: isInteractive ? "pointer" : "default",
          outline: "none",
          WebkitTapHighlightColor: "transparent",
          touchAction: "manipulation",
        }}
        onClick={onClick}
        onKeyDown={(e) => {
          if (!isInteractive) return;
          if (e.key === "Enter" || e.key === " ") {
            e.preventDefault();
            handleClick();
          }
        }}
      >
      <div
        style={{
          width: SIZE,
          height: "auto",
          borderRadius: 8,
          border: "none",
          background: palette.panelBg,
          backdropFilter: "blur(12px) saturate(130%)",
          WebkitBackdropFilter: "blur(12px) saturate(130%)",
          boxShadow:
            "inset 0 1px 0 rgba(255,255,255,0.14), 0 8px 18px rgba(0,0,0,0.32)",
          display: "flex",
          flexDirection: "column",
          justifyContent: "flex-start",
          gap: 6,
          padding: "10px",
          pointerEvents: "none",
          fontFamily:
            "-apple-system, BlinkMacSystemFont, 'SF Pro Text', 'Segoe UI', sans-serif",
        }}
        aria-hidden
      >
        <MetricRow
          label="BAT"
          value={batteryText}
          color={isCharging && !isOffline ? palette.greenCharging : batteryColor}
        />
        <MetricRow label="TMP" value={ambientText} color={ambientColor} />
        <MetricRow label="CPU" value={cpuText} color={cpuColor} />
        <MetricRow label="LAT" value={latencyText} color={latencyColor} />
      </div>
      </div>

      {/* Rev strip: dashes only (no track box); color left→right green→red */}
      <div
        style={{
          width: SIZE + 8,
          height: 6,
          opacity: throttlePct > 0 && !isOffline ? 1 : 0,
          transition: "opacity 0.12s ease-out",
          display: "flex",
          alignItems: "stretch",
          gap: 2,
        }}
        aria-label={`Throttle ${Math.round(throttlePct)}%`}
      >
        {Array.from({ length: THROTTLE_SEGMENTS }, (_, i) => {
          const active = i < throttleFrac * THROTTLE_SEGMENTS;
          const t =
            THROTTLE_SEGMENTS > 1 ? i / (THROTTLE_SEGMENTS - 1) : 0;
          const fill = isOffline
            ? palette.grey
            : active
              ? throttleDashColor(t)
              : "rgba(255,255,255,0.12)";
          return (
            <div
              key={i}
              style={{
                flex: "1 1 0",
                minWidth: 0,
                height: "100%",
                borderRadius: 1,
                background: fill,
                opacity: active ? 1 : 0.55,
                transition: "background 0.07s ease-out, opacity 0.07s ease-out",
              }}
            />
          );
        })}
      </div>
    </div>
  );
};

function MetricRow({ label, value, color }) {
  return (
    <div
      style={{
        display: "flex",
        alignItems: "center",
        justifyContent: "space-between",
        gap: 10,
      }}
    >
      <div
        style={{
          display: "inline-flex",
          alignItems: "center",
          gap: 6,
          color: palette.label,
          fontSize: 9,
          letterSpacing: "0.08em",
          fontWeight: 600,
        }}
      >
        <span
          style={{
            width: 6,
            height: 6,
            borderRadius: 999,
            background: color,
            boxShadow: `0 0 10px ${color}66`,
          }}
        />
        {label}
      </div>
      <div
        style={{
          color: palette.text,
          fontSize: 13,
          lineHeight: 1,
          fontWeight: 700,
          letterSpacing: "0.02em",
          minWidth: 44,
          textAlign: "right",
          fontVariantNumeric: "tabular-nums",
        }}
      >
        {value}
      </div>
    </div>
  );
}

MetricRow.propTypes = {
  label: PropTypes.string.isRequired,
  value: PropTypes.string.isRequired,
  color: PropTypes.string.isRequired,
};

RoverSchematic.propTypes = {
  pan: PropTypes.number,
  battery: PropTypes.number,
  cpuTemp: PropTypes.number,
  ambientTempC: PropTypes.number,
  latencyMs: PropTypes.number,
  throttle: PropTypes.number,
  isOffline: PropTypes.bool,
  isCharging: PropTypes.bool,
  handleClick: PropTypes.func,
};