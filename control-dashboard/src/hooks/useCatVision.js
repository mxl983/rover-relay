import { useEffect, useRef, useState } from "react";
import { getCatVisionWsUrl } from "../utils/catVisionApi.js";

/**
 * Stable fingerprint so ~15 Hz VISION_UPDATE with an unchanged box
 * does not thrash React state / gimbal effects.
 */
function catFingerprint(cat) {
  if (!cat || !cat.cat_present) return "none";
  const box = Array.isArray(cat.bbox_norm) ? cat.bbox_norm : [];
  const boxKey = box.map((v) => Number(v).toFixed(2)).join(",");
  const posture = cat.posture || "";
  const pconf = Number.isFinite(cat.posture_conf)
    ? cat.posture_conf.toFixed(2)
    : "";
  const fps = Number.isFinite(cat.detect_fps) ? cat.detect_fps.toFixed(0) : "";
  return `${boxKey}|${posture}|${pconf}|${fps}`;
}

/**
 * Subscribe to vision box /ws/vision while cat vision is enabled.
 * @returns {{ cat: object|null, live: boolean, error: string|null }}
 */
export function useCatVision(enabled) {
  const [cat, setCat] = useState(null);
  const [live, setLive] = useState(false);
  const [error, setError] = useState(null);
  const wsRef = useRef(null);
  const lastFpRef = useRef("");

  useEffect(() => {
    if (!enabled) {
      setCat(null);
      setLive(false);
      setError(null);
      lastFpRef.current = "";
      if (wsRef.current) {
        wsRef.current.close();
        wsRef.current = null;
      }
      return undefined;
    }

    let cancelled = false;
    let reconnectTimer = null;

    const connect = () => {
      if (cancelled) return;
      const url = getCatVisionWsUrl();
      let ws;
      try {
        ws = new WebSocket(url);
      } catch (err) {
        setError(err?.message || "cat vision ws failed");
        setLive(false);
        reconnectTimer = setTimeout(connect, 2500);
        return;
      }
      wsRef.current = ws;

      ws.onopen = () => {
        if (cancelled) return;
        setLive(true);
        setError(null);
      };
      ws.onmessage = (ev) => {
        if (cancelled) return;
        try {
          const msg = JSON.parse(ev.data);
          if (msg?.type !== "VISION_UPDATE") return;
          const next = msg.cat ?? null;
          const fp = catFingerprint(next);
          if (fp === lastFpRef.current) return;
          lastFpRef.current = fp;
          setCat(next);
        } catch {
          /* ignore */
        }
      };
      ws.onerror = () => {
        if (cancelled) return;
        setError("cat vision ws error");
      };
      ws.onclose = () => {
        if (cancelled) return;
        setLive(false);
        wsRef.current = null;
        reconnectTimer = setTimeout(connect, 2500);
      };
    };

    connect();

    return () => {
      cancelled = true;
      if (reconnectTimer) clearTimeout(reconnectTimer);
      if (wsRef.current) {
        wsRef.current.close();
        wsRef.current = null;
      }
    };
  }, [enabled]);

  return { cat, live, error };
}
