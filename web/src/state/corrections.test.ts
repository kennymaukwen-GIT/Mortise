import type { TextBoxInfo } from "../api/types";
import { diffForm, initialForm, normalizeHex } from "./corrections";

const tb: TextBoxInfo = {
  id: "p001-box-1",
  box: { x: 10, y: 10, w: 100, h: 40 },
  z: 1,
  confidence: 0.6,
  fields: {},
  issues: [],
  paragraphs: [
    { id: "p001-para-1", text: "Hello", align: "left", role: "body", runs: [{ text: "Hello", font: "Arial", size_pt: 12, color: "#333333" }] },
    { id: "p001-para-2", text: "World", align: "left", role: "body", runs: [{ text: "World", font: "Arial", size_pt: 12, color: "#333333" }] },
  ],
};

describe("corrections", () => {
  it("sends nothing when nothing changed", () => {
    const f = initialForm(tb);
    expect(diffForm(tb, f, f, 1)).toEqual({ ok: true, items: [] });
  });

  it("maps each changed field to the right element", () => {
    const before = initialForm(tb);
    const after = { ...before, paragraphs: [before.paragraphs[0], { id: "p001-para-2", text: "Earth" }], font: "Noto Sans TC", size: "14.5", color: "#ff0000", align: "center" };
    const d = diffForm(tb, before, after, 2);
    expect(d.ok).toBe(true);
    expect(d.ok && d.items).toEqual([
      { field: "text", value: "Earth", element_id: "p001-para-2", page: 2 },
      { field: "font_family", value: "Noto Sans TC", element_id: "p001-box-1", page: 2 },
      { field: "font_size_pt", value: 14.5, element_id: "p001-box-1", page: 2 },
      { field: "color", value: "#FF0000", element_id: "p001-box-1", page: 2 },
      { field: "align", value: "center", element_id: "p001-box-1", page: 2 },
    ]);
  });

  it("rejects bad sizes and colours", () => {
    const before = initialForm(tb);
    expect(diffForm(tb, before, { ...before, size: "0" }, 1)).toEqual({ ok: false, field: "size" });
    expect(diffForm(tb, before, { ...before, color: "red" }, 1)).toEqual({ ok: false, field: "color" });
  });

  it("normalises hex colours", () => {
    expect(normalizeHex("#abc")).toBe("#AABBCC");
    expect(normalizeHex("12ab3F")).toBe("#12AB3F");
    expect(normalizeHex("nope")).toBeNull();
  });
});
