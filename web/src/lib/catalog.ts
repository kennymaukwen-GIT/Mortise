// Fallback data used only while the server does not (yet) provide it.
import type { FormatInfo, PageSizePreset } from "../api/types";

/** English names agreed in the API contract (label_en). */
export const FORMAT_LABELS_EN: Record<string, string> = {
  pdf: "Editable PDF (.pdf)",
  svg: "SVG (.svg)",
  svg_assets: "SVG graphics only (.zip)",
  pptx: "PowerPoint (.pptx)",
  docx_fixed: "Word, fixed layout (.docx)",
  docx_flow: "Word, reflowable (.docx)",
  xlsx: "Excel (.xlsx)",
  ai_pdf: "PDF for Illustrator (.pdf)",
  idml: "InDesign (.idml)",
  google_slides: "Google Slides (.pptx)",
  template: "Layout template (.pptx)",
};

/** Short name shown inside the pill (the mockup shows just "SVG"). */
export const FORMAT_SHORT: Record<string, string> = {
  pdf: "PDF",
  svg: "SVG",
  svg_assets: "SVG graphics",
  pptx: "PowerPoint",
  docx_fixed: "Word (fixed)",
  docx_flow: "Word (reflow)",
  xlsx: "Excel",
  ai_pdf: "Illustrator PDF",
  idml: "InDesign",
  google_slides: "Google Slides",
  template: "Template",
};

export function formatLabel(f: FormatInfo, lang: "en" | "zh-Hant"): string {
  if (lang === "zh-Hant") return f.label || FORMAT_LABELS_EN[f.format] || f.format;
  return f.label_en || FORMAT_LABELS_EN[f.format] || f.format;
}

/** Same list and order as the [output_sizes] section of the server config. Portrait except the slide sizes. */
export const FALLBACK_PAGE_SIZES: PageSizePreset[] = [
  { id: "A0", label: "A0", width_mm: 841, height_mm: 1189 },
  { id: "A1", label: "A1", width_mm: 594, height_mm: 841 },
  { id: "A2", label: "A2", width_mm: 420, height_mm: 594 },
  { id: "A3", label: "A3", width_mm: 297, height_mm: 420 },
  { id: "A4", label: "A4", width_mm: 210, height_mm: 297 },
  { id: "A5", label: "A5", width_mm: 148, height_mm: 210 },
  { id: "A6", label: "A6", width_mm: 105, height_mm: 148 },
  { id: "B4", label: "B4", width_mm: 257, height_mm: 364 },
  { id: "B5", label: "B5", width_mm: 182, height_mm: 257 },
  { id: "Letter", label: "Letter", width_mm: 215.9, height_mm: 279.4 },
  { id: "Legal", label: "Legal", width_mm: 215.9, height_mm: 355.6 },
  { id: "Tabloid", label: "Tabloid", width_mm: 279.4, height_mm: 431.8 },
  { id: "Slide 16:9", label: "Slide 16:9", width_mm: 338.67, height_mm: 190.5 },
  { id: "Slide 4:3", label: "Slide 4:3", width_mm: 254, height_mm: 190.5 },
];

export const DEFAULT_ACCEPTED = ["application/pdf", "image/png", "image/jpeg"];
export const DEFAULT_MAX_FILE_MB = 100;
