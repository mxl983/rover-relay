import { beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("../api/client.js", () => ({
  apiPost: vi.fn(),
}));

vi.mock("../config.js", () => ({
  PI_SYSTEM_ENDPOINT: "https://rover.example:3000/api/system",
}));

import { apiPost } from "../api/client.js";
import { playRoverChime } from "./chimeApi.js";

describe("playRoverChime", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it("POSTs to /api/system/chime", async () => {
    apiPost.mockResolvedValue({ success: true });
    await playRoverChime();
    expect(apiPost).toHaveBeenCalledWith(
      "https://rover.example:3000/api/system/chime",
      { timeout: 2500, retries: 0 },
    );
  });

  it("swallows errors so setting changes are not blocked", async () => {
    apiPost.mockRejectedValue(new Error("offline"));
    await expect(playRoverChime()).resolves.toBeUndefined();
  });
});
