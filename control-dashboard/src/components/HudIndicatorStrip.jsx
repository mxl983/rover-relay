import React from "react";
import {
  BatteryCharging,
  BatteryLow,
  Clock,
  Radar,
  TriangleAlert,
  WifiLow,
  Zap,
} from "lucide-react";
import {
  isDriveAssistHudActive,
  readDriveAssistClosestRangeM,
} from "../utils/driveAssistApi.js";
import { isWifiWeak } from "../utils/wifiSignal.js";

const RESERVED_SLOT_COUNT = 0;
const INDICATOR_ICON_SIZE = 12;
const INDICATOR_ICON_STROKE = 2;
const indicatorIconProps = {
  size: INDICATOR_ICON_SIZE,
  strokeWidth: INDICATOR_ICON_STROKE,
};

function IndicatorIcon({ toneClass, children }) {
  return (
    <span className={`hud-indicator-icon-wrap ${toneClass}`}>
      {children}
    </span>
  );
}

function ChargingIndicator({ enabled }) {
  return (
    <div
      className={`hud-indicator-slot hud-indicator-slot--charging${
        enabled ? " hud-indicator-slot--active" : " hud-indicator-slot--reserved"
      }`}
      role={enabled ? "status" : undefined}
      aria-label={enabled ? "Charging" : undefined}
      aria-hidden={enabled ? undefined : true}
    >
      {enabled ? (
        <IndicatorIcon toneClass="hud-indicator-icon-wrap--charging">
          <BatteryCharging
            className="hud-indicator-icon hud-indicator-icon--charging"
            {...indicatorIconProps}
          />
        </IndicatorIcon>
      ) : null}
    </div>
  );
}

function LowBatteryIndicator({ enabled }) {
  return (
    <div
      className={`hud-indicator-slot hud-indicator-slot--low-battery${
        enabled ? " hud-indicator-slot--active" : " hud-indicator-slot--reserved"
      }`}
      role={enabled ? "status" : undefined}
      aria-live={enabled ? "assertive" : undefined}
      aria-label={enabled ? "Low battery" : undefined}
      aria-hidden={enabled ? undefined : true}
    >
      {enabled ? (
        <IndicatorIcon toneClass="hud-indicator-icon-wrap--low-battery">
          <BatteryLow
            className="hud-indicator-icon hud-indicator-icon--low-battery"
            {...indicatorIconProps}
          />
        </IndicatorIcon>
      ) : null}
    </div>
  );
}

function PowerSavingIndicator({ enabled }) {
  if (!enabled) return null;

  return (
    <div
      className="hud-indicator-slot hud-indicator-slot--power-saving"
      role="status"
      aria-label="Idle shutdown enabled"
    >
      <IndicatorIcon toneClass="hud-indicator-icon-wrap--power-saving">
        <Clock
          className="hud-indicator-icon hud-indicator-icon--power-saving"
          {...indicatorIconProps}
        />
      </IndicatorIcon>
    </div>
  );
}

function SportModeIndicator({ enabled }) {
  return (
    <div
      className={`hud-indicator-slot${
        enabled ? " hud-indicator-slot--sport" : " hud-indicator-slot--reserved"
      }`}
      role={enabled ? "status" : undefined}
      aria-label={enabled ? "Sport drive mode" : undefined}
      aria-hidden={enabled ? undefined : true}
    >
      {enabled ? (
        <IndicatorIcon toneClass="hud-indicator-icon-wrap--sport">
          <Zap
            className="hud-indicator-icon hud-indicator-icon--sport"
            {...indicatorIconProps}
          />
        </IndicatorIcon>
      ) : null}
    </div>
  );
}

function DriveAssistIndicator({ enabled }) {
  return (
    <div
      className={`hud-indicator-slot${
        enabled ? " hud-indicator-slot--drive-assist" : " hud-indicator-slot--reserved"
      }`}
      role={enabled ? "status" : undefined}
      aria-label={enabled ? "Drive assist on" : undefined}
      aria-hidden={enabled ? undefined : true}
    >
      {enabled ? (
        <IndicatorIcon toneClass="hud-indicator-icon-wrap--drive-assist">
          <Radar
            className="hud-indicator-icon hud-indicator-icon--drive-assist"
            {...indicatorIconProps}
          />
        </IndicatorIcon>
      ) : null}
    </div>
  );
}

function WeakWifiIndicator({ dbm }) {
  const val = Number(dbm);
  if (!Number.isFinite(val) || !isWifiWeak(val)) return null;

  const label = `Weak Wi-Fi signal (${Math.round(val)} dBm)`;

  return (
    <div
      className="hud-indicator-slot hud-indicator-slot--weak-wifi hud-indicator-slot--active"
      role="status"
      aria-live="assertive"
      aria-label={label}
      title={label}
    >
      <IndicatorIcon toneClass="hud-indicator-icon-wrap--weak-wifi">
        <WifiLow
          className="hud-indicator-icon hud-indicator-icon--weak-wifi"
          {...indicatorIconProps}
        />
      </IndicatorIcon>
    </div>
  );
}

function CollisionIndicator({ update, enabled }) {
  const active = enabled && isDriveAssistHudActive(update);
  const rangeM = active ? readDriveAssistClosestRangeM(update) : null;

  return (
    <div
      className={`hud-indicator-slot hud-indicator-slot--collision${
        active ? " hud-indicator-slot--active" : ""
      }`}
      role={active ? "status" : undefined}
      aria-live={active ? "assertive" : undefined}
      aria-label={
        active
          ? `Collision warning${rangeM != null ? `, ${rangeM.toFixed(2)} meters` : ""}`
          : undefined
      }
    >
      {active ? (
        <IndicatorIcon toneClass="hud-indicator-icon-wrap--collision">
          <TriangleAlert
            className="hud-indicator-icon hud-indicator-icon--collision"
            {...indicatorIconProps}
            aria-hidden
          />
        </IndicatorIcon>
      ) : null}
    </div>
  );
}

export function HudIndicatorStrip({
  driveAssistEnabled,
  driveAssistUpdate,
  powerSavingEnabled = false,
  quietMode = true,
  isCharging = false,
  isLowBattery = false,
  lowBatteryIndicatorArmed = false,
  wifiSignal = null,
}) {
  const sportModeEnabled = quietMode === false;
  const showCharging = isCharging;
  const showLowBattery = lowBatteryIndicatorArmed && isLowBattery && !showCharging;

  return (
    <div className="hud-indicator-strip" aria-label="Status indicators">
      <PowerSavingIndicator enabled={powerSavingEnabled} />
      <SportModeIndicator enabled={sportModeEnabled} />
      <DriveAssistIndicator enabled={driveAssistEnabled} />
      <CollisionIndicator update={driveAssistUpdate} enabled={driveAssistEnabled} />
      <ChargingIndicator enabled={showCharging} />
      <LowBatteryIndicator enabled={showLowBattery} />
      <WeakWifiIndicator dbm={wifiSignal} />
      {Array.from({ length: RESERVED_SLOT_COUNT }, (_, index) => (
        <div
          key={`reserved-${index}`}
          className="hud-indicator-slot hud-indicator-slot--reserved"
          aria-hidden
        />
      ))}
    </div>
  );
}
