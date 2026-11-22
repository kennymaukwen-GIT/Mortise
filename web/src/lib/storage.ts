// Small wrappers around localStorage. Everything here stays in this browser.
import type { Lang } from "../i18n/dict";

const KEY_LANG = "mortise.lang";
const KEY_TRAINING = "mortise.allowTraining";
const KEY_RECENT = "mortise.recentJobs";
const MAX_RECENT = 20;

export interface RecentJob {
  jobId: string;
  filename: string;
  format: string;
  createdAt: number; // ms since epoch
  score?: number | null;
  status?: string;
}

function read(key: string): string | null {
  try {
    return window.localStorage.getItem(key);
  } catch {
    return null;
  }
}

function write(key: string, value: string): void {
  try {
    window.localStorage.setItem(key, value);
  } catch {
    /* storage full or disabled: settings just are not remembered */
  }
}

export function loadLang(): Lang {
  const v = read(KEY_LANG);
  if (v === "en" || v === "zh-Hant") return v;
  const nav = typeof navigator !== "undefined" ? navigator.language || "" : "";
  return /^zh-(TW|HK|MO|Hant)/i.test(nav) ? "zh-Hant" : "en";
}

export function saveLang(lang: Lang): void {
  write(KEY_LANG, lang);
}

export function loadAllowTraining(): boolean {
  return read(KEY_TRAINING) === "true";
}

export function saveAllowTraining(v: boolean): void {
  write(KEY_TRAINING, v ? "true" : "false");
}

export function loadRecent(): RecentJob[] {
  const raw = read(KEY_RECENT);
  if (!raw) return [];
  try {
    const list = JSON.parse(raw) as unknown;
    if (!Array.isArray(list)) return [];
    return list.filter((j): j is RecentJob => !!j && typeof j === "object" && typeof (j as RecentJob).jobId === "string").slice(0, MAX_RECENT);
  } catch {
    return [];
  }
}

export function saveRecent(list: RecentJob[]): void {
  write(KEY_RECENT, JSON.stringify(list.slice(0, MAX_RECENT)));
}

/** Insert or update a job (newest first). */
export function upsertRecent(list: RecentJob[], job: RecentJob): RecentJob[] {
  const rest = list.filter((j) => j.jobId !== job.jobId);
  const prev = list.find((j) => j.jobId === job.jobId);
  return [{ ...prev, ...job }, ...rest].slice(0, MAX_RECENT);
}

export function removeRecent(list: RecentJob[], jobId: string): RecentJob[] {
  return list.filter((j) => j.jobId !== jobId);
}
