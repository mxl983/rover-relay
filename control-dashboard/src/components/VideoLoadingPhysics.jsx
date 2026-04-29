import { useEffect, useRef } from "react";

const GROUND = 0x1f5538;

/**
 * Stage sorts direct children only (Pixi: higher zIndex = drawn later = on top).
 * Far range → dense mid (behind rover) → rover + ground shadow → dense near hills in front.
 *
 * Vertical depth (1–10): z=10 = horizon (slabTopY); smaller z = lower on screen (into the green band).
 * Rover wheel contact uses Z_DEPTH_ROVER (1–10 scale), not the Pixi stage z-index `Z_ROVER`.
 */
const Z_STARS = -10;
const Z_GROUND = 0;
const Z_GROUND_RIVERS = 0.5;
const Z_MOUNTAINS_FAR_GROUP = 2;
/** Side-framing peaks behind the rover (center of strip left open). */
const Z_MOUNTAINS_MID_GROUP = 4;
/** Contact shadow on the ground, under the rover sprite (legs/chassis would hide in-rover draws). */
const Z_ROVER_GROUND_SHADOW = 4.5;
/** Pixi stage order only. */
const Z_ROVER = 5;
/** Foreground mountain stack (in front of rover). */
const Z_MOUNTAINS_NEAR_GROUP = 50;
const Z_DEPTH_HORIZON = 10;
const Z_DEPTH_MIN = 1;
/** Logical depth for rover wheel line (see wheelContactYFromDepthZ). */
const Z_DEPTH_ROVER = 3;
const Z_DEPTH_RIVER_MIN = 4;
const Z_DEPTH_RIVER_MAX = 10;

/** World Y for wheel / ground contact at logical depth z (z=10 horizon, z=1 bottom of band). */
function wheelContactYFromDepthZ(z, slabTopY, groundBand) {
  const span = Z_DEPTH_HORIZON - Z_DEPTH_MIN;
  const t = span > 0 ? (Z_DEPTH_HORIZON - z) / span : 0;
  return slabTopY + groundBand * t;
}

/**
 * baseFrac = how far below horizon into the ground band (0 = furthest). scroll = parallax.
 * peaks: [cx·period, w·period, h·(hMax·hScale)] multipliers.
 * peakReachHMaxMul (optional): peak is forced at least up to slabTopY − hMax·mul so low bases still overlap the rover.
 */
const MOUNTAIN_BEHIND = [
  {
    baseFrac: -0.08,
    scroll: 0.08,
    hScale: 0.5,
    color: 0x0f141b,
    alpha: 1,
    peaks: [
      [0.08, 0.24, 0.58],
      [0.34, 0.28, 0.52],
      [0.63, 0.26, 0.62],
      [0.9, 0.22, 0.55],
    ],
  },
  {
    baseFrac: -0.03,
    scroll: 0.14,
    hScale: 0.52,
    color: 0x111821,
    alpha: 1,
    peaks: [
      [0.12, 0.22, 0.56],
      [0.39, 0.26, 0.64],
      [0.69, 0.24, 0.52],
      [0.93, 0.2, 0.5],
    ],
  },
  {
    baseFrac: 0.02,
    scroll: 0.2,
    hScale: 0.53,
    color: 0x141d27,
    alpha: 1,
    peaks: [
      [0.1, 0.2, 0.48],
      [0.34, 0.28, 0.72],
      [0.62, 0.22, 0.56],
      [0.86, 0.24, 0.62],
    ],
  },
  {
    baseFrac: 0.07,
    scroll: 0.28,
    hScale: 0.54,
    color: 0x18222c,
    alpha: 1,
    peaks: [
      [0.14, 0.22, 0.6],
      [0.44, 0.24, 0.54],
      [0.72, 0.3, 0.76],
      [0.95, 0.2, 0.5],
    ],
  },
];

/**
 * Mid distance: lower on screen (higher baseFrac), squat silhouettes, tightly packed peaks.
 * Opaque fills (alpha 1).
 */
const MOUNTAIN_MID = [
  {
    baseFrac: 0.38,
    scroll: 0.4,
    hScale: 0.25,
    peakReachHMaxMul: 0.62,
    color: 0x1e2a34,
    alpha: 1,
    peaks: [
      [0.05, 0.095, 0.3],
      [0.12, 0.088, 0.26],
      [0.2, 0.102, 0.33],
      [0.3, 0.09, 0.28],
      [0.4, 0.096, 0.31],
      [0.5, 0.092, 0.27],
      [0.6, 0.1, 0.32],
      [0.71, 0.088, 0.28],
      [0.82, 0.095, 0.3],
      [0.91, 0.09, 0.27],
    ],
  },
  {
    baseFrac: 0.44,
    scroll: 0.34,
    hScale: 0.22,
    peakReachHMaxMul: 0.56,
    color: 0x243038,
    alpha: 1,
    peaks: [
      [0.08, 0.11, 0.29],
      [0.22, 0.1, 0.27],
      [0.36, 0.105, 0.31],
      [0.52, 0.098, 0.28],
      [0.68, 0.104, 0.3],
      [0.84, 0.1, 0.28],
    ],
  },
];

/**
 * Foreground: bases must sit near the rover ground line (same depth band as Z_DEPTH_ROVER), or triangles
 * only occupy the upper green band and never overlap the rover — zIndex cannot fix that.
 * baseFrac = (Z_DEPTH_HORIZON − Z_DEPTH_ROVER) / (Z_DEPTH_HORIZON − Z_DEPTH_MIN) = 7/9.
 */
const ROVER_GROUND_BASE_FRAC =
  (Z_DEPTH_HORIZON - Z_DEPTH_ROVER) / (Z_DEPTH_HORIZON - Z_DEPTH_MIN);

const MOUNTAIN_NEAR = [
  {
    baseFrac: ROVER_GROUND_BASE_FRAC,
    scroll: 1.02,
    hScale: 0.38,
    peakReachHMaxMul: 1.08,
    color: 0x5f7d93,
    alpha: 1,
    peaks: [
      [0.03, 0.052, 0.42],
      [0.16, 0.046, 0.47],
      [0.29, 0.05, 0.44],
      [0.71, 0.05, 0.44],
      [0.84, 0.046, 0.47],
      [0.97, 0.052, 0.42],
    ],
  },
  {
    baseFrac: ROVER_GROUND_BASE_FRAC + 0.035,
    scroll: 0.9,
    hScale: 0.34,
    peakReachHMaxMul: 0.95,
    color: 0x6f8ea5,
    alpha: 1,
    peaks: [
      [0.08, 0.055, 0.35],
      [0.24, 0.05, 0.33],
      [0.76, 0.05, 0.33],
      [0.92, 0.055, 0.35],
    ],
  },
];

/** Side-view design ratios: length : body height : wheel diameter : mast+cam height. */
const ROVER = { L: 20, H: 7, WHEEL: 8, CAM: 10 };

function paintGround(g, width, slabTopY, screenH) {
  g.clear();
  const slabBottom = screenH + 80;
  g.rect(0, slabTopY, width, slabBottom - slabTopY).fill({ color: GROUND });
}

/** Curved rivers on the ground plane; painted over one tile period and wrapped in X. */
function paintGroundRivers(g, period, slabTopY, screenH) {
  g.clear();
  const groundBand = Math.max(1, screenH - slabTopY);
  const yTop = wheelContactYFromDepthZ(Z_DEPTH_RIVER_MAX, slabTopY, groundBand);
  const yBottom = wheelContactYFromDepthZ(Z_DEPTH_RIVER_MIN, slabTopY, groundBand);
  const yA = yTop + (yBottom - yTop) * 0.58;

  const drawRiver = (baseY, ampMul, freq, phase, outerW, innerW, outerColor, innerColor) => {
    const samples = 28;
    for (let i = 0; i <= samples; i += 1) {
      const t = i / samples;
      const x = -26 + (period + 52) * t;
      const yRaw =
        baseY +
        Math.sin(t * Math.PI * (freq + 0.65) + phase) * groundBand * ampMul +
        Math.sin(t * Math.PI * (freq * 0.47) + phase * 0.7) * groundBand * (ampMul * 0.6);
      const y = Math.max(yTop + 2, Math.min(yBottom - 2, yRaw));
      if (i === 0) g.moveTo(x, y);
      else g.lineTo(x, y);
    }
    g.stroke({ width: outerW, color: outerColor, alpha: 0.95 });
    for (let i = 0; i <= samples; i += 1) {
      const t = i / samples;
      const x = -26 + (period + 52) * t;
      const yRaw =
        baseY +
        Math.sin(t * Math.PI * (freq + 0.65) + phase) * groundBand * ampMul +
        Math.sin(t * Math.PI * (freq * 0.47) + phase * 0.7) * groundBand * (ampMul * 0.6);
      const y = Math.max(yTop + 2, Math.min(yBottom - 2, yRaw));
      if (i === 0) g.moveTo(x, y);
      else g.lineTo(x, y);
    }
    g.stroke({ width: innerW, color: innerColor, alpha: 0.86 });
  };

  drawRiver(yA, 0.048, 1.6, 0.9, 18, 11, 0x1c6491, 0x67c6f2);
}

/** Flat base at `b`, peak at (cx, b−h); vary width/height for different silhouettes. */
function polyTri(g, b, cx, w, h, fill) {
  const x0 = cx - w * 0.5;
  const x1 = cx + w * 0.5;
  g.poly([x0, b, cx, b - h, x1, b], true).fill(fill);
}

/** Wide, very flat strip at the peak’s base (reads as ground contact, not a round blob). */
function mountainPeakGroundShadow(g, b, cx, baseW, layerAlpha) {
  const w = Math.max(14, baseW * 0.86);
  const h = 2.4;
  const x = cx - w * 0.5;
  const y = b + 1.2;
  g.roundRect(x, y, w, h, h * 0.55).fill({
    color: 0x030508,
    alpha: layerAlpha * 0.26,
  });
}

function paintMountainLayer(g, period, slabTopY, screenH, hMax, def) {
  g.clear();
  const groundBand = Math.max(1, screenH - slabTopY);
  const b = slabTopY + groundBand * def.baseFrac;
  const H = hMax * def.hScale;
  const f = { color: def.color, alpha: def.alpha };
  const minPeakY =
    typeof def.peakReachHMaxMul === "number" ? slabTopY - hMax * def.peakReachHMaxMul : null;
  for (let i = 0; i < def.peaks.length; i += 1) {
    const [cxm, wm, hm] = def.peaks[i];
    let triH = H * hm;
    if (minPeakY !== null) triH = Math.max(triH, b - minPeakY);
    const cx = period * cxm;
    const baseW = period * wm;
    mountainPeakGroundShadow(g, b, cx, baseW, def.alpha);
    polyTri(g, b, cx, baseW, triH, f);
  }
}

function starHash(i, salt) {
  const x = Math.sin(i * 12.9898 + salt * 78.233) * 43758.5453;
  return x - Math.floor(x);
}

/** Few stars, irregular positions (no modulo grid lines). */
const STAR_COUNT = 20;

function drawStars(graphics, width, height, timeSec) {
  graphics.clear();
  const skyH = height * 0.66;
  for (let i = 0; i < STAR_COUNT; i += 1) {
    const x = starHash(i * 17 + 3, 1) * width;
    const y = starHash(i * 41 + 11, 2) * skyH;
    const rate = 1.1 + starHash(i, 4) * 5.5;
    const phase = starHash(i, 5) * Math.PI * 2;
    const wobble = Math.sin(timeSec * (2.4 + starHash(i, 6) * 3) + i * 0.91);
    const blink = 0.5 + 0.5 * Math.sin(timeSec * rate + phase + wobble * 0.35);
    const alpha = 0.04 + blink * 0.92;
    const rad = 0.35 + starHash(i, 7) * 0.55 + blink * 1.35;
    graphics.circle(x, y, rad).fill({ color: 0xe8f4ff, alpha });
  }
}

/** Rubber tire, red hub, spokes (rotates for spin). */
function drawWheel(g, radius) {
  g.clear();
  g.circle(0, 0, radius).fill({ color: 0x14161c }).stroke({ width: 2.2, color: 0x2d333f });
  g.circle(0, 0, radius * 0.72).fill({ color: 0x252a33 }).stroke({ width: 1, color: 0x1a1d22 });
  g.circle(0, 0, radius * 0.32).fill({ color: 0xc42a2a });
  g.circle(0, 0, radius * 0.14).fill({ color: 0x4a1515 });
  for (let i = 0; i < 6; i += 1) {
    const a = (i / 6) * Math.PI * 2;
    g.moveTo(Math.cos(a) * radius * 0.34, Math.sin(a) * radius * 0.34)
      .lineTo(Math.cos(a) * radius * 0.92, Math.sin(a) * radius * 0.92)
      .stroke({ width: 1.4, color: 0x0d0f14, alpha: 0.9 });
  }
}

/** Pixels per ratio unit; `bodyLenPx` is on-screen length for ratio ROVER.L. */
function sideBodyMetrics(bodyLenPx) {
  const u = bodyLenPx / ROVER.L;
  const len = ROVER.L * u;
  const hL = ROVER.H * u;
  const x0 = -len / 2;
  const y0 = -hL;
  const wL = len;
  const inset = wL * 0.065;
  const rearAxle = x0 + inset;
  const frontAxle = x0 + wL - inset;
  const midAxle = (rearAxle + frontAxle) / 2;
  const halfWheelbase = (frontAxle - rearAxle) / 2;
  const camPx = ROVER.CAM * u;
  return {
    u,
    len,
    x0,
    y0,
    wL,
    hL,
    bodyBottom: y0 + hL,
    midAxle,
    halfWheelbase,
    rearAxle,
    frontAxle,
    camPx,
  };
}

/** Struts from deck sill down to each axle (rover space; wheels at ±halfWheelbase). */
function drawLegs(g, bodyLenPx, chassisY) {
  g.clear();
  const STRUT = 0x3a414c;
  const m = sideBodyMetrics(bodyLenPx);
  const sx = (x) => x - m.midAxle;
  const yTop = chassisY + m.bodyBottom;
  const yBot = 0;
  const wb = m.halfWheelbase;
  g.poly(
    [
      sx(m.x0 + m.wL * 0.1),
      yTop,
      sx(m.x0 + m.wL * 0.02),
      yTop,
      -wb + 5,
      yBot,
      -wb - 6,
      yBot,
    ],
    true,
  ).fill({ color: STRUT });
  g.poly(
    [
      sx(m.x0 + m.wL * 0.76),
      yTop,
      sx(m.x0 + m.wL * 0.95),
      yTop,
      wb + 6,
      yBot,
      wb - 5,
      yBot,
    ],
    true,
  ).fill({ color: STRUT });
  g.rect(sx(m.x0 + m.wL * 0.04), yTop - 2, m.wL * 0.9, 3).fill({ color: 0x2f3540 });
}

/** Side view +X forward. Mast + camera vertical stack height = ROVER.CAM in ratio units. */
function drawChassis(g, bodyLenPx) {
  g.clear();
  const m = sideBodyMetrics(bodyLenPx);
  const sx = (x) => x - m.midAxle;
  const { u, camPx } = m;
  const rBody = Math.max(2, bodyLenPx * 0.07);
  g.roundRect(sx(m.x0), m.y0, m.wL, m.hL, rBody)
    .fill({ color: 0xeef4f8 })
    .stroke({ width: 1.1, color: 0xb0c4d4 });

  const wx = sx(m.x0 + m.wL * 0.22);
  const wy = m.y0 + m.hL * 0.22;
  const ww = m.wL * 0.28;
  const wh = m.hL * 0.42;
  g.roundRect(wx, wy, ww, wh, 3).fill({ color: 0x151820 });
  g.roundRect(wx + ww * 0.12, wy + wh * 0.18, ww * 0.35, wh * 0.22, 1.5).fill({ color: 0x00c8ff, alpha: 0.22 });

  const spx = sx(m.x0 + m.wL * 0.06);
  const sw = m.wL * 0.12;
  const spy = m.y0 + m.hL * 0.2;
  const sh = m.hL * 0.48;
  g.roundRect(spx, spy, sw, sh, 2).fill({ color: 0x3a4a6e, alpha: 0.94 });

  const mastFootX = sx(m.x0 + m.wL * 0.76);
  const mastW = Math.max(2.8, 1.7 * u);
  const roofY = m.y0 + m.hL * 0.06;
  const mastH = camPx * 0.57;
  const camBoxH = camPx * 0.43;
  const mastTopY = roofY - mastH;
  const camTopY = mastTopY - camBoxH;
  g.rect(mastFootX - mastW * 0.5, mastTopY, mastW, roofY - mastTopY).fill({ color: 0x2a3038 }).stroke({
    width: 1,
    color: 0x1a1e24,
  });

  const camW = bodyLenPx * 0.32;
  const camX = mastFootX - camW * 0.5;
  g.roundRect(camX, camTopY, camW, camBoxH, 3).fill({ color: 0x242830 }).stroke({ width: 1, color: 0x3d4654 });
  const lensCx = mastFootX;
  const lensCy = camTopY + camBoxH * 0.38;
  const lensR = Math.max(2.2, 1.9 * u);
  g.circle(lensCx, lensCy, lensR).fill({ color: 0x1e2528 }).stroke({ width: 1, color: 0x333b44 });
  g.circle(lensCx, lensCy, lensR * 0.52).fill({ color: 0x2a9c4a });

  g.circle(sx(m.x0 + m.wL * 0.14), m.y0 + m.hL * 0.12, Math.max(1.8, u * 1.1)).fill({ color: 0xff9a3c });

  const ax = sx(m.x0 + m.wL * 0.08);
  g.moveTo(ax, m.y0 + m.hL * 0.08).lineTo(ax, m.y0 - 2.4 * u).stroke({ width: 1.6, color: 0x1c2026 });
  g.moveTo(ax, m.y0 - 3.2 * u).lineTo(ax - 1.2 * u, m.y0 - 4.4 * u).stroke({ width: 1, color: 0x00c8ff, alpha: 0.45 });
  g.moveTo(ax, m.y0 - 4 * u).lineTo(ax - 1.6 * u, m.y0 - 5.6 * u).stroke({ width: 1, color: 0x00c8ff, alpha: 0.3 });

  const bumperY = m.bodyBottom - 1.4 * u;
  g.circle(sx(m.x0 + m.wL * 0.96), bumperY, 1.4 * u).fill({ color: 0x20a8ff }).stroke({ width: 0.9, color: 0x88ddff });

  g.moveTo(sx(m.x0 + m.wL * 0.02), m.bodyBottom).lineTo(sx(m.x0 + m.wL * 0.94), m.bodyBottom).stroke({
    width: 1,
    color: 0xc8d8e8,
    alpha: 0.55,
  });
}

/**
 * Rover ground shadow in **stage/world** space (wheels at roverY; axle line).
 * Must not live under the rover Container or legs/chassis cover it entirely.
 */
function drawRoverGroundShadowWorld(g, roverX, roverY, bodyLenPx, chassisY, wheelRadius) {
  g.clear();
  const m = sideBodyMetrics(bodyLenPx);
  const wb = m.halfWheelbase * 0.9;
  const shadowY = roverY + wheelRadius * 0.44;
  // Abstract soft contact: one broad smear plus two denser wheel lobes.
  g.ellipse(roverX, shadowY, m.wL * 0.56, Math.max(3.8, wheelRadius * 0.56)).fill({
    color: 0x070c0f,
    alpha: 0.26,
  });
  g.ellipse(roverX - wb, shadowY + 0.4, wheelRadius * 1.25, Math.max(2.4, wheelRadius * 0.42)).fill({
    color: 0x060a0d,
    alpha: 0.42,
  });
  g.ellipse(roverX + wb, shadowY + 0.4, wheelRadius * 1.25, Math.max(2.4, wheelRadius * 0.42)).fill({
    color: 0x060a0d,
    alpha: 0.42,
  });
  const deckBottomWorldY = roverY + chassisY + m.bodyBottom;
  const bodyW = m.wL * 0.48;
  const bodyH = Math.max(2.8, wheelRadius * 0.48);
  g.roundRect(roverX - bodyW * 0.5, deckBottomWorldY + wheelRadius * 0.14, bodyW, bodyH, bodyH * 0.5).fill({
    color: 0x0a1510,
    alpha: 0.3,
  });
}

/**
 * PixiJS: stars, flat ground, multi-layer triangle mountains (parallax + depth), rover.
 * Skipped in Vitest.
 */
export function VideoLoadingPhysics() {
  const hostRef = useRef(null);

  useEffect(() => {
    if (import.meta.env.MODE === "test") return undefined;

    const host = hostRef.current;
    if (!host) return undefined;

    let cancelled = false;
    let app = null;
    let resizeObserver = null;

    const boot = async () => {
      const { Application, Container, Graphics } = await import("pixi.js");
      if (cancelled) return;

      const measure = () => {
        const r = host.getBoundingClientRect();
        const width = Math.max(320, r.width || 640);
        const height = Math.max(240, r.height || 400);
        const groundBand = Math.max(110, height * 0.34);
        const slabTopY = height - groundBand;
        const period = Math.max(900, Math.ceil(width * 2.2));
        return { width, height, groundBand, slabTopY, period };
      };

      let v = measure();

      const nextApp = new Application();
      await nextApp.init({
        width: v.width,
        height: v.height,
        backgroundAlpha: 0,
        antialias: false,
        resolution: Math.min(window.devicePixelRatio || 1, 2),
        autoDensity: true,
      });
      if (cancelled) {
        nextApp.destroy(true);
        return;
      }

      app = nextApp;
      app.stage.sortableChildren = true;
      const canvas = app.canvas;
      canvas.style.cssText = "display:block;width:100%;height:100%;touch-action:none";
      host.appendChild(canvas);

      const starsG = new Graphics();
      starsG.zIndex = Z_STARS;
      app.stage.addChild(starsG);

      const groundG = new Graphics();
      groundG.zIndex = Z_GROUND;
      app.stage.addChild(groundG);

      const groundRivers = new Container();
      groundRivers.zIndex = Z_GROUND_RIVERS;
      const riverA = new Graphics();
      const riverB = new Graphics();
      groundRivers.addChild(riverA);
      groundRivers.addChild(riverB);
      app.stage.addChild(groundRivers);

      const mountainsFar = new Container();
      mountainsFar.sortableChildren = true;
      mountainsFar.zIndex = Z_MOUNTAINS_FAR_GROUP;
      app.stage.addChild(mountainsFar);

      const mountainsMid = new Container();
      mountainsMid.sortableChildren = true;
      mountainsMid.zIndex = Z_MOUNTAINS_MID_GROUP;
      app.stage.addChild(mountainsMid);

      const roverGroundShadowG = new Graphics();
      roverGroundShadowG.zIndex = Z_ROVER_GROUND_SHADOW;
      app.stage.addChild(roverGroundShadowG);

      const behindLayers = MOUNTAIN_BEHIND.map((def, i) => {
        const c = new Container();
        c.zIndex = i;
        const ga = new Graphics();
        const gb = new Graphics();
        c.addChild(ga);
        c.addChild(gb);
        mountainsFar.addChild(c);
        return { def, c, ga, gb };
      });

      const midLayers = MOUNTAIN_MID.map((def, i) => {
        const c = new Container();
        c.zIndex = i;
        const ga = new Graphics();
        const gb = new Graphics();
        c.addChild(ga);
        c.addChild(gb);
        mountainsMid.addChild(c);
        return { def, c, ga, gb };
      });

      const mountainsNear = new Container();
      mountainsNear.sortableChildren = true;
      mountainsNear.zIndex = Z_MOUNTAINS_NEAR_GROUP;

      const nearLayers = MOUNTAIN_NEAR.map((def, i) => {
        const c = new Container();
        c.zIndex = i;
        const ga = new Graphics();
        const gb = new Graphics();
        c.addChild(ga);
        c.addChild(gb);
        mountainsNear.addChild(c);
        return { def, c, ga, gb };
      });

      let scrollAccum = 0;
      const scrollSpeed = 2.65;

      const hMax = () => Math.max(44, Math.min(92, v.slabTopY * 0.28));

      const wrapScroll = (s, period) => {
        let x = s % period;
        if (x < 0) x += period;
        return x;
      };

      const paintStaticScene = () => {
        const hm = hMax();
        paintGround(groundG, v.width, v.slabTopY, v.height);
        paintGroundRivers(riverA, v.period, v.slabTopY, v.height);
        paintGroundRivers(riverB, v.period, v.slabTopY, v.height);
        for (let i = 0; i < behindLayers.length; i += 1) {
          const { def, ga, gb } = behindLayers[i];
          paintMountainLayer(ga, v.period, v.slabTopY, v.height, hm, def);
          paintMountainLayer(gb, v.period, v.slabTopY, v.height, hm, def);
        }
        for (let i = 0; i < midLayers.length; i += 1) {
          const { def, ga, gb } = midLayers[i];
          paintMountainLayer(ga, v.period, v.slabTopY, v.height, hm, def);
          paintMountainLayer(gb, v.period, v.slabTopY, v.height, hm, def);
        }
        for (let i = 0; i < nearLayers.length; i += 1) {
          const { def, ga, gb } = nearLayers[i];
          paintMountainLayer(ga, v.period, v.slabTopY, v.height, hm, def);
          paintMountainLayer(gb, v.period, v.slabTopY, v.height, hm, def);
        }
      };
      paintStaticScene();

      const syncLayerGroup = (layers) => {
        const p = v.period;
        for (let i = 0; i < layers.length; i += 1) {
          const { def, ga, gb } = layers[i];
          const s = wrapScroll(scrollAccum * def.scroll, p);
          ga.x = -s;
          gb.x = ga.x + p;
        }
      };

      const syncMountainScroll = () => {
        syncLayerGroup(behindLayers);
        syncLayerGroup(midLayers);
        syncLayerGroup(nearLayers);
      };
      syncMountainScroll();

      const syncGroundRivers = () => {
        const s = wrapScroll(scrollAccum * 0.5, v.period);
        riverA.x = -s;
        riverB.x = riverA.x + v.period;
      };
      syncGroundRivers();

      const rover = new Container();
      const legsG = new Graphics();
      rover.addChild(legsG);

      // Side view: wheelL = rear axle (−X), wheelR = front axle (+X), forward is to the right.
      const wheelL = new Container();
      const wheelLG = new Graphics();
      wheelL.addChild(wheelLG);

      const wheelR = new Container();
      const wheelRG = new Graphics();
      wheelR.addChild(wheelRG);

      const chassis = new Container();
      const chassisG = new Graphics();
      chassis.addChild(chassisG);

      rover.addChild(wheelL);
      rover.addChild(wheelR);
      rover.addChild(chassis);
      rover.zIndex = Z_ROVER;
      app.stage.addChild(mountainsNear);
      app.stage.addChild(rover);

      let wheelRadius = 9;
      const roverAnim = {
        baseX: 0,
        baseY: 0,
        bodyLenPx: 32,
        chassisY: -10,
      };

      const layoutRover = () => {
        const bodyLenPx = Math.max(24, Math.min(40, v.width * 0.065));
        const m = sideBodyMetrics(bodyLenPx);
        wheelRadius = (ROVER.WHEEL / 2) * m.u;
        const chassisY = -wheelRadius * 1.08;

        drawChassis(chassisG, bodyLenPx);
        drawLegs(legsG, bodyLenPx, chassisY);
        drawWheel(wheelLG, wheelRadius);
        drawWheel(wheelRG, wheelRadius);

        chassis.position.set(0, chassisY);
        wheelL.position.set(-m.halfWheelbase, 0);
        wheelR.position.set(m.halfWheelbase, 0);

        roverAnim.bodyLenPx = bodyLenPx;
        roverAnim.chassisY = chassisY;
        roverAnim.baseX = v.width * 0.5;
        const contactY = wheelContactYFromDepthZ(Z_DEPTH_ROVER, v.slabTopY, v.groundBand);
        roverAnim.baseY = contactY - wheelRadius;
        rover.position.set(roverAnim.baseX, roverAnim.baseY);
        drawRoverGroundShadowWorld(
          roverGroundShadowG,
          roverAnim.baseX,
          roverAnim.baseY,
          bodyLenPx,
          chassisY,
          wheelRadius,
        );
      };
      layoutRover();

      app.ticker.add((ticker) => {
        const dt = ticker.deltaTime;
        scrollAccum += scrollSpeed * dt;

        syncGroundRivers();
        syncMountainScroll();

        const tSec = performance.now() * 0.001;
        const ay = Math.max(0.38, wheelRadius * 0.048);
        const vy =
          Math.sin(tSec * 9.1 + 2.2) * ay * 0.92 + Math.sin(tSec * 18.7) * ay * 0.36;
        const rx = roverAnim.baseX;
        const ry = roverAnim.baseY + vy;
        rover.position.set(rx, ry);
        drawRoverGroundShadowWorld(
          roverGroundShadowG,
          rx,
          ry,
          roverAnim.bodyLenPx,
          roverAnim.chassisY,
          wheelRadius,
        );

        const spin = (scrollSpeed / wheelRadius) * 0.9;
        wheelL.rotation += spin * dt;
        wheelR.rotation += spin * dt;

        drawStars(starsG, v.width, v.height, performance.now() * 0.001);
      });

      const relayout = () => {
        if (!app || cancelled) return;
        v = measure();
        app.renderer.resize(v.width, v.height);
        paintStaticScene();
        syncGroundRivers();
        syncMountainScroll();
        layoutRover();
        drawStars(starsG, v.width, v.height, performance.now() * 0.001);
      };

      resizeObserver = new ResizeObserver(() => relayout());
      resizeObserver.observe(host);
    };

    void boot();

    return () => {
      cancelled = true;
      resizeObserver?.disconnect();
      resizeObserver = null;
      if (app) {
        try {
          app.destroy(true, { children: true, texture: true });
        } catch {
          /* ignore */
        }
        app = null;
      }
      while (host.firstChild) {
        host.removeChild(host.firstChild);
      }
    };
  }, []);

  return (
    <div
      ref={hostRef}
      style={{
        position: "absolute",
        inset: 0,
        width: "100%",
        height: "100%",
        overflow: "hidden",
        pointerEvents: "none",
      }}
    />
  );
}
