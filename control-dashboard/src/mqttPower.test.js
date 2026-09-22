import { describe, it, expect, beforeEach, afterEach, vi } from "vitest";
import {
  powerOnPayload,
  powerOffPayload,
  powerOffDelayedPayload,
  publishPowerOn,
  recordPowerOnSent,
  getLastPowerOnAt,
  getBootProgressPercent,
  MQTT_CMD_TOPIC,
  ROVER_BOOT_DURATION_MS,
} from "./mqttPower.js";

describe("mqttPower", () => {
  beforeEach(() => {
    localStorage.clear();
  });

  afterEach(() => {
    localStorage.clear();
    vi.useRealTimers();
  });

  it("builds esp-web-arduino GPIO commands", () => {
    expect(powerOnPayload(13)).toBe("ON GPIO13");
    expect(powerOffPayload(13)).toBe("OFF GPIO13");
    expect(powerOffDelayedPayload(15, 13)).toBe("OFF GPIO13 DELAY 15");
  });

  it("defaults cmd topic to rover/web/cmd", () => {
    expect(MQTT_CMD_TOPIC).toBe("rover/web/cmd");
  });

  it("records last power-on time when publishing ON", () => {
    const client = { publish: vi.fn() };
    const before = Date.now();
    expect(publishPowerOn(client)).toBe(true);
    expect(client.publish).toHaveBeenCalledWith(
      MQTT_CMD_TOPIC,
      "ON GPIO13",
      { qos: 1 },
    );
    const at = getLastPowerOnAt();
    expect(at).toBeGreaterThanOrEqual(before);
    expect(at).toBeLessThanOrEqual(Date.now());
  });

  it("computes boot percent from last MQTT ON over 50s", () => {
    expect(ROVER_BOOT_DURATION_MS).toBe(50_000);
    expect(getBootProgressPercent()).toBeNull();

    recordPowerOnSent(1_000);
    expect(getBootProgressPercent(1_000)).toBe(0);
    expect(getBootProgressPercent(1_000 + 25_000)).toBe(50);
    expect(getBootProgressPercent(1_000 + 50_000)).toBe(99);
    expect(getBootProgressPercent(1_000 + 90_000)).toBe(99);
  });
});
