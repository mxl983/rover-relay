import PropTypes from "prop-types";

/**
 * Shown until SteamOS / Chrome exposes the Gamepad API (focus + button press).
 */
export function GamepadActivationOverlay({ onActivate }) {
  return (
    <button
      type="button"
      className="gamepad-activation-overlay"
      onClick={onActivate}
      aria-label="Tap screen or press any controller button to enable gamepad controls"
    >
      <div className="gamepad-activation-card glass-card">
        <strong>Controller ready</strong>
        <span>Tap here or press any controller button to start</span>
        <span className="gamepad-activation-sub">
          Required on SteamOS / handheld Chrome before sticks and buttons work.
        </span>
      </div>
    </button>
  );
}

GamepadActivationOverlay.propTypes = {
  onActivate: PropTypes.func.isRequired,
};
