// The big black frame: drop a file in, page through it, watch the conversion, compare and correct the result.
import { useCallback, useEffect, useMemo, useRef, useState, type Dispatch } from "react";
import * as api from "../api/client";
import { ApiError } from "../api/client";
import type { CorrectionIn, PageElements, PageGeometry, PageInfo } from "../api/types";
import { useI18n } from "../i18n/I18n";
import { LIBREOFFICE_PREVIEW, targetApp } from "../lib/catalog";
import { hasCjk } from "../lib/errors";
import type { LocalDoc } from "../lib/localDoc";
import { loadServerImage, renderKind } from "../lib/serverImages";
import { fmtMm, widthBucket } from "../lib/units";
import { detectedSizeMm, pageGeometry } from "../state/derive";
import { cornersCorrection } from "../lib/quad";
import { PAGE_TAGS } from "../state/feedback";
import type { FeedbackState } from "../state/useFeedback";
import { pageCount, type Action, type FrameMessage, type State, type View } from "../state/store";
import { EditDialog, EditLayer } from "./EditLayer";
import { PageReportDialog } from "./Feedback";
import { Alert, ChevronLeft, ChevronRight, Close, Corners, Grid, Info, More, Pencil, Upload } from "./Icons";
import { onMenuKeyDown, PopoverPanel, usePopoverDismiss } from "./Popover";
import { QuadEditor, reasonKey } from "./QuadEditor";
import { preload, SmartImg } from "./SmartImg";
import { Viewer, type ViewerRenderInfo } from "./Viewer";

export interface StageProps {
  state: State;
  dispatch: Dispatch<Action>;
  maxMb: number;
  /** LibreOffice is installed on the server (GET /api/health verify_engine). */
  serverHasLibreOffice: boolean;
  onPickFile: () => void;
  onDropFile: (f: File) => void;
  onCancel: () => void;
  onRetry: () => void;
  onCorrect: (items: CorrectionIn[]) => Promise<void>;
  /** A request about the current job failed with 404: check whether the job is gone. */
  onJobMissing: () => void;
  /** Ratings and reports of the job on screen. */
  feedback?: FeedbackState;
}

function useFrameMessage(m: FrameMessage | null): string | null {
  const { t } = useI18n();
  if (!m) return null;
  return "text" in m ? m.text : t(m.key, m.vars);
}

/** Server text (Traditional Chinese) shown in the English UI: marked as Chinese for screen readers and fonts. */
function ServerText({ text }: { text: string }) {
  return <span lang={hasCjk(text) ? "zh-Hant" : undefined}>{text}</span>;
}

/** Notes from the server that explain why a check or a drawing did not happen. */
export function whyNotes(notes: string[]): string[] {
  return notes.filter((n) => /沒有執行|失敗|找不到|無法|沒有輸出|沒能/.test(n)).slice(0, 4);
}

/* ------------------------------------------------------------------ images */

function LocalLayer({ doc, index, info, alt }: { doc: LocalDoc; index: number; info: ViewerRenderInfo; alt: string }) {
  const bucket = doc.kind === "image" ? 0 : widthBucket(info.pixelWidth);
  const load = useCallback(() => doc.render(index, bucket || 1000, "main"), [doc, index, bucket]);
  const [st, setSt] = useState<"loading" | "ready" | "error">("loading");
  return (
    <>
      {/* The document id is part of the key: a newly opened file never shows the previous file's picture. */}
      <SmartImg baseKey={`local:${doc.id}:${index}`} srcKey={String(bucket)} load={load} alt={alt} className="layer-img" onState={setSt} />
      {st === "loading" && <Spinner />}
    </>
  );
}

export interface LayerInfo {
  render: string | null;
  status: "loading" | "ready" | "error";
  errorStatus?: number;
  errorDetail?: string;
}

function ServerLayer({ jobId, page, kind, format, version, info, alt, onInfo, reason }: {
  jobId: string;
  page: number;
  kind: "original" | "result" | "overlay";
  format: string | null;
  version: number;
  info: ViewerRenderInfo;
  alt: string;
  onInfo?: (i: LayerInfo) => void;
  /** Extra explanation shown when the picture cannot be drawn. */
  reason?: string | null;
}) {
  const { t, lang } = useI18n();
  const bucket = widthBucket(info.pixelWidth);
  // The original (the page as Mortise straightened it) changes too when the user moves the paper's corners: versioned like the result.
  const url = api.pageImageUrl(jobId, page, kind, { width: bucket, format: format ?? undefined, v: version });
  const [err, setErr] = useState<ApiError | null>(null);
  const [st, setSt] = useState<"loading" | "ready" | "error">("loading");
  const infoRef = useRef(onInfo);
  infoRef.current = onInfo;
  const load = useCallback(
    () =>
      loadServerImage(url).then(
        (im) => {
          setErr(null);
          infoRef.current?.({ render: im.render, status: "ready" });
          return im.src;
        },
        (e: unknown) => {
          const ae = e instanceof ApiError ? e : new ApiError(0, String(e));
          setErr(ae);
          infoRef.current?.({ render: null, status: "error", errorStatus: ae.status, errorDetail: ae.message });
          throw e;
        },
      ),
    [url],
  );
  return (
    <>
      <SmartImg baseKey={`${jobId}:${page}:${kind}:${format}:${version}`} srcKey={String(bucket)} load={load} alt={alt} className="layer-img" onState={setSt} />
      {st === "loading" && <Spinner />}
      {st === "error" && (
        <div className="layer-msg" role="status">
          <p>{kind === "original" ? t("view.loadFailed") : t("view.noRender")}</p>
          {kind !== "original" && reason && <p className="layer-msg__why">{reason}</p>}
          {err && err.status !== 0 && err.message && !/^HTTP \d+$/.test(err.message) && (lang === "zh-Hant" || !hasCjk(err.message)) && (
            <p className="layer-msg__detail">
              <ServerText text={err.message} />
            </p>
          )}
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

/** True once the element has come near the visible part of its scrolling strip (thumbnails load lazily). */
function useNearView<T extends HTMLElement>(): [React.RefObject<T | null>, boolean] {
  const ref = useRef<T>(null);
  const [near, setNear] = useState(false);
  useEffect(() => {
    const el = ref.current;
    if (!el || near) return;
    if (typeof IntersectionObserver === "undefined") {
      setNear(true);
      return;
    }
    const io = new IntersectionObserver((entries) => entries.some((e) => e.isIntersecting) && setNear(true), { root: el.closest(".thumbs"), rootMargin: "0px 240px" });
    io.observe(el);
    return () => io.disconnect();
  }, [near]);
  return [ref, near];
}

function Thumb({ state, index, active, onClick }: { state: State; index: number; active: boolean; onClick: () => void }) {
  const { t } = useI18n();
  const [ref, near] = useNearView<HTMLButtonElement>();
  const done = state.phase === "done" && state.jobId && state.pages;
  const doc = state.doc;
  const load = useCallback(() => {
    if (doc) return doc.render(index, 160, "thumb");
    if (state.jobId) return preload(api.pageImageUrl(state.jobId, index + 1, "original", { width: 160, v: state.resultVersion }));
    return Promise.reject(new Error("none"));
  }, [doc, state.jobId, index, state.resultVersion]);
  const base = doc ? `l:${doc.id}:${index}` : `s:${state.jobId}:${index}:${state.resultVersion}`;
  return (
    <button
      ref={ref}
      type="button"
      className={"thumb" + (active ? " is-active" : "")}
      aria-label={t("nav.thumbLabel", { n: index + 1 })}
      aria-current={active ? "page" : undefined}
      onClick={onClick}
      data-no-pan
      data-done={done ? "1" : undefined}
    >
      {near && <SmartImg baseKey={base} srcKey="t" load={load} alt="" className="thumb__img" />}
      <span className="thumb__n">{index + 1}</span>
    </button>
  );
}

/* ------------------------------------------------------------------ progress */

/** Only changes when the step (or page) changes, so screen readers are not told every percent. */
function useStepAnnouncement(state: State): string {
  const { t } = useI18n();
  const p = state.progress;
  if (state.phase === "uploading") return t("progress.uploadingShort");
  if (p.finishing) return t("progress.finishing");
  if (p.rerun) return t("progress.rerun");
  if (!p.stage) return t("progress.queued");
  const name = t(`stage.${p.stage}` as "stage.1");
  return p.page != null && (p.pages ?? 0) > 1 && p.stage <= 4 ? t("progress.pageStage", { n: p.page, total: p.pages ?? 0, name }) : t("progress.stage", { n: p.stage, name });
}

/* ------------------------------------------------------------------ stage */

/** Pages whose paper corners the user should check (photos), 1-based. */
export function pagesToReview(pages: PageInfo[] | null): number[] {
  return (pages ?? []).filter((p) => p.needs_review).map((p) => p.page);
}

/** The paper's corners can be moved on this page: a photo, or any page of an uploaded picture. */
export function canAdjustPage(page: PageInfo | undefined, filename: string | null): boolean {
  if (!page) return false;
  if (page.quad_status != null || page.input_type === "photo") return true;
  return /\.(png|jpe?g)$/i.test(filename ?? "");
}

export function Stage({ state, dispatch, maxMb, serverHasLibreOffice, onPickFile, onDropFile, onCancel, onRetry, onCorrect, onJobMissing, feedback }: StageProps) {
  const { t, lang } = useI18n();
  const [dragOver, setDragOver] = useState(false);
  const dragDepth = useRef(0);
  const [thumbs, setThumbs] = useState(false);
  const [split, setSplit] = useState(0.5);
  const [elements, setElements] = useState<{ key: string; data: PageElements | null; error: string | null } | null>(null);
  const [selected, setSelected] = useState<string | null>(null);
  const [layer, setLayer] = useState<{ key: string; info: LayerInfo } | null>(null);
  const frameMsg = useFrameMessage(state.frameError);
  const announcement = useStepAnnouncement(state);
  const editReturnFocus = useRef<HTMLElement | null>(null);
  // "Adjust the page" (drag the paper's corners): the 1-based page being adjusted, or null.
  const [adjust, setAdjust] = useState<number | null>(null);
  const [geo, setGeo] = useState<{ key: string; data: PageGeometry | null; status: "loading" | "ready" | "error" | "pending" } | null>(null);
  const [pageMenu, setPageMenu] = useState(false);
  const [pageReport, setPageReport] = useState<number | null>(null);
  const pageMenuBtn = useRef<HTMLButtonElement>(null);
  const pageMenuPanel = useRef<HTMLDivElement>(null);
  const adjustRef = useRef(adjust);
  adjustRef.current = adjust;

  const total = pageCount(state);
  const index = state.pageIndex;
  const page: PageInfo | undefined = state.pages?.[index];
  const isDone = state.phase === "done" && !!state.jobId && !!state.pages;
  const busy = state.phase === "uploading" || state.phase === "converting";
  const view: View = state.view;
  const layerKey = `${state.jobId}:${index + 1}:${state.jobFormat}:${state.resultVersion}`;
  const resultInfo = layer?.key === layerKey ? layer.info : null;
  const onLayerInfo = useCallback(
    (info: LayerInfo) => {
      setLayer({ key: layerKey, info });
      if (info.status === "error" && info.errorStatus === 404) onJobMissing();
    },
    [layerKey, onJobMissing],
  );

  useEffect(() => setSelected(null), [index, state.editMode, state.resultVersion]);

  // Leave "adjust the page" when the job is no longer shown as finished (re-running, another file, …).
  useEffect(() => {
    if (!isDone || state.progress.background) setAdjust(null);
  }, [isDone, state.progress.background]);
  useEffect(() => {
    setAdjust(null);
    setPageReport(null);
  }, [state.jobId]);

  // Page geometry (where the paper is in the photo) for the page on screen, when it is needed.
  const adjusting = isDone && adjust != null && adjust === index + 1;
  const reviewPages = useMemo(() => pagesToReview(isDone ? state.pages : null), [isDone, state.pages]);
  const wantGeo = isDone && !state.progress.background && !!page && (!!page.needs_review || adjusting);
  const geoKey = `${state.jobId}:${index + 1}:${state.resultVersion}`;
  useEffect(() => {
    if (!wantGeo || !state.jobId) return;
    if (geo?.key === geoKey && geo.status !== "error") return;
    let alive = true;
    setGeo({ key: geoKey, data: null, status: "loading" });
    api
      .getGeometry(state.jobId, index + 1)
      .then((data) => alive && setGeo({ key: geoKey, data, status: "ready" }))
      .catch((e) => {
        if (!alive) return;
        if (e instanceof ApiError && e.status === 404) onJobMissing();
        // 409: page step ② is still running (for example after new corners were sent).
        setGeo({ key: geoKey, data: null, status: e instanceof ApiError && e.status === 409 ? "pending" : "error" });
      });
    return () => {
      alive = false;
    };
  }, [wantGeo, geoKey, adjust]); // eslint-disable-line react-hooks/exhaustive-deps
  const pageGeo = geo?.key === geoKey ? geo : null;

  usePopoverDismiss(
    pageMenu,
    (why) => {
      setPageMenu(false);
      if (why === "escape") pageMenuBtn.current?.focus();
    },
    [pageMenuBtn, pageMenuPanel],
  );
  useEffect(() => {
    if (pageMenu) pageMenuPanel.current?.querySelector<HTMLElement>('[role="menuitem"]:not([aria-disabled="true"])')?.focus();
  }, [pageMenu]);

  const startAdjust = (n: number) => {
    setPageMenu(false);
    dispatch({ type: "SET_EDIT", on: false });
    if (n !== index + 1) dispatch({ type: "SET_PAGE", index: n - 1 });
    setAdjust(n);
  };

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
      .catch((e) => {
        if (!alive) return;
        if (e instanceof ApiError && e.status === 404) onJobMissing();
        setElements({ key: elKey, data: null, error: t("edit.loadFailed") });
      });
    return () => {
      alive = false;
    };
  }, [state.editMode, isDone, elKey]); // eslint-disable-line react-hooks/exhaustive-deps

  // ← → change pages unless the focus is on a control that uses the arrow keys itself.
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.defaultPrevented || e.altKey || e.ctrlKey || e.metaKey || adjustRef.current != null) return;
      const el = e.target as HTMLElement | null;
      if (
        el &&
        (el.closest("input, textarea, select, [contenteditable], [role=slider], [role=dialog], [role=menu], [role=radio], [role=radiogroup], [role=tab], [role=tablist], [role=switch]") ||
          el.isContentEditable)
      )
        return;
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
  const why = whyNotes(state.reportNotes);
  const noRenderReason = why.length ? (lang === "zh-Hant" ? why.join(" ") : t("render.noneWhy")) : null;

  // What to draw, and its aspect ratio.
  let aspect = 1 / Math.SQRT2;
  let content: ((info: ViewerRenderInfo) => React.ReactNode) | null = null;
  const alt = (kind: string) => `${kind} — ${t("nav.pageLabel", { n: index + 1, total })}`;

  if (adjusting) {
    content = null; // the corner editor draws the photo itself
  } else if (isDone && state.jobId) {
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
          <ServerLayer
            jobId={jobId}
            page={p}
            kind={view}
            format={fmt}
            version={v}
            info={info}
            alt={alt(t(view === "result" ? "view.result" : "view.overlay"))}
            onInfo={onLayerInfo}
            reason={noRenderReason}
          />
          {view === "result" && state.editMode && elements?.data && (
            <EditLayer
              elements={elements.data}
              selectedId={selected}
              onSelect={(id) => {
                editReturnFocus.current = document.activeElement as HTMLElement | null;
                setSelected(id);
              }}
            />
          )}
        </>
      );
    } else {
      aspect = geometry.resultAspect;
      content = (info) => (
        <>
          <ServerLayer jobId={jobId} page={p} kind="result" format={fmt} version={v} info={info} alt={alt(t("view.result"))} onInfo={onLayerInfo} reason={noRenderReason} />
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

  const showNav = total > 0 && state.phase !== "empty" && state.phase !== "opening";
  const detected = isDone && !state.jobHadOriginSize ? detectedSizeMm(state.pages?.[0]) : null;
  const uploading = state.phase === "uploading";
  const pct = Math.round((uploading ? (state.progress.upload ?? 0) : state.progress.fraction) * 100);
  const prog = state.progress;

  // Who drew the result preview (only explained when it is not the output file itself).
  let renderNote: string | null = null;
  if (isDone && view !== "original" && resultInfo?.status === "ready") {
    const kind = renderKind(resultInfo.render);
    const app = targetApp(state.jobFormat, lang);
    if (state.jobFormat === "svg_assets") renderNote = t("render.assets");
    else if (kind === "neutral") {
      renderNote = t("render.neutral", { app });
      if (state.jobFormat === "docx_flow") renderNote += t("render.neutralFlow", { app });
      if (LIBREOFFICE_PREVIEW.has(state.jobFormat ?? "")) renderNote += serverHasLibreOffice ? t("render.neutralLoFailed") : t("render.neutralLo");
    } else if (kind === "standin") renderNote = t("render.standin", { app });
  }
  const scoreNone = isDone && state.score == null;

  // Pale yellow bar above the view: a photo's paper corners should be checked.
  let review: { text: string; lang?: string; page: number } | null = null;
  if (isDone && !adjusting && !prog.background && reviewPages.length) {
    const here = reviewPages.includes(index + 1);
    if (here) {
      const g = pageGeo?.data;
      const serverReason = g?.review_reason && (lang === "zh-Hant" || !hasCjk(g.review_reason)) ? g.review_reason : null;
      // The server's sentence may already ask for the corners ("…請拖曳四角確認。"): then it is shown as it is.
      const text = serverReason && /拖曳四角|drag the (four )?corners/i.test(serverReason) ? serverReason : t("review.bar", { reason: (serverReason ?? t(reasonKey(g?.status ?? page?.quad_status ?? ""))).replace(/[。.]\s*$/, "") });
      review = { text: total > 1 ? t("review.onPage", { n: index + 1, text }) : text, page: index + 1 };
    } else {
      const n = reviewPages[0];
      review = { text: reviewPages.length > 1 ? t("review.otherPages", { n, count: reviewPages.length }) : t("review.otherPage", { n }), page: n };
    }
  }
  const canAdjust = isDone && canAdjustPage(page, state.jobFilename);

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
          resetKey={`${index}:${view === "original" ? "o" : "r"}:${state.jobId ?? "local"}:${state.doc?.id ?? 0}`}
          label={t("nav.pageLabel", { n: index + 1, total })}
          inset={{ top: isDone ? 72 + (renderNote || scoreNone ? 46 : 0) + (review ? 58 : 0) : 24, right: 24, bottom: (showNav ? 76 : 24) + (thumbs ? 112 : 0), left: 24 }}
          disabled={busy}
        >
          {(info) => <div className={"page-sheet" + (view === "compare" && isDone ? " page-sheet--compare" : "")}>{content!(info)}</div>}
        </Viewer>
      )}

      {/* "Adjust the page": drag the paper's corners on the untouched photo. */}
      {adjusting && state.jobId && (
        <>
          {pageGeo?.status === "ready" && pageGeo.data ? (
            <QuadEditor
              key={geoKey}
              jobId={state.jobId}
              geometry={pageGeo.data}
              onCancel={() => setAdjust(null)}
              onApply={async (q) => {
                await onCorrect(cornersCorrection(index + 1, q));
                setAdjust(null);
              }}
            />
          ) : (
            <div className="stage__center quad-wait" role="status">
              {pageGeo?.status === "error" || pageGeo?.status === "pending" ? (
                <>
                  <p>{pageGeo.status === "pending" ? t("adjust.pending") : t("adjust.loadFailed")}</p>
                  <button type="button" className="btn btn--outline-light" onClick={() => setAdjust(null)}>
                    {t("adjust.cancel")}
                  </button>
                </>
              ) : (
                <>
                  <Spinner />
                  <span className="visually-hidden">{t("adjust.loading")}</span>
                </>
              )}
            </div>
          )}
        </>
      )}

      {/* Top bar when finished: score, view switch, edit. */}
      {isDone && !adjusting && (
        <div className="stage-top">
          <div className="chips">
            <span className="chip chip--score" data-testid="frame-score">
              {state.score != null ? t("done.score", { pct: Math.round(state.score * 100) }) : t("done.scoreNone")}
            </span>
            {detected && <span className="chip">{t("done.detected", { w: fmtMm(detected.w), h: fmtMm(detected.h) })}</span>}
            {prog.background && (
              <span className="chip chip--busy" role="status">
                <span className="spinner spinner--inline" aria-hidden="true" /> {t("done.updating")}
              </span>
            )}
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
                disabled={prog.background}
                onClick={() => dispatch({ type: "SET_EDIT", on: !state.editMode })}
              >
                <Pencil size={16} />
                <span>{t("view.edit")}</span>
              </button>
            )}
          </div>
        </div>
      )}

      {isDone && !adjusting && (review || renderNote || scoreNone || view === "overlay" || (state.editMode && view === "result")) && (
        <div className="stage-notes">
          {review && (
            <button type="button" className="review-bar" data-testid="review-bar" onClick={() => startAdjust(review!.page)}>
              <Corners size={18} className="review-bar__icon" />
              <span className="review-bar__text">{review.text}</span>
              <span className="review-bar__action">{review.page === index + 1 ? t("review.action") : t("review.go", { n: review.page })}</span>
            </button>
          )}
          {view === "overlay" && <p className="stage-note">{t("view.overlayHint")}</p>}
          {state.editMode && view === "result" && (
            <p className="stage-note" aria-live="polite">
              {elements && !elements.data && !elements.error ? t("edit.loading") : elements?.error ? elements.error : t("edit.hint")}
            </p>
          )}
          {renderNote && (
            <p className="stage-note stage-note--info" data-testid="render-note" data-render={resultInfo?.render ?? ""}>
              <Info size={16} />
              <span>{renderNote}</span>
            </p>
          )}
          {scoreNone && (
            <details className="stage-note stage-note--info stage-note--why" data-testid="score-why">
              <summary>
                <Info size={16} />
                <span>
                  {t("done.scoreNone")} · {t("done.scoreWhy")}
                </span>
              </summary>
              <p>{t("done.scoreNoneWhy")}</p>
              {why.length > 0 && (
                <ul aria-label={t("done.serverNotes")}>
                  {why.map((n, i) => (
                    <li key={i}>
                      <ServerText text={n} />
                    </li>
                  ))}
                </ul>
              )}
            </details>
          )}
        </div>
      )}

      {/* Bottom: pages. */}
      {showNav && !adjusting && (
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
          <div className="page-nav" role="toolbar" aria-label={t("nav.toolbar")}>
            <button type="button" className="tool-btn" onClick={() => dispatch({ type: "SET_PAGE", index: index - 1 })} disabled={index <= 0} aria-label={t("nav.prev")} title={t("nav.prev")}>
              <ChevronLeft size={18} />
            </button>
            <span className="page-nav__count" aria-live="polite">
              <span className="visually-hidden">{t("nav.pageLabel", { n: index + 1, total })}</span>
              <span aria-hidden="true" data-testid="page-count">
                {t("nav.pageOf", { n: index + 1, total })}
              </span>
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
            {isDone && (
              <span className="page-menu-anchor">
                <button
                  ref={pageMenuBtn}
                  type="button"
                  className={"tool-btn" + (pageMenu ? " is-on" : "")}
                  aria-haspopup="menu"
                  aria-expanded={pageMenu}
                  aria-controls="page-menu"
                  aria-label={t("page.menu")}
                  title={t("page.menu")}
                  disabled={prog.background}
                  onClick={() => setPageMenu((v) => !v)}
                  data-testid="page-menu-button"
                >
                  <More size={18} />
                </button>
                {pageMenu && (
                  <PopoverPanel id="page-menu" role="menu" label={t("page.menu")} panelRef={pageMenuPanel} className="popover--menu popover--page" onKeyDown={(e) => onMenuKeyDown(e)}>
                    <button
                      type="button"
                      role="menuitem"
                      className="menu-item"
                      aria-disabled={!canAdjust || undefined}
                      title={!canAdjust ? t("page.adjustNotPhoto") : undefined}
                      onClick={() => canAdjust && startAdjust(index + 1)}
                    >
                      {t("page.adjust")}
                    </button>
                    <button
                      type="button"
                      role="menuitem"
                      className="menu-item"
                      onClick={() => {
                        setPageMenu(false);
                        setPageReport(index + 1);
                      }}
                    >
                      {t("page.report")}
                    </button>
                  </PopoverPanel>
                )}
              </span>
            )}
          </div>
        </div>
      )}

      {/* Converting. */}
      {busy && (
        <div className="progress-panel" data-testid="progress">
          <div className="progress-panel__box">
            {/* One short line for screen readers, updated only when the step changes. */}
            <p className="visually-hidden" role="status" aria-live="polite">
              {announcement}
            </p>
            <p className="progress-panel__pct" aria-hidden="true">
              {pct}%
            </p>
            <p className="progress-panel__title" aria-hidden="true">
              {uploading
                ? t("progress.uploading", { pct })
                : prog.finishing
                  ? t("progress.finishing")
                  : prog.rerun && !prog.stage
                    ? t("progress.rerun")
                    : announcement}
            </p>
            <div className="progress-bar" role="progressbar" aria-label={t("progress.converting")} aria-valuemin={0} aria-valuemax={100} aria-valuenow={pct}>
              <span style={{ width: `${pct}%` }} />
            </div>
            <ol className="steps" aria-hidden="true">
              {[1, 2, 3, 4, 5, 6, 7].map((n) => {
                // Pages go through steps 2–4 one by one; the indicator shows the furthest step reached (never goes back).
                const cur = prog.finishing ? 8 : prog.maxStage ?? prog.stage;
                const stateCls = cur == null ? "" : n < cur ? " is-done" : n === cur ? " is-current" : "";
                return (
                  <li key={n} className={"steps__item" + stateCls}>
                    <span className="steps__n">{n}</span>
                    <span className="steps__name">{t(`stage.${n}` as "stage.1")}</span>
                  </li>
                );
              })}
            </ol>
            <button type="button" className="btn btn--outline-light" onClick={onCancel}>
              {prog.rerun && state.pages ? t("progress.hide") : t("progress.cancel")}
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
              {state.jobError ? t(state.jobError.key) : t("err.failedUnknown")}
            </p>
            {state.jobError?.detail &&
              (lang === "zh-Hant" ? (
                <p className="progress-panel__detail">{state.jobError.detail}</p>
              ) : (
                <details className="progress-panel__details">
                  <summary>{t("err.detail")}</summary>
                  <p>
                    <ServerText text={state.jobError.detail} />
                  </p>
                </details>
              ))}
            {state.jobError?.retry === "none" ? (
              <button type="button" className="btn btn--light" onClick={onPickFile}>
                {t("failed.another")}
              </button>
            ) : (
              <button type="button" className="btn btn--light" onClick={onRetry}>
                {t("failed.retry")}
              </button>
            )}
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
        <EditDialog
          elements={elements.data}
          elementId={selected}
          page={index + 1}
          onClose={() => {
            setSelected(null);
            // Give the focus back to the element box that opened the dialog.
            const back = editReturnFocus.current;
            editReturnFocus.current = null;
            requestAnimationFrame(() => back?.isConnected && back.focus());
          }}
          onApply={onCorrect}
          report={feedback ? { onSend: feedback.send, count: feedback.items.length, consented: feedback.consented, reported: feedback.reported } : undefined}
        />
      )}

      {pageReport != null && feedback && isDone && (
        <PageReportDialog
          page={pageReport}
          tags={PAGE_TAGS}
          onSend={feedback.send}
          count={feedback.items.length}
          consented={feedback.consented}
          onClose={() => {
            setPageReport(null);
            requestAnimationFrame(() => pageMenuBtn.current?.focus());
          }}
        />
      )}
    </section>
  );
}
