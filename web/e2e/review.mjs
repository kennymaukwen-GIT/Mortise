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
// The generator prints the photo's size and the paper's true corners (source pixels; the cut-off ones lie below the photo).
const TRUTH = JSON.parse(execFileSync("python3", [resolve(here, "make_photo.py"), PHOTO, "cut", "7"], { cwd: repo }).toString().trim().split("\n").pop());
console.log(`photo ${TRUTH.size.join("×")} px, true corners ${JSON.stringify(TRUTH.corners)}`);

let browser;

async function newPage(viewport, lang = "zh-Hant", extra = {}) {
  const ctx = await browser.newContext({ viewport, deviceScaleFactor: 2, acceptDownloads: true, locale: lang === "en" ? "en-US" : "zh-TW", ...extra });
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

/** Where a source pixel is on the screen in the corner editor (the photo box carries its frame). */
async function sourceToScreen(page, p) {
  return page.evaluate(([x, y]) => {
    const box = document.querySelector(".quad-box");
    const [fx, fy, fw, fh] = box.dataset.frame.split(" ").map(Number);
    const r = box.getBoundingClientRect();
    return [r.left + ((x - fx) / fw) * r.width, r.top + ((y - fy) / fh) * r.height];
  }, p);
}

/**
 * Drag a corner handle to a source point, the way a person would: grab it, move there, let go. When the point is
 * further out than the editor shows, the person drags to the edge, lets go (the editor makes room) and goes on.
 * `input` is "mouse" or "touch" (real touch events through the browser's DevTools protocol).
 */
async function dragCornerTo(page, corner, target, input = "mouse") {
  const handle = page.locator(`.quad-handle[data-corner="${corner}"]`);
  const stage = await page.locator(".quad-editor .viewer__stage").boundingBox();
  const cdp = input === "touch" ? await page.context().newCDPSession(page) : null;
  let tries = 0;
  for (; tries < 5; tries++) {
    const bb = await handle.boundingBox();
    const from = [bb.x + bb.width / 2, bb.y + bb.height / 2];
    const want = await sourceToScreen(page, target);
    const to = [Math.min(stage.x + stage.width - 3, Math.max(stage.x + 3, want[0])), Math.min(stage.y + stage.height - 3, Math.max(stage.y + 3, want[1]))];
    const steps = 10;
    if (cdp) {
      await cdp.send("Input.dispatchTouchEvent", { type: "touchStart", touchPoints: [{ x: from[0], y: from[1] }] });
      for (let i = 1; i <= steps; i++) await cdp.send("Input.dispatchTouchEvent", { type: "touchMove", touchPoints: [{ x: from[0] + ((to[0] - from[0]) * i) / steps, y: from[1] + ((to[1] - from[1]) * i) / steps }] });
      await cdp.send("Input.dispatchTouchEvent", { type: "touchEnd", touchPoints: [] });
    } else {
      await page.mouse.move(from[0], from[1]);
      await page.mouse.down();
      for (let i = 1; i <= steps; i++) await page.mouse.move(from[0] + ((to[0] - from[0]) * i) / steps, from[1] + ((to[1] - from[1]) * i) / steps);
      await page.mouse.up();
    }
    await page.waitForTimeout(150);
    if (Math.abs(to[0] - want[0]) < 0.5 && Math.abs(to[1] - want[1]) < 0.5) break; // reached in one go
  }
  if (cdp) await cdp.detach();
  return { at: cornerFromLabel(await handle.getAttribute("aria-label")), drags: tries + 1 };
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

  // 3. The paper goes on below the photo: drag the two bottom corners out to where it really ends.
  //    The editor leaves room below a photo that cuts the paper off; nothing holds a corner inside the photo.
  const [W0, H0] = TRUTH.size;
  const frame0 = (await page.locator(".quad-box").getAttribute("data-frame")).split(" ").map(Number);
  console.log(`    frame on screen (source px): x ${frame0[0].toFixed(0)}…${(frame0[0] + frame0[2]).toFixed(0)}, y ${frame0[1].toFixed(0)}…${(frame0[1] + frame0[3]).toFixed(0)} (photo ${W0}×${H0})`);
  const br = page.locator('.quad-handle[data-corner="br"]');
  // The magnifier shows while dragging.
  {
    const bb = await br.boundingBox();
    const cx = bb.x + bb.width / 2;
    const cy = bb.y + bb.height / 2;
    await page.mouse.move(cx, cy);
    await page.mouse.down();
    for (let i = 1; i <= 8; i++) await page.mouse.move(cx - 3 * i, cy + 2 * i);
    await page.getByTestId("magnifier").waitFor();
    await shot(page, "03-desktop-drag-magnifier");
    await page.mouse.up();
  }
  const truthBR = TRUTH.corners[2];
  const truthBL = TRUTH.corners[3];
  const dBR = await dragCornerTo(page, "br", truthBR);
  const dBL = await dragCornerTo(page, "bl", truthBL);
  console.log(`    bottom-right → ${dBR.at} (true ${truthBR}, ${dBR.drags} drag), bottom-left → ${dBL.at} (true ${truthBL}, ${dBL.drags} drag)`);
  for (const [d, t] of [
    [dBR, truthBR],
    [dBL, truthBL],
  ])
    assert(Math.hypot(d.at[0] - t[0], d.at[1] - t[1]) <= 2.5, `corner reaches the true corner outside the photo: ${d.at} vs ${t}`);
  assert(dBR.at[1] > H0 && dBL.at[1] > H0, "both bottom corners are below the photo");
  assert((await page.locator('.quad-choices [aria-checked="true"]').textContent()).includes("你調整的"), "the choice becomes 'yours'");
  await shot(page, "03b-desktop-dragged-outside");
  // Keyboard: one step left with the arrow key, and back.
  await br.focus();
  const p1 = cornerFromLabel(await br.getAttribute("aria-label"));
  await page.keyboard.press("ArrowLeft");
  const p2 = cornerFromLabel(await br.getAttribute("aria-label"));
  assert(p2[0] < p1[0] && p2[1] === p1[1], `arrow key moves left: ${p1} → ${p2}`);
  await page.keyboard.press("ArrowRight");
  const sentQuad = await Promise.all(["tl", "tr", "br", "bl"].map(async (c) => cornerFromLabel(await page.locator(`.quad-handle[data-corner="${c}"]`).getAttribute("aria-label"))));
  const pagesBefore = await api(`/api/jobs/${jobId}/pages`);
  const ratioBefore = pagesBefore[0].height_pt / pagesBefore[0].width_pt;

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
  const errTrue = Math.max(...after.corners.map((p, i) => Math.hypot(p[0] - TRUTH.corners[i][0], p[1] - TRUTH.corners[i][1])));
  const pagesAfter = await api(`/api/jobs/${jobId}/pages`);
  const ratioAfter = pagesAfter[0].height_pt / pagesAfter[0].width_pt;
  console.log(`    server corners vs the paper's true corners: max ${errTrue.toFixed(1)} px; page height/width ${ratioBefore.toFixed(3)} → ${ratioAfter.toFixed(3)} (A4 is 1.414)`);
  await shot(page, "04-desktop-after-apply");

  // Leaving the editor with Escape gives the focus back to the page menu button (the yellow bar is gone now).
  await page.getByTestId("page-menu-button").click();
  await page.getByRole("menuitem", { name: /調整紙張範圍/ }).click();
  await page.waitForSelector(".quad-photo", { timeout: 30000 });
  // Tab goes through the choice, the four corners in order, then Cancel and Apply.
  const tabOrder = [];
  for (let i = 0; i < 14; i++) {
    await page.keyboard.press("Tab");
    tabOrder.push(await page.evaluate(() => document.activeElement?.dataset.corner ?? document.activeElement?.textContent?.trim() ?? ""));
  }
  const cornersInTab = tabOrder.filter((x) => ["tl", "tr", "br", "bl"].includes(x));
  assert(JSON.stringify(cornersInTab) === JSON.stringify(["tl", "tr", "br", "bl"]), "Tab reaches the four corners in order: " + tabOrder.join(" → "));
  assert(tabOrder.indexOf("bl") < tabOrder.indexOf("取消"), "corners before Cancel: " + tabOrder.join(" → "));
  console.log(`    Tab order: ${tabOrder.slice(0, tabOrder.indexOf("取消") + 2).join(" → ")}`);
  await page.locator('.quad-handle[data-corner="tr"]').focus(); // (the Tabs above went on past the editor)
  await page.keyboard.press("Escape");
  await page.waitForFunction(() => document.activeElement?.getAttribute("data-testid") === "page-menu-button", null, { timeout: 3000 });

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

  // 6. Rate the page in the result card: a click sends at once; a quick run of arrow keys sends one rating.
  const ratingPosts = [];
  page.on("request", (r) => {
    if (r.method() === "POST" && r.url().endsWith(`/api/jobs/${jobId}/feedback`) && /"rating"/.test(r.postData() ?? "")) ratingPosts.push(JSON.parse(r.postData()).rating);
  });
  await page.locator('[data-testid="page-rating"] [data-star="3"]').click();
  await page.waitForFunction(() => /已記下/.test(document.querySelector('[data-testid="page-rating"] [data-testid="fb-saved"]')?.textContent ?? ""));
  const count = await page.locator('[data-testid="page-rating"] [data-testid="fb-saved"]').textContent();
  assert(/2 則回饋/.test(count), "rating counted: " + count);
  await page.locator('[data-testid="page-rating"] [data-star="3"]').focus();
  await page.keyboard.press("ArrowRight");
  await page.keyboard.press("ArrowRight");
  await page.keyboard.press("ArrowLeft");
  await page.keyboard.press("ArrowRight");
  await page.waitForTimeout(600);
  assert(ratingPosts.length === 1, "arrow keys send nothing while pressed: " + ratingPosts.join(","));
  await page.waitForTimeout(1800);
  console.log(`    ratings sent: ${ratingPosts.join(", ")} (one click, then → → ← → )`);
  assert(JSON.stringify(ratingPosts) === "[3,5]", "one click + one key run = two ratings: " + ratingPosts.join(","));
  await page.waitForFunction(() => /3 則回饋/.test(document.querySelector('[data-testid="page-rating"] [data-testid="fb-saved"]')?.textContent ?? ""));
  await shot(page, "06-desktop-rated");
  const listed = await api(`/api/jobs/${jobId}/feedback`);
  const ratings = listed.items.filter((x) => x.rating != null).map((x) => x.rating);
  assert(listed.items.length === 3 && JSON.stringify(ratings) === "[3,5]", "server has the two ratings: " + JSON.stringify(ratings));
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

test("mobile: yellow bar and the corner editor fit a phone; touch-size handles; touch drag past the photo; cancel", async () => {
  const page = await newPage({ width: 390, height: 844 }, "zh-Hant", { hasTouch: true });
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
  // Every choice is on screen (the chips wrap instead of scrolling sideways).
  const vw = 390;
  const chips = await page.locator(".quad-chip").evaluateAll((els) => els.map((e) => { const r = e.getBoundingClientRect(); return { l: r.left, r: r.right, t: e.textContent }; }));
  for (const c of chips) assert(c.l >= 0 && c.r <= vw, `chip on screen: ${c.t} ${c.l.toFixed(0)}–${c.r.toFixed(0)}`);
  console.log(`    chips: ${chips.map((c) => c.t).join(" | ")}`);
  // A real touch drag (touch events through the browser) takes the bottom-left corner below the photo, where the paper ends.
  const tl = page.locator('.quad-handle[data-corner="tl"]');
  const b = await tl.boundingBox();
  const cdp = await page.context().newCDPSession(page);
  await cdp.send("Input.dispatchTouchEvent", { type: "touchStart", touchPoints: [{ x: b.x + b.width / 2, y: b.y + b.height / 2 }] });
  for (let i = 1; i <= 6; i++) await cdp.send("Input.dispatchTouchEvent", { type: "touchMove", touchPoints: [{ x: b.x + b.width / 2 + 2 * i, y: b.y + b.height / 2 + 2 * i }] });
  await page.getByTestId("magnifier").waitFor();
  await shot(page, "09-mobile-adjust-drag");
  await cdp.send("Input.dispatchTouchEvent", { type: "touchEnd", touchPoints: [] });
  await cdp.detach();
  const dBL = await dragCornerTo(page, "bl", TRUTH.corners[3], "touch");
  console.log(`    touch: bottom-left → ${dBL.at} (true ${TRUTH.corners[3]}, ${dBL.drags} drag)`);
  assert(Math.hypot(dBL.at[0] - TRUTH.corners[3][0], dBL.at[1] - TRUTH.corners[3][1]) <= 4, `touch drag reaches the true corner: ${dBL.at}`);
  assert(dBL.at[1] > TRUTH.size[1], "the corner is below the photo");
  await shot(page, "09b-mobile-dragged-outside");
  await page.getByRole("button", { name: "取消" }).click();
  await page.getByTestId("review-bar").waitFor();
  const g = await api(`/api/jobs/${jobId}/pages/1/geometry`);
  assert(g.needs_review === true, "cancel sent nothing");
  await page.waitForFunction(() => document.activeElement?.getAttribute("data-testid") === "review-bar", null, { timeout: 3000 });
  // The page menu stays on the screen.
  await page.getByTestId("page-menu-button").click();
  const menu = await page.locator("#page-menu").boundingBox();
  console.log(`    page menu: x ${menu.x.toFixed(0)}–${(menu.x + menu.width).toFixed(0)} on a ${vw} px screen`);
  assert(menu.x >= 0 && menu.x + menu.width <= vw, "page menu on screen");
  await shot(page, "10-mobile-page-menu");
  await page.keyboard.press("Escape");
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
