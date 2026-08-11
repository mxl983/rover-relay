import React from "react";
import PropTypes from "prop-types";

/**
 * Overlay cat bbox + posture on the live video plane (normalized 0–1 box).
 * FPS sits at the box's top-right corner.
 */
export function CatVisionOverlay({ cat, enabled }) {
  const box = cat?.bbox_norm || null;
  if (!enabled || !cat?.cat_present || !Array.isArray(box) || box.length !== 4) {
    return null;
  }

  const [x1, y1, x2, y2] = box.map(Number);
  const style = {
    left: `${Math.max(0, Math.min(100, x1 * 100))}%`,
    top: `${Math.max(0, Math.min(100, y1 * 100))}%`,
    width: `${Math.max(0, Math.min(100, (x2 - x1) * 100))}%`,
    height: `${Math.max(0, Math.min(100, (y2 - y1) * 100))}%`,
  };

  const label = `${cat.posture || "cat"} ${
    Number.isFinite(cat.posture_conf) ? `(${(cat.posture_conf * 100).toFixed(0)}%)` : ""
  }`.trim();

  const fps =
    Number.isFinite(Number(cat.detect_fps)) && Number(cat.detect_fps) > 0
      ? Number(cat.detect_fps).toFixed(1)
      : null;

  return (
    <div className="cat-vision-overlay" aria-hidden="true">
      <div className="cat-vision-box" style={style}>
        <span className="cat-vision-label">{label}</span>
        {fps != null ? <span className="cat-vision-fps">{fps}</span> : null}
      </div>
      <style>{`
        .cat-vision-overlay {
          position: absolute;
          inset: 0;
          pointer-events: none;
          z-index: 6;
        }
        .cat-vision-box {
          position: absolute;
          border: 2px solid #00f2ff;
          box-shadow: 0 0 0 1px rgba(0,0,0,0.35);
          border-radius: 2px;
        }
        .cat-vision-label {
          position: absolute;
          left: 0;
          top: 0;
          transform: translateY(-100%);
          background: rgba(0, 0, 0, 0.72);
          color: #00f2ff;
          font-size: 11px;
          font-weight: 700;
          letter-spacing: 0.04em;
          text-transform: uppercase;
          padding: 2px 6px;
          white-space: nowrap;
        }
        .cat-vision-fps {
          position: absolute;
          right: 0;
          top: 0;
          transform: translateY(-100%);
          background: rgba(0, 0, 0, 0.72);
          color: #9ef0ff;
          font-size: 10px;
          font-weight: 700;
          font-variant-numeric: tabular-nums;
          letter-spacing: 0.02em;
          padding: 2px 5px;
          white-space: nowrap;
        }
      `}</style>
    </div>
  );
}

CatVisionOverlay.propTypes = {
  cat: PropTypes.object,
  enabled: PropTypes.bool,
};
