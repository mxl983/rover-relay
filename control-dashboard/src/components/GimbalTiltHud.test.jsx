import { describe, it, expect } from "vitest";
import { render } from "@testing-library/react";
import { GimbalTiltHud } from "./GimbalTiltHud.jsx";

describe("GimbalTiltHud", () => {
  it("renders tilt readout", () => {
    const { container } = render(<GimbalTiltHud tilt={95} />);
    expect(container.textContent).toMatch(/°/);
  });
});
