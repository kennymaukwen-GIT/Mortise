// What the feedback controls send (POST /api/jobs/{id}/feedback) and how the list from GET …/feedback is read.
import type { FeedbackIn, FeedbackItem, FeedbackTag } from "../api/types";

/** Tags offered when something on the page (an element) is wrong. */
export const ELEMENT_TAGS: FeedbackTag[] = ["text_wrong", "font_wrong", "size_wrong", "color_wrong", "position_wrong", "table_wrong", "missing", "extra", "layout_broken", "other"];

/** Tags offered for a whole page (nothing selected). */
export const PAGE_TAGS: FeedbackTag[] = ["missing", "extra", "layout_broken", "corners_wrong", "text_wrong", "table_wrong", "other"];

/** Same limit as the server (FeedbackIn.comment max_length). */
export const MAX_COMMENT = 5000;

/** Tags that fit an element kind best come first. */
export function tagsFor(kind: "text" | "table" | "image" | "shape"): FeedbackTag[] {
  const first: Record<typeof kind, FeedbackTag[]> = {
    text: ["text_wrong", "font_wrong", "size_wrong", "color_wrong", "position_wrong"],
    table: ["table_wrong", "text_wrong", "position_wrong"],
    image: ["position_wrong", "size_wrong", "extra", "missing"],
    shape: ["position_wrong", "size_wrong", "color_wrong", "extra"],
  };
  const head = first[kind];
  return [...head, ...ELEMENT_TAGS.filter((t) => !head.includes(t))];
}

/** A page rating: 1 (unusable) to 5 (perfect). Page is 1-based. */
export function ratingFeedback(page: number, rating: number): FeedbackIn {
  const r = Math.round(rating);
  if (!(r >= 1 && r <= 5)) throw new RangeError("rating must be 1–5");
  return { page, rating: r };
}

export type ReportCheck = { ok: true; body: FeedbackIn } | { ok: false; reason: "empty" };

/**
 * "Something is wrong here": an element (element_id, marked not correct) or the whole page (no element).
 * At least one tag or a sentence is needed. Tags keep the order they are offered in and appear once.
 */
export function reportFeedback(page: number, elementId: string | null, tags: Iterable<FeedbackTag>, comment: string): ReportCheck {
  const list = [...new Set(tags)];
  const text = comment.trim().slice(0, MAX_COMMENT);
  if (!list.length && !text) return { ok: false, reason: "empty" };
  const body: FeedbackIn = { page, tags: list };
  if (elementId) {
    body.element_id = elementId;
    body.correct = false;
  }
  if (text) body.comment = text;
  return { ok: true, body };
}

/** The latest rating given to each page (1-based page → 1..5). */
export function latestRatings(items: FeedbackItem[]): Record<number, number> {
  const out: Record<number, number> = {};
  for (const it of items) {
    if (typeof it.rating === "number" && typeof it.page === "number") out[it.page] = it.rating; // items come oldest first
  }
  return out;
}

/** Elements already reported as wrong (by element id). */
export function reportedElements(items: FeedbackItem[]): Set<string> {
  const out = new Set<string>();
  for (const it of items) if (it.element_id && it.correct === false) out.add(it.element_id);
  return out;
}
