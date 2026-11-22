import { loadAllowTraining, loadLang, loadRecent, removeRecent, saveAllowTraining, saveLang, saveRecent, upsertRecent } from "./storage";

describe("storage", () => {
  beforeEach(() => localStorage.clear());

  it("remembers the language and the training consent (default off)", () => {
    expect(loadAllowTraining()).toBe(false);
    saveAllowTraining(true);
    expect(loadAllowTraining()).toBe(true);
    saveLang("zh-Hant");
    expect(loadLang()).toBe("zh-Hant");
  });

  it("keeps recent jobs newest first, without duplicates", () => {
    let list = upsertRecent([], { jobId: "a", filename: "a.pdf", format: "svg", createdAt: 1 });
    list = upsertRecent(list, { jobId: "b", filename: "b.pdf", format: "pdf", createdAt: 2 });
    list = upsertRecent(list, { jobId: "a", filename: "a.pdf", format: "svg", createdAt: 3, score: 0.9 });
    expect(list.map((j) => j.jobId)).toEqual(["a", "b"]);
    expect(list[0].score).toBe(0.9);
    saveRecent(list);
    expect(loadRecent()).toEqual(list);
    expect(removeRecent(list, "a").map((j) => j.jobId)).toEqual(["b"]);
  });

  it("ignores corrupt data", () => {
    localStorage.setItem("mortise.recentJobs", "{not json");
    expect(loadRecent()).toEqual([]);
    localStorage.setItem("mortise.recentJobs", JSON.stringify([{ nope: 1 }, { jobId: "x", filename: "x", format: "svg", createdAt: 1 }]));
    expect(loadRecent().map((j) => j.jobId)).toEqual(["x"]);
  });
});
