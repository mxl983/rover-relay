import { describe, it, expect } from "vitest";
import { render } from "@testing-library/react";
import { WifiSignal } from "./WifiSignal.jsx";

describe("WifiSignal", () => {
  it("renders for strong signal", () => {
    const { container } = render(<WifiSignal dbm={-40} />);
    expect(container.querySelectorAll("div").length).toBeGreaterThan(0);
  });
});
