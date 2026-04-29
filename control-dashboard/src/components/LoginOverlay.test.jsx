import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { LoginOverlay } from "./LoginOverlay.jsx";

const handlers = {};

vi.mock("mqtt", () => ({
  default: {
    connect: vi.fn(() => {
      const client = {
        on: vi.fn((ev, fn) => {
          handlers[ev] = fn;
        }),
        publish: vi.fn(),
        end: vi.fn(),
      };
      queueMicrotask(() => handlers.connect?.());
      return client;
    }),
  },
}));

describe("LoginOverlay", () => {
  beforeEach(() => {
    Object.keys(handlers).forEach((k) => delete handlers[k]);
  });

  it("submits credentials and calls onLoginSuccess", async () => {
    const user = userEvent.setup();
    const onLoginSuccess = vi.fn();
    render(<LoginOverlay onLoginSuccess={onLoginSuccess} />);
    const inputs = document.querySelectorAll(".hud-input");
    await user.type(inputs[0], "u1");
    await user.type(inputs[1], "p1");
    await user.click(screen.getByRole("button", { name: /INITIALIZE_UPLINK/i }));
    await vi.waitFor(() => expect(onLoginSuccess).toHaveBeenCalled());
  });
});
