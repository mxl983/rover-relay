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
  PawPrint,
  BarChart3,
} from "lucide-react";

export const SystemControls = ({
  isPowered,
  nvActive,
  resMode,
  focusMode,
  isCapturing,
  quietMode,
  powerSavingEnabled,
  onQuietModeChange,
  onPowerSavingChange,
  onNVToggle,
  onResChange,
  onFocusChange,
  onAction,
  controlMode,
  onControlModeChange,
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

          {/* Quiet (default) vs boost drive — does not affect speaker */}
          <DropdownMenu.Item
            style={styles.menuItem}
            onSelect={() => onQuietModeChange && onQuietModeChange(!quietMode)}
            title={
              quietMode
                ? "Switch to boost: full motor speed"
                : "Switch to quiet: slow driving (default)"
            }
          >
            {quietMode ? <Footprints size={14} /> : <Zap size={14} />}
            <span>
              Drive mode is {quietMode ? "Quiet" : "Boost"}
            </span>
          </DropdownMenu.Item>

          <DropdownMenu.Separator style={styles.separator} />

          <DropdownMenu.Item
            style={styles.menuItem}
            onSelect={() =>
              onPowerSavingChange && onPowerSavingChange(!powerSavingEnabled)
            }
            title={
              powerSavingEnabled
                ? "Power saving currently on; click to disable idle shutdown for this session."
                : "Power saving currently off; click to re-enable idle shutdown."
            }
          >
            <Power size={14} />
            <span>
              Power saving is {powerSavingEnabled ? "ON" : "OFF"}
            </span>
          </DropdownMenu.Item>

          <DropdownMenu.Separator style={styles.separator} />

          <DropdownMenu.Label style={styles.menuLabel}>
            Drive input
          </DropdownMenu.Label>
          <DropdownMenu.RadioGroup
            value={controlMode}
            onValueChange={onControlModeChange}
          >
            <DropdownMenu.RadioItem
              style={styles.menuItem}
              value="keyboard"
            >
              <Keyboard size={14} />
              <span>Keyboard</span>
              <DropdownMenu.ItemIndicator style={{ marginLeft: "auto" }}>
                <Check size={12} color="#00f2ff" />
              </DropdownMenu.ItemIndicator>
            </DropdownMenu.RadioItem>
            <DropdownMenu.RadioItem
              style={styles.menuItem}
              value="joystick"
            >
              <Gamepad2 size={14} />
              <span>Joystick</span>
              <DropdownMenu.ItemIndicator style={{ marginLeft: "auto" }}>
                <Check size={12} color="#00f2ff" />
              </DropdownMenu.ItemIndicator>
            </DropdownMenu.RadioItem>
          </DropdownMenu.RadioGroup>

          <DropdownMenu.Separator style={styles.separator} />

          <DropdownMenu.Item
            style={styles.menuItem}
            onSelect={() => onAction("meow")}
          >
            <PawPrint size={14} /> <span>Meow</span>
          </DropdownMenu.Item>

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
    minWidth: "180px",
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
