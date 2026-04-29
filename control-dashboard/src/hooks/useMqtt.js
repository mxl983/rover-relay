import { useEffect, useRef, useState } from "react";
import mqtt from "mqtt";
import { MQTT_HOST } from "../config";

const HEARTBEAT_TOPIC = "rover/esp/heartbeat";
const DASHBOARD_BASE_PATH = import.meta.env.BASE_URL || "/";

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
 * Connects to MQTT when sessionCreds is set. Subscribes to ESP heartbeat and exposes client ref.
 * @param {{ username: string; password: string } | null} sessionCreds
 * @returns {{ isEspOnline: boolean; mqttClientRef: React.MutableRefObject<mqtt.MqttClient | null> }}
 */
export function useMqtt(sessionCreds) {
  const [isEspOnline, setIsEspOnline] = useState(false);
  const mqttClientRef = useRef(null);
  const didWakeRef = useRef(false);

  useEffect(() => {
    if (!sessionCreds) return;
    didWakeRef.current = false;

    const isDashboardUrlHit = () => {
      if (typeof window === "undefined") return false;
      return isDashboardPath(window.location?.pathname);
    };

    const canWakeNow = () => {
      if (typeof document === "undefined") return false;
      return document.visibilityState === "visible" && !document.hidden && isDashboardUrlHit();
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
      client.publish("rover/power/pi", "On", { qos: 1 });
      client.publish("rover/power/aux", "On", { qos: 1 });
      didWakeRef.current = true;
    };

    // Wake only when page is actively visible and opened on dashboard URL.
    client.on("connect", tryWakeRover);

    client.subscribe(HEARTBEAT_TOPIC, (err) => {
      if (err) return;
    });

    client.on("message", (topic) => {
      if (topic === HEARTBEAT_TOPIC) setIsEspOnline(true);
    });

    const onVisible = () => tryWakeRover();
    window.addEventListener("focus", onVisible);
    document.addEventListener("visibilitychange", onVisible);

    return () => {
      window.removeEventListener("focus", onVisible);
      document.removeEventListener("visibilitychange", onVisible);
      client.end();
    };
  }, [sessionCreds]);

  return { isEspOnline, mqttClientRef };
}
