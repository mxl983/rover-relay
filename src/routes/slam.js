import { Router } from "express";
import fs from "fs/promises";
import path from "path";
import { randomUUID } from "crypto";
import config from "../config.js";
import { success, error } from "../utils/apiResponse.js";

const router = Router();

async function readMapFromFile() {
  const raw = await fs.readFile(config.slam.mapFilePath, "utf8");
  return JSON.parse(raw);
}

async function readMapFromUpstream() {
  const response = await fetch(config.slam.mapUrl, {
    headers: { "user-agent": "rover-relay/1.0" },
    signal: AbortSignal.timeout(3000),
  });
  if (!response.ok) {
    throw new Error(`SLAM upstream HTTP ${response.status}`);
  }
  return response.json();
}

async function readWaypoints() {
  try {
    const raw = await fs.readFile(config.slam.waypointsPath, "utf8");
    const data = JSON.parse(raw);
    const list = Array.isArray(data?.waypoints)
      ? data.waypoints
      : Array.isArray(data)
        ? data
        : [];
    return list;
  } catch {
    return [];
  }
}

async function writeWaypoints(waypoints) {
  const dir = path.dirname(config.slam.waypointsPath);
  await fs.mkdir(dir, { recursive: true });
  const tmp = `${config.slam.waypointsPath}.tmp`;
  const payload = {
    version: 1,
    updated_at: Date.now() / 1000,
    waypoints,
  };
  await fs.writeFile(tmp, JSON.stringify(payload, null, 2));
  await fs.rename(tmp, config.slam.waypointsPath);
}

router.get("/map", async (_req, res) => {
  try {
    let data;
    try {
      data = await readMapFromFile();
    } catch {
      if (!config.slam.mapUrl) throw new Error("SLAM map file missing");
      data = await readMapFromUpstream();
    }
    res.setHeader("Cache-Control", "no-store");
    return success(res, data);
  } catch (e) {
    return error(
      res,
      config.env === "production" ? "SLAM map unavailable" : e.message,
      502,
    );
  }
});

router.get("/waypoints", async (_req, res) => {
  try {
    const waypoints = await readWaypoints();
    return success(res, { waypoints });
  } catch (e) {
    return error(res, e.message, 500);
  }
});

router.post("/waypoints", async (req, res) => {
  try {
    const currentMap = await readMapFromFile();
    if (currentMap?.mode !== "localization") {
      return error(res, "Freeze the structural map before creating stable marks", 409);
    }
    let x = Number(req.body?.x);
    let y = Number(req.body?.y);
    let yaw = Number(req.body?.yaw);
    const label = String(req.body?.label || "mark").trim() || "mark";

    if (![x, y, yaw].every(Number.isFinite)) {
      const pose = currentMap?.pose || {};
      x = Number(pose.x);
      y = Number(pose.y);
      yaw = Number(pose.yaw);
    }
    if (![x, y, yaw].every(Number.isFinite)) {
      return error(res, "No pose available to mark", 409);
    }

    const waypoints = await readWaypoints();
    const item = {
      id: randomUUID(),
      label,
      x: Math.round(x * 1000) / 1000,
      y: Math.round(y * 1000) / 1000,
      // Docking / approach orientation in map frame (radians).
      yaw: Math.round(yaw * 10000) / 10000,
      theta_deg: Math.round((((yaw * 180) / Math.PI) % 360 + 360) % 360),
      createdAt: new Date().toISOString(),
    };
    waypoints.push(item);
    await writeWaypoints(waypoints);
    return success(res, { waypoint: item, waypoints });
  } catch (e) {
    return error(
      res,
      config.env === "production" ? "Failed to mark waypoint" : e.message,
      500,
    );
  }
});

router.delete("/waypoints/:id", async (req, res) => {
  try {
    const id = String(req.params.id || "");
    const waypoints = await readWaypoints();
    const next = waypoints.filter((w) => String(w?.id) !== id);
    if (next.length === waypoints.length) {
      return error(res, "Waypoint not found", 404);
    }
    await writeWaypoints(next);
    return success(res, { waypoints: next });
  } catch (e) {
    return error(res, e.message, 500);
  }
});

router.post("/map/save", async (_req, res) => {
  try {
    const url = config.slam.controlUrl;
    if (!url) {
      return error(res, "SLAM_CONTROL_URL not configured", 501);
    }
    const response = await fetch(`${url.replace(/\/$/, "")}/map/save`, {
      method: "POST",
      signal: AbortSignal.timeout(5000),
    });
    const data = await response.json().catch(() => ({}));
    if (!response.ok) {
      return error(res, data.error || `Save failed (${response.status})`, 502);
    }
    return success(res, data);
  } catch (e) {
    return error(res, e.message, 502);
  }
});

router.post("/map/freeze", async (_req, res) => {
  try {
    const map = await readMapFromFile();
    if (map?.mode === "localization") {
      return success(res, { status: "frozen", mode: "localization" });
    }
    const marker = config.slam.freezeRequestPath;
    await fs.mkdir(path.dirname(marker), { recursive: true });
    const tmp = `${marker}.tmp`;
    await fs.writeFile(tmp, `${Date.now()}\n`);
    await fs.rename(tmp, marker);
    return success(res, { status: "freezing", mode: "mapping" });
  } catch (e) {
    return error(res, e.message, 500);
  }
});

router.post("/map/reposition", async (_req, res) => {
  try {
    const map = await readMapFromFile();
    if (map?.mode !== "localization") {
      return error(res, "Reposition is only available after the map is frozen", 409);
    }
    const marker = config.slam.repositionRequestPath;
    await fs.mkdir(path.dirname(marker), { recursive: true });
    const tmp = `${marker}.tmp`;
    await fs.writeFile(tmp, `${Date.now()}\n`);
    await fs.rename(tmp, marker);
    return success(res, { status: "repositioning", mode: "localization" });
  } catch (e) {
    return error(res, e.message, 500);
  }
});

router.post("/map/promote", async (_req, res) => {
  try {
    const map = await readMapFromFile();
    if (map?.mode !== "localization") {
      return error(res, "Promote is only available after the map is frozen", 409);
    }
    if (!map?.working_active && !(map?.overlay?.occupied_count > 0)) {
      return error(res, "No local map corrections to promote", 409);
    }
    const marker = config.slam.promoteRequestPath;
    await fs.mkdir(path.dirname(marker), { recursive: true });
    const tmp = `${marker}.tmp`;
    await fs.writeFile(tmp, `${Date.now()}\n`);
    await fs.rename(tmp, marker);
    return success(res, { status: "promoting", mode: "localization" });
  } catch (e) {
    return error(res, e.message, 500);
  }
});

router.post("/map/purge", async (_req, res) => {
  try {
    // Relay and ros2-slam share /app/lidar. A marker avoids Docker bridge →
    // host networking, which may be blocked by the host firewall.
    await writeWaypoints([]);
    const marker = config.slam.purgeRequestPath;
    await fs.mkdir(path.dirname(marker), { recursive: true });
    const tmp = `${marker}.tmp`;
    await fs.writeFile(tmp, `${Date.now()}\n`);
    await fs.rename(tmp, marker);
    return success(res, { status: "purging" });
  } catch (e) {
    return error(res, e.message, 500);
  }
});

export default router;
