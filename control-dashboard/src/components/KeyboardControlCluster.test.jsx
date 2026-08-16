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
        usbPower
        laserOn={false}
        voiceSupported={false}
        voiceListening={false}
        isCapturing={false}
      />,
    );
    expect(container.querySelector(".wasd-controls")).toBeTruthy();
  });
});
