import { describe, it, expect, vi } from "vitest";
import { render } from "@testing-library/react";
import { KeyboardControlCluster } from "./KeyboardControlCluster.jsx";

const noop = () => {};

describe("KeyboardControlCluster", () => {
  it("renders without crashing", () => {
    const { container } = render(
      <KeyboardControlCluster
        onDrive={noop}
        onVoiceStart={noop}
        onVoiceStop={noop}
        onCapture={noop}
        onReset={noop}
        voiceSupported={false}
        voiceListening={false}
        isCapturing={false}
      />,
    );
    expect(container.querySelector(".wasd-controls")).toBeTruthy();
  });

  it("renders drive and remaining action keys without treat/park/laser/light", () => {
    const { container } = render(
      <KeyboardControlCluster
        onDrive={noop}
        onVoiceStart={noop}
        onVoiceStop={noop}
        onCapture={noop}
        onReset={noop}
        voiceSupported={false}
        voiceListening={false}
        isCapturing={false}
      />,
    );
    const buttons = [...container.querySelectorAll(".wasd-controls .btn")];
    const labels = buttons.map((btn) => btn.textContent);
    expect(labels.some((t) => t.includes("TRT"))).toBe(false);
    expect(labels.some((t) => t.includes("PRK"))).toBe(false);
    expect(labels.some((t) => t.includes("LZR"))).toBe(false);
    expect(labels.some((t) => t.includes("RST"))).toBe(true);
    expect(labels.some((t) => t.startsWith("Q"))).toBe(true);
    expect(labels.some((t) => t.startsWith("E"))).toBe(true);
  });
});
