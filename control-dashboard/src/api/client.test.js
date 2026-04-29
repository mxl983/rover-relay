import { describe, it, expect, vi, beforeEach } from "vitest";
import { apiPostJson, apiPost, apiFetch } from "./client";

describe("api client", () => {
  beforeEach(() => {
    vi.stubGlobal("fetch", vi.fn());
  });

  it("apiFetch passes through to fetch with timeout", async () => {
    const mockRes = { ok: true, text: () => Promise.resolve("") };
    fetch.mockResolvedValue(mockRes);

    const res = await apiFetch("/api/foo", { method: "GET", timeout: 100 });
    expect(fetch).toHaveBeenCalledWith("/api/foo", expect.objectContaining({ method: "GET" }));
    expect(res).toBe(mockRes);
  });

  it("apiPostJson throws on non-ok response", async () => {
    fetch.mockResolvedValue({
      ok: false,
      status: 500,
      statusText: "Server Error",
      text: () => Promise.resolve("error body"),
    });

    await expect(apiPostJson("/api/foo", {})).rejects.toThrow(/500/);
  });

  it("apiPostJson returns parsed JSON on ok", async () => {
    fetch.mockResolvedValue({
      ok: true,
      text: () => Promise.resolve(JSON.stringify({ id: 1 })),
    });

    const out = await apiPostJson("/api/foo", {});
    expect(out).toEqual({ id: 1 });
  });

  it("apiPost returns null for empty response", async () => {
    fetch.mockResolvedValue({ ok: true, text: () => Promise.resolve("") });
    const out = await apiPost("/api/foo");
    expect(out).toBeNull();
  });
});
