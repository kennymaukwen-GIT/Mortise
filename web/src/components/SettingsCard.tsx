import { useId, useRef, useState, type Dispatch } from "react";
import { downloadUrl } from "../api/client";
import type { FormatInfo, PageSizePreset } from "../api/types";
import { useI18n } from "../i18n/I18n";
import { formatLabel, shortFormatLabel } from "../lib/catalog";
import { fmtMm, orient, ptToMm, SIZE_MAX_MM, SIZE_MIN_MM, type Orientation } from "../lib/units";
import { blocks, effectiveOrientation, resolveOrigin, resolveTarget, sourceOrientation } from "../state/derive";
import type { Action, State } from "../state/store";
import { ArrowCircle, Caret, Landscape, Portrait } from "./Icons";

export interface SettingsCardProps {
  state: State;
  dispatch: Dispatch<Action>;
  formats: FormatInfo[] | null;
  formatsError: string | null;
  presets: PageSizePreset[];
  onConvert: () => void;
  onConvertAgain: () => void;
}

function PairInputs(props: {
  idPrefix: string;
  w: string;
  h: string;
  placeholderW: string;
  placeholderH: string;
  labelW: string;
  labelH: string;
  invalid: boolean;
  describedBy?: string;
  disabled?: boolean;
  onChange: (w: string, h: string) => void;
  onBlur: () => void;
}) {
  const { t } = useI18n();
  return (
    <div className="pair">
      <span className="pair__letter" aria-hidden="true">
        {t("card.w")}
      </span>
      <input
        id={props.idPrefix + "-w"}
        className="pill-input"
        inputMode="decimal"
        autoComplete="off"
        aria-label={props.labelW}
        aria-invalid={props.invalid || undefined}
        aria-describedby={props.describedBy}
        placeholder={props.placeholderW}
        value={props.w}
        disabled={props.disabled}
        onChange={(e) => props.onChange(e.target.value, props.h)}
        onBlur={props.onBlur}
      />
      <span className="pair__letter" aria-hidden="true">
        {t("card.h")}
      </span>
      <input
        id={props.idPrefix + "-h"}
        className="pill-input"
        inputMode="decimal"
        autoComplete="off"
        aria-label={props.labelH}
        aria-invalid={props.invalid || undefined}
        aria-describedby={props.describedBy}
        placeholder={props.placeholderH}
        value={props.h}
        disabled={props.disabled}
        onChange={(e) => props.onChange(props.w, e.target.value)}
        onBlur={props.onBlur}
      />
      <span className="pair__unit">{t("card.mm")}</span>
    </div>
  );
}

export function SettingsCard({ state, dispatch, formats, formatsError, presets, onConvert, onConvertAgain }: SettingsCardProps) {
  const { t, lang } = useI18n();
  const uid = useId();
  const [touched, setTouched] = useState({ origin: false, target: false });
  const targetW = useRef<HTMLDivElement>(null);
  const s = state.settings;
  const busy = state.phase === "uploading" || state.phase === "converting" || state.phase === "opening";
  const hasFile = !!state.file;
  const src = sourceOrientation(state.doc, state.pages);

  const origin = resolveOrigin(s);
  const target = resolveTarget(s, presets, src);
  const range = { min: SIZE_MIN_MM, max: SIZE_MAX_MM };
  // Out-of-range sizes are flagged as soon as they are typed (they would only fail on the server).
  const originMsg =
    origin.kind === "range"
      ? t("card.rangeOrigin", range)
      : touched.origin && (origin.kind === "incomplete" ? t("card.pairIncomplete") : origin.kind === "invalid" ? t("card.pairInvalid") : null);
  const targetMsg =
    s.sizeMode === "custom" &&
    (target.kind === "range"
      ? t("card.rangeTarget", range)
      : touched.target && (target.kind === "incomplete" ? t("card.pairIncomplete") : target.kind === "invalid" ? t("card.pairInvalid") : null));

  // Placeholders: the PDF's own page size for Origin, the preset's size for Size.
  const page0 = state.doc?.sizes[0];
  const auto = t("card.auto");
  const originPh = page0 && page0.unit === "pt" ? { w: fmtMm(ptToMm(page0.width)), h: fmtMm(ptToMm(page0.height)) } : { w: auto, h: auto };
  const preset = presets.find((p) => p.id === s.presetId);
  const orientation: Orientation | null = s.sizeMode === "preset" && preset ? effectiveOrientation(s, preset, src) : null;
  const presetDims = preset && orientation ? orient(preset.width_mm, preset.height_mm, orientation) : null;
  const targetPh = s.sizeMode === "preset" && presetDims ? { w: fmtMm(presetDims.width), h: fmtMm(presetDims.height) } : { w: "—", h: "—" };

  const available = (formats ?? []).filter((f) => f.available);
  const sizeValue = s.sizeMode === "original" ? "original" : s.sizeMode === "custom" ? "custom" : s.presetId ?? "original";
  const uploading = state.phase === "uploading";
  const pct = Math.round((uploading ? (state.progress.upload ?? 0) : state.progress.fraction) * 100);
  const current = (formats ?? []).find((f) => f.format === s.format);
  const shortLabel = current ? shortFormatLabel(formatLabel(current, lang)) : null;

  const convert = () => {
    setTouched({ origin: true, target: true });
    if (blocks(origin) || blocks(target)) return;
    onConvert();
  };

  // Orientation radio group (WAI-ARIA): arrow keys move the choice, only the chosen button is in the Tab order.
  const orientKey = (e: React.KeyboardEvent) => {
    const keys: Record<string, Orientation> = { ArrowLeft: "portrait", ArrowUp: "portrait", ArrowRight: "landscape", ArrowDown: "landscape" };
    const o = keys[e.key];
    if (!o) return;
    e.preventDefault();
    e.stopPropagation();
    dispatch({ type: "SETTINGS", patch: { orientation: o } });
    (e.currentTarget.querySelector(`[data-orient="${o}"]`) as HTMLElement | null)?.focus();
  };

  const convertDisabled = !hasFile || busy || !available.length;
  const done = state.phase === "done" && !!state.jobId;

  return (
    <div className="side">
      <form
        className="card"
        aria-label={t("card.title")}
        onSubmit={(e) => {
          e.preventDefault();
          if (!convertDisabled) convert();
        }}
        noValidate
      >
        {/* Convert to */}
        <div className="card-row card-row--format">
          <label className="card-label" htmlFor={uid + "-fmt"}>
            {t("card.convertTo")}
          </label>
          <div className={"pill-select" + (shortLabel ? " pill-select--short" : "")}>
            {shortLabel && (
              <span className="pill-select__value" aria-hidden="true">
                {shortLabel}
              </span>
            )}
            <select
              id={uid + "-fmt"}
              value={formats ? s.format : ""}
              disabled={busy || !formats}
              aria-label={t("card.formatLabel")}
              onChange={(e) => dispatch({ type: "SETTINGS", patch: { format: e.target.value } })}
            >
              {!formats && <option value="">{formatsError ?? t("card.loadingFormats")}</option>}
              {(formats ?? []).map((f) => (
                <option key={f.format} value={f.format} disabled={!f.available}>
                  {formatLabel(f, lang)}
                  {f.available ? "" : ` — ${t("card.unavailable")}`}
                </option>
              ))}
            </select>
            <Caret className="pill-select__caret" />
          </div>
        </div>

        {/* Origin Size */}
        <div className="card-row card-row--origin">
          <div className="card-label card-label--stack">
            <span id={uid + "-origin"}>{t("card.originSize")}</span>
            <span className="card-optional">{t("card.optional")}</span>
          </div>
          <div role="group" aria-labelledby={uid + "-origin"}>
            <PairInputs
              idPrefix={uid + "-origin"}
              w={s.originW}
              h={s.originH}
              placeholderW={originPh.w}
              placeholderH={originPh.h}
              labelW={t("card.originW")}
              labelH={t("card.originH")}
              invalid={!!originMsg}
              describedBy={originMsg ? uid + "-origin-msg" : undefined}
              disabled={busy}
              onChange={(w, h) => dispatch({ type: "SETTINGS", patch: { originW: w, originH: h } })}
              onBlur={() => setTouched((x) => ({ ...x, origin: true }))}
            />
          </div>
        </div>
        {originMsg && (
          <p className="card-error" id={uid + "-origin-msg"} role="alert">
            {originMsg}
          </p>
        )}

        {/* Size */}
        <div className="card-row card-row--size">
          <div className="card-label card-label--stack card-label--size">
            <span id={uid + "-size"}>{t("card.size")}</span>
            {s.sizeMode === "preset" && (
              <div className="orient" role="radiogroup" aria-label={t("card.orientation")} onKeyDown={orientKey}>
                {(["portrait", "landscape"] as const).map((o) => (
                  <button
                    key={o}
                    type="button"
                    role="radio"
                    data-orient={o}
                    aria-checked={orientation === o}
                    tabIndex={orientation === o ? 0 : -1}
                    className="orient__btn"
                    title={t(o === "portrait" ? "card.portrait" : "card.landscape")}
                    aria-label={t(o === "portrait" ? "card.portrait" : "card.landscape")}
                    disabled={busy}
                    onClick={() => dispatch({ type: "SETTINGS", patch: { orientation: o } })}
                  >
                    {o === "portrait" ? <Portrait size={16} /> : <Landscape size={16} />}
                  </button>
                ))}
              </div>
            )}
          </div>
          <div className="size-stack" role="group" aria-labelledby={uid + "-size"}>
            <div className="pill-select">
              <select
                value={sizeValue}
                disabled={busy}
                aria-label={t("card.sizeLabel")}
                onChange={(e) => {
                  const v = e.target.value;
                  if (v === "original") dispatch({ type: "SETTINGS", patch: { sizeMode: "original", presetId: null, targetW: "", targetH: "" } });
                  else if (v === "custom") {
                    dispatch({ type: "SETTINGS", patch: { sizeMode: "custom", presetId: null } });
                    requestAnimationFrame(() => targetW.current?.querySelector("input")?.focus());
                  } else dispatch({ type: "SETTINGS", patch: { sizeMode: "preset", presetId: v, targetW: "", targetH: "", orientation: null } });
                }}
              >
                <option value="original">{t("card.originalSize")}</option>
                {presets.map((p) => (
                  <option key={p.id} value={p.id}>
                    {p.label}
                  </option>
                ))}
                <option value="custom">{t("card.custom")}</option>
              </select>
              <Caret className="pill-select__caret" />
            </div>
            <span className="card-or" aria-hidden="true">
              {t("card.or")}
            </span>
            <div ref={targetW}>
              <PairInputs
                idPrefix={uid + "-target"}
                w={s.targetW}
                h={s.targetH}
                placeholderW={targetPh.w}
                placeholderH={targetPh.h}
                labelW={t("card.targetW")}
                labelH={t("card.targetH")}
                invalid={!!targetMsg}
                describedBy={targetMsg ? uid + "-target-msg" : undefined}
                disabled={busy}
                onChange={(w, h) => {
                  const empty = !w.trim() && !h.trim();
                  dispatch({
                    type: "SETTINGS",
                    patch: empty && s.sizeMode === "custom" ? { targetW: w, targetH: h, sizeMode: "original", presetId: null } : { targetW: w, targetH: h, sizeMode: "custom", presetId: null },
                  });
                }}
                onBlur={() => setTouched((x) => ({ ...x, target: true }))}
              />
            </div>
          </div>
        </div>
        {targetMsg && (
          <p className="card-error" id={uid + "-target-msg"} role="alert">
            {targetMsg}
          </p>
        )}

        <button
          type="submit"
          className={"convert-btn" + (busy ? " convert-btn--busy" : "")}
          aria-disabled={convertDisabled || undefined}
          disabled={busy}
          title={!hasFile ? t("card.needFile") : undefined}
          aria-describedby={!hasFile ? uid + "-need" : undefined}
          style={busy ? ({ "--pct": `${pct}%` } as React.CSSProperties) : undefined}
        >
          <span className="convert-btn__fill" aria-hidden="true" />
          <span className="convert-btn__label">
            {uploading ? t("card.uploading", { pct }) : busy && state.phase !== "opening" ? t("card.converting", { pct }) : t("card.convert")}
          </span>
          <ArrowCircle className="convert-btn__icon" size={28} />
        </button>
        {!hasFile && (
          <span id={uid + "-need"} className="visually-hidden">
            {t("card.needFile")}
          </span>
        )}
      </form>

      {done && (
        <section className="result-card" aria-label={t("card.resultTitle")}>
          <div className="result-card__score">
            <span className="result-card__label">{t("card.score")}</span>
            <span className="result-card__value" data-testid="score">
              {state.score != null ? `${Math.round(state.score * 100)}%` : "—"}
            </span>
          </div>
          {state.noGraphics && (
            <p className="result-card__warn" role="note" data-testid="no-graphics">
              {t("done.noGraphics")}
            </p>
          )}
          <div className="result-card__actions">
            <a className="btn btn--primary" href={downloadUrl(state.jobId!, state.jobFormat ?? s.format)} download>
              {t("card.download")}
            </a>
            <a className="btn btn--outline" href={downloadUrl(state.jobId!, "report")} download>
              {t("card.downloadReport")}
            </a>
            {state.file && (
              <button type="button" className="btn btn--text" onClick={onConvertAgain}>
                {t("card.convertAgain")}
              </button>
            )}
          </div>
        </section>
      )}
    </div>
  );
}
