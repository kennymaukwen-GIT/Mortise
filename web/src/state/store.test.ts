import type { JobStatus } from "../api/types";
import type { LocalDoc } from "../lib/localDoc";
import { initialState, isBusy, pageCount, reducer, type State } from "./store";

const doc = (n: number): LocalDoc => ({ id: n, kind: "pdf", pageCount: n, sizes: Array.from({ length: n }, () => ({ width: 960, height: 540, unit: "pt" as const })), render: async () => "", destroy: () => {} });
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

  it("shows the job-level done event as finishing, not as a restart at 0%", () => {
    let s = reducer(loaded(), { type: "UPLOAD_START", format: "svg", hadOriginSize: false });
    s = reducer(s, { type: "JOB_CREATED", status: status() });
    s = reducer(s, { type: "JOB_EVENT", event: { stage: 7, progress: 0.98, status: "done" } });
    s = reducer(s, { type: "JOB_EVENT", event: { status: "done", progress: 1 } });
    expect(s.progress.fraction).toBe(1);
    expect(s.progress.finishing).toBe(true);
    expect(s.progress.stage).toBe(7);
    expect(s.phase).toBe("converting");
  });

  it("never moves the progress or the step indicator back while pages are processed one by one", () => {
    let s = reducer(loaded(), { type: "UPLOAD_START", format: "svg", hadOriginSize: false });
    s = reducer(s, { type: "JOB_CREATED", status: status({ pages: 0 }) });
    expect(s.progress.pages).toBe(3); // the server does not know the page count yet: the local file does
    s = reducer(s, { type: "JOB_EVENT", event: { stage: 4, page: 0, status: "done", progress: 0.3 } });
    s = reducer(s, { type: "JOB_EVENT", event: { stage: 2, page: 1, status: "start", progress: 0.28 } });
    expect(s.progress.fraction).toBe(0.3);
    expect(s.progress.stage).toBe(2);
    expect(s.progress.maxStage).toBe(4);
    expect(s.progress.page).toBe(2);
  });

  it("a correction re-run can be left running in the background without losing the result", () => {
    let s = reducer(loaded(), { type: "UPLOAD_START", format: "svg", hadOriginSize: false });
    s = reducer(s, { type: "JOB_CREATED", status: status() });
    s = reducer(s, { type: "JOB_DONE", pages, score: 0.9 });
    s = reducer(s, { type: "RERUN_START" });
    s = reducer(s, { type: "RERUN_HIDE" });
    expect(s.phase).toBe("done");
    expect(s.jobId).toBe("j1");
    expect(s.pages).toBe(pages);
    expect(s.progress.background).toBe(true);
    s = reducer(s, { type: "JOB_DONE", pages, score: 0.95 });
    expect(s.progress.background).toBe(false);
    expect(s.score).toBe(0.95);
  });

  it("an error opening another file keeps a running conversion", () => {
    let s = reducer(loaded(), { type: "UPLOAD_START", format: "svg", hadOriginSize: false });
    s = reducer(s, { type: "JOB_CREATED", status: status() });
    s = reducer(s, { type: "OPEN_FAIL", error: { key: "err.type", vars: { name: "x.pdf" } } });
    expect(s.phase).toBe("converting");
    expect(s.jobId).toBe("j1");
  });

  it("a job that disappeared from the server goes back to the local file", () => {
    let s = reducer(loaded(), { type: "UPLOAD_START", format: "svg", hadOriginSize: false });
    s = reducer(s, { type: "JOB_CREATED", status: status() });
    s = reducer(s, { type: "JOB_DONE", pages, score: 0.9 });
    s = reducer(s, { type: "JOB_GONE" });
    expect(s.phase).toBe("loaded");
    expect(s.jobId).toBeNull();
    expect(s.frameError).toEqual({ key: "err.expired" });
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
    s = reducer(s, { type: "JOB_FAILED", error: { key: "err.failedServer", detail: "壞掉了", retry: "reupload" } });
    expect(s.phase).toBe("failed");
    expect(s.jobError).toEqual({ key: "err.failedServer", detail: "壞掉了", retry: "reupload" });
    expect(s.file).toBe(file);
  });

  it("keeps a format filled in from a job when the format list arrives later", () => {
    let s = reducer(initialState, { type: "JOB_META", hadOriginSize: false, settings: { format: "docx_fixed" } });
    s = reducer(s, { type: "FORMATS_AVAILABLE", formats: ["svg", "docx_fixed"] });
    expect(s.settings.format).toBe("docx_fixed");
    s = reducer(s, { type: "FORMATS_AVAILABLE", formats: ["pdf", "svg"] });
    expect(s.settings.format).toBe("svg");
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
