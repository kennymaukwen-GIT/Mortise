import { useEffect, useRef, useState } from "react";
import { getEngine } from "../api/client";
import type { EngineInfo } from "../api/types";
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

function pct(v: unknown, digits = 0): string | null {
  return typeof v === "number" && Number.isFinite(v) ? (v * 100).toFixed(digits) : null;
}

/** "About": the recognition engine's version (GET /api/engine) and its latest test run. */
function EngineAbout({ engine, failed }: { engine: EngineInfo | null; failed: boolean }) {
  const { t, lang } = useI18n();
  const card = engine?.scorecard ?? null;
  const fid = pct(card?.headline?.e2e_score?.mean);
  const cer = pct(card?.headline?.page_cer?.mean, 1);
  const date = typeof card?.created === "string" ? card.created.slice(0, 10) : "";
  return (
    <section className="acct-section" aria-labelledby="acct-about" data-testid="engine-about">
      <h2 className="acct-heading" id="acct-about">
        {t("account.about")}
      </h2>
      {!engine ? (
        <p className="acct-muted">{failed ? t("account.engineUnknown") : t("account.engineLoading")}</p>
      ) : (
        <>
          <p className="acct-engine">
            <span>{t("account.engine")}</span>
            <strong lang={engine.name && /[\u3400-\u9fff]/.test(engine.name) ? "zh-Hant" : undefined} data-testid="engine-name">
              {engine.name || "—"}
            </strong>
          </p>
          <p className="acct-version">
            {t("account.engineVersion")} <code data-testid="engine-version">{engine.version}</code>
          </p>
          {card ? (
            <p className="acct-muted">
              {t("account.scorecard", { date, suite: String(card.suite ?? "") })}
              {fid && ` · ${t("account.scoreFidelity", { pct: fid })}`}
              {cer && ` · ${t("account.scoreCer", { pct: cer })}`}
              {card.matches_current === false && ` ${t("account.scorecardOld")}`}
            </p>
          ) : (
            <p className="acct-muted">{t("account.noScorecard")}</p>
          )}
          <p className="acct-muted" lang={lang}>
            {t("account.engineHint")}
          </p>
        </>
      )}
    </section>
  );
}

export function AccountMenu(p: AccountMenuProps) {
  const { t, lang } = useI18n();
  const [open, setOpen] = useState(false);
  const btn = useRef<HTMLButtonElement>(null);
  const panel = useRef<HTMLDivElement>(null);
  usePopoverDismiss(
    open,
    (why) => {
      setOpen(false);
      if (why === "escape") btn.current?.focus();
    },
    [btn, panel],
  );
  useEffect(() => {
    if (open) panel.current?.focus();
  }, [open]);
  // The engine version is read the first time the menu opens (it does not change while the server runs).
  const [engine, setEngine] = useState<EngineInfo | null>(null);
  const [engineFailed, setEngineFailed] = useState(false);
  useEffect(() => {
    if (!open || engine) return;
    let alive = true;
    setEngineFailed(false);
    getEngine()
      .then((e) => alive && setEngine(e))
      .catch(() => alive && setEngineFailed(true));
    return () => {
      alive = false;
    };
  }, [open, engine]);

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
            <div
              className="segmented"
              role="radiogroup"
              aria-labelledby="acct-lang"
              onKeyDown={(e) => {
                const i = langs.findIndex((l) => l.id === p.lang);
                const step = e.key === "ArrowRight" || e.key === "ArrowDown" ? 1 : e.key === "ArrowLeft" || e.key === "ArrowUp" ? -1 : 0;
                if (!step) return;
                e.preventDefault();
                const group = e.currentTarget;
                const next = langs[(i + step + langs.length) % langs.length];
                p.onLang(next.id);
                requestAnimationFrame(() => (group.querySelector(`[data-lang="${next.id}"]`) as HTMLElement | null)?.focus());
              }}
            >
              {langs.map((l) => (
                <button
                  key={l.id}
                  type="button"
                  role="radio"
                  data-lang={l.id}
                  tabIndex={p.lang === l.id ? 0 : -1}
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

          <EngineAbout engine={engine} failed={engineFailed} />
        </PopoverPanel>
      )}
    </div>
  );
}
