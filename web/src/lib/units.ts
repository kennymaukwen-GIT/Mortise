// Unit helpers. The API reports page sizes in PostScript points (1/72 in).
export const MM_PER_PT = 25.4 / 72;

export function ptToMm(pt: number): number {
  return pt * MM_PER_PT;
}

export function mmToPt(mm: number): number {
  return mm / MM_PER_PT;
}

/** Round for display: 1 decimal below 100 mm, whole numbers above. */
export function fmtMm(mm: number): string {
  if (!Number.isFinite(mm)) return "";
  const r = mm >= 100 ? Math.round(mm) : Math.round(mm * 10) / 10;
  return String(r);
}

export type Orientation = "portrait" | "landscape";

export function orientationOf(w: number, h: number): Orientation {
  return w > h ? "landscape" : "portrait";
}

/** Rotate a preset (listed portrait) to the wanted orientation. Square sizes are unchanged. */
export function orient(w: number, h: number, o: Orientation): { width: number; height: number } {
  const long = Math.max(w, h);
  const short = Math.min(w, h);
  return o === "landscape" ? { width: long, height: short } : { width: short, height: long };
}

/** Parse a user-typed positive number ("12", "12.5", "12,5"). Empty → null; invalid → NaN. */
export function parsePositive(s: string): number | null {
  const t = s.trim().replace(",", ".");
  if (!t) return null;
  if (!/^\d*\.?\d+$|^\d+\.$/.test(t)) return Number.NaN;
  const v = Number(t);
  return v > 0 ? v : Number.NaN;
}

export type PairCheck =
  | { kind: "empty" }
  | { kind: "ok"; width: number; height: number }
  | { kind: "incomplete" }
  | { kind: "invalid" }
  | { kind: "range" };

/**
 * Sizes the server accepts for both the original size and the output size: 0.5 in to 200 in
 * (36 pt to 14400 pt, the same limits as POST /api/jobs). Outside them the server answers 422.
 */
export const SIZE_MIN_MM = 12.7;
export const SIZE_MAX_MM = 5080;

/** Validate a W/H pair in mm: both empty, or both numbers within SIZE_MIN_MM..SIZE_MAX_MM. */
export function checkPair(w: string, h: string, min = SIZE_MIN_MM, max = SIZE_MAX_MM): PairCheck {
  const a = parsePositive(w);
  const b = parsePositive(h);
  if (a === null && b === null) return { kind: "empty" };
  if ((a !== null && Number.isNaN(a)) || (b !== null && Number.isNaN(b))) return { kind: "invalid" };
  if (a === null || b === null) return { kind: "incomplete" };
  // A little tolerance so the limits themselves (typed as 12.7 or 5080) are accepted despite rounding.
  if (a < min - 1e-6 || b < min - 1e-6 || a > max + 1e-6 || b > max + 1e-6) return { kind: "range" };
  return { kind: "ok", width: a, height: b };
}

/** Convert a physical length to mm (null for units without a physical size, e.g. px). */
export function toMm(v: number, unit: string): number | null {
  const k: Record<string, number> = { mm: 1, cm: 10, in: 25.4, pt: MM_PER_PT };
  return unit in k ? v * k[unit] : null;
}

/** Pick an image width bucket (px) so we do not refetch on every zoom step. */
export function widthBucket(px: number): number {
  const buckets = [600, 1000, 1600, 2400, 3200, 4000];
  for (const b of buckets) if (px <= b) return b;
  return 4000;
}
