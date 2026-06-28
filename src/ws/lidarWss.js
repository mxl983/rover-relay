import fs from "fs/promises";
import { WebSocketServer } from "ws";
import config from "../config.js";

const PATH = "/ws/lidar";

async function readLatestScan() {
  const raw = await fs.readFile(config.lidar.scanFilePath, "utf8");
  return JSON.parse(raw);
}

/**
 * Browser clients subscribe for decimated LiDAR scans from the shared snapshot file.
 */
export function attachLidarWss(httpServer) {
  const wss = new WebSocketServer({ noServer: true });
  const pushMs = config.lidar.wsPushMs;

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
      socket.destroy();
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

    let timer = null;
    let stopped = false;
    let lastStamp = null;

    const tick = async () => {
      if (stopped || ws.readyState !== 1) return;
      try {
        const scan = await readLatestScan();
        const stamp = scan?.stamp;
        if (stamp === lastStamp) return;
        lastStamp = stamp;
        ws.send(
          JSON.stringify({
            type: "relay.lidar.scan",
            success: true,
            ...scan,
            ts: Date.now(),
          }),
        );
      } catch (e) {
        const msg = e instanceof Error ? e.message : String(e);
        ws.send(
          JSON.stringify({
            type: "relay.lidar.scan",
            success: false,
            error: msg,
            ts: Date.now(),
          }),
        );
      }
    };

    void tick();
    timer = setInterval(tick, pushMs);

    const cleanup = () => {
      stopped = true;
      if (timer) clearInterval(timer);
      timer = null;
    };

    ws.on("close", cleanup);
    ws.on("error", cleanup);
  });

  return wss;
}
