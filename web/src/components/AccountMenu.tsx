import { useEffect, useRef, useState } from "react";
import type { Lang } from "../i18n/dict";
import { useI18n } from "../i18n/I18n";
import { shortName } from "../lib/files";
import type { RecentJob } from "../lib/storage";
import { FORMAT_SHORT } from "../lib/catalog";
import { User } from "./Icons";
import { PopoverPanel, usePopoverDismiss } from "./Popover";

export interface AccountMenuProps {
  lang: Lang;
  onLang: (l: Lang) => void;
  allowTraining: boolean;
  onAllowTraining: (v: boolean) => void;
  recent: RecentJob[];
  currentJobId: string | null;
  onOpenJob: (j: RecentJob) => void;
  onDeleteJob: (j: RecentJob) => void;
}

function when(ms: number, lang: Lang): string {
  try {
    return new Intl.DateTimeFormat(lang === "zh-Hant" ? "zh-TW" : "en", { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" }).format(new Date(ms));
  } catch {
    return new Date(ms).toLocaleString();
  }
}

export function AccountMenu(p: AccountMenuProps) {
  const { t, lang } = useI18n();
  const [open, setOpen] = useState(false);
  const btn = useRef<HTMLButtonElement>(null);
  const panel = useRef<HTMLDivElement>(null);
  usePopoverDismiss(open, () => setOpen(false), [btn, panel]);
  useEffect(() => {
    if (open) panel.current?.focus();
  }, [open]);

  const langs: { id: Lang; label: string }[] = [
    { id: "en", label: t("lang.en") },
    { id: "zh-Hant", label: t("lang.zh") },
  ];

  return (
    <div className="header-anchor">
      <button
        ref={btn}
        type="button"
        className="account-button"
        aria-haspopup="dialog"
        aria-expanded={open}
        aria-controls="account-menu"
        aria-label={t("account.button")}
        title={t("account.button")}
        onClick={() => setOpen((v) => !v)}
      >
        <User size={18} />
      </button>
      {open && (
        <PopoverPanel id="account-menu" role="dialog" label={t("account.button")} panelRef={panel} className="popover--account">
          <section className="acct-section">
            <p className="acct-soon">{t("account.soon")}</p>
            <p className="acct-muted">{t("account.soonDetail")}</p>
          </section>

          <section className="acct-section">
            <h2 className="acct-heading" id="acct-lang">
              {t("account.language")}
            </h2>
            <div className="segmented" role="radiogroup" aria-labelledby="acct-lang">
              {langs.map((l) => (
                <button
                  key={l.id}
                  type="button"
                  role="radio"
                  aria-checked={p.lang === l.id}
                  className="segmented__item"
                  lang={l.id === "zh-Hant" ? "zh-Hant" : "en"}
                  onClick={() => p.onLang(l.id)}
                >
                  {l.label}
                </button>
              ))}
            </div>
          </section>

          <section className="acct-section">
            <div className="switch-row">
              <span className="switch-row__label" id="acct-train">
                {t("account.training")}
              </span>
              <button
                type="button"
                role="switch"
                aria-checked={p.allowTraining}
                aria-labelledby="acct-train"
                aria-describedby="acct-train-hint"
                className="switch"
                onClick={() => p.onAllowTraining(!p.allowTraining)}
              >
                <span className="switch__knob" />
              </button>
            </div>
            <p className="acct-muted" id="acct-train-hint">
              {t("account.trainingHint")}
            </p>
          </section>

          <section className="acct-section">
            <h2 className="acct-heading">{t("account.recent")}</h2>
            {p.recent.length === 0 ? (
              <p className="acct-muted">{t("account.recentEmpty")}</p>
            ) : (
              <ul className="recent-list">
                {p.recent.map((j) => (
                  <li key={j.jobId} className={"recent" + (j.jobId === p.currentJobId ? " recent--current" : "")}>
                    <div className="recent__info">
                      <span className="recent__name" title={j.filename}>
                        {shortName(j.filename, 26)}
                      </span>
                      <span className="recent__meta">
                        {when(j.createdAt, lang)} · {FORMAT_SHORT[j.format] ?? j.format}
                        {typeof j.score === "number" ? ` · ${Math.round(j.score * 100)}%` : ""}
                      </span>
                    </div>
                    <div className="recent__actions">
                      <button
                        type="button"
                        className="mini-btn"
                        aria-label={t("account.openLabel", { name: j.filename })}
                        onClick={() => {
                          setOpen(false);
                          p.onOpenJob(j);
                        }}
                      >
                        {t("account.open")}
                      </button>
                      <button type="button" className="mini-btn mini-btn--danger" aria-label={t("account.removeLabel", { name: j.filename })} onClick={() => p.onDeleteJob(j)}>
                        {t("account.remove")}
                      </button>
                    </div>
                  </li>
                ))}
              </ul>
            )}
          </section>
        </PopoverPanel>
      )}
    </div>
  );
}
