import { describe, it, expect } from "vitest";
import * as constants from "./constants.js";
import * as config from "./config.js";

describe("constants re-exports", () => {
  it("PI_WEBSOCKET matches config", () => {
    expect(constants.PI_WEBSOCKET).toBe(config.PI_WEBSOCKET);
  });

  it("PI_SERVER_IP matches config", () => {
    expect(constants.PI_SERVER_IP).toBe(config.PI_SERVER_IP);
  });
});
