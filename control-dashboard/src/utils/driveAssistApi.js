import { apiFetch, apiPostJson } from "../api/client.js";
import { PI_SYSTEM_ENDPOINT } from "../config.js";

const DRIVE_ASSIST_ENDPOINT = `${PI_SYSTEM_ENDPOINT}/drive-assist`;

/** POST /drive-assist — turn assist on or off. Returns the same shape as /info. */
export async function postDriveAssist(enabled) {
  return apiPostJson(DRIVE_ASSIST_ENDPOINT, { enabled });
}

/** GET /drive-assist — lightweight { success, enabled } toggle check. */
export async function fetchDriveAssistStatus() {
  const res = await apiFetch(DRIVE_ASSIST_ENDPOINT, {
    timeout: 2500,
    retries: 0,
  });
  if (!res.ok) {
    throw new Error(`drive-assist ${res.status}`);
  }
  return res.json();
}

/** GET /drive-assist/info — full debug snapshot (lidar, obstacles, braking). */
export async function fetchDriveAssistInfo() {
  const res = await apiFetch(`${DRIVE_ASSIST_ENDPOINT}/info`, {
    timeout: 2500,
    retries: 0,
  });
  if (!res.ok) {
    throw new Error(`drive-assist/info ${res.status}`);
  }
  return res.json();
}

/** @param {unknown} response */
export function readDriveAssistEnabled(response) {
  return typeof response?.enabled === "boolean" ? response.enabled : null;
}

/** Whether the collision HUD should show from a WS DRIVE_ASSIST_UPDATE payload. */
export function isDriveAssistHudActive(update) {
  if (!update || update.active !== true) return false;
  return update.assistUiState === "warning" || update.assistUiState === "maneuvering";
}

/** @param {unknown} update */
export function readDriveAssistClosestRangeM(update) {
  if (!update) return null;
  const rangeM =
    update.obstacle?.closest?.rangeM ??
    update.obstacle?.minRangeM ??
    update.minRangeM;
  return Number.isFinite(rangeM) ? rangeM : null;
}

/** Distance in meters for the collision indicator. */
export function formatDriveAssistClosestDistance(update) {
  const rangeM = readDriveAssistClosestRangeM(update);
  if (rangeM == null) return null;
  return rangeM.toFixed(2);
}

/**
 * @param {unknown} update
 * @returns {string[]}
 */
export function formatDriveAssistDebugLines(update) {
  const lines = [];
  if (update?.enabled === false) {
    lines.push("assist off");
    return lines;
  }
  if (update?.assistUiLabel) {
    lines.push(String(update.assistUiLabel));
  } else if (update?.assistUiState === "warning") {
    lines.push("warning");
  } else if (update?.assistUiState === "maneuvering") {
    lines.push("maneuvering");
  }
  if (update?.assistPhase) {
    lines.push(String(update.assistPhase));
  }
  const obstacle = update?.obstacle;
  if (obstacle?.inRange && obstacle.closest) {
    const { angleDeg, rangeM } = obstacle.closest;
    const rangeLabel = Number.isFinite(rangeM) ? `${rangeM.toFixed(2)}m` : String(rangeM);
    lines.push(`Obstacle at ${angleDeg}° — ${rangeLabel}`);
  }

  for (const entry of update?.prohibitedDirections ?? []) {
    if (!entry?.direction) continue;
    lines.push(`${entry.direction} blocked`);
  }

  if (update?.blocked) {
    lines.push("blocked");
  }
  if (update?.forwardHold) {
    lines.push("forward hold");
  }
  if (update?.wheelsStopped) {
    lines.push("wheels stopped");
  }

  return lines;
}

/** Log the full /info snapshot for debugging server-side decision making. */
export function logDriveAssistInfoDetail(source, info) {
  if (!info || typeof info !== "object") return;
  const summary = formatDriveAssistDebugLines(info);
  const label = `[drive-assist] ${source}`;
  console.groupCollapsed(`${label} · ${summary.join(" · ") || "no active block"}`);
  console.log(JSON.stringify(info, null, 2));
  console.groupEnd();
}
