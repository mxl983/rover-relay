import { describe, it, expect } from "vitest";
import { render, screen } from "@testing-library/react";
import { HudIndicatorStrip } from "./HudIndicatorStrip.jsx";

const warningUpdate = {
  active: true,
  assistUiState: "warning",
  obstacle: {
    inRange: true,
    closest: { angleDeg: 95, rangeM: 0.18 },
  },
};

const maneuverUpdate = {
  active: true,
  assistUiState: "maneuvering",
  obstacle: {
    inRange: true,
    closest: { angleDeg: 95, rangeM: 0.35 },
  },
};

describe("HudIndicatorStrip", () => {
  it("reserves indicator slots without showing collision when inactive", () => {
    const { container } = render(
      <HudIndicatorStrip
        driveAssistEnabled={false}
        driveAssistUpdate={null}
        navigationEnabled={false}
      />,
    );
    expect(container.querySelectorAll(".hud-indicator-slot")).toHaveLength(6);
    expect(screen.queryByLabelText(/collision warning/i)).toBeNull();
  });

  it("shows power saving icon leftmost when enabled", () => {
    const { container } = render(
      <HudIndicatorStrip
        driveAssistEnabled
        driveAssistUpdate={null}
        powerSavingEnabled
        navigationEnabled={false}
      />,
    );
    const slots = container.querySelectorAll(".hud-indicator-slot");
    expect(slots).toHaveLength(7);
    expect(slots[0]).toHaveClass("hud-indicator-slot--power-saving");
    expect(screen.getByLabelText("Idle shutdown enabled").querySelector(".hud-indicator-icon--power-saving")).toBeTruthy();
  });

  it("shows zap icon in sport mode", () => {
    const { container } = render(
      <HudIndicatorStrip
        driveAssistEnabled={false}
        driveAssistUpdate={null}
        quietMode={false}
      />,
    );
    expect(screen.getByLabelText("Sport drive mode")).toBeTruthy();
    expect(container.querySelector(".hud-indicator-icon--sport")).toBeTruthy();
  });

  it("hides sport icon in eco mode", () => {
    render(
      <HudIndicatorStrip
        driveAssistEnabled={false}
        driveAssistUpdate={null}
        quietMode
      />,
    );
    expect(screen.queryByLabelText("Sport drive mode")).toBeNull();
  });

  it("shows navigation compass when roam is enabled", () => {
    render(
      <HudIndicatorStrip
        driveAssistEnabled={false}
        driveAssistUpdate={null}
        navigationEnabled
      />,
    );
    expect(screen.getByLabelText("Autonomous roam active")).toBeTruthy();
  });

  it("shows radar icon when drive assist is on", () => {
    const { container } = render(
      <HudIndicatorStrip driveAssistEnabled driveAssistUpdate={null} />,
    );
    expect(screen.getByLabelText("Drive assist on")).toBeTruthy();
    expect(container.querySelector(".hud-indicator-icon--drive-assist")).toBeTruthy();
  });

  it("hides power saving icon when disabled", () => {
    render(
      <HudIndicatorStrip
        driveAssistEnabled
        driveAssistUpdate={null}
        powerSavingEnabled={false}
      />,
    );
    expect(screen.queryByLabelText("Idle shutdown enabled")).toBeNull();
  });

  it("shows red collision icon without distance text", () => {
    const { container } = render(
      <HudIndicatorStrip driveAssistEnabled driveAssistUpdate={warningUpdate} />,
    );
    expect(screen.getByLabelText(/collision warning/i)).toBeTruthy();
    expect(container.querySelector(".hud-indicator-icon--collision")).toBeTruthy();
    expect(container.querySelector(".hud-indicator-value")).toBeNull();
  });

  it("uses the same red icon during maneuvering", () => {
    const { container } = render(
      <HudIndicatorStrip driveAssistEnabled driveAssistUpdate={maneuverUpdate} />,
    );
    expect(screen.getByLabelText(/collision warning/i)).toBeTruthy();
    expect(container.querySelector(".hud-indicator-slot--collision.hud-indicator-slot--active")).toBeTruthy();
    expect(container.querySelector(".hud-indicator-value")).toBeNull();
  });

  it("hides collision indicator when assist is off", () => {
    render(
      <HudIndicatorStrip driveAssistEnabled={false} driveAssistUpdate={warningUpdate} />,
    );
    expect(screen.queryByLabelText(/collision warning/i)).toBeNull();
  });

  it("shows blinking charging battery icon when charging", () => {
    const { container } = render(
      <HudIndicatorStrip
        driveAssistEnabled={false}
        driveAssistUpdate={null}
        isCharging
      />,
    );
    expect(screen.getByLabelText("Charging")).toBeTruthy();
    expect(container.querySelector(".hud-indicator-slot--charging.hud-indicator-slot--active")).toBeTruthy();
    expect(container.querySelector(".hud-indicator-icon--charging")).toBeTruthy();
  });

  it("shows blinking low battery icon when armed and below threshold", () => {
    const { container } = render(
      <HudIndicatorStrip
        driveAssistEnabled={false}
        driveAssistUpdate={null}
        isLowBattery
        lowBatteryIndicatorArmed
      />,
    );
    expect(screen.getByLabelText("Low battery")).toBeTruthy();
    expect(container.querySelector(".hud-indicator-slot--low-battery.hud-indicator-slot--active")).toBeTruthy();
  });

  it("prefers charging indicator over low battery", () => {
    render(
      <HudIndicatorStrip
        driveAssistEnabled={false}
        driveAssistUpdate={null}
        isCharging
        isLowBattery
        lowBatteryIndicatorArmed
      />,
    );
    expect(screen.getByLabelText("Charging")).toBeTruthy();
    expect(screen.queryByLabelText("Low battery")).toBeNull();
  });

  it("hides indicator when collision clears", () => {
    render(
      <HudIndicatorStrip
        driveAssistEnabled
        driveAssistUpdate={{ active: false, assistUiState: "clear" }}
      />,
    );
    expect(screen.queryByLabelText(/collision warning/i)).toBeNull();
  });
});
