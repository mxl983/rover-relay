import React from "react";

/** Default 🐱 — also try 😺 😸 🐈 🐈‍⬛ */
export function BrandCatIcon({ emoji = "🐱", size = 18, className = "" }) {
  return (
    <span
      className={`hud-brand-emoji ${className}`.trim()}
      style={{ fontSize: size, lineHeight: 1 }}
      aria-hidden
    >
      {emoji}
    </span>
  );
}

export default BrandCatIcon;
