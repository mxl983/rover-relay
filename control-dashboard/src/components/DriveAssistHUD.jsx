import React from "react";

export const DriveAssistHUD = ({ tilt = 90 }) => {
  const green = "#00f2ff";
  const deviation = tilt - 90;

  return (
    <div
      style={{
        position: "absolute",
        top: 0,
        left: 0,
        width: "100%",
        height: "100%",
        pointerEvents: "none",
        color: green,
        fontFamily: "monospace", // Monospace keeps the numbers from 'jumping'
      }}
    >
      <svg viewBox="0 0 400 300" style={{ width: "100%", height: "100%" }}>
        {/* --- FPS CROSSHAIR (Perfectly centered) --- */}
        <g stroke={green} strokeWidth="1" fill="none">
          {/* Horizontal lines */}
          <line x1="190" y1="150" x2="196" y2="150" />
          <line x1="204" y1="150" x2="210" y2="150" />

          {/* Vertical lines */}
          <line x1="200" y1="140" x2="200" y2="146" />
          <line x1="200" y1="154" x2="200" y2="160" />

          {/* Center point dot */}
          <circle cx="200" cy="150" r="0.5" fill={green} />
        </g>

        {/* --- TILT READOUT --- */}
        {/* Placed at a fixed offset from the center lines */}
        <text x="215" y="154" fill={green} fontSize="3" fontWeight="bold">
          {deviation > 0
            ? `+${deviation.toFixed(1)}°`
            : `${deviation.toFixed(1)}°`}
        </text>
      </svg>
    </div>
  );
};
