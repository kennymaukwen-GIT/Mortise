// The big black frame: drop a file in, page through it, watch the conversion, compare and correct the result.
import { useCallback, useEffect, useMemo, useRef, useState, type Dispatch } from "react";
import * as api from "../api/client";
import type { CorrectionIn, PageElements, PageInfo } from "../api/types";
import { useI18n } from "../i18n/I18n";
import type { LocalDoc } from "../lib/localDoc";
import { fmtMm, widthBucket } from "../lib/units";
import { detectedSizeMm, pageGeometry } from "../state/derive";
import { pageCount, type Action, type FrameMessage, type State, type View } from "../state/store";
import { EditDialog, EditLayer } from "./EditLayer";
import { Alert, ChevronLeft, ChevronRight, Close, Grid, Pencil, Upload } from "./Icons";
import { preload, SmartImg } from "./SmartImg";
import { Viewer, type ViewerRenderInfo } from "./Viewer";

export interface StageProps {
  state: State;
  dispatch: Dispatch<Action>;
  maxMb: number;
  onPickFile: () => void;
  onDropFile: (f: File) => void;
  onCancel: () => void;
  onRetry: () => void;
  onCorrect: (items: CorrectionIn[]) => Promise<void>;
}

function useFrameMessage(m: FrameMessage | null): string | null {
  const { t } = useI18n();
  if (!m) return null;
  return "text" in m ? m.text : t(m.key, m.vars);
}

/* ------------------------------------------------------------------ images */

function LocalLayer({ doc, index, info, alt }: { doc: LocalDoc; index: number; info: ViewerRenderInfo; alt: string }) {
  const bucket = doc.kind === "image" ? 0 : widthBucket(info.pixelWidth);
  const load = useCallback(() => doc.render(index, bucket || 1000), [doc, index, bucket]);
  const [st, setSt] = useState<"loading" | "ready" | "error">("loading");
  return (
    <>
      <SmartImg baseKey={`local:${index}`} srcKey={String(bucket)} load={load} alt={alt} className="layer-img" onState={setSt} />
      {st === "loading" && <Spinner />}
    </>
  );
}

function ServerLayer({ jobId, page, kind, format, version, info, alt }: {
  jobId: string;
  page: number;
  kind: "original" | "result" | "overlay";
  format: string | null;
  version: number;
  info: ViewerRenderInfo;
  alt: string;
}) {
  const { t } = useI18n();
  const bucket = widthBucket(info.pixelWidth);
  const url = api.pageImageUrl(jobId, page, kind, { width: bucket, format: format ?? undefined, v: kind === "original" ? undefined : version });
  const load = useCallback(() => preload(url), [url]);
  const [st, setSt] = useState<"loading" | "ready" | "error">("loading");
  return (
    <>
      <SmartImg baseKey={`${jobId}:${page}:${kind}:${format}:${version}`} srcKey={String(bucket)} load={load} alt={alt} className="layer-img" onState={setSt} />
      {st === "loading" && <Spinner />}
      {st === "error" && (
        <div className="layer-msg" role="status">
          {kind === "original" ? t("view.loadFailed") : t("view.noRender")}
        </div>
      )}
    </>
  );
}

function Spinner() {
  return <span className="spinner" aria-hidden="true" />;
}

/* ------------------------------------------------------------------ compare */

function CompareDivider({ split, onSplit }: { split: number; onSplit: (v: number) => void }) {
  const { t } = useI18n();
  const ref = useRef<HTMLDivElement>(null);
  const move = (clientX: number) => {
    const box = ref.current?.parentElement?.getBoundingClientRect();
    if (!box || box.width <= 0) return;
    onSplit(Math.max(0, Math.min(1, (clientX - box.left) / box.width)));
  };
  return (
    <div
      ref={ref}
      className="compare-divider"
      style={{ left: `${split * 100}%` }}
      data-no-pan
      role="slider"
      tabIndex={0}
      aria-label={t("view.compareHandle")}
      aria-valuemin={0}
      aria-valuemax={100}
      aria-valuenow={Math.round(split * 100)}
      aria-valuetext={`${Math.round(split * 100)}%`}
      onPointerDown={(e) => {
        e.stopPropagation();
        e.currentTarget.setPointerCapture(e.pointerId);
        move(e.clientX);
      }}
      onPointerMove={(e) => {
        if (e.currentTarget.hasPointerCapture(e.pointerId)) move(e.clientX);
      }}
      onKeyDown={(e) => {
        const step = e.shiftKey ? 0.1 : 0.02;
        if (e.key === "ArrowLeft") onSplit(Math.max(0, split - step));
        else if (e.key === "ArrowRight") onSplit(Math.min(1, split + step));
        else if (e.key === "Home") onSplit(0);
        else if (e.key === "End") onSplit(1);
        else return;
        e.preventDefault();
        e.stopPropagation();
      }}
    >
      <span className="compare-divider__grip" aria-hidden="true">
        <ChevronLeft size={14} />
        <ChevronRight size={14} />
      </span>
    </div>
  );
}

/* ------------------------------------------------------------------ thumbnails */

function Thumb({ state, index, active, onClick }: { state: State; index: number; active: boolean; onClick: () => void }) {
  const { t } = useI18n();
  const done = state.phase === "done" && state.jobId && state.pages;
  const load = useCallback(() => {
    if (state.doc) return state.doc.render(index, 160);
    if (state.jobId) return preload(api.pageImageUrl(state.jobId, index + 1, "original", { width: 160 }));
    return Promise.reject(new Error("none"));
  }, [state.doc, state.jobId, index]);
  return (
    <button
      type="button"
      className={"thumb" + (active ? " is-active" : "")}
      aria-label={t("nav.thumbLabel", { n: index + 1 })}
      aria-current={active ? "page" : undefined}
      onClick={onClick}
      data-no-pan
    >
      <SmartImg baseKey={`${done ? "s" : "l"}:${index}`} srcKey="t" load={load} alt="" className="thumb__img" />
      <span className="thumb__n">{index + 1}</span>
    </button>
  );
}

/* ------------------------------------------------------------------ stage */

export function Stage({ state, dispatch, maxMb, onPickFile, onDropFile, onCancel, onRetry, onCorrect }: StageProps) {
  const { t } = useI18n();
  const [dragOver, setDragOver] = useState(false);
  const dragDepth = useRef(0);
  const [thumbs, setThumbs] = useState(false);
  const [split, setSplit] = useState(0.5);
  const [elements, setElements] = useState<{ key: string; data: PageElements | null; error: string | null } | null>(null);
  const [selected, setSelected] = useState<string | null>(null);
  const frameMsg = useFrameMessage(state.frameError);

  const total = pageCount(state);
  const index = state.pageIndex;
  const page: PageInfo | undefined = state.pages?.[index];
  const isDone = state.phase === "done" && !!state.jobId && !!state.pages;
  const busy = state.phase === "uploading" || state.phase === "converting";
  const view: View = state.view;

  useEffect(() => setSelected(null), [index, state.editMode, state.resultVersion]);

  // Elements for Edit mode.
  const elKey = `${state.jobId}:${index + 1}:${state.resultVersion}`;
  useEffect(() => {
    if (!state.editMode || !isDone || !state.jobId) return;
    if (elements?.key === elKey) return;
    let alive = true;
    setElements({ key: elKey, data: null, error: null });
    api
      .getElements(state.jobId, index + 1)
      .then((data) => alive && setElements({ key: elKey, data, error: null }))
      .catch((e) => alive && setElements({ key: elKey, data: null, error: e instanceof Error ? e.message : String(e) }));
    return () => {
      alive = false;
    };
  }, [state.editMode, isDone, elKey]); // eslint-disable-line react-hooks/exhaustive-deps

  // ← → change pages anywhere on the page unless typing in a field or a dialog is open.
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.defaultPrevented || e.altKey || e.ctrlKey || e.metaKey) return;
      const el = e.target as HTMLElement | null;
      if (el && (el.closest("input, textarea, select, [contenteditable], [role=slider], [role=dialog], [role=menu]") || el.isContentEditable)) return;
      if (e.key === "ArrowLeft" || e.key === "PageUp") dispatch({ type: "SET_PAGE", index: state.pageIndex - 1 });
      else if (e.key === "ArrowRight" || e.key === "PageDown") dispatch({ type: "SET_PAGE", index: state.pageIndex + 1 });
      else return;
      e.preventDefault();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [dispatch, state.pageIndex]);

  // Drag and drop on the frame.
  const onDragEnter = (e: React.DragEvent) => {
    if (!Array.from(e.dataTransfer.types).includes("Files") || busy) return;
    e.preventDefault();
    dragDepth.current++;
    setDragOver(true);
  };
  const onDragOver = (e: React.DragEvent) => {
    if (!Array.from(e.dataTransfer.types).includes("Files")) return;
    e.preventDefault();
    e.dataTransfer.dropEffect = busy ? "none" : "copy";
  };
  const onDragLeave = () => {
    dragDepth.current = Math.max(0, dragDepth.current - 1);
    if (dragDepth.current === 0) setDragOver(false);
  };
  const onDrop = (e: React.DragEvent) => {
    e.preventDefault();
    dragDepth.current = 0;
    setDragOver(false);
    if (busy) return;
    const f = e.dataTransfer.files?.[0];
    if (f) onDropFile(f);
  };

  const geometry = useMemo(() => pageGeometry(page, state.doc, index), [page, state.doc, index]);
  const localSize = state.doc?.sizes[index];

  // What to draw, and its aspect ratio.
  let aspect = 1 / Math.SQRT2;
  let content: ((info: ViewerRenderInfo) => React.ReactNode) | null = null;
  const alt = (kind: string) => `${kind} — ${t("nav.pageLabel", { n: index + 1, total })}`;

  if (isDone && state.jobId) {
    const jobId = state.jobId;
    const fmt = state.jobFormat;
    const v = state.resultVersion;
    const p = index + 1;
    const placementStyle = geometry.placement
      ? {
          left: `${(geometry.placement.x / geometry.width) * 100}%`,
          top: `${(geometry.placement.y / geometry.height) * 100}%`,
          width: `${(geometry.placement.w / geometry.width) * 100}%`,
          height: `${(geometry.placement.h / geometry.height) * 100}%`,
        }
      : { left: 0, top: 0, width: "100%", height: "100%" };
    if (view === "original") {
      aspect = geometry.origAspect;
      content = (info) => <ServerLayer jobId={jobId} page={p} kind="original" format={null} version={v} info={info} alt={alt(t("view.original"))} />;
    } else if (view === "result" || view === "overlay") {
      aspect = geometry.resultAspect;
      content = (info) => (
        <>
          <ServerLayer jobId={jobId} page={p} kind={view} format={fmt} version={v} info={info} alt={alt(t(view === "result" ? "view.result" : "view.overlay"))} />
          {view === "result" && state.editMode && elements?.data && <EditLayer elements={elements.data} selectedId={selected} onSelect={setSelected} />}
        </>
      );
    } else {
      aspect = geometry.resultAspect;
      content = (info) => (
        <>
          <ServerLayer jobId={jobId} page={p} kind="result" format={fmt} version={v} info={info} alt={alt(t("view.result"))} />
          <div className="compare-clip" style={{ clipPath: `inset(0 ${(1 - split) * 100}% 0 0)` }}>
            <div className="compare-clip__orig" style={placementStyle}>
              <ServerLayer jobId={jobId} page={p} kind="original" format={null} version={v} info={{ ...info, pixelWidth: info.pixelWidth * (geometry.placement ? geometry.placement.w / geometry.width : 1) }} alt={alt(t("view.original"))} />
            </div>
          </div>
          <span className="compare-tag compare-tag--left" aria-hidden="true">{t("view.original")}</span>
          <span className="compare-tag compare-tag--right" aria-hidden="true">{t("view.result")}</span>
          <CompareDivider split={split} onSplit={setSplit} />
        </>
      );
    }
  } else if (state.doc && localSize) {
    aspect = localSize.width / localSize.height;
    const doc = state.doc;
    content = (info) => <LocalLayer doc={doc} index={index} info={info} alt={alt(state.file?.name ?? "")} />;
  }

  const showNav = total > 0 && (state.phase !== "empty" && state.phase !== "opening");
  const detected = isDone && !state.jobHadOriginSize ? detectedSizeMm(state.pages?.[0]) : null;
  const pct = Math.round((state.phase === "uploading" ? (state.progress.upload ?? 0) * 0.05 : state.progress.fraction) * 100);

  return (
    <section
      className={"stage" + (dragOver ? " is-dragover" : "") + (state.phase === "empty" ? " is-empty" : "")}
      aria-label={t("drop.zone")}
      onDragEnter={onDragEnter}
      onDragOver={onDragOver}
      onDragLeave={onDragLeave}
      onDrop={onDrop}
      data-phase={state.phase}
    >
      {state.phase === "empty" && (
        <div className="empty">
          <Upload size={44} className="empty__icon" />
          <p className="empty__title">{t("empty.title")}</p>
          <p className="empty__or">{t("empty.or")}</p>
          <button type="button" className="btn btn--light" onClick={onPickFile}>
            {t("empty.choose")}
          </button>
          <p className="empty__hint">{t("empty.hint", { mb: maxMb })}</p>
          {frameMsg && (
            <p className="empty__error" role="alert">
              <Alert size={18} /> {frameMsg}
            </p>
          )}
        </div>
      )}

      {state.phase === "opening" && (
        <div className="stage__center">
          <Spinner />
        </div>
      )}

      {content && (
        <Viewer
          aspect={aspect}
          resetKey={`${index}:${view === "original" ? "o" : "r"}:${state.jobId ?? "local"}`}
          label={t("nav.pageLabel", { n: index + 1, total })}
          inset={{ top: isDone ? 72 : 24, right: 24, bottom: (showNav ? 76 : 24) + (thumbs ? 112 : 0), left: 24 }}
          disabled={busy}
        >
          {(info) => <div className={"page-sheet" + (view === "compare" && isDone ? " page-sheet--compare" : "")}>{content!(info)}</div>}
        </Viewer>
      )}

      {/* Top bar when finished: score, view switch, edit. */}
      {isDone && (
        <div className="stage-top">
          <div className="chips">
            <span className="chip chip--score" data-testid="frame-score">
              {state.score != null ? t("done.score", { pct: Math.round(state.score * 100) }) : t("done.scoreNone")}
            </span>
            {detected && <span className="chip">{t("done.detected", { w: fmtMm(detected.w), h: fmtMm(detected.h) })}</span>}
          </div>
          <div
            className="view-tabs"
            role="tablist"
            aria-label={t("view.label")}
            onKeyDown={(e) => {
              const order: View[] = ["original", "result", "compare", "overlay"];
              const i = order.indexOf(view);
              let next = -1;
              if (e.key === "ArrowRight") next = (i + 1) % order.length;
              else if (e.key === "ArrowLeft") next = (i - 1 + order.length) % order.length;
              else if (e.key === "Home") next = 0;
              else if (e.key === "End") next = order.length - 1;
              if (next < 0) return;
              e.preventDefault();
              dispatch({ type: "SET_VIEW", view: order[next] });
              (e.currentTarget.children[next] as HTMLElement | undefined)?.focus();
            }}
          >
            {(["original", "result", "compare", "overlay"] as const).map((v) => (
              <button
                key={v}
                type="button"
                role="tab"
                aria-selected={view === v}
                tabIndex={view === v ? 0 : -1}
                className="view-tabs__tab"
                onClick={() => dispatch({ type: "SET_VIEW", view: v })}
              >
                {t(`view.${v}`)}
              </button>
            ))}
          </div>
          <div className="stage-top__right">
            {view === "result" && (
              <button
                type="button"
                className={"tool-btn tool-btn--text" + (state.editMode ? " is-on" : "")}
                aria-pressed={state.editMode}
                onClick={() => dispatch({ type: "SET_EDIT", on: !state.editMode })}
              >
                <Pencil size={16} />
                <span>{t("view.edit")}</span>
              </button>
            )}
          </div>
        </div>
      )}

      {isDone && view === "overlay" && <p className="stage-note">{t("view.overlayHint")}</p>}
      {isDone && state.editMode && view === "result" && (
        <p className="stage-note" aria-live="polite">
          {elements && !elements.data && !elements.error ? t("edit.loading") : elements?.error ? elements.error : t("edit.hint")}
        </p>
      )}

      {/* Bottom: pages. */}
      {showNav && (
        <div className="stage-bottom">
          {thumbs && total > 0 && (
            <div className="thumbs" role="list" aria-label={t("nav.thumbs")}>
              {Array.from({ length: total }, (_, i) => (
                <div role="listitem" key={i}>
                  <Thumb state={state} index={i} active={i === index} onClick={() => dispatch({ type: "SET_PAGE", index: i })} />
                </div>
              ))}
            </div>
          )}
          <div className="page-nav" role="toolbar" aria-label={t("nav.thumbs")}>
            <button type="button" className="tool-btn" onClick={() => dispatch({ type: "SET_PAGE", index: index - 1 })} disabled={index <= 0} aria-label={t("nav.prev")} title={t("nav.prev")}>
              <ChevronLeft size={18} />
            </button>
            <span className="page-nav__count" aria-live="polite" aria-label={t("nav.pageLabel", { n: index + 1, total })} data-testid="page-count">
              {t("nav.pageOf", { n: index + 1, total })}
            </span>
            <button type="button" className="tool-btn" onClick={() => dispatch({ type: "SET_PAGE", index: index + 1 })} disabled={index >= total - 1} aria-label={t("nav.next")} title={t("nav.next")}>
              <ChevronRight size={18} />
            </button>
            <button
              type="button"
              className={"tool-btn" + (thumbs ? " is-on" : "")}
              onClick={() => setThumbs((v) => !v)}
              aria-pressed={thumbs}
              aria-label={thumbs ? t("nav.hideThumbs") : t("nav.showThumbs")}
              title={thumbs ? t("nav.hideThumbs") : t("nav.showThumbs")}
            >
              <Grid size={18} />
            </button>
          </div>
        </div>
      )}

      {/* Converting. */}
      {busy && (
        <div className="progress-panel" role="status" aria-live="polite" data-testid="progress">
          <div className="progress-panel__box">
            <p className="progress-panel__pct">{pct}%</p>
            <p className="progress-panel__title">
              {state.phase === "uploading"
                ? t("progress.uploading", { pct: Math.round((state.progress.upload ?? 0) * 100) })
                : state.progress.rerun
                  ? t("progress.rerun")
                  : state.progress.stage
                    ? t("progress.stage", { n: state.progress.stage, name: t(`stage.${state.progress.stage}` as "stage.1") })
                    : t("progress.queued")}
            </p>
            <div className="progress-bar" aria-hidden="true">
              <span style={{ width: `${pct}%` }} />
            </div>
            <ol className="steps" aria-label={t("progress.converting")}>
              {[1, 2, 3, 4, 5, 6, 7].map((n) => {
                const cur = state.progress.stage;
                const stateCls = cur == null ? "" : n < cur ? " is-done" : n === cur ? " is-current" : "";
                return (
                  <li key={n} className={"steps__item" + stateCls} aria-current={cur === n ? "step" : undefined}>
                    <span className="steps__n">{n}</span>
                    <span className="steps__name">{t(`stage.${n}` as "stage.1")}</span>
                  </li>
                );
              })}
            </ol>
            {state.progress.page != null && (state.progress.pages ?? 0) > 1 && state.progress.stage != null && state.progress.stage <= 4 && (
              <p className="progress-panel__page">{t("progress.page", { n: state.progress.page, total: state.progress.pages ?? 0 })}</p>
            )}
            <button type="button" className="btn btn--outline-light" onClick={onCancel}>
              {t("progress.cancel")}
            </button>
          </div>
        </div>
      )}

      {/* Failed. */}
      {state.phase === "failed" && (
        <div className="progress-panel progress-panel--failed" role="alert">
          <div className="progress-panel__box">
            <Alert size={36} />
            <p className="progress-panel__title">{t("failed.title")}</p>
            <p className="progress-panel__error" data-testid="job-error">
              {state.jobError === "network" ? t("err.network") : state.jobError}
            </p>
            <button type="button" className="btn btn--light" onClick={onRetry}>
              {t("failed.retry")}
            </button>
          </div>
        </div>
      )}

      {/* File errors when something is already shown. */}
      {frameMsg && state.phase !== "empty" && (
        <div className="frame-toast" role="alert">
          <Alert size={18} />
          <span>{frameMsg}</span>
          <button type="button" className="icon-btn icon-btn--light" onClick={() => dispatch({ type: "DISMISS_ERROR" })} aria-label={t("err.dismiss")}>
            <Close size={16} />
          </button>
        </div>
      )}

      {dragOver && (
        <div className="drop-hint" aria-hidden="true">
          <Upload size={40} />
          <span>{t("drop.release")}</span>
        </div>
      )}

      {selected && elements?.data && state.jobId && (
        <EditDialog elements={elements.data} elementId={selected} page={index + 1} onClose={() => setSelected(null)} onApply={onCorrect} />
      )}
    </section>
  );
}
