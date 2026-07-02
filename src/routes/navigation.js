import { Router } from "express";
import fs from "fs/promises";
import config from "../config.js";
import { forwardNavigationDrive } from "../services/navigationDriveBridge.js";
import { success, error } from "../utils/apiResponse.js";

const router = Router();

router.get("/status", async (_req, res) => {
  try {
    const raw = await fs.readFile(config.navigation.statusFilePath, "utf8");
    const data = JSON.parse(raw);
    res.setHeader("Cache-Control", "no-store");
    return success(res, data);
  } catch (e) {
    return error(
      res,
      config.env === "production" ? "Navigation status unavailable" : e.message,
      502,
    );
  }
});

router.post("/drive", async (req, res) => {
  const result = await forwardNavigationDrive(req.body);
  if (!result.accepted) {
    const status = result.reason === "navigation_disabled" ? 409 : 502;
    return error(res, result.reason, status);
  }
  return success(res, { drive: result.drive });
});

export default router;
