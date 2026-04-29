import { describe, it, expect } from "vitest";
import { render } from "@testing-library/react";
import { RoverSchematic } from "./RoverSchematic.jsx";

describe("RoverSchematic", () => {
  it("renders svg", () => {
    const { container } = render(
      <RoverSchematic
        pan={90}
        battery={50}
        cpuTemp={40}
        latencyMs={10}
        isCharging={false}
      />,
    );
    expect(container.querySelector("svg")).toBeTruthy();
  });
});
