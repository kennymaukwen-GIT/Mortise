import { createContext, useCallback, useContext, useEffect, useMemo, type ReactNode } from "react";
import { translate, type Lang, type MessageKey } from "./dict";

export type TFn = (key: MessageKey, vars?: Record<string, string | number>) => string;

interface I18nValue {
  lang: Lang;
  t: TFn;
}

const Ctx = createContext<I18nValue>({ lang: "en", t: (k, v) => translate("en", k, v) });

export function I18nProvider({ lang, children }: { lang: Lang; children: ReactNode }) {
  const t = useCallback<TFn>((k, v) => translate(lang, k, v), [lang]);
  useEffect(() => {
    document.documentElement.lang = lang === "zh-Hant" ? "zh-Hant" : "en";
    document.title = translate(lang, "app.title");
  }, [lang]);
  const value = useMemo(() => ({ lang, t }), [lang, t]);
  return <Ctx.Provider value={value}>{children}</Ctx.Provider>;
}

export function useI18n(): I18nValue {
  return useContext(Ctx);
}
