import { describe, expect, it } from "vitest";
import {
  angularGapDeg,
  buildLidarWallSegments,
  canLinkLidarWallPoints,
} from "./lidarWallDraw.js";

function point(angleDeg, range) {
  const rad = (angleDeg * Math.PI) / 180;
  const lx = Math.cos(rad) * range;
  const ly = Math.sin(rad) * range;
  return {
    angleDeg,
    range,
    lx,
    ly,
    x: lx,
    y: ly,
    bodyDistM: range,
  };
}

describe("lidarWallDraw", () => {
  it("measures angular gap through wrap", () => {
    expect(angularGapDeg(350, 10)).toBeCloseTo(20, 5);
    expect(angularGapDeg(90, 95)).toBeCloseTo(5, 5);
  });

  it("links nearby returns on the same wall", () => {
    const a = point(90, 2);
    const b = point(92, 2.05);
    expect(canLinkLidarWallPoints(a, b)).toBe(true);
  });

  it("breaks across corners and gaps", () => {
    const a = point(90, 2);
    const b = point(110, 2);
    expect(canLinkLidarWallPoints(a, b)).toBe(false);

    const c = point(90, 2);
    const d = point(91, 3.5);
    expect(canLinkLidarWallPoints(c, d)).toBe(false);
  });

  it("merges colinear returns into one segment", () => {
    const sorted = [point(88, 2), point(90, 2.02), point(92, 2.04), point(120, 1.5)];
    const segments = buildLidarWallSegments(sorted);
    expect(segments).toHaveLength(2);
    expect(segments[0]).toHaveLength(3);
    expect(segments[1]).toHaveLength(1);
  });
});
