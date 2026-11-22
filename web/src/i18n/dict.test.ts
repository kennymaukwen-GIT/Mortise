import { en, translate, zhHant } from "./dict";

describe("i18n", () => {
  it("has every key in both languages", () => {
    expect(Object.keys(zhHant).sort()).toEqual(Object.keys(en).sort());
    for (const [k, v] of Object.entries(zhHant)) expect(v, k).toBeTruthy();
  });

  it("keeps the same placeholders in both languages", () => {
    const vars = (s: string) => (s.match(/\{\w+\}/g) ?? []).sort().join(",");
    for (const k of Object.keys(en) as (keyof typeof en)[]) expect(vars(zhHant[k]), k).toBe(vars(en[k]));
  });

  it("fills in variables", () => {
    expect(translate("en", "nav.pageOf", { n: 2, total: 5 })).toBe("2 / 5");
    expect(translate("zh-Hant", "progress.stage", { n: 3, name: "辨識" })).toBe("第 3 / 7 步 · 辨識");
  });

  it("uses the mockup's English labels", () => {
    expect(en["card.convertTo"]).toBe("Convert to:");
    expect(en["card.originSize"]).toBe("Origin Size:");
    expect(en["card.size"]).toBe("Size:");
    expect(en["card.convert"]).toBe("CONVERT");
    expect(en["file.button"]).toBe("File");
  });
});
