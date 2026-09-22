import { describe, it, expect } from "vitest";
import {
  powerOnPayload,
  powerOffPayload,
  powerOffDelayedPayload,
  MQTT_CMD_TOPIC,
} from "./mqttPower.js";

describe("mqttPower", () => {
  it("builds esp-web-arduino GPIO commands", () => {
    expect(powerOnPayload(13)).toBe("ON GPIO13");
    expect(powerOffPayload(13)).toBe("OFF GPIO13");
    expect(powerOffDelayedPayload(15, 13)).toBe("OFF GPIO13 DELAY 15");
  });

  it("defaults cmd topic to rover/web/cmd", () => {
    expect(MQTT_CMD_TOPIC).toBe("rover/web/cmd");
  });
});
