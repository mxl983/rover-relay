import { Router } from "express";
import {
  getNavigationMode,
  setNavigationMode,
} from "../services/navigationModeService.js";
import { setPiDriveAssistEnabled } from "../services/piDriveAssistService.js";
import { success } from "../utils/apiResponse.js";

const router = Router();

router.get("/", async (_req, res) => {
  const mode = await getNavigationMode();
  return success(res, mode);
});

router.post("/", async (req, res) => {
  const enabled = Boolean(req.body?.enabled);
  const mode = await setNavigationMode(enabled);
  let driveAssist = null;
  if (enabled) {
    // Drive assist on the Pi forces backup maneuvers — conflicts with roam forward-only.
    driveAssist = await setPiDriveAssistEnabled(false);
  }
  return success(res, { ...mode, driveAssist });
});

export default router;
