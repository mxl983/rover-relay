import React from "react";

export const DriveAssistHUD = ({ tilt = 90 }) => {
  const green = "rgba(0, 242, 255, 0.72)";
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
        fontFamily: "monospace",
      }}
    >
      <svg viewBox="0 0 400 300" style={{ width: "100%", height: "100%" }}>
        <g stroke={green} strokeWidth="0.5" fill="none" strokeLinecap="round">
          <line x1="198.5" y1="150" x2="201.5" y2="150" />
          <line x1="200" y1="148.5" x2="200" y2="151.5" />
        </g>

        <text x="206" y="151.5" fill={green} fontSize="2.5" fontWeight="500">
          {deviation > 0
            ? `+${deviation.toFixed(1)}°`
            : `${deviation.toFixed(1)}°`}
        </text>
      </svg>
    </div>
  );
};
