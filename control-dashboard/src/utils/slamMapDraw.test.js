import { describe, expect, it } from "vitest";
import { drawSlamMapPoints } from "../utils/slamMapDraw.js";

describe("drawSlamMapPoints", () => {
  it("draws robot-frame map points", () => {
    const ctx = {
      fillStyle: "",
      calls: [],
      beginPath() {},
      arc(x, y, r) {
        this.calls.push({ x, y, r });
      },
      fill() {},
    };
    drawSlamMapPoints(
      ctx,
      90,
      90,
      80,
      4,
      [{ x: 1, y: 0 }, { x: 0, y: 1 }],
      1,
    );
    expect(ctx.calls.length).toBe(2);
    expect(ctx.fillStyle).toContain("130");
  });
});
