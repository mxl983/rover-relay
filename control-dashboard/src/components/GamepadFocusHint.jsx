import PropTypes from "prop-types";

export function GamepadFocusHint({ onClaimFocus }) {
  return (
    <button
      type="button"
      className="glass-card gamepad-focus-hint"
      onClick={onClaimFocus}
      aria-live="polite"
    >
      Click this window to enable controller inputs
    </button>
  );
}

GamepadFocusHint.propTypes = {
  onClaimFocus: PropTypes.func.isRequired,
};
