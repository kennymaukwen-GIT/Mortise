import { act, cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type { PageGeometry, Quad } from "../api/types";
import { I18nProvider } from "../i18n/I18n";
import { QuadEditor } from "./QuadEditor";

const W = 1000;
const H = 1100;
const FOUND: Quad = [
  [180, 160],
  [820, 140],
  [860, 1300],
  [150, 1320],
];
const ALT: Quad = [
  [180, 160],
  [820, 140],
  [840, 1100],
  [165, 1100],
];

function geometry(over: Partial<PageGeometry> = {}): PageGeometry {
  return {
    page: 1,
    input_type: "photo",
    source: { width: W, height: H, image: "/api/jobs/j1/pages/1/image?kind=source" },
    corners: FOUND,
    method: "edge_fit",
    confidence: 0.55,
    status: "cut_off",
    corner_error_px: null,
    needs_review: true,
    review_reason: "紙的下緣好像超出畫面。",
    alternatives: [{ corners: ALT, score: 0.8, label: "三條邊＋畫面邊" }],
    preselect: 0,
    outline: [
      [180, 160],
      [500, 150],
      [820, 140],
      [840, 1100],
      [165, 1100],
    ],
    dewarp: { applied: false, source: null, residual_px: null, needs_review: false },
    glare: null,
    user_set: false,
    notes: [],
    ...over,
  };
}

// jsdom has no layout: give elements a size, and the photo box a known place on the screen.
const BOX = { left: 100, top: 50, width: 0, height: 0 };
beforeAll(() => {
  vi.stubGlobal(
    "ResizeObserver",
    class {
      observe() {}
      disconnect() {}
    },
  );
  Object.defineProperty(HTMLElement.prototype, "clientWidth", { configurable: true, get: () => 900 });
  Object.defineProperty(HTMLElement.prototype, "clientHeight", { configurable: true, get: () => 800 });
  vi.spyOn(HTMLElement.prototype, "getBoundingClientRect").mockImplementation(function (this: HTMLElement) {
    if (this.classList.contains("quad-box")) return { ...BOX, right: BOX.left + BOX.width, bottom: BOX.top + BOX.height, x: BOX.left, y: BOX.top, toJSON: () => ({}) } as DOMRect;
    return { left: 0, top: 0, width: 900, height: 800, right: 900, bottom: 800, x: 0, y: 0, toJSON: () => ({}) } as DOMRect;
  });
  // The photo itself (GET …/image?kind=source).
  vi.stubGlobal("fetch", vi.fn(async () => new Response(new Uint8Array([137, 80, 78, 71]), { status: 200, headers: { "Content-Type": "image/png" } })));
  URL.createObjectURL = vi.fn(() => "blob:photo");
  URL.revokeObjectURL = vi.fn();
});
afterAll(() => vi.unstubAllGlobals());

function setup(g = geometry(), lang: "en" | "zh-Hant" = "en") {
  const onApply = vi.fn(async (_q: Quad) => undefined);
  const onCancel = vi.fn();
  const utils = render(
    <I18nProvider lang={lang}>
      <QuadEditor jobId="j1" geometry={g} onApply={onApply} onCancel={onCancel} />
    </I18nProvider>,
  );
  return { ...utils, onApply, onCancel };
}

/**
 * The frame shown (see frameFor and marginsFor): the photo with 5% of its longer side around it, except a side that a
 * candidate touches or crosses (the paper is cut off there), which gets 30% of the photo's size across it; corners
 * further out get 5% around them.
 */
function frame(g = geometry(), extra: [number, number][] = []) {
  const n = 0.05 * Math.max(W, H);
  const pts = [...g.corners, ...g.alternatives.flatMap((a) => a.corners), ...(g.outline ?? [])];
  const m = {
    left: pts.some((p) => p[0] <= 0.015 * W) ? 0.3 * W : n,
    right: pts.some((p) => p[0] >= W - 0.015 * W) ? 0.3 * W : n,
    top: pts.some((p) => p[1] <= 0.015 * H) ? 0.3 * H : n,
    bottom: pts.some((p) => p[1] >= H - 0.015 * H) ? 0.3 * H : n,
  };
  const all = [...pts, ...extra];
  const x0 = Math.min(-m.left, ...all.map((p) => p[0] - n));
  const y0 = Math.min(-m.top, ...all.map((p) => p[1] - n));
  const x1 = Math.max(W + m.right, ...all.map((p) => p[0] + n));
  const y1 = Math.max(H + m.bottom, ...all.map((p) => p[1] + n));
  return { x: x0, y: y0, w: x1 - x0, h: y1 - y0 };
}

describe("QuadEditor", () => {
  it("starts on the server's suggested candidate and shows every choice", () => {
    setup();
    const group = screen.getByRole("radiogroup", { name: "Paper outline" });
    const radios = within(group).getAllByRole("radio");
    expect(radios.map((r) => r.textContent)).toEqual(["Found by Mortise", "Option 1suggested", "Whole image as the page"]);
    expect(radios[1]).toHaveAttribute("aria-checked", "true");
    // The hint explains why in English (the server's sentence is Chinese).
    expect(screen.getByText(/Part of the paper seems to be outside the photo/)).toBeInTheDocument();
    expect(screen.getByTestId("outline")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Top-left corner, at x 180, y 160/ })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Bottom-right corner, at x 840, y 1100/ })).toBeInTheDocument();
  });

  it("shows the server's reason and candidate label in Chinese", () => {
    setup(geometry(), "zh-Hant");
    expect(screen.getByText(/紙的下緣好像超出畫面/)).toBeInTheDocument();
    expect(screen.getByRole("radio", { name: /三條邊＋畫面邊/ })).toBeInTheDocument();
  });

  it("drags a corner: screen pixels become source pixels, then Apply sends them", async () => {
    const g = geometry();
    const f = frame(g);
    BOX.width = 550;
    BOX.height = (550 * f.h) / f.w;
    const k = BOX.width / f.w; // screen px per source px
    const { onApply } = setup(g);
    await screen.findByAltText("The photo as it was uploaded");
    const handle = screen.getByRole("button", { name: /Bottom-right corner/ });
    const at = (p: [number, number]) => ({ clientX: BOX.left + (p[0] - f.x) * k, clientY: BOX.top + (p[1] - f.y) * k });
    // Grab 3 px right of the handle's centre (the handle must not jump), move by (+40, -20) screen px.
    const start = at([840, 1100]);
    fireEvent.pointerDown(handle, { pointerId: 1, button: 0, pointerType: "mouse", clientX: start.clientX + 3, clientY: start.clientY });
    expect(screen.getByTestId("magnifier")).toBeInTheDocument();
    fireEvent.pointerMove(handle, { pointerId: 1, clientX: start.clientX + 3 + 40, clientY: start.clientY - 20 });
    fireEvent.pointerUp(handle, { pointerId: 1 });
    expect(screen.queryByTestId("magnifier")).not.toBeInTheDocument();
    expect(screen.getByRole("radio", { name: "Your adjustment" })).toHaveAttribute("aria-checked", "true");
    await userEvent.click(screen.getByTestId("adjust-apply"));
    expect(onApply).toHaveBeenCalledTimes(1);
    const sent = onApply.mock.calls[0][0];
    expect(sent[0]).toEqual([180, 160]);
    expect(sent[2][0]).toBeCloseTo(840 + 40 / k, 6);
    expect(sent[2][1]).toBeCloseTo(1100 - 20 / k, 6);
  });

  it("the paper cut off at the bottom: the frame leaves room below the photo, and a corner dragged out there is sent as it is", async () => {
    const g = geometry();
    const f = frame(g);
    // The bottom side is cut off (a candidate's corners sit on it): 30% of the photo's height below it.
    expect(f.y + f.h).toBeCloseTo(H + 0.3 * H, 6);
    expect(f.x).toBeCloseTo(-55, 6);
    BOX.width = 550;
    BOX.height = (550 * f.h) / f.w;
    const k = BOX.width / f.w;
    const { onApply } = setup(g);
    const at = (p: [number, number]) => ({ clientX: BOX.left + (p[0] - f.x) * k, clientY: BOX.top + (p[1] - f.y) * k });
    // The suggested candidate puts the bottom-left corner on the photo's edge (165, 1100); the paper really ends
    // 15% further down (y = 1265) — and then a little more, past the frame drawn on screen (y = 1480).
    const handle = screen.getByRole("button", { name: /Bottom-left corner/ });
    const from = at([165, 1100]);
    const to = at([150, 1265]);
    fireEvent.pointerDown(handle, { pointerId: 3, button: 0, pointerType: "touch", ...from });
    fireEvent.pointerMove(handle, { pointerId: 3, ...to });
    expect(handle).toHaveAccessibleName(/x 150, y 1265/);
    const past = at([150, 1480]);
    fireEvent.pointerMove(handle, { pointerId: 3, ...past });
    expect(handle).toHaveAccessibleName(/x 150, y 1480/); // not stopped at the frame's edge (1430)
    fireEvent.pointerUp(handle, { pointerId: 3 });
    // When the drag ends, the frame grows to show the corner with room around it.
    const photo = await screen.findByAltText("The photo as it was uploaded");
    const grown = frame(g, [[150, 1480]]);
    expect(grown.y + grown.h).toBeCloseTo(1480 + 55, 6);
    expect(parseFloat(photo.style.height)).toBeCloseTo((H / grown.h) * 100, 3); // the style keeps 4 decimals
    expect(parseFloat(handle.style.top)).toBeCloseTo(((1480 - grown.y) / grown.h) * 100, 3);
    await userEvent.click(screen.getByTestId("adjust-apply"));
    expect(onApply.mock.calls[0][0][3]).toEqual([150, 1480]);
  });

  it("a corner can go as far out as the server accepts, no further", () => {
    const g = geometry({ preselect: null });
    const f = frame(g);
    BOX.width = f.w;
    BOX.height = f.h; // 1 screen px = 1 source px
    setup(g);
    const handle = screen.getByRole("button", { name: /Top-right corner/ });
    const p = { clientX: BOX.left + (820 - f.x), clientY: BOX.top + (140 - f.y) };
    fireEvent.pointerDown(handle, { pointerId: 4, button: 0, pointerType: "mouse", ...p });
    fireEvent.pointerMove(handle, { pointerId: 4, clientX: p.clientX + 1e6, clientY: p.clientY });
    fireEvent.pointerUp(handle, { pointerId: 4 });
    const limit = 0.99 * 4 * Math.hypot(W, H);
    expect(handle).toHaveAccessibleName(new RegExp(`x ${Math.round(limit)}, y 140`));
  });

  it("moves a corner with the arrow keys (one screen pixel, ten with Shift)", async () => {
    const g = geometry({ preselect: null });
    const f = frame(g);
    BOX.width = 500;
    BOX.height = (500 * f.h) / f.w;
    const step = f.w / BOX.width;
    const { onApply } = setup(g);
    const handle = screen.getByRole("button", { name: /Top-left corner/ });
    act(() => handle.focus());
    fireEvent.keyDown(handle, { key: "ArrowRight" });
    fireEvent.keyDown(handle, { key: "ArrowDown", shiftKey: true });
    await userEvent.click(screen.getByTestId("adjust-apply"));
    const sent = onApply.mock.calls[0][0];
    expect(sent[0][0]).toBeCloseTo(180 + step, 6);
    expect(sent[0][1]).toBeCloseTo(160 + 10 * step, 6);
    expect(sent[1]).toEqual(FOUND[1]);
  });

  it("'whole image as the page' sends the photo's corners; confirming unchanged corners is allowed", async () => {
    const { onApply } = setup(geometry({ preselect: null, needs_review: false, review_reason: "", status: "ok" }));
    expect(screen.getByTestId("adjust-apply")).toHaveTextContent("Corners are right");
    await userEvent.click(screen.getByRole("radio", { name: "Whole image as the page" }));
    expect(screen.getByTestId("adjust-apply")).toHaveTextContent("Apply");
    await userEvent.click(screen.getByTestId("adjust-apply"));
    expect(onApply).toHaveBeenCalledWith([
      [0, 0],
      [W, 0],
      [W, H],
      [0, H],
    ]);
  });

  it("refuses crossing sides and shows the error from a failed send", async () => {
    const g = geometry({ preselect: null });
    const { onApply } = setup(g);
    // Pull the top-left corner far to the right of the top-right one is still fixable by re-ordering;
    // collapsing it onto the top-right corner is not.
    const f = frame(g);
    BOX.width = f.w;
    BOX.height = f.h; // 1 screen px = 1 source px
    const handle = screen.getByRole("button", { name: /Top-left corner/ });
    const p = { clientX: BOX.left + (180 - f.x), clientY: BOX.top + (160 - f.y) };
    fireEvent.pointerDown(handle, { pointerId: 2, button: 0, pointerType: "touch", ...p });
    fireEvent.pointerMove(handle, { pointerId: 2, clientX: p.clientX + 640, clientY: p.clientY - 20 });
    fireEvent.pointerUp(handle, { pointerId: 2 });
    expect(screen.getByRole("button", { name: /Top-left corner/ })).toHaveAccessibleName(/x 820, y 140/);
    await userEvent.click(screen.getByTestId("adjust-apply"));
    expect(onApply).not.toHaveBeenCalled();
    expect(screen.getByRole("alert")).toHaveTextContent("without the sides crossing");

    onApply.mockRejectedValueOnce(new Error("HTTP 500"));
    await userEvent.click(screen.getByRole("radio", { name: "Found by Mortise" }));
    await userEvent.click(screen.getByTestId("adjust-apply"));
    expect(await screen.findByRole("alert")).toHaveTextContent("The corners could not be sent: HTTP 500");
  });

  it("Tab goes through the choices, then the four corners in order, then Cancel and Apply", async () => {
    setup(geometry({ preselect: null }));
    // The editor starts on the chosen outline.
    expect(screen.getByRole("radio", { name: "Found by Mortise" })).toHaveFocus();
    const order: string[] = [];
    for (let i = 0; i < 12; i++) {
      await userEvent.tab();
      const el = document.activeElement as HTMLElement;
      order.push(el.dataset.corner ?? el.getAttribute("aria-label") ?? el.textContent ?? "");
    }
    const corners = order.filter((x) => ["tl", "tr", "br", "bl"].includes(x));
    expect(corners).toEqual(["tl", "tr", "br", "bl"]);
    expect(order.indexOf("tl")).toBeLessThan(order.indexOf("Cancel"));
    expect(order.indexOf("Cancel")).toBeLessThan(order.indexOf("Corners are right"));
  });

  it("numbers candidates that share a label; the English reason ends with a full stop before the hint", () => {
    const alt = (label: string) => ({ corners: ALT, score: 0.5, label });
    setup(geometry({ alternatives: [alt("另一種框法"), alt("另一種框法"), alt("三條邊＋畫面邊")], preselect: null }), "zh-Hant");
    const names = within(screen.getByRole("radiogroup")).getAllByRole("radio").map((r) => r.textContent);
    expect(names).toEqual(["Mortise 找到的", "另一種框法 1", "另一種框法 2", "三條邊＋畫面邊", "整張圖當頁面"]);
  });

  it("the English reason and the hint are separate sentences", () => {
    setup(geometry({ status: "curved_edge", review_reason: "紙邊是彎的。" }));
    const hint = screen.getByText(/Drag the four round handles/).closest("p")!;
    expect(hint.textContent).toMatch(/so its corners are uncertain\. Drag the four round handles/);
  });

  it("Cancel and Escape leave without sending", async () => {
    const { onApply, onCancel } = setup();
    await userEvent.click(screen.getByRole("button", { name: "Cancel" }));
    fireEvent.keyDown(window, { key: "Escape" });
    expect(onCancel).toHaveBeenCalledTimes(2);
    expect(onApply).not.toHaveBeenCalled();
  });
});

describe("the suggested choice is the corners found", () => {
  it("shows one chip, not two that frame the same corners", () => {
    setup(geometry({ corners: ALT, alternatives: [{ corners: ALT, score: 0.8, label: "三條邊＋畫面邊" }], preselect: 0 }));
    expect(screen.queryByTestId("choice-current")).not.toBeInTheDocument();
    expect(screen.getByTestId("choice-alt-0")).toBeInTheDocument();
  });
  it("keeps 'found by Mortise' when the suggestion differs, or after the user set the corners", () => {
    setup(geometry());
    expect(screen.getByTestId("choice-current")).toBeInTheDocument();
    cleanup();
    setup(geometry({ corners: ALT, alternatives: [{ corners: ALT, score: 0.8, label: "Mortise 原本找到的" }], preselect: null, user_set: true, status: "user" }));
    expect(screen.getByTestId("choice-current")).toBeInTheDocument();
  });
});
