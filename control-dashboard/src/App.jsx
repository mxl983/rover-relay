import { useEffect, useState, useRef } from "react";
import { VideoStream } from "./components/VideoStream";
import { ControlCluster } from "./components/ControlCluster";
import {
  PI_SYSTEM_ENDPOINT,
  PI_CAMERA_ENDPOINT,
  PI_HI_RES_CAPTURE_ENDPOINT,
  BACKUP_STREAM_ENDPOINT,
  CAMERA_SECRET,
  VOICE_DRIVE_DEBUG,
  ROVER_STATE_ENDPOINT,
  getRelayRoverHeartbeatWebSocketUrl,
} from "./config";
import { LoginOverlay } from "./components/LoginOverlay";
import { SystemControls } from "./components/SystemControls";
import { WifiSignal } from "./components/WifiSignal";
import { DriveAssistHUD } from "./components/DriveAssistHUD";
import { RoverSchematic } from "./components/RoverSchematic";
import { FullscreenButton } from "./components/FullscreenButton";
import { DualJoystickControls } from "./components/JoystickControlCluster";
import { MouseGimbalLayer } from "./components/MouseGimbalLayer";
import { MobileTouchGimbalLayer } from "./components/MobileTouchGimbalLayer";
import { AssistantPanel } from "./components/AssistantPanel";
import { useIsMobile, getIsMobileSnapshot } from "./hooks/useIsMobile";
import { useFullscreen } from "./hooks/useFullscreen";
import { usePiWebSocket } from "./hooks/usePiWebSocket";
import { useMqtt } from "./hooks/useMqtt";
import { useVoiceAssistant } from "./hooks/useVoiceAssistant";
import { useRoverSession } from "./context/RoverSessionContext";
import { apiPostJson, apiPost, apiFetch } from "./api/client";
import { isAllowedCaptureUrl } from "./api/capture";

/** Set true to show the floating voice-assistant panel again. */
const SHOW_ASSISTANT_AGENT_UI = false;

/** Voice/LLM gimbal-only sequences (nod, shake): used to center cam before & after. */
function isGimbalOnlyAssistantSequence(steps) {
  return (
    Array.isArray(steps) &&
    steps.length > 0 &&
    steps.every(
      (s) =>
        s?.type === "control" &&
        s.payload &&
        !s.payload.drive &&
        !s.payload.command &&
        s.payload.gimbal,
    )
  );
}

const GIMBAL_HOME_SETTLE_MS = 600;

const CONTROL_MODE_STORAGE_KEY = "rover-dashboard-control-mode";

function readInitialControlMode() {
  if (typeof window === "undefined") return "keyboard";
  try {
    const v = window.localStorage.getItem(CONTROL_MODE_STORAGE_KEY);
    if (v === "keyboard" || v === "joystick") return v;
  } catch {
    /* ignore */
  }
  return getIsMobileSnapshot() ? "joystick" : "keyboard";
}

function formatRemainingTime(minutes) {
  if (!Number.isFinite(minutes) || minutes <= 0) return "an unknown amount of time";
  const roundedMinutes = Math.max(1, Math.round(minutes));
  if (roundedMinutes < 60) return `about ${roundedMinutes} minute${roundedMinutes === 1 ? "" : "s"}`;
  const hours = Math.floor(roundedMinutes / 60);
  const mins = roundedMinutes % 60;
  if (mins === 0) return `about ${hours} hour${hours === 1 ? "" : "s"}`;
  return `about ${hours} hour${hours === 1 ? "" : "s"} ${mins} minute${mins === 1 ? "" : "s"}`;
}

export default function App() {
  const { isAuthenticated, sessionCreds, login } = useRoverSession();
  const { stats, isOnline: piOnline, hasEverConnected, sendControl } = usePiWebSocket();
  const { isEspOnline, mqttClientRef } = useMqtt(
    isAuthenticated ? sessionCreds : null,
  );

  const [isPowered, setIsPowered] = useState(true);
  const [nvActive, setNvActive] = useState(false);
  const [isCapturing, setIsCapturing] = useState(false);
  const [resMode, setResMode] = useState("720p");
  const [focusMode, setFocusMode] = useState("far");
  const [controlMode, setControlModeState] = useState(readInitialControlMode);
  const [showBackupView, setShowBackupView] = useState(false);

  const setControlMode = (mode) => {
    if (mode !== "keyboard" && mode !== "joystick") return;
    setControlModeState(mode);
    try {
      window.localStorage.setItem(CONTROL_MODE_STORAGE_KEY, mode);
    } catch {
      /* ignore */
    }
  };
  const [actionError, setActionError] = useState(null);
  const [actionToast, setActionToast] = useState(null);
  const [, setSystemLoading] = useState(false);
  const [, setCameraLoading] = useState(false);
  const [isPointerLocked, setIsPointerLocked] = useState(false);
  const [videoStreamReady, setVideoStreamReady] = useState(false);
  const [relayCharging, setRelayCharging] = useState(null);
  const [relayBatteryPct, setRelayBatteryPct] = useState(null);
  const [relayTemperatureC, setRelayTemperatureC] = useState(null);
  const [relayBatteryMinutesRemaining, setRelayBatteryMinutesRemaining] = useState(null);
  const [powerSavingEnabled, setPowerSavingEnabled] = useState(true);
  const [lowBatteryGlowArmed, setLowBatteryGlowArmed] = useState(false);

  useEffect(() => {
    let cancelled = false;
    let ws = null;
    let reconnectTimer = null;

    const connect = () => {
      if (cancelled) return;
      const url = getRelayRoverHeartbeatWebSocketUrl(showBackupView);
      try {
        ws = new WebSocket(url);
      } catch {
        reconnectTimer = setTimeout(connect, 2500);
        return;
      }

      ws.onmessage = (ev) => {
        try {
          const msg = JSON.parse(ev.data);
          if (msg.type === "relay.rover.heartbeat" && msg.success && msg.rover?.charging) {
            setRelayCharging(msg.rover.charging.isCharging === true);
          }
        } catch {
          /* ignore */
        }
      };

      ws.onerror = () => {
        try {
          ws?.close();
        } catch {
          /* ignore */
        }
      };

      ws.onclose = () => {
        if (cancelled) return;
        reconnectTimer = setTimeout(connect, 2500);
      };
    };

    connect();

    return () => {
      cancelled = true;
      if (reconnectTimer) clearTimeout(reconnectTimer);
      try {
        ws?.close();
      } catch {
        /* ignore */
      }
    };
  }, [showBackupView]);

  useEffect(() => {
    let stopped = false;
    let timer = null;
    const nextPollDelayMs = videoStreamReady ? 10000 : 1000;

    const pollRoverState = async () => {
      try {
        const res = await apiFetch(ROVER_STATE_ENDPOINT, { timeout: 2500, retries: 0 });
        if (!res.ok) return;
        const json = await res.json();
        const rover = json?.rover || {};
        if (!stopped) {
          const pct = Number(rover?.battery?.currentPct);
          setRelayBatteryPct(Number.isFinite(pct) ? pct : null);
          const minsRemaining = Number(rover?.battery?.estimatedMinutesRemainingActiveVideo);
          setRelayBatteryMinutesRemaining(Number.isFinite(minsRemaining) ? minsRemaining : null);
          const tempC = Number(rover?.environment?.temperatureC);
          setRelayTemperatureC(Number.isFinite(tempC) ? tempC : null);
        }
      } catch {
        if (!stopped) {
          setRelayBatteryMinutesRemaining(null);
        }
      } finally {
        if (!stopped) timer = setTimeout(pollRoverState, nextPollDelayMs);
      }
    };

    pollRoverState();
    return () => {
      stopped = true;
      if (timer) clearTimeout(timer);
    };
  }, [videoStreamReady]);

  useEffect(() => {
    if (!isAuthenticated) return;
    let cancelled = false;

    const fetchPowerSaving = async () => {
      try {
        const res = await apiFetch(`${PI_SYSTEM_ENDPOINT}/power-saving`, {
          timeout: 2500,
          retries: 0,
        });
        if (!res.ok) return;
        const json = await res.json();
        if (!cancelled && typeof json?.enabled === "boolean") {
          setPowerSavingEnabled(json.enabled);
        }
      } catch {
        // Keep the existing value when fetch fails.
      }
    };

    void fetchPowerSaving();
    return () => {
      cancelled = true;
    };
  }, [isAuthenticated]);

  useEffect(() => {
    const timer = setTimeout(() => setLowBatteryGlowArmed(true), 5000);
    return () => clearTimeout(timer);
  }, []);

  // Realtime Pi WebSocket health (voltage → %) takes priority; relay /state poll is fallback only.
  const batteryPct = Number.isFinite(Number(stats?.battery))
    ? Number(stats.battery)
    : Number.isFinite(Number(relayBatteryPct))
      ? Number(relayBatteryPct)
      : null;
  const isLowBattery = Number.isFinite(batteryPct) && batteryPct < 20;
  // Relay WebSocket `relay.rover.heartbeat` is the charging source of truth.
  const effectiveIsCharging = relayCharging === true;
  const viewportGlowClass = effectiveIsCharging
    ? "status-glow-charging"
    : lowBatteryGlowArmed && isLowBattery
      ? "status-glow-low-battery"
      : "";

  const isMobile = useIsMobile();
  const isFullscreen = useFullscreen();
  const viewportRef = useRef(null);
  const lastDriveRef = useRef({ x: 0, y: 0 });
  const pendingControlRef = useRef(null);
  const controlTimerRef = useRef(null);
  const mountedAtRef = useRef(Date.now());

  const CONTROL_INTERVAL_WS_MS = 16; // ~60Hz for low-latency websocket control

  useEffect(() => {
    setIsPowered(piOnline);
  }, [piOnline]);

  const clearError = () => setActionError(null);
  const clearErrorIfAny = () => setActionError((prev) => (prev ? null : prev));

  const showActionToast = (message) => {
    setActionToast(message);
  };

  useEffect(() => {
    if (!actionToast) return undefined;
    const timer = setTimeout(() => setActionToast(null), 2000);
    return () => clearTimeout(timer);
  }, [actionToast]);

  const sendControlNow = (payload) => {
    if (piOnline && sendControl) {
      sendControl(payload);
      return Promise.resolve();
    }
    const startupGraceActive = Date.now() - mountedAtRef.current < 15000;
    if (hasEverConnected || !startupGraceActive) {
      setActionError("Control channel offline (WebSocket reconnecting)");
    }
    return Promise.resolve();
  };

  const flushPendingControl = () => {
    controlTimerRef.current = null;
    const payload = pendingControlRef.current;
    if (!payload) return;
    pendingControlRef.current = null;
    void sendControlNow(payload);
  };

  const queueControl = (patch) => {
    const prev = pendingControlRef.current ?? {};
    pendingControlRef.current = { ...prev, ...patch };
    if (controlTimerRef.current != null) return;
    controlTimerRef.current = setTimeout(flushPendingControl, CONTROL_INTERVAL_WS_MS);
  };

  useEffect(
    () => () => {
      if (controlTimerRef.current != null) {
        clearTimeout(controlTimerRef.current);
        controlTimerRef.current = null;
      }
    },
    [],
  );

  /** Avoid stuck drive/gimbal when swapping input surfaces. */
  useEffect(() => {
    if (!isAuthenticated || !piOnline) return;
    void sendControlNow({ drive: { x: 0, y: 0 }, gimbal: { x: 0, y: 0 } });
  }, [controlMode, isAuthenticated, piOnline]);

  const handleDriveUpdate = (payload) => {
    clearErrorIfAny();
    if (Array.isArray(payload)) {
      // Keyboard control arrays are sparse and should remain immediate.
      void sendControlNow(payload);
      return;
    }
    if (typeof payload === "object" && payload?.drive != null) {
      lastDriveRef.current = payload.drive;
    }
    if (typeof payload === "object" && payload) {
      queueControl(payload);
    }
  };

  const handleGimbalUpdate = (gimbal) => {
    clearErrorIfAny();
    queueControl({ gimbal });
  };

  const handleLoginSuccess = (_client, creds) => {
    setActionError(null);
    login(creds);
  };

  const handleSystemAction = async (type) => {
    // 1. Intercept Boot
    if (type === "boot") {
      mqttClientRef.current?.publish("rover/power/pi", "On", { qos: 1 });
      mqttClientRef.current?.publish("rover/power/aux", "On", { qos: 1 });
      setIsPowered(true);
      return;
    }

    // 2. Intercept Capture
    if (type === "capture") {
      await handleCapture(); // Divert to your specific capture logic
      return;
    }

    // 3. Rover sound action over control channel.
    if (type === "meow") {
      await sendControlNow({ command: "meow" });
      return;
    }

    // 4. Handle generic system commands (Reboot/Shutdown)
    if (!window.confirm(`Confirm ${type}?`)) return;

    setSystemLoading(true);
    setActionError(null);
    try {
      const endpoint = `${PI_SYSTEM_ENDPOINT}/${type}`;
      await apiPostJson(endpoint, {});

      if (type === "shutdown") {
        mqttClientRef.current?.publish("rover/power/pi", "Off 15000", {
          qos: 1,
        });
        setIsPowered(false);
      }
    } catch (err) {
      setActionError(err.message ?? `System ${type} failed`);
    } finally {
      setSystemLoading(false);
    }
  };

  const handleNVToggle = async (requestedState) => {
    setCameraLoading(true);
    setActionError(null);
    try {
      await apiPostJson(`${PI_CAMERA_ENDPOINT}/nightvision`, {
        active: requestedState,
        ...(CAMERA_SECRET ? { secret: CAMERA_SECRET } : {}),
      });
      setNvActive(requestedState);
      showActionToast(`Night mode ${requestedState ? "enabled" : "disabled"}`);
    } catch (err) {
      setActionError(err.message ?? "Night vision toggle failed");
    } finally {
      setCameraLoading(false);
    }
  };

  const handleResChange = async (newMode) => {
    setCameraLoading(true);
    setActionError(null);
    try {
      await apiPostJson(`${PI_CAMERA_ENDPOINT}/resolution`, {
        mode: newMode,
        ...(CAMERA_SECRET ? { secret: CAMERA_SECRET } : {}),
      });
      setResMode(newMode);
      showActionToast(`Resolution set to ${newMode.toUpperCase()}`);
    } catch (err) {
      setActionError(err.message ?? "Resolution change failed");
    } finally {
      setCameraLoading(false);
    }
  };

  const handleFocusChange = async (newMode) => {
    setCameraLoading(true);
    setActionError(null);
    try {
      await apiPostJson(`${PI_CAMERA_ENDPOINT}/focus`, {
        mode: newMode,
        ...(CAMERA_SECRET ? { secret: CAMERA_SECRET } : {}),
      });
      setFocusMode(newMode);
    } catch (err) {
      setActionError(err.message ?? "Focus change failed");
    } finally {
      setCameraLoading(false);
    }
  };

  const toggleLight = async (state) => {
    setActionError(null);
    try {
      await apiPostJson(`${PI_SYSTEM_ENDPOINT}/usb-power`, { action: state });
      showActionToast(`Headlight ${state === "on" ? "enabled" : "disabled"}`);
    } catch (err) {
      setActionError(err.message ?? "Light toggle failed");
    }
  };

  const setQuietMode = async (enabled) => {
    setActionError(null);
    try {
      await apiPostJson(`${PI_SYSTEM_ENDPOINT}/quiet-mode`, { enabled });
      showActionToast(`Quiet mode ${enabled ? "enabled" : "disabled"}`);
    } catch (err) {
      setActionError(err.message ?? "Drive mode update failed");
    }
  };

  const setPowerSaving = async (enabled) => {
    if (!enabled && powerSavingEnabled) {
      const estimated = formatRemainingTime(relayBatteryMinutesRemaining);
      const confirmed = window.confirm(
        `Disabling power saving may cause rover to run out of battery in ${estimated}.\n\nDo you want to continue?`,
      );
      if (!confirmed) return;
    }

    setActionError(null);
    try {
      await apiPostJson(`${PI_SYSTEM_ENDPOINT}/power-saving`, { enabled });
      setPowerSavingEnabled(enabled);
      showActionToast(`Power saving ${enabled ? "enabled" : "disabled"}`);
    } catch (err) {
      setActionError(err.message ?? "Power-saving update failed");
    }
  };

  const handleCapture = async () => {
    setIsCapturing(true);
    setActionError(null);
    try {
      const data = await apiPost(PI_HI_RES_CAPTURE_ENDPOINT);
      const url = data?.url;
      if (url && isAllowedCaptureUrl(url)) {
        window.open(url, "_blank", "noopener,noreferrer");
      } else if (url) {
        setActionError("Invalid capture URL");
      } else {
        setActionError("No capture URL returned");
      }
    } catch (err) {
      setActionError(err.message ?? "Capture failed");
    } finally {
      setIsCapturing(false);
    }
  };

  const handleCameraReset = async () => {
    setActionError(null);
    await sendControlNow({ command: "reset_servos" });
  };

  const handleLookDown = async () => {
    setActionError(null);
    await sendControlNow({ command: "look_down" });
  };

  const handleQuickTurn = async (dir) => {
    setActionError(null);
    const command =
      dir === "L" ? "turn_left_90_slow" : "turn_right_90_slow";
    await sendControlNow({ command });
  };

  const handleLaserToggle = async () => {
    setActionError(null);
    await sendControlNow({ command: "toggle_laser" });
    showActionToast(`Laser ${stats.laserOn ? "disabled" : "enabled"}`);
  };

  const handleFeederTreat = async () => {
    setActionError(null);
    await sendControlNow({ command: "feeder_treat" });
    showActionToast("Treat");
  };

  const handleToggleBackupView = () => {
    setShowBackupView((prev) => !prev);
  };

  const runAssistantAction = async (action) => {
    if (!action || typeof action !== "object") return;
    if (VOICE_DRIVE_DEBUG) {
      // eslint-disable-next-line no-console
      console.debug("[voice→drive] assistant action", action);
    }
    const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

    if (action.type === "sequence" && Array.isArray(action.actions)) {
      const steps = action.actions.slice(0, 10);
      const gimbalOnly = isGimbalOnlyAssistantSequence(steps);
      if (gimbalOnly) {
        await sendControlNow({ gimbal: { x: 0, y: 0 } });
        await sleep(GIMBAL_HOME_SETTLE_MS);
      }
      for (let i = 0; i < steps.length; i += 1) {
        // Execute in-order for compound command reliability.
        // eslint-disable-next-line no-await-in-loop
        await runAssistantAction(steps[i]);
        if (i < steps.length - 1) {
          if (gimbalOnly) {
            // Gimbal gestures are hold-based; avoid injecting drive-stop and keep a gentler cadence.
            // eslint-disable-next-line no-await-in-loop
            await sleep(220);
          } else {
            // Brief full stop between drive stages so timed segments do not blend into a curve.
            // eslint-disable-next-line no-await-in-loop
            await sendControlNow({ drive: { x: 0, y: 0 } });
            // eslint-disable-next-line no-await-in-loop
            await sleep(120);
          }
        }
      }
      if (gimbalOnly) {
        await sendControlNow({ gimbal: { x: 0, y: 0 } });
        await sleep(GIMBAL_HOME_SETTLE_MS);
      }
      return;
    }

    if (action.type === "control") {
      const payload = action.payload;
      if (!payload) return;
      if (VOICE_DRIVE_DEBUG) {
        // eslint-disable-next-line no-console
        console.debug("[voice→drive] sendControl payload", payload);
      }
      await sendControlNow(payload);
      if (action.durationMs && !payload.command) {
        if (payload.drive) {
          await sleep(action.durationMs);
          await sendControlNow({ drive: { x: 0, y: 0 } });
        } else if (payload.gimbal) {
          await sleep(action.durationMs);
        }
      }
      return;
    }
    if (action.type === "usb_power" && (action.action === "on" || action.action === "off")) {
      await toggleLight(action.action);
      return;
    }
    if (action.type === "camera") {
      if (action.action === "capture") {
        await handleCapture();
        return;
      }
      if (action.action === "nightvision" && typeof action.active === "boolean") {
        await handleNVToggle(action.active);
        return;
      }
      if (action.action === "focus" && action.mode) {
        await handleFocusChange(action.mode);
        return;
      }
      if (action.action === "resolution" && action.mode) {
        await handleResChange(action.mode);
        return;
      }
      return;
    }
    if (action.type === "quiet_mode" && typeof action.enabled === "boolean") {
      await setQuietMode(action.enabled);
    }
  };

  const {
    isSupported: voiceSupported,
    isListening: voiceListening,
    isLiveMode: voiceLiveMode,
    isThinking: voiceThinking,
    lastTranscript,
    assistantReply,
    voiceError,
    startListening: startVoice,
    stopListening: stopVoice,
    setLiveMode: setVoiceLiveMode,
    sendText: sendVoiceText,
  } = useVoiceAssistant({ onAction: runAssistantAction });

  return (
    <div
      className={`viewport${isPointerLocked ? " viewport-mouse-look" : ""}${viewportGlowClass ? ` ${viewportGlowClass}` : ""}`}
      ref={viewportRef}
    >
      <div className="status-glow-layer" aria-hidden="true" />
      <ActionErrorBanner message={actionError} onDismiss={clearError} />
      <ActionToast message={actionToast} />
      {SHOW_ASSISTANT_AGENT_UI && (
        <AssistantPanel
          videoStreamReady={videoStreamReady}
          voiceSupported={voiceSupported}
          isListening={voiceListening}
          isLiveMode={voiceLiveMode}
          isThinking={voiceThinking}
          transcript={lastTranscript}
          reply={assistantReply}
          error={voiceError}
          onSendText={sendVoiceText}
          onSetLiveMode={setVoiceLiveMode}
        />
      )}

      {!isAuthenticated && (
        <LoginOverlay onLoginSuccess={handleLoginSuccess} />
      )}

      <VideoStream
        dockingData={stats.docking}
        onVideoReadyChange={setVideoStreamReady}
        controlChannelReady={piOnline}
        backupStreamUrl={BACKUP_STREAM_ENDPOINT}
        showBackupView={showBackupView}
      />
      <DriveAssistHUD pan={stats.pan} tilt={stats.tilt} />

      {isAuthenticated && isMobile && (
        <MobileTouchGimbalLayer
          onGimbal={handleGimbalUpdate}
        />
      )}

      {isAuthenticated && isFullscreen && !isMobile && (
        <MouseGimbalLayer
          viewportRef={viewportRef}
          isFullscreen={isFullscreen}
          isPointerLocked={isPointerLocked}
          onPointerLockChange={setIsPointerLocked}
          onDrive={handleDriveUpdate}
          lastDriveRef={lastDriveRef}
        />
      )}

      {isAuthenticated && (
        <div className="hud-overlay">
          <HudHeader
            wifiSignal={stats?.wifiSignal}
            isPowered={isPowered}
            nvActive={nvActive}
            resMode={resMode}
            isCapturing={isCapturing}
            focusMode={focusMode}
            quietMode={stats?.quietMode}
            powerSavingEnabled={powerSavingEnabled}
            onQuietModeChange={setQuietMode}
            onPowerSavingChange={setPowerSaving}
            onNVToggle={handleNVToggle}
            onResChange={handleResChange}
            onAction={handleSystemAction}
            onFocusChange={handleFocusChange}
            controlMode={controlMode}
            onControlModeChange={setControlMode}
          />

          <HudFooter
            isMobile={isMobile}
            controlMode={controlMode}
            stats={stats}
            batteryPct={batteryPct}
            isCharging={effectiveIsCharging}
            ambientTemperatureC={relayTemperatureC}
            piOnline={piOnline}
            isEspOnline={isEspOnline}
            onDrive={handleDriveUpdate}
            onResetCamera={handleCameraReset}
            onLookDown={handleLookDown}
            onTurnLeft={() => handleQuickTurn("L")}
            onTurnRight={() => handleQuickTurn("R")}
            onLaserToggle={handleLaserToggle}
            laserOn={stats.laserOn}
            onVoiceStart={startVoice}
            onVoiceStop={stopVoice}
            voiceSupported={voiceSupported}
            voiceListening={voiceListening}
            onToggleLight={toggleLight}
            onCapture={handleCapture}
            isCapturing={isCapturing}
            onToggleBackupView={handleToggleBackupView}
            backupViewEnabled={showBackupView}
            onFeederTreat={handleFeederTreat}
          />
        </div>
      )}
    </div>
  );
}

function ActionErrorBanner({ message, onDismiss }) {
  if (!message) return null;
  return (
    <div className="glass-card action-error-banner" role="alert">
      <span>{message}</span>
      <button
        type="button"
        className="hud-dismiss"
        onClick={onDismiss}
        aria-label="Dismiss"
      >
        ×
      </button>
    </div>
  );
}

function ActionToast({ message }) {
  if (!message) return null;
  return (
    <div className="glass-card action-toast" role="status" aria-live="polite">
      <span>{message}</span>
    </div>
  );
}

function HudHeader({
  wifiSignal,
  isPowered,
  nvActive,
  resMode,
  isCapturing,
  focusMode,
  quietMode,
  powerSavingEnabled,
  onQuietModeChange,
  onPowerSavingChange,
  onNVToggle,
  onResChange,
  onAction,
  onFocusChange,
  controlMode,
  onControlModeChange,
}) {
  return (
    <div className="hud-header">
      <div className="glass-card hud-header-brand">
        <div>Mango Mate</div>
        {wifiSignal && <WifiSignal dbm={wifiSignal} />}
      </div>
      <div className="glass-card hud-header-actions">
        <SystemControls
          isPowered={isPowered}
          nvActive={nvActive}
          resMode={resMode}
          isCapturing={isCapturing}
          quietMode={quietMode}
          powerSavingEnabled={powerSavingEnabled}
          onQuietModeChange={onQuietModeChange}
          onPowerSavingChange={onPowerSavingChange}
          onNVToggle={onNVToggle}
          onResChange={onResChange}
          onAction={onAction}
          focusMode={focusMode}
          onFocusChange={onFocusChange}
          controlMode={controlMode}
          onControlModeChange={onControlModeChange}
        />
        <FullscreenButton />
      </div>
    </div>
  );
}

function HudFooter({
  isMobile,
  controlMode,
  stats,
  batteryPct,
  isCharging,
  ambientTemperatureC,
  piOnline,
  isEspOnline,
  onDrive,
  onResetCamera,
  onLookDown,
  onTurnLeft,
  onTurnRight,
  onLaserToggle,
  laserOn,
  onVoiceStart,
  onVoiceStop,
  voiceSupported,
  voiceListening,
  onToggleLight,
  onCapture,
  isCapturing,
  onToggleBackupView,
  backupViewEnabled,
  onFeederTreat,
}) {
  const joystickProps = {
    onDrive,
    onReset: onResetCamera,
    onLookDown,
    onTurnLeft,
    onTurnRight,
    onLaserToggle,
    laserOn,
    onVoiceStart,
    onVoiceStop,
    voiceSupported,
    voiceListening,
    onHeadlightToggle: () => {
      const nextState = stats.usbPower === "on" ? "off" : "on";
      onToggleLight(nextState);
    },
    headlightOn: stats.usbPower === "on",
    onToggleBackupView,
    backupViewEnabled,
    onTreat: onFeederTreat,
  };

  const schematic = (
    <RoverSchematic
      pan={stats.pan}
      battery={batteryPct}
      cpuTemp={stats.cpuTemp}
      latencyMs={stats.latency}
      throttle={stats.throttle}
      isOffline={!piOnline}
      isCharging={isCharging}
      ambientTempC={ambientTemperatureC}
    />
  );

  return (
    <div className="hud-footer">
      {!isMobile && controlMode === "keyboard" && schematic}

      {isMobile && controlMode === "joystick" && (
        <DualJoystickControls {...joystickProps}>{schematic}</DualJoystickControls>
      )}

      {isMobile && controlMode === "keyboard" && schematic}

      <div className="footer-controls">
        {piOnline ? (
          <>
            {!isMobile && controlMode === "keyboard" && (
              <ControlCluster
                onDrive={onDrive}
                usbPower={stats.usbPower}
                laserOn={laserOn}
                onVoiceStart={onVoiceStart}
                onVoiceStop={onVoiceStop}
                voiceSupported={voiceSupported}
                voiceListening={voiceListening}
                onLightToggle={() => {
                  const nextState =
                    stats.usbPower === "on" ? "off" : "on";
                  onToggleLight(nextState);
                }}
                onLaserToggle={onLaserToggle}
                onCapture={onCapture}
                isCapturing={isCapturing}
                onReset={onResetCamera}
                onToggleBackupView={onToggleBackupView}
                backupViewEnabled={backupViewEnabled}
                onTreat={onFeederTreat}
              />
            )}
            {!isMobile && controlMode === "joystick" && (
              <DualJoystickControls {...joystickProps}>
                {schematic}
              </DualJoystickControls>
            )}

            {isMobile && controlMode === "keyboard" && (
              <ControlCluster
                onDrive={onDrive}
                usbPower={stats.usbPower}
                laserOn={laserOn}
                onVoiceStart={onVoiceStart}
                onVoiceStop={onVoiceStop}
                voiceSupported={voiceSupported}
                voiceListening={voiceListening}
                onLightToggle={() => {
                  const nextState =
                    stats.usbPower === "on" ? "off" : "on";
                  onToggleLight(nextState);
                }}
                onLaserToggle={onLaserToggle}
                onCapture={onCapture}
                isCapturing={isCapturing}
                onReset={onResetCamera}
                onToggleBackupView={onToggleBackupView}
                backupViewEnabled={backupViewEnabled}
                onTreat={onFeederTreat}
              />
            )}
          </>
        ) : null}
      </div>
    </div>
  );
}

