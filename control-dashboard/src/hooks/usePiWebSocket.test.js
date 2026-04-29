import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { renderHook, act } from "@testing-library/react";
import { usePiWebSocket } from "./usePiWebSocket.js";

describe("usePiWebSocket", () => {
  let handlers;
  const OriginalWebSocket = globalThis.WebSocket;

  beforeEach(() => {
    handlers = {};
    globalThis.WebSocket = vi.fn().mockImplementation(() => {
      const socket = {
        readyState: 0,
        send: vi.fn(),
        close: vi.fn(),
        set onopen(fn) {
          handlers.onopen = fn;
          queueMicrotask(() => fn?.());
        },
        set onmessage(fn) {
          handlers.onmessage = fn;
        },
        set onclose(fn) {
          handlers.onclose = fn;
        },
        set onerror(fn) {
          handlers.onerror = fn;
        },
      };
      return socket;
    });
    vi.stubGlobal("navigator", { geolocation: undefined });
  });

  afterEach(() => {
    globalThis.WebSocket = OriginalWebSocket;
    vi.unstubAllGlobals();
  });

  it("connects and handles PONG", async () => {
    const { result } = renderHook(() => usePiWebSocket());
    await act(async () => {
      await new Promise((r) => setTimeout(r, 5));
    });
    expect(globalThis.WebSocket).toHaveBeenCalled();
    act(() => {
      handlers.onmessage?.({ data: JSON.stringify({ type: "PONG" }) });
    });
    expect(result.current.isOnline).toBe(true);
  });
});
