import type { FeedbackItem } from "../api/types";
import { confirmedElements, confirmFeedback, ELEMENT_TAGS, latestRatings, MAX_COMMENT, PAGE_TAGS, ratingFeedback, reportedElements, reportFeedback, tagsFor } from "./feedback";

const CONTRACT_TAGS = ["text_wrong", "font_wrong", "size_wrong", "color_wrong", "position_wrong", "missing", "extra", "table_wrong", "layout_broken", "corners_wrong", "other"];

describe("feedback payloads", () => {
  it("a page rating is {page, rating} with a whole number 1–5", () => {
    expect(ratingFeedback(2, 4)).toEqual({ page: 2, rating: 4 });
    expect(ratingFeedback(1, 5)).toEqual({ page: 1, rating: 5 });
    expect(() => ratingFeedback(1, 0)).toThrow();
    expect(() => ratingFeedback(1, 6)).toThrow();
  });

  it("'something is wrong here' on an element marks it not correct, with tags and a sentence", () => {
    const r = reportFeedback(3, "p3-t-7", ["font_wrong", "text_wrong", "font_wrong"], "  第二行應該是粗體  ");
    expect(r).toEqual({ ok: true, body: { page: 3, element_id: "p3-t-7", correct: false, tags: ["font_wrong", "text_wrong"], comment: "第二行應該是粗體" } });
  });

  it("a page report has no element and no 'correct'", () => {
    const r = reportFeedback(1, null, ["missing"], "");
    expect(r).toEqual({ ok: true, body: { page: 1, tags: ["missing"] } });
  });

  it("needs a tag or a sentence", () => {
    expect(reportFeedback(1, "x", [], "   ")).toEqual({ ok: false, reason: "empty" });
    expect(reportFeedback(1, "x", [], "only words")).toEqual({ ok: true, body: { page: 1, element_id: "x", correct: false, tags: [], comment: "only words" } });
  });

  it("cuts a very long sentence to the server's limit", () => {
    const r = reportFeedback(1, null, [], "a".repeat(MAX_COMMENT + 50));
    expect(r.ok && r.body.comment?.length).toBe(MAX_COMMENT);
  });

  it("offers only tags from the API contract", () => {
    for (const t of [...ELEMENT_TAGS, ...PAGE_TAGS, ...tagsFor("text"), ...tagsFor("table"), ...tagsFor("image"), ...tagsFor("shape")]) expect(CONTRACT_TAGS).toContain(t);
    expect(PAGE_TAGS).toContain("corners_wrong");
    expect(tagsFor("table")[0]).toBe("table_wrong");
    expect(new Set(tagsFor("text")).size).toBe(tagsFor("text").length);
  });
});

describe("reading the sent feedback back", () => {
  const items: FeedbackItem[] = [
    { id: "a", page: 1, rating: 2 },
    { id: "b", page: 2, rating: 5 },
    { id: "c", page: 1, element_id: "p1-t-1", correct: false, tags: ["text_wrong"] },
    { id: "d", page: 1, rating: 4 },
    { id: "e", page: 1, element_id: "p1-t-2", correct: true },
  ];
  it("keeps the latest rating of each page", () => {
    expect(latestRatings(items)).toEqual({ 1: 4, 2: 5 });
  });
  it("knows which elements were reported wrong", () => {
    expect([...reportedElements(items)]).toEqual(["p1-t-1"]);
  });
});

describe("this is right", () => {
  it("is a confirmation of one element", () => {
    expect(confirmFeedback(2, "p2-t-3")).toEqual({ page: 2, element_id: "p2-t-3", correct: true });
  });
  it("lists the elements already confirmed", () => {
    const items = [
      { id: "1", element_id: "a", correct: true },
      { id: "2", element_id: "b", correct: false },
      { id: "3", rating: 4, page: 1 },
    ];
    expect([...confirmedElements(items as never)]).toEqual(["a"]);
  });
});
