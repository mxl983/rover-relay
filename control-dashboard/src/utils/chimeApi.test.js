import { describe, expect, it } from "vitest";
import { playRoverChime } from "./chimeApi.js";

describe("playRoverChime", () => {
  it("is a no-op that never throws", async () => {
    await expect(playRoverChime()).resolves.toBeUndefined();
  });
});
