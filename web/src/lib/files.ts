// Checks done in the browser before anything is uploaded.
export type FileKind = "pdf" | "image";

export type FileCheck =
  | { ok: true; kind: FileKind; mime: string }
  | { ok: false; reason: "type" }
  | { ok: false; reason: "size"; sizeMb: number };

const EXT_MIME: Record<string, string> = {
  pdf: "application/pdf",
  png: "image/png",
  jpg: "image/jpeg",
  jpeg: "image/jpeg",
  jpe: "image/jpeg",
};

/** Browsers sometimes report an empty or generic type; fall back to the extension. */
export function mimeOf(file: { name: string; type: string }): string {
  const t = (file.type || "").toLowerCase();
  if (t && t !== "application/octet-stream") return t === "image/jpg" || t === "image/pjpeg" ? "image/jpeg" : t;
  const ext = file.name.toLowerCase().split(".").pop() ?? "";
  return EXT_MIME[ext] ?? t;
}

export function checkFile(file: { name: string; type: string; size: number }, accepted: string[], maxMb: number): FileCheck {
  const mime = mimeOf(file);
  if (!accepted.includes(mime)) return { ok: false, reason: "type" };
  const sizeMb = file.size / (1024 * 1024);
  if (sizeMb > maxMb) return { ok: false, reason: "size", sizeMb };
  return { ok: true, kind: mime === "application/pdf" ? "pdf" : "image", mime };
}

/** Sniff the first bytes so a renamed file is caught before upload. */
export async function sniff(file: Blob): Promise<"pdf" | "png" | "jpeg" | null> {
  const head = new Uint8Array(await file.slice(0, 1024).arrayBuffer());
  const s = String.fromCharCode(...head.slice(0, 1024));
  if (s.includes("%PDF-")) return "pdf";
  if (head[0] === 0x89 && head[1] === 0x50 && head[2] === 0x4e && head[3] === 0x47) return "png";
  if (head[0] === 0xff && head[1] === 0xd8 && head[2] === 0xff) return "jpeg";
  return null;
}

export function shortName(name: string, max = 22): string {
  if (name.length <= max) return name;
  const dot = name.lastIndexOf(".");
  const ext = dot > 0 && name.length - dot <= 6 ? name.slice(dot) : "";
  const keep = max - ext.length - 1;
  return name.slice(0, Math.max(4, keep)) + "…" + ext;
}
