/**
 * Xbox Gamepad API helpers (USB + Bluetooth).
 * Uses the Standard Gamepad mapping when the browser exposes it.
 */

const ACTIVITY_EPS = 0.02;

/**
 * @param {Gamepad | null | undefined} g
 */
export function scoreGamepad(g) {
  if (!g?.connected) return -Infinity;
  const id = String(g.id || "").toLowerCase();
  let score = 0;

  // Ghost / non-controller slots that Chrome sometimes exposes.
  if (/touch|mouse|keyboard|stylus|digitizer/.test(id)) score -= 200;

  if (g.mapping === "standard") score += 50;

  const axisCount = g.axes?.length ?? 0;
  if (axisCount >= 4) score += 30;
  else if (axisCount >= 2) score += 10;

  const buttonCount = g.buttons?.length ?? 0;
  if (buttonCount >= 12) score += 20;
  else if (buttonCount >= 8) score += 10;
  else if (buttonCount < 4) score -= 20;

  // Prefer real Xbox pads (BT + USB). Vendor 045e = Microsoft.
  if (/xbox|x-box|xinput|microsoft|\b045e\b/.test(id)) score += 40;

  // Prefer pads that currently show stick activity (helps skip idle ghost slots).
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
 * Pick the best connected Xbox / standard gamepad.
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
 * Xbox Standard Gamepad mapping (USB + Bluetooth when browser remaps):
 *   Left stick  axes[0], axes[1]  → drive
 *   Right stick axes[2], axes[3]  → gimbal
 *   buttons: A0 B1 X2 Y3 LB4 RB5 LT6 RT7 View8 Menu9 L3=10 R3=11
 *
 * Linux Bluetooth without "standard" mapping often puts triggers on axes 2–3
 * and the right stick on axes 4–5 — detect that without touching the left stick.
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

  // Non-standard BT / hid-generic: right stick may live on axes 4–5.
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
 * Active Xbox pad sticks + buttons for the control HUD.
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
  if (!best) return null;

  return {
    sticks: readGamepadSticks(best),
    buttonPads: [best],
    primary: best,
  };
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

/** Any connected pad shows button press, trigger pull, or stick deflection. */
export function anyGamepadPhysicalInput(threshold = 0.12) {
  for (const gp of listConnectedGamepads()) {
    for (const btn of gp.buttons ?? []) {
      if (btn?.pressed) return true;
      if ((btn?.value ?? 0) > threshold) return true;
    }
    for (const axis of gp.axes ?? []) {
      if (Math.abs(Number(axis) || 0) > threshold) return true;
    }
  }
  return false;
}
