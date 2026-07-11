import { useEffect, useRef, useState } from "react";
import { PI_WEBSOCKET, DRIVE_ASSIST_DEBUG } from "../config";
import { getBatteryPercentage } from "../utils/batteryFromVoltage.js";
import { remapReportedBatteryPctRounded } from "../utils/batteryPctScale.js";
import { logDriveAssistInfoDetail } from "../utils/driveAssistApi.js";
import { fetchImuSample } from "../utils/imuApi.js";
import { isImuLive, normalizeImuSample } from "../utils/imuData.js";
import { resetImuDebugLog } from "../utils/imuDebugLog.js";

const PING_INTERVAL_MS = 3000;
const HEARTBEAT_STALE_MS = 5000;
const RECONNECT_BASE_MS = 600;
const RECONNECT_MAX_MS = 8000;
const IMU_STALE_CHECK_MS = 250;

export function usePiWebSocket() {
  const socketRef = useRef(null);
  const [stats, setStats] = useState({});
  const [driveAssistUpdate, setDriveAssistUpdate] = useState(null);
  const [imu, setImu] = useState(null);
  const [imuLive, setImuLive] = useState(false);
  const [isOnline, setIsOnline] = useState(false);
  const [hasEverConnected, setHasEverConnected] = useState(false);
  const lastPingTime = useRef(0);
  const lastHeartBeat = useRef(0);
  const reconnectAttemptRef = useRef(0);
  const imuRef = useRef(null);

  useEffect(() => {
    let socket;
    let reconnectTimeout = null;
    let isUnmounted = false;

    const sendClientInfo = (sock, location = null) => {
      if (!sock || sock.readyState !== WebSocket.OPEN) return;
      const device = {
        userAgent: navigator.userAgent,
        screenWidth: window.screen?.width,
        screenHeight: window.screen?.height,
        platform: navigator.platform,
        language: navigator.language,
      };
      sock.send(
        JSON.stringify({
          type: "CLIENT_INFO",
          device,
          location,
        }),
      );
    };

    const applyImuSample = (sample) => {
      if (!sample) return;
      imuRef.current = sample;
      setImu(sample);
      setImuLive(isImuLive(sample));
    };

    const bootstrapImu = async () => {
      try {
        const sample = await fetchImuSample();
        if (!isUnmounted && sample) {
          applyImuSample(sample);
        }
      } catch {
        // REST fallback is optional; WebSocket is primary.
      }
    };

    const connect = () => {
      socket = new WebSocket(PI_WEBSOCKET);
      socketRef.current = socket;

      socket.onopen = () => {
        setIsOnline(true);
        setHasEverConnected(true);
        reconnectAttemptRef.current = 0;
        lastHeartBeat.current = Date.now();
        sendClientInfo(socket);
        void bootstrapImu();
        if (navigator.geolocation?.getCurrentPosition) {
          navigator.geolocation.getCurrentPosition(
            (pos) => {
              sendClientInfo(socketRef.current, {
                latitude: pos.coords.latitude,
                longitude: pos.coords.longitude,
                accuracy: pos.coords.accuracy,
              });
            },
            () => {},
            { timeout: 2000, maximumAge: 60000 },
          );
        }
      };

      socket.onmessage = (e) => {
        try {
          const data = JSON.parse(e.data);
          if (data.type === "PONG") {
            lastHeartBeat.current = Date.now();
            setIsOnline(true);
            setStats((prev) => ({
              ...prev,
              latency: Date.now() - lastPingTime.current,
            }));
            return;
          }

          if (data.type === "DRIVE_ASSIST_UPDATE") {
            const payload = data?.data && typeof data.data === "object" ? data.data : null;
            if (payload?.active === false) {
              setDriveAssistUpdate(null);
            } else if (payload?.active === true) {
              setDriveAssistUpdate(payload);
            }
            if (DRIVE_ASSIST_DEBUG && payload) {
              logDriveAssistInfoDetail("WS DRIVE_ASSIST_UPDATE", payload);
            }
            return;
          }

          if (data.type === "IMU_UPDATE") {
            const sample = normalizeImuSample(data?.data);
            if (sample) {
              applyImuSample(sample);
            }
            return;
          }

          const raw = data?.data && typeof data.data === "object" ? { ...data.data } : {};
            if (Number.isFinite(Number(raw.voltage))) {
              const pct = getBatteryPercentage(Number(raw.voltage));
              if (pct != null) {
                raw.battery = pct;
              }
            } else if (raw.battery != null && raw.battery !== "") {
              const mapped = remapReportedBatteryPctRounded(raw.battery);
              if (mapped != null) raw.battery = mapped;
            }
            setStats((prev) => ({ ...prev, ...raw }));
        } catch {
          // ignore parse errors
        }
      };

      socket.onclose = () => {
        if (isUnmounted) return;
        setDriveAssistUpdate(null);
        imuRef.current = null;
        setImu(null);
        setImuLive(false);
        resetImuDebugLog();
        setIsOnline(false);
        const attempt = reconnectAttemptRef.current;
        const base = Math.min(RECONNECT_BASE_MS * 2 ** attempt, RECONNECT_MAX_MS);
        const jitter = Math.floor(Math.random() * 220);
        reconnectAttemptRef.current += 1;
        reconnectTimeout = setTimeout(connect, base + jitter);
      };

      socket.onerror = () => {
        try {
          socket.close();
        } catch {
          // ignore close races
        }
      };
    };

    connect();

    const pingInterval = setInterval(() => {
      if (document.hidden) return;
      if (socket?.readyState === WebSocket.OPEN) {
        lastPingTime.current = Date.now();
        socket.send(JSON.stringify({ type: "PING" }));
      }
      if (Date.now() - lastHeartBeat.current > HEARTBEAT_STALE_MS) {
        setIsOnline(false);
      }
    }, PING_INTERVAL_MS);

    const imuStaleTimer = setInterval(() => {
      const sample = imuRef.current;
      setImuLive(Boolean(sample && isImuLive(sample)));
    }, IMU_STALE_CHECK_MS);

    return () => {
      isUnmounted = true;
      clearInterval(pingInterval);
      clearInterval(imuStaleTimer);
      clearTimeout(reconnectTimeout);
      socket?.close();
    };
  }, []);

  const sendControl = (payload) => {
    if (socketRef.current?.readyState !== WebSocket.OPEN) return;
    // Only use payload wrapper for command-only messages (e.g. toggle_laser); keep drive/gimbal at top level for compatibility
    const hasCommand = payload && payload.command !== undefined;
    const msg = hasCommand
      ? { type: "DRIVE", payload }
      : Array.isArray(payload)
        ? { type: "DRIVE", payload }
        : { type: "DRIVE", drive: payload.drive, gimbal: payload.gimbal };
    socketRef.current.send(JSON.stringify(msg));
  };

  return { stats, driveAssistUpdate, imu, imuLive, isOnline, hasEverConnected, socketRef, sendControl };
}
