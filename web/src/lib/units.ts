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
  | { kind: "invalid" };

/** Validate a W/H pair: both empty, or both positive numbers. */
export function checkPair(w: string, h: string): PairCheck {
  const a = parsePositive(w);
  const b = parsePositive(h);
  if (a === null && b === null) return { kind: "empty" };
  if ((a !== null && Number.isNaN(a)) || (b !== null && Number.isNaN(b))) return { kind: "invalid" };
  if (a === null || b === null) return { kind: "incomplete" };
  return { kind: "ok", width: a, height: b };
}

/** Pick an image width bucket (px) so we do not refetch on every zoom step. */
export function widthBucket(px: number): number {
  const buckets = [600, 1000, 1600, 2400, 3200, 4000];
  for (const b of buckets) if (px <= b) return b;
  return 4000;
}
