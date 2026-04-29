import { describe, it, expect } from "vitest";
import {
  PI_SERVER_IP,
  PI_WEBSOCKET,
  PI_CONTROL_ENDPOINT,
  getAllowedCaptureOrigin,
  MQTT_HOST,
} from "./config.js";

describe("config", () => {
  it("uses vite test env for host", () => {
    expect(PI_SERVER_IP).toBe("test.rover.local");
    expect(PI_WEBSOCKET).toBe("wss://test.rover.local:3000");
    expect(PI_CONTROL_ENDPOINT).toContain("test.rover.local");
    expect(MQTT_HOST).toBe("wss://mqtt.test/mqtt");
  });

  it("getAllowedCaptureOrigin matches control endpoint origin", () => {
    expect(getAllowedCaptureOrigin()).toBe("https://test.rover.local:3000");
  });
});
