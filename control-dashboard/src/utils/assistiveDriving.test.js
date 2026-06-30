import { describe, expect, it } from "vitest";
import {
  applyAssistiveControl,
  controlPayloadHasDrive,
  driveNeedsAssistFilter,
  evaluateAssistiveThreat,
  filterDriveBackupOnly,
  filterDriveForwardOnly,
  filterKeyboardBackupOnly,
  filterKeyboardForwardOnly,
  isAssistiveThreatActive,
  keyboardNeedsAssistFilter,
} from "./assistiveDriving.js";
import {
  closestBodyThreatFromPoints,
  isAngleInAssistBackupRange,
  isAngleInAssistForwardRange,
  LIDAR_ASSIST_STOP_M,
  LIDAR_MINIMAP_ARC_DEG,
} from "./lidarCoords.js";

describe("assistiveDriving", () => {
  it("classifies backup (30°–150° wedge) and forward (210°–330°) zones", () => {
    expect(isAngleInAssistBackupRange(85)).toBe(true);
    expect(isAngleInAssistBackupRange(30)).toBe(true);
    expect(isAngleInAssistBackupRange(150)).toBe(true);
    expect(isAngleInAssistBackupRange(15)).toBe(false);
    expect(isAngleInAssistBackupRange(0)).toBe(false);
    expect(isAngleInAssistBackupRange(180)).toBe(false);
    expect(isAngleInAssistForwardRange(210)).toBe(true);
    expect(isAngleInAssistForwardRange(270)).toBe(true);
    expect(isAngleInAssistForwardRange(330)).toBe(true);
    expect(isAngleInAssistForwardRange(200)).toBe(false);
  });

  it("activates backup assist at 85° within 20 cm", () => {
    expect(evaluateAssistiveThreat({ distanceM: 0.16, angleDeg: 85 }, true, true)).toEqual({
      distanceM: 0.16,
      angleDeg: 85,
      mode: "backup_only",
    });
    expect(filterKeyboardBackupOnly(["w", "a"])).toEqual(["a"]);
    expect(filterDriveBackupOnly({ x: 0.8, y: 0.6 })).toEqual({ x: 0.8, y: 0 });
  });

  it("210°–330° close threat allows only forward", () => {
    expect(evaluateAssistiveThreat({ distanceM: 0.1, angleDeg: 240 }, true, true)).toEqual({
      distanceM: 0.1,
      angleDeg: 240,
      mode: "forward_only",
    });
    expect(filterDriveForwardOnly({ x: 0.8, y: -0.6 })).toEqual({ x: 0.8, y: 0 });
    expect(filterKeyboardForwardOnly(["w", "a", "s"])).toEqual(["w", "a"]);
  });

  it("other angles allow all actions", () => {
    expect(evaluateAssistiveThreat({ distanceM: 0.16, angleDeg: 15 }, true, true)).toBe(null);
    expect(evaluateAssistiveThreat({ distanceM: 0.1, angleDeg: 160 }, true, true)).toBe(null);
    expect(isAssistiveThreatActive({ distanceM: 0.1, angleDeg: 160 }, true, true)).toBe(false);
  });

  it("does not activate at or beyond stop distance", () => {
    expect(
      evaluateAssistiveThreat(
        { distanceM: LIDAR_ASSIST_STOP_M, angleDeg: 85 },
        true,
        true,
      ),
    ).toBe(null);
    expect(isAssistiveThreatActive(null, true, true)).toBe(false);
  });

  it("detects drive-bearing payloads", () => {
    expect(controlPayloadHasDrive(["w"])).toBe(true);
    expect(controlPayloadHasDrive([])).toBe(false);
    expect(controlPayloadHasDrive({ drive: { x: 1, y: 0 } })).toBe(true);
    expect(controlPayloadHasDrive({ gimbal: { x: 0.2, y: 0 } })).toBe(false);
  });

  it("filters drive and keys based on threat mode", () => {
    const backup = { distanceM: 0.16, angleDeg: 85, mode: "backup_only" };
    const forward = { distanceM: 0.1, angleDeg: 240, mode: "forward_only" };
    expect(driveNeedsAssistFilter({ x: 0, y: 0.5 }, backup)).toBe(true);
    expect(driveNeedsAssistFilter({ x: 0, y: -0.5 }, forward)).toBe(true);
    expect(keyboardNeedsAssistFilter(["w"], backup)).toBe(true);
    expect(keyboardNeedsAssistFilter(["s"], forward)).toBe(true);
  });

  it("leaves gimbal untouched", () => {
    expect(
      applyAssistiveControl(
        { drive: { x: 0.5, y: 0.7 }, gimbal: { x: 0.2, y: 0 } },
        { distanceM: 0.16, angleDeg: 85, mode: "backup_only" },
      ),
    ).toEqual({
      drive: { x: 0.5, y: 0 },
      gimbal: { x: 0.2, y: 0 },
    });
  });

  it("finds closest threat bearing in the 270° arc", () => {
    expect(
      closestBodyThreatFromPoints(
        [
          { x: 0, y: 0.1, r: 0.1, a_deg: 270 },
          { x: 0, y: 0.12, r: 0.12, a_deg: 15 },
        ],
        { displayArcDeg: LIDAR_MINIMAP_ARC_DEG },
      ),
    ).toEqual({ distanceM: 0.12, angleDeg: 15 });
  });
});
