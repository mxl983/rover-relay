import fs from "fs/promises";
import fsSync from "fs";
import { WebSocketServer } from "ws";
import config from "../config.js";

const PATH = "/ws/slam";

async function readLatestMap() {
  try {
    const raw = await fs.readFile(config.lidar.slamLiveFilePath, "utf8");
    return JSON.parse(raw);
  } catch {
    const raw = await fs.readFile(config.lidar.slamMapFilePath, "utf8");
    return JSON.parse(raw);
  }
}

/**
 * Browser clients subscribe for SLAM map updates from the shared snapshot file.
 */
export function attachSlamWss(httpServer) {
  const wss = new WebSocketServer({ noServer: true });
  const pushMs = config.lidar.slamWsPushMs;
  const mapPath = config.lidar.slamLiveFilePath;
  const fallbackMapPath = config.lidar.slamMapFilePath;

  /** @type {Set<import("ws").WebSocket>} */
  const clients = new Set();
  let lastKey = null;
  let pollTimer = null;
  let watchStarted = false;

  const broadcast = async (force = false) => {
    if (clients.size === 0) return;
    try {
      const map = await readLatestMap();
      const key = `${map?.stamp ?? ""}:${map?.updated_at ?? ""}:${map?.scan_count ?? ""}`;
      if (!force && key === lastKey) return;
      lastKey = key;
      const frame = JSON.stringify({
        type: "relay.slam.map",
        success: true,
        ...map,
        ts: Date.now(),
      });
      for (const ws of clients) {
        if (ws.readyState === 1) ws.send(frame);
      }
    } catch (e) {
      const msg = e instanceof Error ? e.message : String(e);
      const frame = JSON.stringify({
        type: "relay.slam.map",
        success: false,
        error: msg,
        ts: Date.now(),
      });
      for (const ws of clients) {
        if (ws.readyState === 1) ws.send(frame);
      }
    }
  };

  const ensurePump = () => {
    if (watchStarted) return;
    watchStarted = true;

    try {
      fsSync.watch(mapPath, { persistent: false }, () => {
        void broadcast();
      });
    } catch {
      /* live file may not exist until first scan */
    }
    try {
      fsSync.watch(fallbackMapPath, { persistent: false }, () => {
        void broadcast();
      });
    } catch {
      /* optional full map snapshot */
    }

    if (!pollTimer) {
      pollTimer = setInterval(() => void broadcast(), pushMs);
      if (typeof pollTimer.unref === "function") pollTimer.unref();
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
    void broadcast(true);

    const cleanup = () => {
      clients.delete(ws);
    };

    ws.on("close", cleanup);
    ws.on("error", cleanup);
  });

  return wss;
}
