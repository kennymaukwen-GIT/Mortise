import { useCallback, useEffect, useMemo, useReducer, useRef, useState } from "react";
import * as api from "./api/client";
import type { FormatInfo, PageSizePreset } from "./api/types";
import { AccountMenu } from "./components/AccountMenu";
import { FileMenu } from "./components/FileMenu";
import { Logo } from "./components/Icons";
import { SettingsCard } from "./components/SettingsCard";
import { Stage } from "./components/Stage";
import type { Lang } from "./i18n/dict";
import { I18nProvider, useI18n } from "./i18n/I18n";
import { DEFAULT_ACCEPTED, DEFAULT_MAX_FILE_MB, FALLBACK_PAGE_SIZES, FORMAT_LABELS_EN } from "./lib/catalog";
import * as storage from "./lib/storage";
import type { RecentJob } from "./lib/storage";
import { initialState, reducer } from "./state/store";
import { useController } from "./state/useController";

const FALLBACK_FORMATS: FormatInfo[] = Object.entries(FORMAT_LABELS_EN).map(([format, label_en]) => ({ format, label: label_en, label_en, available: false }));

function Shell({ lang, setLang }: { lang: Lang; setLang: (l: Lang) => void }) {
  const { t } = useI18n();
  const [state, dispatch] = useReducer(reducer, initialState);
  const [maxMb, setMaxMb] = useState(DEFAULT_MAX_FILE_MB);
  const [accepted, setAccepted] = useState<string[]>(DEFAULT_ACCEPTED);
  const [formats, setFormats] = useState<FormatInfo[] | null>(null);
  const [formatsError, setFormatsError] = useState<string | null>(null);
  const [presets, setPresets] = useState<PageSizePreset[]>(FALLBACK_PAGE_SIZES);
  const [allowTraining, setAllowTraining] = useState(storage.loadAllowTraining);
  const [recent, setRecent] = useState<RecentJob[]>(storage.loadRecent);
  const [toast, setToast] = useState<string | null>(null);
  const fileInput = useRef<HTMLInputElement>(null);

  // Server facts: limits, formats, page sizes.
  useEffect(() => {
    let alive = true;
    api
      .getHealth()
      .then((h) => {
        if (!alive) return;
        if (typeof h.max_file_mb === "number" && h.max_file_mb > 0) setMaxMb(h.max_file_mb);
        if (Array.isArray(h.accepted) && h.accepted.length) setAccepted(h.accepted);
      })
      .catch(() => undefined);
    api
      .getFormats()
      .then((list) => {
        if (!alive) return;
        setFormats(list);
        const avail = list.filter((f) => f.available).map((f) => f.format);
        dispatch({ type: "SETTINGS", patch: { format: avail.includes("svg") ? "svg" : (avail[0] ?? "svg") } });
      })
      .catch(() => {
        if (!alive) return;
        setFormats(FALLBACK_FORMATS);
        setFormatsError(t("err.network"));
      });
    api
      .getPageSizes()
      .then((list) => alive && Array.isArray(list) && list.length && setPresets(list))
      .catch(() => undefined);
    return () => {
      alive = false;
    };
  }, []); // eslint-disable-line react-hooks/exhaustive-deps

  const notify = useCallback((text: string) => setToast(text), []);
  useEffect(() => {
    if (!toast) return;
    const id = setTimeout(() => setToast(null), 4000);
    return () => clearTimeout(id);
  }, [toast]);

  const onRecent = useCallback((job: RecentJob) => {
    setRecent((list) => {
      const next = storage.upsertRecent(list, job);
      storage.saveRecent(next);
      return next;
    });
  }, []);
  const onRecentRemoved = useCallback((jobId: string) => {
    setRecent((list) => {
      const next = storage.removeRecent(list, jobId);
      storage.saveRecent(next);
      return next;
    });
  }, []);

  const ctl = useController(state, dispatch, { maxMb, accepted, presets, formats: formats ?? [], allowTraining, onRecent, onRecentRemoved, notify });

  // Dropping a file anywhere else must not navigate away from the app.
  useEffect(() => {
    const stop = (e: DragEvent) => {
      if (e.dataTransfer && Array.from(e.dataTransfer.types).includes("Files")) e.preventDefault();
    };
    window.addEventListener("dragover", stop);
    window.addEventListener("drop", stop);
    return () => {
      window.removeEventListener("dragover", stop);
      window.removeEventListener("drop", stop);
    };
  }, []);

  const pickFile = () => fileInput.current?.click();
  const fileName = state.file?.name ?? state.jobFilename;
  const toastText = toast === "file.deleted" ? t("file.deleted") : toast;
  const formatsReady = useMemo(() => !!formats?.some((f) => f.available), [formats]);

  return (
    <div className="app">
      <header className="header">
        <a className="brand" href="/" aria-label={t("header.home")} onClick={(e) => e.preventDefault()}>
          <Logo size={36} />
          <span className="brand__name">{t("brand")}</span>
        </a>
        <div className="header__actions">
          <FileMenu
            fileName={fileName}
            jobId={state.jobId}
            format={state.jobFormat}
            done={state.phase === "done"}
            onOpen={pickFile}
            onStartOver={ctl.startOver}
            onDelete={() => void ctl.deleteFromServer()}
            canStartOver={state.phase !== "empty"}
          />
          <AccountMenu
            lang={lang}
            onLang={setLang}
            allowTraining={allowTraining}
            onAllowTraining={(v) => {
              setAllowTraining(v);
              storage.saveAllowTraining(v);
            }}
            recent={recent}
            currentJobId={state.jobId}
            onOpenJob={(j) => void ctl.openJob(j)}
            onDeleteJob={(j) => void ctl.deleteJob(j)}
          />
        </div>
        <input
          ref={fileInput}
          type="file"
          className="visually-hidden"
          tabIndex={-1}
          aria-hidden="true"
          accept={[...accepted, ".pdf", ".png", ".jpg", ".jpeg"].join(",")}
          data-testid="file-input"
          onChange={(e) => {
            const f = e.target.files?.[0];
            e.target.value = "";
            if (f) void ctl.openFile(f);
          }}
        />
      </header>

      <main className="main">
        <Stage
          state={state}
          dispatch={dispatch}
          maxMb={maxMb}
          onPickFile={pickFile}
          onDropFile={(f) => void ctl.openFile(f)}
          onCancel={() => void ctl.cancel()}
          onRetry={() => void ctl.retry()}
          onCorrect={ctl.correct}
        />
        <SettingsCard
          state={state}
          dispatch={dispatch}
          formats={formats}
          formatsError={formatsError}
          presets={presets}
          onConvert={() => formatsReady && void ctl.convert()}
          onConvertAgain={ctl.convertAgain}
        />
      </main>

      <div className="toast-region" aria-live="polite">
        {toastText && <div className="toast">{toastText}</div>}
      </div>
    </div>
  );
}

export default function App() {
  const [lang, setLangState] = useState<Lang>(storage.loadLang);
  const setLang = useCallback((l: Lang) => {
    setLangState(l);
    storage.saveLang(l);
  }, []);
  return (
    <I18nProvider lang={lang}>
      <Shell lang={lang} setLang={setLang} />
    </I18nProvider>
  );
}
