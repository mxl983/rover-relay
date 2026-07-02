import React from "react";
import * as DropdownMenu from "@radix-ui/react-dropdown-menu";
import {
  Power,
  RefreshCw,
  Moon,
  Sun,
  Settings,
  Focus,
  ChevronLeft,
  Video,
  Check,
  Aperture,
  Footprints,
  Zap,
  Keyboard,
  Gamepad2,
  BarChart3,
  Radar,
  ShieldAlert,
  Route,
  Glasses,
  Gauge,
} from "lucide-react";

const CONTROL_MODE_OPTIONS = [
  { value: "keyboard", label: "Keyboard", icon: <Keyboard size={10} strokeWidth={2.25} /> },
  { value: "joystick", label: "Joystick", icon: <Gamepad2 size={10} strokeWidth={2.25} /> },
  { value: "immersive", label: "Immersive", icon: <Glasses size={10} strokeWidth={2.25} /> },
];

const SEGMENT_TOGGLE_WIDTH = "88px";

function SegmentedToggle({
  options,
  value,
  onChange,
  ariaLabel,
  uppercase = true,
  iconMode = false,
}) {
  return (
    <div
      style={styles.segmentGroup}
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

export const SystemControls = ({
  isPowered,
  nvActive,
  resMode,
  focusMode,
  isCapturing,
  quietMode,
  driveAssistEnabled,
  navigationEnabled,
  powerSavingEnabled,
  onQuietModeChange,
  onDriveAssistChange,
  onNavigationChange,
  onPowerSavingChange,
  onNVToggle,
  onResChange,
  onFocusChange,
  onAction,
  controlMode,
  onControlModeChange,
  lidarMinimapEnabled = false,
  onLidarMinimapChange,
  metricsPanelEnabled,
  onMetricsPanelChange,
}) => {
  if (!isPowered) return null;

  return (
    <DropdownMenu.Root>
      <DropdownMenu.Trigger asChild>
        <div style={styles.triggerWrapper}>
          <Settings size={18} style={styles.bareIcon} />
        </div>
      </DropdownMenu.Trigger>

      <DropdownMenu.Portal>
        <DropdownMenu.Content
          style={styles.menuContent}
          side="bottom"
          align="end"
          sideOffset={8}
        >
          {/* HI-RES CAPTURE ACTION */}
          <DropdownMenu.Item
            style={{
              ...styles.menuItem,
              color: "#00f2ff",
              opacity: isCapturing ? 0.5 : 1,
            }}
            onSelect={() => !isCapturing && onAction("capture")}
            disabled={isCapturing}
          >
            <Aperture
              size={14}
              style={
                isCapturing ? { animation: "spin 2s linear infinite" } : {}
              }
            />
            <span>{isCapturing ? "Capturing..." : "Take Hi-Res Photo"}</span>
            <span style={{ marginLeft: "auto", fontSize: "9px", opacity: 0.5 }}>
              C
            </span>
          </DropdownMenu.Item>

          <DropdownMenu.Separator style={styles.separator} />

          <DropdownMenu.Sub>
            <DropdownMenu.SubTrigger style={styles.menuItem}>
              <Video size={14} /> <span>Stream</span>
              <ChevronLeft
                size={12}
                style={{ marginLeft: "auto", opacity: 0.5 }}
              />
            </DropdownMenu.SubTrigger>
            <DropdownMenu.Portal>
              <DropdownMenu.SubContent
                style={styles.menuContent}
                sideOffset={2}
                alignOffset={-5}
              >
                <DropdownMenu.Label style={styles.menuLabel}>
                  Resolution
                </DropdownMenu.Label>
                {["240p", "480p", "720p", "1080p"].map((res) => (
                  <DropdownMenu.CheckboxItem
                    key={res}
                    style={styles.menuItem}
                    checked={resMode === res}
                    onCheckedChange={() => onResChange(res)}
                  >
                    {res.toUpperCase()}
                    <DropdownMenu.ItemIndicator style={{ marginLeft: "auto" }}>
                      <Check size={12} color="#00f2ff" />
                    </DropdownMenu.ItemIndicator>
                  </DropdownMenu.CheckboxItem>
                ))}
                <DropdownMenu.Separator style={styles.separator} />
                <DropdownMenu.Item
                  style={styles.menuItem}
                  onSelect={() => onNVToggle(!nvActive)}
                >
                  {nvActive ? <Moon size={12} /> : <Sun size={12} />}
                  <span>
                    NV mode is {nvActive ? "ON" : "OFF"}
                  </span>
                </DropdownMenu.Item>
              </DropdownMenu.SubContent>
            </DropdownMenu.Portal>
          </DropdownMenu.Sub>

          {/* FOCUS SUBMENU */}
          <DropdownMenu.Sub>
            <DropdownMenu.SubTrigger style={styles.menuItem}>
              <Focus size={14} /> <span>Focus</span>
              <ChevronLeft
                size={12}
                style={{ marginLeft: "auto", opacity: 0.5 }}
              />
            </DropdownMenu.SubTrigger>
            <DropdownMenu.Portal>
              <DropdownMenu.SubContent
                style={styles.menuContent}
                sideOffset={2}
              >
                {[
                  { label: "Auto Focus", value: "auto" },
                  { label: "Near", value: "near" },
                  { label: "Mid", value: "normal" },
                  { label: "Far", value: "far" },
                ].map((f) => (
                  <DropdownMenu.CheckboxItem
                    key={f.value}
                    style={styles.menuItem}
                    checked={focusMode === f.value}
                    onCheckedChange={() => onFocusChange(f.value)}
                  >
                    {f.label}
                    <DropdownMenu.ItemIndicator style={{ marginLeft: "auto" }}>
                      <Check size={12} color="#00f2ff" />
                    </DropdownMenu.ItemIndicator>
                  </DropdownMenu.CheckboxItem>
                ))}
              </DropdownMenu.SubContent>
            </DropdownMenu.Portal>
          </DropdownMenu.Sub>

          <DropdownMenu.Separator style={styles.separator} />

          <SettingsToggleRow
            icon={<Power size={12} />}
            label="PSM"
            title={
              powerSavingEnabled
                ? "Power saving on; disables idle shutdown when off."
                : "Power saving off; re-enables idle shutdown when on."
            }
          >
            <SegmentedToggle
              ariaLabel="Power saving"
              uppercase={false}
              value={powerSavingEnabled ? "on" : "off"}
              options={[
                { label: "Off", value: "off" },
                { label: "On", value: "on" },
              ]}
              onChange={(mode) => onPowerSavingChange?.(mode === "on")}
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
            icon={quietMode ? <Footprints size={12} /> : <Zap size={12} />}
            label="Mode"
          >
            <SegmentedToggle
              ariaLabel="Drive mode"
              value={quietMode ? "eco" : "sport"}
              options={[
                { label: "ECO", value: "eco" },
                { label: "Sport", value: "sport" },
              ]}
              onChange={(mode) => onQuietModeChange?.(mode === "eco")}
            />
          </SettingsToggleRow>

          <SettingsToggleRow
            icon={<ShieldAlert size={12} />}
            label="Assist"
          >
            <SegmentedToggle
              ariaLabel="Drive assist"
              value={driveAssistEnabled ? "on" : "off"}
              options={[
                { label: "OFF", value: "off" },
                { label: "ON", value: "on" },
              ]}
              onChange={(mode) => onDriveAssistChange?.(mode === "on")}
            />
          </SettingsToggleRow>

          <SettingsToggleRow icon={<Route size={12} />} label="Roam">
            <SegmentedToggle
              ariaLabel="Autonomous roam"
              value={navigationEnabled ? "on" : "off"}
              options={[
                { label: "OFF", value: "off" },
                { label: "ON", value: "on" },
              ]}
              onChange={(mode) => onNavigationChange?.(mode === "on")}
            />
          </SettingsToggleRow>

          <SettingsToggleRow
            icon={<Radar size={12} />}
            label="Map"
          >
            <SegmentedToggle
              ariaLabel="LiDAR map"
              value={lidarMinimapEnabled ? "on" : "off"}
              options={[
                { label: "OFF", value: "off" },
                { label: "ON", value: "on" },
              ]}
              onChange={(mode) => onLidarMinimapChange?.(mode === "on")}
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

          <DropdownMenu.Separator style={styles.separator} />

          <DropdownMenu.Item
            style={styles.menuItem}
            onSelect={() => onAction("telemetry")}
          >
            <BarChart3 size={14} /> <span>View telemetry</span>
          </DropdownMenu.Item>

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
      </DropdownMenu.Portal>
    </DropdownMenu.Root>
  );
};

const styles = {
  triggerWrapper: {
    outline: "none",
    display: "flex",
  },
  bareIcon: {
    color: "#00f2ff",
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
    border: "1px solid rgba(0, 242, 255, 0.2)",
    boxShadow: "0px 10px 38px -10px rgba(0, 0, 0, 0.5)",
    zIndex: 9999,
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
    border: "1px solid rgba(0, 242, 255, 0.2)",
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
    background: "rgba(0, 242, 255, 0.18)",
    color: "#00f2ff",
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
    background: "#00f2ff",
    color: "#000",
    border: "none",
    padding: "8px 16px",
    borderRadius: "4px",
    fontSize: "11px",
    fontWeight: "bold",
    cursor: "pointer",
  },
};
