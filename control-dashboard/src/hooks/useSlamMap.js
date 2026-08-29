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

function gridKey(payload) {
  if (!payload) return "";
  return [
    payload.grid_revision ?? "",
    payload.update_count ?? "",
    payload.occupied_count ?? "",
    payload.width ?? "",
    payload.height ?? "",
  ].join(":");
}

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
  const lastMessageAtRef = useRef(0);
  const mapRef = useRef(null);
  const gridKeyRef = useRef("");

  useEffect(() => {
    if (!enabled) {
      setMap(null);
      mapRef.current = null;
      gridKeyRef.current = "";
      setIsLive(false);
      setError(null);
      lastMessageAtRef.current = 0;
      return undefined;
    }

    let cancelled = false;
    let ws = null;
    let reconnectTimer = null;
    let staleTimer = null;
    let rafPending = false;
    let pendingMap = null;

    const flushMap = () => {
      rafPending = false;
      if (cancelled || pendingMap == null) return;
      mapRef.current = pendingMap;
      setMap(pendingMap);
      pendingMap = null;
      applyLive();
    };

    const scheduleMap = (next) => {
      pendingMap = next;
      if (rafPending) return;
      rafPending = true;
      requestAnimationFrame(flushMap);
    };

    const markStale = () => {
      if (cancelled) return;
      const age = Date.now() - lastMessageAtRef.current;
      if (!lastMessageAtRef.current || age > STALE_MS) {
        setIsLive(false);
      }
    };

    const applyLive = () => {
      lastMessageAtRef.current = Date.now();
      setIsLive(true);
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
            scheduleMap({ ...prev, ...posePatch });
            return;
          }
          if (msg.type !== "relay.slam.map") return;
          if (!msg.success) {
            setError(msg.error || "SLAM unavailable");
            setIsLive(false);
            return;
          }
          const { type: _type, success: _success, error: _error, ts: _ts, ...payload } = msg;
          const prev = mapRef.current;
          // Keep newer live pose if a stale full-grid frame arrives behind pose ticks.
          let next = payload;
          if (
            prev?.pose &&
            payload?.pose &&
            Number(prev.updated_at) > Number(payload.updated_at)
          ) {
            next = {
              ...payload,
              pose: prev.pose,
              view: prev.view ?? payload.view,
              scan_hits: prev.scan_hits ?? payload.scan_hits,
              scan_hit_count: prev.scan_hit_count ?? payload.scan_hit_count,
              scan_match_score: prev.scan_match_score ?? payload.scan_match_score,
              updated_at: prev.updated_at,
              stamp: prev.stamp ?? payload.stamp,
              pose_in_map: prev.pose_in_map ?? payload.pose_in_map,
            };
          }
          gridKeyRef.current = gridKey(next);
          scheduleMap(next);
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
