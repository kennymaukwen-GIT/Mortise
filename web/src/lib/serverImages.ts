// Preview pictures from the server, fetched (not just put in an <img>) so the X-Mortise-Render header can be read:
// it says which engine drew a result (the output file itself, LibreOffice, or Mortise's own neutral drawing).
import { ApiError } from "../api/client";

export interface ServerImage {
  /** Object URL of the picture. */
  src: string;
  /** Value of X-Mortise-Render ("pdf", "svg-resvg", "pptx-libreoffice", "neutral", "original", …) or null. */
  render: string | null;
}

/** Pictures kept (object URLs); the least recently used are released beyond this. */
const MAX_CACHED = 40;
const cache = new Map<string, Promise<ServerImage>>();
const settled = new Set<string>();

function trim(keep: string) {
  for (const key of [...cache.keys()]) {
    if (cache.size <= MAX_CACHED) break;
    if (key === keep || !settled.has(key)) continue;
    const p = cache.get(key);
    cache.delete(key);
    settled.delete(key);
    p?.then((im) => URL.revokeObjectURL(im.src)).catch(() => undefined);
  }
}

export function loadServerImage(url: string): Promise<ServerImage> {
  const hit = cache.get(url);
  if (hit) {
    cache.delete(url);
    cache.set(url, hit); // most recently used
    return hit;
  }
  const p = (async () => {
    let res: Response;
    try {
      res = await fetch(url, { credentials: "same-origin" });
    } catch (e) {
      throw new ApiError(0, e instanceof Error ? e.message : String(e));
    }
    if (!res.ok) {
      let detail = `HTTP ${res.status}`;
      try {
        const body = (await res.json()) as { detail?: unknown };
        if (typeof body?.detail === "string") detail = body.detail;
      } catch {
        /* not JSON */
      }
      throw new ApiError(res.status, detail);
    }
    const blob = await res.blob();
    return { src: URL.createObjectURL(blob), render: res.headers.get("X-Mortise-Render") };
  })();
  cache.set(url, p);
  p.then(
    () => settled.add(url),
    () => {
      cache.delete(url);
    },
  );
  trim(url);
  return p;
}

/** Engines that draw the output file the way its target application would (or the file itself). */
export type RenderKind = "file" | "standin" | "neutral" | "unknown";

/**
 * How close a result preview is to what the user will see in the target application.
 * "file": the output file drawn directly (PDF via PDFium, SVG via resvg) — what a viewer shows;
 * "standin": LibreOffice standing in for PowerPoint; "neutral": Mortise drew its own document (not the output file).
 */
export function renderKind(render: string | null | undefined): RenderKind {
  if (!render) return "unknown";
  if (render === "neutral") return "neutral";
  if (render.endsWith("-libreoffice")) return "standin";
  if (render === "pdf" || render === "ai_pdf" || render.startsWith("svg")) return "file";
  return "unknown";
}
