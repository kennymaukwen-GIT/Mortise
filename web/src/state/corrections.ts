// Turn the edit dialog's form into correction items for POST /api/jobs/{id}/corrections.
import type { CorrectionIn, TextBoxInfo } from "../api/types";

export interface TextForm {
  paragraphs: { id: string; text: string }[];
  font: string;
  size: string;
  color: string;
  align: string;
}

export function initialForm(tb: TextBoxInfo): TextForm {
  const p0 = tb.paragraphs[0];
  const r0 = p0?.runs[0];
  return {
    paragraphs: tb.paragraphs.map((p) => ({ id: p.id, text: p.text })),
    font: r0?.font ?? "",
    size: r0 ? String(Math.round(r0.size_pt * 10) / 10) : "",
    color: (r0?.color ?? "#000000").toLowerCase(),
    align: p0?.align ?? "left",
  };
}

export function normalizeHex(v: string): string | null {
  const s = v.trim().replace(/^#/, "");
  if (/^[0-9a-f]{3}$/i.test(s)) return ("#" + s.split("").map((c) => c + c).join("")).toUpperCase();
  if (/^[0-9a-f]{6}$/i.test(s)) return ("#" + s).toUpperCase();
  return null;
}

export type DiffResult = { ok: true; items: CorrectionIn[] } | { ok: false; field: "size" | "color" | "font" };

/** Only fields the user actually changed become corrections. */
export function diffForm(tb: TextBoxInfo, before: TextForm, after: TextForm, page: number): DiffResult {
  const items: CorrectionIn[] = [];
  after.paragraphs.forEach((p, i) => {
    if (p.text !== before.paragraphs[i]?.text) items.push({ field: "text", value: p.text, element_id: p.id, page });
  });
  if (after.font.trim() !== before.font.trim()) {
    if (!after.font.trim()) return { ok: false, field: "font" };
    items.push({ field: "font_family", value: after.font.trim(), element_id: tb.id, page });
  }
  if (after.size.trim() !== before.size.trim()) {
    const v = Number(after.size.replace(",", "."));
    if (!Number.isFinite(v) || v <= 0 || v > 2000) return { ok: false, field: "size" };
    items.push({ field: "font_size_pt", value: v, element_id: tb.id, page });
  }
  if (after.color.toLowerCase() !== before.color.toLowerCase()) {
    const hex = normalizeHex(after.color);
    if (!hex) return { ok: false, field: "color" };
    items.push({ field: "color", value: hex, element_id: tb.id, page });
  }
  if (after.align !== before.align) items.push({ field: "align", value: after.align, element_id: tb.id, page });
  return { ok: true, items };
}
