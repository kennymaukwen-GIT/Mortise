// Pure helpers that turn settings + documents into what the API needs or the UI shows.
import type { PageInfo, PageSizePreset } from "../api/types";
import type { LocalDoc } from "../lib/localDoc";
import { checkPair, orient, orientationOf, ptToMm, type Orientation, type PairCheck } from "../lib/units";
import type { Settings, State } from "./store";

export function isSlidePreset(p: PageSizePreset): boolean {
  return p.width_mm > p.height_mm || /slide/i.test(p.id) || /slide/i.test(p.label);
}

/** Orientation of the source: first page of the local file, else the server's first page. */
export function sourceOrientation(doc: LocalDoc | null, pages: PageInfo[] | null): Orientation | null {
  const s = doc?.sizes[0];
  if (s) return orientationOf(s.width, s.height);
  const p = pages?.[0];
  if (p?.width_pt && p.height_pt) {
    const b = p.placement;
    return b ? orientationOf(b.w, b.h) : orientationOf(p.width_pt, p.height_pt);
  }
  return null;
}

/** Orientation actually used for a preset: the user's choice, else slides keep theirs, paper follows the source. */
export function effectiveOrientation(settings: Settings, preset: PageSizePreset | undefined, src: Orientation | null): Orientation {
  if (settings.orientation) return settings.orientation;
  if (preset && isSlidePreset(preset)) return orientationOf(preset.width_mm, preset.height_mm);
  return src ?? "portrait";
}

export type TargetResult = { kind: "none" } | { kind: "ok"; width: number; height: number } | { kind: "incomplete" } | { kind: "invalid" };

export function resolveTarget(settings: Settings, presets: PageSizePreset[], src: Orientation | null): TargetResult {
  if (settings.sizeMode === "original") return { kind: "none" };
  if (settings.sizeMode === "preset") {
    const p = presets.find((x) => x.id === settings.presetId);
    if (!p) return { kind: "none" };
    const o = effectiveOrientation(settings, p, src);
    const { width, height } = orient(p.width_mm, p.height_mm, o);
    return { kind: "ok", width: round2(width), height: round2(height) };
  }
  const c: PairCheck = checkPair(settings.targetW, settings.targetH);
  if (c.kind === "empty") return { kind: "incomplete" };
  return c;
}

export function resolveOrigin(settings: Settings): PairCheck {
  return checkPair(settings.originW, settings.originH);
}

function round2(v: number): number {
  return Math.round(v * 100) / 100;
}

/** Size the system detected (mm), shown when the user left Origin Size empty. */
export function detectedSizeMm(page: PageInfo | undefined): { w: number; h: number } | null {
  if (!page) return null;
  const sw = page.source_width_pt;
  const sh = page.source_height_pt;
  if (sw && sh) return { w: ptToMm(sw), h: ptToMm(sh) };
  if (page.placement) return null; // width_pt is the output page here, not the detected size
  if (page.width_pt && page.height_pt) return { w: ptToMm(page.width_pt), h: ptToMm(page.height_pt) };
  return null;
}

/** Aspect ratios (w/h) of the original and result layers for a finished page. */
export function pageGeometry(page: PageInfo | undefined, doc: LocalDoc | null, index: number) {
  const local = doc?.sizes[index];
  const W = page?.width_pt ?? local?.width ?? 595;
  const H = page?.height_pt ?? local?.height ?? 842;
  const placement = page?.placement ?? null;
  const origAspect = placement ? placement.w / placement.h : local && !page?.width_pt ? local.width / local.height : W / H;
  return { width: W, height: H, placement, resultAspect: W / H, origAspect };
}

export function canConvert(s: State, formatsReady: boolean): boolean {
  return !!s.file && (s.phase === "loaded" || s.phase === "done" || s.phase === "failed") && formatsReady;
}
