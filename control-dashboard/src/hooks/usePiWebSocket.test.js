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

  it("handles DRIVE_ASSIST_UPDATE collision and clear messages", async () => {
    const { result } = renderHook(() => usePiWebSocket());
    await act(async () => {
      await new Promise((r) => setTimeout(r, 5));
    });

    act(() => {
      handlers.onmessage?.({
        data: JSON.stringify({
          type: "DRIVE_ASSIST_UPDATE",
          data: {
            active: true,
            assistUiState: "warning",
            obstacle: { closest: { rangeM: 0.18 } },
          },
        }),
      });
    });
    expect(result.current.driveAssistUpdate).toEqual({
      active: true,
      assistUiState: "warning",
      obstacle: { closest: { rangeM: 0.18 } },
    });

    act(() => {
      handlers.onmessage?.({
        data: JSON.stringify({
          type: "DRIVE_ASSIST_UPDATE",
          data: { active: false, assistUiState: "clear" },
        }),
      });
    });
    expect(result.current.driveAssistUpdate).toBeNull();
  });

  it("handles IMU_UPDATE messages", async () => {
    const { result } = renderHook(() => usePiWebSocket());
    await act(async () => {
      await new Promise((r) => setTimeout(r, 5));
    });

    act(() => {
      handlers.onmessage?.({
        data: JSON.stringify({
          type: "IMU_UPDATE",
          data: {
            stamp: 1783406393.87,
            seq: 237,
            connected: true,
            accel: { x: 0.66, y: -0.004, z: -0.003, unit: "g" },
            gyro: { x: -0.01, y: 0.003, z: 1.69, unit: "rad_s" },
            aux: { x: -7, y: -20, z: 2056 },
          },
        }),
      });
    });

    expect(result.current.imu?.seq).toBe(237);
    expect(result.current.imu?.gyro.z).toBeCloseTo(1.69);
    expect(result.current.imuLive).toBe(true);
  });
});
