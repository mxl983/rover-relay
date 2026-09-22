import { useEffect, useState, useRef } from "react";
import { VideoStream } from "./components/VideoStream";
import { KeyboardControlCluster } from "./components/KeyboardControlCluster";
import { LoginOverlay } from "./components/LoginOverlay";
import { SystemControls } from "./components/SystemControls";
import { GimbalTiltHud } from "./components/GimbalTiltHud";
import { RoverSchematic } from "./components/RoverSchematic";
import { FullscreenButton } from "./components/FullscreenButton";
import { DualJoystickControls } from "./components/DualJoystickControls";
import { MouseGimbalLayer } from "./components/MouseGimbalLayer";
import { MobileTouchGimbalLayer } from "./components/MobileTouchGimbalLayer";
import { AssistantPanel } from "./components/AssistantPanel";
import { BrandCatIcon } from "./components/BrandCatIcon";
import { HudIndicatorStrip } from "./components/HudIndicatorStrip";
import { useIsMobile, getIsMobileSnapshot } from "./hooks/useIsMobile";
import { useFullscreen } from "./hooks/useFullscreen";
import { useMentorPiControl } from "./hooks/useMentorPiControl";
import { useEspMqtt } from "./hooks/useEspMqtt";
import { useVoiceAssistant } from "./hooks/useVoiceAssistant";
import { useRoverSession } from "./context/RoverSessionContext";
import { apiPostJson, apiPost, apiFetch } from "./api/client";
import { isAllowedCaptureUrl } from "./api/captureUrl";
import {
  postDriveAssist,
  readDriveAssistEnabled,
} from "./utils/driveAssistApi.js";
import { logImuDebug } from "./utils/imuDebugLog.js";
import { playRoverChime } from "./utils/chimeApi.js";
import { toggleDocumentFullscreen } from "./utils/fullscreen.js";
import {
  PI_SYSTEM_ENDPOINT,
  CAMERA_SECRET,
  VOICE_DRIVE_DEBUG,
  DRIVE_ASSIST_DEBUG,
  JOYSTICK_DRIVE_DEBUG,
  IMU_DEBUG,
  MENTOR_BEEP_ENDPOINT,
  MENTOR_GIMBAL_ENDPOINT,
  PI_CAMERA_ENDPOINT,
  PI_NIGHTVISION_ENDPOINT,
  PI_RESOLUTION_ENDPOINT,
  PI_HI_RES_CAPTURE_ENDPOINT,
} from "./config";
import {
  MQTT_POWER_OFF_DELAY_SEC,
  publishPowerOff,
  publishPowerOffDelayed,
  publishPowerOn,
} from "./mqttPower";

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
const METRICS_PANEL_STORAGE_KEY = "rover-dashboard-metrics-panel";
const ROVER_SPEAKER_STORAGE_KEY = "rover-dashboard-rover-speaker";
const DASH_MIC_STORAGE_KEY = "rover-dashboard-dash-mic";
const CONTROL_INTERVAL_WS_MS = 8; // ~125Hz coalesce before MentorPi HTTP

function readInitialControlMode() {
  if (typeof window === "undefined") return "keyboard";
  try {
    const v = window.localStorage.getItem(CONTROL_MODE_STORAGE_KEY);
    if (v === "keyboard" || v === "joystick" || v === "immersive") return v;
  } catch {
    /* ignore */
  }
  return getIsMobileSnapshot() ? "joystick" : "keyboard";
}

function readInitialMetricsPanel() {
  if (typeof window === "undefined") return true;
  try {
    const v = window.localStorage.getItem(METRICS_PANEL_STORAGE_KEY);
    if (v === "false") return false;
    if (v === "true") return true;
  } catch {
    /* ignore */
  }
  return true;
}

function readInitialRoverSpeaker() {
  if (typeof window === "undefined") return true;
  try {
    const v = window.localStorage.getItem(ROVER_SPEAKER_STORAGE_KEY);
    if (v === "false") return false;
    if (v === "true") return true;
  } catch {
    /* ignore */
  }
  return true;
}

function readInitialDashMic() {
  if (typeof window === "undefined") return false;
  try {
    const v = window.localStorage.getItem(DASH_MIC_STORAGE_KEY);
    if (v === "true") return true;
    if (v === "false") return false;
  } catch {
    /* ignore */
  }
  return false;
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
  const { stats, driveAssistUpdate, imu, imuLive, isOnline: piOnline, hasEverConnected, sendControl, speedLevel, setSpeedLevel } =
    useMentorPiControl();
  const [driveAssistEnabled, setDriveAssistEnabled] = useState(false);
  const driveAssistHudUpdate = driveAssistEnabled ? driveAssistUpdate : null;

  useEffect(() => {
    if (typeof stats?.driveAssistEnabled === "boolean") {
      setDriveAssistEnabled(stats.driveAssistEnabled);
    }
  }, [stats?.driveAssistEnabled]);

  useEffect(() => {
    if (!isAuthenticated || !DRIVE_ASSIST_DEBUG) return;
    console.log(
      "[drive-assist]",
      driveAssistEnabled
        ? "WS collision updates active (DRIVE_ASSIST_UPDATE)"
        : 'idle — turn Assist ON in Settings (gear icon → Driving → Assist)',
    );
  }, [isAuthenticated, driveAssistEnabled]);

  useEffect(() => {
    if (!isAuthenticated || !IMU_DEBUG) return;
    if (!imu) return;
    logImuDebug(imu);
  }, [isAuthenticated, imu]);

  useEffect(() => {
    if (!isAuthenticated || !IMU_DEBUG) return;
    // eslint-disable-next-line no-console
    console.log(imuLive ? "[imu] stream live" : "[imu] stream stale / offline");
  }, [isAuthenticated, imuLive]);

  const { isEspOnline, mqttClientRef } = useEspMqtt(
    isAuthenticated ? sessionCreds : null,
  );

  const [isPowered, setIsPowered] = useState(true);
  const [nvActive, setNvActive] = useState(false);
  const nvActiveRef = useRef(false);
  const nvInFlightRef = useRef(false);
  const [isCapturing, setIsCapturing] = useState(false);
  const [resMode, setResMode] = useState("720p");
  const [focusMode, setFocusMode] = useState("far");
  const [controlMode, setControlModeState] = useState(readInitialControlMode);
  const [showMetricsPanel, setShowMetricsPanelState] = useState(readInitialMetricsPanel);
  const [roverSpeakerEnabled, setRoverSpeakerEnabledState] = useState(readInitialRoverSpeaker);
  const [dashMicEnabled, setDashMicEnabledState] = useState(readInitialDashMic);

  const setShowMetricsPanel = (enabled) => {
    setShowMetricsPanelState(enabled);
    try {
      window.localStorage.setItem(METRICS_PANEL_STORAGE_KEY, enabled ? "true" : "false");
    } catch {
      /* ignore */
    }
    void playRoverChime();
  };

  const toggleMetricsPanel = () => {
    setShowMetricsPanelState((prev) => {
      const next = !prev;
      try {
        window.localStorage.setItem(METRICS_PANEL_STORAGE_KEY, next ? "true" : "false");
      } catch {
        /* ignore */
      }
      void playRoverChime();
      return next;
    });
  };

  const setRoverSpeakerEnabled = (enabled) => {
    setRoverSpeakerEnabledState(enabled);
    try {
      window.localStorage.setItem(ROVER_SPEAKER_STORAGE_KEY, enabled ? "true" : "false");
    } catch {
      /* ignore */
    }
    void playRoverChime();
  };

  const setDashMicEnabled = (enabled) => {
    setDashMicEnabledState(enabled);
    try {
      window.localStorage.setItem(DASH_MIC_STORAGE_KEY, enabled ? "true" : "false");
    } catch {
      /* ignore */
    }
    void playRoverChime();
  };

  const setControlMode = (mode) => {
    if (mode !== "keyboard" && mode !== "joystick" && mode !== "immersive") return;
    setControlModeState(mode);
    try {
      window.localStorage.setItem(CONTROL_MODE_STORAGE_KEY, mode);
    } catch {
      /* ignore */
    }
    void playRoverChime();
  };
  const [actionError, setActionError] = useState(null);
  const [actionToast, setActionToast] = useState(null);
  const [, setSystemLoading] = useState(false);
  const [, setCameraLoading] = useState(false);
  const [isPointerLocked, setIsPointerLocked] = useState(false);
  const [videoStreamReady, setVideoStreamReady] = useState(false);
  const [powerSavingEnabled, setPowerSavingEnabled] = useState(true);
  const [powerSavingTimeoutMinutes, setPowerSavingTimeoutMinutes] = useState(5);
  const [lowBatteryGlowArmed, setLowBatteryGlowArmed] = useState(false);

  useEffect(() => {
    if (typeof stats?.powerSavingEnabled === "boolean") {
      setPowerSavingEnabled(stats.powerSavingEnabled);
    }
  }, [stats?.powerSavingEnabled]);

  useEffect(() => {
    const mins = Number(stats?.powerSavingTimeoutMinutes);
    if (mins === 5 || mins === 10 || mins === 30) {
      setPowerSavingTimeoutMinutes(mins);
    }
  }, [stats?.powerSavingTimeoutMinutes]);

  useEffect(() => {
    if (!isAuthenticated) return;
    let cancelled = false;

    const fetchNightVision = async () => {
      try {
        const res = await apiFetch(PI_NIGHTVISION_ENDPOINT, {
          timeout: 2500,
          retries: 0,
        });
        if (!res.ok) return;
        const json = await res.json();
        if (!cancelled && typeof json?.nightVision === "boolean") {
          nvActiveRef.current = json.nightVision;
          setNvActive(json.nightVision);
        }
      } catch {
        /* optional */
      }
    };

    const fetchResolution = async () => {
      try {
        const res = await apiFetch(PI_RESOLUTION_ENDPOINT, {
          timeout: 2500,
          retries: 0,
        });
        if (!res.ok) return;
        const json = await res.json();
        if (!cancelled && typeof json?.resolution === "string") {
          setResMode(json.resolution);
        }
      } catch {
        /* optional */
      }
    };

    void fetchNightVision();
    void fetchResolution();
    return () => {
      cancelled = true;
    };
  }, [isAuthenticated]);

  useEffect(() => {
    const timer = setTimeout(() => setLowBatteryGlowArmed(true), 5000);
    return () => clearTimeout(timer);
  }, []);

  const batteryPct = Number.isFinite(Number(stats?.battery))
    ? Number(stats.battery)
    : null;
  const isLowBattery = Number.isFinite(batteryPct) && batteryPct < 20;
  const effectiveIsCharging =
    stats?.isCharging === true ||
    stats?.charging === true ||
    stats?.charging?.isCharging === true;
  const distanceMeters = (() => {
    const v = Number(stats?.distance);
    return Number.isFinite(v) ? v : null;
  })();

  const isMobile = useIsMobile();
  const isFullscreen = useFullscreen();
  const viewportRef = useRef(null);
  const mountedAtRef = useRef(Date.now());
  const lastDriveRef = useRef({ x: 0, y: 0 });
  const lastGimbalRef = useRef({ x: 0, y: 0 });
  const pendingControlRef = useRef(null);
  const controlTimerRef = useRef(null);
  const lastKeyboardKeysRef = useRef([]);

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
    if (
      JOYSTICK_DRIVE_DEBUG &&
      payload?.drive != null &&
      !Array.isArray(payload) &&
      payload.command === undefined
    ) {
      const x = Number(payload.drive.x ?? 0);
      const y = Number(payload.drive.y ?? 0);
      if (x !== 0 || y !== 0) {
        // eslint-disable-next-line no-console
        console.log("[drive→backend] speed vector", {
          x: x.toFixed(3),
          y: y.toFixed(3),
        });
      }
    }
    if (piOnline && sendControl) {
      sendControl(payload);
      return Promise.resolve();
    }
    const startupGraceActive = Date.now() - mountedAtRef.current < 15000;
    if (hasEverConnected || !startupGraceActive) {
      setActionError("Control channel offline (MentorPi API unreachable)");
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
      lastKeyboardKeysRef.current = payload;
      void sendControlNow(payload);
      return;
    }
    if (typeof payload !== "object" || !payload) return;

    const next = { ...payload };
    if (payload.drive != null) {
      lastDriveRef.current = payload.drive;
    }
    if (payload.gimbal != null) {
      next.gimbal = payload.gimbal;
      lastGimbalRef.current = payload.gimbal;
    }
    queueControl(next);
  };

  const handleGimbalUpdate = (gimbal) => {
    clearErrorIfAny();
    lastGimbalRef.current = gimbal;
    queueControl({ gimbal });
  };

  const handleLoginSuccess = (_client, creds) => {
    setActionError(null);
    login(creds);
  };

  const handleHardPowerOff = () => {
    if (
      !window.confirm(
        "Hard reset: send MQTT Off to cut rover power now?",
      )
    ) {
      return;
    }
    publishPowerOff(mqttClientRef.current);
    setIsPowered(false);
    showActionToast("Hard reset sent (OFF GPIO13)");
  };

  const handleSystemAction = async (type) => {
    // 1. Intercept Boot
    if (type === "boot") {
      publishPowerOn(mqttClientRef.current);
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
      try {
        await apiPostJson(MENTOR_BEEP_ENDPOINT, {}, { timeout: 3000, retries: 0 });
      } catch (err) {
        setActionError(err.message ?? "Beep failed");
      }
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
        // Pi OS shutdown first (API above); ESP cuts GPIO after delay.
        publishPowerOffDelayed(
          mqttClientRef.current,
          MQTT_POWER_OFF_DELAY_SEC,
        );
        setIsPowered(false);
        showActionToast(
          `Power cut scheduled (OFF GPIO13 DELAY ${MQTT_POWER_OFF_DELAY_SEC}s)`,
        );
      }
    } catch (err) {
      setActionError(err.message ?? `System ${type} failed`);
    } finally {
      setSystemLoading(false);
    }
  };

  const handleNVToggle = async (requestedState) => {
    if (nvInFlightRef.current) return;

    // Explicit bool (voice/settings) wins; button taps flip. Ignore click events.
    const hasExplicit = typeof requestedState === "boolean";
    const optimistic = hasExplicit ? requestedState : !nvActiveRef.current;
    if (hasExplicit && optimistic === nvActiveRef.current) return;

    nvInFlightRef.current = true;
    nvActiveRef.current = optimistic;
    setNvActive(optimistic);
    setCameraLoading(true);
    setActionError(null);
    try {
      // Server-side flip for button taps so a second press always turns OFF,
      // even if the client briefly lost track of state.
      const json = await apiPostJson(
        PI_NIGHTVISION_ENDPOINT,
        {
          ...(hasExplicit ? { active: requestedState } : { toggle: true }),
          ...(CAMERA_SECRET ? { secret: CAMERA_SECRET } : {}),
        },
        { timeout: 25_000, retries: 0 },
      );
      const applied =
        typeof json?.nightVision === "boolean" ? json.nightVision : optimistic;
      nvActiveRef.current = applied;
      setNvActive(applied);
      showActionToast(`Night mode ${applied ? "enabled" : "disabled"}`);
      void playRoverChime();
    } catch (err) {
      // Re-sync — request may have applied before the response failed.
      try {
        const res = await apiFetch(PI_NIGHTVISION_ENDPOINT, {
          timeout: 2500,
          retries: 0,
        });
        if (res.ok) {
          const status = await res.json();
          if (typeof status?.nightVision === "boolean") {
            nvActiveRef.current = status.nightVision;
            setNvActive(status.nightVision);
          }
        } else {
          nvActiveRef.current = !optimistic;
          setNvActive(!optimistic);
        }
      } catch {
        nvActiveRef.current = !optimistic;
        setNvActive(!optimistic);
      }
      setActionError(err.message ?? "Night vision toggle failed");
    } finally {
      nvInFlightRef.current = false;
      setCameraLoading(false);
    }
  };

  const handleResChange = async (newMode) => {
    setCameraLoading(true);
    setActionError(null);
    try {
      await apiPostJson(
        PI_RESOLUTION_ENDPOINT,
        {
          mode: newMode,
          ...(CAMERA_SECRET ? { secret: CAMERA_SECRET } : {}),
        },
        { timeout: 20_000, retries: 0 },
      );
      setResMode(newMode);
      showActionToast(`Resolution set to ${newMode.toUpperCase()}`);
      void playRoverChime();
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
      void playRoverChime();
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
      showActionToast(`Drive mode: ${enabled ? "ECO" : "Sport"}`);
      void playRoverChime();
    } catch (err) {
      setActionError(err.message ?? "Drive mode update failed");
    }
  };

  const setDriveAssist = async (enabled) => {
    setActionError(null);
    const previousEnabled = driveAssistEnabled;
    setDriveAssistEnabled(enabled);
    try {
      const info = await postDriveAssist(enabled);
      const nextEnabled = readDriveAssistEnabled(info);
      if (nextEnabled != null) setDriveAssistEnabled(nextEnabled);
      showActionToast(`Drive assist ${enabled ? "enabled" : "disabled"}`);
      void playRoverChime();
    } catch (err) {
      setDriveAssistEnabled(previousEnabled);
      setActionError(err.message ?? "Drive assist update failed");
      if (DRIVE_ASSIST_DEBUG) {
        console.log("[drive-assist] toggle failed", err?.message ?? err);
      }
    }
  };

  const setPowerSaving = async ({ enabled, timeoutMinutes } = {}) => {
    const nextEnabled = Boolean(enabled);
    const nextTimeout =
      timeoutMinutes === 5 || timeoutMinutes === 10 || timeoutMinutes === 30
        ? timeoutMinutes
        : powerSavingTimeoutMinutes;

    if (!nextEnabled && powerSavingEnabled) {
      const mins = Number(stats?.batteryMinutesRemaining);
      const estimated = formatRemainingTime(Number.isFinite(mins) ? mins : null);
      const confirmed = window.confirm(
        `Disabling power saving may cause rover to run out of battery in ${estimated}.\n\nDo you want to continue?`,
      );
      if (!confirmed) return;
    }

    setActionError(null);
    try {
      const body = { enabled: nextEnabled };
      if (nextEnabled) body.timeoutMinutes = nextTimeout;
      const res = await apiPostJson(`${PI_SYSTEM_ENDPOINT}/power-saving`, body);
      setPowerSavingEnabled(nextEnabled);
      const appliedTimeout = Number(res?.timeoutMinutes);
      if (appliedTimeout === 5 || appliedTimeout === 10 || appliedTimeout === 30) {
        setPowerSavingTimeoutMinutes(appliedTimeout);
      } else if (nextEnabled) {
        setPowerSavingTimeoutMinutes(nextTimeout);
      }
      showActionToast(
        nextEnabled
          ? `Idle shutdown: ${nextTimeout} min`
          : "Idle shutdown off",
      );
      void playRoverChime();
    } catch (err) {
      setActionError(err.message ?? "Power-saving update failed");
    }
  };

  const handleCapture = async () => {
    setIsCapturing(true);
    setActionError(null);
    try {
      const data = await apiPost(PI_HI_RES_CAPTURE_ENDPOINT, {
        timeout: 90_000,
        retries: 0,
      });
      const url =
        typeof data?.url === "string"
          ? data.url
          : data?.name
            ? `${new URL(PI_HI_RES_CAPTURE_ENDPOINT).origin}/photos/${encodeURIComponent(data.name)}`
            : null;
      if (url && isAllowedCaptureUrl(url)) {
        window.open(url, "_blank", "noopener,noreferrer");
        showActionToast(
          data?.width && data?.height
            ? `Photo saved (${data.width}×${data.height})`
            : "Photo saved",
        );
      } else if (url) {
        window.open(url, "_blank", "noopener,noreferrer");
        showActionToast("Photo saved");
      } else {
        setActionError(data?.error || "No capture URL returned");
      }
    } catch (err) {
      setActionError(err.message ?? "Capture failed");
    } finally {
      setIsCapturing(false);
    }
  };

  const handleCameraReset = async () => {
    setActionError(null);
    try {
      await apiPostJson(MENTOR_GIMBAL_ENDPOINT, { action: "center" });
    } catch (err) {
      setActionError(err.message ?? "Gimbal center failed");
    }
  };

  const handleLookDown = async () => {
    setActionError(null);
    try {
      await apiPostJson(MENTOR_GIMBAL_ENDPOINT, { action: "down" });
    } catch (err) {
      setActionError(err.message ?? "Look down failed");
    }
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

  useEffect(() => {
    // Release any leftover pointer lock from older handheld mouse-bridge builds.
    if (typeof document !== "undefined" && document.pointerLockElement) {
      document.exitPointerLock?.();
    }
  }, []);

  return (
    <div
      className={`viewport${isPointerLocked ? " viewport-mouse-look" : ""}`}
      ref={viewportRef}
    >
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
        onVideoReadyChange={setVideoStreamReady}
        controlChannelReady={piOnline}
        roverSpeakerEnabled={roverSpeakerEnabled}
        dashMicEnabled={dashMicEnabled}
        onHardPowerOff={handleHardPowerOff}
      />
      <GimbalTiltHud pan={stats.pan} tilt={stats.tilt} />

      {isAuthenticated && isMobile && controlMode !== "immersive" && (
        <MobileTouchGimbalLayer
          onGimbal={handleGimbalUpdate}
        />
      )}

      {isAuthenticated && isFullscreen && !isMobile && controlMode !== "immersive" && (
        <MouseGimbalLayer
          viewportRef={viewportRef}
          isFullscreen={isFullscreen}
          isPointerLocked={isPointerLocked}
          onPointerLockChange={setIsPointerLocked}
          onDrive={handleDriveUpdate}
          lastDriveRef={lastDriveRef}
        />
      )}

      {/* Gamepad bridge when on-screen joysticks are not mounted (keyboard / immersive).
          Xbox controller via USB or Bluetooth (Standard Gamepad mapping). */}
      {isAuthenticated && controlMode !== "joystick" && (
        <DualJoystickControls
          immersive
          onDrive={handleDriveUpdate}
          onReset={handleCameraReset}
          onLookDown={handleLookDown}
          onLaserToggle={handleLaserToggle}
          laserOn={stats.laserOn}
          onVoiceStart={startVoice}
          onVoiceStop={stopVoice}
          voiceSupported={voiceSupported}
          voiceListening={voiceListening}
          onHeadlightToggle={() => {
            const nextState = stats.usbPower === "on" ? "off" : "on";
            toggleLight(nextState);
          }}
          headlightOn={stats.usbPower === "on"}
          onTreat={handleFeederTreat}
          onToggleFullscreen={toggleDocumentFullscreen}
          onToggleMetrics={toggleMetricsPanel}
          onNVToggle={handleNVToggle}
          nvActive={nvActive}
          onCapture={handleCapture}
          isCapturing={isCapturing}
        />
      )}

      {isAuthenticated && (
        <div className={`hud-overlay${controlMode === "immersive" ? " hud-overlay--immersive" : ""}`}>
          <HudHeader
            wifiSignal={stats?.wifiSignal}
            latencyMs={stats?.latency}
            isPowered={isPowered}
            resMode={resMode}
            quietMode={stats?.quietMode}
            driveAssistEnabled={driveAssistEnabled}
            driveAssistUpdate={driveAssistHudUpdate}
            powerSavingEnabled={powerSavingEnabled}
            powerSavingTimeoutMinutes={powerSavingTimeoutMinutes}
            powerSavingTtlMs={
              stats?.displayTtlMs != null ? stats.displayTtlMs : stats?.ttlMs
            }
            isCharging={effectiveIsCharging}
            isLowBattery={isLowBattery}
            lowBatteryIndicatorArmed={lowBatteryGlowArmed}
            onQuietModeChange={setQuietMode}
            onDriveAssistChange={setDriveAssist}
            onPowerSavingChange={setPowerSaving}
            onResChange={handleResChange}
            onAction={handleSystemAction}
            controlMode={controlMode}
            onControlModeChange={setControlMode}
            driveSpeed={speedLevel}
            onDriveSpeedChange={(level) => {
              setSpeedLevel(level);
              const label = level === "slow" ? "Slow" : level === "fast" ? "Fast" : "Mid";
              showActionToast(`Drive speed: ${label}`);
            }}
            metricsPanelEnabled={showMetricsPanel}
            onMetricsPanelChange={setShowMetricsPanel}
            roverSpeakerEnabled={roverSpeakerEnabled}
            onRoverSpeakerChange={setRoverSpeakerEnabled}
            dashMicEnabled={dashMicEnabled}
            onDashMicChange={setDashMicEnabled}
          />

          <HudFooter
            isMobile={isMobile}
            controlMode={controlMode}
            metricsPanelEnabled={showMetricsPanel}
            stats={stats}
            batteryPct={batteryPct}
            isCharging={effectiveIsCharging}
            distanceMeters={distanceMeters}
            piOnline={piOnline}
            isEspOnline={isEspOnline}
            onDrive={handleDriveUpdate}
            onResetCamera={handleCameraReset}
            onLookDown={handleLookDown}
            onLaserToggle={handleLaserToggle}
            laserOn={stats.laserOn}
            onVoiceStart={startVoice}
            onVoiceStop={stopVoice}
            voiceSupported={voiceSupported}
            voiceListening={voiceListening}
            onToggleLight={toggleLight}
            onCapture={handleCapture}
            isCapturing={isCapturing}
            onFeederTreat={handleFeederTreat}
            onToggleFullscreen={toggleDocumentFullscreen}
            onToggleMetrics={toggleMetricsPanel}
            onNVToggle={handleNVToggle}
            nvActive={nvActive}
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
  latencyMs,
  isPowered,
  resMode,
  quietMode,
  driveAssistEnabled,
  driveAssistUpdate,
  powerSavingEnabled,
  powerSavingTimeoutMinutes = 5,
  powerSavingTtlMs = null,
  isCharging,
  isLowBattery,
  lowBatteryIndicatorArmed,
  onQuietModeChange,
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
  roverSpeakerEnabled = true,
  onRoverSpeakerChange,
  dashMicEnabled = false,
  onDashMicChange,
}) {
  return (
    <div className="hud-header">
      <div className="glass-card hud-header-brand">
        <div className="hud-brand-stack">
          <div className="hud-brand-title" aria-label="芒果号 v2" title="芒果号 v2">
            <BrandCatIcon size={18} />
            <span className="hud-brand-version">v2</span>
          </div>
        </div>
      </div>
      <div className="hud-header-center">
        <HudIndicatorStrip
          driveAssistEnabled={driveAssistEnabled}
          driveAssistUpdate={driveAssistUpdate}
          powerSavingEnabled={powerSavingEnabled}
          powerSavingTtlMs={powerSavingTtlMs}
          quietMode={quietMode}
          isCharging={isCharging}
          isLowBattery={isLowBattery}
          lowBatteryIndicatorArmed={lowBatteryIndicatorArmed}
          wifiSignal={wifiSignal}
          latencyMs={latencyMs}
        />
      </div>
      <div className="glass-card hud-header-actions">
        <SystemControls
          isPowered={isPowered}
          resMode={resMode}
          quietMode={quietMode}
          driveAssistEnabled={driveAssistEnabled}
          powerSavingEnabled={powerSavingEnabled}
          powerSavingTimeoutMinutes={powerSavingTimeoutMinutes}
          onQuietModeChange={onQuietModeChange}
          onDriveAssistChange={onDriveAssistChange}
          onPowerSavingChange={onPowerSavingChange}
          onResChange={onResChange}
          onAction={onAction}
          controlMode={controlMode}
          onControlModeChange={onControlModeChange}
          driveSpeed={driveSpeed}
          onDriveSpeedChange={onDriveSpeedChange}
          metricsPanelEnabled={metricsPanelEnabled}
          onMetricsPanelChange={onMetricsPanelChange}
          roverSpeakerEnabled={roverSpeakerEnabled}
          onRoverSpeakerChange={onRoverSpeakerChange}
          dashMicEnabled={dashMicEnabled}
          onDashMicChange={onDashMicChange}
        />
        <FullscreenButton />
      </div>
    </div>
  );
}

function HudFooter({
  isMobile,
  controlMode,
  metricsPanelEnabled = true,
  stats,
  batteryPct,
  isCharging,
  distanceMeters = null,
  piOnline,
  isEspOnline,
  onDrive,
  onResetCamera,
  onLookDown,
  onLaserToggle,
  laserOn,
  onVoiceStart,
  onVoiceStop,
  voiceSupported,
  voiceListening,
  onToggleLight,
  onCapture,
  isCapturing,
  onFeederTreat,
  onToggleFullscreen,
  onToggleMetrics,
  onNVToggle,
  nvActive = false,
}) {
  const joystickProps = {
    onDrive,
    onReset: onResetCamera,
    onLookDown,
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
    onTreat: onFeederTreat,
    onToggleFullscreen,
    onToggleMetrics,
    onNVToggle,
    nvActive,
    onCapture,
    isCapturing,
  };

  const schematic = metricsPanelEnabled ? (
    <RoverSchematic
      pan={stats.pan}
      battery={batteryPct}
      cpuTemp={stats.cpuTemp}
      latencyMs={stats.latency}
      throttle={stats.throttle}
      voltage={stats.voltage}
      wifiSignal={stats.wifiSignal}
      distanceMeters={distanceMeters}
      cpuLoad={stats.cpuLoad}
      isOffline={!piOnline}
      isCharging={isCharging}
    />
  ) : null;

  const joystickCenter = metricsPanelEnabled ? schematic : null;

  const renderKeyboardControls = () => (
    <KeyboardControlCluster
      onDrive={onDrive}
      usbPower={stats.usbPower}
      laserOn={laserOn}
      onVoiceStart={onVoiceStart}
      onVoiceStop={onVoiceStop}
      voiceSupported={voiceSupported}
      voiceListening={voiceListening}
      onLightToggle={() => {
        const nextState = stats.usbPower === "on" ? "off" : "on";
        onToggleLight(nextState);
      }}
      onLaserToggle={onLaserToggle}
      onCapture={onCapture}
      isCapturing={isCapturing}
      onReset={onResetCamera}
      onLookDown={onLookDown}
      onTreat={onFeederTreat}
    />
  );

  if (controlMode === "immersive") {
    return null;
  }

  return (
    <div className="hud-footer">
      {!isMobile && controlMode === "keyboard" && schematic}

      {isMobile && controlMode === "joystick" && (
        <DualJoystickControls {...joystickProps}>{joystickCenter}</DualJoystickControls>
      )}

      {isMobile && controlMode === "keyboard" && schematic}

      <div className="footer-controls">
        {piOnline ? (
          <>
            {!isMobile && controlMode === "keyboard" && renderKeyboardControls()}
            {!isMobile && controlMode === "joystick" && (
              <DualJoystickControls {...joystickProps}>{joystickCenter}</DualJoystickControls>
            )}

            {isMobile && controlMode === "keyboard" && renderKeyboardControls()}
          </>
        ) : null}
      </div>
    </div>
  );
}

