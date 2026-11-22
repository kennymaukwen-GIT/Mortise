// Pure helpers that turn settings + documents into what the API needs or the UI shows.
import type { IntentJson, PageInfo, PageSizePreset } from "../api/types";
import type { LocalDoc } from "../lib/localDoc";
import { checkPair, fmtMm, orient, orientationOf, ptToMm, toMm, type Orientation, type PairCheck } from "../lib/units";
import type { Settings } from "./store";

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

export type TargetResult = { kind: "none" } | { kind: "ok"; width: number; height: number } | { kind: "incomplete" } | { kind: "invalid" } | { kind: "range" };

/** A W/H check that stops CONVERT (the message is shown on the card instead of uploading). */
export function blocks(r: { kind: string }): boolean {
  return r.kind === "incomplete" || r.kind === "invalid" || r.kind === "range";
}

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

/**
 * Card settings that reproduce a job's intent (used when a job is reopened from the list or after a reload):
 * format, original size and output size. An output size matching a preset (either way round) selects the preset.
 */
export function settingsFromIntent(intent: IntentJson | null | undefined, presets: PageSizePreset[]): Partial<Settings> {
  if (!intent) return {};
  const out: Partial<Settings> = {};
  if (intent.outputs?.length) out.format = intent.outputs[0];
  const ks = intent.known_size;
  const kw = ks ? toMm(ks.width, ks.unit) : null;
  const kh = ks ? toMm(ks.height, ks.unit) : null;
  out.originW = kw != null ? fmtMm(kw) : "";
  out.originH = kh != null ? fmtMm(kh) : "";
  const ts = intent.target_size;
  const tw = ts ? toMm(ts.width, ts.unit) : null;
  const th = ts ? toMm(ts.height, ts.unit) : null;
  if (tw == null || th == null) return { ...out, sizeMode: "original", presetId: null, targetW: "", targetH: "", orientation: null };
  const near = (a: number, b: number) => Math.abs(a - b) < 0.6;
  const p = presets.find((x) => (near(x.width_mm, tw) && near(x.height_mm, th)) || (near(x.width_mm, th) && near(x.height_mm, tw)));
  if (p) return { ...out, sizeMode: "preset", presetId: p.id, targetW: "", targetH: "", orientation: orientationOf(tw, th) };
  return { ...out, sizeMode: "custom", presetId: null, targetW: fmtMm(tw), targetH: fmtMm(th), orientation: null };
}
