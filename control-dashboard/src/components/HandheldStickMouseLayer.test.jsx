import { describe, expect, it } from "vitest";
import { render, screen } from "@testing-library/react";
import { HandheldStickMouseLayer } from "./HandheldStickMouseLayer.jsx";

describe("HandheldStickMouseLayer", () => {
  it("prompts to tap video to enable left stick drive", () => {
    const viewportRef = { current: document.createElement("div") };
    render(
      <HandheldStickMouseLayer
        enabled
        viewportRef={viewportRef}
        onDrive={() => {}}
        lastGimbalRef={{ current: { x: 0, y: 0 } }}
      />,
    );
    expect(screen.getByText(/enable left stick drive/i)).toBeTruthy();
    expect(screen.getByText(/tap the video once/i)).toBeTruthy();
  });

  it("renders nothing when disabled", () => {
    const { container } = render(
      <HandheldStickMouseLayer
        enabled={false}
        viewportRef={{ current: document.createElement("div") }}
        onDrive={() => {}}
        lastGimbalRef={{ current: { x: 0, y: 0 } }}
      />,
    );
    expect(container.querySelector(".handheld-stick-mouse-layer")).toBeNull();
  });
});
