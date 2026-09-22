import { useCallback, useEffect, useRef, useState } from "react";
import {
  JOYSTICK_DRIVE_DEBUG,
  MENTOR_CMD_VEL_ENDPOINT,
  MENTOR_GIMBAL_ENDPOINT,
  MENTOR_SPEED_LEVELS,
  MENTOR_STATUS_ENDPOINT,
  PI_ACTIVITY_ENDPOINT,
  PI_PANEL_ENDPOINT,
  PI_POWER_SAVING_ENDPOINT,
  resolveMentorSpeedLimits,
  stickToCmdVel,
} from "../config";
import { apiPostJson, apiFetch } from "../api/client";
import { getBatteryPercentage } from "../utils/batteryFromVoltage.js";

const STATUS_POLL_MS = 5000;
const PANEL_POLL_MS = 5000;
const TTL_POLL_MS = 5000;
/** Keep idle timer alive while the dashboard tab is visible. */
const VISIBLE_HEARTBEAT_MS = 5000;
const ACTIVITY_TOUCH_MIN_MS = 4000;
const GIMBAL_DEAD = 0.35;
const SPEED_STORAGE_KEY = "rover-dashboard-mentor-speed";

function readInitialSpeedLevel() {
  if (typeof window === "undefined") return "fast";
  try {
    const v = window.localStorage.getItem(SPEED_STORAGE_KEY);
    // Prefer Fast as the default operating tier; honor Slow; migrate Mid/unknown → Fast.
    if (v === "slow") return "slow";
    if (v === "fast") return "fast";
  } catch {
    /* ignore */
  }
  return "fast";
}

/** WASD + Q/E only — arrow keys are gimbal on the keyboard cluster. */
function keysToStick(keys) {
  const set = new Set(
    (Array.isArray(keys) ? keys : []).map((k) => String(k).toLowerCase()),
  );
  let x = 0;
  let y = 0;
  let strafe = 0;
  if (set.has("w")) y -= 1;
  if (set.has("s")) y += 1;
  if (set.has("a")) x -= 1;
  if (set.has("d")) x += 1;
  // Lateral strafe (mecanum): Q left, E right
  if (set.has("q")) strafe -= 1;
  if (set.has("e")) strafe += 1;
  const mag = Math.hypot(x, y);
  if (mag > 1) {
    x /= mag;
    y /= mag;
  }
  return { x, y, strafe };
}

/** Arrow keys → gimbal stick (same axes as on-screen gimbal pad). */
function keysToGimbal(keys) {
  const set = new Set(
    (Array.isArray(keys) ? keys : []).map((k) => String(k).toLowerCase()),
  );
  let x = 0;
  let y = 0;
  if (set.has("arrowup")) y -= 1;
  if (set.has("arrowdown")) y += 1;
  if (set.has("arrowleft")) x -= 1;
  if (set.has("arrowright")) x += 1;
  return { x, y };
}

function gimbalStickToAction(gimbal) {
  const x = Number(gimbal?.x ?? 0) || 0;
  const y = Number(gimbal?.y ?? 0) || 0;
  if (Math.abs(x) < GIMBAL_DEAD && Math.abs(y) < GIMBAL_DEAD) return null;
  if (Math.abs(x) >= Math.abs(y)) return x > 0 ? "right" : "left";
  // Dashboard gimbal y: negative often = look up (mouse look); match web_car up/down.
  return y < 0 ? "up" : "down";
}

/**
 * MentorPi control channel (web_car HTTP → ROS cmd_vel / gimbal / status).
 * Replaces the old rover :3000 WebSocket for drive on this chassis.
 */
export function useMentorPiControl() {
  const [stats, setStats] = useState({});
  const [isOnline, setIsOnline] = useState(false);
  const [hasEverConnected, setHasEverConnected] = useState(false);
  const [speedLevel, setSpeedLevelState] = useState(readInitialSpeedLevel);
  const speedLevelRef = useRef(speedLevel);
  speedLevelRef.current = speedLevel;
  const lastCmdRef = useRef({ linear_x: 0, linear_y: 0, angular_z: 0 });
  const lastGimbalActionRef = useRef(null);
  const inFlightRef = useRef(false);
  /** Latest pending control payload while a request is in flight (coalesce). */
  const pendingControlRef = useRef(null);
  const flushControlRef = useRef(() => {});

  const setSpeedLevel = useCallback((level) => {
    const next = MENTOR_SPEED_LEVELS[level] ? level : "fast";
    setSpeedLevelState(next);
    try {
      window.localStorage.setItem(SPEED_STORAGE_KEY, next);
    } catch {
      /* ignore */
    }
  }, []);

  useEffect(() => {
    let cancelled = false;
    let timer = null;

    const poll = async () => {
      try {
        const res = await apiFetch(MENTOR_STATUS_ENDPOINT, {
          timeout: 2500,
          retries: 0,
        });
        if (!res.ok) throw new Error(`status ${res.status}`);
        const body = await res.json();
        if (cancelled) return;
        setIsOnline(true);
        setHasEverConnected(true);
        const pctReported = Number(body?.battery_percent);
        const mv = Number(body?.battery_mv);
        const voltageV = Number.isFinite(mv) ? mv / 1000 : null;
        const pctFromVoltage =
          voltageV != null ? getBatteryPercentage(voltageV) : null;
        setStats((prev) => ({
          ...prev,
          battery:
            pctFromVoltage != null
              ? pctFromVoltage
              : Number.isFinite(pctReported)
                ? pctReported
                : prev.battery,
          voltage: voltageV != null ? voltageV : prev.voltage,
          trip_m: body?.trip_m,
          dirs: body?.dirs,
          wifiSignal: prev.wifiSignal,
          latency: prev.latency,
        }));
      } catch {
        if (!cancelled) setIsOnline(false);
      } finally {
        if (!cancelled) timer = setTimeout(poll, STATUS_POLL_MS);
      }
    };

    void poll();
    return () => {
      cancelled = true;
      if (timer) clearTimeout(timer);
    };
  }, []);

  // Panel metrics (CPU temp/load) from pi-server rover-control on :3000.
  // Does not reset idle TTL — use /power-saving for countdown and /activity for keep-alive.
  useEffect(() => {
    let cancelled = false;
    let timer = null;

    const pollPanel = async () => {
      try {
        const res = await apiFetch(PI_PANEL_ENDPOINT, {
          timeout: 2500,
          retries: 0,
        });
        if (!res.ok) throw new Error(`panel ${res.status}`);
        const body = await res.json();
        if (cancelled) return;
        const cpuTemp = Number(body?.cpuTemp);
        const cpuLoad = Number(body?.cpuLoad);
        const wifiSignal = Number(body?.wifiSignal);
        setStats((prev) => ({
          ...prev,
          cpuTemp: Number.isFinite(cpuTemp) ? cpuTemp : prev.cpuTemp,
          cpuLoad: Number.isFinite(cpuLoad) ? cpuLoad : prev.cpuLoad,
          wifiSignal: Number.isFinite(wifiSignal) ? wifiSignal : prev.wifiSignal,
        }));
      } catch {
        /* panel optional while MentorPi drive still works */
      } finally {
        if (!cancelled) timer = setTimeout(pollPanel, PANEL_POLL_MS);
      }
    };

    void pollPanel();
    return () => {
      cancelled = true;
      if (timer) clearTimeout(timer);
    };
  }, []);

  // Idle TTL from GET /api/system/power-saving (read-only; does not reset activity).
  useEffect(() => {
    let cancelled = false;
    let timer = null;

    const applyPowerSaving = (body) => {
      const ttlMs = Number(body?.ttlMs);
      const powerSavingEnabled =
        typeof body?.enabled === "boolean"
          ? body.enabled
          : typeof body?.powerSavingEnabled === "boolean"
            ? body.powerSavingEnabled
            : undefined;
      const powerSavingTimeoutMinutes = Number(
        body?.timeoutMinutes ?? body?.powerSavingTimeoutMinutes,
      );
      setStats((prev) => ({
        ...prev,
        ttlMs:
          powerSavingEnabled === false
            ? null
            : Number.isFinite(ttlMs)
              ? ttlMs
              : prev.ttlMs,
        ttlPolledAtMs: Number.isFinite(ttlMs) ? Date.now() : prev.ttlPolledAtMs,
        displayTtlMs:
          powerSavingEnabled === false
            ? null
            : Number.isFinite(ttlMs)
              ? ttlMs
              : prev.displayTtlMs,
        powerSavingEnabled:
          powerSavingEnabled !== undefined
            ? powerSavingEnabled
            : prev.powerSavingEnabled,
        powerSavingTimeoutMinutes: Number.isFinite(powerSavingTimeoutMinutes)
          ? powerSavingTimeoutMinutes
          : prev.powerSavingTimeoutMinutes,
      }));
    };

    const pollTtl = async () => {
      try {
        const res = await apiFetch(PI_POWER_SAVING_ENDPOINT, {
          timeout: 2500,
          retries: 0,
        });
        if (!res.ok) throw new Error(`power-saving ${res.status}`);
        const body = await res.json();
        if (!cancelled) applyPowerSaving(body);
      } catch {
        /* optional */
      } finally {
        if (!cancelled) timer = setTimeout(pollTtl, TTL_POLL_MS);
      }
    };

    void pollTtl();
    return () => {
      cancelled = true;
      if (timer) clearTimeout(timer);
    };
  }, []);

  // Smooth 1s local countdown between 5s TTL polls.
  useEffect(() => {
    const id = setInterval(() => {
      setStats((prev) => {
        if (prev.powerSavingEnabled === false) {
          if (prev.ttlMs == null) return prev;
          return { ...prev, ttlMs: null };
        }
        const base = Number(prev.ttlMs);
        const polledAt = Number(prev.ttlPolledAtMs);
        if (!Number.isFinite(base) || !Number.isFinite(polledAt)) return prev;
        const elapsed = Date.now() - polledAt;
        const next = Math.max(0, base - elapsed);
        // Keep base + polledAt stable; expose display via recomputing in render…
        // Store displayTtlMs for HUD so we don't mutate the poll baseline.
        if (prev.displayTtlMs === next) return prev;
        return { ...prev, displayTtlMs: next };
      });
    }, 1000);
    return () => clearInterval(id);
  }, []);

  const lastActivityTouchRef = useRef(0);
  const applyActivitySnapshot = useCallback((body) => {
    const ttlMs = Number(body?.ttlMs);
    if (!Number.isFinite(ttlMs) && typeof body?.enabled !== "boolean") return;
    setStats((prev) => ({
      ...prev,
      ttlMs: Number.isFinite(ttlMs) ? ttlMs : prev.ttlMs,
      ttlPolledAtMs: Number.isFinite(ttlMs) ? Date.now() : prev.ttlPolledAtMs,
      displayTtlMs: Number.isFinite(ttlMs) ? ttlMs : prev.displayTtlMs,
      powerSavingEnabled:
        typeof body?.enabled === "boolean" ? body.enabled : prev.powerSavingEnabled,
    }));
  }, []);

  const touchActivity = useCallback(
    (force = false) => {
      const now = Date.now();
      if (!force && now - lastActivityTouchRef.current < ACTIVITY_TOUCH_MIN_MS) {
        return;
      }
      lastActivityTouchRef.current = now;
      void apiPostJson(PI_ACTIVITY_ENDPOINT, {}, { timeout: 2000, retries: 0 })
        .then((body) => applyActivitySnapshot(body))
        .catch(() => {});
    },
    [applyActivitySnapshot],
  );

  // Visible-tab heartbeat: reset idle TTL every 5s while the dashboard is on-screen.
  // Hidden / background tabs stop heartbeats so the Pi can still idle-shutdown.
  useEffect(() => {
    let cancelled = false;
    let timer = null;

    const clearTimer = () => {
      if (timer != null) {
        clearTimeout(timer);
        timer = null;
      }
    };

    const isDashboardVisible = () =>
      typeof document === "undefined" || document.visibilityState === "visible";

    const scheduleNext = () => {
      clearTimer();
      if (!cancelled) timer = setTimeout(() => void beat(), VISIBLE_HEARTBEAT_MS);
    };

    const beat = async () => {
      if (cancelled) return;
      if (isDashboardVisible()) {
        try {
          const body = await apiPostJson(
            PI_ACTIVITY_ENDPOINT,
            {},
            { timeout: 2000, retries: 0 },
          );
          if (!cancelled) {
            lastActivityTouchRef.current = Date.now();
            applyActivitySnapshot(body);
          }
        } catch {
          /* optional while offline */
        }
      }
      scheduleNext();
    };

    const onVisibility = () => {
      if (!isDashboardVisible()) return;
      clearTimer();
      void beat();
    };

    document.addEventListener("visibilitychange", onVisibility);
    void beat();

    return () => {
      cancelled = true;
      clearTimer();
      document.removeEventListener("visibilitychange", onVisibility);
    };
  }, [applyActivitySnapshot]);

  const postCmdVel = useCallback(async (twist) => {
    const body = {
      linear_x: Number(twist.linear_x) || 0,
      linear_y: Number(twist.linear_y) || 0,
      angular_z: Number(twist.angular_z) || 0,
    };
    lastCmdRef.current = body;
    const t0 = performance.now();
    try {
      const res = await apiPostJson(MENTOR_CMD_VEL_ENDPOINT, body, {
        timeout: 800,
        retries: 0,
      });
      const ms = Math.round(performance.now() - t0);
      setStats((prev) => ({ ...prev, latency: ms }));
      setIsOnline(true);
      setHasEverConnected(true);
      return res;
    } catch (err) {
      setIsOnline(false);
      throw err;
    }
  }, []);

  const postGimbal = useCallback(async (action) => {
    await apiPostJson(
      MENTOR_GIMBAL_ENDPOINT,
      { action },
      { timeout: 800, retries: 0 },
    );
  }, []);

  const dispatchControl = useCallback(
    async (payload) => {
      const noteActivity = (active) => {
        if (active) touchActivity();
      };

      if (Array.isArray(payload)) {
        const stick = keysToStick(payload);
        const gimbal = keysToGimbal(payload);
        const twist = stickToCmdVel(stick, speedLevelRef.current);
        const moving =
          Boolean(stick.x || stick.y || stick.strafe) ||
          gimbalStickToAction(gimbal) != null;
        noteActivity(moving);
        if (JOYSTICK_DRIVE_DEBUG && (stick.x || stick.y || stick.strafe)) {
          // eslint-disable-next-line no-console
          console.log("[drive→mentor]", stick, twist);
        }
        const tasks = [postCmdVel(twist)];
        const action = gimbalStickToAction(gimbal);
        if (action) {
          lastGimbalActionRef.current = action;
          tasks.push(postGimbal(action));
        } else if (lastGimbalActionRef.current) {
          lastGimbalActionRef.current = null;
          tasks.push(postGimbal("stop"));
        }
        await Promise.all(tasks);
        return;
      }

      if (payload?.command === "reset_servos" || payload?.command === "look_down") {
        const action = payload.command === "look_down" ? "down" : "center";
        lastGimbalActionRef.current = action;
        noteActivity(true);
        await postGimbal(action);
        return;
      }

      const tasks = [];
      let active = false;

      if (payload?.drive != null) {
        const twist = stickToCmdVel(payload.drive, speedLevelRef.current);
        const { x, y, strafe } = payload.drive;
        if (x || y || strafe) active = true;
        if (JOYSTICK_DRIVE_DEBUG) {
          if (x || y || strafe) {
            // eslint-disable-next-line no-console
            console.log("[drive→mentor]", payload.drive, twist);
          }
        }
        tasks.push(postCmdVel(twist));
      } else if (
        payload?.drive === undefined &&
        payload?.gimbal === undefined &&
        !payload?.command
      ) {
        tasks.push(postCmdVel({ linear_x: 0, linear_y: 0, angular_z: 0 }));
      }

      if (payload?.gimbal != null) {
        const action = gimbalStickToAction(payload.gimbal);
        if (action) {
          active = true;
          // Keepalive refresh even for same direction (MentorPi hold window ~0.4s).
          lastGimbalActionRef.current = action;
          tasks.push(postGimbal(action));
        } else if (lastGimbalActionRef.current) {
          lastGimbalActionRef.current = null;
          tasks.push(postGimbal("stop"));
        }
      }

      noteActivity(active);
      if (tasks.length) await Promise.all(tasks);
    },
    [postCmdVel, postGimbal, touchActivity],
  );

  const flushControl = useCallback(async () => {
    if (inFlightRef.current) return;
    inFlightRef.current = true;
    try {
      while (pendingControlRef.current != null) {
        const next = pendingControlRef.current;
        pendingControlRef.current = null;
        try {
          await dispatchControl(next);
        } catch (err) {
          if (JOYSTICK_DRIVE_DEBUG) {
            // eslint-disable-next-line no-console
            console.warn("[mentor control]", err?.message ?? err);
          }
        }
      }
    } finally {
      inFlightRef.current = false;
      if (pendingControlRef.current != null) {
        void flushControlRef.current();
      }
    }
  }, [dispatchControl]);
  flushControlRef.current = flushControl;

  const sendControl = useCallback(
    (payload) => {
      // Latest-wins: coalesce while a request is outstanding so Flask isn't
      // flooded with stale overlapping POSTs (major source of RTT inflation).
      if (Array.isArray(payload) || payload?.command) {
        pendingControlRef.current = payload;
      } else if (payload && typeof payload === "object") {
        const prev = pendingControlRef.current;
        if (prev && typeof prev === "object" && !Array.isArray(prev) && !prev.command) {
          pendingControlRef.current = { ...prev, ...payload };
        } else {
          pendingControlRef.current = { ...payload };
        }
      } else {
        pendingControlRef.current = payload;
      }
      void flushControl();
      return true;
    },
    [flushControl],
  );

  // Stop on unmount.
  useEffect(
    () => () => {
      void apiPostJson(
        MENTOR_CMD_VEL_ENDPOINT,
        { linear_x: 0, linear_y: 0, angular_z: 0 },
        { timeout: 1000, retries: 0 },
      ).catch(() => {});
    },
    [],
  );

  return {
    stats,
    driveAssistUpdate: null,
    imu: null,
    imuLive: false,
    isOnline,
    hasEverConnected,
    sendControl,
    speedLevel,
    setSpeedLevel,
    limits: resolveMentorSpeedLimits(speedLevel),
  };
}
