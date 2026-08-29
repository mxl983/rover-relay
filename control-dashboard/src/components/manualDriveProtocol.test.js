import { describe, expect, it } from "vitest";
import { keyboardDrivePayload } from "./KeyboardControlCluster.jsx";
import { buildDriveWebSocketMessage } from "../hooks/usePiWebSocket.js";

describe("keyboard drive protocol", () => {
  it("preserves W/S/A/D direction keys", () => {
    expect(keyboardDrivePayload(["w"])).toEqual(["w"]);
    expect(keyboardDrivePayload(["s"])).toEqual(["s"]);
    expect(keyboardDrivePayload(["a"])).toEqual(["a"]);
    expect(keyboardDrivePayload(["d"])).toEqual(["d"]);
  });

  it("sends an empty keyboard payload for release", () => {
    expect(keyboardDrivePayload([])).toEqual([]);
    expect(keyboardDrivePayload(new Set(["w"]))).toEqual(["w"]);
    expect(buildDriveWebSocketMessage([])).toEqual({
      type: "DRIVE",
      payload: [],
    });
  });
});

describe("WebSocket manual drive protocol", () => {
  it("keeps joystick drive and gimbal at the top level", () => {
    expect(
      buildDriveWebSocketMessage({
        drive: { x: -0.25, y: -0.6 },
        gimbal: { x: 0, y: 0 },
      }),
    ).toEqual({
      type: "DRIVE",
      drive: { x: -0.25, y: -0.6 },
      gimbal: { x: 0, y: 0 },
    });
  });

  it("represents a joystick neutral stop without REST", () => {
    expect(
      buildDriveWebSocketMessage({
        drive: { x: 0, y: 0 },
        gimbal: { x: 0, y: 0 },
      }),
    ).toEqual({
      type: "DRIVE",
      drive: { x: 0, y: 0 },
      gimbal: { x: 0, y: 0 },
    });
  });
});
