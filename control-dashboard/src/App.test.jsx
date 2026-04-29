import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import App from "./App.jsx";
import { RoverSessionProvider } from "./context/RoverSessionContext.jsx";

vi.mock("mqtt", () => ({
  default: {
    connect: vi.fn(() => ({
      on: vi.fn(),
      subscribe: vi.fn(),
      publish: vi.fn(),
      end: vi.fn(),
      connected: false,
    })),
  },
}));

vi.mock("./components/VideoStream.jsx", () => ({
  VideoStream: () => <div data-testid="video-stream" />,
}));

describe("App", () => {
  const OrigWS = globalThis.WebSocket;

  beforeEach(() => {
    localStorage.setItem(
      "roverSessionCreds",
      JSON.stringify({ username: "u", password: "p" }),
    );
    globalThis.WebSocket = vi.fn().mockImplementation(() => {
      const s = {
        readyState: 1,
        send: vi.fn(),
        close: vi.fn(),
        set onopen(fn) {
          queueMicrotask(() => fn?.());
        },
        set onmessage(_fn) {},
        set onclose(_fn) {},
        set onerror(_fn) {},
      };
      return s;
    });
    vi.stubGlobal("navigator", {
      ...navigator,
      geolocation: undefined,
      maxTouchPoints: 0,
    });
    window.confirm = vi.fn(() => false);
  });

  afterEach(() => {
    globalThis.WebSocket = OrigWS;
    vi.unstubAllGlobals();
    localStorage.clear();
  });

  it("shows dashboard when session exists", async () => {
    render(
      <RoverSessionProvider>
        <App />
      </RoverSessionProvider>,
    );
    await waitFor(() => {
      expect(screen.getByTestId("video-stream")).toBeInTheDocument();
    });
  });
});
