import { describe, expect, it } from "vitest";
import { laserToCanvas, nearestPointWithAngle, pointAngleDeg, pointToLaserXY } from "./lidarCoords.js";

describe("lidarCoords", () => {
  it("maps forward (+x, 0°) to screen right", () => {
    const { x, y } = laserToCanvas(100, 100, 2, 0, 50, 6);
    expect(x).toBeGreaterThan(100);
    expect(y).toBeCloseTo(100, 5);
  });

  it("maps left (+y, 90°) to screen up", () => {
    const { x, y } = laserToCanvas(100, 100, 0, 2, 50, 6);
    expect(x).toBeCloseTo(100, 5);
    expect(y).toBeLessThan(100);
  });

  it("converts polar points to laser xy", () => {
    const { lx, ly, range, angleDeg } = pointToLaserXY({ a: 0, r: 3 });
    expect(lx).toBeCloseTo(3, 5);
    expect(ly).toBeCloseTo(0, 5);
    expect(range).toBe(3);
    expect(angleDeg).toBeCloseTo(0, 5);
  });

  it("reads explicit a_deg on points", () => {
    expect(pointAngleDeg({ a_deg: 45, x: 1, y: 1 })).toBe(45);
    const nearest = nearestPointWithAngle([
      { x: 2, y: 0, r: 2, a_deg: 0 },
      { x: 0, y: 1, r: 1, a_deg: 90 },
    ]);
    expect(nearest?.range).toBe(1);
    expect(nearest?.angleDeg).toBe(90);
  });
});
