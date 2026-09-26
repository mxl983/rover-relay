import { describe, it, expect, vi } from "vitest";
import { render } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { SystemControls } from "./SystemControls.jsx";

describe("SystemControls", () => {
  it("returns null when not powered", () => {
    const { container } = render(
      <SystemControls
        isPowered={false}
        resMode="720p"
        quietMode={false}
        powerSavingEnabled
        onPowerSavingChange={vi.fn()}
        onResChange={vi.fn()}
        onAction={vi.fn()}
        controlMode="keyboard"
        onControlModeChange={vi.fn()}
      />,
    );
    expect(container.firstChild).toBeNull();
  });

  it("opens menu when powered", async () => {
    const user = userEvent.setup();
    const onAction = vi.fn();
    const onPowerSavingChange = vi.fn();
    render(
      <SystemControls
        isPowered
        resMode="720p"
        quietMode={false}
        powerSavingEnabled
        powerSavingTimeoutMinutes={5}
        onPowerSavingChange={onPowerSavingChange}
        onResChange={vi.fn()}
        onAction={onAction}
        controlMode="keyboard"
        onControlModeChange={vi.fn()}
      />,
    );
    const trigger = document.querySelector("[aria-haspopup='menu']");
    expect(trigger).toBeTruthy();
    expect(document.querySelector(".settings-menu-drawer")).toHaveAttribute(
      "data-state",
      "closed",
    );
    await user.click(trigger);
    expect(document.querySelector(".settings-menu-drawer")).toHaveAttribute(
      "data-state",
      "open",
    );
    expect(document.querySelector(".settings-capture-button")).toBeNull();
    expect(document.body.textContent).toMatch(
      /Res|PSM|Off|5m|10m|30m|Driving|Control|Speed|Slow|Mid|Fast|Pre-collision|Metrics|Auto Exposure|Reboot|Shutdown/i,
    );
    expect(document.body.textContent).not.toMatch(/\bNV\b|Focus|Stream|\bMode\b|ECO|Sport/i);
    await user.click(document.querySelector('[aria-label="10m"]'));
    expect(onPowerSavingChange).toHaveBeenCalledWith({
      enabled: true,
      timeoutMinutes: 10,
    });
    await user.click(document.querySelector('[aria-label="Off"]'));
    expect(onPowerSavingChange).toHaveBeenCalledWith({ enabled: false });

    await user.click(document.querySelector(".settings-drawer-backdrop"));
    expect(document.querySelector(".settings-menu-drawer")).toHaveAttribute(
      "data-state",
      "closed",
    );
  });
});
