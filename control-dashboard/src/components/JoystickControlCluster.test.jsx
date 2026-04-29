import { describe, it, expect, vi } from "vitest";
import { render } from "@testing-library/react";
import { DualJoystickControls } from "./JoystickControlCluster.jsx";

vi.mock("nipplejs", () => ({
  default: {
    create: vi.fn(() => ({
      on: vi.fn(),
      off: vi.fn(),
      destroy: vi.fn(),
    })),
  },
}));

const noop = () => {};

describe("DualJoystickControls", () => {
  it("mounts zones", () => {
    const { container } = render(
      <DualJoystickControls
        onDrive={noop}
        onReset={noop}
        onLookDown={noop}
        onTurnLeft={noop}
        onTurnRight={noop}
        onLaserToggle={noop}
        laserOn={false}
        onHeadlightToggle={noop}
        headlightOn={false}
        onVoiceStart={noop}
        onVoiceStop={noop}
        voiceSupported={false}
        voiceListening={false}
      />,
    );
    expect(container.querySelectorAll('[style*="flex"]').length + container.children.length).toBeGreaterThan(0);
  });
});
