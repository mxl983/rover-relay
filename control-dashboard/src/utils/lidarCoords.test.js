import { describe, expect, it } from "vitest";
import {
  bearingToCanvasPx,
  bodyProximityPointColor,
  distanceToRoverBodyM,
  forwardViewConeEdgesDeg,
  isAngleInDisplayArc,
  laserBearingToCanvas,
  laserToCanvas,
  lidarMinimapMarkedAnglesDeg,
  LIDAR_BODY_PROXIMITY_M,
  LIDAR_FORWARD_DEG,
  LIDAR_MINIMAP_ARC_DEG,
  minBodyProximityFromPoints,
  nearestPointWithAngle,
  pointAngleDeg,
  pointToLaserXY,
  roverBodyFootprintCornersM,
  ROVER_BODY_WIDTH_M,
  viewHeadingFromPan,
} from "./lidarCoords.js";

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

  it("maps forward bearing to the radar rim", () => {
    const maxR = 50;
    const rangeM = 6;
    const { x, y } = laserBearingToCanvas(100, 100, LIDAR_FORWARD_DEG, maxR, rangeM);
    expect(x).toBeCloseTo(100, 5);
    expect(y).toBeCloseTo(100 - maxR, 5);
  });

  it("computes forward view cone edges opening toward the view heading", () => {
    const { edgeADeg, edgeBDeg } = forwardViewConeEdgesDeg();
    expect(edgeADeg).toBe(60);
    expect(edgeBDeg).toBe(120);
  });

  it("rotates the view cone with pan heading", () => {
    const { edgeADeg, edgeBDeg } = forwardViewConeEdgesDeg(viewHeadingFromPan(120));
    expect(edgeADeg).toBe(90);
    expect(edgeBDeg).toBe(150);
  });

  it("maps data bearings to canvas pixels at a fixed radius", () => {
    expect(bearingToCanvasPx(100, 100, 0, 50)).toEqual({ x: 150, y: 100 });
    expect(bearingToCanvasPx(100, 100, 90, 50)).toEqual({ x: 100, y: 50 });
  });

  it("lists minimap rim angles in the data frame", () => {
    const marks = lidarMinimapMarkedAnglesDeg();
    expect(marks.cardinal).toEqual([0, 90, 180, 270]);
    expect(marks.minor).toEqual([60, 120, 240, 300]);
  });

  it("scales rover body corners with zoom range", () => {
    const cx = 100;
    const cy = 100;
    const maxR = 50;
    const widthPlotZoomedOut = laserToCanvas(cx, cy, ROVER_BODY_WIDTH_M / 2, 0, maxR, 4);
    const widthPlotZoomedIn = laserToCanvas(cx, cy, ROVER_BODY_WIDTH_M / 2, 0, maxR, 2);
    expect(Math.abs(widthPlotZoomedIn.x - cx)).toBeGreaterThan(
      Math.abs(widthPlotZoomedOut.x - cx),
    );
    expect(roverBodyFootprintCornersM()).toHaveLength(4);
  });

  it("measures distance from a point to the rover body rectangle", () => {
    expect(distanceToRoverBodyM(0, -0.5)).toBeCloseTo(0.195, 3);
    expect(distanceToRoverBodyM(0, 0.2)).toBeCloseTo(0.2, 3);
    expect(distanceToRoverBodyM(0, -0.15)).toBe(0);
  });

  it("colors nearby body points yellow to red by proximity", () => {
    expect(bodyProximityPointColor(LIDAR_BODY_PROXIMITY_M)).toBe(null);
    expect(bodyProximityPointColor(0.3)).toBe(null);
    expect(bodyProximityPointColor(0.15)).toBe("rgba(255, 140, 60");
    expect(bodyProximityPointColor(0)).toBe("rgba(255, 60, 40");
  });

  it("finds minimum body proximity across scan points", () => {
    expect(
      minBodyProximityFromPoints([
        { x: 2, y: 0, r: 2 },
        { x: 0, y: 0.2, r: 0.2 },
      ]),
    ).toBeCloseTo(0.2, 5);
    expect(minBodyProximityFromPoints([])).toBe(null);
  });

  it("ignores points outside the 270° display arc for body proximity", () => {
    expect(
      minBodyProximityFromPoints(
        [
          { x: 0, y: 0.1, r: 0.1, a_deg: 270 },
          { x: 0, y: 0.2, r: 0.2, a_deg: 90 },
        ],
        { displayArcDeg: LIDAR_MINIMAP_ARC_DEG },
      ),
    ).toBeCloseTo(0.2, 5);
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

  it("ignores points outside the display arc when finding nearest", () => {
    const nearest = nearestPointWithAngle(
      [
        { x: 0.5, y: 0, r: 0.5, a_deg: 270 },
        { x: 2, y: 0, r: 2, a_deg: 0 },
        { x: 0, y: 1.2, r: 1.2, a_deg: 90 },
      ],
      { displayArcDeg: LIDAR_MINIMAP_ARC_DEG },
    );
    expect(nearest?.range).toBe(1.2);
    expect(nearest?.angleDeg).toBe(90);
    expect(isAngleInDisplayArc(270, LIDAR_MINIMAP_ARC_DEG)).toBe(false);
    expect(isAngleInDisplayArc(90, LIDAR_MINIMAP_ARC_DEG)).toBe(true);
  });
});
