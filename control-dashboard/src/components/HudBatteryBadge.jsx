import React from "react";

/**
 * Compact iPhone-style battery for the top HUD nav.
 */
export function HudBatteryBadge({
  level = null,
  isCharging = false,
  isOffline = false,
}) {
  const hasData = level != null && Number.isFinite(Number(level));
  const fillPct = hasData ? Math.min(100, Math.max(0, Number(level))) : 0;
  const low = hasData && !isOffline && fillPct < 20 && !isCharging;
  const text = hasData ? `${Math.round(fillPct)}%` : isOffline ? "--" : "…";
  // Keep the bar translucent so the white % stays readable.
  const fillColor = isOffline
    ? "#636366"
    : isCharging
      ? "#34c759"
      : low
        ? "#ff3b30"
        : "rgba(255,255,255,0.38)";

  return (
    <div
      className={[
        "hud-battery-badge",
        isCharging ? "hud-battery-badge--charging" : "",
        low ? "hud-battery-badge--low" : "",
      ]
        .filter(Boolean)
        .join(" ")}
      title={`Battery ${text}${isCharging ? " · charging" : ""}`}
      aria-label={`Battery ${text}${isCharging ? ", charging" : ""}`}
      role="status"
    >
      <svg
        className="hud-battery-badge__icon"
        width="34"
        height="15"
        viewBox="0 0 27 12"
        aria-hidden
      >
        <rect
          x="0.5"
          y="0.5"
          width="22"
          height="11"
          rx="2.2"
          ry="2.2"
          fill="none"
          stroke="rgba(255,255,255,0.55)"
          strokeWidth="1"
        />
        <path
          d="M24 3.8c.9.4 1.4 1.1 1.4 2.2s-.5 1.8-1.4 2.2V3.8z"
          fill="rgba(255,255,255,0.45)"
        />
        <rect
          className="hud-battery-badge__fill"
          x="2"
          y="2"
          width={Math.max(0, (fillPct / 100) * 19)}
          height="8"
          rx="1.4"
          ry="1.4"
          fill={fillColor}
        />
        {isCharging ? (
          <path
            className="hud-battery-badge__bolt"
            d="M12.2 1.6L9.4 6.6h2.1l-.9 3.8 3.4-5.4h-2.2l1.4-3.4z"
            fill="#0b1220"
            stroke="none"
          />
        ) : null}
        <text
          className="hud-battery-badge__pct"
          x="12"
          y="7"
          textAnchor="middle"
          dominantBaseline="middle"
          fill="#ffffff"
          stroke="rgba(0,0,0,0.55)"
          strokeWidth="0.55"
          paintOrder="stroke fill"
          fontSize="6.5"
          fontWeight="700"
          fontFamily="ui-monospace, SFMono-Regular, Menlo, monospace"
        >
          {text}
        </text>
      </svg>
    </div>
  );
}
