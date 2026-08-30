import { describe, it, expect, vi } from "vitest";
import { render } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { SystemControls } from "./SystemControls.jsx";

describe("SystemControls", () => {
  it("returns null when not powered", () => {
    const { container } = render(
      <SystemControls
        isPowered={false}
        nvActive={false}
        resMode="720p"
        focusMode="far"
        isCapturing={false}
        quietMode={false}
        powerSavingEnabled
        onQuietModeChange={vi.fn()}
        onPowerSavingChange={vi.fn()}
        onNVToggle={vi.fn()}
        onResChange={vi.fn()}
        onFocusChange={vi.fn()}
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
    render(
      <SystemControls
        isPowered
        nvActive={false}
        resMode="720p"
        focusMode="far"
        isCapturing={false}
        quietMode={false}
        powerSavingEnabled
        onQuietModeChange={vi.fn()}
        onPowerSavingChange={vi.fn()}
        onNVToggle={vi.fn()}
        onResChange={vi.fn()}
        onFocusChange={vi.fn()}
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
    expect(document.querySelector(".settings-capture-button")).toBeTruthy();
    expect(document.body.textContent).toMatch(
      /Stream|Night|720|Focus|PSM|On|Off|Driving|Control|Mode|ECO|Sport|Assist|Map|Metrics|View telemetry|Reboot|Shutdown/i,
    );
    await user.click(document.querySelector(".settings-drawer-backdrop"));
    expect(document.querySelector(".settings-menu-drawer")).toHaveAttribute(
      "data-state",
      "closed",
    );
  });
});
