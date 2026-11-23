// Thin client for the Mortise API. Everything the UI needs from the server goes through here.
import type {
  CorrectionIn,
  CreateJobInput,
  EngineInfo,
  FeedbackIn,
  FeedbackList,
  FidelityReportJson,
  FormatInfo,
  Health,
  ImageKind,
  JobEvent,
  JobStatus,
  PageElements,
  PageGeometry,
  PageInfo,
  PageSizePreset,
} from "./types";

export class ApiError extends Error {
  readonly status: number;
  constructor(status: number, message: string) {
    super(message);
    this.name = "ApiError";
    this.status = status;
  }
}

const BASE: string = (import.meta.env?.VITE_API_BASE as string | undefined) ?? "";

export function apiUrl(path: string): string {
  return BASE + path;
}

/** Pull a human-readable message out of a FastAPI error body. */
export function errorMessage(body: unknown, fallback: string): string {
  if (body && typeof body === "object" && "detail" in body) {
    const detail = (body as { detail: unknown }).detail;
    if (typeof detail === "string") return detail;
    if (Array.isArray(detail)) {
      const parts = detail
        .map((d) => (d && typeof d === "object" && "msg" in d ? String((d as { msg: unknown }).msg) : ""))
        .filter(Boolean);
      if (parts.length) return parts.join("; ");
    }
  }
  return fallback;
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  let res: Response;
  try {
    res = await fetch(apiUrl(path), init);
  } catch (e) {
    throw new ApiError(0, e instanceof Error ? e.message : String(e));
  }
  if (!res.ok) {
    let body: unknown = null;
    try {
      body = await res.json();
    } catch {
      /* not JSON */
    }
    throw new ApiError(res.status, errorMessage(body, `HTTP ${res.status}`));
  }
  if (res.status === 204) return undefined as T;
  return (await res.json()) as T;
}

export function getHealth(): Promise<Health> {
  return request<Health>("/api/health");
}

export function getFormats(): Promise<FormatInfo[]> {
  return request<FormatInfo[]>("/api/formats");
}

export function getPageSizes(): Promise<PageSizePreset[]> {
  return request<PageSizePreset[]>("/api/page-sizes");
}

export function buildJobForm(input: CreateJobInput): FormData {
  const fd = new FormData();
  fd.append("file", input.file, input.file.name);
  fd.append("outputs", input.outputs.join(","));
  fd.append("mode", input.mode ?? "rescue");
  if (input.width && input.height) {
    fd.append("width", String(input.width));
    fd.append("height", String(input.height));
    fd.append("unit", input.unit ?? "mm");
  }
  if (input.target_width && input.target_height) {
    fd.append("target_width", String(input.target_width));
    fd.append("target_height", String(input.target_height));
    fd.append("target_unit", input.target_unit ?? "mm");
  }
  fd.append("allow_training", input.allow_training ? "true" : "false");
  return fd;
}

/**
 * Upload a file and create a job. Uses XHR so upload progress can be reported.
 * Aborting stops the upload; once the whole file has been sent the request is NOT aborted any more
 * (the server may already have created the job): it resolves normally and the caller, seeing its signal
 * aborted, deletes the job it no longer wants.
 */
export function createJob(input: CreateJobInput, opts: { onUpload?: (fraction: number) => void; signal?: AbortSignal } = {}): Promise<JobStatus> {
  return new Promise((resolve, reject) => {
    const xhr = new XMLHttpRequest();
    let sent = false;
    xhr.open("POST", apiUrl("/api/jobs"));
    xhr.responseType = "text";
    xhr.upload.onprogress = (e) => {
      if (e.lengthComputable && opts.onUpload) opts.onUpload(e.loaded / Math.max(1, e.total));
    };
    xhr.upload.onload = () => {
      sent = true;
    };
    xhr.onload = () => {
      let body: unknown = null;
      try {
        body = JSON.parse(xhr.responseText);
      } catch {
        /* ignore */
      }
      if (xhr.status >= 200 && xhr.status < 300 && body) resolve(body as JobStatus);
      else reject(new ApiError(xhr.status, errorMessage(body, `HTTP ${xhr.status}`)));
    };
    xhr.onerror = () => reject(new ApiError(0, "network"));
    xhr.onabort = () => reject(new DOMException("aborted", "AbortError"));
    if (opts.signal) {
      if (opts.signal.aborted) return reject(new DOMException("aborted", "AbortError"));
      opts.signal.addEventListener("abort", () => !sent && xhr.abort(), { once: true });
    }
    xhr.send(buildJobForm(input));
  });
}

export function getJob(id: string): Promise<JobStatus> {
  return request<JobStatus>(`/api/jobs/${encodeURIComponent(id)}`);
}

export function getPages(id: string): Promise<PageInfo[]> {
  return request<PageInfo[]>(`/api/jobs/${encodeURIComponent(id)}/pages`);
}

export function getReport(id: string): Promise<FidelityReportJson> {
  return request<FidelityReportJson>(`/api/jobs/${encodeURIComponent(id)}/report`);
}

export function getElements(id: string, page: number): Promise<PageElements> {
  return request<PageElements>(`/api/jobs/${encodeURIComponent(id)}/pages/${page}/elements`);
}

export function postCorrections(id: string, items: CorrectionIn[]): Promise<JobStatus> {
  return request<JobStatus>(`/api/jobs/${encodeURIComponent(id)}/corrections`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(items),
  });
}

export function postFeedback(id: string, fb: FeedbackIn): Promise<{ ok: boolean; id: string }> {
  return request(`/api/jobs/${encodeURIComponent(id)}/feedback`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(fb),
  });
}

/** Where the paper is in a photo (409 while page step ② has not finished, or is re-running after new corners). */
export function getGeometry(id: string, page: number): Promise<PageGeometry> {
  return request<PageGeometry>(`/api/jobs/${encodeURIComponent(id)}/pages/${page}/geometry`);
}

/** Feedback already sent for a job, the number of corrections, and whether the job may be used to improve recognition. */
export function getFeedback(id: string): Promise<FeedbackList> {
  return request<FeedbackList>(`/api/jobs/${encodeURIComponent(id)}/feedback`);
}

export function feedbackExportUrl(id: string): string {
  return apiUrl(`/api/jobs/${encodeURIComponent(id)}/feedback/export`);
}

export function getEngine(): Promise<EngineInfo> {
  return request<EngineInfo>("/api/engine");
}

/** File name from a Content-Disposition header (prefers the UTF-8 filename*). */
export function dispositionName(header: string | null, fallback: string): string {
  if (!header) return fallback;
  const star = /filename\*\s*=\s*(?:UTF-8|utf-8)''([^;]+)/.exec(header);
  if (star) {
    try {
      return decodeURIComponent(star[1].trim().replace(/^"|"$/g, ""));
    } catch {
      /* fall through */
    }
  }
  const plain = /filename\s*=\s*"?([^";]+)"?/.exec(header);
  return plain ? plain[1].trim() : fallback;
}

/** Download a job's feedback bundle (zip). Fetched first so a failure can be shown instead of saving an error page. */
export async function downloadFeedbackBundle(id: string, fallbackName: string): Promise<string> {
  let res: Response;
  try {
    res = await fetch(feedbackExportUrl(id), { credentials: "same-origin" });
  } catch (e) {
    throw new ApiError(0, e instanceof Error ? e.message : String(e));
  }
  if (!res.ok) {
    let body: unknown = null;
    try {
      body = await res.json();
    } catch {
      /* not JSON */
    }
    throw new ApiError(res.status, errorMessage(body, `HTTP ${res.status}`));
  }
  const name = dispositionName(res.headers.get("Content-Disposition"), fallbackName);
  const blob = await res.blob();
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = name;
  a.style.display = "none";
  document.body.appendChild(a);
  a.click();
  a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 30000);
  return name;
}

export function deleteJob(id: string): Promise<void> {
  return request<void>(`/api/jobs/${encodeURIComponent(id)}`, { method: "DELETE" });
}

export function pageImageUrl(id: string, page: number, kind: ImageKind, opts: { width?: number; format?: string; v?: number } = {}): string {
  const q = new URLSearchParams({ kind });
  if (opts.format && kind !== "original" && kind !== "source") q.set("format", opts.format);
  if (opts.width) q.set("width", String(Math.round(opts.width)));
  if (opts.v) q.set("v", String(opts.v)); // cache-buster after a re-run
  return apiUrl(`/api/jobs/${encodeURIComponent(id)}/pages/${page}/image?${q.toString()}`);
}

export function downloadUrl(id: string, what: string): string {
  return apiUrl(`/api/jobs/${encodeURIComponent(id)}/download/${encodeURIComponent(what)}`);
}

/** Retry a request a few times (with back-off) when the network drops or the server answers 5xx. */
export async function withRetry<T>(fn: () => Promise<T>, stop: () => boolean = () => false, delays: number[] = [500, 1500, 4000]): Promise<T> {
  for (let i = 0; ; i++) {
    try {
      return await fn();
    } catch (e) {
      const transient = e instanceof ApiError && (e.status === 0 || e.status >= 500);
      if (!transient || i >= delays.length || stop()) throw e;
      await new Promise((r) => setTimeout(r, delays[i]));
    }
  }
}

export interface JobWatcher {
  close(): void;
}

/**
 * Follow a job's progress. Uses Server-Sent Events and falls back to polling
 * GET /api/jobs/{id} if the stream breaks. `onFinal` is called exactly once.
 */
export function watchJob(
  id: string,
  handlers: { onEvent: (e: JobEvent) => void; onFinal: (status: JobStatus) => void; onError: (err: unknown) => void },
  opts: { pollMs?: number } = {},
): JobWatcher {
  let closed = false;
  let es: EventSource | null = null;
  let timer: ReturnType<typeof setTimeout> | null = null;
  let finishing = false;

  const finish = async () => {
    if (closed || finishing) return;
    finishing = true;
    es?.close();
    try {
      const st = await withRetry(() => getJob(id), () => closed);
      if (!closed) {
        closed = true;
        handlers.onFinal(st);
      }
    } catch (e) {
      if (!closed) {
        closed = true;
        handlers.onError(e);
      }
    }
  };

  const poll = async () => {
    if (closed) return;
    try {
      const st = await getJob(id);
      if (closed) return;
      handlers.onEvent({ stage: st.stage ?? undefined, stage_name: st.stage_name ?? undefined, page: st.page != null ? st.page - 1 : null, progress: st.progress, status: "poll" });
      if (st.status === "done" || st.status === "failed") {
        closed = true;
        handlers.onFinal(st);
        return;
      }
    } catch (e) {
      if (e instanceof ApiError && e.status === 404) {
        closed = true;
        handlers.onError(e);
        return;
      }
    }
    timer = setTimeout(poll, opts.pollMs ?? 1000);
  };

  if (typeof EventSource !== "undefined") {
    es = new EventSource(apiUrl(`/api/jobs/${encodeURIComponent(id)}/events`));
    es.onmessage = (m) => {
      let e: JobEvent;
      try {
        e = JSON.parse(m.data) as JobEvent;
      } catch {
        return;
      }
      if (e.final) {
        void finish();
        return;
      }
      handlers.onEvent(e);
    };
    es.onerror = () => {
      if (closed || finishing) return;
      // Stream broke (proxy, network, server restart): switch to polling.
      es?.close();
      es = null;
      void poll();
    };
  } else {
    void poll();
  }

  return {
    close() {
      closed = true;
      es?.close();
      if (timer) clearTimeout(timer);
    },
  };
}
