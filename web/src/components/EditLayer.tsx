// Clickable element boxes drawn over the Result view, and the text correction dialog.
import { useEffect, useId, useMemo, useRef, useState } from "react";
import type { Box, CorrectionIn, FeedbackIn, PageElements, TextBoxInfo } from "../api/types";
import { useI18n } from "../i18n/I18n";
import { hasCjk, visibleIssues } from "../lib/errors";
import { diffForm, initialForm, normalizeHex, type TextForm } from "../state/corrections";
import { tagsFor } from "../state/feedback";
import { ReportForm } from "./Feedback";
import { Caret, Close, Flag } from "./Icons";

/** "Something is wrong here" inside the element panel. */
export interface ElementReport {
  onSend: (fb: FeedbackIn) => Promise<void>;
  /** Notes saved for this job so far. */
  count: number;
  consented: boolean | null;
  /** Elements already reported as wrong. */
  reported: Set<string>;
}

const LOW = 0.7;

type AnyEl = { id: string; box: Box; confidence: number | null; issues: string[]; kind: "text" | "table" | "image" | "shape"; tb?: TextBoxInfo; label: string };

export function flatten(els: PageElements): AnyEl[] {
  const out: AnyEl[] = [];
  // "信心值 0.50 偏低" is already shown in the UI's own words (Confidence 50%), so it is left out of the issues.
  for (const t of els.text_boxes) out.push({ id: t.id, box: t.box, confidence: t.confidence, issues: visibleIssues(t.issues), kind: "text", tb: t, label: t.paragraphs.map((p) => p.text).join(" ").slice(0, 60) });
  for (const t of els.tables) out.push({ id: t.id, box: t.box, confidence: t.confidence, issues: visibleIssues(t.issues), kind: "table", label: `${t.rows}×${t.cols}` });
  for (const i of els.images) out.push({ id: i.id, box: i.box, confidence: i.confidence, issues: visibleIssues(i.issues), kind: "image", label: i.id });
  for (const s of els.shapes) out.push({ id: s.id, box: s.box, confidence: s.confidence, issues: visibleIssues(s.issues), kind: "shape", label: s.geometry });
  // Big things first so small boxes stay on top and clickable.
  return out.sort((a, b) => b.box.w * b.box.h - a.box.w * a.box.h);
}

export function isFlagged(e: { confidence: number | null; issues: string[] }): boolean {
  return e.issues.length > 0 || (e.confidence != null && e.confidence < LOW);
}

export function EditLayer({ elements, onSelect, selectedId }: { elements: PageElements; onSelect: (id: string) => void; selectedId: string | null }) {
  const { t, lang } = useI18n();
  const list = useMemo(() => flatten(elements), [elements]);
  const W = elements.width_pt || 1;
  const H = elements.height_pt || 1;
  return (
    <div className="edit-layer">
      {list.map((e) => {
        const flagged = isFlagged(e);
        const kindLabel = e.kind === "text" ? t("edit.kind.text") : t(`edit.kind.${e.kind}` as "edit.kind.table");
        // Server issue texts are Chinese; in the English UI they are left to the dialog (where they are marked as Chinese).
        const issues = lang === "zh-Hant" ? e.issues : e.issues.filter((m) => !hasCjk(m));
        const conf = e.confidence != null && e.confidence < LOW ? t("edit.confidence", { pct: Math.round(e.confidence * 100) }) : "";
        const reason = [conf, ...issues].filter(Boolean).join(" · ") || (e.issues.length ? t("edit.issues") : "");
        return (
          <button
            key={e.id}
            type="button"
            className={"el-box el-box--" + e.kind + (flagged ? " is-flagged" : "") + (selectedId === e.id ? " is-selected" : "")}
            style={{ left: `${(e.box.x / W) * 100}%`, top: `${(e.box.y / H) * 100}%`, width: `${(e.box.w / W) * 100}%`, height: `${(e.box.h / H) * 100}%` }}
            title={reason ? `${kindLabel} — ${reason}` : kindLabel}
            aria-label={t("edit.elementLabel", { kind: kindLabel, text: e.label }) + (reason ? ` (${reason})` : "")}
            data-element-id={e.id}
            onClick={(ev) => {
              ev.stopPropagation();
              onSelect(e.id);
            }}
          />
        );
      })}
    </div>
  );
}

export function EditDialog({ elements, elementId, page, onClose, onApply, report }: {
  elements: PageElements;
  elementId: string;
  page: number;
  onClose: () => void;
  onApply: (items: CorrectionIn[]) => Promise<void>;
  report?: ElementReport;
}) {
  const { t, lang } = useI18n();
  const uid = useId();
  const el = useMemo(() => flatten(elements).find((e) => e.id === elementId) ?? null, [elements, elementId]);
  const tb = el?.tb ?? null;
  const before = useMemo<TextForm | null>(() => (tb ? initialForm(tb) : null), [tb]);
  const [form, setForm] = useState<TextForm | null>(before);
  const [error, setError] = useState<string | null>(null);
  const [sending, setSending] = useState(false);
  const panel = useRef<HTMLDivElement>(null);
  // Only text boxes can be corrected here, so for anything else "something is wrong here" is open from the start.
  const [reportOpen, setReportOpen] = useState(!tb);

  useEffect(() => {
    setForm(before);
    setError(null);
  }, [before]);
  useEffect(() => setReportOpen(!tb), [elementId]); // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => {
    // Start in the first text field (the close button comes first in the markup).
    const first =
      panel.current?.querySelector<HTMLElement>("textarea") ?? panel.current?.querySelector<HTMLElement>("[data-tag]") ?? panel.current?.querySelector<HTMLElement>("input, select, button");
    first?.focus();
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") {
        e.stopPropagation();
        onClose();
      }
    };
    document.addEventListener("keydown", onKey, true);
    return () => document.removeEventListener("keydown", onKey, true);
  }, [elementId]); // eslint-disable-line react-hooks/exhaustive-deps

  // Keep Tab inside the dialog.
  const trap = (e: React.KeyboardEvent) => {
    if (e.key !== "Tab" || !panel.current) return;
    const f = Array.from(panel.current.querySelectorAll<HTMLElement>("textarea, input, select, button:not([disabled])"));
    if (!f.length) return;
    const first = f[0];
    const last = f[f.length - 1];
    if (e.shiftKey && document.activeElement === first) {
      e.preventDefault();
      last.focus();
    } else if (!e.shiftKey && document.activeElement === last) {
      e.preventDefault();
      first.focus();
    }
  };

  const fonts = useMemo(() => {
    const set = new Set<string>();
    elements.styles.forEach((s) => s.font && set.add(s.font));
    elements.text_boxes.forEach((b) => b.paragraphs.forEach((p) => p.runs.forEach((r) => r.font && set.add(r.font))));
    return [...set].sort();
  }, [elements]);

  if (!el) return null;
  const kindLabel = el.kind === "text" ? t("edit.kind.text") : t(`edit.kind.${el.kind}` as "edit.kind.table");
  const diff = tb && before && form ? diffForm(tb, before, form, page) : null;
  const changed = diff?.ok ? diff.items.length > 0 : true;

  const apply = async () => {
    if (!diff) return;
    if (!diff.ok) {
      setError(diff.field === "size" ? t("card.pairInvalid") : diff.field === "color" ? "#RRGGBB" : t("edit.font"));
      return;
    }
    if (!diff.items.length) return;
    setSending(true);
    setError(null);
    try {
      await onApply(diff.items);
      onClose();
    } catch (e) {
      setError(t("edit.failed", { msg: e instanceof Error ? e.message : String(e) }));
    } finally {
      setSending(false);
    }
  };

  return (
    <div className="dialog-backdrop" data-no-pan onPointerDown={(e) => e.target === e.currentTarget && onClose()}>
      <div ref={panel} className="dialog" role="dialog" aria-modal="true" aria-labelledby={uid + "-title"} onKeyDown={trap}>
        <div className="dialog__head">
          <h2 id={uid + "-title"} className="dialog__title">
            {tb ? t("edit.title") : kindLabel}
          </h2>
          <button type="button" className="icon-btn" onClick={onClose} aria-label={t("edit.close")}>
            <Close size={18} />
          </button>
        </div>

        {(el.issues.length > 0 || (el.confidence != null && el.confidence < LOW)) && (
          <div className="dialog__issues" role="note">
            <strong>{t("edit.issues")}</strong>
            <ul>
              {el.confidence != null && el.confidence < LOW && <li>{t("edit.confidence", { pct: Math.round(el.confidence * 100) })}</li>}
              {el.issues.map((m, i) => (
                <li key={i} lang={hasCjk(m) ? "zh-Hant" : undefined}>
                  {m}
                </li>
              ))}
            </ul>
          </div>
        )}

        {!tb || !form ? (
          <p className="dialog__note">
            {t("edit.notText", { kind: kindLabel.toLowerCase(), article: lang === "en" ? (/^[aeiou]/i.test(kindLabel) ? "an" : "a") : "" })}
          </p>
        ) : (
          <form
            className="dialog__form"
            onSubmit={(e) => {
              e.preventDefault();
              void apply();
            }}
          >
            {form.paragraphs.map((p, i) => (
              <label key={p.id} className="field">
                <span className="field__label">{form.paragraphs.length > 1 ? t("edit.paragraph", { n: i + 1 }) : t("edit.text")}</span>
                <textarea
                  className="field__input"
                  rows={Math.min(6, Math.max(2, p.text.split("\n").length + Math.floor(p.text.length / 40)))}
                  value={p.text}
                  onChange={(e) => setForm({ ...form, paragraphs: form.paragraphs.map((q, k) => (k === i ? { ...q, text: e.target.value } : q)) })}
                />
              </label>
            ))}
            <div className="field-row">
              <label className="field field--grow">
                <span className="field__label">{t("edit.font")}</span>
                <input className="field__input" list={uid + "-fonts"} value={form.font} onChange={(e) => setForm({ ...form, font: e.target.value })} />
                <datalist id={uid + "-fonts"}>
                  {fonts.map((f) => (
                    <option key={f} value={f} />
                  ))}
                </datalist>
              </label>
              <label className="field field--size">
                <span className="field__label">{t("edit.size")}</span>
                <input className="field__input" inputMode="decimal" value={form.size} onChange={(e) => setForm({ ...form, size: e.target.value })} />
              </label>
            </div>
            <div className="field-row">
              <label className="field">
                <span className="field__label">{t("edit.color")}</span>
                <span className="color-field">
                  <input
                    type="color"
                    className="color-field__swatch"
                    value={normalizeHex(form.color)?.toLowerCase() ?? "#000000"}
                    onChange={(e) => setForm({ ...form, color: e.target.value })}
                    aria-label={t("edit.color")}
                  />
                  <input className="field__input color-field__hex" value={form.color} onChange={(e) => setForm({ ...form, color: e.target.value })} aria-label={t("edit.color") + " (hex)"} />
                </span>
              </label>
              <label className="field field--grow">
                <span className="field__label">{t("edit.align")}</span>
                <span className="pill-select pill-select--field">
                  <select value={form.align} onChange={(e) => setForm({ ...form, align: e.target.value })}>
                    {(["left", "center", "right", "justify"] as const).map((a) => (
                      <option key={a} value={a}>
                        {t(`edit.align.${a}`)}
                      </option>
                    ))}
                  </select>
                  <Caret className="pill-select__caret" />
                </span>
              </label>
            </div>
            {error && (
              <p className="dialog__error" role="alert">
                {error}
              </p>
            )}
            <div className="dialog__actions">
              {!changed && <span className="dialog__hint">{t("edit.noChanges")}</span>}
              <button type="button" className="btn btn--outline-dark" onClick={onClose}>
                {t("edit.cancel")}
              </button>
              <button type="submit" className="btn btn--primary" disabled={!changed || sending}>
                {t("edit.apply")}
              </button>
            </div>
          </form>
        )}

        {report && (
          <section className="dialog__report" aria-label={t("fb.wrong")}>
            <button
              type="button"
              className={"report-toggle" + (reportOpen ? " is-open" : "")}
              aria-expanded={reportOpen}
              onClick={() => setReportOpen((v) => !v)}
              data-testid="report-toggle"
            >
              <Flag size={16} />
              <span>{t("fb.wrong")}</span>
              {report.reported.has(el.id) && <span className="report-toggle__done">{t("fb.alreadyReported")}</span>}
              <Caret className="report-toggle__caret" />
            </button>
            {reportOpen && (
              <ReportForm
                page={page}
                elementId={el.id}
                tags={tagsFor(el.kind)}
                onSend={report.onSend}
                count={report.count}
                consented={report.consented}
                autoFocus={!!tb}
                key={el.id}
              />
            )}
          </section>
        )}
      </div>
    </div>
  );
}
