import { Router } from "express";
import crypto from "node:crypto";
import fs from "fs/promises";
import path from "path";
import config from "../config.js";
import { success, error } from "../utils/apiResponse.js";

const router = Router();

/** Human-sortable session id shared with ros2-nav (nav-YYYYMMDD-HHMMSS-xxxxxx). */
function makeNavId() {
  const ts = new Date()
    .toISOString()
    .replace(/\.\d{3}Z$/, "Z")
    .replace(/[-:T]/g, "")
    .slice(0, 14);
  const compact = `${ts.slice(0, 8)}-${ts.slice(8, 14)}`;
  return `nav-${compact}-${crypto.randomBytes(3).toString("hex")}`;
}

async function readJsonFile(filePath, fallback) {
  try {
    const raw = await fs.readFile(filePath, "utf8");
    return JSON.parse(raw);
  } catch {
    return fallback;
  }
}

async function writeJsonAtomic(filePath, payload) {
  const dir = path.dirname(filePath);
  await fs.mkdir(dir, { recursive: true });
  const tmp = `${filePath}.tmp`;
  await fs.writeFile(tmp, JSON.stringify(payload));
  await fs.rename(tmp, filePath);
}

async function readWaypoints() {
  try {
    const raw = await fs.readFile(config.slam.waypointsPath, "utf8");
    const data = JSON.parse(raw);
    return Array.isArray(data?.waypoints)
      ? data.waypoints
      : Array.isArray(data)
        ? data
        : [];
  } catch {
    return [];
  }
}

async function assertGoalOnMap(x, y) {
  const map = await readJsonFile(config.slam.mapFilePath, null);
  if (!map) return;
  if (map.mode !== "localization") {
    const err = new Error("Freeze the structural map before autonomous navigation");
    err.status = 409;
    throw err;
  }
  const width = Number(map.width);
  const height = Number(map.height);
  const res = Number(map.resolution) || 0.05;
  const ox = Number(map.origin?.x);
  const oy = Number(map.origin?.y);
  if (![width, height, ox, oy].every(Number.isFinite) || width <= 0 || height <= 0) {
    return;
  }
  // Display slam.json is the live Cartographer grid (can be tight around the
  // explored area). Only reject goals clearly outside — a small margin would
  // false-reject marks near the map edge.
  const margin = -0.5;
  const minX = ox + margin;
  const maxX = ox + width * res - margin;
  const minY = oy + margin;
  const maxY = oy + height * res - margin;
  if (x < minX || x > maxX || y < minY || y > maxY) {
    const err = new Error(
      `Goal (${x.toFixed(2)}, ${y.toFixed(2)}) is outside the live map ` +
        `[${minX.toFixed(1)}…${maxX.toFixed(1)}, ${minY.toFixed(1)}…${maxY.toFixed(1)}]. ` +
        `Remake the mark on the current map — off-map goals make Nav2 drive blind.`,
    );
    err.status = 400;
    throw err;
  }
}

async function resolveGoal(body, idParam) {
  const waypointId =
    idParam != null && String(idParam) !== ""
      ? String(idParam)
      : body?.id != null
        ? String(body.id)
        : "";
  let x = Number(body?.x);
  let y = Number(body?.y);
  let yaw = Number(body?.yaw);
  let label = String(body?.label || "").trim();

  if (waypointId) {
    const waypoints = await readWaypoints();
    const wp = waypoints.find((w) => String(w?.id) === waypointId);
    if (!wp) {
      const err = new Error("Waypoint not found");
      err.status = 404;
      throw err;
    }
    x = Number(wp.x);
    y = Number(wp.y);
    yaw = Number(wp.yaw) || 0;
    label = label || String(wp.label || waypointId);
  }

  if (![x, y].every(Number.isFinite)) {
    const err = new Error("Need waypoint id or x,y[,yaw]");
    err.status = 400;
    throw err;
  }
  if (!Number.isFinite(yaw)) yaw = 0;
  await assertGoalOnMap(x, y);
  return { x, y, yaw, label, id: waypointId || undefined };
}

/**
 * Write a nav command for ros2-nav (host network) via the shared lidar volume.
 * Docker bridge → host HTTP is often firewalled; this avoids that path.
 */
async function snapshotDriveAssist(bodyAssist) {
  const path = config.navigation.driveAssistSnapshotPath;
  let snapshot = bodyAssist && typeof bodyAssist === "object" ? bodyAssist : null;
  const piUrl = config.navigation.piDriveAssistInfoUrl;
  if (piUrl) {
    try {
      const headers = {};
      if (config.navigation.apiToken) {
        headers.Authorization = `Bearer ${config.navigation.apiToken}`;
      }
      const res = await fetch(piUrl, {
        headers,
        signal: AbortSignal.timeout(2000),
      });
      if (res.ok) {
        snapshot = await res.json();
      }
    } catch {
      // Fall back to dashboard-provided snapshot.
    }
  }
  if (snapshot) {
    await writeJsonAtomic(path, {
      ...snapshot,
      updated_at: new Date().toISOString(),
    });
  }
  return snapshot;
}

async function enqueueCommand(command) {
  const cmdPath = config.navigation.commandPath;
  const seq = Date.now();
  const payload = {
    ...command,
    seq,
    ts: new Date().toISOString(),
  };
  await writeJsonAtomic(cmdPath, payload);

  // Wait briefly for goal_server to ack via status file.
  const statusPath = config.navigation.goalStatusPath;
  const deadline = Date.now() + 2500;
  while (Date.now() < deadline) {
    const status = await readJsonFile(statusPath, null);
    if (status && Number(status.cmd_seq) === seq) {
      if (status.cmd_error) {
        const err = new Error(String(status.cmd_error));
        err.status = 502;
        throw err;
      }
      return { ...payload, status: status.status || "accepted" };
    }
    await new Promise((r) => setTimeout(r, 100));
  }
  // Command is on disk even if ack is slow — Nav2 may still pick it up.
  return { ...payload, status: "queued" };
}

async function sendEmergencyDriveStop() {
  const piUrl = config.navigation.piDriveUrl;
  if (!piUrl) return;
  const headers = { "Content-Type": "application/json" };
  if (config.navigation.apiToken) {
    headers.Authorization = `Bearer ${config.navigation.apiToken}`;
  }
  await fetch(piUrl, {
    method: "POST",
    headers,
    body: JSON.stringify({
      drive: { x: 0, y: 0 },
      gimbal: { x: 0, y: 0 },
    }),
    signal: AbortSignal.timeout(1500),
  });
}

async function latchNavigationKill(reason) {
  await writeJsonAtomic(config.navigation.killPath, {
    latched: true,
    reason,
    updatedAt: new Date().toISOString(),
  });
}

const NAV_DRIVE_KEYS = new Set(["w", "a", "s", "d"]);

function normalizeDriveKeys(value) {
  if (!Array.isArray(value)) return null;
  const keys = [...new Set(value.map((key) => String(key).toLowerCase()))];
  return keys.every((key) => NAV_DRIVE_KEYS.has(key)) ? keys : null;
}

/** Existing analog endpoint retained unchanged for compatibility. */
router.post("/drive", async (req, res) => {
  try {
    const drive = req.body?.drive;
    if (
      !drive ||
      typeof drive !== "object" ||
      !Number.isFinite(Number(drive.x)) ||
      !Number.isFinite(Number(drive.y))
    ) {
      return error(res, "body.drive.{x,y} required", 400);
    }
    const piUrl = config.navigation.piDriveUrl;
    if (!piUrl) {
      return error(res, "NAV_PI_DRIVE_URL not configured", 501);
    }
    const payload = {
      drive: {
        x: Math.max(-1, Math.min(1, Number(drive.x))),
        y: Math.max(-1, Math.min(1, Number(drive.y))),
      },
      gimbal: { x: 0, y: 0 },
    };
    const headers = { "Content-Type": "application/json" };
    if (config.navigation.apiToken) {
      headers.Authorization = `Bearer ${config.navigation.apiToken}`;
    }
    const upstream = await fetch(piUrl, {
      method: "POST",
      headers,
      body: JSON.stringify(payload),
      signal: AbortSignal.timeout(2000),
    });
    const data = await upstream.json().catch(() => ({}));
    if (!upstream.ok) {
      return error(
        res,
        data.error || data.message || `Pi drive HTTP ${upstream.status}`,
        502,
      );
    }
    return success(res, { drive: payload.drive, upstream: data });
  } catch (e) {
    return error(res, e.message, 502);
  }
});

/** Autonomous-only endpoint: forward explicit WASD intent as a raw Pi key array. */
router.post("/drive/keys", async (req, res) => {
  try {
    const keys = normalizeDriveKeys(req.body?.keys);
    if (keys === null) {
      return error(res, "body.keys must be a WASD array", 400);
    }
    if (
      (keys.includes("w") && keys.includes("s")) ||
      (keys.includes("a") && keys.includes("d"))
    ) {
      return error(res, "conflicting WASD directions", 400);
    }
    const piUrl = config.navigation.piDriveUrl;
    if (!piUrl) {
      return error(res, "NAV_PI_DRIVE_URL not configured", 501);
    }
    const headers = { "Content-Type": "application/json" };
    if (config.navigation.apiToken) {
      headers.Authorization = `Bearer ${config.navigation.apiToken}`;
    }
    const upstream = await fetch(piUrl, {
      method: "POST",
      headers,
      body: JSON.stringify(keys),
      signal: AbortSignal.timeout(2000),
    });
    const data = await upstream.json().catch(() => ({}));
    if (!upstream.ok) {
      return error(
        res,
        data.error || data.message || `Pi drive HTTP ${upstream.status}`,
        502,
      );
    }
    return success(res, { keys, upstream: data });
  } catch (e) {
    return error(res, e.message, 502);
  }
});

router.get("/status", async (_req, res) => {
  const driveStatus = await readJsonFile(config.navigation.statusPath, null);
  const goalStatus = await readJsonFile(config.navigation.goalStatusPath, null);
  const pathStatus = await readJsonFile(config.navigation.pathFilePath, null);
  return success(res, {
    drive: driveStatus,
    goal: goalStatus,
    path: pathStatus,
  });
});

async function postGoto(req, res, idParam) {
  try {
    const goal = await resolveGoal(req.body || {}, idParam);
    const fineDocking =
      Boolean(req.body?.fine_docking) ||
      req.query?.fine_docking === "1" ||
      req.query?.fine_docking === "true";
    // A deliberate new mission is the only operation that may re-arm
    // autonomous motor output after Pause/Kill.
    await fs.unlink(config.navigation.killPath).catch((e) => {
      if (e?.code !== "ENOENT") throw e;
    });
    const navId = makeNavId();
    const driveAssist = await snapshotDriveAssist(req.body?.drive_assist);
    const accepted = await enqueueCommand({
      op: "goto",
      nav_id: navId,
      drive_assist: driveAssist,
      x: goal.x,
      y: goal.y,
      yaw: goal.yaw,
      label: goal.label || "",
      id: goal.id || "",
      fine_docking: fineDocking,
    });
    console.log(
      `[navigation] goto nav_id=${navId} ${goal.label || goal.id || ""} fine_docking=${fineDocking}`,
    );
    return success(res, {
      nav_id: navId,
      goal: {
        x: goal.x,
        y: goal.y,
        yaw: goal.yaw,
        label: goal.label || "",
        id: goal.id || "",
        fine_docking: fineDocking,
      },
      status: accepted.status || "navigating",
      seq: accepted.seq,
    });
  } catch (e) {
    return error(res, e.message, e.status || 502);
  }
}

router.post("/goto", (req, res) => postGoto(req, res, undefined));
router.post("/goto/:id", (req, res) => postGoto(req, res, req.params.id));

router.post("/cancel", async (_req, res) => {
  try {
    await latchNavigationKill("cancel");
    const motorStop = sendEmergencyDriveStop().catch(() => null);
    const accepted = await enqueueCommand({ op: "cancel" });
    await motorStop;
    return success(res, { status: "idle", seq: accepted.seq });
  } catch (e) {
    return error(res, e.message, 502);
  }
});

router.post("/pause", async (_req, res) => {
  try {
    await latchNavigationKill("pause");
    const motorStop = sendEmergencyDriveStop().catch(() => null);
    const accepted = await enqueueCommand({ op: "pause" });
    await motorStop;
    return success(res, { status: "paused", seq: accepted.seq });
  } catch (e) {
    return error(res, e.message, 502);
  }
});

router.post("/kill", async (_req, res) => {
  try {
    await latchNavigationKill("emergency_kill");
    const motorStop = sendEmergencyDriveStop().catch(() => null);
    const accepted = await enqueueCommand({ op: "cancel" });
    await motorStop;
    return success(res, {
      status: "killed",
      latched: true,
      seq: accepted.seq,
    });
  } catch (e) {
    return error(res, e.message, 502);
  }
});

export default router;
