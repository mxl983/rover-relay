import express from "express";
import cors from "cors";
import path from "path";
import { Readable } from "stream";
import { fileURLToPath } from "url";
import config from "./config.js";
import telemetryRoutes from "./routes/telemetry.js";
import roverRoutes from "./routes/rover.js";
import backupCamRoutes from "./routes/backupCam.js";
import pulseRoutes from "./routes/pulse.js";
import { success, error } from "./utils/apiResponse.js";

const __dirname = path.dirname(fileURLToPath(import.meta.url));

export function createApp() {
  const corsOptions = {
    origin: config.cors.origins,
    methods: ["GET", "POST", "OPTIONS"],
    allowedHeaders: ["Content-Type", "Authorization"],
    credentials: true,
  };

  const app = express();
  app.use(express.json({ limit: "512kb" }));
  app.use(cors(corsOptions));
  app.options(/(.*)/, cors(corsOptions));
  app.use((req, res, next) => {
    if (!config.logging.requestEnabled) return next();
    const start = Date.now();
    const ip = req.headers["x-forwarded-for"] || req.socket.remoteAddress || "unknown";
    res.on("finish", () => {
      const elapsed = Date.now() - start;
      console.log(`[HTTP] ${ip} ${req.method} ${req.originalUrl} -> ${res.statusCode} ${elapsed}ms`);
    });
    next();
  });

  app.get("/healthz", (req, res) => {
    success(res, { status: "ok", uptime: process.uptime(), service: "rover-relay" });
  });

  app.get("/dashboard", (req, res) => {
    res.sendFile(path.join(__dirname, "..", "public", "dashboard.html"));
  });

  const cdCfg = config.controlDashboard;
  const rawBasePath = cdCfg.basePath || "/mangomate";
  const normalizedBasePath = rawBasePath.startsWith("/") ? rawBasePath : `/${rawBasePath}`;
  const controlTarget = (cdCfg.targetUrl || "http://control-dashboard:80").replace(/\/+$/, "");

  async function proxyControlDashboard(req, res) {
    try {
      const rel = req.originalUrl.startsWith(normalizedBasePath)
        ? req.originalUrl.slice(normalizedBasePath.length)
        : req.originalUrl;
      const upstreamPath = rel && rel.length > 0 ? rel : "/";
      const url = `${controlTarget}${upstreamPath.startsWith("/") ? upstreamPath : `/${upstreamPath}`}`;
      const headers = { ...req.headers };
      delete headers.host;
      delete headers["content-length"];
      const upstream = await fetch(url, {
        method: req.method,
        headers,
        body: req.method === "GET" || req.method === "HEAD" ? undefined : req,
        duplex: req.method === "GET" || req.method === "HEAD" ? undefined : "half",
        redirect: "manual",
      });

      res.status(upstream.status);
      upstream.headers.forEach((value, key) => {
        if (key.toLowerCase() === "transfer-encoding") return;
        res.setHeader(key, value);
      });
      if (!upstream.body) return res.end();
      Readable.fromWeb(upstream.body).pipe(res);
    } catch (e) {
      return error(
        res,
        config.env === "production" ? "Control dashboard unavailable" : e.message,
        502,
      );
    }
  }

  if (cdCfg.proxyEnabled) {
    app.all(normalizedBasePath, proxyControlDashboard);
    app.all(`${normalizedBasePath}/*`, proxyControlDashboard);
  }

  app.use("/api/telemetry", telemetryRoutes);
  app.use("/api/rover/pulse", pulseRoutes);
  app.use("/api/rover", roverRoutes);
  app.use("/api/cams/backup", backupCamRoutes);

  app.use((req, res) => {
    res.status(404).json({ success: false, error: "Not found" });
  });

  app.use((err, req, res, next) => {
    console.error(
      `[ERROR] ${req.method} ${req.originalUrl} from ${req.socket.remoteAddress || "unknown"}`,
      err,
    );
    error(res, config.env === "production" ? "Internal server error" : err.message, 500);
  });

  return app;
}
