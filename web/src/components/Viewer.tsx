// Zoom and pan for one page. Children are drawn inside a box with the page's aspect ratio.
import { useCallback, useEffect, useLayoutEffect, useRef, useState, type ReactNode } from "react";
import { useI18n } from "../i18n/I18n";
import { Fit, Minus, Plus } from "./Icons";

export const MIN_ZOOM = 0.25;
export const MAX_ZOOM = 8;

export interface ViewerRenderInfo {
  /** Device pixels the page currently spans horizontally (for choosing image resolution). */
  pixelWidth: number;
  /** CSS pixels of the fitted page box (before zoom). */
  fitWidth: number;
  zoom: number;
}

interface Props {
  aspect: number;
  /** Changing this resets zoom to fit (e.g. a different page). */
  resetKey: string | number;
  children: (info: ViewerRenderInfo) => ReactNode;
  label: string;
  /** Extra space kept free around the page for toolbars, in CSS px. */
  inset?: { top: number; right: number; bottom: number; left: number };
  disabled?: boolean;
}

interface Xform {
  zoom: number;
  x: number;
  y: number;
}

const FIT: Xform = { zoom: 1, x: 0, y: 0 };

export function clampZoom(z: number): number {
  return Math.min(MAX_ZOOM, Math.max(MIN_ZOOM, z));
}

/** Zoom by `factor` keeping the point `c` (relative to the viewport centre) still. */
export function zoomAt(xf: Xform, factor: number, cx: number, cy: number): Xform {
  const zoom = clampZoom(xf.zoom * factor);
  const k = zoom / xf.zoom;
  return { zoom, x: cx - (cx - xf.x) * k, y: cy - (cy - xf.y) * k };
}

export function Viewer({ aspect, resetKey, children, label, inset = { top: 16, right: 16, bottom: 16, left: 16 }, disabled }: Props) {
  const { t } = useI18n();
  const root = useRef<HTMLDivElement>(null);
  const [size, setSize] = useState({ w: 0, h: 0 });
  const [xf, setXf] = useState<Xform>(FIT);
  const xfRef = useRef(xf);
  xfRef.current = xf;

  useLayoutEffect(() => {
    const el = root.current;
    if (!el) return;
    const update = () => setSize({ w: el.clientWidth, h: el.clientHeight });
    update();
    const ro = new ResizeObserver(update);
    ro.observe(el);
    return () => ro.disconnect();
  }, []);

  useEffect(() => setXf(FIT), [resetKey]);

  const availW = Math.max(1, size.w - inset.left - inset.right);
  const availH = Math.max(1, size.h - inset.top - inset.bottom);
  const fitW = Math.max(1, Math.min(availW, availH * aspect));
  const fitH = fitW / aspect;
  const cxOff = (inset.left - inset.right) / 2;
  const cyOff = (inset.top - inset.bottom) / 2;

  const clampPan = useCallback(
    (v: Xform): Xform => {
      const mx = (fitW * v.zoom) / 2 + size.w / 2 - 48;
      const my = (fitH * v.zoom) / 2 + size.h / 2 - 48;
      return { zoom: v.zoom, x: Math.max(-mx, Math.min(mx, v.x)), y: Math.max(-my, Math.min(my, v.y)) };
    },
    [fitW, fitH, size.w, size.h],
  );

  const toLocal = (clientX: number, clientY: number) => {
    const r = root.current!.getBoundingClientRect();
    return { x: clientX - r.left - r.width / 2 - cxOff, y: clientY - r.top - r.height / 2 - cyOff };
  };

  // Wheel / trackpad pinch (non-passive so the page does not scroll).
  useEffect(() => {
    const el = root.current;
    if (!el || disabled) return;
    const onWheel = (e: WheelEvent) => {
      e.preventDefault();
      const unit = e.deltaMode === 1 ? 16 : e.deltaMode === 2 ? 400 : 1;
      const dy = e.deltaY * unit;
      const factor = Math.exp(-dy * (e.ctrlKey ? 0.01 : 0.0015));
      const p = toLocal(e.clientX, e.clientY);
      setXf((v) => clampPan(zoomAt(v, factor, p.x, p.y)));
    };
    el.addEventListener("wheel", onWheel, { passive: false });
    return () => el.removeEventListener("wheel", onWheel);
  }); // re-bind each render so clampPan/toLocal see fresh sizes

  // Drag to pan, two fingers to pinch.
  const pointers = useRef(new Map<number, { x: number; y: number }>());
  const gesture = useRef<{ startX: number; startY: number; dragging: boolean; pinchDist: number; pinchMid: { x: number; y: number } } | null>(null);

  const onPointerDown = (e: React.PointerEvent) => {
    if (disabled || (e.pointerType === "mouse" && e.button !== 0)) return;
    if ((e.target as HTMLElement).closest("[data-no-pan]")) return;
    pointers.current.set(e.pointerId, { x: e.clientX, y: e.clientY });
    if (pointers.current.size === 1) gesture.current = { startX: e.clientX, startY: e.clientY, dragging: false, pinchDist: 0, pinchMid: { x: 0, y: 0 } };
    if (pointers.current.size === 2) {
      const [a, b] = [...pointers.current.values()];
      gesture.current = { startX: 0, startY: 0, dragging: true, pinchDist: Math.hypot(a.x - b.x, a.y - b.y), pinchMid: toLocal((a.x + b.x) / 2, (a.y + b.y) / 2) };
      root.current?.setPointerCapture(e.pointerId);
    }
  };

  const onPointerMove = (e: React.PointerEvent) => {
    const prev = pointers.current.get(e.pointerId);
    const g = gesture.current;
    if (!prev || !g) return;
    pointers.current.set(e.pointerId, { x: e.clientX, y: e.clientY });
    if (pointers.current.size >= 2) {
      const [a, b] = [...pointers.current.values()];
      const dist = Math.hypot(a.x - b.x, a.y - b.y);
      const mid = toLocal((a.x + b.x) / 2, (a.y + b.y) / 2);
      if (g.pinchDist > 0) {
        const factor = dist / g.pinchDist;
        setXf((v) => {
          const z = zoomAt(v, factor, mid.x, mid.y);
          return clampPan({ ...z, x: z.x + (mid.x - g.pinchMid.x), y: z.y + (mid.y - g.pinchMid.y) });
        });
      }
      g.pinchDist = dist;
      g.pinchMid = mid;
      return;
    }
    if (!g.dragging) {
      if (Math.hypot(e.clientX - g.startX, e.clientY - g.startY) < 4) return;
      g.dragging = true;
      root.current?.setPointerCapture(e.pointerId);
    }
    const dx = e.clientX - prev.x;
    const dy = e.clientY - prev.y;
    setXf((v) => clampPan({ zoom: v.zoom, x: v.x + dx, y: v.y + dy }));
  };

  const endPointer = (e: React.PointerEvent) => {
    pointers.current.delete(e.pointerId);
    if (root.current?.hasPointerCapture(e.pointerId)) root.current.releasePointerCapture(e.pointerId);
    if (pointers.current.size === 0) gesture.current = null;
    else if (gesture.current) gesture.current.pinchDist = 0;
  };

  const zoomBy = (f: number) => setXf((v) => clampPan(zoomAt(v, f, 0, 0)));
  const onKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === "+" || e.key === "=") zoomBy(1.25);
    else if (e.key === "-" || e.key === "_") zoomBy(0.8);
    else if (e.key === "0") setXf(FIT);
    else if (xfRef.current.zoom > 1 && ["ArrowUp", "ArrowDown"].includes(e.key)) {
      setXf((v) => clampPan({ ...v, y: v.y + (e.key === "ArrowUp" ? 60 : -60) }));
    } else return;
    e.preventDefault();
  };

  const dpr = typeof window !== "undefined" ? window.devicePixelRatio || 1 : 1;
  const info: ViewerRenderInfo = { pixelWidth: fitW * xf.zoom * dpr, fitWidth: fitW, zoom: xf.zoom };
  const dragging = !!gesture.current?.dragging;

  return (
    <div className="viewer">
      <div
        ref={root}
        className={"viewer__stage" + (dragging ? " is-dragging" : "")}
        tabIndex={0}
        role="group"
        aria-roledescription={t("viewer.role")}
        aria-label={label}
        onPointerDown={onPointerDown}
        onPointerMove={onPointerMove}
        onPointerUp={endPointer}
        onPointerCancel={endPointer}
        onKeyDown={onKeyDown}
        onDoubleClick={(e) => {
          if ((e.target as HTMLElement).closest("[data-no-pan]")) return;
          const p = toLocal(e.clientX, e.clientY);
          setXf((v) => (v.zoom > 1.01 ? FIT : clampPan(zoomAt(v, 2, p.x, p.y))));
        }}
      >
        {size.w > 0 && (
          <div
            className="viewer__page"
            style={{
              width: fitW,
              height: fitH,
              transform: `translate(-50%, -50%) translate(${xf.x + cxOff}px, ${xf.y + cyOff}px) scale(${xf.zoom})`,
            }}
          >
            {children(info)}
          </div>
        )}
      </div>
      <div className="zoom-controls" role="toolbar" aria-label={t("zoom.level")}>
        <button type="button" className="tool-btn" onClick={() => zoomBy(0.8)} aria-label={t("zoom.out")} title={t("zoom.out")} disabled={disabled || xf.zoom <= MIN_ZOOM}>
          <Minus size={18} />
        </button>
        <output className="zoom-controls__level" aria-live="off" aria-label={t("zoom.level")}>
          {Math.round(xf.zoom * 100)}%
        </output>
        <button type="button" className="tool-btn" onClick={() => zoomBy(1.25)} aria-label={t("zoom.in")} title={t("zoom.in")} disabled={disabled || xf.zoom >= MAX_ZOOM}>
          <Plus size={18} />
        </button>
        <button type="button" className="tool-btn tool-btn--text" onClick={() => setXf(FIT)} aria-label={t("zoom.fitLabel")} title={t("zoom.fitLabel")} disabled={disabled}>
          <Fit size={16} />
          <span>{t("zoom.fit")}</span>
        </button>
      </div>
    </div>
  );
}
