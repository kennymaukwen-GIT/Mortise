// Feedback controls: rate a page (1–5 stars), "something is wrong here" (tags + one sentence), and the note that says
// what happens with feedback and corrections.
import { useEffect, useId, useRef, useState } from "react";
import type { FeedbackIn, FeedbackTag } from "../api/types";
import { useI18n } from "../i18n/I18n";
import { MAX_COMMENT, reportFeedback } from "../state/feedback";
import { Check, Close, Flag, Star } from "./Icons";

/* ------------------------------------------------------------------ stars */

export function RatingStars({ page, value, onRate, disabled }: { page: number; value: number | null; onRate: (n: number) => Promise<void>; disabled?: boolean }) {
  const { t } = useI18n();
  const [hover, setHover] = useState<number | null>(null);
  const [sending, setSending] = useState<number | null>(null);
  const [error, setError] = useState<string | null>(null);
  const shown = hover ?? sending ?? value ?? 0;
  const rate = async (n: number) => {
    if (sending != null || disabled) return;
    setSending(n);
    setError(null);
    try {
      await onRate(n);
    } catch (e) {
      setError(t("fb.failed", { msg: e instanceof Error ? e.message : String(e) }));
    } finally {
      setSending(null);
    }
  };
  return (
    <div className="rating">
      <div
        className="rating__stars"
        role="radiogroup"
        aria-label={t("fb.rateLabel", { n: page })}
        onMouseLeave={() => setHover(null)}
        onKeyDown={(e) => {
          const step = e.key === "ArrowRight" || e.key === "ArrowUp" ? 1 : e.key === "ArrowLeft" || e.key === "ArrowDown" ? -1 : 0;
          if (!step) return;
          e.preventDefault();
          e.stopPropagation();
          const group = e.currentTarget;
          const next = Math.max(1, Math.min(5, (value ?? 0) + step));
          void rate(next);
          requestAnimationFrame(() => group.querySelector<HTMLElement>(`[data-star="${next}"]`)?.focus());
        }}
      >
        {[1, 2, 3, 4, 5].map((n) => (
          <button
            key={n}
            type="button"
            role="radio"
            aria-checked={value === n}
            tabIndex={(value ?? 1) === n ? 0 : -1}
            className={"rating__star" + (n <= shown ? " is-on" : "")}
            data-star={n}
            title={t(`fb.star.${n}` as "fb.star.1")}
            aria-label={`${n} / 5 · ${t(`fb.star.${n}` as "fb.star.1")}`}
            disabled={disabled}
            onMouseEnter={() => setHover(n)}
            onFocus={() => setHover(null)}
            onClick={() => void rate(n)}
          >
            <Star size={22} filled={n <= shown} />
          </button>
        ))}
      </div>
      <span className="rating__word" aria-hidden="true">
        {shown ? t(`fb.star.${shown}` as "fb.star.1") : ""}
      </span>
      {error && (
        <p className="rating__error" role="alert">
          {error}
        </p>
      )}
    </div>
  );
}

/** "Saved · 3 notes for this job" */
export function SavedCount({ count, fresh }: { count: number; fresh: boolean }) {
  const { t } = useI18n();
  if (!count) return null;
  return (
    <p className={"fb-saved" + (fresh ? " is-fresh" : "")} role="status" data-testid="fb-saved">
      {fresh && (
        <>
          <Check size={15} />
          <strong>{t("fb.saved")}</strong>
          <span aria-hidden="true"> · </span>
        </>
      )}
      <span>{count === 1 ? t("fb.count1") : t("fb.countN", { n: count })}</span>
    </p>
  );
}

/** What happens with feedback and corrections (locked; used to improve recognition only with consent). */
export function FeedbackNote({ consented, className }: { consented: boolean | null; className?: string }) {
  const { t } = useI18n();
  return (
    <p className={"fb-note " + (className ?? "")} data-testid="fb-note">
      {t("fb.locked")} {consented == null ? t("fb.consentUnknown") : consented ? t("fb.consentOn") : t("fb.consentOff")}
    </p>
  );
}

/* ------------------------------------------------------------------ "something is wrong here" */

export interface ReportFormProps {
  page: number;
  elementId: string | null;
  tags: FeedbackTag[];
  onSend: (fb: FeedbackIn) => Promise<void>;
  /** How many notes this job has (shown after sending). */
  count: number;
  consented: boolean | null;
  /** Called after a successful send. */
  onSent?: () => void;
  autoFocus?: boolean;
}

export function ReportForm({ page, elementId, tags, onSend, count, consented, onSent, autoFocus }: ReportFormProps) {
  const { t } = useI18n();
  const uid = useId();
  const [picked, setPicked] = useState<FeedbackTag[]>([]);
  const [comment, setComment] = useState("");
  const [sending, setSending] = useState(false);
  const [sent, setSent] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const first = useRef<HTMLButtonElement>(null);
  useEffect(() => {
    if (autoFocus) first.current?.focus();
  }, [autoFocus]);

  const toggle = (tag: FeedbackTag) => {
    setSent(false);
    setError(null);
    setPicked((p) => (p.includes(tag) ? p.filter((x) => x !== tag) : [...p, tag]));
  };

  const send = async () => {
    // Keep the order the tags are offered in.
    const res = reportFeedback(page, elementId, tags.filter((x) => picked.includes(x)), comment);
    if (!res.ok) {
      setError(t("fb.needTag"));
      return;
    }
    setSending(true);
    setError(null);
    try {
      await onSend(res.body);
      setSent(true);
      setPicked([]);
      setComment("");
      onSent?.();
    } catch (e) {
      setError(t("fb.failed", { msg: e instanceof Error ? e.message : String(e) }));
    } finally {
      setSending(false);
    }
  };

  return (
    <div className="report" data-testid="report-form">
      <fieldset className="report__tags">
        <legend className="field__label">{t("fb.whatWrong")}</legend>
        <div className="report__chips">
          {tags.map((tag, i) => (
            <button
              key={tag}
              ref={i === 0 ? first : undefined}
              type="button"
              className="tag-chip"
              aria-pressed={picked.includes(tag)}
              data-tag={tag}
              onClick={() => toggle(tag)}
            >
              {picked.includes(tag) && <Check size={14} />}
              {t(`fb.tag.${tag}` as "fb.tag.other")}
            </button>
          ))}
        </div>
      </fieldset>
      <label className="field">
        <span className="field__label">{t("fb.comment")}</span>
        <input
          className="field__input field__input--light"
          value={comment}
          maxLength={MAX_COMMENT}
          placeholder={t("fb.commentPlaceholder")}
          onChange={(e) => {
            setComment(e.target.value);
            setSent(false);
            setError(null);
          }}
          onKeyDown={(e) => {
            if (e.key === "Enter") {
              e.preventDefault();
              void send();
            }
          }}
          aria-describedby={uid + "-note"}
        />
      </label>
      {error && (
        <p className="dialog__error" role="alert">
          {error}
        </p>
      )}
      <div className="report__foot">
        {sent ? <SavedCount count={count} fresh /> : <span />}
        <button type="button" className="btn btn--primary btn--small" onClick={() => void send()} disabled={sending} data-testid="report-send">
          <Flag size={15} />
          {sending ? t("fb.sending") : t("fb.send")}
        </button>
      </div>
      <p className="fb-note fb-note--dialog" id={uid + "-note"}>
        {t("fb.reportNote")} {consented == null ? "" : consented ? t("fb.consentOn") : t("fb.consentOff")}
      </p>
    </div>
  );
}

/** A dialog for a problem with the whole page (nothing selected). */
export function PageReportDialog({ page, tags, onSend, onClose, count, consented }: { page: number; tags: FeedbackTag[]; onSend: (fb: FeedbackIn) => Promise<void>; onClose: () => void; count: number; consented: boolean | null }) {
  const { t } = useI18n();
  const uid = useId();
  const panel = useRef<HTMLDivElement>(null);
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") {
        e.stopPropagation();
        onClose();
      }
    };
    document.addEventListener("keydown", onKey, true);
    return () => document.removeEventListener("keydown", onKey, true);
  }, [onClose]);
  const trap = (e: React.KeyboardEvent) => {
    if (e.key !== "Tab" || !panel.current) return;
    const f = Array.from(panel.current.querySelectorAll<HTMLElement>("input, button:not([disabled])"));
    if (!f.length) return;
    if (e.shiftKey && document.activeElement === f[0]) {
      e.preventDefault();
      f[f.length - 1].focus();
    } else if (!e.shiftKey && document.activeElement === f[f.length - 1]) {
      e.preventDefault();
      f[0].focus();
    }
  };
  return (
    <div className="dialog-backdrop" data-no-pan onPointerDown={(e) => e.target === e.currentTarget && onClose()}>
      <div ref={panel} className="dialog" role="dialog" aria-modal="true" aria-labelledby={uid + "-title"} onKeyDown={trap}>
        <div className="dialog__head">
          <h2 id={uid + "-title"} className="dialog__title">
            {t("fb.pageReportTitle", { n: page })}
          </h2>
          <button type="button" className="icon-btn" onClick={onClose} aria-label={t("edit.close")}>
            <Close size={18} />
          </button>
        </div>
        <ReportForm page={page} elementId={null} tags={tags} onSend={onSend} count={count} consented={consented} autoFocus />
      </div>
    </div>
  );
}
