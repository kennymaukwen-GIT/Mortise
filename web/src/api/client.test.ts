import { buildJobForm, errorMessage, pageImageUrl, watchJob } from "./client";
import type { JobEvent, JobStatus } from "./types";

class FakeES {
  static last: FakeES | null = null;
  onmessage: ((m: { data: string }) => void) | null = null;
  onerror: (() => void) | null = null;
  closed = false;
  constructor(public url: string) {
    FakeES.last = this;
  }
  close() {
    this.closed = true;
  }
  emit(data: object) {
    this.onmessage?.({ data: JSON.stringify(data) });
  }
}

const done: JobStatus = {
  job_id: "j1", filename: "a.pdf", status: "done", progress: 1, stage: 7, stage_name: "驗證", page: null, pages: 1, error: null,
  rounds: [], corrections: 0, intent: null, created_at: 0, allow_training: false,
};

describe("api client", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
    vi.useRealTimers();
  });

  it("builds the multipart form of POST /api/jobs", () => {
    const file = new File(["x"], "a.pdf", { type: "application/pdf" });
    const fd = buildJobForm({ file, outputs: ["svg"], width: 210, height: 297, unit: "mm", target_width: 420, target_height: 297, target_unit: "mm", allow_training: true });
    expect(fd.get("outputs")).toBe("svg");
    expect(fd.get("mode")).toBe("rescue");
    expect(fd.get("width")).toBe("210");
    expect(fd.get("height")).toBe("297");
    expect(fd.get("unit")).toBe("mm");
    expect(fd.get("target_width")).toBe("420");
    expect(fd.get("target_height")).toBe("297");
    expect(fd.get("target_unit")).toBe("mm");
    expect(fd.get("allow_training")).toBe("true");
    expect((fd.get("file") as File).name).toBe("a.pdf");
  });

  it("leaves out sizes that were not given", () => {
    const fd = buildJobForm({ file: new File(["x"], "a.png"), outputs: ["pdf", "svg"] });
    expect(fd.get("outputs")).toBe("pdf,svg");
    expect(fd.has("width")).toBe(false);
    expect(fd.has("target_width")).toBe(false);
    expect(fd.get("allow_training")).toBe("false");
  });

  it("reads FastAPI error bodies", () => {
    expect(errorMessage({ detail: "檔案超過 100 MB 上限。" }, "x")).toBe("檔案超過 100 MB 上限。");
    expect(errorMessage({ detail: [{ msg: "field required" }, { msg: "bad" }] }, "x")).toBe("field required; bad");
    expect(errorMessage(null, "fallback")).toBe("fallback");
  });

  it("builds image URLs with the output format", () => {
    expect(pageImageUrl("j1", 2, "result", { width: 1600, format: "svg" })).toBe("/api/jobs/j1/pages/2/image?kind=result&format=svg&width=1600");
    expect(pageImageUrl("j1", 1, "original", { width: 600, format: "svg" })).toBe("/api/jobs/j1/pages/1/image?kind=original&width=600");
  });

  it("follows Server-Sent Events and reports the final status once", async () => {
    vi.stubGlobal("EventSource", FakeES);
    vi.stubGlobal("fetch", vi.fn(async () => new Response(JSON.stringify(done), { status: 200, headers: { "Content-Type": "application/json" } })));
    const events: JobEvent[] = [];
    const final = vi.fn();
    watchJob("j1", { onEvent: (e) => events.push(e), onFinal: final, onError: () => {} });
    const es = FakeES.last!;
    expect(es.url).toBe("/api/jobs/j1/events");
    es.emit({ stage: 1, stage_name: "輸入", page: null, status: "start", progress: 0.02 });
    es.emit({ status: "done", progress: 1 });
    es.emit({ status: "done", final: true });
    await vi.waitFor(() => expect(final).toHaveBeenCalledTimes(1));
    expect(final.mock.calls[0][0].status).toBe("done");
    expect(events).toHaveLength(2);
    expect(es.closed).toBe(true);
  });

  it("falls back to polling when the stream breaks", async () => {
    vi.stubGlobal("EventSource", FakeES);
    const running = { ...done, status: "running", progress: 0.4, stage: 3, page: 2 };
    let calls = 0;
    vi.stubGlobal("fetch", vi.fn(async () => new Response(JSON.stringify(++calls < 2 ? running : done), { status: 200 })));
    const events: JobEvent[] = [];
    const final = vi.fn();
    watchJob("j1", { onEvent: (e) => events.push(e), onFinal: final, onError: () => {} }, { pollMs: 5 });
    FakeES.last!.onerror?.();
    await vi.waitFor(() => expect(final).toHaveBeenCalledTimes(1));
    expect(events[0]).toMatchObject({ stage: 3, page: 1, progress: 0.4 });
  });
});
