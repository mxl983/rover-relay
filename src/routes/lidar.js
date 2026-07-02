import { Router } from "express";
import fs from "fs/promises";
import config from "../config.js";
import { success, error } from "../utils/apiResponse.js";

const router = Router();

async function readScanFromFile() {
  const raw = await fs.readFile(config.lidar.scanFilePath, "utf8");
  return JSON.parse(raw);
}

async function readScanFromUpstream() {
  const response = await fetch(config.lidar.scanUrl, {
    headers: { "user-agent": "rover-relay/1.0" },
    signal: AbortSignal.timeout(3000),
  });
  if (!response.ok) {
    throw new Error(`LiDAR upstream HTTP ${response.status}`);
  }
  return response.json();
}

router.get("/scan", async (req, res) => {
  try {
    let data;
    try {
      data = await readScanFromFile();
    } catch {
      if (!config.lidar.scanUrl) throw new Error("LiDAR scan file missing");
      data = await readScanFromUpstream();
    }
    res.setHeader("Cache-Control", "no-store");
    return success(res, data);
  } catch (e) {
    return error(
      res,
      config.env === "production" ? "LiDAR scan unavailable" : e.message,
      502,
    );
  }
});

async function readMapFromFile() {
  try {
    const raw = await fs.readFile(config.lidar.slamLiveFilePath, "utf8");
    return JSON.parse(raw);
  } catch {
    const raw = await fs.readFile(config.lidar.slamMapFilePath, "utf8");
    return JSON.parse(raw);
  }
}

router.get("/map", async (req, res) => {
  try {
    const data = await readMapFromFile();
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

export default router;
