// Turn API failures into messages the UI can translate. The server's own text (Traditional Chinese) is kept
// as a secondary detail: shown as is in Chinese, behind a "Server message" toggle in English.
import { ApiError } from "../api/client";
import type { MessageKey } from "../i18n/dict";

/** What Retry should do: upload the file again, ask the server about the same job again, or nothing (retrying cannot help). */
export type RetryMode = "reupload" | "reopen" | "none";

export interface JobError {
  key: MessageKey;
  detail: string | null;
  retry: RetryMode;
}

/** Network failures and server errors (5xx) are worth retrying; 4xx answers are not. */
export function isTransient(e: unknown): boolean {
  return e instanceof ApiError && (e.status === 0 || e.status >= 500);
}

/**
 * An exception from a request. `ctx` says what we were doing: creating the job ("upload", Retry uploads again)
 * or following an existing job ("follow", Retry asks the server about the same job again).
 */
export function fromException(e: unknown, ctx: "upload" | "follow"): JobError {
  const again: RetryMode = ctx === "follow" ? "reopen" : "reupload";
  if (e instanceof ApiError) {
    const detail = e.status === 0 ? null : e.message || null;
    if (e.status === 0) return { key: "err.network", detail: null, retry: again };
    if (e.status === 404) return { key: "err.expired", detail, retry: "none" };
    if (e.status === 413) return { key: "err.tooLarge", detail, retry: "none" };
    if (e.status === 422) return fromServerFailure(e.message, "err.badSettings");
    if (e.status === 409) return { key: "err.server", detail, retry: "reopen" };
    return { key: "err.server", detail, retry: again };
  }
  return { key: "err.server", detail: e instanceof Error ? e.message : String(e), retry: again };
}

/** A job the server reports as failed (`error` is the server's message, possibly empty). */
export function fromServerFailure(message: string | null | undefined, fallback: MessageKey = "err.failedServer"): JobError {
  const m = (message ?? "").trim();
  if (!m || m === "failed") return { key: "err.failedUnknown", detail: null, retry: "reupload" };
  if (/中斷/.test(m)) return { key: "err.interrupted", detail: m, retry: "reupload" };
  if (/已被刪除/.test(m)) return { key: "err.jobDeleted", detail: m, retry: "none" };
  if (/密碼/.test(m)) return { key: "err.encrypted", detail: m, retry: "none" };
  if (/沒有任何頁面/.test(m)) return { key: "err.noPages", detail: m, retry: "none" };
  if (/頁數超過/.test(m)) return { key: "err.tooManyPages", detail: m, retry: "none" };
  if (/上限/.test(m) && /MB/.test(m)) return { key: "err.tooLarge", detail: m, retry: "none" };
  if (/無法開啟|已損壞|打不開|不支援的檔案|不是 PDF/.test(m)) return { key: "err.fileBroken", detail: m, retry: "none" };
  if (/尺寸.*(之間|一起填|大於 0)/.test(m)) return { key: "err.badSize", detail: m, retry: "none" };
  if (/格式/.test(m) && fallback === "err.badSettings") return { key: "err.badFormat", detail: m, retry: "none" };
  return { key: fallback, detail: m, retry: fallback === "err.badSettings" ? "none" : "reupload" };
}

/** Problems the server attaches to elements that the UI already shows in its own words (e.g. low confidence). */
const ALREADY_SHOWN = /^信心值\s*[\d.]+\s*偏低$/;

export function visibleIssues(issues: string[]): string[] {
  return issues.filter((m) => !ALREADY_SHOWN.test(m.trim()));
}

/** Does the text contain CJK characters (so it should be marked lang="zh-Hant" in the English UI)? */
export function hasCjk(s: string): boolean {
  return /[㐀-鿿豈-﫿　-〿＀-￯]/.test(s);
}
