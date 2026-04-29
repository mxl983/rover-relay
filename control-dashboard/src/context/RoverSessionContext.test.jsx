import { describe, it, expect, beforeEach } from "vitest";
import { renderHook, act } from "@testing-library/react";
import { RoverSessionProvider, useRoverSession } from "./RoverSessionContext.jsx";

function wrapper({ children }) {
  return <RoverSessionProvider>{children}</RoverSessionProvider>;
}

describe("RoverSessionContext", () => {
  beforeEach(() => {
    localStorage.clear();
  });

  it("login sets authenticated and persists", () => {
    const { result } = renderHook(() => useRoverSession(), { wrapper });
    expect(result.current.isAuthenticated).toBe(false);
    act(() => result.current.login({ username: "a", password: "b" }));
    expect(result.current.isAuthenticated).toBe(true);
    expect(result.current.sessionCreds).toEqual({ username: "a", password: "b" });
    expect(JSON.parse(localStorage.getItem("roverSessionCreds"))).toEqual({
      username: "a",
      password: "b",
    });
  });

  it("logout clears session", () => {
    const { result } = renderHook(() => useRoverSession(), { wrapper });
    act(() => result.current.login({ username: "x", password: "y" }));
    act(() => result.current.logout());
    expect(result.current.isAuthenticated).toBe(false);
    expect(localStorage.getItem("roverSessionCreds")).toBeNull();
  });
});
