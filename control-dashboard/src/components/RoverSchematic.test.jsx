import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { act, render, screen } from "@testing-library/react";
import {
  formatClockShort,
  RoverSchematic,
} from "./RoverSchematic.jsx";

describe("RoverSchematic", () => {
  beforeEach(() => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date("2026-08-16T06:48:00"));
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  it("keeps battery visible and rolls secondary metrics", () => {
    const { container } = render(
      <RoverSchematic
        pan={90}
        battery={50}
        cpuTemp={40}
        ambientTempC={22}
        latencyMs={10}
        voltage={11.8}
        wifiSignal={-70}
        distanceMeters={12500}
        pressureHpa={1013.2}
        cpuLoad={35}
        isCharging={false}
      />,
    );

    const clock = formatClockShort(new Date("2026-08-16T06:48:00"));
    expect(screen.getByText(clock)).toBeTruthy();
    expect(screen.getAllByLabelText(/Battery 50%/i).length).toBeGreaterThan(0);
    expect(screen.getByText("50%", { selector: "text" })).toBeTruthy();
    expect(screen.queryByText("BAT")).toBeNull();
    expect(screen.getAllByText("TMP").length).toBeGreaterThan(0);
    expect(screen.getAllByText("VOL").length).toBeGreaterThan(0);
    expect(screen.getAllByText("WIFI").length).toBeGreaterThan(0);
    expect(screen.getAllByText("LV2").length).toBeGreaterThan(0);
    expect(screen.getAllByText("AIR").length).toBeGreaterThan(0);
    expect(screen.getAllByText("DST").length).toBeGreaterThan(0);
    expect(screen.getAllByText("13k").length).toBeGreaterThan(0);
    expect(screen.getAllByText("0").length).toBeGreaterThan(0);
    expect(screen.queryByText("ETA")).toBeNull();
    expect(screen.queryByText("-70")).toBeNull();

    const track = container.querySelector('[style*="translateY"]');
    expect(track).toBeTruthy();
    expect(track.getAttribute("style") || "").toMatch(/translateY\(-?0px\)/);

    act(() => {
      vi.advanceTimersByTime(4000);
    });

    expect(screen.getByText(clock)).toBeTruthy();
    expect(screen.getAllByLabelText(/Battery 50%/i).length).toBeGreaterThan(0);
    expect(track.getAttribute("style") || "").toMatch(/translateY\(-20px\)/);
  });
});

describe("formatClockShort", () => {
  it("formats local time without seconds", () => {
    const label = formatClockShort(new Date("2026-08-16T06:48:30"));
    expect(label).toMatch(/6:48|06:48/);
    expect(label.includes("30")).toBe(false);
  });
});
