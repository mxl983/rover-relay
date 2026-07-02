import { describe, expect, it, vi } from "vitest";
import {
  formatDriveAssistClosestDistance,
  formatDriveAssistDebugLines,
  isDriveAssistHudActive,
  logDriveAssistInfoDetail,
  readDriveAssistClosestRangeM,
  readDriveAssistEnabled,
} from "./driveAssistApi.js";

describe("driveAssistApi", () => {
  it("reads enabled from API responses", () => {
    expect(readDriveAssistEnabled({ success: true, enabled: true })).toBe(true);
    expect(readDriveAssistEnabled({ enabled: false })).toBe(false);
    expect(readDriveAssistEnabled({})).toBeNull();
  });

  it("formats closest distance in meters", () => {
    expect(
      formatDriveAssistClosestDistance({
        obstacle: { closest: { rangeM: 0.28 } },
      }),
    ).toBe("0.28");
    expect(readDriveAssistClosestRangeM({ obstacle: { minRangeM: 1.24 } })).toBe(1.24);
    expect(
      formatDriveAssistClosestDistance({
        obstacle: { closest: { rangeM: 1.24 } },
      }),
    ).toBe("1.24");
  });

  it("detects active collision WS states", () => {
    expect(isDriveAssistHudActive({ active: true, assistUiState: "warning" })).toBe(true);
    expect(isDriveAssistHudActive({ active: true, assistUiState: "maneuvering" })).toBe(true);
    expect(isDriveAssistHudActive({ active: false, assistUiState: "clear" })).toBe(false);
  });

  it("summarizes WS collision payloads", () => {
    const lines = formatDriveAssistDebugLines({
      enabled: true,
      assistUiLabel: "Warning!",
      assistPhase: "stopping",
      obstacle: {
        inRange: true,
        closest: { angleDeg: 95, rangeM: 0.28 },
      },
      blocked: true,
      forwardHold: true,
    });
    expect(lines).toEqual([
      "Warning!",
      "stopping",
      "Obstacle at 95° — 0.28m",
      "blocked",
      "forward hold",
    ]);
  });

  it("returns empty lines when nothing is active", () => {
    expect(formatDriveAssistDebugLines({ enabled: true })).toEqual([]);
  });

  it("logs full info JSON inside a collapsed group", () => {
    const groupCollapsed = vi.fn();
    const groupEnd = vi.fn();
    const log = vi.fn();
    vi.stubGlobal("console", {
      ...console,
      groupCollapsed,
      groupEnd,
      log,
    });

    logDriveAssistInfoDetail("WS DRIVE_ASSIST_UPDATE", {
      active: true,
      assistUiState: "warning",
      obstacle: { inRange: true, closest: { angleDeg: 95, rangeM: 0.28 } },
    });

    expect(groupCollapsed).toHaveBeenCalledOnce();
    expect(log).toHaveBeenCalledWith(expect.stringContaining('"active": true'));
    expect(groupEnd).toHaveBeenCalledOnce();
    vi.unstubAllGlobals();
  });
});
