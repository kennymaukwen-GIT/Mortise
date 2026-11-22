import { checkPair, fmtMm, mmToPt, orient, orientationOf, parsePositive, ptToMm, widthBucket } from "./units";

describe("units", () => {
  it("converts points and millimetres", () => {
    expect(ptToMm(72)).toBeCloseTo(25.4);
    expect(mmToPt(25.4)).toBeCloseTo(72);
    expect(ptToMm(595.28)).toBeCloseTo(210, 0);
  });

  it("formats millimetres for display", () => {
    expect(fmtMm(209.97)).toBe("210");
    expect(fmtMm(25.44)).toBe("25.4");
  });

  it("parses positive numbers", () => {
    expect(parsePositive("")).toBeNull();
    expect(parsePositive(" 12 ")).toBe(12);
    expect(parsePositive("12,5")).toBe(12.5);
    expect(parsePositive(".5")).toBe(0.5);
    expect(parsePositive("0")).toBeNaN();
    expect(parsePositive("-3")).toBeNaN();
    expect(parsePositive("abc")).toBeNaN();
  });

  it("checks W/H pairs", () => {
    expect(checkPair("", "")).toEqual({ kind: "empty" });
    expect(checkPair("210", "")).toEqual({ kind: "incomplete" });
    expect(checkPair("", "297")).toEqual({ kind: "incomplete" });
    expect(checkPair("0", "297")).toEqual({ kind: "invalid" });
    expect(checkPair("x", "")).toEqual({ kind: "invalid" });
    expect(checkPair("210", "297")).toEqual({ kind: "ok", width: 210, height: 297 });
    expect(checkPair("100000", "50000")).toEqual({ kind: "range" });
    expect(checkPair("0.01", "0.01")).toEqual({ kind: "range" });
  });

  it("orients sizes", () => {
    expect(orientationOf(842, 595)).toBe("landscape");
    expect(orientationOf(595, 842)).toBe("portrait");
    expect(orientationOf(500, 500)).toBe("portrait");
    expect(orient(297, 420, "landscape")).toEqual({ width: 420, height: 297 });
    expect(orient(420, 297, "portrait")).toEqual({ width: 297, height: 420 });
  });

  it("buckets image widths", () => {
    expect(widthBucket(10)).toBe(600);
    expect(widthBucket(601)).toBe(1000);
    expect(widthBucket(2300)).toBe(2400);
    expect(widthBucket(99999)).toBe(4000);
  });
});
