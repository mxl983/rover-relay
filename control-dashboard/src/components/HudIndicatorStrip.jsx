import React from "react";
import {
  BatteryCharging,
  BatteryLow,
  Clock,
  EthernetPort,
  Laptop,
  Monitor,
  Radar,
  Smartphone,
  Tablet,
  TriangleAlert,
  WifiOff,
  Zap,
} from "lucide-react";
import {
  isDriveAssistHudActive,
  readDriveAssistClosestRangeM,
} from "../utils/driveAssistApi.js";
import { isWifiWeak } from "../utils/wifiSignal.js";
import { isHighLatency } from "../utils/latencySignal.js";

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

function formatTtl(ttlMs) {
  if (ttlMs == null || !Number.isFinite(Number(ttlMs))) return null;
  const totalSec = Math.max(0, Math.ceil(Number(ttlMs) / 1000));
  const m = Math.floor(totalSec / 60);
  const s = totalSec % 60;
  return `${m}:${String(s).padStart(2, "0")}`;
}

function PowerSavingIndicator({ enabled, ttlMs = null }) {
  if (!enabled) return null;
  const ttlLabel = formatTtl(ttlMs);

  return (
    <div
      className="hud-indicator-slot hud-indicator-slot--power-saving hud-indicator-slot--active"
      role="status"
      aria-label={
        ttlLabel
          ? `Idle shutdown in ${ttlLabel}`
          : "Idle shutdown enabled"
      }
      title={
        ttlLabel
          ? `Power-saving TTL ${ttlLabel}`
          : "Idle shutdown enabled"
      }
    >
      <IndicatorIcon toneClass="hud-indicator-icon-wrap--power-saving">
        <Clock
          className="hud-indicator-icon hud-indicator-icon--power-saving"
          {...indicatorIconProps}
        />
      </IndicatorIcon>
      {ttlLabel ? (
        <span className="hud-indicator-ttl" aria-hidden>
          {ttlLabel}
        </span>
      ) : null}
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
      aria-label={enabled ? "Pre-collision stop on" : undefined}
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

  const label = `Wi-Fi signal cut (${Math.round(val)} dBm)`;

  return (
    <div
      className="hud-indicator-slot hud-indicator-slot--weak-wifi hud-indicator-slot--active"
      role="status"
      aria-live="assertive"
      aria-label={label}
      title={label}
    >
      <IndicatorIcon toneClass="hud-indicator-icon-wrap--weak-wifi">
        <WifiOff
          className="hud-indicator-icon hud-indicator-icon--weak-wifi"
          {...indicatorIconProps}
        />
      </IndicatorIcon>
    </div>
  );
}

function HighLatencyIndicator({ latencyMs }) {
  const val = Number(latencyMs);
  if (!Number.isFinite(val) || !isHighLatency(val)) return null;

  const label = `High latency — drive carefully (${Math.round(val)} ms)`;

  return (
    <div
      className="hud-indicator-slot hud-indicator-slot--high-latency hud-indicator-slot--active"
      role="status"
      aria-live="assertive"
      aria-label={label}
      title={label}
    >
      <IndicatorIcon toneClass="hud-indicator-icon-wrap--high-latency">
        <EthernetPort
          className="hud-indicator-icon hud-indicator-icon--high-latency"
          {...indicatorIconProps}
        />
      </IndicatorIcon>
    </div>
  );
}

function presenceClientIcon(label) {
  const s = String(label || "").toLowerCase();
  if (s.includes("iphone") || s.includes("android")) return Smartphone;
  if (s.includes("ipad")) return Tablet;
  if (s.includes("mac") || s.includes("windows") || s.includes("linux") || s.includes("chrome")) {
    return Laptop;
  }
  return Monitor;
}

function PresenceIndicator({ presence = null }) {
  const count = Number(presence?.count);
  const clients = Array.isArray(presence?.clients) ? presence.clients : [];
  if (!Number.isFinite(count) || count < 1) return null;

  const entries =
    clients.length > 0
      ? clients.slice(0, 8).map((c, i) => ({
          id: String(c?.id || `viewer-${i}`),
          label: String(c?.label || "Browser").trim() || "Browser",
        }))
      : Array.from({ length: Math.min(count, 8) }, (_, i) => ({
          id: `viewer-${i}`,
          label: "Browser",
        }));

  const summary = entries.map((e) => e.label).join(", ");
  const groupAria =
    entries.length === 1
      ? `1 viewer online · ${summary}`
      : `${entries.length} viewers online · ${summary}`;

  return (
    <div
      className="hud-indicator-presence-group"
      role="status"
      aria-live="polite"
      aria-label={groupAria}
      title={groupAria}
    >
      {entries.map((client) => {
        const Icon = presenceClientIcon(client.label);
        return (
          <div
            key={client.id}
            className="hud-indicator-slot hud-indicator-slot--presence hud-indicator-slot--active"
            aria-hidden
          >
            <IndicatorIcon toneClass="hud-indicator-icon-wrap--presence">
              <Icon
                className="hud-indicator-icon hud-indicator-icon--presence"
                {...indicatorIconProps}
              />
            </IndicatorIcon>
            <span className="hud-indicator-presence-label">{client.label}</span>
          </div>
        );
      })}
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
  powerSavingTtlMs = null,
  quietMode = true,
  isCharging = false,
  isLowBattery = false,
  lowBatteryIndicatorArmed = false,
  wifiSignal = null,
  latencyMs = null,
  presence = null,
}) {
  const sportModeEnabled = quietMode === false;
  const showCharging = isCharging;
  const showLowBattery = lowBatteryIndicatorArmed && isLowBattery && !showCharging;

  return (
    <div className="hud-indicator-strip" aria-label="Status indicators">
      <PresenceIndicator presence={presence} />
      <PowerSavingIndicator enabled={powerSavingEnabled} ttlMs={powerSavingTtlMs} />
      <SportModeIndicator enabled={sportModeEnabled} />
      <DriveAssistIndicator enabled={driveAssistEnabled} />
      <CollisionIndicator update={driveAssistUpdate} enabled={driveAssistEnabled} />
      <ChargingIndicator enabled={showCharging} />
      <LowBatteryIndicator enabled={showLowBattery} />
      <WeakWifiIndicator dbm={wifiSignal} />
      <HighLatencyIndicator latencyMs={latencyMs} />
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
