// Side effects: opening files, uploading, following progress, corrections, deleting.
import { useCallback, useEffect, useRef, type Dispatch } from "react";
import * as api from "../api/client";
import { ApiError } from "../api/client";
import type { CorrectionIn, FormatInfo, PageSizePreset } from "../api/types";
import { checkFile, sniff } from "../lib/files";
import { openLocal } from "../lib/localDoc";
import { deleteSoon, resumePendingDeletes } from "../lib/pendingDelete";
import type { RecentJob } from "../lib/storage";
import { resolveOrigin, resolveTarget, sourceOrientation } from "./derive";
import type { Action, State } from "./store";

export interface ControllerDeps {
  maxMb: number;
  accepted: string[];
  presets: PageSizePreset[];
  formats: FormatInfo[];
  allowTraining: boolean;
  onRecent: (job: RecentJob) => void;
  onRecentRemoved: (jobId: string) => void;
  notify: (text: string) => void;
}

export interface Controller {
  openFile(file: File): Promise<void>;
  convert(): Promise<void>;
  cancel(): Promise<void>;
  retry(): Promise<void>;
  correct(items: CorrectionIn[]): Promise<void>;
  startOver(): void;
  deleteFromServer(): Promise<void>;
  openJob(job: RecentJob): Promise<void>;
  deleteJob(job: RecentJob): Promise<void>;
  convertAgain(): void;
}

function messageOf(e: unknown): string {
  if (e instanceof ApiError) return e.message;
  if (e instanceof Error) return e.message;
  return String(e);
}

export function useController(state: State, dispatch: Dispatch<Action>, deps: ControllerDeps): Controller {
  const stateRef = useRef(state);
  stateRef.current = state;
  const depsRef = useRef(deps);
  depsRef.current = deps;
  const watcher = useRef<api.JobWatcher | null>(null);
  const uploadAbort = useRef<AbortController | null>(null);
  const openSeq = useRef(0);

  // Free the previous preview when the document changes, and the last one on unmount.
  const docRef = useRef(state.doc);
  useEffect(() => {
    const prev = docRef.current;
    if (prev && prev !== state.doc) prev.destroy();
    docRef.current = state.doc;
  }, [state.doc]);
  useEffect(() => resumePendingDeletes(), []);
  useEffect(
    () => () => {
      watcher.current?.close();
      uploadAbort.current?.abort();
      docRef.current?.destroy();
    },
    [],
  );

  const stopWatching = () => {
    watcher.current?.close();
    watcher.current = null;
  };

  const finishJob = useCallback(
    async (jobId: string) => {
      try {
        const [pages, report] = await Promise.all([api.getPages(jobId), api.getReport(jobId).catch(() => null)]);
        if (stateRef.current.jobId !== jobId) return;
        const score = report?.overall_score ?? null;
        dispatch({ type: "JOB_DONE", pages, score });
        const s = stateRef.current;
        depsRef.current.onRecent({ jobId, filename: s.jobFilename ?? s.file?.name ?? jobId, format: s.jobFormat ?? "", createdAt: Date.now(), score, status: "done" });
      } catch (e) {
        if (stateRef.current.jobId === jobId) dispatch({ type: "JOB_FAILED", message: messageOf(e) });
      }
    },
    [dispatch],
  );

  const follow = useCallback(
    (jobId: string) => {
      stopWatching();
      watcher.current = api.watchJob(jobId, {
        onEvent: (event) => {
          if (stateRef.current.jobId === jobId) dispatch({ type: "JOB_EVENT", event });
        },
        onFinal: (st) => {
          watcher.current = null;
          if (stateRef.current.jobId !== jobId) return;
          if (st.status === "done") void finishJob(jobId);
          else {
            dispatch({ type: "JOB_FAILED", message: st.error || "failed" });
            const s = stateRef.current;
            depsRef.current.onRecent({ jobId, filename: s.jobFilename ?? jobId, format: s.jobFormat ?? "", createdAt: Date.now(), status: "failed" });
          }
        },
        onError: (e) => {
          watcher.current = null;
          if (stateRef.current.jobId === jobId) dispatch({ type: "JOB_FAILED", message: messageOf(e) });
        },
      });
    },
    [dispatch, finishJob],
  );

  const openFile = useCallback(
    async (file: File) => {
      const d = depsRef.current;
      const check = checkFile(file, d.accepted, d.maxMb);
      if (!check.ok) {
        dispatch({
          type: "FRAME_ERROR",
          error:
            check.reason === "type"
              ? { key: "err.type", vars: { name: file.name } }
              : { key: "err.size", vars: { name: file.name, size: check.sizeMb.toFixed(1), mb: d.maxMb } },
        });
        return;
      }
      const seq = ++openSeq.current;
      stopWatching();
      uploadAbort.current?.abort();
      dispatch({ type: "OPEN_START", file });
      try {
        const magic = await sniff(file);
        if (!magic || (check.kind === "pdf") !== (magic === "pdf")) {
          if (seq === openSeq.current) dispatch({ type: "OPEN_FAIL", error: { key: "err.type", vars: { name: file.name } } });
          return;
        }
        const doc = await openLocal(file, check.kind);
        if (seq !== openSeq.current) {
          doc.destroy();
          return;
        }
        dispatch({ type: "OPEN_OK", file, doc });
      } catch (e) {
        if (seq === openSeq.current) dispatch({ type: "OPEN_FAIL", error: { key: "err.read", vars: { msg: messageOf(e) } } });
      }
    },
    [dispatch],
  );

  const convert = useCallback(async () => {
    const s = stateRef.current;
    const d = depsRef.current;
    if (!s.file) return;
    if (s.phase === "uploading" || s.phase === "converting" || s.phase === "opening") return;
    const origin = resolveOrigin(s.settings);
    const target = resolveTarget(s.settings, d.presets, sourceOrientation(s.doc, s.pages));
    if (origin.kind === "incomplete" || origin.kind === "invalid" || target.kind === "incomplete" || target.kind === "invalid") return;
    const format = s.settings.format;
    dispatch({ type: "UPLOAD_START", format, hadOriginSize: origin.kind === "ok" });
    const ctrl = new AbortController();
    uploadAbort.current = ctrl;
    try {
      const st = await api.createJob(
        {
          file: s.file,
          outputs: [format],
          ...(origin.kind === "ok" ? { width: origin.width, height: origin.height, unit: "mm" as const } : {}),
          ...(target.kind === "ok" ? { target_width: target.width, target_height: target.height, target_unit: "mm" as const } : {}),
          allow_training: d.allowTraining,
        },
        { onUpload: (f) => dispatch({ type: "UPLOAD_PROGRESS", fraction: f }), signal: ctrl.signal },
      );
      if (ctrl.signal.aborted) return;
      uploadAbort.current = null;
      dispatch({ type: "JOB_CREATED", status: st });
      d.onRecent({ jobId: st.job_id, filename: st.filename ?? s.file.name, format, createdAt: Date.now(), status: st.status });
      if (st.status === "done") void finishJob(st.job_id);
      else follow(st.job_id);
    } catch (e) {
      if (ctrl.signal.aborted || (e instanceof DOMException && e.name === "AbortError")) return;
      uploadAbort.current = null;
      dispatch({ type: "JOB_FAILED", message: e instanceof ApiError && e.status === 0 ? "network" : messageOf(e) });
    }
  }, [dispatch, finishJob, follow]);

  const cancel = useCallback(async () => {
    const s = stateRef.current;
    stopWatching();
    uploadAbort.current?.abort();
    uploadAbort.current = null;
    const id = s.jobId;
    dispatch({ type: "CANCELLED" });
    if (id) {
      depsRef.current.onRecentRemoved(id);
      await deleteSoon(id);
    }
  }, [dispatch]);

  const openJobImpl = async (job: RecentJob) => {
    stopWatching();
    uploadAbort.current?.abort();
    openSeq.current++;
    dispatch({ type: "OPEN_JOB_START", jobId: job.jobId, filename: job.filename, format: job.format });
    try {
      const st = await api.getJob(job.jobId);
      if (stateRef.current.jobId !== job.jobId) return;
      dispatch({ type: "JOB_META", hadOriginSize: !!st.intent?.known_size });
      if (st.status === "done") await finishJob(job.jobId);
      else if (st.status === "failed") dispatch({ type: "JOB_FAILED", message: st.error || "failed" });
      else follow(job.jobId);
    } catch (e) {
      if (e instanceof ApiError && e.status === 404) {
        depsRef.current.onRecentRemoved(job.jobId);
        dispatch({ type: "RESET" });
        dispatch({ type: "FRAME_ERROR", error: { key: "err.expired" } });
      } else dispatch({ type: "JOB_FAILED", message: messageOf(e) });
    }
  };
  const retry = useCallback(async () => {
    const s = stateRef.current;
    if (s.file) return convert();
    if (s.jobId) {
      // No local copy (job reopened from the list): just ask the server again.
      const job = { jobId: s.jobId, filename: s.jobFilename ?? s.jobId, format: s.jobFormat ?? "", createdAt: Date.now() };
      return openJobImpl(job);
    }
  }, [convert]); // openJobImpl only reads refs

  const correct = useCallback(
    async (items: CorrectionIn[]) => {
      const s = stateRef.current;
      if (!s.jobId || !items.length) return;
      const id = s.jobId;
      await api.postCorrections(id, items); // throws to the caller (the dialog shows the error)
      dispatch({ type: "RERUN_START" });
      follow(id);
    },
    [dispatch, follow],
  );

  const startOver = useCallback(() => {
    stopWatching();
    uploadAbort.current?.abort();
    openSeq.current++;
    dispatch({ type: "RESET" });
  }, [dispatch]);

  const deleteFromServer = useCallback(async () => {
    const s = stateRef.current;
    if (!s.jobId) return;
    const id = s.jobId;
    stopWatching();
    await deleteSoon(id);
    depsRef.current.onRecentRemoved(id);
    depsRef.current.notify("file.deleted");
    dispatch({ type: "CANCELLED" });
  }, [dispatch]);

  const openJob = useCallback((job: RecentJob) => openJobImpl(job), [dispatch, finishJob, follow]); // openJobImpl only reads refs

  const deleteJob = useCallback(
    async (job: RecentJob) => {
      await deleteSoon(job.jobId);
      depsRef.current.onRecentRemoved(job.jobId);
      if (stateRef.current.jobId === job.jobId) {
        stopWatching();
        dispatch({ type: "CANCELLED" });
      }
    },
    [dispatch],
  );

  const convertAgain = useCallback(() => {
    dispatch({ type: "CONVERT_AGAIN" });
  }, [dispatch]);

  return { openFile, convert, cancel, retry, correct, startOver, deleteFromServer, openJob, deleteJob, convertAgain };
}
