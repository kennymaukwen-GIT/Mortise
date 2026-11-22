// A small anchored popover: closes on Escape, outside click, or when focus leaves.
import { useEffect, useRef, type ReactNode, type RefObject } from "react";

export function usePopoverDismiss(open: boolean, onClose: () => void, refs: RefObject<HTMLElement | null>[]) {
  const closeRef = useRef(onClose);
  closeRef.current = onClose;
  useEffect(() => {
    if (!open) return;
    const onDown = (e: PointerEvent) => {
      const t = e.target as Node;
      if (refs.some((r) => r.current?.contains(t))) return;
      closeRef.current();
    };
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") {
        e.stopPropagation();
        closeRef.current();
      }
    };
    document.addEventListener("pointerdown", onDown, true);
    document.addEventListener("keydown", onKey, true);
    return () => {
      document.removeEventListener("pointerdown", onDown, true);
      document.removeEventListener("keydown", onKey, true);
    };
  }, [open]); // eslint-disable-line react-hooks/exhaustive-deps
}

/** Arrow-key navigation between [role=menuitem] / focusable rows inside `root`. */
export function onMenuKeyDown(e: React.KeyboardEvent<HTMLElement>, selector = '[role="menuitem"]:not([aria-disabled="true"])') {
  const items = Array.from(e.currentTarget.querySelectorAll<HTMLElement>(selector));
  if (!items.length) return;
  const i = items.indexOf(document.activeElement as HTMLElement);
  let next = -1;
  if (e.key === "ArrowDown") next = (i + 1) % items.length;
  else if (e.key === "ArrowUp") next = (i - 1 + items.length) % items.length;
  else if (e.key === "Home") next = 0;
  else if (e.key === "End") next = items.length - 1;
  if (next >= 0) {
    e.preventDefault();
    items[next].focus();
  }
}

export function PopoverPanel({ id, label, role, children, className, panelRef, onKeyDown }: {
  id: string;
  label: string;
  role: "menu" | "dialog";
  children: ReactNode;
  className?: string;
  panelRef: RefObject<HTMLDivElement | null>;
  onKeyDown?: (e: React.KeyboardEvent<HTMLDivElement>) => void;
}) {
  return (
    <div id={id} ref={panelRef} role={role} aria-label={label} className={"popover " + (className ?? "")} onKeyDown={onKeyDown} tabIndex={-1}>
      {children}
    </div>
  );
}
