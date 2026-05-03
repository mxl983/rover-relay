import { WebSocketServer } from "ws";
import { getRoverState } from "../services/roverStateService.js";
import { readEnvironmentFromBackupCam } from "../services/roverEnvironmentService.js";

const PATH = "/ws/rover";

/**
 * Browser clients subscribe here for relay rover snapshots including webcam charging state.
 * Query: ?backup=1 → push every 1s (backup camera UI); otherwise every 5s.
 */
export function attachRoverChargingWss(httpServer) {
  const wss = new WebSocketServer({ noServer: true });

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

    const backup = u.searchParams.get("backup") === "1";
    const intervalMs = backup ? 1000 : 5000;

    let timer = null;
    let stopped = false;

    const tick = async () => {
      if (stopped || ws.readyState !== 1) return;
      try {
        const rover = await getRoverState();
        const { environment, error: environmentError } = await readEnvironmentFromBackupCam();
        ws.send(
          JSON.stringify({
            type: "relay.rover.heartbeat",
            success: true,
            rover: {
              ...rover,
              environment,
              environmentError,
            },
            ts: Date.now(),
          }),
        );
      } catch (e) {
        const msg = e instanceof Error ? e.message : String(e);
        ws.send(
          JSON.stringify({
            type: "relay.rover.heartbeat",
            success: false,
            error: msg,
            ts: Date.now(),
          }),
        );
      }
    };

    void tick();
    timer = setInterval(tick, intervalMs);

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
