import { describe, it, expect, vi, beforeEach } from "vitest";
import { renderHook } from "@testing-library/react";
import { useEspMqtt } from "./useEspMqtt.js";

const connectMock = vi.fn();

vi.mock("mqtt", () => ({
  default: {
    connect: (...args) => connectMock(...args),
  },
}));

describe("useEspMqtt", () => {
  beforeEach(() => {
    connectMock.mockReset();
    connectMock.mockReturnValue({
      on: vi.fn(),
      subscribe: vi.fn(),
      publish: vi.fn(),
      end: vi.fn(),
      connected: false,
    });
  });

  it("does not connect without creds", () => {
    renderHook(() => useEspMqtt(null));
    expect(connectMock).not.toHaveBeenCalled();
  });

  it("connects when creds provided", () => {
    renderHook(() => useEspMqtt({ username: "u", password: "p" }));
    expect(connectMock).toHaveBeenCalled();
  });
});
