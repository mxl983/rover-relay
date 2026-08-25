import { useEffect, useRef, useState } from "react";
import { getSlamWebSocketUrl } from "../config";

const STALE_MS = 4000;

/**
 * @typedef {object} SlamPose
 * @property {number} x
 * @property {number} y
 * @property {number} yaw
 * @property {number} [stamp]
 */

/**
 * @typedef {object} SlamMap
 * @property {number} stamp
 * @property {string} frame_id
 * @property {number} resolution
 * @property {number} width
 * @property {number} height
 * @property {{ x: number, y: number, yaw: number }} origin
 * @property {SlamPose} pose
 * @property {number[]} occupied
 * @property {number} occupied_count
 * @property {number} hz
 */

/**
 * Subscribe to Cartographer occupancy + pose over relay WebSocket.
 * Full `relay.slam.map` frames carry occupancy; thin `relay.slam.pose`
 * frames only update pose / scan / waypoints on top of the last map.
 * @param {boolean} enabled
 */
export function useSlamMap(enabled) {
  const [map, setMap] = useState(null);
  const [isLive, setIsLive] = useState(false);
  const [error, setError] = useState(null);
  const lastStampRef = useRef(0);
  const lastMessageAtRef = useRef(0);
  const mapRef = useRef(null);

  useEffect(() => {
    if (!enabled) {
      setMap(null);
      mapRef.current = null;
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

    const applyLive = (payload) => {
      setError(null);
      lastMessageAtRef.current = Date.now();
      const fresh =
        Number.isFinite(payload?.stamp) &&
        payload.stamp !== lastStampRef.current;
      if (fresh) lastStampRef.current = payload.stamp;
      setIsLive(fresh || Date.now() - lastMessageAtRef.current <= STALE_MS);
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
          if (msg.type === "relay.slam.pose") {
            if (!msg.success) return;
            const {
              type: _type,
              success: _success,
              error: _error,
              ts: _ts,
              ...posePatch
            } = msg;
            const prev = mapRef.current;
            if (!prev) return;
            const next = { ...prev, ...posePatch };
            mapRef.current = next;
            setMap(next);
            applyLive(next);
            return;
          }
          if (msg.type !== "relay.slam.map") return;
          if (!msg.success) {
            setError(msg.error || "SLAM unavailable");
            setIsLive(false);
            return;
          }
          const { type: _type, success: _success, error: _error, ts: _ts, ...payload } = msg;
          mapRef.current = payload;
          setMap(payload);
          applyLive(payload);
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

  return { map, isLive, error };
}
