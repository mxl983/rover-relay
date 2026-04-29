import { describe, it, expect, vi, beforeEach } from "vitest";
import { renderHook } from "@testing-library/react";
import { useMqtt } from "./useMqtt.js";

const connectMock = vi.fn();

vi.mock("mqtt", () => ({
  default: {
    connect: (...args) => connectMock(...args),
  },
}));

describe("useMqtt", () => {
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
    renderHook(() => useMqtt(null));
    expect(connectMock).not.toHaveBeenCalled();
  });

  it("connects when creds provided", () => {
    renderHook(() => useMqtt({ username: "u", password: "p" }));
    expect(connectMock).toHaveBeenCalled();
  });
});
