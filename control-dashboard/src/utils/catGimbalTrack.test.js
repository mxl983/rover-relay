import { describe, expect, it } from "vitest";
import {
  computeCatGimbalCommand,
  getCatOffset,
  isGimbalActive,
  nextCenteredSettled,
  bboxMovedEnough,
  gimbalCommandsEqual,
  smoothGimbalCommand,
  CAT_GIMBAL_SETTLE_RADIUS,
  CAT_GIMBAL_RESUME_RADIUS,
  CAT_GIMBAL_MAX,
} from "./catGimbalTrack.js";


function boxAround(cx, cy, half = 0.1) {
  return [cx - half, cy - half, cx + half, cy + half];
}

describe("getCatOffset / settle hysteresis", () => {
  it("measures offset from frame center", () => {
    const off = getCatOffset({
      cat_present: true,
      bbox_norm: boxAround(0.7, 0.5),
    });
    expect(off.errX).toBeCloseTo(0.2, 5);
    expect(off.errY).toBeCloseTo(0, 5);
    expect(off.radius).toBeCloseTo(0.2, 5);
  });

  it("settles inside settle radius and stays until resume radius", () => {
    expect(nextCenteredSettled(false, CAT_GIMBAL_SETTLE_RADIUS)).toBe(true);
    expect(nextCenteredSettled(false, CAT_GIMBAL_SETTLE_RADIUS + 0.02)).toBe(
      false,
    );

    const mid = (CAT_GIMBAL_SETTLE_RADIUS + CAT_GIMBAL_RESUME_RADIUS) / 2;
    expect(nextCenteredSettled(true, mid)).toBe(true);
    expect(nextCenteredSettled(true, CAT_GIMBAL_RESUME_RADIUS + 0.01)).toBe(
      false,
    );
  });
});

describe("computeCatGimbalCommand", () => {
  it("returns zero when no cat", () => {
    expect(computeCatGimbalCommand(null)).toEqual({ x: 0, y: 0 });
    expect(computeCatGimbalCommand({ cat_present: false })).toEqual({
      x: 0,
      y: 0,
    });
  });

  it("returns zero when already settled", () => {
    const cmd = computeCatGimbalCommand(
      { cat_present: true, bbox_norm: boxAround(0.8, 0.8) },
      { settled: true },
    );
    expect(cmd).toEqual({ x: 0, y: 0 });
  });

  it("returns zero inside settle deadzone", () => {
    const d = CAT_GIMBAL_SETTLE_RADIUS * 0.4;
    const cmd = computeCatGimbalCommand({
      cat_present: true,
      bbox_norm: boxAround(0.5, 0.5, d),
    });
    expect(cmd).toEqual({ x: 0, y: 0 });
  });

  it("pans right when cat is on the right", () => {
    const cmd = computeCatGimbalCommand({
      cat_present: true,
      bbox_norm: boxAround(0.8, 0.5),
    });
    expect(cmd.x).toBeGreaterThan(0.03);
    expect(cmd.x).toBeLessThanOrEqual(CAT_GIMBAL_MAX);
    expect(Math.abs(cmd.y)).toBeLessThan(0.01);
  });

  it("tilts down when cat is below center", () => {
    const cmd = computeCatGimbalCommand({
      cat_present: true,
      bbox_norm: boxAround(0.5, 0.8),
    });
    expect(cmd.y).toBeGreaterThan(0.03);
    expect(cmd.y).toBeLessThanOrEqual(CAT_GIMBAL_MAX);
    expect(Math.abs(cmd.x)).toBeLessThan(0.01);
  });

  it("clamps to max", () => {
    const cmd = computeCatGimbalCommand(
      { cat_present: true, bbox_norm: boxAround(0.95, 0.95) },
      { max: 0.3, gain: 10 },
    );
    expect(cmd.x).toBeLessThanOrEqual(0.3);
    expect(cmd.y).toBeLessThanOrEqual(0.3);
  });
});

describe("smoothGimbalCommand", () => {
  it("rises fast enough to leave the dead snap zone", () => {
    const next = { x: 0.1, y: 0 };
    const stepped = smoothGimbalCommand({ x: 0, y: 0 }, next);
    expect(Math.abs(stepped.x)).toBeGreaterThan(0.02);
  });
});

describe("bboxMovedEnough / gimbalCommandsEqual", () => {
  it("ignores tiny bbox jitter", () => {
    expect(bboxMovedEnough({ cx: 0.5, cy: 0.5 }, { cx: 0.51, cy: 0.5 })).toBe(
      false,
    );
    expect(bboxMovedEnough({ cx: 0.5, cy: 0.5 }, { cx: 0.55, cy: 0.5 })).toBe(
      true,
    );
  });

  it("treats near-identical gimbal cmds as equal", () => {
    expect(gimbalCommandsEqual({ x: 0.1, y: 0 }, { x: 0.105, y: 0 })).toBe(
      true,
    );
    expect(gimbalCommandsEqual({ x: 0.1, y: 0 }, { x: 0.2, y: 0 })).toBe(
      false,
    );
  });
});

describe("isGimbalActive", () => {
  it("detects non-zero vectors", () => {
    expect(isGimbalActive({ x: 0, y: 0 })).toBe(false);
    expect(isGimbalActive({ x: 0.1, y: 0 })).toBe(true);
  });
});
