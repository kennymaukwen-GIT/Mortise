// Page corners in a photo: conversions between source-image pixels and the screen, and small checks.
// Corners are always kept in source-image pixels (what GET geometry gives and POST corrections takes);
// the screen only ever sees them through these functions.
import type { CorrectionIn, PageGeometry, Pt, Quad } from "../api/types";

/** Part of the source image shown in the editor, in source pixels (may reach outside the image). */
export interface Frame {
  x: number;
  y: number;
  w: number;
  h: number;
}

/** A box on the screen (CSS pixels), as getBoundingClientRect gives it. */
export interface ScreenRect {
  left: number;
  top: number;
  width: number;
  height: number;
}

/** Corner names in the order the API uses. */
export const CORNERS = ["tl", "tr", "br", "bl"] as const;

/** Source pixels → screen (CSS pixels). `rect` is where the frame is drawn on the screen. */
export function sourceToScreen(p: Pt, frame: Frame, rect: ScreenRect): Pt {
  return [rect.left + ((p[0] - frame.x) / frame.w) * rect.width, rect.top + ((p[1] - frame.y) / frame.h) * rect.height];
}

/** Screen (CSS pixels) → source pixels. */
export function screenToSource(clientX: number, clientY: number, frame: Frame, rect: ScreenRect): Pt {
  return [frame.x + ((clientX - rect.left) / Math.max(1e-9, rect.width)) * frame.w, frame.y + ((clientY - rect.top) / Math.max(1e-9, rect.height)) * frame.h];
}

/** Where a source point sits inside the frame, in percent (for CSS left/top). */
export function toPercent(p: Pt, frame: Frame): { left: number; top: number } {
  return { left: ((p[0] - frame.x) / frame.w) * 100, top: ((p[1] - frame.y) / frame.h) * 100 };
}

/** Source pixels per screen pixel along x (how far one arrow-key step moves a corner). */
export function sourcePerScreenPx(frame: Frame, rect: ScreenRect): number {
  return frame.w / Math.max(1e-9, rect.width);
}

/** Room kept around the photo on each side, in source pixels. */
export interface Margins {
  top: number;
  right: number;
  bottom: number;
  left: number;
}

/** The server refuses corners further out than this (s2_page: four times the photo's diagonal, on either axis). */
export function cornerLimit(width: number, height: number): number {
  return 4 * Math.hypot(width, height);
}

/**
 * Where a corner may be dragged: anywhere the server accepts, a little inside its limit. Paper cut off by the edge of
 * the photo has its real corner outside the photo, so corners are not kept inside what happens to be on screen.
 */
export function dragBounds(width: number, height: number): Frame {
  const l = 0.99 * cornerLimit(width, height);
  return { x: -l, y: -l, w: 2 * l, h: 2 * l };
}

/**
 * Room to leave around the photo on each side. A side that a candidate's corner (or the curved outline) touches or
 * goes past most likely cuts the paper off, so it gets a wide margin (a share of the photo's size across that side):
 * the real corner can then be dragged straight out to where the paper ends. Other sides get a narrow margin.
 * "The whole image" says nothing about the paper and is left out.
 */
export function marginsFor(width: number, height: number, quads: Pt[][], narrow = 0.05, wide = 0.3, touch = 0.015): Margins {
  const n = narrow * Math.max(width, height);
  const m: Margins = { top: n, right: n, bottom: n, left: n };
  const limit = cornerLimit(width, height);
  for (const q of quads) {
    if (q.length === 4 && isFullImage(q as Quad, width, height)) continue;
    for (const p of q) {
      if (!Number.isFinite(p[0]) || !Number.isFinite(p[1]) || Math.abs(p[0]) > limit || Math.abs(p[1]) > limit) continue;
      if (p[0] <= touch * width) m.left = Math.max(m.left, wide * width);
      if (p[0] >= width - touch * width) m.right = Math.max(m.right, wide * width);
      if (p[1] <= touch * height) m.top = Math.max(m.top, wide * height);
      if (p[1] >= height - touch * height) m.bottom = Math.max(m.bottom, wide * height);
    }
  }
  return m;
}

/**
 * The part of the photo to show: the whole image with `margin` around it (a share of the photo's longer side, or
 * per side in source pixels), grown to show any corner that lies further out, with room around that corner too
 * (the narrowest margin), so it can still be grabbed.
 */
export function frameFor(width: number, height: number, quads: Pt[][], margin: number | Margins = 0.05): Frame {
  const m: Margins = typeof margin === "number" ? { top: 0, right: 0, bottom: 0, left: 0 } : margin;
  if (typeof margin === "number") m.top = m.right = m.bottom = m.left = margin * Math.max(width, height);
  const pad = Math.min(m.top, m.right, m.bottom, m.left);
  let x0 = 0 - m.left; // (not -m.left: that gives -0)
  let y0 = 0 - m.top;
  let x1 = width + m.right;
  let y1 = height + m.bottom;
  const limit = cornerLimit(width, height); // the server refuses corners further out than this
  for (const q of quads)
    for (const p of q) {
      if (!Number.isFinite(p[0]) || !Number.isFinite(p[1]) || Math.abs(p[0]) > limit || Math.abs(p[1]) > limit) continue;
      x0 = Math.min(x0, p[0] - pad);
      y0 = Math.min(y0, p[1] - pad);
      x1 = Math.max(x1, p[0] + pad);
      y1 = Math.max(y1, p[1] + pad);
    }
  return { x: x0, y: y0, w: x1 - x0, h: y1 - y0 };
}

export function clampToFrame(p: Pt, frame: Frame): Pt {
  return [Math.min(frame.x + frame.w, Math.max(frame.x, p[0])), Math.min(frame.y + frame.h, Math.max(frame.y, p[1]))];
}

/** Move corner `i` by (dx, dy) source pixels, staying inside `bounds` (see dragBounds). */
export function nudge(q: Quad, i: number, dx: number, dy: number, bounds: Frame): Quad {
  const out = q.map((p) => [p[0], p[1]] as Pt) as Quad;
  out[i] = clampToFrame([q[i][0] + dx, q[i][1] + dy], bounds);
  return out;
}

/** Put corner `i` at `p` (source pixels), staying inside `bounds` (see dragBounds). */
export function setCorner(q: Quad, i: number, p: Pt, bounds: Frame): Quad {
  const out = q.map((c) => [c[0], c[1]] as Pt) as Quad;
  out[i] = clampToFrame(p, bounds);
  return out;
}

/** The whole image as the page: its four corners. */
export function fullImageQuad(width: number, height: number): Quad {
  return [
    [0, 0],
    [width, 0],
    [width, height],
    [0, height],
  ];
}

function cross(o: Pt, a: Pt, b: Pt): number {
  return (a[0] - o[0]) * (b[1] - o[1]) - (a[1] - o[1]) * (b[0] - o[0]);
}

/** In order, a convex four-sided shape turning clockwise on the screen (y points down). */
export function isClockwiseConvex(q: Quad): boolean {
  for (let i = 0; i < 4; i++) if (!(cross(q[i], q[(i + 1) % 4], q[(i + 2) % 4]) > 0)) return false;
  return true;
}

export function area(q: Pt[]): number {
  let s = 0;
  for (let i = 0; i < q.length; i++) {
    const a = q[i];
    const b = q[(i + 1) % q.length];
    s += a[0] * b[1] - b[0] * a[1];
  }
  return Math.abs(s) / 2;
}

/** Put four points in the order top-left, top-right, bottom-right, bottom-left (clockwise, starting nearest the top-left). */
export function orderQuad(q: Pt[]): Quad {
  const cx = q.reduce((s, p) => s + p[0], 0) / q.length;
  const cy = q.reduce((s, p) => s + p[1], 0) / q.length;
  const byAngle = [...q].sort((a, b) => Math.atan2(a[1] - cy, a[0] - cx) - Math.atan2(b[1] - cy, b[0] - cx)); // clockwise on screen
  let start = 0;
  let best = Infinity;
  byAngle.forEach((p, i) => {
    const d = p[0] + p[1];
    if (d < best) {
      best = d;
      start = i;
    }
  });
  return [0, 1, 2, 3].map((k) => byAngle[(start + k) % 4]) as Quad;
}

export type QuadCheck = { ok: true; quad: Quad } | { ok: false; reason: "shape" | "small" };

/**
 * The corners to send: kept as they are when they already go clockwise (the user may have chosen which corner is the
 * top-left, to turn the page); otherwise put in order. Crossing sides or a tiny shape are refused, as the server would.
 */
export function checkQuad(q: Quad, width: number, height: number): QuadCheck {
  let out = q;
  if (!isClockwiseConvex(out)) {
    out = orderQuad(q);
    if (!isClockwiseConvex(out)) return { ok: false, reason: "shape" };
  }
  // Two corners dropped (almost) on top of each other make a triangle, not a page.
  const minSide = 0.01 * Math.hypot(width, height);
  for (let i = 0; i < 4; i++) if (Math.hypot(out[(i + 1) % 4][0] - out[i][0], out[(i + 1) % 4][1] - out[i][1]) < minSide) return { ok: false, reason: "shape" };
  if (area(out) < 0.002 * width * height) return { ok: false, reason: "small" };
  return { ok: true, quad: out };
}

export function sameQuad(a: Quad, b: Quad, tol = 0.5): boolean {
  return a.every((p, i) => Math.abs(p[0] - b[i][0]) <= tol && Math.abs(p[1] - b[i][1]) <= tol);
}

/** Corners rounded to 0.1 px (plenty: the photo has whole pixels). */
export function roundQuad(q: Quad): Quad {
  return q.map((p) => [Math.round(p[0] * 10) / 10, Math.round(p[1] * 10) / 10]) as Quad;
}

/** POST /api/jobs/{id}/corrections body that sets a page's corners (source pixels; page is 1-based). */
export function cornersCorrection(page: number, q: Quad): CorrectionIn[] {
  return [{ field: "corners", page, value: roundQuad(q).map((p) => [p[0], p[1]]) }];
}

/** A choice in the editor: the corners found, another candidate, the whole image, or the user's own. */
export type QuadChoice = { kind: "current" } | { kind: "alt"; index: number } | { kind: "full" } | { kind: "custom" };

export function quadOf(choice: QuadChoice, g: PageGeometry): Quad | null {
  if (choice.kind === "current") return g.corners;
  if (choice.kind === "full") return fullImageQuad(g.source.width, g.source.height);
  if (choice.kind === "alt") return g.alternatives[choice.index]?.corners ?? null;
  return null;
}

/** What the editor starts with: the server's pre-selected candidate when there is one, otherwise the corners found. */
export function initialChoice(g: PageGeometry): QuadChoice {
  if (g.preselect != null && g.alternatives[g.preselect]) return { kind: "alt", index: g.preselect };
  return { kind: "current" };
}

/** The corners are the whole image (within half a pixel). */
export function isFullImage(q: Quad, width: number, height: number): boolean {
  return sameQuad(q, fullImageQuad(width, height));
}

/** A curved outline as an SVG points list. */
export function svgPoints(pts: Pt[]): string {
  return pts.map((p) => `${p[0]},${p[1]}`).join(" ");
}

/**
 * Where to draw the magnifier next to a corner being dragged (screen pixels, relative to `box`):
 * above and to the left of the finger, flipped when that would leave the box.
 */
export function magnifierPlace(corner: { x: number; y: number }, box: { width: number; height: number }, size: number, gap = 28): { x: number; y: number } {
  let x = corner.x - size - gap;
  let y = corner.y - size - gap;
  if (x < 4) x = corner.x + gap;
  if (y < 4) y = corner.y + gap;
  x = Math.max(4, Math.min(box.width - size - 4, x));
  y = Math.max(4, Math.min(box.height - size - 4, y));
  return { x, y };
}

/**
 * Candidate labels as shown: labels that appear more than once get a number ("Another outline 1", "… 2"),
 * so the chips can be told apart. Unique labels stay as they are.
 */
export function numberedLabels(labels: string[]): string[] {
  const seen = new Map<string, number>();
  for (const l of labels) seen.set(l, (seen.get(l) ?? 0) + 1);
  const at = new Map<string, number>();
  return labels.map((l) => {
    if ((seen.get(l) ?? 0) < 2) return l;
    const k = (at.get(l) ?? 0) + 1;
    at.set(l, k);
    return `${l} ${k}`;
  });
}

/** A sentence that ends with a full stop (。 after Chinese, "." otherwise), so another sentence can follow it. */
export function endSentence(s: string): string {
  const t = s.trimEnd();
  if (!t || /[。.!?！？…:：]$/.test(t)) return t;
  return t + (/[　-鿿＀-￯]$/.test(t) ? "。" : ".");
}
