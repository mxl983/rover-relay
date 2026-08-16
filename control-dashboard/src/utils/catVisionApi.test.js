import { describe, expect, it } from "vitest";
import { visionEndpoints } from "./catVisionApi.js";

describe("visionEndpoints", () => {
  it("uses relay proxy paths under /api/vision", () => {
    const e = visionEndpoints("https://jjcloud.tail9d0237.ts.net:8787/api/vision");
    expect(e.cat).toBe("https://jjcloud.tail9d0237.ts.net:8787/api/vision/cat");
    expect(e.ws).toBe("wss://jjcloud.tail9d0237.ts.net:8787/api/vision/ws");
  });

  it("uses direct vision_server paths", () => {
    const e = visionEndpoints("http://127.0.0.1:8010");
    expect(e.cat).toBe("http://127.0.0.1:8010/api/cat");
    expect(e.ws).toBe("ws://127.0.0.1:8010/ws/vision");
  });
});
