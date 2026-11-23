// Types for the Mortise HTTP API (mortise/api/app.py). Field names follow the backend exactly.

export type OutputFormatId =
  | "pptx"
  | "google_slides"
  | "docx_fixed"
  | "docx_flow"
  | "xlsx"
  | "ai_pdf"
  | "idml"
  | "template"
  | "pdf"
  | "svg"
  | "svg_assets"
  // Unknown future formats are passed through untouched.
  | (string & {});

export interface Health {
  ok: boolean;
  verify_engine: string | null;
  formats: OutputFormatId[];
  max_file_mb?: number;
  accepted?: string[];
}

export interface FormatInfo {
  format: OutputFormatId;
  label: string; // Traditional Chinese
  label_en?: string;
  available: boolean;
}

export interface PageSizePreset {
  id: string;
  label: string;
  width_mm: number;
  height_mm: number;
}

export type JobStatusValue = "queued" | "running" | "done" | "failed" | (string & {});

export interface PhysicalSizeJson {
  width: number;
  height: number;
  unit: string;
}

export interface IntentJson {
  outputs?: OutputFormatId[];
  mode?: string;
  known_size?: PhysicalSizeJson | null;
  target_size?: PhysicalSizeJson | null;
  languages?: string[];
}

export interface JobStatus {
  job_id: string;
  filename: string | null;
  status: JobStatusValue;
  progress: number;
  stage: number | null;
  stage_name: string | null;
  page: number | null; // 1-based
  pages: number | null;
  error: string | null;
  rounds: unknown[];
  corrections: number;
  intent: IntentJson | null;
  created_at: number | null;
  allow_training: boolean;
}

/** One Server-Sent Event from /api/jobs/{id}/events. */
export interface JobEvent {
  stage?: number;
  stage_name?: string;
  page?: number | null; // 0-based page index as emitted by the pipeline
  status?: string; // start | done | cached for stage events; done | failed for the job
  progress?: number;
  t?: number;
  error?: string | null;
  final?: boolean;
}

export interface Box {
  x: number;
  y: number;
  w: number;
  h: number;
  rotation?: number;
}

export interface PageInfo {
  page: number; // 1-based
  input_type?: string;
  input_type_confidence?: number;
  width_pt?: number;
  height_pt?: number;
  size_source?: string;
  size_confidence?: number;
  notes?: string[];
  images: { original: string; result: string; overlay: string; source?: string };
  /** Result image URL per output format of this job. */
  result_images?: Record<string, string>;
  elements: string;
  /** Where the original page content sits on the output page (pt) when an output size was chosen. */
  placement?: Box | null;
  /** Size the system detected for the source page (pt), before any output scaling. */
  source_width_pt?: number;
  source_height_pt?: number;
  /** Photos: the paper's corners should be checked by the user (GET geometry says why). */
  needs_review?: boolean;
  /** Photos: how the paper was found (ok, cut_off, user, …); null for PDFs and screenshots. */
  quad_status?: string | null;
  /** URL of GET /api/jobs/{id}/pages/{n}/geometry. */
  geometry?: string;
}

/** A point in source-image pixels: [x, y]. */
export type Pt = [number, number];
/** Four corners in source-image pixels: top-left, top-right, bottom-right, bottom-left. */
export type Quad = [Pt, Pt, Pt, Pt];

export type QuadStatus = "ok" | "low_contrast" | "curved_edge" | "multiple_pages" | "cut_off" | "not_found" | "user" | "not_photo" | (string & {});

export interface QuadAlternative {
  corners: Quad;
  score: number;
  /** Plain-language short label from the server (Traditional Chinese). */
  label: string;
}

/** GET /api/jobs/{id}/pages/{n}/geometry: where the paper is in the photo. */
export interface PageGeometry {
  page: number;
  input_type: string;
  source: { width: number; height: number; image: string };
  corners: Quad;
  method: string;
  confidence: number;
  status: QuadStatus;
  corner_error_px: number[] | null;
  needs_review: boolean;
  /** One plain sentence (Traditional Chinese); "" when no check is needed. */
  review_reason: string;
  alternatives: QuadAlternative[];
  /** Index into alternatives to pre-select, or null. */
  preselect: number | null;
  /** Curved outline of the paper (source pixels) or null. */
  outline: Pt[] | null;
  dewarp: { applied: boolean; source: "measured" | "uvdoc_prior" | null | (string & {}); residual_px: number | null; needs_review: boolean };
  glare: number | null;
  /** The corners were set by the user. */
  user_set: boolean;
  notes: string[];
}

/** One feedback record as GET /api/jobs/{id}/feedback lists it. */
export interface FeedbackItem extends FeedbackIn {
  id: string;
  created?: string | number;
  [key: string]: unknown;
}

/** GET /api/jobs/{id}/feedback */
export interface FeedbackList {
  items: FeedbackItem[];
  corrections: number;
  consented: boolean;
}

/** GET /api/engine */
export interface EngineInfo {
  version: string;
  name?: string;
  components?: Record<string, unknown>;
  scorecard: EngineScorecard | null;
}

export interface EngineScorecard {
  version?: string;
  name?: string;
  created?: string;
  suite?: string;
  matches_current?: boolean;
  headline?: Record<string, { label?: string; mean?: number | null; n?: number; scope?: string; [key: string]: unknown }>;
  errors?: number;
  [key: string]: unknown;
}

export interface RunInfo {
  text: string;
  font: string;
  font_confidence?: number;
  verified_with?: string | null;
  size_pt: number;
  size_source?: string;
  bold?: boolean;
  italic?: boolean;
  color: string;
}

export interface ParagraphInfo {
  id: string;
  text: string;
  align: string;
  role: string;
  style_id?: string | null;
  runs: RunInfo[];
}

export interface ElementMeta {
  confidence: number | null;
  fields: Record<string, string>;
  issues: string[];
}

export interface TextBoxInfo extends ElementMeta {
  id: string;
  box: Box;
  z: number;
  paragraphs: ParagraphInfo[];
}

export interface TableCellInfo {
  row: number;
  col: number;
  row_span: number;
  col_span: number;
  paragraphs: ParagraphInfo[];
  id: string;
  value: unknown;
  number_format?: string | null;
  formula?: string | null;
  formula_candidate?: string | null;
}

export interface TableInfo extends ElementMeta {
  id: string;
  box: Box;
  z: number;
  rows: number;
  cols: number;
  header_rows: number;
  cells: TableCellInfo[];
}

export interface ImageInfo extends ElementMeta {
  id: string;
  box: Box;
  z: number;
  url: string | null;
}

export interface ShapeInfo extends ElementMeta {
  id: string;
  box: Box;
  z: number;
  geometry: string;
  fill: string | null;
}

export interface StyleInfo {
  id: string;
  name: string;
  font: string;
  size_pt: number;
  bold: boolean;
  italic: boolean;
  color: string;
}

export interface PageElements {
  page: number;
  width_pt: number;
  height_pt: number;
  background_color: string | null;
  text_boxes: TextBoxInfo[];
  tables: TableInfo[];
  images: ImageInfo[];
  shapes: ShapeInfo[];
  styles: StyleInfo[];
}

/** Summary of one output file (GET /api/jobs/{id}/report, field "outputs"). */
export interface OutputSummary {
  format: OutputFormatId;
  filename: string;
  selfcheck_ok: boolean;
  notes: string[];
  /** svg_assets only: number of vector graphics in the zip (0 = only a README). */
  vector_count?: number | null;
}

export interface FidelityReportJson {
  overall_score: number | null;
  outputs?: OutputSummary[];
  pages: { page_index: number; neutral_similarity?: number | null; target_similarity?: number | null }[];
  notes?: string[];
  round?: number;
  [key: string]: unknown;
}

export type CorrectionField = "size" | "corners" | "text" | "role" | "align" | "font_family" | "font_size_pt" | "color" | "formula";

export interface CorrectionIn {
  field: CorrectionField;
  value: unknown;
  element_id?: string | null;
  page?: number | null;
}

/** Tags the feedback API understands (POST /api/jobs/{id}/feedback). */
export type FeedbackTag =
  | "text_wrong"
  | "font_wrong"
  | "size_wrong"
  | "color_wrong"
  | "position_wrong"
  | "missing"
  | "extra"
  | "table_wrong"
  | "layout_broken"
  | "corners_wrong"
  | "other";

export interface FeedbackIn {
  rating?: number;
  page?: number;
  element_id?: string;
  correct?: boolean;
  comment?: string;
  tags?: string[];
}

export type ImageKind = "original" | "result" | "overlay" | "source";

export interface CreateJobInput {
  file: File;
  outputs: OutputFormatId[];
  mode?: "rescue" | "template";
  /** Original (known) size of the source document. */
  width?: number;
  height?: number;
  unit?: "mm" | "cm" | "in" | "pt" | "px";
  /** Output page size. */
  target_width?: number;
  target_height?: number;
  target_unit?: "mm" | "cm" | "in" | "pt";
  allow_training?: boolean;
}
