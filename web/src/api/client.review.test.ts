import { cornersCorrection, fullImageQuad } from "../lib/quad";
import { ratingFeedback, reportFeedback } from "../state/feedback";
import { ApiError, dispositionName, downloadFeedbackBundle, getEngine, getFeedback, getGeometry, pageImageUrl, postCorrections, postFeedback } from "./client";

type Call = { url: string; init?: RequestInit };

function stubFetch(reply: (url: string, init?: RequestInit) => Response) {
  const calls: Call[] = [];
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      calls.push({ url, init });
      return reply(url, init);
    }),
  );
  return calls;
}
const json = (b: unknown, status = 200, headers: Record<string, string> = {}) => new Response(JSON.stringify(b), { status, headers: { "Content-Type": "application/json", ...headers } });

describe("api client: page corners and feedback", () => {
  afterEach(() => vi.unstubAllGlobals());

  it("asks for the untouched photo with kind=source (no output format)", () => {
    expect(pageImageUrl("j1", 1, "source", { width: 1600, format: "pdf" })).toBe("/api/jobs/j1/pages/1/image?kind=source&width=1600");
    expect(pageImageUrl("j1", 1, "original", { width: 600, v: 3 })).toBe("/api/jobs/j1/pages/1/image?kind=original&width=600&v=3");
  });

  it("reads the page geometry and reports 409 while step ② is running", async () => {
    const calls = stubFetch((url) => (url.includes("/pages/2/") ? json({ detail: "正在依你拉的四角重新處理這一頁，請稍候。" }, 409) : json({ page: 1, corners: fullImageQuad(10, 20) })));
    const g = await getGeometry("j 1", 1);
    expect(calls[0].url).toBe("/api/jobs/j%201/pages/1/geometry");
    expect(g.corners).toEqual(fullImageQuad(10, 20));
    await expect(getGeometry("j1", 2)).rejects.toMatchObject({ status: 409 });
  });

  it("sends dragged corners as one corners correction in source pixels", async () => {
    const calls = stubFetch(() => json({ job_id: "j1", status: "queued" }));
    await postCorrections("j1", cornersCorrection(1, [[12.34, 5], [990, 7.77], [1001, 1210], [3, 1199.96]]));
    expect(calls[0].url).toBe("/api/jobs/j1/corrections");
    expect(calls[0].init?.method).toBe("POST");
    expect((calls[0].init?.headers as Record<string, string>)["Content-Type"]).toBe("application/json");
    expect(JSON.parse(String(calls[0].init?.body))).toEqual([
      { field: "corners", page: 1, value: [[12.3, 5], [990, 7.8], [1001, 1210], [3, 1200]] },
    ]);
  });

  it("'whole image as the page' sends the photo's four corners", async () => {
    const calls = stubFetch(() => json({ job_id: "j1", status: "queued" }));
    await postCorrections("j1", cornersCorrection(1, fullImageQuad(4032, 3024)));
    expect(JSON.parse(String(calls[0].init?.body))[0].value).toEqual([[0, 0], [4032, 0], [4032, 3024], [0, 3024]]);
  });

  it("sends a page rating and an element report to POST feedback", async () => {
    const calls = stubFetch(() => json({ ok: true, id: "20261123T120000-abc" }, 201));
    const r1 = await postFeedback("j1", ratingFeedback(2, 4));
    expect(r1.id).toBe("20261123T120000-abc");
    expect(calls[0].url).toBe("/api/jobs/j1/feedback");
    expect(JSON.parse(String(calls[0].init?.body))).toEqual({ page: 2, rating: 4 });
    const rep = reportFeedback(2, "p2-t-3", ["text_wrong", "size_wrong"], "標題字太小");
    if (!rep.ok) throw new Error("expected ok");
    await postFeedback("j1", rep.body);
    expect(JSON.parse(String(calls[1].init?.body))).toEqual({ page: 2, element_id: "p2-t-3", correct: false, tags: ["text_wrong", "size_wrong"], comment: "標題字太小" });
  });

  it("lists sent feedback and reads the engine version", async () => {
    stubFetch((url) =>
      url.endsWith("/feedback")
        ? json({ items: [{ id: "a", created: "2026-11-23T12:00:00", page: 1, rating: 5, tags: [] }], corrections: 2, consented: true })
        : json({ version: "mortise-0.1.0+abc", name: "測試-1", components: {}, scorecard: null }),
    );
    const fb = await getFeedback("j1");
    expect(fb).toMatchObject({ corrections: 2, consented: true });
    expect(fb.items[0].rating).toBe(5);
    const e = await getEngine();
    expect(e.version).toBe("mortise-0.1.0+abc");
  });

  it("reads the file name from Content-Disposition (UTF-8 name first)", () => {
    expect(dispositionName(`attachment; filename="_____.mortise-feedback.zip"; filename*=UTF-8''%E6%B5%B7%E5%A0%B1.mortise-feedback.zip`, "x.zip")).toBe("海報.mortise-feedback.zip");
    expect(dispositionName(`attachment; filename="a.mortise-feedback.zip"`, "x.zip")).toBe("a.mortise-feedback.zip");
    expect(dispositionName(null, "x.zip")).toBe("x.zip");
  });

  it("downloads the feedback bundle, and fails visibly on an error", async () => {
    const zip = new Uint8Array([0x50, 0x4b, 3, 4]);
    const calls = stubFetch((url) =>
      url.includes("/bad/") ? json({ detail: "這份工作的原始檔不見了，無法匯出。" }, 409) : new Response(zip, { status: 200, headers: { "Content-Type": "application/zip", "Content-Disposition": `attachment; filename="a.mortise-feedback.zip"` } }),
    );
    const created: string[] = [];
    vi.stubGlobal("URL", Object.assign(Object.create(URL), { createObjectURL: () => (created.push("blob:1"), "blob:1"), revokeObjectURL: () => undefined }));
    const click = vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(() => undefined);
    const name = await downloadFeedbackBundle("j1", "fallback.zip");
    expect(calls[0].url).toBe("/api/jobs/j1/feedback/export");
    expect(name).toBe("a.mortise-feedback.zip");
    expect(click).toHaveBeenCalledTimes(1);
    expect(created).toHaveLength(1);
    await expect(downloadFeedbackBundle("bad", "f.zip")).rejects.toBeInstanceOf(ApiError);
    click.mockRestore();
  });
});
