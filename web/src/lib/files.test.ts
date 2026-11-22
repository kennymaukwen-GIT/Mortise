import { checkFile, mimeOf, shortName, sniff } from "./files";

const ACCEPTED = ["application/pdf", "image/png", "image/jpeg"];

describe("files", () => {
  it("falls back to the extension when the browser gives no type", () => {
    expect(mimeOf({ name: "scan.PDF", type: "" })).toBe("application/pdf");
    expect(mimeOf({ name: "photo.jpg", type: "application/octet-stream" })).toBe("image/jpeg");
    expect(mimeOf({ name: "x.png", type: "image/png" })).toBe("image/png");
    expect(mimeOf({ name: "x", type: "image/jpg" })).toBe("image/jpeg");
  });

  it("rejects unsupported types and oversize files", () => {
    expect(checkFile({ name: "a.docx", type: "application/vnd.openxmlformats-officedocument.wordprocessingml.document", size: 10 }, ACCEPTED, 100)).toEqual({ ok: false, reason: "type" });
    const big = checkFile({ name: "a.pdf", type: "application/pdf", size: 101 * 1024 * 1024 }, ACCEPTED, 100);
    expect(big.ok).toBe(false);
    expect(big.ok === false && big.reason).toBe("size");
    expect(checkFile({ name: "a.pdf", type: "application/pdf", size: 1000 }, ACCEPTED, 100)).toEqual({ ok: true, kind: "pdf", mime: "application/pdf" });
    expect(checkFile({ name: "a.png", type: "image/png", size: 1000 }, ACCEPTED, 100)).toMatchObject({ ok: true, kind: "image" });
  });

  it("sniffs magic bytes", async () => {
    expect(await sniff(new Blob(["%PDF-1.7\n..."]))).toBe("pdf");
    expect(await sniff(new Blob([new Uint8Array([0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a])]))).toBe("png");
    expect(await sniff(new Blob([new Uint8Array([0xff, 0xd8, 0xff, 0xe0])]))).toBe("jpeg");
    expect(await sniff(new Blob(["hello"]))).toBeNull();
  });

  it("shortens long names but keeps the extension", () => {
    expect(shortName("short.pdf")).toBe("short.pdf");
    const s = shortName("a-very-long-file-name-for-the-quarterly-report.pdf", 20);
    expect(s.length).toBeLessThanOrEqual(20);
    expect(s.endsWith(".pdf")).toBe(true);
    expect(s).toContain("…");
  });
});
