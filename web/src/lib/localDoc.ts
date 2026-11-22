// In-browser preview of the chosen file, before (and without) any upload.
// PDFs are drawn with pdf.js; PNG/JPEG are shown as they are.
import type { PDFDocumentProxy } from "pdfjs-dist";

export interface PageSize {
  width: number;
  height: number;
  /** "pt" for PDF pages (physical size known), "px" for images (aspect ratio only). */
  unit: "pt" | "px";
}

export interface LocalDoc {
  /** Unique per opened file, so pictures of an earlier file are never mistaken for this one. */
  id: number;
  kind: "pdf" | "image";
  pageCount: number;
  sizes: PageSize[];
  /**
   * Returns an object URL of the page drawn about `widthPx` wide. Cached per width.
   * "main" requests (the page in the frame) go before "thumb" requests, newest first; a main request that is no
   * longer the newest when its turn comes (the user paged on) is skipped and rejected with Error("stale").
   */
  render(pageIndex: number, widthPx: number, priority?: "main" | "thumb"): Promise<string>;
  destroy(): void;
}

/** Why a file could not be opened in the browser. */
export class OpenError extends Error {
  readonly reason: "encrypted" | "empty" | "broken";
  constructor(reason: "encrypted" | "empty" | "broken", message?: string) {
    super(message ?? reason);
    this.reason = reason;
  }
}

let nextId = 1;
/** Rendered pages kept per document (object URLs); older ones are released. */
const MAX_CACHED = 48;
/** Largest canvas drawn (pixels). iOS Safari refuses canvases above about 16.7 million pixels. */
export const MAX_CANVAS_PIXELS = 16_000_000;

/** Canvas size for a page of `w`×`h` drawn `widthPx` wide, kept under MAX_CANVAS_PIXELS. */
export function canvasSize(w: number, h: number, widthPx: number): { width: number; height: number; scale: number } {
  let scale = widthPx / w;
  if (w * scale * h * scale > MAX_CANVAS_PIXELS) scale = Math.sqrt(MAX_CANVAS_PIXELS / (w * h));
  return { width: Math.max(1, Math.round(w * scale)), height: Math.max(1, Math.round(h * scale)), scale };
}

let pdfjsPromise: Promise<typeof import("pdfjs-dist")> | null = null;

async function loadPdfjs() {
  if (!pdfjsPromise) {
    pdfjsPromise = (async () => {
      const [lib, worker] = await Promise.all([import("pdfjs-dist"), import("pdfjs-dist/build/pdf.worker.min.mjs?url")]);
      lib.GlobalWorkerOptions.workerSrc = worker.default;
      return lib;
    })();
  }
  return pdfjsPromise;
}

function canvasToUrl(canvas: HTMLCanvasElement): Promise<string> {
  return new Promise((resolve, reject) => {
    canvas.toBlob((b) => (b ? resolve(URL.createObjectURL(b)) : reject(new Error("canvas"))), "image/png");
  });
}

export async function openPdf(data: ArrayBuffer): Promise<LocalDoc> {
  const pdfjs = await loadPdfjs();
  const base = new URL(import.meta.env.BASE_URL || "/", window.location.href);
  const task = pdfjs.getDocument({
    data: new Uint8Array(data),
    cMapUrl: new URL("pdfjs/cmaps/", base).toString(),
    cMapPacked: true,
    standardFontDataUrl: new URL("pdfjs/standard_fonts/", base).toString(),
    wasmUrl: new URL("pdfjs/wasm/", base).toString(),
    iccUrl: new URL("pdfjs/iccs/", base).toString(),
    isEvalSupported: false,
  } as Parameters<typeof pdfjs.getDocument>[0]);
  let doc: PDFDocumentProxy;
  try {
    doc = await task.promise;
  } catch (e) {
    void task.destroy();
    const name = (e as { name?: string } | null)?.name;
    throw new OpenError(name === "PasswordException" ? "encrypted" : "broken", e instanceof Error ? e.message : String(e));
  }
  if (doc.numPages < 1) {
    void task.destroy();
    throw new OpenError("empty");
  }

  const sizes: PageSize[] = [];
  for (let i = 1; i <= doc.numPages; i++) {
    const p = await doc.getPage(i);
    const vp = p.getViewport({ scale: 1 });
    sizes.push({ width: vp.width, height: vp.height, unit: "pt" });
  }

  // Rendered pages: key → object URL promise, in least-recently-used order (Map keeps insertion order).
  const cache = new Map<string, Promise<string>>();
  const settled = new Set<string>(); // keys whose render finished (only those are ever evicted)
  // Waiting renders. pdf.js does not like parallel renders on one document, so they run one at a time:
  // the page in the frame first (newest request first), thumbnails after.
  type Job = { key: string; pageIndex: number; width: number; main: boolean; seq: number; resolve: (u: string) => void; reject: (e: unknown) => void };
  const queue: Job[] = [];
  let running = false;
  let destroyed = false;
  let seq = 0;
  let latestMain = "";

  const draw = async (pageIndex: number, widthPx: number): Promise<string> => {
    const page = await doc.getPage(pageIndex + 1);
    const vp1 = page.getViewport({ scale: 1 });
    const { width, height, scale } = canvasSize(vp1.width, vp1.height, widthPx);
    const vp = page.getViewport({ scale });
    const canvas = document.createElement("canvas");
    canvas.width = width;
    canvas.height = height;
    const ctx = canvas.getContext("2d");
    if (ctx) {
      ctx.fillStyle = "#fff";
      ctx.fillRect(0, 0, canvas.width, canvas.height);
    }
    await page.render({ canvas, viewport: vp }).promise;
    const url = await canvasToUrl(canvas);
    canvas.width = canvas.height = 0; // free the bitmap now (matters on phones)
    return url;
  };

  const release = (key: string) => {
    const p = cache.get(key);
    cache.delete(key);
    settled.delete(key);
    p?.then((u) => URL.revokeObjectURL(u)).catch(() => undefined);
  };
  const trim = () => {
    if (cache.size <= MAX_CACHED) return;
    for (const key of [...cache.keys()]) {
      if (cache.size <= MAX_CACHED) break;
      if (settled.has(key) && key !== latestMain) release(key);
    }
  };

  const pump = async () => {
    if (running) return;
    running = true;
    while (queue.length && !destroyed) {
      // Main renders before thumbnails; within each, the newest request first.
      queue.sort((a, b) => (a.main === b.main ? b.seq - a.seq : a.main ? -1 : 1));
      const job = queue.shift()!;
      if (job.main && job.key !== latestMain) {
        cache.delete(job.key);
        job.reject(new Error("stale"));
        continue;
      }
      try {
        const url = await draw(job.pageIndex, job.width);
        if (destroyed || cache.get(job.key) === undefined) URL.revokeObjectURL(url);
        else settled.add(job.key);
        job.resolve(url);
      } catch (e) {
        cache.delete(job.key);
        job.reject(e);
      }
    }
    for (const job of queue.splice(0)) job.reject(new Error("closed"));
    running = false;
  };

  return {
    id: nextId++,
    kind: "pdf",
    pageCount: doc.numPages,
    sizes,
    render(pageIndex, widthPx, priority = "main") {
      const w = Math.max(16, Math.round(widthPx));
      const key = `${pageIndex}:${w}`;
      const main = priority === "main";
      if (main) latestMain = key;
      const hit = cache.get(key);
      if (hit) {
        cache.delete(key);
        cache.set(key, hit); // most recently used
        return hit;
      }
      if (destroyed) return Promise.reject(new Error("closed"));
      const promise = new Promise<string>((resolve, reject) => queue.push({ key, pageIndex, width: w, main, seq: ++seq, resolve, reject }));
      cache.set(key, promise);
      promise.catch(() => undefined);
      trim();
      void pump();
      return promise;
    },
    destroy() {
      destroyed = true;
      for (const key of [...cache.keys()]) release(key);
      void task.destroy();
    },
  };
}

export async function openImage(file: Blob): Promise<LocalDoc> {
  const url = URL.createObjectURL(file);
  const size = await new Promise<{ w: number; h: number }>((resolve, reject) => {
    const img = new Image();
    img.onload = () => resolve({ w: img.naturalWidth, h: img.naturalHeight });
    img.onerror = () => reject(new OpenError("broken", "image"));
    img.src = url;
  }).catch((e) => {
    URL.revokeObjectURL(url);
    throw e;
  });
  return {
    id: nextId++,
    kind: "image",
    pageCount: 1,
    sizes: [{ width: size.w, height: size.h, unit: "px" }],
    render: () => Promise.resolve(url),
    destroy: () => URL.revokeObjectURL(url),
  };
}

export async function openLocal(file: File, kind: "pdf" | "image"): Promise<LocalDoc> {
  return kind === "pdf" ? openPdf(await file.arrayBuffer()) : openImage(file);
}
