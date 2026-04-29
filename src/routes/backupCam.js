import { Router } from "express";
import http from "http";
import https from "https";
import { URL } from "url";
import config from "../config.js";
import { optionalStreamAuth } from "../middleware/auth.js";

const router = Router();
let nextSessionId = 1;
let activeStreamSession = null;

const HOP_BY_HOP = new Set([
  "connection",
  "keep-alive",
  "proxy-authenticate",
  "proxy-authorization",
  "te",
  "trailers",
  "transfer-encoding",
  "upgrade",
]);

function requestUpstream(urlString, onResponse, onError) {
  let u;
  try {
    u = new URL(urlString);
  } catch (e) {
    onError(new Error("Invalid BACKUP_CAM_STREAM_URL"));
    return;
  }
  const lib = u.protocol === "https:" ? https : http;
  const req = lib.request(
    u,
    {
      method: "GET",
      headers: { "user-agent": "rover-relay/1.0" },
    },
    onResponse,
  );
  req.on("error", onError);
  req.end();
  return req;
}

function closeActiveStreamSession(reason = "replaced") {
  if (!activeStreamSession) return;
  const session = activeStreamSession;
  activeStreamSession = null;

  try {
    session.upstreamReq?.destroy(new Error(`stream ${reason}`));
  } catch {
    // Ignore teardown errors while replacing stream session.
  }
  try {
    if (!session.res.writableEnded) session.res.end();
  } catch {
    // Ignore teardown errors while replacing stream session.
  }
}

router.get("/stream", optionalStreamAuth, (req, res) => {
  const urlString = config.backupCam.streamUrl;
  let finished = false;
  const sessionId = nextSessionId++;
  let upstreamReq;
  let upstreamRes = null;
  let tornDown = false;

  // Enforce single-viewer policy for fragile camera upstreams (ESP32).
  closeActiveStreamSession("replaced by newer viewer");
  activeStreamSession = { id: sessionId, res, upstreamReq: null };

  const fail = (message) => {
    if (finished || res.headersSent || res.writableEnded) return;
    finished = true;
    res.status(502).json({ success: false, error: message || "upstream failed" });
  };

  const teardown = () => {
    if (tornDown) return;
    tornDown = true;
    clearTimeout(connectTimer);
    try {
      upstreamRes?.unpipe(res);
    } catch {
      // Ignore teardown errors while ending stream.
    }
    try {
      upstreamRes?.destroy();
    } catch {
      // Ignore teardown errors while ending stream.
    }
    try {
      upstreamReq?.destroy();
    } catch {
      // Ignore teardown errors while ending stream.
    }
    if (activeStreamSession?.id === sessionId) activeStreamSession = null;
  };

  upstreamReq = requestUpstream(
    urlString,
    (upstream) => {
      upstreamRes = upstream;
      const code = upstream.statusCode || 502;
      if (code >= 400) {
        upstream.resume();
        fail(`upstream HTTP ${code}`);
        return;
      }
      for (const [k, v] of Object.entries(upstream.headers)) {
        if (!k || v === undefined) continue;
        const key = k.toLowerCase();
        if (HOP_BY_HOP.has(key)) continue;
        res.setHeader(k, v);
      }
      finished = true;
      res.status(code);
      if (activeStreamSession?.id === sessionId) {
        activeStreamSession.upstreamReq = upstreamReq;
      }
      upstream.pipe(res);
      upstream.on("error", () => {
        if (!res.writableEnded) res.end();
      });
      upstream.on("end", () => {
        if (activeStreamSession?.id === sessionId) activeStreamSession = null;
      });
      upstream.on("close", teardown);
    },
    (err) => {
      fail(err.message || "upstream failed");
    },
  );

  const connectTimer = setTimeout(() => {
    upstreamReq.destroy(new Error("upstream connect timeout"));
  }, 15_000);
  upstreamReq.on("response", () => clearTimeout(connectTimer));
  upstreamReq.on("error", () => clearTimeout(connectTimer));
  req.on("close", () => {
    teardown();
  });
  res.on("close", () => {
    teardown();
  });
  res.on("finish", teardown);
});

router.post("/stop", optionalStreamAuth, (req, res) => {
  closeActiveStreamSession("stopped by dashboard");
  return res.json({ success: true });
});

router.get("/voltage", optionalStreamAuth, async (req, res) => {
  const urlString = config.backupCam.voltageUrl || config.backupCam.streamUrl;
  let upstream;
  try {
    upstream = new URL(urlString);
  } catch {
    return res.status(502).json({ success: false, error: "Invalid BACKUP_CAM_VOLTAGE_URL" });
  }

  try {
    const r = await fetch(upstream, {
      method: "GET",
      headers: { "user-agent": "rover-relay/1.0", accept: "application/json" },
    });
    if (!r.ok) {
      return res.status(502).json({ success: false, error: `upstream HTTP ${r.status}` });
    }
    const data = await r.json();
    return res.json({ success: true, espVoltage: data });
  } catch (e) {
    return res.status(502).json({ success: false, error: e.message || "upstream failed" });
  }
});

export default router;
