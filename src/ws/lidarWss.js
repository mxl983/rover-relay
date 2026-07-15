import fs from "fs/promises";
import fsSync from "fs";
import { WebSocketServer } from "ws";
import config from "../config.js";

const PATH = "/ws/lidar";

async function readLatestScan() {
  const raw = await fs.readFile(config.lidar.scanFilePath, "utf8");
  return JSON.parse(raw);
}

/**
 * Browser clients subscribe for LiDAR scans from the shared snapshot file.
 */
export function attachLidarWss(httpServer) {
  const wss = new WebSocketServer({ noServer: true });
  const pushMs = config.lidar.wsPushMs;
  const scanPath = config.lidar.scanFilePath;

  /** @type {Set<import("ws").WebSocket>} */
  const clients = new Set();
  let lastStamp = null;
  let pollTimer = null;
  let watchStarted = false;

  const broadcast = async () => {
    if (clients.size === 0) return;
    try {
      const scan = await readLatestScan();
      const stamp = scan?.stamp;
      if (stamp === lastStamp) return;
      lastStamp = stamp;
      const frame = JSON.stringify({
        type: "relay.lidar.scan",
        success: true,
        ...scan,
        ts: Date.now(),
      });
      for (const ws of clients) {
        if (ws.readyState === 1) ws.send(frame);
      }
    } catch (e) {
      const msg = e instanceof Error ? e.message : String(e);
      const frame = JSON.stringify({
        type: "relay.lidar.scan",
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
      fsSync.watch(scanPath, { persistent: false }, () => {
        void broadcast();
      });
    } catch {
      /* file may not exist until first scan */
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
    // Do not destroy here — other upgrade handlers (/ws/rover, /ws/slam) share this server.
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
    void broadcast();

    const cleanup = () => {
      clients.delete(ws);
    };

    ws.on("close", cleanup);
    ws.on("error", cleanup);
  });

  return wss;
}
