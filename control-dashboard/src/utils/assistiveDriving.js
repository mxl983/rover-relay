import {
  isAngleInAssistBackupRange,
  isAngleInAssistForwardRange,
  LIDAR_ASSIST_STOP_M,
} from "./lidarCoords.js";

/** drive.y = fwd(+)/rev(-), drive.x = turn. */
const FORWARD_KEYS = new Set(["w", "ArrowUp"]);
const BACK_KEYS = new Set(["s", "ArrowDown"]);
const DRIVE_FWD_EPS = 0.01;

/**
 * @typedef {'backup_only' | 'forward_only'} AssistiveThreatMode
 * @typedef {{ distanceM: number; angleDeg: number; mode: AssistiveThreatMode }} EvaluatedAssistiveThreat
 */

/**
 * Within 20 cm in the 270° arc:
 * - sector between 30° and 150° → backup only
 * - 210°–330° → forward only (checked first where zones overlap)
 * - otherwise → no assist
 * @param {{ distanceM: number; angleDeg: number }|null|undefined} threat
 * @param {boolean} enabled
 * @param {boolean} [lidarLive]
 * @param {number} [stopThresholdM]
 * @returns {EvaluatedAssistiveThreat|null}
 */
export function evaluateAssistiveThreat(
  threat,
  enabled,
  lidarLive = true,
  stopThresholdM = LIDAR_ASSIST_STOP_M,
) {
  if (
    !enabled ||
    !lidarLive ||
    threat == null ||
    !Number.isFinite(threat.distanceM) ||
    !Number.isFinite(threat.angleDeg) ||
    threat.distanceM >= stopThresholdM
  ) {
    return null;
  }

  if (isAngleInAssistForwardRange(threat.angleDeg)) {
    return { ...threat, mode: "forward_only" };
  }

  if (isAngleInAssistBackupRange(threat.angleDeg)) {
    return { ...threat, mode: "backup_only" };
  }

  return null;
}

/**
 * @param {{ distanceM: number; angleDeg: number }|null|undefined} threat
 * @param {boolean} enabled
 * @param {boolean} [lidarLive]
 */
export function isAssistiveThreatActive(threat, enabled, lidarLive = true) {
  return evaluateAssistiveThreat(threat, enabled, lidarLive) != null;
}

/** @deprecated use isAssistiveThreatActive */
export function isAssistiveBrakeActive(
  minDistM,
  enabled,
  lidarLive = true,
  stopThresholdM = LIDAR_ASSIST_STOP_M,
) {
  return isAssistiveThreatActive(
    minDistM == null ? null : { distanceM: minDistM, angleDeg: 0 },
    enabled,
    lidarLive,
  );
}

export function filterKeyboardBackupOnly(keys) {
  return keys.filter((key) => !FORWARD_KEYS.has(key));
}

export function filterKeyboardForwardOnly(keys) {
  return keys.filter((key) => !BACK_KEYS.has(key));
}

export function filterDriveBackupOnly(drive) {
  const x = drive?.x ?? 0;
  let y = drive?.y ?? 0;
  if (y > DRIVE_FWD_EPS) y = 0;
  return { x, y };
}

export function filterDriveForwardOnly(drive) {
  const x = drive?.x ?? 0;
  let y = drive?.y ?? 0;
  if (y < -DRIVE_FWD_EPS) y = 0;
  return { x, y };
}

/**
 * @param {unknown} payload
 * @param {EvaluatedAssistiveThreat} threat
 */
export function applyAssistiveControl(payload, threat) {
  const filterKeys =
    threat.mode === "forward_only" ? filterKeyboardForwardOnly : filterKeyboardBackupOnly;
  const filterDrive =
    threat.mode === "forward_only" ? filterDriveForwardOnly : filterDriveBackupOnly;

  if (Array.isArray(payload)) return filterKeys(payload);
  if (!payload || typeof payload !== "object") return payload;
  const record = /** @type {{ drive?: { x?: number; y?: number } }} */ (payload);
  if (record.drive == null) return payload;
  return { ...record, drive: filterDrive(record.drive) };
}

/** @deprecated use applyAssistiveControl */
export function applyAssistiveBrakeToControl(payload) {
  return applyAssistiveControl(payload, { distanceM: 0, angleDeg: 0, mode: "backup_only" });
}

/** @param {unknown} payload */
export function controlPayloadHasDrive(payload) {
  if (Array.isArray(payload)) return payload.length > 0;
  if (!payload || typeof payload !== "object") return false;
  return /** @type {{ drive?: unknown }} */ (payload).drive != null;
}

/**
 * @param {{ x?: number; y?: number }} drive
 * @param {EvaluatedAssistiveThreat} threat
 */
export function driveNeedsAssistFilter(drive, threat) {
  const y = drive?.y ?? 0;
  if (threat.mode === "forward_only") return y < -DRIVE_FWD_EPS;
  return y > DRIVE_FWD_EPS;
}

/**
 * @param {string[]} keys
 * @param {EvaluatedAssistiveThreat} threat
 */
export function keyboardNeedsAssistFilter(keys, threat) {
  if (threat.mode === "forward_only") return keys.some((key) => BACK_KEYS.has(key));
  return keys.some((key) => FORWARD_KEYS.has(key));
}

/** @param {EvaluatedAssistiveThreat} _threat */
export function assistiveDriveStopPayload(_threat) {
  return { drive: { x: 0, y: 0 } };
}
