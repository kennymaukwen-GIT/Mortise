// Visual check: screenshot the empty state at the mockup's size and put it next to the mockup.
// Usage: node e2e/shots.mjs [baseUrl] [outDir]
import { chromium } from "./pw.mjs";
import { mkdirSync } from "node:fs";
import { resolve, dirname } from "node:path";
import { fileURLToPath } from "node:url";

const here = dirname(fileURLToPath(import.meta.url));
const base = process.argv[2] || process.env.MORTISE_UI || "http://127.0.0.1:5173";
const out = resolve(process.argv[3] || process.env.MORTISE_SHOTS || resolve(here, "../test-results/shots"));
mkdirSync(out, { recursive: true });

const mockup = resolve(here, "../../docs/design/ui-mockup-20261122.png");

const browser = await chromium.launch();
const page = await browser.newPage({ viewport: { width: 1676, height: 947 }, deviceScaleFactor: 1 });
await page.goto(base, { waitUntil: "networkidle" });
await page.evaluate(() => document.fonts.ready);
await page.waitForTimeout(300);
await page.screenshot({ path: resolve(out, "empty.png") });

// Side by side (mockup without the 105 px of Safari chrome | ours), plus a 50% blend underneath.
const html = `<html><body style="margin:0;background:#888">
<div style="display:flex;gap:0">
  <div style="width:1676px;height:947px;background:url('data:image/png;base64,MOCK') 0 -105px no-repeat"></div>
  <img src="data:image/png;base64,OURS" style="width:1676px;height:947px">
</div>
<div style="position:relative;width:1676px;height:947px;margin-top:0">
  <div style="position:absolute;inset:0;background:url('data:image/png;base64,MOCK') 0 -105px no-repeat"></div>
  <img src="data:image/png;base64,OURS" style="position:absolute;inset:0;opacity:.5">
</div></body></html>`;
const fs = await import("node:fs");
const mock64 = fs.readFileSync(mockup).toString("base64");
const ours64 = fs.readFileSync(resolve(out, "empty.png")).toString("base64");
const cmp = await browser.newPage({ viewport: { width: 3352, height: 1894 }, deviceScaleFactor: 0.5 });
await cmp.setContent(html.replaceAll("MOCK", mock64).replaceAll("OURS", ours64), { waitUntil: "load" });
await cmp.screenshot({ path: resolve(out, "compare-empty.png"), clip: { x: 0, y: 0, width: 3352, height: 947 } });
await cmp.screenshot({ path: resolve(out, "blend-empty.png"), clip: { x: 0, y: 947, width: 1676, height: 947 } });
await browser.close();
console.log("wrote", out);
