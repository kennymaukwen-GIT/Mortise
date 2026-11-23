import type { PageGeometry, Quad } from "../api/types";
import {
  checkQuad,
  clampToFrame,
  cornersCorrection,
  frameFor,
  fullImageQuad,
  initialChoice,
  isClockwiseConvex,
  isFullImage,
  magnifierPlace,
  nudge,
  orderQuad,
  quadOf,
  roundQuad,
  screenToSource,
  sourcePerScreenPx,
  sourceToScreen,
  toPercent,
} from "./quad";

const W = 1000;
const H = 1100;
const Q: Quad = [
  [180, 160],
  [820, 140],
  [860, 1300],
  [150, 1320],
]; // bottom corners below the photo (paper cut off)

function geo(over: Partial<PageGeometry> = {}): PageGeometry {
  return {
    page: 1,
    input_type: "photo",
    source: { width: W, height: H, image: "/api/jobs/j/pages/1/image?kind=source" },
    corners: Q,
    method: "edge_fit",
    confidence: 0.6,
    status: "cut_off",
    corner_error_px: [2, 2, 9, 9],
    needs_review: true,
    review_reason: "紙的下緣超出畫面。",
    alternatives: [
      {
        corners: [
          [180, 160],
          [820, 140],
          [840, 1100],
          [165, 1100],
        ],
        score: 0.8,
        label: "三條邊＋畫面邊",
      },
    ],
    preselect: 0,
    outline: null,
    dewarp: { applied: false, source: null, residual_px: null, needs_review: false },
    glare: null,
    user_set: false,
    notes: [],
    ...over,
  };
}

describe("page corners: screen ↔ source pixels", () => {
  const frame = { x: -50, y: -50, w: 1100, h: 1420 };
  // The frame drawn 550 × 710 CSS px at (100, 40) on the screen: half size.
  const rect = { left: 100, top: 40, width: 550, height: 710 };

  it("maps source pixels to the screen and back exactly", () => {
    for (const p of [...Q, [0, 0], [W, H], [-50, -50]] as [number, number][]) {
      const s = sourceToScreen(p, frame, rect);
      const back = screenToSource(s[0], s[1], frame, rect);
      expect(back[0]).toBeCloseTo(p[0], 9);
      expect(back[1]).toBeCloseTo(p[1], 9);
    }
  });

  it("puts the frame's corners on the rectangle's corners", () => {
    expect(sourceToScreen([-50, -50], frame, rect)).toEqual([100, 40]);
    expect(sourceToScreen([1050, 1370], frame, rect)).toEqual([650, 750]);
    // The photo's top-left (0,0) is 50 source px = 25 CSS px inside.
    expect(sourceToScreen([0, 0], frame, rect)).toEqual([125, 65]);
  });

  it("follows zoom: the same source point under a doubled rectangle", () => {
    const zoomed = { left: 100 - 275, top: 40 - 355, width: 1100, height: 1420 }; // zoom 2 around the centre
    const s = sourceToScreen([500, 600], frame, zoomed);
    expect(screenToSource(s[0], s[1], frame, zoomed)).toEqual([500, 600]);
    expect(sourcePerScreenPx(frame, zoomed)).toBeCloseTo(1, 9);
    expect(sourcePerScreenPx(frame, rect)).toBeCloseTo(2, 9);
  });

  it("gives CSS percentages inside the frame", () => {
    expect(toPercent([-50, -50], frame)).toEqual({ left: 0, top: 0 });
    expect(toPercent([500, 660], frame)).toEqual({ left: 50, top: 50 });
  });
});

describe("frameFor", () => {
  it("covers the photo plus a margin", () => {
    const f = frameFor(W, H, [fullImageQuad(W, H)], 0.05);
    expect(f).toEqual({ x: -55, y: -55, w: 1110, h: 1210 });
  });

  it("grows to show corners outside the photo", () => {
    const f = frameFor(W, H, [Q], 0);
    expect(f.y + f.h).toBe(1320);
    expect(f.x).toBe(0);
  });

  it("ignores absurd points", () => {
    const f = frameFor(W, H, [[[1e9, 5]]], 0);
    expect(f).toEqual({ x: 0, y: 0, w: W, h: H });
  });
});

describe("moving corners", () => {
  const frame = { x: -10, y: -10, w: 1020, h: 1120 };
  it("nudges one corner and keeps it in the frame", () => {
    const q = fullImageQuad(W, H);
    const moved = nudge(q, 2, 5, -3, frame);
    expect(moved[2]).toEqual([1005, 1097]);
    expect(moved[0]).toEqual([0, 0]);
    expect(q[2]).toEqual([W, H]); // not changed in place
    expect(nudge(q, 0, -100, -100, frame)[0]).toEqual([-10, -10]);
    expect(clampToFrame([2000, -50], frame)).toEqual([1010, -10]);
  });
});

describe("checking the corners before sending", () => {
  it("accepts a clockwise convex outline as it is (the start corner may turn the page)", () => {
    const turned: Quad = [Q[1], Q[2], Q[3], Q[0]];
    expect(isClockwiseConvex(turned)).toBe(true);
    const r = checkQuad(turned, W, H);
    expect(r.ok && r.quad).toEqual(turned);
  });

  it("re-orders points given counter-clockwise or shuffled", () => {
    const ccw: Quad = [Q[0], Q[3], Q[2], Q[1]];
    const r = checkQuad(ccw, W, H);
    expect(r.ok && r.quad).toEqual(Q);
    expect(orderQuad([Q[2], Q[0], Q[3], Q[1]])).toEqual(Q);
  });

  it("refuses crossing sides only when no order makes a shape, and tiny shapes", () => {
    // Two corners dragged onto each other: three distinct points.
    const flat: Quad = [
      [0, 0],
      [100, 0],
      [100, 0],
      [0, 100],
    ];
    expect(checkQuad(flat, W, H)).toEqual({ ok: false, reason: "shape" });
    const almost: Quad = [
      [820.0001, 140],
      [820, 140],
      [860, 1300],
      [150, 1320],
    ];
    expect(checkQuad(almost, W, H)).toEqual({ ok: false, reason: "shape" });
    const tiny: Quad = [
      [0, 0],
      [40, 0],
      [40, 40],
      [0, 40],
    ];
    expect(checkQuad(tiny, W, H)).toEqual({ ok: false, reason: "small" });
  });

  it("builds the corners correction in source pixels (rounded to 0.1 px)", () => {
    const q: Quad = [
      [10.04, 20.06],
      [900.55, 19.94],
      [910, 1000.123],
      [5, 990],
    ];
    expect(roundQuad(q)[0]).toEqual([10, 20.1]);
    expect(cornersCorrection(3, q)).toEqual([
      {
        field: "corners",
        page: 3,
        value: [
          [10, 20.1],
          [900.6, 19.9],
          [910, 1000.1],
          [5, 990],
        ],
      },
    ]);
  });

  it("whole image = the photo's four corners", () => {
    const full = fullImageQuad(W, H);
    expect(full).toEqual([
      [0, 0],
      [W, 0],
      [W, H],
      [0, H],
    ]);
    expect(isFullImage(full, W, H)).toBe(true);
    expect(isFullImage(Q, W, H)).toBe(false);
    expect(cornersCorrection(1, full)[0].value).toEqual([
      [0, 0],
      [1000, 0],
      [1000, 1100],
      [0, 1100],
    ]);
  });
});

describe("choices", () => {
  it("starts with the server's pre-selected candidate", () => {
    const g = geo();
    expect(initialChoice(g)).toEqual({ kind: "alt", index: 0 });
    expect(quadOf(initialChoice(g), g)).toEqual(g.alternatives[0].corners);
  });
  it("starts with the corners found when nothing is pre-selected", () => {
    const g = geo({ preselect: null });
    expect(initialChoice(g)).toEqual({ kind: "current" });
    expect(quadOf({ kind: "current" }, g)).toEqual(Q);
    expect(quadOf({ kind: "full" }, g)).toEqual(fullImageQuad(W, H));
    expect(quadOf({ kind: "alt", index: 5 }, g)).toBeNull();
    expect(initialChoice(geo({ preselect: 3 }))).toEqual({ kind: "current" }); // index out of range
  });
});

describe("magnifier placement", () => {
  it("sits above-left of the corner and flips near the edges", () => {
    const box = { width: 800, height: 600 };
    expect(magnifierPlace({ x: 400, y: 300 }, box, 100, 20)).toEqual({ x: 280, y: 180 });
    expect(magnifierPlace({ x: 30, y: 30 }, box, 100, 20)).toEqual({ x: 50, y: 50 });
    expect(magnifierPlace({ x: 790, y: 590 }, box, 100, 20)).toEqual({ x: 670, y: 470 });
  });
});
