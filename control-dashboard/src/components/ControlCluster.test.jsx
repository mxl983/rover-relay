import { describe, it, expect, vi } from "vitest";
import { render } from "@testing-library/react";
import { ControlCluster } from "./ControlCluster.jsx";

const noop = () => {};

describe("ControlCluster", () => {
  it("renders without crashing", () => {
    const { container } = render(
      <ControlCluster
        onDrive={noop}
        onLightToggle={noop}
        onLaserToggle={noop}
        onVoiceStart={noop}
        onVoiceStop={noop}
        onDockingToggle={noop}
        onCapture={noop}
        onReset={noop}
        usbPower
        laserOn={false}
        voiceSupported={false}
        voiceListening={false}
        isDockingMode={false}
        isCapturing={false}
      />,
    );
    expect(container.querySelector(".wasd-controls")).toBeTruthy();
  });
});
