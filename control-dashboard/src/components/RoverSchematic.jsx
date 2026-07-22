import React, { useCallback, useEffect, useMemo, useState } from "react";
import PropTypes from "prop-types";
import { getWifiLevel } from "../utils/wifiSignal.js";

const TOUCH_TARGET_MIN = 44;
const SIZE = 84;
const VISIBLE_SECONDARY = 3;
/** Row box must clear 13px glyphs + antialiasing; was 13 and clipped values. */
const ROW_H = 16;
const ROW_GAP = 4;
const ROLL_STEP_PX = ROW_H + ROW_GAP;
const VIEWPORT_H =
  VISIBLE_SECONDARY * ROW_H + (VISIBLE_SECONDARY - 1) * ROW_GAP;
const ROLL_INTERVAL_MS = 3800;
const ROLL_DURATION_MS = 520;

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

function formatDistanceShort(meters, isOffline) {
  if (meters == null || !Number.isFinite(Number(meters))) {
    return isOffline ? "--" : "…";
  }
  const m = Math.max(0, Number(meters));
  if (m <= 99) return `${Math.round(m)}m`;
  const km = m / 1000;
  if (km < 10) return `${km.toFixed(1)}k`;
  if (km < 1000) return `${Math.round(km)}k`;
  return `${(km / 1000).toFixed(1)}M`;
}

const STANDARD_PRESSURE_HPA = 1013.25;

function formatPressureDeltaShort(pressureHpa, isOffline) {
  if (pressureHpa == null || !Number.isFinite(Number(pressureHpa))) {
    return isOffline ? "--" : "…";
  }
  // Upstream is hPa; show signed kPa offset from standard sea-level pressure.
  const deltaKpa = (Number(pressureHpa) - STANDARD_PRESSURE_HPA) / 10;
  if (Math.abs(deltaKpa) < 0.05) return "0";
  const rounded = Math.round(deltaKpa * 10) / 10;
  const body = Number.isInteger(rounded) ? `${rounded}` : rounded.toFixed(1);
  return rounded > 0 ? `+${body}` : body;
}

function formatWifiShort(dbm, isOffline) {
  const level = getWifiLevel(dbm);
  if (level == null) return isOffline ? "--" : "…";
  if (level <= 1) return "LV1";
  if (level === 2) return "LV2";
  return "LV3";
}

function wifiStrengthColor(dbm, isOffline) {
  if (isOffline) return palette.grey;
  const level = getWifiLevel(dbm);
  if (level == null) return palette.grey;
  if (level <= 1) return palette.red;
  if (level === 2) return palette.yellow;
  return palette.green;
}

function formatVoltageShort(voltage, isOffline) {
  if (voltage == null || !Number.isFinite(Number(voltage))) {
    return isOffline ? "--" : "…";
  }
  return `${Number(voltage).toFixed(1)}V`;
}

export const RoverSchematic = ({
  pan = 90,
  battery = null,
  cpuTemp = null,
  ambientTempC = null,
  latencyMs = null,
  throttle = null,
  voltage = null,
  wifiSignal = null,
  distanceMeters = null,
  pressureHpa = null,
  cpuLoad = null,
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
  const voltageColor = isOffline
    ? palette.grey
    : bandColor(voltage, { good: 11.4, warn: 10.5 }, true);
  const wifiColor = wifiStrengthColor(wifiSignal, isOffline);
  const loadColor = isOffline
    ? palette.grey
    : bandColor(cpuLoad, { good: 55, warn: 80 });
  const distColor = isOffline ? palette.grey : palette.green;
  const pressureColor = isOffline
    ? palette.grey
    : bandColor(
        pressureHpa != null && Number.isFinite(Number(pressureHpa))
          ? Math.abs(Number(pressureHpa) - STANDARD_PRESSURE_HPA) / 10
          : null,
        { good: 0.5, warn: 1.5 },
      );

  const throttlePct = throttle != null ? Math.min(100, Math.max(0, throttle)) : 0;
  const batteryText = hasBatteryData
    ? `${Math.round(chargeLevel)}%`
    : isOffline
      ? "--"
      : "…";

  const secondaryMetrics = useMemo(() => {
    /** @type {{ key: string; label: string; value: string; color: string }[]} */
    const rows = [
      {
        key: "tmp",
        label: "TMP",
        value: formatMetricValue(ambientTempC, "°", isOffline),
        color: ambientColor,
      },
      {
        key: "cpu",
        label: "CPU",
        value: formatMetricValue(cpuTemp, "°", isOffline),
        color: cpuColor,
      },
      {
        key: "lat",
        label: "LAT",
        value: formatMetricValue(latencyMs, "ms", isOffline),
        color: latencyColor,
      },
      {
        key: "vol",
        label: "VOL",
        value: formatVoltageShort(voltage, isOffline),
        color: voltageColor,
      },
      {
        key: "wifi",
        label: "WIFI",
        value: formatWifiShort(wifiSignal, isOffline),
        color: wifiColor,
      },
      {
        key: "dst",
        label: "DST",
        value: formatDistanceShort(distanceMeters, isOffline),
        color: distColor,
      },
      {
        key: "air",
        label: "AIR",
        value: formatPressureDeltaShort(pressureHpa, isOffline),
        color: pressureColor,
      },
      {
        key: "load",
        label: "LOAD",
        value: formatMetricValue(cpuLoad, "%", isOffline),
        color: loadColor,
      },
    ];
    return rows;
  }, [
    ambientTempC,
    ambientColor,
    cpuTemp,
    cpuColor,
    latencyMs,
    latencyColor,
    voltage,
    voltageColor,
    wifiSignal,
    wifiColor,
    distanceMeters,
    distColor,
    pressureHpa,
    pressureColor,
    cpuLoad,
    loadColor,
    isOffline,
  ]);

  const [rollIndex, setRollIndex] = useState(0);
  const [rollAnimating, setRollAnimating] = useState(true);
  const canRoll = secondaryMetrics.length > VISIBLE_SECONDARY;

  useEffect(() => {
    setRollIndex(0);
    setRollAnimating(false);
  }, [secondaryMetrics.length]);

  useEffect(() => {
    if (!canRoll) return undefined;
    const id = setInterval(() => {
      setRollAnimating(true);
      setRollIndex((prev) => prev + 1);
    }, ROLL_INTERVAL_MS);
    return () => clearInterval(id);
  }, [canRoll, secondaryMetrics.length]);

  useEffect(() => {
    if (!canRoll || rollIndex < secondaryMetrics.length) return undefined;
    const t = setTimeout(() => {
      setRollAnimating(false);
      setRollIndex(0);
    }, ROLL_DURATION_MS);
    return () => clearTimeout(t);
  }, [canRoll, rollIndex, secondaryMetrics.length]);

  const rolledMetrics = useMemo(() => {
    if (!canRoll) return secondaryMetrics.slice(0, VISIBLE_SECONDARY);
    return [
      ...secondaryMetrics,
      ...secondaryMetrics.slice(0, VISIBLE_SECONDARY),
    ];
  }, [canRoll, secondaryMetrics]);

  const labelParts = [];
  if (hasBatteryData) labelParts.push(`battery ${Math.round(chargeLevel)}%`);
  if (cpuTemp != null) labelParts.push(`CPU ${Math.round(cpuTemp)}°C`);
  if (ambientTempC != null) labelParts.push(`ambient ${Math.round(ambientTempC)}°C`);
  if (latencyMs != null) labelParts.push(`latency ${Math.round(latencyMs)}ms`);
  if (voltage != null) labelParts.push(`voltage ${Number(voltage).toFixed(1)}V`);
  if (wifiSignal != null) {
    const level = getWifiLevel(wifiSignal);
    const tier = level == null ? "?" : level <= 1 ? "1" : level === 2 ? "2" : "3";
    labelParts.push(`wifi level ${tier} (${Math.round(wifiSignal)} dBm)`);
  }
  if (distanceMeters != null) labelParts.push(`distance ${Math.round(distanceMeters)}m`);
  if (pressureHpa != null) {
    const deltaKpa = (Number(pressureHpa) - STANDARD_PRESSURE_HPA) / 10;
    labelParts.push(`pressure ${deltaKpa >= 0 ? "+" : ""}${deltaKpa.toFixed(1)} kPa from standard`);
  }
  if (cpuLoad != null) labelParts.push(`load ${Math.round(cpuLoad)}%`);
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
          gap: ROW_GAP,
          padding: "8px 9px",
          pointerEvents: "none",
          fontFamily:
            "-apple-system, BlinkMacSystemFont, 'SF Pro Text', 'Segoe UI', sans-serif",
          overflow: "visible",
        }}
        aria-hidden
      >
        <MetricRow
          label="BAT"
          value={batteryText}
          color={isCharging && !isOffline ? palette.greenCharging : batteryColor}
        />
        <div
          style={{
            height: VIEWPORT_H,
            overflow: "hidden",
            position: "relative",
            // Keep a hair of vertical room so glyph edges aren't clipped mid-roll.
            marginBlock: 1,
          }}
        >
          <div
            style={{
              display: "flex",
              flexDirection: "column",
              gap: ROW_GAP,
              transform: `translateY(-${(canRoll ? rollIndex : 0) * ROLL_STEP_PX}px)`,
              transition: rollAnimating
                ? `transform ${ROLL_DURATION_MS}ms cubic-bezier(0.22, 1, 0.36, 1)`
                : "none",
              willChange: "transform",
            }}
          >
            {rolledMetrics.map((metric, i) => (
              <MetricRow
                key={`${metric.key}-${i}`}
                label={metric.label}
                value={metric.value}
                color={metric.color}
              />
            ))}
          </div>
        </div>
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
        gap: 6,
        height: ROW_H,
        minHeight: ROW_H,
        maxHeight: ROW_H,
        overflow: "visible",
      }}
    >
      <div
        style={{
          display: "inline-flex",
          alignItems: "center",
          gap: 5,
          color: palette.label,
          fontSize: 9,
          letterSpacing: "0.08em",
          fontWeight: 600,
          flexShrink: 0,
          lineHeight: 1,
        }}
      >
        <span
          style={{
            width: 6,
            height: 6,
            borderRadius: 999,
            background: color,
            boxShadow: `0 0 10px ${color}66`,
            flexShrink: 0,
          }}
        />
        {label}
      </div>
      <div
        style={{
          color: palette.text,
          fontSize: 12,
          lineHeight: `${ROW_H}px`,
          fontWeight: 700,
          letterSpacing: "0.01em",
          flex: "1 1 auto",
          minWidth: 0,
          textAlign: "right",
          fontVariantNumeric: "tabular-nums",
          whiteSpace: "nowrap",
          overflow: "visible",
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
  voltage: PropTypes.number,
  wifiSignal: PropTypes.number,
  distanceMeters: PropTypes.number,
  pressureHpa: PropTypes.number,
  cpuLoad: PropTypes.number,
  isOffline: PropTypes.bool,
  isCharging: PropTypes.bool,
  handleClick: PropTypes.func,
};
