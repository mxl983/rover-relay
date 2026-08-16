import { Router } from "express";
import fs from "fs/promises";
import config from "../config.js";
import { forwardNavigationDrive } from "../services/navigationDriveBridge.js";
import {
  getNavigationMode,
  setNavigationMode,
} from "../services/navigationModeService.js";
import { setPiDriveAssistEnabled } from "../services/piDriveAssistService.js";
import { success, error } from "../utils/apiResponse.js";

/** Drive + status under `/api/navigation`. */
const navigationRouter = Router();

navigationRouter.get("/status", async (_req, res) => {
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

navigationRouter.post("/drive", async (req, res) => {
  const result = await forwardNavigationDrive(req.body);
  if (!result.accepted) {
    const status = result.reason === "navigation_disabled" ? 409 : 502;
    return error(res, result.reason, status);
  }
  return success(res, { drive: result.drive });
});

/** Kill switch under `/api/system/navigation`. */
export const systemNavigationRouter = Router();

systemNavigationRouter.get("/", async (_req, res) => {
  const mode = await getNavigationMode();
  return success(res, mode);
});

systemNavigationRouter.post("/", async (req, res) => {
  const enabled = Boolean(req.body?.enabled);
  const mode = await setNavigationMode(enabled);
  let driveAssist = null;
  if (enabled) {
    // Drive assist on the Pi forces backup maneuvers — conflicts with roam forward-only.
    driveAssist = await setPiDriveAssistEnabled(false);
  }
  return success(res, { ...mode, driveAssist });
});

export default navigationRouter;
