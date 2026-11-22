import type { PageSizePreset } from "../api/types";
import { FALLBACK_PAGE_SIZES } from "../lib/catalog";
import type { LocalDoc } from "../lib/localDoc";
import { detectedSizeMm, effectiveOrientation, pageGeometry, resolveOrigin, resolveTarget, sourceOrientation } from "./derive";
import { initialSettings, type Settings } from "./store";

const presets: PageSizePreset[] = FALLBACK_PAGE_SIZES;
const s = (patch: Partial<Settings>): Settings => ({ ...initialSettings, ...patch });
const doc = (w: number, h: number): LocalDoc => ({ kind: "pdf", pageCount: 1, sizes: [{ width: w, height: h, unit: "pt" }], render: async () => "", destroy: () => {} });

describe("output size", () => {
  it("is not sent for Original size", () => {
    expect(resolveTarget(s({ sizeMode: "original" }), presets, "landscape")).toEqual({ kind: "none" });
  });

  it("follows the source orientation for paper presets", () => {
    expect(resolveTarget(s({ sizeMode: "preset", presetId: "A3" }), presets, "landscape")).toEqual({ kind: "ok", width: 420, height: 297 });
    expect(resolveTarget(s({ sizeMode: "preset", presetId: "A3" }), presets, "portrait")).toEqual({ kind: "ok", width: 297, height: 420 });
  });

  it("lets the user override the orientation", () => {
    expect(resolveTarget(s({ sizeMode: "preset", presetId: "A4", orientation: "portrait" }), presets, "landscape")).toEqual({ kind: "ok", width: 210, height: 297 });
  });

  it("keeps slides landscape unless asked otherwise", () => {
    const slide = presets.find((p) => p.id === "Slide 16:9")!;
    expect(effectiveOrientation(s({}), slide, "portrait")).toBe("landscape");
    expect(resolveTarget(s({ sizeMode: "preset", presetId: "Slide 16:9" }), presets, "portrait")).toEqual({ kind: "ok", width: 338.67, height: 190.5 });
    expect(resolveTarget(s({ sizeMode: "preset", presetId: "Slide 16:9", orientation: "portrait" }), presets, null)).toEqual({ kind: "ok", width: 190.5, height: 338.67 });
  });

  it("validates custom sizes", () => {
    expect(resolveTarget(s({ sizeMode: "custom", targetW: "100", targetH: "" }), presets, null)).toEqual({ kind: "incomplete" });
    expect(resolveTarget(s({ sizeMode: "custom", targetW: "100", targetH: "-1" }), presets, null)).toEqual({ kind: "invalid" });
    expect(resolveTarget(s({ sizeMode: "custom", targetW: "100", targetH: "50" }), presets, null)).toEqual({ kind: "ok", width: 100, height: 50 });
  });

  it("validates the origin size", () => {
    expect(resolveOrigin(s({})).kind).toBe("empty");
    expect(resolveOrigin(s({ originW: "210" })).kind).toBe("incomplete");
    expect(resolveOrigin(s({ originW: "210", originH: "297" }))).toEqual({ kind: "ok", width: 210, height: 297 });
  });
});

describe("geometry", () => {
  it("reads the orientation from the local file first", () => {
    expect(sourceOrientation(doc(960, 540), null)).toBe("landscape");
    expect(sourceOrientation(null, [{ page: 1, width_pt: 595, height_pt: 842, images: { original: "", result: "", overlay: "" }, elements: "" }])).toBe("portrait");
    expect(sourceOrientation(null, null)).toBeNull();
  });

  it("places the original inside the output page when scaled", () => {
    const page = { page: 1, width_pt: 1190, height_pt: 842, placement: { x: 95, y: 0, w: 1000, h: 842 }, images: { original: "", result: "", overlay: "" }, elements: "" };
    const g = pageGeometry(page, null, 0);
    expect(g.resultAspect).toBeCloseTo(1190 / 842);
    expect(g.origAspect).toBeCloseTo(1000 / 842);
    expect(detectedSizeMm(page)).toBeNull();
  });

  it("reports the detected size in millimetres", () => {
    const d = detectedSizeMm({ page: 1, width_pt: 595.28, height_pt: 841.89, images: { original: "", result: "", overlay: "" }, elements: "" });
    expect(d!.w).toBeCloseTo(210, 0);
    expect(d!.h).toBeCloseTo(297, 0);
  });
});
