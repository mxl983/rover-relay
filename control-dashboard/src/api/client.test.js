import { describe, it, expect, vi, beforeEach } from "vitest";
import { apiPostJson, apiPost, apiFetch } from "./client";

describe("api client", () => {
  beforeEach(() => {
    vi.stubGlobal("fetch", vi.fn());
  });

  it("apiFetch passes through to fetch with timeout", async () => {
    const mockRes = { ok: true, text: () => Promise.resolve("") };
    fetch.mockResolvedValue(mockRes);

    const res = await apiFetch("https://example.test/api/foo", {
      method: "GET",
      timeout: 100,
    });
    expect(fetch).toHaveBeenCalledWith(
      "https://example.test/api/foo",
      expect.objectContaining({ method: "GET" }),
    );
    expect(res).toBe(mockRes);
  });

  it("apiFetch rejects empty URLs that would hit the page origin", async () => {
    await expect(apiFetch("", { timeout: 100 })).rejects.toThrow(/absolute/);
    expect(fetch).not.toHaveBeenCalled();
  });

  it("apiPostJson throws on non-ok response", async () => {
    fetch.mockResolvedValue({
      ok: false,
      status: 500,
      statusText: "Server Error",
      text: () => Promise.resolve("error body"),
    });

    await expect(apiPostJson("https://example.test/api/foo", {})).rejects.toThrow(
      /500/,
    );
  });

  it("apiPostJson returns parsed JSON on ok", async () => {
    fetch.mockResolvedValue({
      ok: true,
      text: () => Promise.resolve(JSON.stringify({ id: 1 })),
    });

    const out = await apiPostJson("https://example.test/api/foo", {});
    expect(out).toEqual({ id: 1 });
  });

  it("apiPost returns null for empty response", async () => {
    fetch.mockResolvedValue({ ok: true, text: () => Promise.resolve("") });
    const out = await apiPost("https://example.test/api/foo");
    expect(out).toBeNull();
  });
});
