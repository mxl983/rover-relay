/**
 * Cross-controller Gamepad API helpers.
 * Supports Xbox-standard pads and Legion Go / dual half-pad (D-input) layouts.
 */

const ACTIVITY_EPS = 0.02;

/**
 * @param {Gamepad | null | undefined} g
 */
export function scoreGamepad(g) {
  if (!g?.connected) return -Infinity;
  const id = String(g.id || "").toLowerCase();
  let score = 0;

  if (/touch|mouse|keyboard|stylus|digitizer/.test(id)) score -= 200;

  if (g.mapping === "standard") score += 50;

  const axisCount = g.axes?.length ?? 0;
  if (axisCount >= 4) score += 30;
  else if (axisCount >= 2) score += 10;

  const buttonCount = g.buttons?.length ?? 0;
  if (buttonCount >= 12) score += 20;
  else if (buttonCount >= 8) score += 10;
  else if (buttonCount < 4) score -= 20;

  if (/xbox|x-box|xinput|microsoft/.test(id)) score += 25;
  // Legion Go / Go S (vendor 17ef / 1a86, product e310, etc.)
  if (/legion|lenovo|go s|\b17ef\b|\be310\b|\b6182\b|\b1a86\b/.test(id)) {
    score += 45;
  }

  // Prefer pads that currently show stick activity (helps skip ghost slots).
  let activity = 0;
  for (const a of g.axes ?? []) {
    if (Number.isFinite(a)) activity += Math.abs(a);
  }
  score += Math.min(15, activity * 4);

  return score;
}

/**
 * @returns {Gamepad[]}
 */
export function listConnectedGamepads() {
  const pads = typeof navigator !== "undefined" ? navigator.getGamepads?.() : null;
  if (!pads) return [];
  const out = [];
  for (let i = 0; i < pads.length; i++) {
    const g = pads[i];
    if (g?.connected) out.push(g);
  }
  return out;
}

/**
 * Pick the best single full gamepad (Xbox / Legion XInput).
 * @param {Gamepad[]} [pads]
 * @returns {Gamepad | null}
 */
export function selectBestGamepad(pads = listConnectedGamepads()) {
  if (!pads.length) return null;
  let best = null;
  let bestScore = -Infinity;
  for (const g of pads) {
    const s = scoreGamepad(g);
    if (s > bestScore) {
      bestScore = s;
      best = g;
    }
  }
  return bestScore > -100 ? best : null;
}

/**
 * Xbox Standard Gamepad mapping (also Legion Go S in XInput mode):
 *   Left stick  axes[0], axes[1]  → drive
 *   Right stick axes[2], axes[3]  → gimbal
 *   buttons: A0 B1 X2 Y3 LB4 RB5 LT6 RT7 View8 Menu9 L3=10 R3=11
 *
 * Prefer `mapping === "standard"` pads and never swap sticks.
 * @param {Gamepad} gp
 * @returns {{ lx: number; ly: number; rx: number; ry: number }}
 */
export function readGamepadSticks(gp) {
  const a = gp.axes;
  if (!a?.length) {
    return { lx: 0, ly: 0, rx: 0, ry: 0 };
  }

  let lx = Number(a[0]) || 0;
  let ly = Number(a[1]) || 0;
  let rx = Number(a[2]) || 0;
  let ry = Number(a[3]) || 0;

  if (gp.mapping === "standard") {
    return { lx, ly, rx, ry };
  }

  // Firefox / some non-standard mappings expose the right stick on axes 4–5
  // when 2–3 are analog triggers (do not steal left-stick axes).
  if (a.length >= 6 && Math.abs(rx) < ACTIVITY_EPS && Math.abs(ry) < ACTIVITY_EPS) {
    const rx4 = Number(a[4]) || 0;
    const ry5 = Number(a[5]) || 0;
    if (Math.abs(rx4) > ACTIVITY_EPS || Math.abs(ry5) > ACTIVITY_EPS) {
      rx = rx4;
      ry = ry5;
    }
  }

  return { lx, ly, rx, ry };
}

/**
 * Legion Go detached / Dual D-input: two half-controllers, each with one stick on axes 0–1.
 * @param {Gamepad[]} pads
 * @returns {{ lx: number; ly: number; rx: number; ry: number; left: Gamepad; right: Gamepad } | null}
 */
export function readDualHalfPadSticks(pads) {
  const halves = pads
    .filter((g) => (g.axes?.length ?? 0) >= 2 && (g.axes?.length ?? 0) < 4)
    .sort((a, b) => a.index - b.index);
  if (halves.length < 2) return null;

  const left = halves[0];
  const right = halves[1];
  return {
    lx: Number(left.axes[0]) || 0,
    ly: Number(left.axes[1]) || 0,
    rx: Number(right.axes[0]) || 0,
    ry: Number(right.axes[1]) || 0,
    left,
    right,
  };
}

/**
 * Unified stick + button source for Xbox-standard and Legion dual-pad layouts.
 * @returns {{
 *   sticks: { lx: number; ly: number; rx: number; ry: number };
 *   buttonPads: Gamepad[];
 *   primary: Gamepad | null;
 * } | null}
 */
export function readActiveGamepadState() {
  const pads = listConnectedGamepads();
  if (!pads.length) return null;

  const best = selectBestGamepad(pads);
  const bestAxes = best?.axes?.length ?? 0;

  // Prefer a full 4-axis pad (XInput / standard).
  if (best && bestAxes >= 4) {
    return {
      sticks: readGamepadSticks(best),
      buttonPads: [best],
      primary: best,
    };
  }

  // Dual half-pads (Legion D-input / Joy-Con style).
  const dual = readDualHalfPadSticks(pads);
  if (dual) {
    return {
      sticks: { lx: dual.lx, ly: dual.ly, rx: dual.rx, ry: dual.ry },
      buttonPads: [dual.left, dual.right],
      primary: dual.left,
    };
  }

  if (best) {
    return {
      sticks: readGamepadSticks(best),
      buttonPads: [best],
      primary: best,
    };
  }

  return null;
}

/**
 * True if any listed pad has the button pressed / trigger pulled.
 * @param {Gamepad[]} pads
 * @param {number} index
 * @param {number} [triggerThreshold]
 */
export function anyPadButtonHeld(pads, index, triggerThreshold = 0.45) {
  for (const gp of pads) {
    const button = gp.buttons?.[index];
    if (!button) continue;
    if (button.pressed) return true;
    const v = typeof button.value === "number" ? button.value : 0;
    if (v >= triggerThreshold) return true;
  }
  return false;
}
