import { describe, expect, it, vi, afterEach } from "vitest";
import {
  anyPadButtonHeld,
  readActiveGamepadState,
  readDualHalfPadSticks,
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

  it("scores Legion Go pads above generic/ghost devices", () => {
    const legion = fakePad({ id: "Legion Go S (Vendor: 1a86 Product: e310)", mapping: "standard" });
    const touch = fakePad({ id: "Touch Digitizer", axes: [0, 0], buttons: [false] });
    expect(scoreGamepad(legion)).toBeGreaterThan(scoreGamepad(touch));
  });

  it("selects the highest-scoring connected pad", () => {
    const ghost = fakePad({ id: "Unknown", mapping: "", axes: [0, 0], index: 0 });
    const xbox = fakePad({ id: "Xbox Wireless Controller", mapping: "standard", index: 1 });
    expect(selectBestGamepad([ghost, xbox])).toBe(xbox);
  });

  it("reads right stick from axes 4–5 when 2–3 are idle", () => {
    const gp = fakePad({
      axes: [0.1, -0.2, 0, 0, 0.5, -0.6],
    });
    expect(readGamepadSticks(gp)).toEqual({
      lx: 0.1,
      ly: -0.2,
      rx: 0.5,
      ry: -0.6,
    });
  });

  it("fuses Legion dual half-pads into left+right sticks", () => {
    const left = fakePad({ id: "Legion Left", index: 0, mapping: "", axes: [0.2, -0.3] });
    const right = fakePad({ id: "Legion Right", index: 1, mapping: "", axes: [-0.4, 0.5] });
    expect(readDualHalfPadSticks([left, right])).toMatchObject({
      lx: 0.2,
      ly: -0.3,
      rx: -0.4,
      ry: 0.5,
    });
  });

  it("uses dual half-pads when no full 4-axis pad is present", () => {
    const left = fakePad({ id: "Legion Left", index: 0, mapping: "", axes: [0.2, -0.3], buttons: Array(12).fill(false) });
    const right = fakePad({ id: "Legion Right", index: 1, mapping: "", axes: [-0.4, 0.5], buttons: Array(12).fill(false) });
    vi.stubGlobal("navigator", {
      getGamepads: () => [left, right],
    });
    const state = readActiveGamepadState();
    expect(state?.sticks).toEqual({ lx: 0.2, ly: -0.3, rx: -0.4, ry: 0.5 });
    expect(state?.buttonPads).toHaveLength(2);
  });

  it("detects a held button on either half-pad", () => {
    const left = fakePad({ buttons: Array(12).fill(false) });
    const rightButtons = Array(12).fill(false);
    rightButtons[3] = true;
    const right = fakePad({ buttons: rightButtons });
    expect(anyPadButtonHeld([left, right], 3)).toBe(true);
    expect(anyPadButtonHeld([left, right], 6)).toBe(false);
  });
});
