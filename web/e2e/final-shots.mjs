// Screenshots of the main states for review, at the mockup's size (1676 × 947) and on a phone (390 × 844).
// Needs a running backend that serves the built UI (or pass the Vite URL).
//
//   node e2e/final-shots.mjs [baseUrl] [outDir]
//
// Writes 00-mockup-vs-app.png (mockup without the browser chrome | our empty state), 01-empty, 02-loaded (deck.pdf, page 2),
// 03-converting, 04-result, 05-compare, 06-overlay, 07-edit, 08-account-menu and 09-mobile.
import { mkdirSync, readFileSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { chromium } from "./pw.mjs";

const here = dirname(fileURLToPath(import.meta.url));
const repo = resolve(here, "../..");
const base = process.argv[2] || process.env.MORTISE_UI || "http://127.0.0.1:8000";
const out = resolve(process.argv[3] || process.env.MORTISE_SHOTS || resolve(here, "../test-results/final"));
mkdirSync(out, { recursive: true });
const fixture = (n) => resolve(repo, "tests/fixtures", n);

const browser = await chromium.launch();

async function open(viewport, opts = {}) {
  const ctx = await browser.newContext({ viewport, locale: "en-US", deviceScaleFactor: 1, ...opts });
  const page = await ctx.newPage();
  await page.goto(base, { waitUntil: "networkidle" });
  await page.evaluate(() => document.fonts.ready);
  await page.waitForFunction(() => document.querySelector("select")?.value === "svg");
  return page;
}
const settle = async (page) => {
  await page.waitForFunction(() => !document.querySelector(".viewer .spinner") && (document.querySelector(".viewer .layer-img") || document.querySelector(".layer-msg")), null, { timeout: 60000 });
  await page.waitForTimeout(400);
};
const shot = (page, name) => page.screenshot({ path: resolve(out, name + ".png") });

// 01 empty, 00 side by side with the mockup
const page = await open({ width: 1676, height: 947 });
await page.waitForTimeout(300);
await shot(page, "01-empty");
{
  const mock64 = readFileSync(resolve(repo, "docs/design/ui-mockup-20261122.png")).toString("base64");
  const ours64 = readFileSync(resolve(out, "01-empty.png")).toString("base64");
  const cmp = await browser.newPage({ viewport: { width: 1676 * 2 + 24, height: 947 + 44 } });
  await cmp.setContent(`<body style="margin:0;background:#fff;font:600 18px system-ui,sans-serif">
    <div style="display:flex;gap:24px;height:44px;align-items:center"><span style="width:1676px;padding-left:12px">Mockup (Safari chrome removed)</span><span>Mortise UI — empty state, 1676 × 947</span></div>
    <div style="display:flex;gap:24px">
      <div style="width:1676px;height:947px;background:url(data:image/png;base64,${mock64}) 0 -105px no-repeat"></div>
      <img style="width:1676px;height:947px" src="data:image/png;base64,${ours64}">
    </div></body>`);
  await cmp.waitForTimeout(200);
  await cmp.screenshot({ path: resolve(out, "00-mockup-vs-app.png"), fullPage: true });
  await cmp.close();
}

// 02 loaded, page 2 of deck.pdf
await page.setInputFiles('[data-testid="file-input"]', fixture("deck.pdf"));
await page.waitForSelector(".viewer .layer-img", { timeout: 30000 });
await page.getByRole("button", { name: "Next page" }).click();
await page.waitForFunction(() => document.querySelector('[data-testid="page-count"]')?.textContent === "2 / 3");
await settle(page);
await shot(page, "02-loaded");

// 03 converting
await page.click("button.convert-btn");
await page.waitForSelector('[data-testid="progress"]');
await page.waitForFunction(() => /Step [2-6]|Page \d of/.test(document.querySelector('[data-testid="progress"]')?.textContent || ""), null, { timeout: 60000 }).catch(() => undefined);
await page.waitForTimeout(300);
await shot(page, "03-converting");

// 04–06 finished views (page 2)
await page.waitForSelector('[data-testid="frame-score"], [data-testid="job-error"]', { timeout: 300000 });
// The finished job opens on the page that was shown (page 2); move there if not.
for (let i = 0; i < 3; i++) {
  const now = await page.textContent('[data-testid="page-count"]');
  if (now === "2 / 3") break;
  await page.getByRole("button", { name: now?.startsWith("1") ? "Next page" : "Previous page" }).click();
  await page.waitForTimeout(150);
}
await page.waitForFunction(() => document.querySelector('[data-testid="page-count"]')?.textContent === "2 / 3");
await page.getByRole("tab", { name: "Result" }).click();
await settle(page);
await shot(page, "04-result");
await page.getByRole("tab", { name: "Compare" }).click();
await settle(page);
await shot(page, "05-compare");
await page.getByRole("tab", { name: "Overlay" }).click();
await settle(page);
await shot(page, "06-overlay");

// 07 edit: element boxes and the correction dialog
await page.getByRole("tab", { name: "Result" }).click();
await page.getByRole("button", { name: "Edit" }).click();
await page.waitForSelector(".el-box--text", { timeout: 30000 });
await settle(page);
await page.locator(".el-box--text").first().click();
await page.getByRole("dialog").waitFor();
await page.waitForTimeout(300);
await shot(page, "07-edit");
await page.keyboard.press("Escape");
await page.getByRole("button", { name: "Edit" }).click();

// 08 account menu
await page.getByRole("button", { name: "Account" }).click();
await page.getByText("Accounts are coming soon").waitFor();
await page.waitForTimeout(200);
await shot(page, "08-account-menu");
await page.keyboard.press("Escape");
const jobUrl = page.url();
await page.context().close();

// 09 phone: the same finished job (opened from its address), Result view
const phone = await browser.newContext({ viewport: { width: 390, height: 844 }, locale: "en-US", deviceScaleFactor: 2, isMobile: true, hasTouch: true });
const pp = await phone.newPage();
await pp.goto(jobUrl, { waitUntil: "networkidle" });
await pp.evaluate(() => document.fonts.ready);
await pp.waitForSelector('[data-testid="frame-score"]', { state: "attached", timeout: 60000 });
await pp.getByRole("tab", { name: "Result" }).click();
await settle(pp);
await pp.screenshot({ path: resolve(out, "09-mobile.png") });
await phone.close();

await browser.close();
console.log("wrote", out);
