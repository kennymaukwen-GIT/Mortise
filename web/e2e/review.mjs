// End-to-end: a phone photo whose paper is cut off → yellow "please check" bar → drag a corner → Apply → re-run →
// the bar is gone; "something is wrong here" and a page rating are saved; the feedback bundle downloads.
// Runs against the real backend serving web/dist (npm run build first). The photo is synthetic (web/e2e/make_photo.py).
//
//   E2E_BASE=http://127.0.0.1:8765 node e2e/review.mjs
//   MORTISE_SHOTS=/some/dir …        where screenshots go (default test-results/review-shots)
//   E2E_ONLY=mobile …                run only tests whose name contains "mobile"
//
import { execFileSync } from "node:child_process";
import { mkdirSync, readFileSync, statSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { chromium } from "./pw.mjs";

const here = dirname(fileURLToPath(import.meta.url));
const webRoot = resolve(here, "..");
const repo = resolve(webRoot, "..");
const BASE = process.env.E2E_BASE || "http://127.0.0.1:8000";
const SHOTS = resolve(process.env.MORTISE_SHOTS || resolve(webRoot, "test-results/review-shots"));
const WORK = resolve(webRoot, "test-results/review-work");
const ONLY = process.env.E2E_ONLY || "";
const CONVERT_TIMEOUT = Number(process.env.E2E_CONVERT_TIMEOUT || 300000);
mkdirSync(SHOTS, { recursive: true });
mkdirSync(WORK, { recursive: true });

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
function assert(cond, msg) {
  if (!cond) throw new Error("assertion failed: " + msg);
}
const tests = [];
const test = (name, fn) => tests.push({ name, fn });

// Synthetic phone photo (a fictional fixture page), cut off at the bottom so step ② asks for the corners.
const PHOTO = resolve(WORK, "poster-photo.jpg");
execFileSync("python3", [resolve(here, "make_photo.py"), PHOTO, "cut", "7"], { cwd: repo, stdio: "inherit" });

let browser;

async function newPage(viewport, lang = "zh-Hant") {
  const ctx = await browser.newContext({ viewport, deviceScaleFactor: 2, acceptDownloads: true, locale: lang === "en" ? "en-US" : "zh-TW" });
  await ctx.addInitScript((l) => {
    if (!localStorage.getItem("mortise.lang")) localStorage.setItem("mortise.lang", l);
  }, lang);
  const page = await ctx.newPage();
  page.errors = [];
  page.on("pageerror", (e) => page.errors.push(e.message));
  await page.goto(BASE, { waitUntil: "networkidle" });
  await page.evaluate(() => document.fonts.ready);
  return page;
}

async function shot(page, name) {
  await page.waitForTimeout(300);
  await page.screenshot({ path: resolve(SHOTS, name + ".png") });
  console.log(`    [shot] ${name}.png`);
}

async function api(path, init) {
  const r = await fetch(BASE + path, init);
  if (!r.ok) throw new Error(`${path}: HTTP ${r.status} ${await r.text()}`);
  return r.headers.get("content-type")?.includes("json") ? r.json() : r.arrayBuffer();
}

/** Upload the photo as an editable PDF and wait for the result. */
async function convertPhoto(page) {
  await page.setInputFiles('[data-testid="file-input"]', PHOTO);
  await page.waitForSelector(".viewer .layer-img", { timeout: 30000 });
  await page.locator(".card select").first().selectOption("pdf");
  const t0 = Date.now();
  await page.click("button.convert-btn");
  await page.waitForSelector('[data-testid="progress"]', { timeout: 15000 });
  // (The score chip is hidden on phones: wait for the frame to say the job is done.)
  await page.waitForSelector('.stage[data-phase="done"], [data-testid="job-error"]', { timeout: CONVERT_TIMEOUT });
  assert(!(await page.locator('[data-testid="job-error"]').count()), "conversion failed");
  const list = JSON.parse((await page.evaluate(() => localStorage.getItem("mortise.recentJobs"))) || "[]");
  return { jobId: list[0].jobId, seconds: (Date.now() - t0) / 1000 };
}

async function waitStill(page) {
  await page.waitForFunction(() => !document.querySelector(".viewer .spinner"), null, { timeout: 30000 });
}

function cornerFromLabel(label) {
  const m = /x (-?\d+)[^\d-]+y (-?\d+)/.exec(label);
  assert(m, "corner label has coordinates: " + label);
  return [Number(m[1]), Number(m[2])];
}

/* ---------------------------------------------------------------- tests */

test("desktop: yellow bar → drag a corner → apply → re-run → bar gone; report, rating, export", async () => {
  const page = await newPage({ width: 1676, height: 947 });
  const { jobId, seconds } = await convertPhoto(page);
  console.log(`    converted in ${seconds.toFixed(1)} s (job ${jobId})`);

  // 1. The pale yellow bar explains why and offers to adjust the page.
  const bar = page.getByTestId("review-bar");
  await bar.waitFor({ timeout: 15000 });
  await page.waitForFunction(() => /請拖曳四角確認/.test(document.querySelector('[data-testid="review-bar"]')?.textContent ?? ""));
  const barText = await bar.textContent();
  assert(/超出/.test(barText), "the bar says the paper goes out of the photo: " + barText);
  const bg = await bar.evaluate((el) => getComputedStyle(el).backgroundColor);
  assert(/rgba\(255, 230, 90, 0\.2/.test(bg), "pale, low-opacity yellow: " + bg);
  await waitStill(page);
  await shot(page, "01-desktop-review-bar");

  const before = await api(`/api/jobs/${jobId}/pages/1/geometry`);
  assert(before.needs_review === true && before.status === "cut_off", "server asks for review: " + before.status);

  // 2. Adjust the page: the untouched photo with four handles; the suggested candidate is chosen.
  await bar.click();
  await page.getByTestId("quad-editor").waitFor();
  await page.waitForSelector(".quad-photo", { timeout: 30000 });
  assert(await page.locator(".quad-handle").count() === 4, "four handles");
  const chosen = await page.locator('.quad-choices [aria-checked="true"]').textContent();
  console.log(`    pre-selected: ${chosen}`);
  await shot(page, "02-desktop-adjust");

  // The handles sit where the geometry says (source pixels → screen).
  const geomCheck = await page.evaluate(() => {
    const box = document.querySelector(".quad-box").getBoundingClientRect();
    return Array.from(document.querySelectorAll(".quad-handle")).map((h) => {
      const r = h.getBoundingClientRect();
      return { cx: r.left + r.width / 2 - box.left, cy: r.top + r.height / 2 - box.top, label: h.getAttribute("aria-label") };
    });
  });
  console.log("    handles:", geomCheck.map((h) => h.label).join(" | "));

  // 3. Drag the bottom-right corner with the mouse; the magnifier shows while dragging.
  const br = page.locator('.quad-handle[data-corner="br"]');
  const startLabel = await br.getAttribute("aria-label");
  const bb = await br.boundingBox();
  const cx = bb.x + bb.width / 2;
  const cy = bb.y + bb.height / 2;
  await page.mouse.move(cx, cy);
  await page.mouse.down();
  for (let i = 1; i <= 8; i++) await page.mouse.move(cx - 3 * i, cy - 2 * i);
  await page.getByTestId("magnifier").waitFor();
  await shot(page, "03-desktop-drag-magnifier");
  await page.mouse.up();
  const endLabel = await br.getAttribute("aria-label");
  const p0 = cornerFromLabel(startLabel);
  const p1 = cornerFromLabel(endLabel);
  assert(p1[0] < p0[0] && p1[1] < p0[1], `corner moved up-left: ${p0} → ${p1}`);
  assert((await page.locator('.quad-choices [aria-checked="true"]').textContent()).includes("你調整的"), "the choice becomes 'yours'");
  // Keyboard: one step left with the arrow key.
  await br.focus();
  await page.keyboard.press("ArrowLeft");
  const p2 = cornerFromLabel(await br.getAttribute("aria-label"));
  assert(p2[0] < p1[0] && p2[1] === p1[1], `arrow key moves left: ${p1} → ${p2}`);
  const sentQuad = await Promise.all(["tl", "tr", "br", "bl"].map(async (c) => cornerFromLabel(await page.locator(`.quad-handle[data-corner="${c}"]`).getAttribute("aria-label"))));

  // 4. Apply → the job re-runs from step ② → back to the view, the bar is gone.
  const t0 = Date.now();
  await page.getByTestId("adjust-apply").click();
  await page.waitForSelector('[data-testid="progress"]', { timeout: 15000 });
  await page.waitForSelector('[data-testid="progress"]', { state: "detached", timeout: CONVERT_TIMEOUT });
  await page.waitForSelector('.stage[data-phase="done"]');
  console.log(`    re-run in ${((Date.now() - t0) / 1000).toFixed(1)} s`);
  await waitStill(page);
  await page.waitForTimeout(800);
  assert(!(await page.getByTestId("review-bar").count()), "the yellow bar is gone after the corners were applied");
  const after = await api(`/api/jobs/${jobId}/pages/1/geometry`);
  assert(after.status === "user" && after.user_set === true && after.needs_review === false, "geometry now says user: " + JSON.stringify({ s: after.status, u: after.user_set, n: after.needs_review }));
  const err = Math.max(...after.corners.map((p, i) => Math.hypot(p[0] - sentQuad[i][0], p[1] - sentQuad[i][1])));
  console.log(`    corners on the server vs the handles: max ${err.toFixed(2)} px (handles show whole pixels)`);
  assert(err <= 1.0, "server corners = the dragged corners (±1 px)");
  await shot(page, "04-desktop-after-apply");

  // 5. Edit → select an element → "這裡錯了" → tags + one sentence → saved and counted.
  await page.getByRole("button", { name: "修正", exact: true }).click();
  await page.waitForSelector(".el-box", { timeout: 30000 });
  const target = page.locator(".el-box--text").first();
  await target.click();
  const dialog = page.getByRole("dialog");
  await dialog.waitFor();
  await dialog.getByTestId("report-toggle").click();
  await dialog.getByRole("button", { name: "文字錯了" }).click();
  await dialog.getByRole("button", { name: "字型不對" }).click();
  await dialog.getByPlaceholder("例如：第二行應該是粗體").fill("標題的字型應該是黑體");
  await dialog.getByTestId("report-send").click();
  await dialog.getByTestId("fb-saved").waitFor();
  const saved = await dialog.getByTestId("fb-saved").textContent();
  assert(/已記下/.test(saved) && /1 則回饋/.test(saved), "saved and counted: " + saved);
  await shot(page, "05-desktop-report-wrong");
  await page.keyboard.press("Escape");

  // 6. Rate the page (4 of 5 stars) in the result card.
  await page.locator('[data-testid="page-rating"] [data-star="4"]').click();
  await page.waitForFunction(() => /已記下/.test(document.querySelector('[data-testid="page-rating"] [data-testid="fb-saved"]')?.textContent ?? ""));
  const count = await page.locator('[data-testid="page-rating"] [data-testid="fb-saved"]').textContent();
  assert(/2 則回饋/.test(count), "rating counted: " + count);
  await shot(page, "06-desktop-rated");
  const listed = await api(`/api/jobs/${jobId}/feedback`);
  assert(listed.items.length === 2 && listed.items.some((x) => x.rating === 4 && x.page === 1), "server has the rating");
  assert(listed.items.some((x) => x.correct === false && x.tags.includes("text_wrong") && x.tags.includes("font_wrong") && x.element_id), "server has the report");
  assert(listed.corrections >= 1, "the corners correction is counted");

  // 7. File → export this job's feedback (zip download).
  await page.locator(".file-button").click();
  const [download] = await Promise.all([page.waitForEvent("download", { timeout: 60000 }), page.getByRole("menuitem", { name: "匯出這份工作的回饋" }).click()]);
  const zipPath = resolve(WORK, download.suggestedFilename());
  await download.saveAs(zipPath);
  const bytes = readFileSync(zipPath);
  assert(bytes.subarray(0, 2).toString() === "PK", "a zip file");
  assert(/\.mortise-feedback\.zip$/.test(download.suggestedFilename()), "file name: " + download.suggestedFilename());
  const listing = execFileSync("python3", ["-I", "-c", "import sys,zipfile,json;z=zipfile.ZipFile(sys.argv[1]);n=z.namelist();fb=[x for x in n if x.endswith('.feedback.json')];d=json.loads(z.read(fb[0]));print(json.dumps({'names':n,'keys':sorted(d)}))", zipPath]).toString();
  const zinfo = JSON.parse(listing);
  console.log(`    zip ${download.suggestedFilename()} ${statSync(zipPath).size} bytes: ${zinfo.names.join(", ")}`);
  assert(zinfo.names.includes("README.txt") && zinfo.names.includes("report.json") && zinfo.names.some((n) => n.startsWith("sample/")), "bundle contents");
  await page.getByRole("status").filter({ hasText: "已匯出回饋" }).first().waitFor({ timeout: 5000 }).catch(() => undefined);

  // 8. Account menu: engine version.
  await page.locator(".account-button").click();
  await page.getByTestId("engine-version").waitFor({ timeout: 60000 });
  const version = await page.getByTestId("engine-version").textContent();
  const engine = await api("/api/engine");
  assert(version === engine.version, "engine version shown");
  await page.getByTestId("engine-about").scrollIntoViewIfNeeded();
  await shot(page, "07-desktop-account-engine");
  await page.keyboard.press("Escape");

  assert(page.errors.length === 0, "no page errors: " + page.errors.join("; "));
  await page.context().close();
});

test("mobile: yellow bar and the corner editor fit a phone; touch-size handles; cancel", async () => {
  const page = await newPage({ width: 390, height: 844 });
  const { jobId } = await convertPhoto(page);
  const bar = page.getByTestId("review-bar");
  await bar.waitFor({ timeout: 15000 });
  await waitStill(page);
  await page.locator(".stage").scrollIntoViewIfNeeded();
  await shot(page, "08-mobile-review-bar");
  await bar.click();
  await page.waitForSelector(".quad-photo", { timeout: 30000 });
  // Everything is inside the frame and the handles are big enough for a finger.
  const frame = await page.locator(".stage").boundingBox();
  for (const sel of [".quad-choices", ".quad-actions", ".zoom-controls"]) {
    const b = await page.locator(sel).boundingBox();
    assert(b.x >= frame.x - 1 && b.x + b.width <= frame.x + frame.width + 1, `${sel} inside the frame horizontally`);
  }
  const hb = await page.locator(".quad-handle").first().boundingBox();
  assert(hb.width >= 40 && hb.height >= 40, "handle hit area ≥ 40 px: " + hb.width);
  // Drag a corner a little (mouse stands in for the finger) and show the magnifier.
  const tl = page.locator('.quad-handle[data-corner="tl"]');
  const b = await tl.boundingBox();
  await page.mouse.move(b.x + b.width / 2, b.y + b.height / 2);
  await page.mouse.down();
  for (let i = 1; i <= 6; i++) await page.mouse.move(b.x + b.width / 2 + 2 * i, b.y + b.height / 2 + 2 * i);
  await page.getByTestId("magnifier").waitFor();
  await shot(page, "09-mobile-adjust-drag");
  await page.mouse.up();
  await page.getByRole("button", { name: "取消" }).click();
  await page.getByTestId("review-bar").waitFor();
  const g = await api(`/api/jobs/${jobId}/pages/1/geometry`);
  assert(g.needs_review === true, "cancel sent nothing");
  // Clean up the extra job.
  await fetch(`${BASE}/api/jobs/${jobId}`, { method: "DELETE" });
  assert(page.errors.length === 0, "no page errors: " + page.errors.join("; "));
  await page.context().close();
});

/* ---------------------------------------------------------------- run */

browser = await chromium.launch();
let failed = 0;
for (const t of tests) {
  if (ONLY && !t.name.includes(ONLY)) continue;
  const t0 = Date.now();
  try {
    await t.fn();
    console.log(`ok   ${t.name} (${((Date.now() - t0) / 1000).toFixed(1)} s)`);
  } catch (e) {
    failed++;
    console.log(`FAIL ${t.name}\n     ${e.stack || e}`);
  }
}
await browser.close();
console.log(failed ? `${failed} failed` : "all passed");
process.exit(failed ? 1 : 0);
