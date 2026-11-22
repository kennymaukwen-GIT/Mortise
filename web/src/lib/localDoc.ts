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
  kind: "pdf" | "image";
  pageCount: number;
  sizes: PageSize[];
  /** Returns an object URL of the page drawn about `widthPx` wide. Cached per width. */
  render(pageIndex: number, widthPx: number): Promise<string>;
  destroy(): void;
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
  const doc: PDFDocumentProxy = await task.promise;

  const sizes: PageSize[] = [];
  for (let i = 1; i <= doc.numPages; i++) {
    const p = await doc.getPage(i);
    const vp = p.getViewport({ scale: 1 });
    sizes.push({ width: vp.width, height: vp.height, unit: "pt" });
  }

  const cache = new Map<string, Promise<string>>();
  let chain: Promise<unknown> = Promise.resolve();
  let destroyed = false;

  const draw = async (pageIndex: number, widthPx: number): Promise<string> => {
    const page = await doc.getPage(pageIndex + 1);
    const vp1 = page.getViewport({ scale: 1 });
    const scale = widthPx / vp1.width;
    const vp = page.getViewport({ scale });
    const canvas = document.createElement("canvas");
    canvas.width = Math.max(1, Math.round(vp.width));
    canvas.height = Math.max(1, Math.round(vp.height));
    const ctx = canvas.getContext("2d");
    if (ctx) {
      ctx.fillStyle = "#fff";
      ctx.fillRect(0, 0, canvas.width, canvas.height);
    }
    await page.render({ canvas, viewport: vp }).promise;
    return canvasToUrl(canvas);
  };

  return {
    kind: "pdf",
    pageCount: doc.numPages,
    sizes,
    render(pageIndex, widthPx) {
      const w = Math.max(16, Math.round(widthPx));
      const key = `${pageIndex}:${w}`;
      let hit = cache.get(key);
      if (!hit) {
        // One render at a time: pdf.js does not like parallel renders on one document.
        hit = chain.then(() => (destroyed ? Promise.reject(new Error("closed")) : draw(pageIndex, w)));
        chain = hit.catch(() => undefined);
        cache.set(key, hit);
        hit.catch(() => cache.delete(key));
      }
      return hit;
    },
    destroy() {
      destroyed = true;
      for (const p of cache.values()) p.then((u) => URL.revokeObjectURL(u)).catch(() => undefined);
      cache.clear();
      void task.destroy();
    },
  };
}

export async function openImage(file: Blob): Promise<LocalDoc> {
  const url = URL.createObjectURL(file);
  const size = await new Promise<{ w: number; h: number }>((resolve, reject) => {
    const img = new Image();
    img.onload = () => resolve({ w: img.naturalWidth, h: img.naturalHeight });
    img.onerror = () => reject(new Error("image"));
    img.src = url;
  }).catch((e) => {
    URL.revokeObjectURL(url);
    throw e;
  });
  return {
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
