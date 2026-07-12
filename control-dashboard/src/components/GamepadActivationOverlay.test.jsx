import { describe, expect, it, vi } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import { GamepadActivationOverlay } from "./GamepadActivationOverlay.jsx";

describe("GamepadActivationOverlay", () => {
  it("calls onActivate when tapped", () => {
    const onActivate = vi.fn();
    render(<GamepadActivationOverlay onActivate={onActivate} />);
    fireEvent.click(screen.getByRole("button", { name: /controller button/i }));
    expect(onActivate).toHaveBeenCalledTimes(1);
  });
});
