import { describe, expect, it, vi } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import { GamepadFocusHint } from "./GamepadFocusHint.jsx";

describe("GamepadFocusHint", () => {
  it("calls onClaimFocus when clicked", () => {
    const onClaimFocus = vi.fn();
    render(<GamepadFocusHint onClaimFocus={onClaimFocus} />);
    fireEvent.click(screen.getByRole("button", { name: /enable controller inputs/i }));
    expect(onClaimFocus).toHaveBeenCalledTimes(1);
  });
});
