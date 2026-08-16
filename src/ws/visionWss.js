import { WebSocketServer, WebSocket } from "ws";
import config from "../config.js";

const PATH = "/api/vision/ws";

function upstreamWsUrl() {
  const httpBase = (config.vision?.upstreamUrl || "http://host.docker.internal:8010").replace(
    /\/$/,
    "",
  );
  return `${httpBase.replace(/^http/i, "ws")}/ws/vision`;
}

/**
 * Browser (HTTPS github.io) ↔ wss://relay/api/vision/ws ↔ ws://vision_server/ws/vision
 */
export function attachVisionWss(httpServer) {
  const wss = new WebSocketServer({ noServer: true });

  httpServer.on("upgrade", (request, socket, head) => {
    let pathname;
    try {
      const host = request.headers.host || "127.0.0.1";
      pathname = new URL(request.url, `http://${host}`).pathname;
    } catch {
      return;
    }
    if (pathname !== PATH) return;

    wss.handleUpgrade(request, socket, head, (client) => {
      wss.emit("connection", client, request);
    });
  });

  wss.on("connection", (client) => {
    const upstreamUrl = upstreamWsUrl();
    let upstream;
    try {
      upstream = new WebSocket(upstreamUrl);
    } catch (e) {
      client.close(1011, "vision upstream connect failed");
      return;
    }

    const closeBoth = (code, reason) => {
      try {
        if (client.readyState === WebSocket.OPEN) client.close(code, reason);
      } catch {
        /* ignore */
      }
      try {
        if (
          upstream &&
          (upstream.readyState === WebSocket.OPEN ||
            upstream.readyState === WebSocket.CONNECTING)
        ) {
          upstream.close();
        }
      } catch {
        /* ignore */
      }
    };

    upstream.on("open", () => {
      /* ready */
    });
    upstream.on("message", (data, isBinary) => {
      if (client.readyState === WebSocket.OPEN) {
        client.send(data, { binary: !!isBinary });
      }
    });
    upstream.on("close", () => closeBoth(1000, "upstream closed"));
    upstream.on("error", () => closeBoth(1011, "upstream error"));

    client.on("message", (data, isBinary) => {
      if (upstream.readyState === WebSocket.OPEN) {
        upstream.send(data, { binary: !!isBinary });
      }
    });
    client.on("close", () => closeBoth(1000, "client closed"));
    client.on("error", () => closeBoth(1011, "client error"));
  });
}
