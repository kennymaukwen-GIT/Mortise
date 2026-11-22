// An <img> that keeps showing the previous resolution while a sharper one loads,
// and reports failures instead of showing a broken image.
import { useEffect, useRef, useState } from "react";

export type Loader = () => Promise<string>;

export function preload(src: string): Promise<string> {
  return new Promise((resolve, reject) => {
    const img = new Image();
    img.decoding = "async";
    img.onload = () => resolve(src);
    img.onerror = () => reject(new Error("load"));
    img.src = src;
  });
}

interface Props {
  /** Identifies the picture regardless of resolution; when it changes, the old picture is dropped at once. */
  baseKey: string;
  /** Identifies this exact resolution. */
  srcKey: string;
  load: Loader;
  alt: string;
  className?: string;
  style?: React.CSSProperties;
  onState?: (s: "loading" | "ready" | "error") => void;
}

export function SmartImg({ baseKey, srcKey, load, alt, className, style, onState }: Props) {
  const [shown, setShown] = useState<{ base: string; src: string } | null>(null);
  const [error, setError] = useState(false);
  const loadRef = useRef(load);
  loadRef.current = load;
  const stateRef = useRef(onState);
  stateRef.current = onState;

  useEffect(() => {
    let alive = true;
    setError(false);
    setShown((s) => (s && s.base === baseKey ? s : null));
    stateRef.current?.("loading");
    loadRef
      .current()
      .then((src) => {
        if (!alive) return;
        setShown({ base: baseKey, src });
        stateRef.current?.("ready");
      })
      .catch(() => {
        if (!alive) return;
        setError(true);
        stateRef.current?.("error");
      });
    return () => {
      alive = false;
    };
  }, [baseKey, srcKey]);

  if (error && !shown) return null;
  if (!shown || shown.base !== baseKey) return null;
  return <img src={shown.src} alt={alt} className={className} style={style} draggable={false} />;
}
