import fs from "fs/promises";
import fsSync from "fs";
import { WebSocketServer } from "ws";
import config from "../config.js";

const PATH = "/ws/slam";
/** Drop frames for a client that is already > this far behind (bytes). */
const MAX_BUFFERED = 1_048_576;

async function readLatestMap() {
  const livePath = config.slam?.mapFilePath || config.lidar?.slamLiveFilePath;
  const fallbackPath = config.lidar?.slamMapFilePath;
  const posePath = livePath
    ? livePath.replace(/slam\.json$/i, "slam_live.json")
    : "/app/lidar/slam_live.json";
  let map = null;
  try {
    if (livePath) {
      const raw = await fs.readFile(livePath, "utf8");
      map = JSON.parse(raw);
    }
  } catch {
    /* try fallback */
  }
  if (!map && fallbackPath && fallbackPath !== livePath) {
    const raw = await fs.readFile(fallbackPath, "utf8");
    map = JSON.parse(raw);
  }
  if (!map) throw new Error("SLAM map file missing");
  try {
    const poseRaw = await fs.readFile(posePath, "utf8");
    const live = JSON.parse(poseRaw);
    if (live && typeof live === "object") {
      // Legacy DIY slam_mapper writes version:2 payloads without pose_in_map /
      // scan_hits — never merge those (they yank the rover to a stale pose).
      const isLegacy =
        Number(live.version) === 2 ||
        (live.scan_count != null && live.scan_hits == null && live.occupied == null);
      if (isLegacy) return map;
      const liveTs = Number(live.updated_at) || 0;
      const mapTs = Number(map.updated_at) || 0;
      if (liveTs >= mapTs - 0.05) {
        map = { ...map, ...live };
      }
    }
  } catch {
    /* pose sidecar optional */
  }
  return map;
}

function occupancyFingerprint(map) {
  const overlay = map?.overlay;
  let overlayFp = "";
  if (overlay && typeof overlay === "object") {
    overlayFp = [
      overlay.occupied_count ?? "",
      overlay.added?.occupied_count ?? "",
      overlay.removed?.occupied_count ?? "",
    ].join(",");
  }
  if (map?.grid_revision != null && map.grid_revision !== "") {
    return `${map.grid_revision}:${overlayFp}`;
  }
  // Prefer stable identity fields — never update_count alone (bumps every /map).
  return [
    map?.source ?? "",
    map?.mode ?? "",
    map?.width ?? "",
    map?.height ?? "",
    map?.origin?.x ?? "",
    map?.origin?.y ?? "",
    map?.baseline_frozen_at ?? "",
    map?.occupied_count ?? "",
    overlayFp,
  ].join(":");
}

function scanHitsFingerprint(map) {
  const hits = map?.scan_hits;
  if (!Array.isArray(hits) || !hits.length) return "0";
  let h = hits.length;
  for (let i = 0; i < hits.length; i += 7) {
    const pt = hits[i];
    h = (Math.imul(h, 31) + Math.round((Number(pt?.x) || 0) * 100)) | 0;
    h = (Math.imul(h, 31) + Math.round((Number(pt?.y) || 0) * 100)) | 0;
  }
  return String(h);
}

function poseKey(map) {
  return [
    map?.updated_at ?? "",
    map?.pose?.x ?? "",
    map?.pose?.y ?? "",
    map?.pose?.yaw ?? "",
    map?.scan_hit_count ?? "",
    scanHitsFingerprint(map),
  ].join(":");
}

/** Dashboard draws `occupied`; `map_points` is a redundant ~80KB world-space copy. */
function stripHeavyFields(map) {
  if (!map || typeof map !== "object") return map;
  const { map_points: _pts, ...rest } = map;
  return rest;
}

function thinPosePayload(map) {
  return {
    stamp: map?.stamp,
    updated_at: map?.updated_at,
    pose: map?.pose,
    view: map?.view,
    hz: map?.hz,
    mode: map?.mode,
    scan_hits: map?.scan_hits,
    scan_hit_count: map?.scan_hit_count,
    scan_match_score: map?.scan_match_score,
    waypoints: map?.waypoints,
    pose_in_map: map?.pose_in_map,
  };
}

/**
 * Browser clients subscribe for SLAM map updates from the shared snapshot file.
 * Full occupancy is sent only when the grid changes; pose/scan go as thin frames.
 */
export function attachSlamWss(httpServer) {
  const wss = new WebSocketServer({ noServer: true });
  const pushMs = config.slam?.wsPushMs ?? config.lidar?.slamWsPushMs ?? 50;
  const mapPath =
    config.slam?.mapFilePath ||
    config.lidar?.slamLiveFilePath ||
    "/app/lidar/slam.json";
  const fallbackMapPath = config.lidar?.slamMapFilePath || mapPath;

  /** @type {Set<import("ws").WebSocket>} */
  const clients = new Set();
  let lastPoseKey = null;
  let lastOccKey = null;
  let pollTimer = null;
  let watchStarted = false;
  let pumpRunning = false;
  let pumpAgain = false;
  let pumpForce = false;

  const sendAll = (frame) => {
    for (const ws of clients) {
      if (ws.readyState !== 1) continue;
      if (ws.bufferedAmount > MAX_BUFFERED) continue;
      ws.send(frame);
    }
  };

  const broadcast = async (force = false) => {
    if (clients.size === 0) return;
    try {
      const map = await readLatestMap();
      const pKey = poseKey(map);
      const oKey = occupancyFingerprint(map);
      if (!force && pKey === lastPoseKey && oKey === lastOccKey) return;

      const occChanged = force || oKey !== lastOccKey;
      lastPoseKey = pKey;
      lastOccKey = oKey;

      const ts = Date.now();
      if (occChanged) {
        const frame = JSON.stringify({
          type: "relay.slam.map",
          success: true,
          ...stripHeavyFields(map),
          ts,
        });
        sendAll(frame);
      } else {
        const frame = JSON.stringify({
          type: "relay.slam.pose",
          success: true,
          ...thinPosePayload(map),
          ts,
        });
        sendAll(frame);
      }
    } catch (e) {
      const msg = e instanceof Error ? e.message : String(e);
      const frame = JSON.stringify({
        type: "relay.slam.map",
        success: false,
        error: msg,
        ts: Date.now(),
      });
      sendAll(frame);
    }
  };

  const queueBroadcast = (force = false) => {
    if (force) pumpForce = true;
    if (pumpRunning) {
      pumpAgain = true;
      return;
    }
    pumpRunning = true;
    void (async () => {
      try {
        do {
          const forced = pumpForce;
          pumpAgain = false;
          pumpForce = false;
          await broadcast(forced);
        } while (pumpAgain);
      } finally {
        pumpRunning = false;
      }
    })();
  };

  const ensurePump = () => {
    if (watchStarted) return;
    watchStarted = true;

    for (const path of new Set([mapPath, fallbackMapPath])) {
      try {
        fsSync.watch(path, { persistent: false }, () => {
          void queueBroadcast();
        });
      } catch {
        /* file may not exist yet */
      }
    }

    if (!pollTimer) {
      pollTimer = setInterval(() => void queueBroadcast(), pushMs);
      if (typeof pollTimer.unref === "function") pollTimer.unref();
    }

    for (const path of new Set([
      mapPath.replace(/slam\.json$/i, "slam_live.json"),
      fallbackMapPath.replace(/slam\.json$/i, "slam_live.json"),
    ])) {
      try {
        fsSync.watch(path, { persistent: false }, () => {
          void queueBroadcast();
        });
      } catch {
        /* file may not exist yet */
      }
    }
  };

  httpServer.on("upgrade", (request, socket, head) => {
    let pathname;
    try {
      const host = request.headers.host || "127.0.0.1";
      pathname = new URL(request.url, `http://${host}`).pathname;
    } catch {
      socket.destroy();
      return;
    }
    if (pathname !== PATH) {
      return;
    }

    wss.handleUpgrade(request, socket, head, (ws) => {
      wss.emit("connection", ws, request);
    });
  });

  wss.on("connection", (ws, req) => {
    const host = req.headers.host || "127.0.0.1";
    let u;
    try {
      u = new URL(req.url, `http://${host}`);
    } catch {
      ws.close();
      return;
    }
    if (u.pathname !== PATH) {
      ws.close();
      return;
    }

    clients.add(ws);
    ensurePump();
    void queueBroadcast(true);

    const cleanup = () => {
      clients.delete(ws);
    };

    ws.on("close", cleanup);
    ws.on("error", cleanup);
  });

  return wss;
}
