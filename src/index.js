import fs from "fs";
import http from "http";
import https from "https";
import config from "./config.js";
import { createApp } from "./app.js";
import { initTelemetry, closeTelemetry } from "./services/telemetryService.js";
import { closeDb } from "./services/db.js";
import { mqttBootService } from "./services/mqttBootService.js";
import {
  startChargingTelemetryLogger,
  stopChargingTelemetryLogger,
} from "./services/chargingTelemetryLogger.js";
import { attachRoverChargingWss } from "./ws/roverChargingWss.js";
import { attachLidarWss } from "./ws/lidarWss.js";
import { attachSlamWss } from "./ws/slamWss.js";
import { closeNavigationDriveBridge } from "./services/navigationDriveBridge.js";

initTelemetry();
startChargingTelemetryLogger();
mqttBootService.start();
const app = createApp();
let server = null;
let redirectServer = null;

if (config.ssl.enabled) {
  if (!fs.existsSync(config.ssl.certPath) || !fs.existsSync(config.ssl.keyPath)) {
    console.error(
      `SSL enabled but cert/key missing. cert=${config.ssl.certPath} key=${config.ssl.keyPath}`,
    );
    process.exit(1);
  }
  const sslOptions = {
    cert: fs.readFileSync(config.ssl.certPath),
    key: fs.readFileSync(config.ssl.keyPath),
  };
  server = https.createServer(sslOptions, app);
} else {
  server = http.createServer(app);
}

attachRoverChargingWss(server);
attachLidarWss(server);
attachSlamWss(server);

function shutdown() {
  stopChargingTelemetryLogger();
  mqttBootService.stop();
  closeNavigationDriveBridge();
  closeTelemetry();
  closeDb();
  if (redirectServer) {
    redirectServer.close();
  }
  server.close(() => process.exit(0));
}

process.on("SIGTERM", shutdown);
process.on("SIGINT", shutdown);

server.listen(config.server.port, config.server.host, () => {
  const proto = config.ssl.enabled ? "https" : "http";
  console.log(
    `rover-relay listening on ${proto}://${config.server.host}:${config.server.port} (${config.env})`,
  );
});

if (config.ssl.enabled && config.ssl.redirectHttpEnabled) {
  redirectServer = http.createServer((req, res) => {
    const host = (req.headers.host || "").replace(/:\d+$/, "");
    const location = `https://${host}:${config.server.port}${req.url || "/"}`;
    res.writeHead(301, { Location: location });
    res.end();
  });
  redirectServer.listen(config.ssl.redirectHttpPort, config.server.host, () => {
    console.log(
      `http redirect listener on http://${config.server.host}:${config.ssl.redirectHttpPort}`,
    );
  });
}
