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
      />,
    );
    expect(container.querySelectorAll(".hud-indicator-slot")).toHaveLength(5);
    expect(screen.queryByLabelText(/collision warning/i)).toBeNull();
  });

  it("shows power saving icon when enabled", () => {
    const { container } = render(
      <HudIndicatorStrip
        driveAssistEnabled
        driveAssistUpdate={null}
        powerSavingEnabled
        powerSavingTtlMs={125000}
      />,
    );
    const slots = container.querySelectorAll(".hud-indicator-slot");
    expect(slots).toHaveLength(6);
    expect(container.querySelector(".hud-indicator-slot--power-saving")).toBeTruthy();
    expect(screen.getByLabelText("Idle shutdown in 2:05").querySelector(".hud-indicator-icon--power-saving")).toBeTruthy();
    expect(screen.getByText("2:05")).toBeTruthy();
  });

  it("shows one icon per connected viewer with platform text beneath", () => {
    const { container } = render(
      <HudIndicatorStrip
        driveAssistEnabled={false}
        driveAssistUpdate={null}
        presence={{
          count: 2,
          clients: [
            { id: "a", label: "iPhone" },
            { id: "b", label: "Mac" },
          ],
        }}
      />,
    );
    expect(
      screen.getByLabelText("2 viewers online · iPhone, Mac"),
    ).toBeTruthy();
    const slots = container.querySelectorAll(".hud-indicator-slot--presence");
    expect(slots).toHaveLength(2);
    expect(screen.getByText("iPhone")).toBeTruthy();
    expect(screen.getByText("Mac")).toBeTruthy();
    expect(container.querySelectorAll(".hud-indicator-icon--presence")).toHaveLength(2);
  });

  it("hides presence when no viewers are reported", () => {
    render(
      <HudIndicatorStrip
        driveAssistEnabled={false}
        driveAssistUpdate={null}
        presence={{ count: 0, clients: [] }}
      />,
    );
    expect(screen.queryByLabelText(/viewer/i)).toBeNull();
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

  it("shows radar icon when pre-collision stop is on", () => {
    const { container } = render(
      <HudIndicatorStrip driveAssistEnabled driveAssistUpdate={null} />,
    );
    expect(screen.getByLabelText("Pre-collision stop on")).toBeTruthy();
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

  it("shows blinking weak wifi icon only when signal is weak", () => {
    const { container, rerender } = render(
      <HudIndicatorStrip
        driveAssistEnabled={false}
        driveAssistUpdate={null}
        wifiSignal={-50}
      />,
    );
    expect(screen.queryByLabelText(/wi-fi signal cut/i)).toBeNull();

    rerender(
      <HudIndicatorStrip
        driveAssistEnabled={false}
        driveAssistUpdate={null}
        wifiSignal={-82}
      />,
    );
    expect(screen.getByLabelText(/wi-fi signal cut/i)).toBeTruthy();
    expect(container.querySelector(".hud-indicator-slot--weak-wifi.hud-indicator-slot--active")).toBeTruthy();
    expect(container.querySelector(".hud-indicator-icon--weak-wifi")).toBeTruthy();
  });

  it("shows high latency ethernet icon only above 300ms", () => {
    const { container, rerender } = render(
      <HudIndicatorStrip
        driveAssistEnabled={false}
        driveAssistUpdate={null}
        latencyMs={250}
      />,
    );
    expect(screen.queryByLabelText(/high latency/i)).toBeNull();

    rerender(
      <HudIndicatorStrip
        driveAssistEnabled={false}
        driveAssistUpdate={null}
        latencyMs={420}
      />,
    );
    expect(screen.getByLabelText(/high latency — drive carefully/i)).toBeTruthy();
    expect(container.querySelector(".hud-indicator-slot--high-latency.hud-indicator-slot--active")).toBeTruthy();
    expect(container.querySelector(".hud-indicator-icon--high-latency")).toBeTruthy();
  });
});
