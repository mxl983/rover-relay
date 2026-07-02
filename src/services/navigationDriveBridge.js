import WebSocket from "ws";
import config from "../config.js";
import { getNavigationMode } from "./navigationModeService.js";

let socket = null;
let connectPromise = null;

function clampAxis(value) {
  const n = Number(value);
  if (!Number.isFinite(n)) return 0;
  return Math.max(-1, Math.min(1, n));
}

/** Pi: forward = -y. Block backward (y > 0) and apply skid-steer wheel guard. */
function toPiDrive(raw) {
  let x = clampAxis(raw.x);
  let y = clampAxis(raw.y);
  if (y > 0) y = 0;
  if (y < 0) {
    const forward = -y;
    x = Math.max(-forward, Math.min(forward, x));
  }
  return { x, y };
}

function connectPiWebSocket() {
  if (socket?.readyState === WebSocket.OPEN) return Promise.resolve(socket);
  if (connectPromise) return connectPromise;

  const url = config.navigation.piWebSocketUrl;
  connectPromise = new Promise((resolve, reject) => {
    const ws = new WebSocket(url, {
      rejectUnauthorized: !config.navigation.piWsTlsInsecure,
    });
    const timer = setTimeout(() => {
      ws.terminate();
      connectPromise = null;
      reject(new Error("pi websocket connect timeout"));
    }, 5000);

    ws.once("open", () => {
      clearTimeout(timer);
      socket = ws;
      connectPromise = null;
      ws.on("close", () => {
        if (socket === ws) socket = null;
      });
      ws.on("error", () => {});
      resolve(ws);
    });

    ws.once("error", (err) => {
      clearTimeout(timer);
      connectPromise = null;
      reject(err);
    });
  });

  return connectPromise;
}

/** Forward a planned drive command to the Pi control WebSocket. */
export async function forwardNavigationDrive(payload) {
  const mode = await getNavigationMode();
  if (!mode.enabled) {
    return { accepted: false, reason: "navigation_disabled" };
  }

  const raw = payload?.drive ?? {};
  const drive = toPiDrive(raw);

  try {
    const ws = await connectPiWebSocket();
    ws.send(
      JSON.stringify({
        type: "DRIVE",
        navigation: true,
        drive,
        gimbal: { x: 0, y: 0 },
      }),
    );
    return { accepted: true, drive };
  } catch (err) {
    return {
      accepted: false,
      reason: err?.message ?? "pi_websocket_offline",
    };
  }
}

export function closeNavigationDriveBridge() {
  connectPromise = null;
  if (socket) {
    try {
      socket.close();
    } catch {
      /* ignore */
    }
    socket = null;
  }
}

/** @internal */
export function _resetNavigationDriveBridgeForTests() {
  closeNavigationDriveBridge();
}
