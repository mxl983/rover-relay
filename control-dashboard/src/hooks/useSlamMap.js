import { useEffect, useRef, useState } from "react";
import { getSlamWebSocketUrl, SLAM_MAP_ENDPOINT } from "../config";

const STALE_MS = 8000;

/**
 * Subscribe to relay SLAM map updates when enabled.
 * @param {boolean} enabled
 */
export function useSlamMap(enabled) {
  const [map, setMap] = useState(null);
  const [isLive, setIsLive] = useState(false);
  const [error, setError] = useState(null);
  const lastKeyRef = useRef("");
  const lastMessageAtRef = useRef(0);

  useEffect(() => {
    if (!enabled) {
      setMap(null);
      setIsLive(false);
      setError(null);
      lastKeyRef.current = "";
      lastMessageAtRef.current = 0;
      return undefined;
    }

    let cancelled = false;

    const applyMap = (payload) => {
      if (cancelled) return;
      setMap(payload);
      setError(null);
      lastMessageAtRef.current = Date.now();
      const key = `${payload?.stamp ?? ""}:${payload?.updated_at ?? ""}:${payload?.scan_count ?? ""}`;
      const fresh = key !== lastKeyRef.current;
      if (fresh) lastKeyRef.current = key;
      setIsLive(fresh || Date.now() - lastMessageAtRef.current <= STALE_MS);
    };

    const loadInitial = async () => {
      try {
        const response = await fetch(SLAM_MAP_ENDPOINT, { cache: "no-store" });
        const body = await response.json();
        if (!response.ok || !body?.success) {
          throw new Error(body?.error || `SLAM map HTTP ${response.status}`);
        }
        const { success: _success, ...payload } = body;
        applyMap(payload);
      } catch (e) {
        if (!cancelled) {
          setError(e instanceof Error ? e.message : "SLAM map unavailable");
        }
      }
    };

    let ws = null;
    let reconnectTimer = null;
    let staleTimer = null;

    const markStale = () => {
      if (cancelled) return;
      const age = Date.now() - lastMessageAtRef.current;
      if (!lastMessageAtRef.current || age > STALE_MS) {
        setIsLive(false);
      }
    };

    const connect = () => {
      if (cancelled) return;
      try {
        ws = new WebSocket(getSlamWebSocketUrl());
      } catch {
        reconnectTimer = setTimeout(connect, 2500);
        return;
      }

      ws.onopen = () => {
        if (!cancelled) setError(null);
      };

      ws.onmessage = (ev) => {
        if (cancelled) return;
        try {
          const msg = JSON.parse(ev.data);
          if (msg.type !== "relay.slam.map") return;
          if (!msg.success) {
            setError(msg.error || "SLAM map unavailable");
            setIsLive(false);
            return;
          }
          const { type: _type, success: _success, error: _error, ts: _ts, ...payload } = msg;
          applyMap(payload);
        } catch {
          /* ignore malformed frames */
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
        setIsLive(false);
        reconnectTimer = setTimeout(connect, 2500);
      };
    };

    void loadInitial();
    connect();
    staleTimer = setInterval(markStale, 1000);

    return () => {
      cancelled = true;
      if (reconnectTimer) clearTimeout(reconnectTimer);
      if (staleTimer) clearInterval(staleTimer);
      try {
        ws?.close();
      } catch {
        /* ignore */
      }
    };
  }, [enabled]);

  return { map, isLive, error };
}
