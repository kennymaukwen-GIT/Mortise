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
import { initialState, isWorking, reducer } from "./state/store";
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
  const [hasLibreOffice, setHasLibreOffice] = useState(false);
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
        setHasLibreOffice(!!h.verify_engine);
      })
      .catch(() => undefined);
    api
      .getFormats()
      .then((list) => {
        if (!alive) return;
        setFormats(list);
        dispatch({ type: "FORMATS_AVAILABLE", formats: list.filter((f) => f.available).map((f) => f.format) });
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

  // The list is shared with other tabs: always merge into what is stored now (not this tab's copy),
  // and follow changes made in other tabs (the storage event fires only in the other tabs).
  const onRecent = useCallback((job: RecentJob) => {
    const next = storage.upsertRecent(storage.loadRecent(), job);
    storage.saveRecent(next);
    setRecent(next);
  }, []);
  const onRecentRemoved = useCallback((jobId: string) => {
    const next = storage.removeRecent(storage.loadRecent(), jobId);
    storage.saveRecent(next);
    setRecent(next);
  }, []);
  useEffect(() => {
    const onStorage = (e: StorageEvent) => {
      if (e.key === null || e.key === storage.KEY_RECENT) setRecent(storage.loadRecent());
      if (e.key === null || e.key === storage.KEY_TRAINING) setAllowTraining(storage.loadAllowTraining());
    };
    window.addEventListener("storage", onStorage);
    return () => window.removeEventListener("storage", onStorage);
  }, []);

  const ctl = useController(state, dispatch, { maxMb, accepted, presets, formats: formats ?? [], allowTraining, onRecent, onRecentRemoved, notify });

  // The job on screen is part of the address (?job=…), so a reload or a shared tab comes back to it.
  const started = useRef(false);
  useEffect(() => {
    if (started.current) return;
    started.current = true;
    const id = new URLSearchParams(window.location.search).get("job");
    if (id && /^[A-Za-z0-9_-]{1,64}$/.test(id)) {
      const known = storage.loadRecent().find((j) => j.jobId === id);
      void ctl.openJob(known ?? { jobId: id, filename: id, format: "", createdAt: Date.now() });
    }
  }, [ctl]);
  useEffect(() => {
    if (!started.current) return;
    const url = new URL(window.location.href);
    const want = state.jobId ?? null;
    if (url.searchParams.get("job") === want) return;
    if (want) url.searchParams.set("job", want);
    else url.searchParams.delete("job");
    window.history.replaceState(window.history.state, "", url.pathname + url.search + url.hash);
  }, [state.jobId]);

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
        <a
          className="brand"
          href="/"
          aria-label={t("header.home")}
          onClick={(e) => {
            // Back to the empty frame (like File → Start over), except while a file is converting.
            e.preventDefault();
            if (!isWorking(state)) ctl.startOver();
          }}
        >
          <Logo size={35} />
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
            canStartOver={state.phase !== "empty" && !isWorking(state)}
            canOpen={!isWorking(state)}
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
          serverHasLibreOffice={hasLibreOffice}
          onJobMissing={() => void ctl.checkJob()}
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
  useEffect(() => {
    // A language chosen in another tab applies here too.
    const onStorage = (e: StorageEvent) => {
      if (e.key === null || e.key === storage.KEY_LANG) setLangState(storage.loadLang());
    };
    window.addEventListener("storage", onStorage);
    return () => window.removeEventListener("storage", onStorage);
  }, []);
  return (
    <I18nProvider lang={lang}>
      <Shell lang={lang} setLang={setLang} />
    </I18nProvider>
  );
}
