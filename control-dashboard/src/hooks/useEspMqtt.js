import { useEffect, useRef, useState } from "react";
import mqtt from "mqtt";
import { MQTT_HOST } from "../config";
import {
  MQTT_STATUS_TOPIC,
  isEspPongPayload,
  publishPing,
  publishPowerOn,
} from "../mqttPower";

const HEARTBEAT_TOPIC = "rover/esp/heartbeat";
const DASHBOARD_BASE_PATH = import.meta.env.BASE_URL || "/";

/** How often to PING while the loading overlay is up. */
const PING_INTERVAL_MS = 3000;
/** No ESP ack within this window → treat main power as cut. */
const ESP_ACK_TIMEOUT_MS = 8000;

function isDashboardPath(pathname) {
  const p = pathname || "/";
  const baseNoTrailingSlash =
    DASHBOARD_BASE_PATH.endsWith("/") && DASHBOARD_BASE_PATH.length > 1
      ? DASHBOARD_BASE_PATH.slice(0, -1)
      : DASHBOARD_BASE_PATH;
  if (baseNoTrailingSlash === "/") return p === "/" || p.startsWith("/rover");
  return p === baseNoTrailingSlash || p.startsWith(`${baseNoTrailingSlash}/`);
}

/**
 * Connects to MQTT when sessionCreds is set. Subscribes to ESP status/heartbeat
 * and publishes ON GPIO13 once when the dashboard is visibly loaded.
 * While `probeEsp` is true (loading screen), periodically PINGs the ESP; lack of
 * ACK means main power is cut.
 *
 * @param {{ username: string; password: string } | null} sessionCreds
 * @param {{ probeEsp?: boolean }} [options]
 */
export function useEspMqtt(sessionCreds, { probeEsp = false } = {}) {
  const [isEspOnline, setIsEspOnline] = useState(false);
  /** true when probing and ESP has not acked recently → main rail cut */
  const [espPoweredOff, setEspPoweredOff] = useState(false);
  const mqttClientRef = useRef(null);
  const didWakeRef = useRef(false);
  const lastAckAtRef = useRef(0);
  const probeStartedAtRef = useRef(0);

  const noteEspAck = () => {
    lastAckAtRef.current = Date.now();
    setIsEspOnline(true);
    setEspPoweredOff(false);
  };

  useEffect(() => {
    if (!sessionCreds) return;
    didWakeRef.current = false;
    lastAckAtRef.current = 0;
    setIsEspOnline(false);
    setEspPoweredOff(false);

    const isDashboardUrlHit = () => {
      if (typeof window === "undefined") return false;
      return isDashboardPath(window.location?.pathname);
    };

    const canWakeNow = () => {
      if (typeof document === "undefined") return false;
      return (
        document.visibilityState === "visible" &&
        !document.hidden &&
        isDashboardUrlHit()
      );
    };

    const client = mqtt.connect(MQTT_HOST, {
      username: sessionCreds.username,
      password: sessionCreds.password,
      clientId: `heartbeat_web_${Math.random().toString(16).slice(2, 8)}`,
    });

    mqttClientRef.current = client;

    const tryWakeRover = () => {
      if (didWakeRef.current) return;
      if (client.connected !== true) return;
      if (!canWakeNow()) return;
      publishPowerOn(client);
      didWakeRef.current = true;
    };

    client.on("connect", tryWakeRover);

    client.subscribe([HEARTBEAT_TOPIC, MQTT_STATUS_TOPIC], (err) => {
      if (err) return;
    });

    client.on("message", (topic, payloadBuf) => {
      if (topic === HEARTBEAT_TOPIC) {
        noteEspAck();
        return;
      }
      if (topic === MQTT_STATUS_TOPIC) {
        const text =
          typeof payloadBuf === "string"
            ? payloadBuf
            : new TextDecoder().decode(payloadBuf);
        // Any status traffic counts; PONG is the explicit ping reply.
        if (isEspPongPayload(text) || text.trim().length > 0) {
          noteEspAck();
        }
      }
    });

    const onVisible = () => tryWakeRover();
    window.addEventListener("focus", onVisible);
    document.addEventListener("visibilitychange", onVisible);

    return () => {
      window.removeEventListener("focus", onVisible);
      document.removeEventListener("visibilitychange", onVisible);
      client.end();
      mqttClientRef.current = null;
    };
  }, [sessionCreds]);

  // Loading-screen probe: PING ESP on an interval; mark powered-off if no ACK.
  useEffect(() => {
    if (!sessionCreds || !probeEsp) {
      setEspPoweredOff(false);
      probeStartedAtRef.current = 0;
      return undefined;
    }

    probeStartedAtRef.current = Date.now();
    let cancelled = false;

    const tick = () => {
      if (cancelled) return;
      const client = mqttClientRef.current;
      if (client?.connected) {
        publishPing(client);
      }
      const now = Date.now();
      const probingFor = now - probeStartedAtRef.current;
      const sinceAck = now - (lastAckAtRef.current || 0);
      const timedOut =
        probingFor >= ESP_ACK_TIMEOUT_MS &&
        (lastAckAtRef.current === 0 || sinceAck >= ESP_ACK_TIMEOUT_MS);
      setEspPoweredOff(Boolean(timedOut));
    };

    tick();
    const id = window.setInterval(tick, PING_INTERVAL_MS);
    return () => {
      cancelled = true;
      window.clearInterval(id);
    };
  }, [sessionCreds, probeEsp]);

  return { isEspOnline, espPoweredOff, mqttClientRef };
}
