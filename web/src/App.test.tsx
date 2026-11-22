import { act, fireEvent, render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import App from "./App";

const formats = [
  { format: "pptx", label: "PowerPoint（.pptx）", label_en: "PowerPoint (.pptx)", available: true },
  { format: "pdf", label: "可編輯 PDF（.pdf）", label_en: "Editable PDF (.pdf)", available: true },
  { format: "svg", label: "SVG 整頁（.svg；多頁為 .zip）", label_en: "SVG (.svg)", available: true },
  { format: "idml", label: "InDesign（.idml）", label_en: "InDesign (.idml)", available: false },
];

function mockServer() {
  const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
    const url = String(input);
    const json = (b: unknown) => new Response(JSON.stringify(b), { status: 200, headers: { "Content-Type": "application/json" } });
    if (url.endsWith("/api/health")) return json({ ok: true, verify_engine: null, formats: ["pptx", "pdf", "svg"], max_file_mb: 1, accepted: ["application/pdf", "image/png", "image/jpeg"] });
    if (url.endsWith("/api/formats")) return json(formats);
    if (url.endsWith("/api/page-sizes")) return json([{ id: "A3", label: "A3", width_mm: 297, height_mm: 420 }, { id: "A4", label: "A4", width_mm: 210, height_mm: 297 }]);
    return new Response("{}", { status: 404 });
  });
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

describe("App", () => {
  beforeEach(() => {
    localStorage.clear();
    mockServer();
  });
  afterEach(() => vi.unstubAllGlobals());

  it("shows the mockup's controls with SVG chosen by default", async () => {
    render(<App />);
    expect(screen.getByText("MORTISE")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "File menu" })).toHaveTextContent("File");
    expect(screen.getByRole("button", { name: "Account" })).toBeInTheDocument();
    const fmt = await screen.findByRole("combobox", { name: "Output format" });
    await vi.waitFor(() => expect(fmt).toHaveValue("svg"));
    expect(within(fmt).getByRole("option", { name: /InDesign/ })).toBeDisabled();
    const size = screen.getByRole("combobox", { name: "Output page size" });
    expect(size).toHaveValue("original");
    expect(within(size).getAllByRole("option").map((o) => o.textContent)).toEqual(["Original size", "A3", "A4", "Custom"]);
    expect(screen.getByRole("button", { name: /CONVERT/ })).toHaveAttribute("aria-disabled", "true");
    expect(screen.getByText("Drop a PDF, PNG or JPEG here")).toBeInTheDocument();
  });

  it("switches the output size to Custom when W or H is typed", async () => {
    const user = userEvent.setup();
    render(<App />);
    const size = screen.getByRole("combobox", { name: "Output page size" });
    await user.type(screen.getByRole("textbox", { name: "Output width in millimetres" }), "300");
    expect(size).toHaveValue("custom");
    await user.tab();
    await user.tab();
    expect(await screen.findByText("Fill in both W and H.")).toBeInTheDocument();
    await user.type(screen.getByRole("textbox", { name: "Output height in millimetres" }), "200");
    expect(screen.queryByText("Fill in both W and H.")).not.toBeInTheDocument();
    await user.selectOptions(size, "A3");
    expect(screen.getByRole("textbox", { name: "Output width in millimetres" })).toHaveValue("");
    expect(screen.getByRole("radiogroup", { name: "Orientation" })).toBeInTheDocument();
  });

  it("warns about a half-filled origin size", async () => {
    const user = userEvent.setup();
    render(<App />);
    await user.type(screen.getByRole("textbox", { name: "Original width in millimetres" }), "0");
    await user.tab();
    expect(await screen.findByText("Use numbers greater than 0.")).toBeInTheDocument();
  });

  it("rejects unsupported and oversized files inside the frame", async () => {
    render(<App />);
    await screen.findByRole("combobox", { name: "Output format" });
    const input = screen.getByTestId("file-input") as HTMLInputElement;
    await act(async () => {
      fireEvent.change(input, { target: { files: [new File(["hello"], "notes.docx", { type: "application/msword" })] } });
    });
    expect(await screen.findByRole("alert")).toHaveTextContent("“notes.docx” is not a PDF, PNG or JPEG.");
    await vi.waitFor(() => expect(screen.getByText(/Up to 1 MB/)).toBeInTheDocument());
    const big = new File([new Uint8Array(2 * 1024 * 1024)], "big.pdf", { type: "application/pdf" });
    await act(async () => {
      fireEvent.change(input, { target: { files: [big] } });
    });
    expect(await screen.findByRole("alert")).toHaveTextContent("“big.pdf” is 2.0 MB. The limit is 1 MB.");
  });

  it("switches the interface to Traditional Chinese and remembers it", async () => {
    const user = userEvent.setup();
    const { unmount } = render(<App />);
    await user.click(screen.getByRole("button", { name: "Account" }));
    const dialog = screen.getByRole("dialog", { name: "Account" });
    expect(within(dialog).getByText("Accounts are coming soon")).toBeInTheDocument();
    await user.click(within(dialog).getByRole("radio", { name: "繁體中文" }));
    expect(screen.getByRole("button", { name: /轉換/ })).toBeInTheDocument();
    expect(screen.getByText("轉換為：")).toBeInTheDocument();
    expect(document.documentElement.lang).toBe("zh-Hant");
    const fmt = screen.getByRole("combobox", { name: "輸出格式" });
    await vi.waitFor(() => expect(within(fmt).getByRole("option", { name: "SVG 整頁（.svg；多頁為 .zip）" })).toBeInTheDocument());
    unmount();
    render(<App />);
    expect(screen.getByText("把 PDF、PNG 或 JPEG 拖到這裡")).toBeInTheDocument();
  });

  it("stores the training consent (off by default)", async () => {
    const user = userEvent.setup();
    render(<App />);
    await user.click(screen.getByRole("button", { name: "Account" }));
    const sw = screen.getByRole("switch", { name: "Help improve recognition with my files" });
    expect(sw).toHaveAttribute("aria-checked", "false");
    await user.click(sw);
    expect(sw).toHaveAttribute("aria-checked", "true");
    expect(localStorage.getItem("mortise.allowTraining")).toBe("true");
  });

  it("keeps download items disabled until a job is done", async () => {
    const user = userEvent.setup();
    render(<App />);
    await user.click(screen.getByRole("button", { name: "File menu" }));
    const menu = screen.getByRole("menu");
    expect(within(menu).getByRole("menuitem", { name: "Open file…" })).not.toHaveAttribute("aria-disabled");
    expect(within(menu).getByRole("menuitem", { name: "Download" })).toHaveAttribute("aria-disabled", "true");
    expect(within(menu).getByRole("menuitem", { name: "Download report" })).toHaveAttribute("aria-disabled", "true");
    expect(within(menu).getByRole("menuitem", { name: "Delete from server" })).toHaveAttribute("aria-disabled", "true");
    await user.keyboard("{Escape}");
    expect(screen.queryByRole("menu")).not.toBeInTheDocument();
  });
});
