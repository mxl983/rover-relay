import { describe, it, expect } from "vitest";
import { render } from "@testing-library/react";
import { Meters } from "./Meters.jsx";

const stats = {
  pan: 90,
  battery: 80,
  cpuTemp: 40,
  latency: 12,
  isCharging: false,
  voltage: 11,
  distance: 1000,
  cpuLoad: 20,
};

describe("Meters", () => {
  it("renders compact", () => {
    const { container } = render(<Meters stats={stats} compact />);
    expect(container.querySelector(".meter-container")).toBeTruthy();
  });

  it("renders expanded rows when not compact", () => {
    render(<Meters stats={stats} compact={false} />);
    expect(document.body.textContent).toMatch(/BAT/);
  });
});
