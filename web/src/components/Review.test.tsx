// The yellow "please check the paper" bar, the page menu, "something is wrong here", and page ratings, wired together.
import { act, fireEvent, render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type { CorrectionIn, FeedbackIn, PageElements, PageGeometry, PageInfo } from "../api/types";
import { I18nProvider } from "../i18n/I18n";
import { initialState, type State } from "../state/store";
import type { FeedbackState } from "../state/useFeedback";
import { SettingsCard } from "./SettingsCard";
import { canAdjustPage, pagesToReview, Stage } from "./Stage";

const W = 1000;
const H = 1100;
const GEO: PageGeometry = {
  page: 1,
  input_type: "photo",
  source: { width: W, height: H, image: "/api/jobs/j1/pages/1/image?kind=source" },
  corners: [
    [180, 160],
    [820, 140],
    [860, 1090],
    [150, 1080],
  ],
  method: "edge_fit",
  confidence: 0.5,
  status: "cut_off",
  corner_error_px: [1, 1, 8, 8],
  needs_review: true,
  review_reason: "紙的下緣好像超出畫面。",
  alternatives: [],
  preselect: null,
  outline: null,
  dewarp: { applied: false, source: null, residual_px: null, needs_review: false },
  glare: null,
  user_set: false,
  notes: [],
};
const ELEMENTS: PageElements = {
  page: 1,
  width_pt: 595,
  height_pt: 842,
  background_color: null,
  text_boxes: [{ id: "p1-t-1", box: { x: 50, y: 50, w: 300, h: 40 }, z: 1, confidence: 0.9, fields: {}, issues: [], paragraphs: [{ id: "p1", text: "Hello", align: "left", role: "body", runs: [] }] }],
  tables: [{ id: "p1-tbl-1", box: { x: 50, y: 200, w: 400, h: 200 }, z: 2, confidence: 0.6, fields: {}, issues: [], rows: 2, cols: 2, header_rows: 1, cells: [] }],
  images: [],
  shapes: [],
  styles: [],
};

function page(over: Partial<PageInfo> = {}): PageInfo {
  return { page: 1, input_type: "photo", width_pt: 595, height_pt: 842, images: { original: "", result: "", overlay: "" }, elements: "", needs_review: true, quad_status: "cut_off", ...over };
}

function doneState(pages: PageInfo[], over: Partial<State> = {}): State {
  return { ...initialState, phase: "done", jobId: "j1", jobFilename: "poster.jpg", jobFormat: "pdf", pages, score: 0.8, view: "result", resultVersion: 1, ...over };
}

function fakeFeedback(over: Partial<FeedbackState> = {}): FeedbackState & { sent: FeedbackIn[] } {
  const sent: FeedbackIn[] = [];
  const fb = {
    items: [] as FeedbackState["items"],
    consented: false,
    corrections: 0,
    ratings: {},
    reported: new Set<string>(),
    sent,
    send: vi.fn(async (x: FeedbackIn) => {
      sent.push(x);
      fb.items = [...fb.items, { ...x, id: String(sent.length) }];
    }),
    ...over,
  };
  return fb;
}

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
  URL.createObjectURL = vi.fn(() => "blob:x");
  URL.revokeObjectURL = vi.fn();
});
beforeEach(() => {
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input);
      const json = (b: unknown) => new Response(JSON.stringify(b), { status: 200, headers: { "Content-Type": "application/json" } });
      if (url.endsWith("/geometry")) return json(GEO);
      if (url.endsWith("/elements")) return json(ELEMENTS);
      return new Response(new Uint8Array([137, 80, 78, 71]), { status: 200, headers: { "Content-Type": "image/png" } });
    }),
  );
});
afterAll(() => vi.unstubAllGlobals());

function renderStage(state: State, opts: { onCorrect?: (i: CorrectionIn[]) => Promise<void>; feedback?: FeedbackState; lang?: "en" | "zh-Hant" } = {}) {
  const dispatch = vi.fn();
  const onCorrect = opts.onCorrect ?? vi.fn(async () => undefined);
  const utils = render(
    <I18nProvider lang={opts.lang ?? "en"}>
      <Stage
        state={state}
        dispatch={dispatch}
        maxMb={100}
        serverHasLibreOffice={false}
        onPickFile={() => {}}
        onDropFile={() => {}}
        onCancel={() => {}}
        onRetry={() => {}}
        onCorrect={onCorrect}
        onJobMissing={() => {}}
        feedback={opts.feedback}
      />
    </I18nProvider>,
  );
  return { ...utils, dispatch, onCorrect };
}

describe("helpers", () => {
  it("lists pages to review and pages whose corners can move", () => {
    expect(pagesToReview([page(), page({ page: 2, needs_review: false })])).toEqual([1]);
    expect(pagesToReview(null)).toEqual([]);
    expect(canAdjustPage(page({ needs_review: false, quad_status: "ok" }), "a.pdf")).toBe(true);
    expect(canAdjustPage(page({ input_type: "screenshot", quad_status: null }), "shot.PNG")).toBe(true);
    expect(canAdjustPage(page({ input_type: "vector_pdf", quad_status: null }), "deck.pdf")).toBe(false);
    expect(canAdjustPage(undefined, "a.jpg")).toBe(false);
  });
});

describe("yellow review bar and the corner editor", () => {
  it("explains why (server sentence in Chinese) and opens the editor; Apply sends the corners", async () => {
    const onCorrect = vi.fn(async (_items: CorrectionIn[]) => undefined);
    renderStage(doneState([page()]), { onCorrect, lang: "zh-Hant" });
    const bar = await screen.findByTestId("review-bar");
    await vi.waitFor(() => expect(bar).toHaveTextContent("紙的下緣好像超出畫面。請拖曳四角確認紙張範圍。"));
    expect(bar).toHaveTextContent("調整頁面");
    await userEvent.click(bar);
    expect(await screen.findByTestId("quad-editor")).toBeInTheDocument();
    expect(screen.queryByTestId("review-bar")).not.toBeInTheDocument();
    await userEvent.click(screen.getByTestId("adjust-apply"));
    expect(onCorrect).toHaveBeenCalledWith([{ field: "corners", page: 1, value: GEO.corners }]);
    await vi.waitFor(() => expect(screen.queryByTestId("quad-editor")).not.toBeInTheDocument());
  });

  it("has an English sentence for the English interface", async () => {
    renderStage(doneState([page()]));
    const bar = await screen.findByTestId("review-bar");
    expect(bar).toHaveTextContent("Part of the paper seems to be outside the photo. Drag the four corners to confirm where the paper is.");
  });

  it("is not shown when no page needs checking; the page menu still opens the editor for a photo", async () => {
    renderStage(doneState([page({ needs_review: false, quad_status: "ok" })]));
    expect(screen.queryByTestId("review-bar")).not.toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Page menu" }));
    const menu = screen.getByRole("menu", { name: "Page menu" });
    await userEvent.click(within(menu).getByRole("menuitem", { name: /Adjust the paper's corners/ }));
    expect(await screen.findByTestId("quad-editor")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Cancel" }));
    expect(screen.queryByTestId("quad-editor")).not.toBeInTheDocument();
  });

  it("points to another page that needs checking", async () => {
    const { dispatch } = renderStage(doneState([page({ needs_review: false, quad_status: "ok" }), page({ page: 2 })]));
    const bar = await screen.findByTestId("review-bar");
    expect(bar).toHaveTextContent("Page 2: please check where the paper is in the photo.");
    await userEvent.click(bar);
    expect(dispatch).toHaveBeenCalledWith({ type: "SET_PAGE", index: 1 });
  });

  it("PDF pages cannot be adjusted", async () => {
    renderStage(doneState([page({ input_type: "vector_pdf", needs_review: false, quad_status: null })], { jobFilename: "deck.pdf" }));
    await userEvent.click(screen.getByRole("button", { name: "Page menu" }));
    expect(screen.getByRole("menuitem", { name: /Adjust the paper's corners/ })).toHaveAttribute("aria-disabled", "true");
  });
});

describe("something is wrong here", () => {
  it("sends tags and a sentence for the selected element, then says it was saved", async () => {
    const fb = fakeFeedback();
    const state = doneState([page({ needs_review: false })], { editMode: true });
    const { rerender } = renderStage(state, { feedback: fb });
    const box = await screen.findByRole("button", { name: /^table: 2×2/ });
    await userEvent.click(box);
    const dialog = screen.getByRole("dialog");
    // Tables cannot be edited here, so the report is open from the start.
    const form = within(dialog).getByTestId("report-form");
    await userEvent.click(within(form).getByTestId("report-send"));
    expect(within(form).getByRole("alert")).toHaveTextContent("Choose what is wrong, or write a sentence.");
    await userEvent.click(within(form).getByRole("button", { name: "Table is wrong" }));
    await userEvent.click(within(form).getByRole("button", { name: "Wrong text" }));
    await userEvent.type(within(form).getByRole("textbox"), "Row 2 is merged");
    await userEvent.click(within(form).getByTestId("report-send"));
    expect(fb.sent).toEqual([{ page: 1, element_id: "p1-tbl-1", correct: false, tags: ["table_wrong", "text_wrong"], comment: "Row 2 is merged" }]);
    rerender(
      <I18nProvider lang="en">
        <Stage state={state} dispatch={vi.fn()} maxMb={100} serverHasLibreOffice={false} onPickFile={() => {}} onDropFile={() => {}} onCancel={() => {}} onRetry={() => {}} onCorrect={vi.fn()} onJobMissing={() => {}} feedback={{ ...fb, items: [...fb.items] }} />
      </I18nProvider>,
    );
    expect(within(screen.getByRole("dialog")).getByTestId("fb-saved")).toHaveTextContent("Saved · 1 note saved for this job");
    expect(within(screen.getByRole("dialog")).getByText(/converted with “Help improve recognition” off/)).toBeInTheDocument();
  });

  it("is folded away under text editing and opens on demand", async () => {
    const fb = fakeFeedback();
    renderStage(doneState([page({ needs_review: false })], { editMode: true }), { feedback: fb });
    await userEvent.click(await screen.findByRole("button", { name: /^Text box: Hello/ }));
    const toggle = screen.getByTestId("report-toggle");
    expect(toggle).toHaveAttribute("aria-expanded", "false");
    expect(screen.queryByTestId("report-form")).not.toBeInTheDocument();
    await userEvent.click(toggle);
    await userEvent.click(screen.getByRole("button", { name: "Wrong font" }));
    await userEvent.click(screen.getByTestId("report-send"));
    expect(fb.sent).toEqual([{ page: 1, element_id: "p1-t-1", correct: false, tags: ["font_wrong"] }]);
  });

  it("a problem with the whole page comes from the page menu", async () => {
    const fb = fakeFeedback();
    renderStage(doneState([page({ needs_review: false })]), { feedback: fb });
    await userEvent.click(screen.getByRole("button", { name: "Page menu" }));
    await userEvent.click(screen.getByRole("menuitem", { name: /Report a problem on this page/ }));
    const dialog = screen.getByRole("dialog", { name: "Report a problem on page 1" });
    await userEvent.click(within(dialog).getByRole("button", { name: "Paper edges are wrong" }));
    await userEvent.click(within(dialog).getByTestId("report-send"));
    expect(fb.sent).toEqual([{ page: 1, tags: ["corners_wrong"] }]);
    fireEvent.keyDown(document, { key: "Escape" });
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  });
});

describe("page rating in the result card", () => {
  function renderCard(state: State, fb: FeedbackState) {
    return render(
      <I18nProvider lang="en">
        <SettingsCard state={state} dispatch={vi.fn()} formats={[{ format: "pdf", label: "PDF", label_en: "PDF", available: true }]} formatsError={null} presets={[]} onConvert={() => {}} onConvertAgain={() => {}} feedback={fb} />
      </I18nProvider>,
    );
  }

  it("sends {page, rating} for the page on screen and shows the count", async () => {
    const fb = fakeFeedback({ consented: true });
    const state = doneState([page(), page({ page: 2 })], { pageIndex: 1 });
    const { rerender } = renderCard(state, fb);
    const group = screen.getByRole("radiogroup", { name: "Rate page 2, 1 to 5 stars" });
    await userEvent.click(within(group).getByRole("radio", { name: /^4 \/ 5/ }));
    expect(fb.sent).toEqual([{ page: 2, rating: 4 }]);
    rerender(
      <I18nProvider lang="en">
        <SettingsCard state={state} dispatch={vi.fn()} formats={[]} formatsError={null} presets={[]} onConvert={() => {}} onConvertAgain={() => {}} feedback={{ ...fb, items: [...fb.items], ratings: { 2: 4 } }} />
      </I18nProvider>,
    );
    expect(screen.getByTestId("fb-saved")).toHaveTextContent("Saved · 1 note saved for this job");
    expect(within(screen.getByRole("radiogroup")).getByRole("radio", { name: /^4 \/ 5/ })).toHaveAttribute("aria-checked", "true");
    expect(screen.getByTestId("fb-note")).toHaveTextContent("Your corrections are locked: re-running never overwrites them.");
    expect(screen.getByTestId("fb-note")).toHaveTextContent("are used to improve recognition");
  });

  it("arrow keys change the rating", async () => {
    const fb = fakeFeedback({ ratings: { 1: 3 } });
    renderCard(doneState([page()]), fb);
    const three = screen.getByRole("radio", { name: /^3 \/ 5/ });
    act(() => three.focus());
    fireEvent.keyDown(three, { key: "ArrowRight" });
    await vi.waitFor(() => expect(fb.sent).toEqual([{ page: 1, rating: 4 }]));
  });
});
