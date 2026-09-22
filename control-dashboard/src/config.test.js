import { describe, it, expect } from "vitest";
import {
  PI_FQDN,
  PI_SERVER_IP,
  WEBRTC_BASE,
  MENTOR_API_BASE,
  VIDEO_STREAM_HOST,
  MENTOR_CMD_VEL_ENDPOINT,
  getAllowedCaptureOrigin,
  MQTT_HOST,
  stickToCmdVel,
} from "./config.js";

describe("config", () => {
  it("uses vite test env FQDN for Pi host", () => {
    expect(PI_FQDN).toBe("test.rover.local");
    expect(PI_SERVER_IP).toBe("test.rover.local");
    expect(WEBRTC_BASE).toContain("test.rover.local");
    expect(VIDEO_STREAM_HOST).toMatch(/https:\/\/test\.rover\.local:8889\/cam\/whep/);
    expect(MENTOR_CMD_VEL_ENDPOINT).toContain("test.rover.local");
    expect(MENTOR_API_BASE).toContain("test.rover.local");
    expect(MQTT_HOST).toBe("wss://mqtt.test/mqtt");
  });

  it("getAllowedCaptureOrigin matches rover-control camera origin", () => {
    expect(getAllowedCaptureOrigin()).toBe(new URL(`https://${PI_FQDN}:3000/api/camera`).origin);
  });

  it("stickToCmdVel maps dashboard forward to +linear_x", () => {
    const t = stickToCmdVel({ x: 0, y: -1 });
    expect(t.linear_x).toBeGreaterThan(0);
    expect(t.angular_z).toBe(0);
  });

  it("stickToCmdVel scales with MentorPi speed tiers", () => {
    const slow = stickToCmdVel({ x: 0, y: -1 }, "slow");
    const mid = stickToCmdVel({ x: 0, y: -1 }, "normal");
    const fast = stickToCmdVel({ x: 0, y: -1 }, "fast");
    // DRIVE_CMD_SCALE default 1.0 — full MentorPi teleop tiers.
    expect(slow.linear_x).toBeCloseTo(0.1, 5);
    expect(mid.linear_x).toBeCloseTo(0.2, 5);
    expect(fast.linear_x).toBeCloseTo(0.35, 5);
    expect(slow.linear_x).toBeLessThan(mid.linear_x);
    expect(mid.linear_x).toBeLessThan(fast.linear_x);
  });
});
