import { describe, it, expect, vi } from "vitest";
import { render } from "@testing-library/react";
import { KeyboardControlCluster } from "./KeyboardControlCluster.jsx";

const noop = () => {};

describe("KeyboardControlCluster", () => {
  it("renders without crashing", () => {
    const { container } = render(
      <KeyboardControlCluster
        onDrive={noop}
        onLightToggle={noop}
        onLaserToggle={noop}
        onVoiceStart={noop}
        onVoiceStop={noop}
        onCapture={noop}
        onReset={noop}
        onLookDown={noop}
        usbPower
        laserOn={false}
        voiceSupported={false}
        voiceListening={false}
        isCapturing={false}
      />,
    );
    expect(container.querySelector(".wasd-controls")).toBeTruthy();
  });

  it("fills the park slot next to reset", () => {
    const { container } = render(
      <KeyboardControlCluster
        onDrive={noop}
        onLightToggle={noop}
        onLaserToggle={noop}
        onVoiceStart={noop}
        onVoiceStop={noop}
        onCapture={noop}
        onReset={noop}
        onLookDown={noop}
        usbPower
        laserOn={false}
        voiceSupported={false}
        voiceListening={false}
        isCapturing={false}
      />,
    );
    const buttons = [...container.querySelectorAll(".wasd-controls .btn")];
    expect(buttons).toHaveLength(16);
    expect(buttons.some((btn) => btn.textContent.includes("PRK"))).toBe(true);
  });
});
