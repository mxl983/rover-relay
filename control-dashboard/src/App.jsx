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
  DRIVE_ASSIST_DEBUG,
  getRelayRoverHeartbeatWebSocketUrl,
  ROVER_CLIENT_DISTANCE_ENDPOINT,
  ROVER_CHARGING_ENDPOINT,
  ROVER_STATE_ENDPOINT,
  SLAM_ENABLED,
} from "./config";
import { LoginOverlay } from "./components/LoginOverlay";
import { SystemControls } from "./components/SystemControls";
import { DriveAssistHUD } from "./components/DriveAssistHUD";
import { RoverSchematic } from "./components/RoverSchematic";
import { FullscreenButton } from "./components/FullscreenButton";
import { DualJoystickControls } from "./components/JoystickControlCluster";
import { MouseGimbalLayer } from "./components/MouseGimbalLayer";
import { MobileTouchGimbalLayer } from "./components/MobileTouchGimbalLayer";
import { AssistantPanel } from "./components/AssistantPanel";
import { LidarMinimap } from "./components/LidarMinimap";
import { BrandCatIcon } from "./components/BrandCatIcon";
import { HudIndicatorStrip } from "./components/HudIndicatorStrip";
import { useIsMobile, getIsMobileSnapshot } from "./hooks/useIsMobile";
import { useFullscreen } from "./hooks/useFullscreen";
import { usePiWebSocket } from "./hooks/usePiWebSocket";
import { useMqtt } from "./hooks/useMqtt";
import { useVoiceAssistant } from "./hooks/useVoiceAssistant";
import { useLidarScan } from "./hooks/useLidarScan";
import { useSlamMap } from "./hooks/useSlamMap";
import { useRoverSession } from "./context/RoverSessionContext";
import { apiPostJson, apiPost, apiFetch } from "./api/client";
import { isAllowedCaptureUrl } from "./api/capture";
import { formatRoverDistance } from "./utils/formatRoverDistance.js";
import { deriveRoverCharging } from "./utils/deriveRoverCharging.js";
import {
  fetchDriveAssistStatus,
  postDriveAssist,
  readDriveAssistEnabled,
} from "./utils/driveAssistApi.js";
import {
  fetchNavigationStatus,
  postNavigation,
  readNavigationEnabled,
} from "./utils/navigationApi.js";

function isManualDrivePayload(payload) {
  if (!payload) return false;
  if (payload.command !== undefined) return false;
  if (Array.isArray(payload)) return true;
  if (payload.drive) return true;
  if (Array.isArray(payload.payload)) return true;
  return false;
}

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
const LIDAR_MINIMAP_STORAGE_KEY = "rover-dashboard-lidar-minimap";
const METRICS_PANEL_STORAGE_KEY = "rover-dashboard-metrics-panel";
const ROVER_SPEAKER_STORAGE_KEY = "rover-dashboard-rover-speaker";
const DASH_MIC_STORAGE_KEY = "rover-dashboard-dash-mic";
const CONTROL_INTERVAL_WS_MS = 16; // ~60Hz for low-latency websocket control

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

function readInitialLidarMinimap() {
  if (typeof window === "undefined") return false;
  try {
    const v = window.localStorage.getItem(LIDAR_MINIMAP_STORAGE_KEY);
    if (v === "true") return true;
  } catch {
    /* ignore */
  }
  return false;
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
  const { stats, driveAssistUpdate, isOnline: piOnline, hasEverConnected, sendControl } =
    usePiWebSocket();
  const [driveAssistEnabled, setDriveAssistEnabled] = useState(false);
  const [navigationEnabled, setNavigationEnabled] = useState(false);
  const driveAssistHudUpdate = driveAssistEnabled ? driveAssistUpdate : null;

  useEffect(() => {
    if (typeof stats?.driveAssistEnabled === "boolean") {
      setDriveAssistEnabled(stats.driveAssistEnabled);
    }
  }, [stats?.driveAssistEnabled]);

  useEffect(() => {
    if (typeof stats?.navigationEnabled === "boolean") {
      setNavigationEnabled(stats.navigationEnabled);
    }
  }, [stats?.navigationEnabled]);

  useEffect(() => {
    if (!isAuthenticated || !DRIVE_ASSIST_DEBUG) return;
    console.log(
      "[drive-assist]",
      driveAssistEnabled
        ? "WS collision updates active (DRIVE_ASSIST_UPDATE)"
        : 'idle — turn Assist ON in Settings (gear icon → Driving → Assist)',
    );
  }, [isAuthenticated, driveAssistEnabled]);

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
  const [showLidarMinimap, setShowLidarMinimapState] = useState(readInitialLidarMinimap);
  const [showMetricsPanel, setShowMetricsPanelState] = useState(readInitialMetricsPanel);
  const [roverSpeakerEnabled, setRoverSpeakerEnabledState] = useState(readInitialRoverSpeaker);
  const [dashMicEnabled, setDashMicEnabledState] = useState(readInitialDashMic);

  const setShowLidarMinimap = (enabled) => {
    setShowLidarMinimapState(enabled);
    try {
      window.localStorage.setItem(LIDAR_MINIMAP_STORAGE_KEY, enabled ? "true" : "false");
    } catch {
      /* ignore */
    }
  };

  const setShowMetricsPanel = (enabled) => {
    setShowMetricsPanelState(enabled);
    try {
      window.localStorage.setItem(METRICS_PANEL_STORAGE_KEY, enabled ? "true" : "false");
    } catch {
      /* ignore */
    }
  };

  const setRoverSpeakerEnabled = (enabled) => {
    setRoverSpeakerEnabledState(enabled);
    try {
      window.localStorage.setItem(ROVER_SPEAKER_STORAGE_KEY, enabled ? "true" : "false");
    } catch {
      /* ignore */
    }
  };

  const setDashMicEnabled = (enabled) => {
    setDashMicEnabledState(enabled);
    try {
      window.localStorage.setItem(DASH_MIC_STORAGE_KEY, enabled ? "true" : "false");
    } catch {
      /* ignore */
    }
  };

  const setControlMode = (mode) => {
    if (mode !== "keyboard" && mode !== "joystick" && mode !== "immersive") return;
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
  /** Same shape as GET /api/rover/state `data` for VideoStream boot loader (from relay WS). */
  const [relayRoverPayload, setRelayRoverPayload] = useState(null);
  const [relayDistanceMeters, setRelayDistanceMeters] = useState(null);
  const [powerSavingEnabled, setPowerSavingEnabled] = useState(true);
  const [lowBatteryGlowArmed, setLowBatteryGlowArmed] = useState(false);
  const lidarSubscribed = isAuthenticated && showLidarMinimap;
  const { scan: lidarScan, isLive: lidarLive, error: lidarError } = useLidarScan(
    lidarSubscribed,
  );
  const {
    map: slamMap,
    isLive: slamLive,
    error: slamError,
  } = useSlamMap(SLAM_ENABLED && lidarSubscribed);

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
          if (msg.type !== "relay.rover.heartbeat" || !msg.success || !msg.rover) return;
          const rover = msg.rover;
          setRelayCharging(deriveRoverCharging(rover));
          const pct = Number(rover?.battery?.currentPct);
          setRelayBatteryPct(Number.isFinite(pct) ? pct : null);
          const minsRemaining = Number(rover?.battery?.estimatedMinutesRemainingActiveVideo);
          setRelayBatteryMinutesRemaining(Number.isFinite(minsRemaining) ? minsRemaining : null);
          const tempC = Number(rover?.environment?.temperatureC);
          setRelayTemperatureC(Number.isFinite(tempC) ? tempC : null);
          const dist = Number(rover?.clientLocation?.distanceMeters);
          setRelayDistanceMeters(Number.isFinite(dist) ? dist : null);
          setRelayRoverPayload({ rover });
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
        setRelayRoverPayload(null);
        setRelayDistanceMeters(null);
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

  /** Charging HUD: poll relay when logged in (WS is primary; HTTP backs up flaky WS). */
  useEffect(() => {
    if (!isAuthenticated) return undefined;

    let cancelled = false;

    const applyFromState = (rover) => {
      if (cancelled || !rover) return;
      setRelayCharging(deriveRoverCharging(rover));
    };

    const poll = async () => {
      try {
        const res = await apiFetch(ROVER_STATE_ENDPOINT, {
          method: "GET",
          timeout: 8000,
          retries: 0,
        });
        if (!res.ok || cancelled) return;
        const body = await res.json();
        applyFromState(body?.rover ?? body?.data?.rover);
      } catch {
        try {
          const res = await apiFetch(ROVER_CHARGING_ENDPOINT, {
            method: "GET",
            timeout: 5000,
            retries: 0,
          });
          if (!res.ok || cancelled) return;
          const body = await res.json();
          applyFromState({ charging: body?.charging ?? body?.data?.charging });
        } catch {
          /* ignore */
        }
      }
    };

    void poll();
    const timer = setInterval(() => {
      void poll();
    }, 5000);

    return () => {
      cancelled = true;
      clearInterval(timer);
    };
  }, [isAuthenticated]);

  useEffect(() => {
    if (!isAuthenticated || !navigator.geolocation?.watchPosition) return;
    let watchId = null;

    const reportLocation = (pos) => {
      const { latitude, longitude, accuracy } = pos.coords;
      void apiPostJson(ROVER_CLIENT_DISTANCE_ENDPOINT, { latitude, longitude, accuracy }, {
        timeout: 8000,
        retries: 0,
      }).catch(() => {
        /* ignore — distance optional */
      });
    };

    watchId = navigator.geolocation.watchPosition(
      reportLocation,
      () => {},
      { enableHighAccuracy: true, maximumAge: 30_000, timeout: 15_000 },
    );

    return () => {
      if (watchId != null) navigator.geolocation.clearWatch(watchId);
    };
  }, [isAuthenticated]);

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
    if (!isAuthenticated) return;
    let cancelled = false;

    const fetchDriveAssist = async () => {
      try {
        const status = await fetchDriveAssistStatus();
        if (!cancelled) {
          const enabled = readDriveAssistEnabled(status);
          if (enabled != null) setDriveAssistEnabled(enabled);
          if (DRIVE_ASSIST_DEBUG) {
            console.log("[drive-assist] GET /drive-assist", JSON.stringify(status, null, 2));
          }
        }
      } catch (err) {
        if (!cancelled && DRIVE_ASSIST_DEBUG) {
          console.log("[drive-assist] status fetch failed", err?.message ?? err);
        }
      }
    };

    const fetchNavigation = async () => {
      try {
        const status = await fetchNavigationStatus();
        if (!cancelled) {
          const enabled = readNavigationEnabled(status);
          if (enabled != null) setNavigationEnabled(enabled);
        }
      } catch (err) {
        if (!cancelled) {
          console.log("[navigation] status fetch failed", err?.message ?? err);
        }
      }
    };

    void fetchDriveAssist();
    void fetchNavigation();
    return () => {
      cancelled = true;
    };
  }, [isAuthenticated]);

  useEffect(() => {
    const timer = setTimeout(() => setLowBatteryGlowArmed(true), 5000);
    return () => clearTimeout(timer);
  }, []);

  const displayStats =
    relayDistanceMeters != null
      ? { ...stats, distance: relayDistanceMeters }
      : stats;

  // Realtime Pi WebSocket health (voltage → %) takes priority; relay /state poll is fallback only.
  const batteryPct =
    Number.isFinite(Number(stats?.battery))
      ? Number(stats.battery)
      : relayBatteryPct != null
        ? relayBatteryPct
        : null;
  const isLowBattery = Number.isFinite(batteryPct) && batteryPct < 20;
  const effectiveIsCharging = relayCharging === true;

  const isMobile = useIsMobile();
  const isFullscreen = useFullscreen();
  const viewportRef = useRef(null);
  const lastDriveRef = useRef({ x: 0, y: 0 });
  const pendingControlRef = useRef(null);
  const controlTimerRef = useRef(null);
  const lastKeyboardKeysRef = useRef([]);
  const driveAssistBeforeNavRef = useRef(null);

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
    if (navigationEnabled && isManualDrivePayload(payload)) {
      return Promise.resolve();
    }
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
      lastKeyboardKeysRef.current = payload;
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

  const handleHardPowerOff = () => {
    if (
      !window.confirm(
        "Hard reset: send MQTT Off to cut rover power now?",
      )
    ) {
      return;
    }
    mqttClientRef.current?.publish("rover/power/pi", "Off", { qos: 1 });
    mqttClientRef.current?.publish("rover/power/aux", "Off", { qos: 1 });
    setIsPowered(false);
    showActionToast("Hard reset sent (power Off)");
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

    // 4. Open relay telemetry dashboard.
    if (type === "telemetry") {
      window.open(
        "https://jjcloud.tail9d0237.ts.net:8787/dashboard",
        "_blank",
        "noopener,noreferrer",
      );
      return;
    }

    // 5. Handle generic system commands (Reboot/Shutdown)
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
      showActionToast(`Drive mode: ${enabled ? "ECO" : "Sport"}`);
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
    } catch (err) {
      setDriveAssistEnabled(previousEnabled);
      setActionError(err.message ?? "Drive assist update failed");
      if (DRIVE_ASSIST_DEBUG) {
        console.log("[drive-assist] toggle failed", err?.message ?? err);
      }
    }
  };

  const setNavigation = async (enabled) => {
    setActionError(null);
    const previousEnabled = navigationEnabled;
    setNavigationEnabled(enabled);
    try {
      if (enabled) {
        driveAssistBeforeNavRef.current = driveAssistEnabled;
        if (driveAssistEnabled) {
          try {
            await postDriveAssist(false);
            setDriveAssistEnabled(false);
          } catch {
            // Relay also disables assist when roam turns on.
          }
        }
      }
      const status = await postNavigation(enabled);
      const nextEnabled = readNavigationEnabled(status);
      if (nextEnabled != null) setNavigationEnabled(nextEnabled);
      if (enabled) {
        void sendControlNow({ drive: { x: 0, y: 0 } });
      } else if (driveAssistBeforeNavRef.current === true) {
        try {
          await postDriveAssist(true);
          setDriveAssistEnabled(true);
        } catch {
          // User can re-enable assist manually.
        }
        driveAssistBeforeNavRef.current = null;
      }
      showActionToast(`Autonomous roam ${enabled ? "enabled" : "disabled"}`);
    } catch (err) {
      setNavigationEnabled(previousEnabled);
      setActionError(err.message ?? "Navigation update failed");
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
        backupStreamUrl={BACKUP_STREAM_ENDPOINT}
        showBackupView={showBackupView}
        relayRoverPayload={relayRoverPayload}
        onHardPowerOff={handleHardPowerOff}
      />
      <DriveAssistHUD pan={stats.pan} tilt={stats.tilt} />

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

      {isAuthenticated && controlMode === "immersive" && (
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
          onToggleBackupView={handleToggleBackupView}
          backupViewEnabled={showBackupView}
          onTreat={handleFeederTreat}
        />
      )}

      {isAuthenticated && (
        <div className={`hud-overlay${controlMode === "immersive" ? " hud-overlay--immersive" : ""}`}>
          <HudHeader
            wifiSignal={stats?.wifiSignal}
            distanceMeters={relayDistanceMeters}
            isPowered={isPowered}
            nvActive={nvActive}
            resMode={resMode}
            isCapturing={isCapturing}
            focusMode={focusMode}
            quietMode={stats?.quietMode}
            driveAssistEnabled={driveAssistEnabled}
            driveAssistUpdate={driveAssistHudUpdate}
            navigationEnabled={navigationEnabled}
            powerSavingEnabled={powerSavingEnabled}
            isCharging={effectiveIsCharging}
            isLowBattery={isLowBattery}
            lowBatteryIndicatorArmed={lowBatteryGlowArmed}
            onQuietModeChange={setQuietMode}
            onDriveAssistChange={setDriveAssist}
            onNavigationChange={setNavigation}
            onPowerSavingChange={setPowerSaving}
            onNVToggle={handleNVToggle}
            onResChange={handleResChange}
            onAction={handleSystemAction}
            onFocusChange={handleFocusChange}
            controlMode={controlMode}
            onControlModeChange={setControlMode}
            lidarMinimapEnabled={showLidarMinimap}
            onLidarMinimapChange={setShowLidarMinimap}
            metricsPanelEnabled={showMetricsPanel}
            onMetricsPanelChange={setShowMetricsPanel}
            roverSpeakerEnabled={roverSpeakerEnabled}
            onRoverSpeakerChange={setRoverSpeakerEnabled}
            dashMicEnabled={dashMicEnabled}
            onDashMicChange={setDashMicEnabled}
          />

          {showLidarMinimap && (
            <div className="lidar-minimap-float">
              <LidarMinimap
                scan={lidarScan}
                isLive={lidarLive}
                error={lidarError}
                slamMap={slamMap}
                slamLive={slamLive}
                slamError={slamError}
                pan={displayStats.pan}
              />
            </div>
          )}

          <HudFooter
            isMobile={isMobile}
            controlMode={controlMode}
            metricsPanelEnabled={showMetricsPanel}
            stats={displayStats}
            batteryPct={batteryPct}
            isCharging={effectiveIsCharging}
            ambientTemperatureC={relayTemperatureC}
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
  distanceMeters,
  isPowered,
  nvActive,
  resMode,
  isCapturing,
  focusMode,
  quietMode,
  driveAssistEnabled,
  driveAssistUpdate,
  navigationEnabled,
  powerSavingEnabled,
  isCharging,
  isLowBattery,
  lowBatteryIndicatorArmed,
  onQuietModeChange,
  onDriveAssistChange,
  onNavigationChange,
  onPowerSavingChange,
  onNVToggle,
  onResChange,
  onAction,
  onFocusChange,
  controlMode,
  onControlModeChange,
  lidarMinimapEnabled,
  onLidarMinimapChange,
  metricsPanelEnabled,
  onMetricsPanelChange,
  roverSpeakerEnabled = true,
  onRoverSpeakerChange,
  dashMicEnabled = false,
  onDashMicChange,
}) {
  const distanceLabel = formatRoverDistance(distanceMeters);

  return (
    <div className="hud-header">
      <div className="glass-card hud-header-brand">
        <div className="hud-brand-stack">
          <div className="hud-brand-title" aria-label="芒果号 v2" title="芒果号 v2">
            <BrandCatIcon size={18} />
            <span className="hud-brand-version">v2</span>
          </div>
          {distanceLabel ? (
            <div
              className="hud-brand-distance"
              title="Your distance from the rover (when location is shared)"
            >
              {distanceLabel}
            </div>
          ) : null}
        </div>
      </div>
      <div className="hud-header-center">
        <HudIndicatorStrip
          driveAssistEnabled={driveAssistEnabled}
          driveAssistUpdate={driveAssistUpdate}
          powerSavingEnabled={powerSavingEnabled}
          navigationEnabled={navigationEnabled}
          quietMode={quietMode}
          isCharging={isCharging}
          isLowBattery={isLowBattery}
          lowBatteryIndicatorArmed={lowBatteryIndicatorArmed}
          wifiSignal={wifiSignal}
        />
      </div>
      <div className="glass-card hud-header-actions">
        <SystemControls
          isPowered={isPowered}
          nvActive={nvActive}
          resMode={resMode}
          isCapturing={isCapturing}
          quietMode={quietMode}
          driveAssistEnabled={driveAssistEnabled}
          navigationEnabled={navigationEnabled}
          powerSavingEnabled={powerSavingEnabled}
          onQuietModeChange={onQuietModeChange}
          onDriveAssistChange={onDriveAssistChange}
          onNavigationChange={onNavigationChange}
          onPowerSavingChange={onPowerSavingChange}
          onNVToggle={onNVToggle}
          onResChange={onResChange}
          onAction={onAction}
          focusMode={focusMode}
          onFocusChange={onFocusChange}
          controlMode={controlMode}
          onControlModeChange={onControlModeChange}
          lidarMinimapEnabled={lidarMinimapEnabled}
          onLidarMinimapChange={onLidarMinimapChange}
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
  ambientTemperatureC,
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
  onToggleBackupView,
  backupViewEnabled,
  onFeederTreat,
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
    onToggleBackupView,
    backupViewEnabled,
    onTreat: onFeederTreat,
  };

  const schematic = metricsPanelEnabled ? (
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
  ) : null;

  const joystickCenter = metricsPanelEnabled ? schematic : null;

  const renderControlCluster = () => (
    <ControlCluster
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
      onToggleBackupView={onToggleBackupView}
      backupViewEnabled={backupViewEnabled}
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
            {!isMobile && controlMode === "keyboard" && renderControlCluster()}
            {!isMobile && controlMode === "joystick" && (
              <DualJoystickControls {...joystickProps}>{joystickCenter}</DualJoystickControls>
            )}

            {isMobile && controlMode === "keyboard" && renderControlCluster()}
          </>
        ) : null}
      </div>
    </div>
  );
}

