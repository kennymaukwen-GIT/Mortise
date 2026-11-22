import type { JobStatus } from "../api/types";
import type { LocalDoc } from "../lib/localDoc";
import { initialState, isBusy, pageCount, reducer, type State } from "./store";

const doc = (n: number): LocalDoc => ({ kind: "pdf", pageCount: n, sizes: Array.from({ length: n }, () => ({ width: 960, height: 540, unit: "pt" as const })), render: async () => "", destroy: () => {} });
const file = new File(["%PDF-1.7"], "deck.pdf", { type: "application/pdf" });
const status = (patch: Partial<JobStatus> = {}): JobStatus => ({
  job_id: "j1", filename: "deck.pdf", status: "queued", progress: 0, stage: null, stage_name: null, page: null, pages: 3, error: null,
  rounds: [], corrections: 0, intent: null, created_at: 0, allow_training: false, ...patch,
});
const pages = [1, 2, 3].map((page) => ({ page, width_pt: 960, height_pt: 540, images: { original: "", result: "", overlay: "" }, elements: "" }));

function loaded(): State {
  return reducer(reducer(initialState, { type: "OPEN_START", file }), { type: "OPEN_OK", file, doc: doc(3) });
}

describe("reducer", () => {
  it("opens a file and pages through it", () => {
    let s = loaded();
    expect(s.phase).toBe("loaded");
    expect(pageCount(s)).toBe(3);
    s = reducer(s, { type: "SET_PAGE", index: 5 });
    expect(s.pageIndex).toBe(2);
    s = reducer(s, { type: "SET_PAGE", index: -1 });
    expect(s.pageIndex).toBe(0);
  });

  it("goes through upload, progress and done", () => {
    let s = reducer(loaded(), { type: "UPLOAD_START", format: "svg", hadOriginSize: false });
    expect(s.phase).toBe("uploading");
    expect(isBusy(s)).toBe(true);
    s = reducer(s, { type: "UPLOAD_PROGRESS", fraction: 0.5 });
    expect(s.progress.upload).toBe(0.5);
    s = reducer(s, { type: "JOB_CREATED", status: status() });
    expect(s.phase).toBe("converting");
    expect(s.jobId).toBe("j1");
    s = reducer(s, { type: "JOB_EVENT", event: { stage: 3, stage_name: "辨識", page: 1, status: "start", progress: 0.3 } });
    expect(s.progress).toMatchObject({ stage: 3, page: 2, fraction: 0.3 });
    s = reducer(s, { type: "JOB_DONE", pages, score: 0.93 });
    expect(s.phase).toBe("done");
    expect(s.view).toBe("result");
    expect(s.score).toBe(0.93);
    expect(s.resultVersion).toBe(1);
  });

  it("treats a replayed job-level done event as the end of an earlier run", () => {
    let s = reducer(loaded(), { type: "UPLOAD_START", format: "svg", hadOriginSize: false });
    s = reducer(s, { type: "JOB_CREATED", status: status() });
    s = reducer(s, { type: "JOB_EVENT", event: { stage: 7, progress: 0.98, status: "done" } });
    s = reducer(s, { type: "JOB_EVENT", event: { status: "done", progress: 1 } });
    expect(s.progress.fraction).toBe(0);
    expect(s.progress.stage).toBeNull();
    expect(s.phase).toBe("converting");
  });

  it("cancels back to the loaded file", () => {
    let s = reducer(loaded(), { type: "UPLOAD_START", format: "svg", hadOriginSize: false });
    s = reducer(s, { type: "JOB_CREATED", status: status() });
    s = reducer(s, { type: "CANCELLED" });
    expect(s.phase).toBe("loaded");
    expect(s.jobId).toBeNull();
    expect(s.doc).not.toBeNull();
  });

  it("keeps the view and edit mode when a correction re-runs", () => {
    let s = reducer(loaded(), { type: "UPLOAD_START", format: "svg", hadOriginSize: false });
    s = reducer(s, { type: "JOB_CREATED", status: status() });
    s = reducer(s, { type: "JOB_DONE", pages, score: 0.9 });
    s = reducer(s, { type: "SET_EDIT", on: true });
    expect(s.view).toBe("result");
    s = reducer(s, { type: "RERUN_START" });
    expect(s.phase).toBe("converting");
    expect(s.progress.rerun).toBe(true);
    s = reducer(s, { type: "JOB_DONE", pages, score: 0.95 });
    expect(s.editMode).toBe(true);
    expect(s.resultVersion).toBe(2);
  });

  it("switching away from Result leaves edit mode", () => {
    let s = reducer(loaded(), { type: "SET_EDIT", on: true });
    s = reducer(s, { type: "SET_VIEW", view: "compare" });
    expect(s.editMode).toBe(false);
  });

  it("shows failures and keeps the file for a retry", () => {
    let s = reducer(loaded(), { type: "UPLOAD_START", format: "svg", hadOriginSize: false });
    s = reducer(s, { type: "JOB_FAILED", message: "壞掉了" });
    expect(s.phase).toBe("failed");
    expect(s.jobError).toBe("壞掉了");
    expect(s.file).toBe(file);
  });

  it("start over clears everything but keeps the chosen format", () => {
    let s = reducer(loaded(), { type: "SETTINGS", patch: { format: "pdf", originW: "10" } });
    s = reducer(s, { type: "RESET" });
    expect(s.phase).toBe("empty");
    expect(s.file).toBeNull();
    expect(s.settings.format).toBe("pdf");
    expect(s.settings.originW).toBe("");
  });
});
