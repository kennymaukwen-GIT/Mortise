// Application state: one reducer, plain data, no side effects (those live in useController).
import type { JobEvent, JobStatus, PageInfo } from "../api/types";
import type { MessageKey } from "../i18n/dict";
import type { JobError } from "../lib/errors";
import type { LocalDoc } from "../lib/localDoc";
import type { Orientation } from "../lib/units";

export type Phase = "empty" | "opening" | "loaded" | "uploading" | "converting" | "done" | "failed";
export type View = "original" | "result" | "compare" | "overlay";
export type SizeMode = "original" | "preset" | "custom";

export interface Settings {
  format: string;
  originW: string;
  originH: string;
  sizeMode: SizeMode;
  presetId: string | null;
  /** null = follow the source document's orientation. */
  orientation: Orientation | null;
  targetW: string;
  targetH: string;
}

export interface Progress {
  fraction: number;
  stage: number | null;
  /** 1-based page currently processed, when the stage works page by page. */
  page: number | null;
  pages: number | null;
  /** Highest stage reached in this run (the step indicator never goes back while pages are processed one by one). */
  maxStage: number | null;
  /** 0..1 while the file is being uploaded, otherwise null. */
  upload: number | null;
  /** True when re-running after a correction. */
  rerun: boolean;
  /** The run has ended; the result is being fetched. */
  finishing: boolean;
  /** A correction re-run the user chose not to watch: the previous result stays on screen until it ends. */
  background: boolean;
}

export type FrameMessage = { key: MessageKey; vars?: Record<string, string | number> } | { text: string };

export interface State {
  phase: Phase;
  file: File | null;
  doc: LocalDoc | null;
  /** Error shown inside the black frame (bad file, cannot open, …). */
  frameError: FrameMessage | null;
  jobId: string | null;
  jobFilename: string | null;
  jobFormat: string | null;
  /** True when the current job asked for a known original size (so we do not show the detected size). */
  jobHadOriginSize: boolean;
  jobError: JobError | null;
  progress: Progress;
  pages: PageInfo[] | null;
  score: number | null;
  /** The fidelity report's notes (server text, Traditional Chinese): why a check did not run, what was simplified, … */
  reportNotes: string[];
  /** The download has no graphics at all (svg_assets found no vector graphics on these pages). */
  noGraphics: boolean;
  pageIndex: number;
  view: View;
  editMode: boolean;
  /** Bumped after every finished run so result images are refetched. */
  resultVersion: number;
  settings: Settings;
}

export const initialSettings: Settings = {
  format: "svg",
  originW: "",
  originH: "",
  sizeMode: "original",
  presetId: null,
  orientation: null,
  targetW: "",
  targetH: "",
};

const noProgress: Progress = { fraction: 0, stage: null, page: null, pages: null, maxStage: null, upload: null, rerun: false, finishing: false, background: false };

export const initialState: State = {
  phase: "empty",
  file: null,
  doc: null,
  frameError: null,
  jobId: null,
  jobFilename: null,
  jobFormat: null,
  jobHadOriginSize: false,
  jobError: null,
  progress: noProgress,
  pages: null,
  score: null,
  reportNotes: [],
  noGraphics: false,
  pageIndex: 0,
  view: "original",
  editMode: false,
  resultVersion: 0,
  settings: initialSettings,
};

export type Action =
  | { type: "OPEN_START"; file: File }
  | { type: "OPEN_OK"; file: File; doc: LocalDoc }
  | { type: "OPEN_FAIL"; error: FrameMessage }
  | { type: "FRAME_ERROR"; error: FrameMessage }
  | { type: "DISMISS_ERROR" }
  | { type: "SET_PAGE"; index: number }
  | { type: "SET_VIEW"; view: View }
  | { type: "SET_EDIT"; on: boolean }
  | { type: "SETTINGS"; patch: Partial<Settings> }
  | { type: "UPLOAD_START"; format: string; hadOriginSize: boolean }
  | { type: "UPLOAD_PROGRESS"; fraction: number }
  | { type: "JOB_CREATED"; status: JobStatus }
  | { type: "JOB_EVENT"; event: JobEvent }
  | { type: "JOB_DONE"; pages: PageInfo[]; score: number | null; notes?: string[]; noGraphics?: boolean }
  | { type: "JOB_FAILED"; error: JobError }
  | { type: "CANCELLED" }
  | { type: "RERUN_START" }
  | { type: "RERUN_HIDE" }
  | { type: "JOB_GONE" }
  | { type: "JOB_RECHECK" }
  | { type: "CONVERT_AGAIN" }
  | { type: "OPEN_JOB_START"; jobId: string; filename: string; format: string }
  | { type: "JOB_META"; hadOriginSize: boolean; settings?: Partial<Settings>; filename?: string | null; format?: string | null }
  | { type: "FORMATS_AVAILABLE"; formats: string[] }
  | { type: "RESET" };

function clampPage(index: number, count: number): number {
  return Math.max(0, Math.min(Math.max(0, count - 1), index));
}

export function pageCount(s: State): number {
  if (s.phase === "done" && s.pages) return s.pages.length;
  if (s.doc) return s.doc.pageCount;
  if (s.pages) return s.pages.length;
  return 0;
}

export function isBusy(s: State): boolean {
  return s.phase === "uploading" || s.phase === "converting" || s.phase === "opening";
}

/** Uploading or converting in the foreground: a new file must not replace the job silently. */
export function isWorking(s: State): boolean {
  return s.phase === "uploading" || s.phase === "converting";
}

export function reducer(s: State, a: Action): State {
  switch (a.type) {
    case "OPEN_START":
      return { ...s, phase: "opening", frameError: null };
    case "OPEN_OK":
      return {
        ...initialState,
        settings: { ...s.settings, originW: "", originH: "", orientation: null },
        phase: "loaded",
        file: a.file,
        doc: a.doc,
      };
    case "OPEN_FAIL":
      // Keep whatever was shown before; the error appears in the frame.
      if (s.phase !== "opening") return { ...s, frameError: a.error };
      return { ...s, phase: s.jobId && s.pages ? "done" : s.doc ? "loaded" : "empty", frameError: a.error };
    case "FRAME_ERROR":
      return { ...s, frameError: a.error };
    case "DISMISS_ERROR":
      return { ...s, frameError: null };
    case "SET_PAGE":
      return { ...s, pageIndex: clampPage(a.index, pageCount(s)) };
    case "SET_VIEW":
      return { ...s, view: a.view, editMode: a.view === "result" ? s.editMode : false };
    case "SET_EDIT":
      return { ...s, editMode: a.on, view: a.on ? "result" : s.view };
    case "SETTINGS":
      return { ...s, settings: { ...s.settings, ...a.patch } };
    case "UPLOAD_START":
      return {
        ...s,
        phase: "uploading",
        jobId: null,
        jobError: null,
        jobFormat: a.format,
        jobFilename: s.file?.name ?? null,
        jobHadOriginSize: a.hadOriginSize,
        pages: null,
        score: null,
        editMode: false,
        frameError: null,
        progress: { ...noProgress, upload: 0 },
      };
    case "UPLOAD_PROGRESS":
      return s.phase === "uploading" ? { ...s, progress: { ...s.progress, upload: a.fraction } } : s;
    case "JOB_CREATED":
      return {
        ...s,
        phase: "converting",
        jobId: a.status.job_id,
        jobFilename: a.status.filename ?? s.jobFilename,
        progress: { ...s.progress, upload: null, fraction: a.status.progress ?? 0, pages: a.status.pages || s.doc?.pageCount || null },
      };
    case "JOB_EVENT": {
      if (s.phase !== "converting") return s;
      const e = a.event;
      // The job-level "done"/"failed" just before the final message: the run is over, the result is being loaded.
      // (The server only streams the current run, so this is never an earlier run.)
      if (e.stage == null && (e.status === "done" || e.status === "failed")) {
        return { ...s, progress: { ...s.progress, fraction: e.status === "done" ? 1 : s.progress.fraction, finishing: e.status === "done" } };
      }
      const page = e.page == null ? null : e.page + 1;
      const stage = e.stage ?? s.progress.stage;
      return {
        ...s,
        progress: {
          ...s.progress,
          fraction: typeof e.progress === "number" ? Math.max(e.progress, s.progress.fraction) : s.progress.fraction,
          stage,
          maxStage: stage == null ? s.progress.maxStage : Math.max(stage, s.progress.maxStage ?? 0),
          page: e.stage != null ? page : s.progress.page,
          pages: s.progress.pages || (page != null && page > 1 ? page : s.progress.pages),
        },
      };
    }
    case "JOB_DONE":
      return {
        ...s,
        phase: "done",
        pages: a.pages,
        score: a.score,
        reportNotes: a.notes ?? [],
        noGraphics: !!a.noGraphics,
        progress: { ...s.progress, fraction: 1, upload: null, finishing: false, background: false },
        pageIndex: clampPage(s.pageIndex, a.pages.length),
        view: s.progress.rerun ? s.view : "result",
        editMode: s.progress.rerun ? s.editMode : false,
        resultVersion: s.resultVersion + 1,
        jobError: null,
      };
    case "JOB_FAILED":
      return { ...s, phase: "failed", jobError: a.error, editMode: false, progress: { ...s.progress, upload: null, finishing: false, background: false } };
    case "CANCELLED":
      return { ...s, phase: s.doc ? "loaded" : "empty", jobId: null, jobError: null, progress: noProgress, pages: null, score: null, editMode: false, view: "original" };
    case "RERUN_START":
      return { ...s, phase: "converting", progress: { ...noProgress, rerun: true, pages: s.pages?.length ?? null } };
    case "RERUN_HIDE":
      // Back to the previous result; the re-run continues on the server and the view refreshes when it ends.
      return s.phase === "converting" && s.progress.rerun && s.pages ? { ...s, phase: "done", progress: { ...s.progress, background: true } } : s;
    case "JOB_RECHECK":
      // Ask the server about the same job again (after a network failure), keeping the file and settings.
      return { ...s, phase: "converting", jobError: null, progress: { ...noProgress, rerun: s.progress.rerun, pages: s.progress.pages } };
    case "JOB_GONE":
      // The job disappeared from the server (deleted elsewhere or expired) while it was on screen.
      return {
        ...s,
        phase: s.doc ? "loaded" : "empty",
        jobId: null,
        jobFilename: s.file ? s.jobFilename : null,
        jobError: null,
        pages: null,
        score: null,
        editMode: false,
        view: "original",
        progress: noProgress,
        frameError: { key: "err.expired" },
      };
    case "CONVERT_AGAIN":
      return { ...s, phase: s.doc ? "loaded" : s.phase, editMode: false, view: "original", jobError: null };
    case "OPEN_JOB_START":
      return {
        ...initialState,
        settings: { ...s.settings, format: a.format || s.settings.format },
        phase: "converting",
        jobId: a.jobId,
        jobFilename: a.filename,
        jobFormat: a.format,
        jobHadOriginSize: true,
      };
    case "JOB_META":
      return {
        ...s,
        jobHadOriginSize: a.hadOriginSize,
        jobFilename: a.filename || s.jobFilename,
        jobFormat: a.format || s.jobFormat,
        settings: a.settings ? { ...s.settings, ...a.settings } : s.settings,
      };
    case "FORMATS_AVAILABLE": {
      // Keep the chosen format (e.g. filled in from a reopened job) when the server offers it; else SVG, else the first one.
      const f = s.settings.format;
      const next = a.formats.includes(f) ? f : a.formats.includes("svg") ? "svg" : (a.formats[0] ?? f);
      return next === f ? s : { ...s, settings: { ...s.settings, format: next } };
    }
    case "RESET":
      return { ...initialState, settings: { ...s.settings, originW: "", originH: "", orientation: null } };
    default:
      return s;
  }
}
