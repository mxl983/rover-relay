import React from "react";
import PropTypes from "prop-types";

/**
 * Semi-transparent vertical guides at 40% / 60% of the video frame —
 * approximate rover body width for judging narrow passages.
 */
export function PassageGuideOverlay({ enabled = false }) {
  if (!enabled) return null;

  return (
    <div
      className="passage-guide-overlay"
      aria-hidden="true"
      data-testid="passage-guide-overlay"
    >
      <div className="passage-guide-line passage-guide-line--left" />
      <div className="passage-guide-line passage-guide-line--right" />
    </div>
  );
}

PassageGuideOverlay.propTypes = {
  enabled: PropTypes.bool,
};
