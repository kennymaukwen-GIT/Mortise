import { useEffect, useRef, useState } from "react";
import { downloadUrl } from "../api/client";
import { useI18n } from "../i18n/I18n";
import { shortName } from "../lib/files";
import { onMenuKeyDown, PopoverPanel, usePopoverDismiss } from "./Popover";

export interface FileMenuProps {
  fileName: string | null;
  jobId: string | null;
  format: string | null;
  done: boolean;
  onOpen: () => void;
  onStartOver: () => void;
  onDelete: () => void;
  canStartOver: boolean;
  /** False while a file is uploading or converting (opening another file would drop that job). */
  canOpen?: boolean;
  /** Download this job's feedback bundle (zip). */
  onExportFeedback?: () => void;
}

export function FileMenu({ fileName, jobId, format, done, onOpen, onStartOver, onDelete, canStartOver, canOpen = true, onExportFeedback }: FileMenuProps) {
  const { t } = useI18n();
  const [open, setOpen] = useState(false);
  const btn = useRef<HTMLButtonElement>(null);
  const panel = useRef<HTMLDivElement>(null);
  const close = (focus = true) => {
    setOpen(false);
    if (focus) btn.current?.focus();
  };
  usePopoverDismiss(open, (why) => close(why === "escape"), [btn, panel]);
  useEffect(() => {
    if (open) panel.current?.querySelector<HTMLElement>('[role="menuitem"]:not([aria-disabled="true"])')?.focus();
  }, [open]);

  const item = (label: string, onClick: (() => void) | null, opts: { href?: string; danger?: boolean } = {}) => {
    const disabled = !onClick && !opts.href;
    const cls = "menu-item" + (opts.danger ? " menu-item--danger" : "");
    if (opts.href && !disabled)
      return (
        <a role="menuitem" className={cls} href={opts.href} download onClick={() => close()}>
          {label}
        </a>
      );
    return (
      <button
        type="button"
        role="menuitem"
        className={cls}
        aria-disabled={disabled || undefined}
        onClick={() => {
          if (disabled || !onClick) return;
          close();
          onClick();
        }}
      >
        {label}
      </button>
    );
  };

  return (
    <div className="header-anchor">
      <button
        ref={btn}
        type="button"
        className="file-button"
        aria-haspopup="menu"
        aria-expanded={open}
        aria-controls="file-menu"
        aria-label={fileName ? `${t("file.menu")}: ${fileName}` : t("file.menu")}
        title={fileName ?? undefined}
        onClick={() => setOpen((v) => !v)}
      >
        <span className="file-button__text">{fileName ? shortName(fileName, 16) : t("file.button")}</span>
      </button>
      {open && (
        <PopoverPanel id="file-menu" role="menu" label={t("file.menu")} panelRef={panel} className="popover--menu" onKeyDown={(e) => onMenuKeyDown(e)}>
          {item(t("file.open"), canOpen ? onOpen : null)}
          <div className="menu-sep" role="separator" />
          {item(t("file.download"), null, { href: done && jobId && format ? downloadUrl(jobId, format) : undefined })}
          {item(t("file.downloadReport"), null, { href: done && jobId ? downloadUrl(jobId, "report") : undefined })}
          {item(t("file.exportFeedback"), done && jobId && onExportFeedback ? onExportFeedback : null)}
          <div className="menu-sep" role="separator" />
          {item(t("file.startOver"), canStartOver ? onStartOver : null)}
          {item(t("file.delete"), jobId ? onDelete : null, { danger: true })}
        </PopoverPanel>
      )}
    </div>
  );
}
