import { describe, expect, it, vi, afterEach } from "vitest";
import {
  anyPadButtonHeld,
  anyGamepadPhysicalInput,
  readActiveGamepadState,
  readGamepadSticks,
  scoreGamepad,
  selectBestGamepad,
} from "./gamepadInput.js";

function fakePad({
  id = "Xbox Controller",
  index = 0,
  mapping = "standard",
  axes = [0, 0, 0, 0],
  buttons = [],
  connected = true,
} = {}) {
  return {
    id,
    index,
    mapping,
    axes,
    buttons: buttons.map((pressed) =>
      typeof pressed === "object" ? pressed : { pressed: Boolean(pressed), value: pressed ? 1 : 0 },
    ),
    connected,
  };
}

describe("gamepadInput", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("scores Xbox pads above generic/ghost devices", () => {
    const xbox = fakePad({
      id: "Xbox Wireless Controller (STANDARD GAMEPAD Vendor: 045e Product: 0b13)",
      mapping: "standard",
    });
    const touch = fakePad({ id: "Touch Digitizer", axes: [0, 0], buttons: [false] });
    expect(scoreGamepad(xbox)).toBeGreaterThan(scoreGamepad(touch));
  });

  it("selects the Xbox pad over a weak unknown slot", () => {
    const ghost = fakePad({ id: "Unknown", mapping: "", axes: [0, 0], index: 0 });
    const xbox = fakePad({ id: "Xbox Wireless Controller", mapping: "standard", index: 1 });
    expect(selectBestGamepad([ghost, xbox])).toBe(xbox);
  });

  it("keeps Xbox standard left/right stick indices", () => {
    const gp = fakePad({
      mapping: "standard",
      axes: [0.2, -0.4, -0.3, 0.5, 0.9, 0.9],
    });
    expect(readGamepadSticks(gp)).toEqual({
      lx: 0.2,
      ly: -0.4,
      rx: -0.3,
      ry: 0.5,
    });
  });

  it("reads right stick from axes 4–5 for non-standard Bluetooth layouts", () => {
    const gp = fakePad({
      id: "Xbox Wireless Controller",
      mapping: "",
      axes: [0.1, -0.2, 0, 0, 0.5, -0.6],
    });
    expect(readGamepadSticks(gp)).toEqual({
      lx: 0.1,
      ly: -0.2,
      rx: 0.5,
      ry: -0.6,
    });
  });

  it("exposes a single Xbox pad via readActiveGamepadState", () => {
    const xbox = fakePad({
      id: "Xbox Wireless Controller",
      mapping: "standard",
      axes: [0.2, -0.3, 0.1, -0.4],
      buttons: Array(12).fill(false),
    });
    vi.stubGlobal("navigator", {
      getGamepads: () => [xbox],
    });
    const state = readActiveGamepadState();
    expect(state?.sticks).toEqual({ lx: 0.2, ly: -0.3, rx: 0.1, ry: -0.4 });
    expect(state?.buttonPads).toEqual([xbox]);
    expect(state?.primary).toBe(xbox);
  });

  it("does not invent dual half-pad fusion from two weak devices", () => {
    const left = fakePad({ id: "Unknown Left", index: 0, mapping: "", axes: [0.2, -0.3] });
    const right = fakePad({ id: "Unknown Right", index: 1, mapping: "", axes: [-0.4, 0.5] });
    vi.stubGlobal("navigator", {
      getGamepads: () => [left, right],
    });
    const state = readActiveGamepadState();
    // Picks one best pad; does not merge two 2-axis ghosts into a virtual controller.
    expect(state?.buttonPads).toHaveLength(1);
    expect(state?.sticks.rx).toBe(0);
    expect(state?.sticks.ry).toBe(0);
  });

  it("detects a held face button", () => {
    const buttons = Array(12).fill(false);
    buttons[3] = true;
    const xbox = fakePad({ buttons });
    expect(anyPadButtonHeld([xbox], 3)).toBe(true);
    expect(anyPadButtonHeld([xbox], 6)).toBe(false);
  });

  it("detects stick deflection as physical input", () => {
    vi.stubGlobal("navigator", {
      getGamepads: () => [
        fakePad({
          axes: [0.2, 0, 0, 0],
          buttons: [{ pressed: false, value: 0 }],
        }),
      ],
    });
    expect(anyGamepadPhysicalInput()).toBe(true);
  });
});
