import { describe, it, expect, beforeEach } from "vitest";
import {
  getPresenceClientLabel,
  getOrCreatePresenceClientId,
  readPresenceSnapshot,
} from "./presenceClient.js";

describe("presenceClient", () => {
  beforeEach(() => {
    localStorage.clear();
  });

  it("labels common platforms", () => {
    expect(getPresenceClientLabel("Mozilla/5.0 (iPhone; CPU iPhone OS 17_0)")).toBe("iPhone");
    expect(getPresenceClientLabel("Mozilla/5.0 (Macintosh; Intel Mac OS X 14_0)")).toBe("Mac");
    expect(getPresenceClientLabel("Mozilla/5.0 (Windows NT 10.0; Win64; x64)")).toBe("Windows");
    expect(getPresenceClientLabel("Mozilla/5.0 (Linux; Android 14)")).toBe("Android");
  });

  it("persists a client id in localStorage", () => {
    const a = getOrCreatePresenceClientId();
    const b = getOrCreatePresenceClientId();
    expect(a).toBe(b);
    expect(a.length).toBeGreaterThan(8);
  });

  it("reads presence snapshots", () => {
    expect(readPresenceSnapshot(null)).toBeNull();
    expect(
      readPresenceSnapshot({
        count: 2,
        clients: [
          { id: "a", label: "iPhone" },
          { id: "b", label: "Mac" },
        ],
      }),
    ).toEqual({
      count: 2,
      clients: [
        { id: "a", label: "iPhone" },
        { id: "b", label: "Mac" },
      ],
    });
  });
});
