import { useEffect, useRef, useState } from "react";
import { getLidarWebSocketUrl } from "../config";

const STALE_MS = 2500;

/**
 * @typedef {object} LidarPoint
 * @property {number} x forward (m)
 * @property {number} y left (m)
 * @property {number} [r]
 * @property {number} [a]
 */

/**
 * @typedef {object} LidarScan
 * @property {number} stamp
 * @property {string} frame_id
 * @property {LidarPoint[]} points
 * @property {number} count
 * @property {number} valid
 * @property {number|null} nearest
 * @property {number|null} farthest
 * @property {number} hz
 */

/**
 * Subscribe to relay LiDAR scans over WebSocket when enabled.
 * @param {boolean} enabled
 */
export function useLidarScan(enabled) {
  const [scan, setScan] = useState(null);
  const [isLive, setIsLive] = useState(false);
  const [error, setError] = useState(null);
  const lastStampRef = useRef(0);
  const lastMessageAtRef = useRef(0);

  useEffect(() => {
    if (!enabled) {
      setScan(null);
      setIsLive(false);
      setError(null);
      lastStampRef.current = 0;
      lastMessageAtRef.current = 0;
      return undefined;
    }

    let cancelled = false;
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
        ws = new WebSocket(getLidarWebSocketUrl());
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
          if (msg.type !== "relay.lidar.scan") return;
          if (!msg.success) {
            setError(msg.error || "LiDAR unavailable");
            setIsLive(false);
            return;
          }
          const { type: _type, success: _success, error: _error, ts: _ts, ...payload } = msg;
          setScan(payload);
          setError(null);
          lastMessageAtRef.current = Date.now();
          const fresh =
            Number.isFinite(payload?.stamp) &&
            payload.stamp !== lastStampRef.current;
          if (fresh) lastStampRef.current = payload.stamp;
          setIsLive(fresh || Date.now() - lastMessageAtRef.current <= STALE_MS);
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

  return { scan, isLive, error };
}
