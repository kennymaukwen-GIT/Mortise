// Side effects: opening files, uploading, following progress, corrections, deleting.
import { useCallback, useEffect, useRef, type Dispatch } from "react";
import * as api from "../api/client";
import { ApiError } from "../api/client";
import type { CorrectionIn, FormatInfo, PageSizePreset } from "../api/types";
import { fromException, fromServerFailure } from "../lib/errors";
import { checkFile, sniff } from "../lib/files";
import { openLocal, OpenError } from "../lib/localDoc";
import { deleteSoon, resumePendingDeletes } from "../lib/pendingDelete";
import type { RecentJob } from "../lib/storage";
import { blocks, resolveOrigin, resolveTarget, settingsFromIntent, sourceOrientation } from "./derive";
import { isWorking, type Action, type FrameMessage, type State } from "./store";

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
  /** A picture, element list or download of the current job failed: find out whether the job is still on the server. */
  checkJob(): Promise<void>;
}

/** Message for a file the browser could not open. */
function openFailure(e: unknown, name: string): FrameMessage {
  if (e instanceof OpenError) {
    if (e.reason === "encrypted") return { key: "err.encrypted" };
    if (e.reason === "empty") return { key: "err.noPages" };
  }
  return { key: "err.readGeneric", vars: { name } };
}

export function useController(state: State, dispatch: Dispatch<Action>, deps: ControllerDeps): Controller {
  const stateRef = useRef(state);
  stateRef.current = state;
  const depsRef = useRef(deps);
  depsRef.current = deps;
  const watcher = useRef<api.JobWatcher | null>(null);
  const uploadAbort = useRef<AbortController | null>(null);
  const openSeq = useRef(0);
  // Set synchronously when a conversion starts, so two clicks in the same task cannot create two jobs
  // (stateRef only changes after React re-renders).
  const busyRef = useRef<AbortController | null>(null);

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

  const recent = (jobId: string, patch: Partial<RecentJob>) => {
    const s = stateRef.current;
    depsRef.current.onRecent({ jobId, filename: s.jobFilename ?? s.file?.name ?? jobId, format: s.jobFormat ?? "", createdAt: Date.now(), ...patch });
  };

  const gone = (jobId: string) => {
    depsRef.current.onRecentRemoved(jobId);
    if (stateRef.current.jobId === jobId) {
      stopWatching();
      dispatch({ type: "JOB_GONE" });
    }
  };

  const finishJob = useCallback(
    async (jobId: string) => {
      const stop = () => stateRef.current.jobId !== jobId;
      try {
        // A short network failure right after the run must not turn a finished job into a failed one.
        const [pages, report] = await Promise.all([api.withRetry(() => api.getPages(jobId), stop), api.withRetry(() => api.getReport(jobId), stop).catch(() => null)]);
        if (stop()) return;
        const score = report?.overall_score ?? null;
        const fmt = stateRef.current.jobFormat;
        const noGraphics = !!report?.outputs?.some((o) => o.format === fmt && o.vector_count === 0);
        dispatch({ type: "JOB_DONE", pages, score, notes: report?.notes ?? [], noGraphics });
        recent(jobId, { score, status: "done" });
      } catch (e) {
        if (stop()) return;
        if (e instanceof ApiError && e.status === 404) return gone(jobId);
        dispatch({ type: "JOB_FAILED", error: fromException(e, "follow") });
      }
    },
    [dispatch], // eslint-disable-line react-hooks/exhaustive-deps
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
            const err = fromServerFailure(st.error);
            if (err.key === "err.jobDeleted") return gone(jobId);
            dispatch({ type: "JOB_FAILED", error: err });
            recent(jobId, { status: "failed" });
          }
        },
        onError: (e) => {
          watcher.current = null;
          if (stateRef.current.jobId !== jobId) return;
          if (e instanceof ApiError && e.status === 404) return gone(jobId);
          dispatch({ type: "JOB_FAILED", error: fromException(e, "follow") });
        },
      });
    },
    [dispatch, finishJob], // eslint-disable-line react-hooks/exhaustive-deps
  );

  const openFile = useCallback(
    async (file: File) => {
      const d = depsRef.current;
      // While a file is uploading or converting, a new file would silently drop that job: finish or cancel first.
      if (isWorking(stateRef.current) || busyRef.current) {
        dispatch({ type: "FRAME_ERROR", error: { key: "err.busy" } });
        return;
      }
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
        // Only now that the new file is known to be good: stop following a job that ran in the background.
        stopWatching();
        dispatch({ type: "OPEN_OK", file, doc });
      } catch (e) {
        if (seq === openSeq.current) dispatch({ type: "OPEN_FAIL", error: openFailure(e, file.name) });
      }
    },
    [dispatch], // eslint-disable-line react-hooks/exhaustive-deps
  );

  const convert = useCallback(async () => {
    const s = stateRef.current;
    const d = depsRef.current;
    if (!s.file || busyRef.current) return;
    if (s.phase === "uploading" || s.phase === "converting" || s.phase === "opening") return;
    const origin = resolveOrigin(s.settings);
    const target = resolveTarget(s.settings, d.presets, sourceOrientation(s.doc, s.pages));
    if (blocks(origin) || blocks(target)) return;
    const ctrl = new AbortController();
    busyRef.current = ctrl;
    const format = s.settings.format;
    dispatch({ type: "UPLOAD_START", format, hadOriginSize: origin.kind === "ok" });
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
      if (ctrl.signal.aborted) {
        // Cancelled after the whole file was sent: the server created the job anyway. Delete it.
        void deleteSoon(st.job_id);
        return;
      }
      uploadAbort.current = null;
      dispatch({ type: "JOB_CREATED", status: st });
      d.onRecent({ jobId: st.job_id, filename: st.filename ?? s.file.name, format, createdAt: Date.now(), status: st.status });
      if (st.status === "done") void finishJob(st.job_id);
      else follow(st.job_id);
    } catch (e) {
      if (ctrl.signal.aborted || (e instanceof DOMException && e.name === "AbortError")) return;
      uploadAbort.current = null;
      dispatch({ type: "JOB_FAILED", error: fromException(e, "upload") });
    } finally {
      if (busyRef.current === ctrl) busyRef.current = null;
    }
  }, [dispatch, finishJob, follow]);

  const cancel = useCallback(async () => {
    const s = stateRef.current;
    if (s.progress.rerun && s.pages) {
      // Re-running after a correction: never delete the job (that would throw away the result and the corrections).
      // Go back to the previous result; the re-run keeps going and the view refreshes when it ends.
      dispatch({ type: "RERUN_HIDE" });
      return;
    }
    stopWatching();
    uploadAbort.current?.abort();
    uploadAbort.current = null;
    busyRef.current = null; // the aborted upload may still be waiting for its answer (then it deletes its job itself)
    const id = s.jobId;
    dispatch({ type: "CANCELLED" });
    if (id) {
      depsRef.current.onRecentRemoved(id);
      await deleteSoon(id);
    }
  }, [dispatch]);

  /** Ask the server where a job stands and continue from there (show it, follow it, or show its failure). */
  const resume = async (jobId: string) => {
    const st = await api.withRetry(() => api.getJob(jobId), () => stateRef.current.jobId !== jobId);
    if (stateRef.current.jobId !== jobId) return st;
    if (st.status === "done") await finishJob(jobId);
    else if (st.status === "failed") {
      const err = fromServerFailure(st.error);
      dispatch({ type: "JOB_FAILED", error: err });
      recent(jobId, { status: "failed" });
    } else follow(jobId);
    return st;
  };

  const openJobImpl = async (job: RecentJob) => {
    stopWatching();
    uploadAbort.current?.abort();
    openSeq.current++;
    dispatch({ type: "OPEN_JOB_START", jobId: job.jobId, filename: job.filename, format: job.format });
    try {
      const st = await api.withRetry(() => api.getJob(job.jobId), () => stateRef.current.jobId !== job.jobId);
      if (stateRef.current.jobId !== job.jobId) return;
      const settings = settingsFromIntent(st.intent, depsRef.current.presets);
      dispatch({ type: "JOB_META", hadOriginSize: !!st.intent?.known_size, settings, filename: st.filename ?? job.filename, format: st.intent?.outputs?.[0] });
      await resume(job.jobId);
    } catch (e) {
      if (stateRef.current.jobId !== job.jobId) return;
      if (e instanceof ApiError && e.status === 404) {
        depsRef.current.onRecentRemoved(job.jobId);
        dispatch({ type: "RESET" });
        dispatch({ type: "FRAME_ERROR", error: { key: "err.expired" } });
      } else dispatch({ type: "JOB_FAILED", error: fromException(e, "follow") });
    }
  };

  const retry = useCallback(async () => {
    const s = stateRef.current;
    const mode = s.jobError?.retry ?? (s.file ? "reupload" : "reopen");
    if (mode === "none") return;
    if (mode === "reopen" && s.jobId) {
      // The job itself may be fine (only the connection failed): ask the server about the same job again.
      const id = s.jobId;
      dispatch({ type: "JOB_RECHECK" });
      try {
        await resume(id);
      } catch (e) {
        if (stateRef.current.jobId !== id) return;
        if (e instanceof ApiError && e.status === 404) return gone(id);
        dispatch({ type: "JOB_FAILED", error: fromException(e, "follow") });
      }
      return;
    }
    if (!s.file) {
      if (s.jobId) return openJobImpl({ jobId: s.jobId, filename: s.jobFilename ?? s.jobId, format: s.jobFormat ?? "", createdAt: Date.now() });
      return;
    }
    // Uploading again creates a new job: remove the failed one so the list and the server do not fill with copies.
    if (s.jobId) {
      depsRef.current.onRecentRemoved(s.jobId);
      void deleteSoon(s.jobId);
    }
    return convert();
  }, [convert]); // eslint-disable-line react-hooks/exhaustive-deps

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
    if (isWorking(stateRef.current)) return; // the menu item is disabled then; Cancel is the way out
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

  const openJob = useCallback((job: RecentJob) => openJobImpl(job), [dispatch, finishJob, follow]); // eslint-disable-line react-hooks/exhaustive-deps

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

  const checkJob = useCallback(async () => {
    const id = stateRef.current.jobId;
    if (!id) return;
    try {
      await api.getJob(id);
    } catch (e) {
      if (e instanceof ApiError && e.status === 404 && stateRef.current.jobId === id) gone(id);
    }
  }, []); // eslint-disable-line react-hooks/exhaustive-deps

  return { openFile, convert, cancel, retry, correct, startOver, deleteFromServer, openJob, deleteJob, convertAgain, checkJob };
}
