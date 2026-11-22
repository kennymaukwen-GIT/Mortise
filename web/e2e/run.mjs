// End-to-end tests against the real Mortise backend (Node Playwright, pre-installed on the machine).
//
//   npm run e2e                 # uses http://127.0.0.1:8000 (starts it if nothing answers), serves the UI with Vite
//   E2E_BASE=http://127.0.0.1:8000 npm run e2e   # test the UI the backend serves from web/dist
//   E2E_ONLY=convert npm run e2e                  # run tests whose name contains "convert"
//   MORTISE_SHOTS=/some/dir npm run e2e           # where screenshots go (default test-results/shots)
//
import { spawn } from "node:child_process";
import { existsSync, mkdirSync, readFileSync, statSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { chromium } from "./pw.mjs";

const here = dirname(fileURLToPath(import.meta.url));
const webRoot = resolve(here, "..");
const repo = resolve(webRoot, "..");
const fixtures = resolve(repo, "tests/fixtures");
const API = process.env.E2E_API || "http://127.0.0.1:8000";
const SHOTS = resolve(process.env.MORTISE_SHOTS || resolve(webRoot, "test-results/shots"));
const ONLY = process.env.E2E_ONLY || "";
const CONVERT_TIMEOUT = Number(process.env.E2E_CONVERT_TIMEOUT || 240000);
mkdirSync(SHOTS, { recursive: true });

const children = [];
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

async function up(url, check = () => true) {
  try {
    const r = await fetch(url, { signal: AbortSignal.timeout(3000) });
    return r.ok && check(await r.text());
  } catch {
    return false;
  }
}

async function waitFor(url, ms, check) {
  const end = Date.now() + ms;
  while (Date.now() < end) {
    if (await up(url, check)) return true;
    await sleep(500);
  }
  return false;
}

async function ensureBackend() {
  if (await up(`${API}/api/health`)) return;
  console.log(`[e2e] starting backend on ${API}`);
  const jobs = process.env.MORTISE_JOBS || resolve(webRoot, "test-results/jobs");
  mkdirSync(jobs, { recursive: true });
  const port = new URL(API).port || "8000";
  const p = spawn("python", ["-m", "uvicorn", "mortise.api.app:app", "--port", port], { cwd: repo, env: { ...process.env, MORTISE_JOBS: jobs }, stdio: "ignore" });
  children.push(p);
  if (!(await waitFor(`${API}/api/health`, 60000))) throw new Error("backend did not start");
}

async function ensureFrontend() {
  if (process.env.E2E_BASE) return process.env.E2E_BASE;
  const port = process.env.E2E_VITE_PORT || "5174";
  const base = `http://127.0.0.1:${port}`;
  if (await up(base, (t) => t.includes('id="root"'))) return base;
  console.log(`[e2e] starting Vite on ${base}`);
  const p = spawn(process.execPath, [resolve(webRoot, "node_modules/vite/bin/vite.js"), "--port", port, "--strictPort", "--host", "127.0.0.1"], { cwd: webRoot, stdio: "ignore" });
  children.push(p);
  if (!(await waitFor(base, 60000, (t) => t.includes('id="root"')))) throw new Error("vite did not start");
  return base;
}

function cleanup() {
  for (const c of children) c.kill();
}
process.on("exit", cleanup);
process.on("SIGINT", () => process.exit(130));

/* ---------------------------------------------------------------- tiny runner */

const tests = [];
const test = (name, fn) => tests.push({ name, fn });
function assert(cond, msg) {
  if (!cond) throw new Error("assertion failed: " + msg);
}
async function expectText(locator, re, timeout = 10000) {
  const end = Date.now() + timeout;
  let last = "";
  while (Date.now() < end) {
    last = (await locator.textContent().catch(() => "")) ?? "";
    if (re.test(last)) return last;
    await sleep(100);
  }
  throw new Error(`expected ${re} but got "${last}"`);
}

/* ---------------------------------------------------------------- helpers */

let BASE = "";
let browser;

async function newPage(viewport = { width: 1676, height: 947 }, opts = {}) {
  const ctx = await browser.newContext({ viewport, acceptDownloads: true, locale: "en-US", ...opts });
  const page = await ctx.newPage();
  page.errors = [];
  page.on("pageerror", (e) => page.errors.push(e.message));
  await page.goto(BASE, { waitUntil: "networkidle" });
  await page.evaluate(() => document.fonts.ready);
  return page;
}

async function chooseFile(page, file) {
  await page.setInputFiles('[data-testid="file-input"]', resolve(fixtures, file));
  await page.waitForSelector(".viewer .layer-img", { timeout: 30000 });
}

async function shot(page, name) {
  await page.waitForTimeout(250);
  await page.screenshot({ path: resolve(SHOTS, name + ".png") });
}

async function convert(page) {
  const t0 = Date.now();
  await page.click("button.convert-btn");
  await page.waitForSelector('[data-testid="progress"]', { timeout: 10000 });
  await page.waitForSelector('[data-testid="frame-score"], [data-testid="job-error"]', { timeout: CONVERT_TIMEOUT });
  const err = await page.locator('[data-testid="job-error"]').count();
  assert(!err, "conversion failed: " + (err ? await page.textContent('[data-testid="job-error"]') : ""));
  return (Date.now() - t0) / 1000;
}

async function currentJob(page) {
  const list = JSON.parse((await page.evaluate(() => localStorage.getItem("mortise.recentJobs"))) || "[]");
  return list[0];
}

/** Each test has its own browser profile: put the job in the recent list, then open it from the Account menu. */
async function openRecent(page, job) {
  await page.evaluate((j) => localStorage.setItem("mortise.recentJobs", JSON.stringify([j])), job);
  await page.reload({ waitUntil: "networkidle" });
  await page.getByRole("button", { name: "Account" }).click();
  await page.getByRole("button", { name: `Open ${job.filename}` }).first().click();
  await page.waitForSelector('[data-testid="frame-score"]', { timeout: 30000 });
}

async function waitImages(page) {
  await page.waitForFunction(() => !document.querySelector(".viewer .spinner") && (document.querySelector(".viewer .layer-img") || document.querySelector(".layer-msg")), null, { timeout: 30000 });
}

/* ---------------------------------------------------------------- tests */

test("empty state matches the mockup layout", async () => {
  const page = await newPage();
  await page.waitForFunction(() => document.querySelector("select")?.value === "svg");
  await shot(page, "empty");
  // Key boxes measured on the mockup (1676 × 947 without browser chrome).
  const box = async (sel) => page.locator(sel).first().boundingBox();
  const frame = await box(".stage");
  const card = await box(".card");
  const file = await box(".file-button");
  const acct = await box(".account-button");
  const near = (a, b, tol, what) => assert(Math.abs(a - b) <= tol, `${what}: ${a.toFixed(1)} vs mockup ${b} (±${tol})`);
  near(frame.x, 32, 3, "frame left");
  near(frame.y, 73, 3, "frame top");
  near(frame.x + frame.width, 1183, 6, "frame right");
  near(frame.y + frame.height, 919, 3, "frame bottom");
  near(card.x, 1227, 6, "card left");
  near(card.width, 414, 2, "card width");
  near(card.y, 291, 12, "card top");
  near(card.height, 413, 12, "card height");
  near(file.x, 1493, 4, "file button left");
  near(acct.x + acct.width, 1645, 4, "account right");
  await page.context().close();

  // Side-by-side picture for people.
  const mock64 = readFileSync(resolve(repo, "docs/design/ui-mockup-20261122.png")).toString("base64");
  const ours64 = readFileSync(resolve(SHOTS, "empty.png")).toString("base64");
  const ctx = await browser.newContext({ viewport: { width: 3352 + 16, height: 947 + 40 } });
  const p = await ctx.newPage();
  await p.setContent(`<body style="margin:0;background:#fff;font:14px sans-serif">
    <div style="display:flex;gap:16px;height:24px;align-items:center"><b style="width:1676px">Mockup (browser chrome removed)</b><b>Mortise UI, empty state, 1676×947</b></div>
    <div style="display:flex;gap:16px">
      <div style="width:1676px;height:947px;background:url(data:image/png;base64,${mock64}) 0 -105px no-repeat"></div>
      <img style="width:1676px;height:947px" src="data:image/png;base64,${ours64}">
    </div></body>`);
  await p.screenshot({ path: resolve(SHOTS, "compare-empty.png"), fullPage: true });
  await ctx.close();
});

test("choose a multi-page PDF and page through it", async () => {
  const page = await newPage();
  await chooseFile(page, "deck.pdf");
  const count = page.getByTestId("page-count");
  await expectText(count, /^1 \/ 3$/);
  assert((await page.textContent(".file-button")).includes("deck.pdf"), "file name shown on the File button");
  await page.waitForTimeout(400);
  await shot(page, "loaded");
  await page.getByRole("button", { name: "Next page" }).click();
  await expectText(count, /^2 \/ 3$/);
  await page.locator("body").press("ArrowRight");
  await expectText(count, /^3 \/ 3$/);
  assert(await page.getByRole("button", { name: "Next page" }).isDisabled(), "next disabled on the last page");
  await page.locator("body").press("ArrowLeft");
  await expectText(count, /^2 \/ 3$/);
  await page.getByRole("button", { name: "Show page thumbnails" }).click();
  await page.getByRole("button", { name: "Go to page 1" }).click();
  await expectText(count, /^1 \/ 3$/);
  // Zoom: buttons, wheel, fit.
  const level = page.locator(".zoom-controls__level");
  await page.getByRole("button", { name: "Zoom in" }).click();
  await expectText(level, /^125%$/);
  const stage = await page.locator(".viewer__stage").boundingBox();
  await page.mouse.move(stage.x + stage.width / 2, stage.y + stage.height / 2);
  await page.mouse.wheel(0, -400);
  await page.waitForTimeout(100);
  const zoomed = parseInt(await level.textContent(), 10);
  assert(zoomed > 125, "wheel zooms in: " + zoomed);
  // Drag to pan.
  const before = await page.locator(".viewer__page").getAttribute("style");
  await page.mouse.down();
  await page.mouse.move(stage.x + stage.width / 2 + 120, stage.y + stage.height / 2 + 60, { steps: 6 });
  await page.mouse.up();
  assert((await page.locator(".viewer__page").getAttribute("style")) !== before, "drag pans the page");
  await page.getByRole("button", { name: "Fit page to frame" }).click();
  await expectText(level, /^100%$/);
  await shot(page, "loaded-thumbs");
  assert(!page.errors.length, "no page errors: " + page.errors.join("; "));
  await page.context().close();
});

test("drag and drop a file into the frame", async () => {
  const page = await newPage();
  // Both ways of opening the file picker work.
  const [fc1] = await Promise.all([page.waitForEvent("filechooser"), page.getByRole("button", { name: "Choose a file" }).click()]);
  assert(!fc1.isMultiple(), "single file picker");
  await page.getByRole("button", { name: /File menu/ }).click();
  const [fc2] = await Promise.all([page.waitForEvent("filechooser"), page.getByRole("menuitem", { name: "Open file…" }).click()]);
  assert(fc2, "File → Open file… opens the picker");
  const b64 = readFileSync(resolve(fixtures, "slide_basic.pdf")).toString("base64");
  const dt = await page.evaluateHandle((b64) => {
    const bytes = Uint8Array.from(atob(b64), (c) => c.charCodeAt(0));
    const d = new DataTransfer();
    d.items.add(new File([bytes], "slide_basic.pdf", { type: "application/pdf" }));
    return d;
  }, b64);
  await page.dispatchEvent("section.stage", "dragenter", { dataTransfer: dt });
  await page.dispatchEvent("section.stage", "dragover", { dataTransfer: dt });
  assert(await page.locator("section.stage.is-dragover").count(), "frame highlights while dragging");
  await shot(page, "dragover");
  await page.dispatchEvent("section.stage", "drop", { dataTransfer: dt });
  await page.waitForSelector(".viewer .layer-img", { timeout: 30000 });
  assert(!(await page.locator("section.stage.is-dragover").count()), "highlight removed after drop");
  await expectText(page.getByTestId("page-count"), /^1 \/ 1$/);
  await page.context().close();
});

test("wrong files are refused inside the frame", async () => {
  const page = await newPage();
  await page.setInputFiles('[data-testid="file-input"]', { name: "notes.txt", mimeType: "text/plain", buffer: Buffer.from("hello") });
  await expectText(page.locator(".stage [role=alert]"), /notes\.txt.*not a PDF, PNG or JPEG/);
  await shot(page, "error-type");
  // A text file renamed to .pdf is caught by its content.
  await page.setInputFiles('[data-testid="file-input"]', { name: "fake.pdf", mimeType: "application/pdf", buffer: Buffer.from("this is not a pdf at all") });
  await expectText(page.locator(".stage [role=alert]"), /fake\.pdf.*not a PDF, PNG or JPEG/);
  assert(await page.getByRole("button", { name: /CONVERT/ }).getAttribute("aria-disabled"), "convert stays disabled");
  await page.context().close();
});

test("a broken PDF fails on the server: English message, no pointless Retry", async () => {
  const page = await newPage();
  // Valid enough for the browser preview? No: pdf.js refuses it too, so send it through a bigger valid file first.
  await chooseFile(page, "slide_basic.pdf");
  // Swap the file the page holds for a corrupt PDF without a preview, by intercepting the upload.
  await page.route("**/api/jobs", async (route) => {
    if (route.request().method() !== "POST") return route.continue();
    const boundary = "----e2e" + Date.now();
    const body = Buffer.concat([
      Buffer.from(`--${boundary}\r\nContent-Disposition: form-data; name="file"; filename="broken.pdf"\r\nContent-Type: application/pdf\r\n\r\n`),
      Buffer.from("%PDF-1.4\n1 0 obj << /Type /Catalog >> endobj\ntrailer << >>\n%%EOF"),
      Buffer.from(`\r\n--${boundary}\r\nContent-Disposition: form-data; name="outputs"\r\n\r\nsvg\r\n--${boundary}--\r\n`),
    ]);
    const r = await route.fetch({ headers: { ...route.request().headers(), "content-type": `multipart/form-data; boundary=${boundary}` }, postData: body });
    await route.fulfill({ response: r });
  });
  await page.click("button.convert-btn");
  await page.waitForSelector('[data-testid="job-error"]', { timeout: 120000 });
  const msg = await page.textContent('[data-testid="job-error"]');
  assert(/could not open this file/i.test(msg) && !/[\u4e00-\u9fff]/.test(msg), "English message for a damaged file: " + msg);
  assert(await page.getByText("Server message").count(), "the server's own text is behind a toggle");
  assert(!(await page.getByRole("button", { name: "Retry" }).count()), "no Retry for a file that cannot be opened");
  await shot(page, "failed");
  await page.unroute("**/api/jobs");
  const [fc] = await Promise.all([page.waitForEvent("filechooser"), page.getByRole("button", { name: "Choose another file" }).click()]);
  assert(fc, "Choose another file opens the picker");
  await page.context().close();
});

test("a network failure after a finished run does not fail the job; Retry asks again", async () => {
  const page = await newPage();
  await chooseFile(page, "slide_basic.pdf");
  // Every request for the job's pages fails until we let it through (simulates the network dropping at the end).
  let block = true;
  await page.route("**/api/jobs/*/pages", (route) => (block ? route.abort("internetdisconnected") : route.continue()));
  await page.click("button.convert-btn");
  await page.waitForSelector('[data-testid="job-error"]', { timeout: CONVERT_TIMEOUT });
  const msg = await page.textContent('[data-testid="job-error"]');
  assert(/could not be reached/.test(msg) && !/Failed to fetch/.test(msg), "network message, translated: " + msg);
  const posts = [];
  page.on("request", (r) => r.method() === "POST" && r.url().endsWith("/api/jobs") && posts.push(r.url()));
  block = false;
  await page.getByRole("button", { name: "Retry" }).click();
  await page.waitForSelector('[data-testid="frame-score"]', { timeout: CONVERT_TIMEOUT });
  assert(posts.length === 0, "Retry asked about the same job instead of uploading again");
  await page.context().close();
});

let doneJob = null;

test("convert a slide to SVG, see all four views, download", async () => {
  const page = await newPage();
  await chooseFile(page, "slide_basic.pdf");
  await page.click("button.convert-btn");
  await page.waitForSelector('[data-testid="progress"]');
  await page.waitForTimeout(1200);
  await shot(page, "converting");
  await page.waitForSelector('[data-testid="frame-score"], [data-testid="job-error"]', { timeout: CONVERT_TIMEOUT });
  assert(!(await page.locator('[data-testid="job-error"]').count()), "conversion succeeded");
  const score = await page.textContent('[data-testid="frame-score"]');
  assert(/Fidelity \d+%/.test(score), "score shown: " + score);
  assert(/\d+%/.test(await page.textContent('[data-testid="score"]')), "score in the result card");
  for (const v of ["Result", "Original", "Compare", "Overlay"]) {
    await page.getByRole("tab", { name: v }).click();
    assert((await page.getByRole("tab", { name: v }).getAttribute("aria-selected")) === "true", v + " selected");
    await waitImages(page);
    await shot(page, "done-" + v.toLowerCase());
    if (v === "Compare") {
      const slider = page.getByRole("slider");
      const b = await slider.boundingBox();
      await page.mouse.move(b.x + b.width / 2, b.y + b.height / 2);
      await page.mouse.down();
      await page.mouse.move(b.x - 150, b.y + b.height / 2, { steps: 5 });
      await page.mouse.up();
      const now = Number(await slider.getAttribute("aria-valuenow"));
      assert(now < 45, "divider moved left: " + now);
      await slider.focus();
      await page.keyboard.press("End");
      assert((await slider.getAttribute("aria-valuenow")) === "100", "End key moves the divider");
      await page.keyboard.press("Home");
      await page.keyboard.press("ArrowRight");
      assert((await slider.getAttribute("aria-valuenow")) === "2", "arrow keys move the divider");
      for (let i = 0; i < 24; i++) await page.keyboard.press("ArrowRight");
    }
  }
  const failedImgs = await page.evaluate(() => document.querySelectorAll(".layer-msg").length);
  // Download the main output and the report.
  const [dl] = await Promise.all([page.waitForEvent("download"), page.locator(".result-card a", { hasText: /^Download$/ }).click()]);
  const path = await dl.path();
  assert(dl.suggestedFilename().endsWith(".svg") || dl.suggestedFilename().endsWith(".zip"), "svg download: " + dl.suggestedFilename());
  assert(statSync(path).size > 100, "download not empty");
  const [rep] = await Promise.all([page.waitForEvent("download"), page.locator(".result-card a", { hasText: "Download report" }).click()]);
  const report = JSON.parse(readFileSync(await rep.path(), "utf8"));
  assert(typeof report.overall_score === "number", "report has a score");
  // Same downloads from the File menu.
  await page.getByRole("button", { name: /File menu/ }).click();
  const [dl2] = await Promise.all([page.waitForEvent("download"), page.getByRole("menuitem", { name: "Download", exact: true }).click()]);
  assert(dl2.suggestedFilename() === dl.suggestedFilename(), "File menu download");
  doneJob = await currentJob(page);
  console.log(`      job ${doneJob.jobId} score ${score}; ${failedImgs} layer(s) without a rendering`);
  assert(!page.errors.length, "no page errors: " + page.errors.join("; "));
  await page.context().close();
});

test("edit a text box and re-run", async () => {
  assert(doneJob, "needs the converted job from the previous test");
  const page = await newPage();
  await openRecent(page, doneJob);
  await page.getByRole("tab", { name: "Result" }).click();
  await page.getByRole("button", { name: "Edit" }).click();
  await page.waitForSelector(".el-box--text", { timeout: 30000 });
  await waitImages(page);
  await shot(page, "edit");
  await page.locator(".el-box--text").last().click();
  const dialog = page.getByRole("dialog", { name: "Edit text" });
  await dialog.waitFor();
  const size = dialog.getByLabel("Size (pt)");
  const old = Number(await size.inputValue());
  await size.fill(String(Math.round((old + 2) * 10) / 10));
  await shot(page, "edit-dialog");
  await dialog.getByRole("button", { name: "Apply" }).click();
  await page.waitForSelector('[data-testid="progress"]', { timeout: 10000 });
  await expectText(page.getByTestId("progress"), /Applying your correction/);
  await page.waitForSelector('[data-testid="progress"]', { state: "detached", timeout: CONVERT_TIMEOUT });
  assert(!(await page.locator('[data-testid="job-error"]').count()), "re-run succeeded");
  assert((await page.getByRole("button", { name: "Edit" }).getAttribute("aria-pressed")) === "true", "still in edit mode");
  const st = await (await fetch(`${API}/api/jobs/${doneJob.jobId}`)).json();
  assert(st.corrections >= 1, "server recorded the correction");
  await page.context().close();
});

test("switch language to Traditional Chinese and back", async () => {
  const page = await newPage();
  await page.getByRole("button", { name: "Account" }).click();
  await page.getByText("Accounts are coming soon").waitFor();
  await page.getByRole("radio", { name: "繁體中文" }).click();
  await page.getByRole("button", { name: /^轉換$/ }).waitFor();
  assert((await page.getAttribute("html", "lang")) === "zh-Hant", "html lang");
  await page.keyboard.press("Escape");
  assert(await page.getByText("把 PDF、PNG 或 JPEG 拖到這裡").count(), "frame text translated");
  const opt = await page.locator("select").first().locator("option:checked").textContent();
  assert(/SVG/.test(opt) && /整頁|（/.test(opt), "format names use the Chinese label: " + opt);
  await shot(page, "empty-zh");
  await page.reload({ waitUntil: "networkidle" });
  await page.getByRole("button", { name: /^轉換$/ }).waitFor();
  await page.getByRole("button", { name: "帳號" }).click();
  await page.getByRole("radio", { name: "English" }).click();
  await page.getByRole("button", { name: /^CONVERT$/ }).waitFor();
  await page.context().close();
});

test("multi-page PDF with an output size (A4) and cancel", async () => {
  const page = await newPage();
  await chooseFile(page, "deck.pdf");
  await page.getByRole("combobox", { name: "Output page size" }).selectOption("A4");
  // deck.pdf is landscape, so A4 follows it.
  assert((await page.getByRole("radio", { name: "Landscape" }).getAttribute("aria-checked")) === "true", "landscape picked from the source");
  assert((await page.getByRole("textbox", { name: "Output width in millimetres" }).getAttribute("placeholder")) === "297", "placeholder shows the A4 width");
  await page.click("button.convert-btn");
  await page.waitForSelector('[data-testid="progress"]');
  await page.waitForFunction(() => JSON.parse(localStorage.getItem("mortise.recentJobs") || "[]").length > 0);
  // Playwright cannot read multipart bodies sent by XHR, so ask the server what it received.
  const created = await currentJob(page);
  const intent = (await (await fetch(`${API}/api/jobs/${created.jobId}`)).json()).intent || {};
  const ts = intent.target_size || {};
  assert(ts.width === 297 && ts.height === 210 && ts.unit === "mm", "target size sent as 297 × 210 mm: " + JSON.stringify(ts));
  assert(!intent.known_size, "no origin size sent when left empty");
  // The progress shows the page being worked on for multi-page files.
  await page.waitForFunction(() => /Page \d of 3/.test(document.querySelector('[data-testid="progress"]')?.textContent || "") || !!document.querySelector('[data-testid="frame-score"]'), null, { timeout: CONVERT_TIMEOUT });
  await page.waitForSelector('[data-testid="frame-score"], [data-testid="job-error"]', { timeout: CONVERT_TIMEOUT });
  assert(!(await page.locator('[data-testid="job-error"]').count()), "conversion succeeded");
  await expectText(page.getByTestId("page-count"), /^1 \/ 3$/);
  await page.getByRole("tab", { name: "Compare" }).click();
  await waitImages(page);
  // Arrow keys on the focused tab list switch views (ARIA tabs); paging uses the button here.
  await page.keyboard.press("ArrowRight");
  assert((await page.getByRole("tab", { name: "Overlay" }).getAttribute("aria-selected")) === "true", "→ on the tab list selects the next view");
  await page.keyboard.press("ArrowLeft");
  await page.getByRole("button", { name: "Next page" }).click();
  await expectText(page.getByTestId("page-count"), /^2 \/ 3$/);
  await waitImages(page);
  await shot(page, "done-a4-compare");
  const job = await currentJob(page);
  const pages = await (await fetch(`${API}/api/jobs/${job.jobId}/pages`)).json();
  console.log(`      pages[0]: ${Math.round(pages[0].width_pt)}×${Math.round(pages[0].height_pt)} pt, placement ${JSON.stringify(pages[0].placement ?? null)}`);
  // Cancel a running conversion: the UI goes back to the file and the job leaves the list.
  await page.getByRole("button", { name: "Convert again" }).click();
  await page.click("button.convert-btn");
  await page.waitForSelector('[data-testid="progress"]');
  await page.waitForTimeout(800);
  const running = await currentJob(page);
  await page.getByRole("button", { name: "Cancel" }).click();
  await page.waitForSelector('[data-testid="progress"]', { state: "detached" });
  await expectText(page.getByTestId("page-count"), /^\d \/ 3$/);
  assert((await currentJob(page))?.jobId !== running.jobId, "cancelled job removed from recent list");
  // The server keeps a running job until it stops; the page deletes it right after.
  const end = Date.now() + CONVERT_TIMEOUT;
  let code = 0;
  while (Date.now() < end && (code = (await fetch(`${API}/api/jobs/${running.jobId}`)).status) !== 404) await sleep(1000);
  assert(code === 404, "cancelled job deleted on the server (last status " + code + ")");
  await page.context().close();
});

test("start over and delete from server", async () => {
  assert(doneJob, "needs the converted job");
  const page = await newPage();
  await openRecent(page, doneJob);
  await page.getByRole("button", { name: /File menu/ }).click();
  await page.getByRole("menuitem", { name: "Delete from server" }).click();
  await page.getByText("Drop a PDF, PNG or JPEG here").waitFor();
  const r = await fetch(`${API}/api/jobs/${doneJob.jobId}`);
  assert(r.status === 404, "job deleted on the server (got " + r.status + ")");
  await chooseFile(page, "slide_basic.pdf");
  await page.getByRole("button", { name: /File menu/ }).click();
  await page.getByRole("menuitem", { name: "Start over" }).click();
  await page.getByText("Drop a PDF, PNG or JPEG here").waitFor();
  await page.context().close();
});

test("mobile layout", async () => {
  const page = await newPage({ width: 390, height: 844 }, { deviceScaleFactor: 2, isMobile: true, hasTouch: true });
  await shot(page, "mobile-empty");
  await chooseFile(page, "deck.pdf");
  await expectText(page.getByTestId("page-count"), /^1 \/ 3$/);
  const frame = await page.locator(".stage").boundingBox();
  const card = await page.locator(".card").boundingBox();
  assert(card.y > frame.y + frame.height - 1, "card below the frame on a phone");
  assert(frame.width <= 390 && card.width <= 390, "nothing wider than the screen");
  const sw = await page.evaluate(() => document.documentElement.scrollWidth);
  assert(sw <= 391, "no horizontal scrolling: " + sw);
  await shot(page, "mobile");
  await page.evaluate(() => window.scrollTo(0, document.body.scrollHeight));
  await shot(page, "mobile-card");
  await page.context().close();
  const tablet = await newPage({ width: 1024, height: 768 });
  const f2 = await tablet.locator(".stage").boundingBox();
  const c2 = await tablet.locator(".card").boundingBox();
  const b2 = await tablet.locator("button.convert-btn").boundingBox();
  assert(c2.x >= f2.x + f2.width, "card beside the frame at 1024 px");
  assert(b2.y + b2.height <= 768, "CONVERT on the first screen at 1024×768: bottom " + Math.round(b2.y + b2.height));
  await shot(tablet, "tablet-empty");
  await tablet.context().close();
  const narrow = await newPage({ width: 768, height: 1024 });
  const f3 = await narrow.locator(".stage").boundingBox();
  const c3 = await narrow.locator(".card").boundingBox();
  assert(c3.y >= f3.y + f3.height && c3.y < 1024, "card below the frame under 900 px, its top on the first screen");
  await narrow.context().close();
});

test("dragging a file over the frame shows only the drop hint", async () => {
  const page = await newPage();
  const dt = await page.evaluateHandle(() => {
    const d = new DataTransfer();
    d.items.add(new File(["%PDF-1.4"], "x.pdf", { type: "application/pdf" }));
    return d;
  });
  await page.dispatchEvent("section.stage", "dragenter", { dataTransfer: dt });
  assert((await page.locator(".empty").evaluate((el) => getComputedStyle(el).visibility)) === "hidden", "empty-state text hidden under the hint");
  const bg = await page.locator(".drop-hint").evaluate((el) => getComputedStyle(el).backgroundColor);
  assert(bg === "rgb(11, 12, 13)", "opaque hint background: " + bg);
  await page.context().close();
});

test("sizes outside 12.7–5080 mm are stopped on the card, nothing is uploaded", async () => {
  const page = await newPage();
  await chooseFile(page, "slide_basic.pdf");
  const posts = [];
  page.on("request", (r) => r.method() === "POST" && r.url().endsWith("/api/jobs") && posts.push(r.url()));
  await page.getByRole("textbox", { name: "Original width in millimetres" }).fill("100000");
  await page.getByRole("textbox", { name: "Original height in millimetres" }).fill("50000");
  await expectText(page.locator(".card .card-error"), /between 12\.7 and 5080 mm/);
  await page.click("button.convert-btn");
  await page.waitForTimeout(500);
  assert(posts.length === 0 && !(await page.getByTestId("progress").count()), "no upload for an impossible origin size");
  await page.getByRole("textbox", { name: "Original width in millimetres" }).fill("");
  await page.getByRole("textbox", { name: "Original height in millimetres" }).fill("");
  await page.getByRole("textbox", { name: "Output width in millimetres" }).fill("5");
  await page.getByRole("textbox", { name: "Output height in millimetres" }).fill("5");
  await expectText(page.locator(".card .card-error"), /between 12\.7 and 5080 mm/);
  await page.click("button.convert-btn");
  await page.waitForTimeout(500);
  assert(posts.length === 0, "no upload for a 5 × 5 mm output");
  await page.context().close();
});

test("opening a second file shows the second file (no stale preview)", async () => {
  const page = await newPage();
  await chooseFile(page, "deck.pdf");
  await waitImages(page);
  const first = await page.locator(".viewer .layer-img").getAttribute("src");
  await page.setInputFiles('[data-testid="file-input"]', resolve(fixtures, "slide_basic.pdf"));
  await expectText(page.getByTestId("page-count"), /^1 \/ 1$/);
  await page.waitForFunction((src) => { const im = document.querySelector(".viewer .layer-img"); return im && im.getAttribute("src") !== src && im.complete && im.naturalWidth > 0; }, first, { timeout: 30000 });
  await page.context().close();
});

test("Word output: the preview says Mortise drew it; reload comes back to the job", async () => {
  const page = await newPage();
  await chooseFile(page, "slide_basic.pdf");
  await page.getByRole("combobox", { name: "Output format" }).selectOption("docx_fixed");
  assert((await page.locator(".pill-select__value").first().textContent()) === "Word, fixed layout", "closed menu shows the short name");
  await convert(page);
  await page.getByRole("tab", { name: "Result" }).click();
  await waitImages(page);
  const note = page.getByTestId("render-note");
  await note.waitFor({ timeout: 30000 });
  assert((await note.getAttribute("data-render")) === "neutral", "Word preview drawn by the neutral renderer");
  assert(/drawn by Mortise itself, not by Word/.test(await note.textContent()), "plain-language note: " + (await note.textContent()));
  assert(/Fidelity \d+%/.test(await page.textContent('[data-testid="frame-score"]')), "score shown for Word");
  await shot(page, "done-docx-neutral");
  const url = new URL(page.url());
  const job = url.searchParams.get("job");
  assert(job, "the job id is in the address");
  await page.reload({ waitUntil: "networkidle" });
  await page.waitForSelector('[data-testid="frame-score"]', { timeout: 30000 });
  assert((await page.getByRole("combobox", { name: "Output format" }).inputValue()) === "docx_fixed", "card filled back in from the job");
  await page.context().close();
});

test("Cancel during a correction re-run keeps the job and the result", async () => {
  const page = await newPage();
  await chooseFile(page, "slide_basic.pdf");
  await convert(page);
  const job = await currentJob(page);
  await page.getByRole("tab", { name: "Result" }).click();
  await page.getByRole("button", { name: "Edit" }).click();
  await page.waitForSelector(".el-box--text", { timeout: 30000 });
  await page.locator(".el-box--text").first().click();
  const dialog = page.getByRole("dialog", { name: "Edit text" });
  await dialog.waitFor();
  assert(await dialog.locator("textarea").first().evaluate((el) => el === document.activeElement), "focus starts in the first text field");
  await dialog.locator("textarea").first().fill("CHANGED TITLE");
  await dialog.getByRole("button", { name: "Apply" }).click();
  await page.waitForSelector('[data-testid="progress"]', { timeout: 10000 });
  await page.getByRole("button", { name: "Show the current result" }).click();
  await page.waitForSelector('[data-testid="progress"]', { state: "detached", timeout: 10000 });
  assert(await page.getByTestId("frame-score").count(), "previous result still shown");
  assert((await fetch(`${API}/api/jobs/${job.jobId}`)).status === 200, "job still on the server");
  // The re-run finishes in the background; the job keeps the correction.
  const end = Date.now() + CONVERT_TIMEOUT;
  let st = null;
  while (Date.now() < end) {
    st = await (await fetch(`${API}/api/jobs/${job.jobId}`)).json();
    if (st.status === "done" && st.corrections >= 1) break;
    await sleep(1000);
  }
  assert(st && st.status === "done" && st.corrections >= 1, "re-run finished with the correction: " + JSON.stringify(st && { s: st.status, c: st.corrections }));
  assert((await currentJob(page))?.jobId === job.jobId, "job still in the recent list");
  await page.context().close();
});

test("report download works with a Chinese file name", async () => {
  const page = await newPage();
  const buf = readFileSync(resolve(fixtures, "slide_basic.pdf"));
  await page.setInputFiles('[data-testid="file-input"]', { name: "季度報告 (final).pdf", mimeType: "application/pdf", buffer: buf });
  await page.waitForSelector(".viewer .layer-img", { timeout: 30000 });
  await convert(page);
  const [rep] = await Promise.all([page.waitForEvent("download"), page.locator(".result-card a", { hasText: "Download report" }).click()]);
  assert((await rep.failure()) === null, "report downloaded");
  const report = JSON.parse(readFileSync(await rep.path(), "utf8"));
  assert(typeof report.overall_score === "number", "the download is the report");
  // Headless Chromium names every non-ASCII download "download" (also the main file, which Starlette names), so check the header.
  const job = await currentJob(page);
  const cd = (await fetch(`${API}/api/jobs/${job.jobId}/download/report`)).headers.get("content-disposition") || "";
  assert(cd.includes("filename*=UTF-8''" + encodeURIComponent("季度報告 (final).report.json").replace(/[()]/g, (c) => "%" + c.charCodeAt(0).toString(16).toUpperCase())), "UTF-8 file name in the header: " + cd);
  await page.context().close();
});

test("keyboard: Esc gives focus back; Tab out closes the menu; orientation arrows do not turn pages", async () => {
  const page = await newPage();
  await chooseFile(page, "deck.pdf");
  const fileBtn = page.getByRole("button", { name: /File menu/ });
  await fileBtn.click();
  await page.keyboard.press("Escape");
  assert(await fileBtn.evaluate((el) => el === document.activeElement), "Esc returns focus to the File button");
  await fileBtn.click();
  for (let i = 0; i < 6; i++) await page.keyboard.press("Tab");
  assert(!(await page.getByRole("menu").count()), "menu closed when focus left it");
  await page.getByRole("combobox", { name: "Output page size" }).selectOption("A4");
  const land = page.getByRole("radio", { name: "Landscape" });
  await land.focus();
  await page.keyboard.press("ArrowLeft");
  await expectText(page.getByTestId("page-count"), /^1 \/ 3$/);
  assert((await page.getByRole("radio", { name: "Portrait" }).getAttribute("aria-checked")) === "true", "← chose portrait");
  assert((await page.getByRole("radio", { name: "Portrait" }).getAttribute("tabindex")) === "0", "roving tabindex");
  await page.context().close();
});

/* ---------------------------------------------------------------- main */

let failed = 0;
try {
  await ensureBackend();
  BASE = await ensureFrontend();
  console.log(`[e2e] UI ${BASE}  API ${API}  shots ${SHOTS}`);
  browser = await chromium.launch();
  for (const t of tests) {
    if (ONLY && !t.name.includes(ONLY)) continue;
    const t0 = Date.now();
    try {
      await t.fn();
      console.log(`  ok   ${t.name} (${((Date.now() - t0) / 1000).toFixed(1)}s)`);
    } catch (e) {
      failed++;
      console.log(`  FAIL ${t.name} (${((Date.now() - t0) / 1000).toFixed(1)}s)\n       ${String(e && e.stack ? e.stack : e).split("\n").slice(0, 4).join("\n       ")}`);
    }
  }
} catch (e) {
  failed++;
  console.error("[e2e] setup failed:", e);
} finally {
  await browser?.close();
  cleanup();
}
console.log(failed ? `[e2e] ${failed} failed` : "[e2e] all passed");
process.exit(failed ? 1 : 0);
