import { describe, expect, it } from "vitest";
import { keyboardDrivePayload } from "./KeyboardControlCluster.jsx";
import { stickToCmdVel } from "../config.js";

describe("keyboard drive protocol", () => {
  it("preserves W/S/A/D direction keys", () => {
    expect(keyboardDrivePayload(["w"])).toEqual(["w"]);
    expect(keyboardDrivePayload(["s"])).toEqual(["s"]);
    expect(keyboardDrivePayload(["a"])).toEqual(["a"]);
    expect(keyboardDrivePayload(["d"])).toEqual(["d"]);
    expect(keyboardDrivePayload(["q", "e"])).toEqual(["e", "q"]);
  });

  it("sends an empty keyboard payload for release", () => {
    expect(keyboardDrivePayload([])).toEqual([]);
    expect(keyboardDrivePayload(new Set(["w"]))).toEqual(["w"]);
  });
});

describe("MentorPi stick → cmd_vel mapping", () => {
  it("maps joystick forward (y=-1) to +linear_x", () => {
    const twist = stickToCmdVel({ x: 0, y: -1 });
    expect(twist.linear_x).toBeGreaterThan(0);
    expect(twist.linear_y).toBe(0);
    expect(twist.angular_z).toBe(0);
  });

  it("maps joystick right (x=+1) to -angular_z (turn right)", () => {
    const twist = stickToCmdVel({ x: 1, y: 0 });
    expect(twist.linear_x).toBe(0);
    expect(twist.angular_z).toBeLessThan(0);
  });

  it("maps neutral stick to zero twist", () => {
    expect(stickToCmdVel({ x: 0, y: 0 })).toEqual({
      linear_x: 0,
      linear_y: 0,
      angular_z: 0,
    });
  });

  it("maps strafe left (strafe=-1) to +linear_y", () => {
    const twist = stickToCmdVel({ x: 0, y: 0, strafe: -1 });
    expect(twist.linear_x).toBe(0);
    expect(twist.linear_y).toBeGreaterThan(0);
    expect(twist.angular_z).toBe(0);
  });

  it("maps strafe right (strafe=+1) to -linear_y", () => {
    const twist = stickToCmdVel({ x: 0, y: 0, strafe: 1 });
    expect(twist.linear_y).toBeLessThan(0);
  });
});
