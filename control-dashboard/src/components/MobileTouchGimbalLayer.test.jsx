import { describe, it, expect, vi } from "vitest";
import { render } from "@testing-library/react";
import { MobileTouchGimbalLayer } from "./MobileTouchGimbalLayer.jsx";

describe("MobileTouchGimbalLayer", () => {
  it("renders full-screen layer", () => {
    const { container } = render(<MobileTouchGimbalLayer onGimbal={vi.fn()} />);
    expect(container.firstElementChild).toBeTruthy();
  });
});
