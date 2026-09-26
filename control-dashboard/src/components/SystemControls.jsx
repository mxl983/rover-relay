import React from "react";
import * as DropdownMenu from "@radix-ui/react-dropdown-menu";
import {
  Power,
  RefreshCw,
  Settings,
  Video,
  Keyboard,
  Gamepad2,
  ShieldAlert,
  Glasses,
  Gauge,
  Rabbit,
  Volume2,
  Mic,
  Aperture,
} from "lucide-react";

const CONTROL_MODE_OPTIONS = [
  { value: "keyboard", label: "Keyboard", icon: <Keyboard size={10} strokeWidth={2.25} /> },
  { value: "joystick", label: "Joystick", icon: <Gamepad2 size={10} strokeWidth={2.25} /> },
  { value: "immersive", label: "Immersive", icon: <Glasses size={10} strokeWidth={2.25} /> },
];

const RESOLUTION_OPTIONS = [
  { value: "480p", label: "480p" },
  { value: "720p", label: "720p" },
  { value: "1080p", label: "1080p" },
];

/** Match MentorPi web_car SPEED_LEVELS (slow / normal / fast). */
const SPEED_OPTIONS = [
  { value: "slow", label: "Slow" },
  { value: "normal", label: "Mid" },
  { value: "fast", label: "Fast" },
];

/** Shared overall width so every settings segmented control lines up. */
const SEGMENT_TOGGLE_WIDTH = "118px";
const POWER_SAVING_OPTIONS = [
  { value: "off", label: "Off" },
  { value: "5", label: "5m" },
  { value: "10", label: "10m" },
  { value: "30", label: "30m" },
];

function SegmentedToggle({
  options,
  value,
  onChange,
  ariaLabel,
  uppercase = true,
  iconMode = false,
  width = SEGMENT_TOGGLE_WIDTH,
}) {
  return (
    <div
      style={{ ...styles.segmentGroup, width }}
      role="group"
      aria-label={ariaLabel}
      onPointerDown={(event) => event.stopPropagation()}
    >
      {options.map((option) => {
        const active = value === option.value;
        return (
          <button
            key={String(option.value)}
            type="button"
            style={{
              ...styles.segmentBtn,
              ...(uppercase ? {} : styles.segmentBtnMixedCase),
              ...(iconMode ? styles.segmentBtnIcon : {}),
              ...(active ? styles.segmentBtnActive : {}),
            }}
            aria-pressed={active}
            aria-label={option.label}
            title={option.label}
            onClick={() => {
              if (!active) onChange(option.value);
            }}
          >
            {iconMode && option.icon ? option.icon : option.label}
          </button>
        );
      })}
    </div>
  );
}

function SettingsToggleRow({ icon, label, children, title }) {
  return (
    <DropdownMenu.Item
      style={styles.toggleRow}
      onSelect={(event) => event.preventDefault()}
      title={title}
    >
      <span style={styles.toggleRowMain}>
        {icon}
        <span>{label}</span>
      </span>
      {children}
    </DropdownMenu.Item>
  );
}

function powerSavingModeValue(enabled, timeoutMinutes) {
  if (!enabled) return "off";
  const mins = Number(timeoutMinutes);
  if (mins === 10 || mins === 30) return String(mins);
  return "5";
}

export const SystemControls = ({
  isPowered,
  resMode,
  driveAssistEnabled,
  powerSavingEnabled,
  powerSavingTimeoutMinutes = 5,
  onDriveAssistChange,
  onPowerSavingChange,
  onResChange,
  onAction,
  controlMode,
  onControlModeChange,
  driveSpeed = "fast",
  onDriveSpeedChange,
  metricsPanelEnabled,
  onMetricsPanelChange,
  autoExposureEnabled = true,
  onAutoExposureChange,
  roverSpeakerEnabled = true,
  onRoverSpeakerChange,
  dashMicEnabled = false,
  onDashMicChange,
}) => {
  const [open, setOpen] = React.useState(false);
  if (!isPowered) return null;

  const powerSavingMode = powerSavingModeValue(
    powerSavingEnabled,
    powerSavingTimeoutMinutes,
  );

  return (
    <DropdownMenu.Root open={open} onOpenChange={setOpen} modal={false}>
      <DropdownMenu.Trigger asChild>
        <button
          type="button"
          style={styles.triggerWrapper}
          aria-label="Open settings"
          onPointerDown={(event) => {
            event.preventDefault();
            event.stopPropagation();
            setOpen(true);
          }}
        >
          <Settings size={18} style={styles.bareIcon} />
        </button>
      </DropdownMenu.Trigger>

      <DropdownMenu.Portal forceMount>
        <>
          {open && (
            <div
              className="settings-drawer-backdrop"
              aria-hidden="true"
              onPointerDown={() => setOpen(false)}
            />
          )}
          <DropdownMenu.Content
            className="settings-menu-drawer"
            forceMount
            style={styles.menuContent}
            side="right"
            align="start"
            sideOffset={0}
            avoidCollisions={false}
          >
          <SettingsToggleRow icon={<Video size={12} />} label="Res">
            <SegmentedToggle
              ariaLabel="Stream resolution"
              uppercase={false}
              value={resMode}
              options={RESOLUTION_OPTIONS}
              onChange={onResChange}
            />
          </SettingsToggleRow>


          <DropdownMenu.Separator style={styles.separator} />

          <SettingsToggleRow
            icon={<Power size={12} />}
            label="PSM"
            title="Idle shutdown: Off, or auto power-off after 5 / 10 / 30 minutes of inactivity"
          >
            <SegmentedToggle
              ariaLabel="Power saving idle timeout"
              uppercase={false}
              value={powerSavingMode}
              options={POWER_SAVING_OPTIONS}
              onChange={(mode) => {
                if (mode === "off") {
                  onPowerSavingChange?.({ enabled: false });
                  return;
                }
                onPowerSavingChange?.({
                  enabled: true,
                  timeoutMinutes: Number(mode),
                });
              }}
            />
          </SettingsToggleRow>

          <DropdownMenu.Separator style={styles.separator} />

          <DropdownMenu.Label style={styles.menuLabel}>
            Driving
          </DropdownMenu.Label>

          <SettingsToggleRow
            icon={
              controlMode === "immersive" ? (
                <Glasses size={12} />
              ) : controlMode === "joystick" ? (
                <Gamepad2 size={12} />
              ) : (
                <Keyboard size={12} />
              )
            }
            label="Control"
          >
            <SegmentedToggle
              ariaLabel="Control mode"
              iconMode
              value={controlMode}
              options={CONTROL_MODE_OPTIONS}
              onChange={onControlModeChange}
            />
          </SettingsToggleRow>

          <SettingsToggleRow
            icon={<Rabbit size={12} />}
            label="Speed"
            title="MentorPi drive speed (slow / normal / fast)"
          >
            <SegmentedToggle
              ariaLabel="Drive speed"
              uppercase={false}
              value={driveSpeed}
              options={SPEED_OPTIONS}
              onChange={(level) => onDriveSpeedChange?.(level)}
            />
          </SettingsToggleRow>

          <SettingsToggleRow
            icon={<ShieldAlert size={12} />}
            label="Pre-collision"
            title="Pre-collision stop: lidar blocks drive into nearby obstacles"
          >
            <SegmentedToggle
              ariaLabel="Pre-collision stop"
              value={driveAssistEnabled ? "on" : "off"}
              options={[
                { label: "OFF", value: "off" },
                { label: "ON", value: "on" },
              ]}
              onChange={(mode) => onDriveAssistChange?.(mode === "on")}
            />
          </SettingsToggleRow>

          <SettingsToggleRow icon={<Gauge size={12} />} label="Metrics">
            <SegmentedToggle
              ariaLabel="Metrics panel"
              value={metricsPanelEnabled ? "on" : "off"}
              options={[
                { label: "OFF", value: "off" },
                { label: "ON", value: "on" },
              ]}
              onChange={(mode) => onMetricsPanelChange?.(mode === "on")}
            />
          </SettingsToggleRow>

          <SettingsToggleRow
            icon={<Aperture size={12} />}
            label="Auto Exposure"
            title="Day mode: camera auto exposure (dims in bright scenes, brightens in dark). Off = fixed drive-sharp shutter. Night vision always uses manual."
          >
            <SegmentedToggle
              ariaLabel="Auto exposure"
              value={autoExposureEnabled ? "on" : "off"}
              options={[
                { label: "OFF", value: "off" },
                { label: "ON", value: "on" },
              ]}
              onChange={(mode) => onAutoExposureChange?.(mode === "on")}
            />
          </SettingsToggleRow>

          <SettingsToggleRow
            icon={<Volume2 size={12} />}
            label="Speaker"
            title="Hear rover microphone audio"
          >
            <SegmentedToggle
              ariaLabel="Rover speaker"
              value={roverSpeakerEnabled ? "on" : "off"}
              options={[
                { label: "OFF", value: "off" },
                { label: "ON", value: "on" },
              ]}
              onChange={(mode) => onRoverSpeakerChange?.(mode === "on")}
            />
          </SettingsToggleRow>

          <SettingsToggleRow
            icon={<Mic size={12} />}
            label="Mic"
            title="Send your voice to the rover"
          >
            <SegmentedToggle
              ariaLabel="Dashboard microphone"
              value={dashMicEnabled ? "on" : "off"}
              options={[
                { label: "OFF", value: "off" },
                { label: "ON", value: "on" },
              ]}
              onChange={(mode) => onDashMicChange?.(mode === "on")}
            />
          </SettingsToggleRow>

          <DropdownMenu.Separator style={styles.separator} />

          {/* SYSTEM ACTIONS */}
          <DropdownMenu.Item
            style={styles.menuItem}
            onSelect={() => onAction("reboot")}
          >
            <RefreshCw size={14} /> <span>Reboot Rover</span>
          </DropdownMenu.Item>

          <DropdownMenu.Item
            style={{ ...styles.menuItem, color: "#ff4444" }}
            onSelect={() => onAction("shutdown")}
          >
            <Power size={14} /> <span>Shutdown</span>
          </DropdownMenu.Item>

          </DropdownMenu.Content>
        </>
      </DropdownMenu.Portal>
    </DropdownMenu.Root>
  );
};

const styles = {
  triggerWrapper: {
    outline: "none",
    display: "flex",
    alignItems: "center",
    justifyContent: "center",
    width: 28,
    height: 28,
    padding: 0,
    border: "none",
    borderRadius: 0,
    background: "transparent",
    boxShadow: "none",
    color: "inherit",
    cursor: "pointer",
  },
  bareIcon: {
    color: "#ffffff",
    cursor: "pointer",
    opacity: 0.8,
    transition: "opacity 0.2s",
    padding: "4px",
  },
  menuContent: {
    minWidth: "220px",
    backgroundColor: "rgba(10, 10, 10, 0.95)",
    backdropFilter: "blur(12px)",
    borderRadius: "6px",
    padding: "5px",
    border: "1px solid rgba(255, 255, 255, 0.2)",
    boxShadow: "0px 10px 38px -10px rgba(0, 0, 0, 0.5)",
    zIndex: 9999,
    display: "flex",
    flexDirection: "column",
  },
  menuItem: {
    fontSize: "12px",
    color: "#eee",
    borderRadius: "3px",
    display: "flex",
    alignItems: "center",
    gap: "10px",
    padding: "8px 10px",
    cursor: "pointer",
    outline: "none",
    transition: "background 0.2s",
  },
  toggleRow: {
    fontSize: "11px",
    color: "#eee",
    borderRadius: "3px",
    display: "flex",
    alignItems: "center",
    justifyContent: "space-between",
    gap: "8px",
    padding: "4px 8px",
    minHeight: "unset",
    cursor: "default",
    outline: "none",
  },
  toggleRowMain: {
    display: "flex",
    alignItems: "center",
    gap: "8px",
    minWidth: 0,
    flex: "1 1 auto",
  },
  segmentGroup: {
    display: "inline-flex",
    flexShrink: 0,
    width: SEGMENT_TOGGLE_WIDTH,
    borderRadius: "3px",
    border: "1px solid rgba(255, 255, 255, 0.2)",
    overflow: "hidden",
    background: "rgba(255, 255, 255, 0.04)",
    height: "18px",
  },
  segmentBtn: {
    border: "none",
    background: "transparent",
    color: "rgba(238, 238, 238, 0.72)",
    fontSize: "8px",
    fontWeight: 600,
    letterSpacing: "0.03em",
    flex: "1 1 0",
    minWidth: 0,
    padding: "0 4px",
    height: "18px",
    cursor: "pointer",
    lineHeight: 1,
    textTransform: "uppercase",
    display: "inline-flex",
    alignItems: "center",
    justifyContent: "center",
  },
  segmentBtnActive: {
    background: "rgba(255, 255, 255, 0.18)",
    color: "#ffffff",
  },
  segmentBtnMixedCase: {
    fontSize: "7px",
    letterSpacing: "0",
    textTransform: "none",
    padding: "0 4px",
  },
  segmentBtnIcon: {
    padding: 0,
  },
  menuLabel: {
    paddingLeft: "10px",
    fontSize: "10px",
    lineHeight: "25px",
    color: "#666",
    textTransform: "uppercase",
    letterSpacing: "0.5px",
  },
  separator: {
    height: "1px",
    backgroundColor: "rgba(255, 255, 255, 0.1)",
    margin: "5px",
  },
  bootBtn: {
    background: "#ffffff",
    color: "#000",
    border: "none",
    padding: "8px 16px",
    borderRadius: "4px",
    fontSize: "11px",
    fontWeight: "bold",
    cursor: "pointer",
  },
};
