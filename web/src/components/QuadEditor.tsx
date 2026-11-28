// "Adjust the page": the untouched photo, the paper's four corners as draggable handles, other candidates as chips,
// "use the whole image", Apply and Cancel. Corners stay in source-image pixels; lib/quad.ts converts for the screen.
import { useEffect, useId, useLayoutEffect, useMemo, useRef, useState } from "react";
import * as api from "../api/client";
import type { PageGeometry, Pt, Quad } from "../api/types";
import { useI18n } from "../i18n/I18n";
import { hasCjk } from "../lib/errors";
import {
  CORNERS,
  checkQuad,
  dragBounds,
  endSentence,
  frameFor,
  initialChoice,
  isFullImage,
  magnifierPlace,
  marginsFor,
  numberedLabels,
  nudge,
  quadOf,
  sameQuad,
  screenToSource,
  setCorner,
  sourcePerScreenPx,
  sourceToScreen,
  svgPoints,
  toPercent,
  type QuadChoice,
} from "../lib/quad";
import { loadServerImage } from "../lib/serverImages";
import { Viewer } from "./Viewer";

export interface QuadEditorProps {
  jobId: string;
  geometry: PageGeometry;
  /** Send the corners (source pixels). Rejects with an error to show. */
  onApply: (q: Quad) => Promise<void>;
  onCancel: () => void;
}

/** Width of the photo requested from the server: enough for the magnifier, not the full camera resolution. */
export function sourceRequestWidth(sourceWidth: number): number {
  return Math.max(100, Math.min(Math.round(sourceWidth), 1600));
}

const MAG_SIZE = 132;
const MAG_ZOOM = 3;

export function QuadEditor({ jobId, geometry: g, onApply, onCancel }: QuadEditorProps) {
  const { t, lang } = useI18n();
  const uid = useId();
  const W = g.source.width;
  const H = g.source.height;
  const start = useMemo(() => initialChoice(g), [g]);
  const [choice, setChoice] = useState<QuadChoice>(start);
  const [quad, setQuadState] = useState<Quad>(() => quadOf(start, g) ?? g.corners);
  // The corners as they are right now (pointer events can come faster than renders) …
  const live = useRef<Quad>(quad);
  const setQuad = (q: Quad) => {
    live.current = q;
    setQuadState(q);
  };
  // … and as they were when the last drag or key press ended. The frame on screen follows these, not every move:
  // changing it mid-drag would move the photo under the finger.
  const [settled, setSettled] = useState<Quad>(quad);
  const candidates = useMemo(() => [g.corners, ...g.alternatives.map((a) => a.corners), g.outline ?? []], [g]);
  const margins = useMemo(() => marginsFor(W, H, candidates), [W, H, candidates]);
  const frame = useMemo(() => frameFor(W, H, [...candidates, settled], margins), [W, H, candidates, settled, margins]);
  const bounds = useMemo(() => dragBounds(W, H), [W, H]);
  // Some side of the photo seems to cut the paper off (it got the wide margin): say corners may go past the edge.
  const narrow = 0.05 * Math.max(W, H);
  const cutOff = g.status === "cut_off" || Object.values(margins).some((m) => m > narrow + 1e-6);
  const [img, setImg] = useState<{ src: string } | null>(null);
  const [imgError, setImgError] = useState(false);
  const [active, setActive] = useState<number | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [sending, setSending] = useState(false);
  const root = useRef<HTMLDivElement>(null);
  const box = useRef<HTMLDivElement>(null);
  const drag = useRef<{ i: number; dx: number; dy: number; id: number } | null>(null);
  const [, setTick] = useState(0); // re-render after layout so the magnifier is placed with fresh sizes
  // Room taken by the toolbars above and below the photo (they wrap on narrow screens).
  const topBar = useRef<HTMLDivElement>(null);
  const bottomBar = useRef<HTMLDivElement>(null);
  const [bars, setBars] = useState({ top: 112, bottom: 84 });
  useLayoutEffect(() => {
    const measure = () => {
      const top = (topBar.current?.offsetHeight ?? 90) + 26;
      const bottom = (bottomBar.current?.offsetHeight ?? 44) + 36;
      setBars((b) => (Math.abs(b.top - top) > 1 || Math.abs(b.bottom - bottom) > 1 ? { top, bottom } : b));
    };
    measure();
    if (typeof ResizeObserver === "undefined") return;
    const ro = new ResizeObserver(measure);
    if (topBar.current) ro.observe(topBar.current);
    if (bottomBar.current) ro.observe(bottomBar.current);
    return () => ro.disconnect();
  }, []);

  useEffect(() => {
    let alive = true;
    setImg(null);
    setImgError(false);
    loadServerImage(api.pageImageUrl(jobId, g.page, "source", { width: sourceRequestWidth(W) }))
      .then((im) => alive && setImg({ src: im.src }))
      .catch(() => alive && setImgError(true));
    return () => {
      alive = false;
    };
  }, [jobId, g.page, W]);

  // Escape leaves without changing anything.
  const cancelRef = useRef(onCancel);
  cancelRef.current = onCancel;
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape" && !e.defaultPrevented) {
        e.preventDefault();
        cancelRef.current();
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);

  // Start with the focus on the first choice, so keyboard users land in the editor.
  useLayoutEffect(() => {
    root.current?.querySelector<HTMLElement>('[role="radio"][aria-checked="true"]')?.focus({ preventScroll: true });
  }, []);

  const choose = (c: QuadChoice) => {
    const q = quadOf(c, g);
    if (!q) return;
    setChoice(c);
    setQuad(q);
    setSettled(q);
    setError(null);
  };

  const rect = () => box.current?.getBoundingClientRect() ?? null;

  const onHandleDown = (e: React.PointerEvent, i: number) => {
    if (e.pointerType === "mouse" && e.button !== 0) return;
    const r = rect();
    if (!r) return;
    e.preventDefault();
    e.stopPropagation();
    (e.currentTarget as HTMLElement).focus({ preventScroll: true });
    e.currentTarget.setPointerCapture(e.pointerId);
    const at = sourceToScreen(quad[i], frame, r);
    drag.current = { i, dx: e.clientX - at[0], dy: e.clientY - at[1], id: e.pointerId };
    setActive(i);
  };
  const onHandleMove = (e: React.PointerEvent) => {
    const d = drag.current;
    if (!d || d.id !== e.pointerId) return;
    const r = rect();
    if (!r) return;
    // Not kept inside the frame: paper cut off by the photo's edge has its corner out there. The frame grows to show
    // the corner when the drag ends.
    const p = screenToSource(e.clientX - d.dx, e.clientY - d.dy, frame, r);
    setQuad(setCorner(live.current, d.i, p, bounds));
    setChoice({ kind: "custom" });
    setError(null);
  };
  const onHandleUp = (e: React.PointerEvent) => {
    const d = drag.current;
    if (!d || d.id !== e.pointerId) return;
    drag.current = null;
    if (e.currentTarget.hasPointerCapture(e.pointerId)) e.currentTarget.releasePointerCapture(e.pointerId);
    setActive(null);
    setSettled(live.current);
  };
  const onHandleKey = (e: React.KeyboardEvent, i: number) => {
    const dirs: Record<string, [number, number]> = { ArrowLeft: [-1, 0], ArrowRight: [1, 0], ArrowUp: [0, -1], ArrowDown: [0, 1] };
    const d = dirs[e.key];
    if (!d) return;
    e.preventDefault();
    e.stopPropagation();
    const r = rect();
    const step = (r ? sourcePerScreenPx(frame, r) : 1) * (e.shiftKey ? 10 : 1);
    setQuad(nudge(live.current, i, d[0] * step, d[1] * step, bounds));
    setChoice({ kind: "custom" });
    setActive(i);
    setError(null);
  };

  useEffect(() => {
    if (active == null) return;
    const id = requestAnimationFrame(() => setTick((n) => n + 1));
    return () => cancelAnimationFrame(id);
  }, [active]);

  const apply = async () => {
    const res = checkQuad(quad, W, H);
    if (!res.ok) {
      setError(res.reason === "small" ? t("adjust.tooSmall") : t("adjust.invalid"));
      return;
    }
    setSending(true);
    setError(null);
    try {
      await onApply(res.quad);
    } catch (e) {
      setError(t("adjust.failed", { msg: e instanceof Error ? e.message : String(e) }));
      setSending(false);
    }
  };

  const cornerName = (i: number) => t(`adjust.corner.${CORNERS[i]}` as "adjust.corner.tl");
  // Candidates with the same label get numbers; a Chinese label in the English interface becomes "Option n".
  const shownLabels = numberedLabels(g.alternatives.map((a) => a.label));
  // The suggested choice is the same corners as "found by Mortise" (e.g. cut off: "three edges + photo edge"): show one chip, not two.
  const pre = g.preselect != null ? g.alternatives[g.preselect] : undefined;
  const currentIsPreselected = !g.user_set && !!pre && sameQuad(pre.corners, g.corners);
  const altLabel = (i: number) => (lang === "zh-Hant" || !hasCjk(g.alternatives[i].label) ? shownLabels[i] : t("adjust.alt", { n: i + 1 }));
  const changed = !sameQuad(quad, g.corners);
  const full = isFullImage(quad, W, H);
  // Darken everything outside the corners; a corner being dragged past the frame is still inside the darkened box.
  const shade = frameFor(W, H, [[[frame.x, frame.y], [frame.x + frame.w, frame.y + frame.h]], quad], 0);
  const imgPct = { left: `${(-frame.x / frame.w) * 100}%`, top: `${(-frame.y / frame.h) * 100}%`, width: `${(W / frame.w) * 100}%`, height: `${(H / frame.h) * 100}%` };
  const reason = g.review_reason && (lang === "zh-Hant" || !hasCjk(g.review_reason)) ? g.review_reason : g.needs_review ? t(reasonKey(g.status)) : "";

  // Magnifier next to the corner being moved: the photo enlarged around the corner, with the two sides that meet there.
  let magnifier: React.ReactNode = null;
  const r = active != null ? rect() : null;
  const rr = active != null ? root.current?.getBoundingClientRect() : null;
  if (active != null && r && rr && img) {
    const c = sourceToScreen(quad[active], frame, r);
    const at = magnifierPlace({ x: c[0] - rr.left, y: c[1] - rr.top }, rr, MAG_SIZE);
    const o = sourceToScreen([0, 0], frame, r);
    const k = r.width / frame.w; // screen px per source px
    const half = MAG_SIZE / 2;
    const toMag = (p: Pt): Pt => {
      const s = sourceToScreen(p, frame, r);
      return [half + (s[0] - c[0]) * MAG_ZOOM, half + (s[1] - c[1]) * MAG_ZOOM];
    };
    const prev = toMag(quad[(active + 3) % 4]);
    const next = toMag(quad[(active + 1) % 4]);
    magnifier = (
      <div
        className="quad-mag"
        aria-hidden="true"
        data-testid="magnifier"
        style={{
          left: at.x,
          top: at.y,
          width: MAG_SIZE,
          height: MAG_SIZE,
          backgroundImage: `url(${img.src})`,
          backgroundSize: `${W * k * MAG_ZOOM}px ${H * k * MAG_ZOOM}px`,
          backgroundPosition: `${half - (c[0] - o[0]) * MAG_ZOOM}px ${half - (c[1] - o[1]) * MAG_ZOOM}px`,
        }}
      >
        <svg width={MAG_SIZE} height={MAG_SIZE} viewBox={`0 0 ${MAG_SIZE} ${MAG_SIZE}`}>
          <path d={`M${prev[0]},${prev[1]} L${half},${half} L${next[0]},${next[1]}`} className="quad-mag__edge" />
          <path d={`M${half - 10},${half} h20 M${half},${half - 10} v20`} className="quad-mag__cross" />
        </svg>
      </div>
    );
  }

  return (
    <div className="quad-editor" ref={root} data-testid="quad-editor">
      {/* Before the photo in the page, so Tab goes: choices → the four corners → zoom → Cancel / Apply. */}
      <div className="quad-top" ref={topBar}>
        <div className="quad-top__row">
          <h2 className="quad-title">{t("adjust.title")}</h2>
          <div className="quad-choices" role="radiogroup" aria-label={t("adjust.choices")} onKeyDown={(e) => radioKeys(e)}>
            {!currentIsPreselected && (
              <ChoiceChip checked={choice.kind === "current"} onClick={() => choose({ kind: "current" })} label={g.user_set ? t("adjust.yours") : t("adjust.current")} testid="choice-current" />
            )}
            {g.alternatives.map((a, i) => (
              <ChoiceChip
                key={i}
                checked={choice.kind === "alt" && choice.index === i}
                onClick={() => choose({ kind: "alt", index: i })}
                label={altLabel(i)}
                lang={hasCjk(a.label) && lang === "zh-Hant" ? "zh-Hant" : undefined}
                hint={g.preselect === i ? t("adjust.suggested") : undefined}
                testid={`choice-alt-${i}`}
              />
            ))}
            <ChoiceChip checked={choice.kind === "full" || (choice.kind === "custom" && full)} onClick={() => choose({ kind: "full" })} label={t("adjust.full")} testid="choice-full" />
            {choice.kind === "custom" && !full && <ChoiceChip checked onClick={() => undefined} label={t("adjust.custom")} testid="choice-custom" />}
          </div>
        </div>
        <p className="quad-hint" id={uid + "-keys"}>
          {reason && <span className="quad-hint__reason">{endSentence(reason)} </span>}
          {t("adjust.hint")}
          {cutOff ? " " + t("adjust.hintOutside") : ""}
          <span className="quad-hint__keys"> {t("adjust.hintKeys")}</span>
          {g.outline && g.outline.length > 2 ? " " + t("adjust.outlineLegend") : ""}
        </p>
      </div>

      <Viewer aspect={frame.w / frame.h} resetKey={`adjust:${jobId}:${g.page}`} label={t("adjust.viewer")} inset={{ top: bars.top, right: 20, bottom: bars.bottom, left: 20 }}>
        {(info) => {
          const inv = 1 / info.zoom;
          return (
            <div className="quad-box" ref={box} style={{ "--quad-stroke": `${2 * inv}px` } as React.CSSProperties} data-frame={`${frame.x} ${frame.y} ${frame.w} ${frame.h}`}>
              {img && <img className="quad-photo" src={img.src} alt={t("adjust.photoAlt")} style={imgPct} draggable={false} />}
              {!img && !imgError && <span className="spinner" aria-hidden="true" />}
              {imgError && (
                <div className="layer-msg" role="status">
                  <p>{t("view.loadFailed")}</p>
                </div>
              )}
              <svg className="quad-svg" viewBox={`${frame.x} ${frame.y} ${frame.w} ${frame.h}`} preserveAspectRatio="none" aria-hidden="true">
                <rect className="quad-svg__photo-edge" x={0} y={0} width={W} height={H} vectorEffect="non-scaling-stroke" />
                <path
                  className="quad-svg__shade"
                  fillRule="evenodd"
                  d={`M${shade.x},${shade.y}h${shade.w}v${shade.h}h${-shade.w}Z M${svgPoints(quad).replace(/ /g, " L")}Z`}
                />
                {g.outline && g.outline.length > 2 && <polygon className="quad-svg__outline" points={svgPoints(g.outline)} vectorEffect="non-scaling-stroke" data-testid="outline" />}
                <polygon className="quad-svg__quad-halo" points={svgPoints(quad)} vectorEffect="non-scaling-stroke" />
                <polygon className="quad-svg__quad" points={svgPoints(quad)} vectorEffect="non-scaling-stroke" />
              </svg>
              {quad.map((p, i) => {
                const pos = toPercent(p, frame);
                return (
                  <button
                    key={i}
                    type="button"
                    className={"quad-handle" + (active === i ? " is-active" : "")}
                    style={{ left: `${pos.left}%`, top: `${pos.top}%`, transform: `translate(-50%, -50%) scale(${inv})` }}
                    data-no-pan
                    data-corner={CORNERS[i]}
                    aria-roledescription={t("adjust.handleRole")}
                    aria-label={t("adjust.handleLabel", { corner: cornerName(i), x: Math.round(p[0]), y: Math.round(p[1]) })}
                    aria-describedby={uid + "-keys"}
                    onPointerDown={(e) => onHandleDown(e, i)}
                    onPointerMove={onHandleMove}
                    onPointerUp={onHandleUp}
                    onPointerCancel={onHandleUp}
                    onKeyDown={(e) => onHandleKey(e, i)}
                    onKeyUp={() => {
                      if (drag.current) return;
                      setActive(null);
                      setSettled(live.current);
                    }}
                    onBlur={() => {
                      if (drag.current) return;
                      setActive(null);
                      setSettled(live.current);
                    }}
                  >
                    <span className="quad-handle__dot" />
                  </button>
                );
              })}
            </div>
          );
        }}
      </Viewer>

      {magnifier}

      <div className="quad-actions" ref={bottomBar}>
        {error && (
          <p className="quad-error" role="alert">
            {error}
          </p>
        )}
        <div className="quad-actions__buttons">
          <button type="button" className="btn btn--outline-light" onClick={onCancel} disabled={sending}>
            {t("adjust.cancel")}
          </button>
          <button type="button" className="btn btn--light" onClick={() => void apply()} disabled={sending} data-testid="adjust-apply">
            {sending ? t("adjust.sending") : changed ? t("adjust.apply") : t("adjust.confirm")}
          </button>
        </div>
      </div>
    </div>
  );
}

function ChoiceChip({ checked, onClick, label, hint, lang, testid }: { checked: boolean; onClick: () => void; label: string; hint?: string; lang?: string; testid?: string }) {
  return (
    <button type="button" role="radio" aria-checked={checked} tabIndex={checked ? 0 : -1} className="quad-chip" onClick={onClick} data-testid={testid}>
      <span lang={lang}>{label}</span>
      {hint && <span className="quad-chip__hint">{hint}</span>}
    </button>
  );
}

/** Arrow keys move between the choices (and pick them), like radio buttons. */
function radioKeys(e: React.KeyboardEvent<HTMLElement>) {
  const items = Array.from(e.currentTarget.querySelectorAll<HTMLElement>('[role="radio"]'));
  const i = items.indexOf(document.activeElement as HTMLElement);
  const step = e.key === "ArrowRight" || e.key === "ArrowDown" ? 1 : e.key === "ArrowLeft" || e.key === "ArrowUp" ? -1 : 0;
  if (!step || i < 0) return;
  e.preventDefault();
  e.stopPropagation();
  const next = items[(i + step + items.length) % items.length];
  next.focus();
  next.click();
}

/** Message key for why a photo's corners should be checked (used when the server's sentence is not in the UI language). */
export function reasonKey(status: string): "review.reason.cut_off" {
  const known = ["cut_off", "curved_edge", "low_contrast", "multiple_pages", "not_found"];
  return (known.includes(status) ? `review.reason.${status}` : "review.reason.default") as "review.reason.cut_off";
}
