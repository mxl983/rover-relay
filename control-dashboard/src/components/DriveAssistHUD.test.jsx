import { describe, it, expect } from "vitest";
import { render } from "@testing-library/react";
import { DriveAssistHUD } from "./DriveAssistHUD.jsx";

describe("DriveAssistHUD", () => {
  it("renders tilt readout", () => {
    const { container } = render(<DriveAssistHUD tilt={95} />);
    expect(container.textContent).toMatch(/°/);
  });
});
