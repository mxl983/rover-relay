import { describe, it, expect, vi } from "vitest";
import { render } from "@testing-library/react";
import { useRef } from "react";
import { MouseGimbalLayer } from "./MouseGimbalLayer.jsx";

function Wrapper() {
  const ref = useRef(document.createElement("div"));
  return (
    <MouseGimbalLayer
      viewportRef={ref}
      isFullscreen={false}
      isPointerLocked={false}
      onPointerLockChange={vi.fn()}
      onDrive={vi.fn()}
      lastDriveRef={{ current: { x: 0, y: 0 } }}
    />
  );
}

describe("MouseGimbalLayer", () => {
  it("renders null when not fullscreen", () => {
    const { container } = render(<Wrapper />);
    expect(container.firstChild).toBeNull();
  });
});
