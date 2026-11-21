"""Excel 輸出器的測試：重新讀入、儲存格內容與格式、合併、欄寬列高、凍結窗格、降級紀錄、
LibreOffice 轉 PDF 後的文字與頁數，以及真實流水線產出的成品文件。"""

from __future__ import annotations

import io
from pathlib import Path

import openpyxl
import pypdfium2 as pdfium
import pytest
from openpyxl.cell.rich_text import CellRichText
from openpyxl.utils import get_column_letter
from PIL import Image

from mortise.artifacts import (
    Align,
    Border,
    Box,
    Color,
    FontChoice,
    Intent,
    OutputFormat,
    Provenance,
    StyledCell,
    StyledDoc,
    StyledImage,
    StyledPage,
    StyledParagraph,
    StyledRun,
    StyledShape,
    StyledTable,
    StyledTextBox,
    TargetMode,
    measured,
)
from mortise.config import Config
from mortise.exporters import xlsx
from mortise.pipeline import Pipeline
from mortise.pipeline.store import Store
from mortise.tools import libreoffice

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"
W = "test@1"


# ---------------------------------------------------------------- 建構測試用成品文件


def run(text, family="Calibri", size=11.0, bold=False, italic=False, color=(0, 0, 0), ea=False, a=1.0):
    font = FontChoice(family=measured(family, 0.9, Provenance.INFERRED, W), verification_family=family, is_substituted=False, east_asian=ea)
    return StyledRun(text=text, font=font, size_pt=measured(float(size), 0.9, Provenance.MEASURED, W), bold=bold, italic=italic, color=Color(r=color[0], g=color[1], b=color[2], a=a))


def para(pid, *runs, align=Align.LEFT, lines=1):
    return StyledParagraph(id=pid, runs=list(runs), align=align, source_line_count=lines)


def border(w, color=(0, 0, 0)):
    return Border(color=Color(r=color[0], g=color[1], b=color[2]), width_pt=w)


def thin4():
    return [border(0.75)] * 4


COL_W = [100.0, 72.75, 150.5]
ROW_H = [24.0, 30.0, 18.75, 40.0]


def main_table() -> StyledTable:
    cells = [
        StyledCell(row=0, col=0, col_span=2, header=True, paragraphs=[para("h1", run("項目名稱", "Microsoft JhengHei", 12, bold=True, color=(255, 255, 255), ea=True), align=Align.CENTER)], fill=Color(r=0x1F, g=0x3A, b=0x5F), borders=thin4(), v_align="middle"),
        StyledCell(row=0, col=2, header=True, paragraphs=[para("h2", run("Amount", "Calibri", 12, bold=True, color=(255, 255, 255)), align=Align.RIGHT)], fill=Color(r=0x1F, g=0x3A, b=0x5F), borders=thin4(), v_align="middle"),
        StyledCell(
            row=1,
            col=0,
            row_span=2,
            paragraphs=[
                para("a1", run("Red ", "Calibri", 11, bold=True, color=(255, 0, 0)), run("apple", "Cambria", 11, italic=True, color=(0, 0, 255))),
                para("a2", run("第二段", "Microsoft JhengHei", 10, ea=True)),
            ],
            borders=thin4(),
            margins_pt=[2.0, 3.0, 2.0, 3.0],
        ),
        StyledCell(row=1, col=1, paragraphs=[para("n1", run("1,234.50"), align=Align.RIGHT)], value=1234.5, number_format="#,##0.00", borders=thin4()),
        StyledCell(row=1, col=2, paragraphs=[para("n2", run("25%"), align=Align.RIGHT)], value=0.25, number_format="0%", borders=thin4()),
        StyledCell(row=2, col=1, paragraphs=[para("b3", run("B3 text", "Arial", 9, color=(0x33, 0x66, 0x99)), align=Align.RIGHT)], v_align="bottom", borders=[border(1.5, (255, 0, 0))] * 4),
        StyledCell(row=2, col=2, paragraphs=[para("f1", run("1,234.50"), align=Align.RIGHT)], value=1234.5, number_format="#,##0.00", formula="SUM(B2:B3)", borders=thin4()),
        StyledCell(row=3, col=0, col_span=3, paragraphs=[para("t1", run("Total 總計", "Microsoft JhengHei", 14, bold=True, ea=True), align=Align.CENTER)], fill=Color(r=255, g=255, b=0, a=0.5), borders=[border(2.25)] * 3 + [border(4.0)], v_align="middle"),
    ]
    return StyledTable(id="p001-table-main", box=Box(x=50, y=100, w=sum(COL_W), h=sum(ROW_H)), z=3, col_widths_pt=COL_W, row_heights_pt=ROW_H, cells=cells, header_rows=1)


def small_table(tid, label, y, z) -> StyledTable:
    cells = [StyledCell(row=r, col=c, paragraphs=[para(f"{tid}-{r}{c}", run(f"{label}{r}{c}"))]) for r in range(2) for c in range(2)]
    return StyledTable(id=tid, box=Box(x=40, y=y, w=200, h=40, rotation=0.0), z=z, col_widths_pt=[80.0, 120.0], row_heights_pt=[20.0, 20.0], cells=cells)


def png_blob(store: Store, color=(200, 30, 30), size=(40, 30)):
    buf = io.BytesIO()
    Image.new("RGB", size, color).save(buf, "PNG")
    return store.put_blob(buf.getvalue(), "image/png", "png")


def table_doc(store: Store) -> StyledDoc:
    tb = StyledTextBox(id="p001-box-title", box=Box(x=50, y=40, w=300, h=30), z=1, paragraphs=[para("t", run("報告標題"))])
    img = StyledImage(id="p001-img-logo", box=Box(x=400, y=40, w=40, h=30), z=2, image=png_blob(store))
    shp = StyledShape(id="p001-shape-bar", box=Box(x=0, y=0, w=595, h=10), z=0, geometry="rect", fill=Color(r=0, g=0, b=0))
    page1 = StyledPage(page_index=0, width_pt=595.0, height_pt=842.0, text_boxes=[tb], images=[img], shapes=[shp], tables=[main_table()], reading_order=["p001-box-title", "p001-table-main"])
    # 第 2 頁不同尺寸；閱讀順序把下面那張表排在前面
    t_a = small_table("p002-table-a", "A", y=300, z=1)
    t_b = small_table("p002-table-b", "B", y=50, z=2)
    page2 = StyledPage(page_index=1, width_pt=960.0, height_pt=540.0, background_color=Color(r=10, g=10, b=10), tables=[t_b, t_a], reading_order=["p002-table-a", "p002-table-b"])
    return StyledDoc(target_mode=measured(TargetMode.CROSS_SOURCE, 1.0, Provenance.INFERRED, W), source_app=measured("unknown", 1.0, Provenance.INFERRED, W), pages=[page1, page2])


def text_only_doc() -> StyledDoc:
    def box(bid, y, texts):
        return StyledTextBox(id=bid, box=Box(x=50, y=y, w=400, h=40), z=1, paragraphs=[para(f"{bid}-{i}", *rs) for i, rs in enumerate(texts)])

    p1 = StyledPage(
        page_index=0,
        width_pt=595,
        height_pt=842,
        text_boxes=[box("b1", 100, [[run("第一頁上方", "Microsoft JhengHei", 14, ea=True)]]), box("b2", 300, [[run("Second ", bold=True), run("box", italic=True)], [run("   ")], [run("third para")]])],
        reading_order=["b2", "b1"],  # 閱讀順序與位置相反
    )
    p2 = StyledPage(page_index=1, width_pt=595, height_pt=842, text_boxes=[box("b3", 100, [[run("Page two")]])], reading_order=["b3"])
    return StyledDoc(target_mode=measured(TargetMode.CROSS_SOURCE, 1.0, Provenance.INFERRED, W), source_app=measured("unknown", 1.0, Provenance.INFERRED, W), pages=[p1, p2])


# ---------------------------------------------------------------- fixtures


@pytest.fixture(scope="module")
def store(tmp_path_factory):
    return Store(tmp_path_factory.mktemp("xlsx-store"))


@pytest.fixture(scope="module")
def cfg():
    return Config.load()


@pytest.fixture(scope="module")
def table_out(store, cfg):
    doc = table_doc(store)
    out = xlsx.export(doc, "report", cfg, store)
    return doc, out, store.read_blob(out.file)


@pytest.fixture(scope="module")
def wb(table_out):
    return openpyxl.load_workbook(io.BytesIO(table_out[2]))


@pytest.fixture(scope="module")
def wb_rich(table_out):
    return openpyxl.load_workbook(io.BytesIO(table_out[2]), rich_text=True)


def pdf_text_and_pages(data: bytes, tmp: Path, name: str) -> tuple[str, int]:
    src = tmp / name
    src.write_bytes(data)
    pdf = pdfium.PdfDocument(libreoffice.to_pdf(src))
    text = "".join(pdf[i].get_textpage().get_text_range() for i in range(len(pdf)))
    return text, len(pdf)


# ---------------------------------------------------------------- 表格


def test_reopens_and_sheets_follow_reading_order(table_out, wb):
    _, out, _ = table_out
    assert out.format == OutputFormat.XLSX and out.filename == "report.xlsx"
    assert out.selfcheck_ok, out.selfcheck_notes
    assert wb.sheetnames == ["第 1 頁 表 1", "第 2 頁 表 1", "第 2 頁 表 2"]
    assert wb["第 2 頁 表 1"]["A1"].value == "A00"  # 閱讀順序中先出現的表
    assert wb["第 2 頁 表 2"]["A1"].value == "B00"


def test_merged_cells_dimensions_and_text(wb, wb_rich):
    ws = wb["第 1 頁 表 1"]
    assert {str(r) for r in ws.merged_cells.ranges} == {"A1:B1", "A2:A3", "A4:C4"}
    assert (ws.max_row, ws.max_column) == (4, 3)
    assert ws["A1"].value == "項目名稱" and ws["C1"].value == "Amount"
    assert ws["B3"].value == "B3 text" and ws["A4"].value == "Total 總計"
    assert str(wb_rich["第 1 頁 表 1"]["A2"].value) == "Red apple\n第二段"


def test_fonts_sizes_colors_fills(wb):
    ws = wb["第 1 頁 表 1"]
    a1 = ws["A1"]
    assert a1.font.name == "Microsoft JhengHei" and a1.font.sz == 12 and a1.font.b
    assert a1.font.color.rgb.endswith("FFFFFF")
    assert a1.font.charset == 136  # 東亞文字的字元集提示
    assert a1.font.scheme in (None, "none")  # 不讓 Excel 改用主題字型
    assert a1.fill.fill_type == "solid" and a1.fill.fgColor.rgb.endswith("1F3A5F")
    c1 = ws["C1"]
    assert c1.font.name == "Calibri" and c1.font.b and c1.font.scheme in (None, "none")
    b3 = ws["B3"]
    assert b3.font.name == "Arial" and b3.font.sz == 9 and b3.font.color.rgb.endswith("336699") and not b3.font.b
    assert ws["A4"].font.name == "Microsoft JhengHei" and ws["A4"].font.sz == 14


def test_rich_text_runs(wb_rich):
    value = wb_rich["第 1 頁 表 1"]["A2"].value
    assert isinstance(value, CellRichText)
    blocks = [b for b in value if not isinstance(b, str)]
    assert [b.text for b in blocks] == ["Red ", "apple", "\n第二段"]
    red, apple, second = (b.font for b in blocks)
    assert red.rFont == "Calibri" and red.b and red.color.rgb.endswith("FF0000") and red.sz == 11
    assert apple.rFont == "Cambria" and apple.i and apple.color.rgb.endswith("0000FF")
    # XlsxWriter 的多格式字串片段只寫字型名稱，不寫 charset（函式庫限制；名稱才是關鍵）
    assert second.rFont == "Microsoft JhengHei" and second.sz == 10
    assert red.scheme in (None, "none")  # Calibri 片段也不得套主題字型


def test_numbers_formats_and_formula(wb, table_out):
    ws = wb["第 1 頁 表 1"]
    assert ws["B2"].value == 1234.5 and ws["B2"].number_format == "#,##0.00" and ws["B2"].data_type == "n"
    assert ws["C2"].value == 0.25 and ws["C2"].number_format == "0%"
    assert ws["C3"].value == "=SUM(B2:B3)" and ws["C3"].number_format == "#,##0.00"
    cached = openpyxl.load_workbook(io.BytesIO(table_out[2]), data_only=True)["第 1 頁 表 1"]
    assert cached["C3"].value == 1234.5  # 公式的快取結果是顯示值
    # 沒有 formula 的儲存格不得寫成公式
    assert all(not (isinstance(c.value, str) and c.value.startswith("=")) for row in ws.iter_rows() for c in row if c.coordinate != "C3")


def test_alignment_borders_wrap(wb):
    ws = wb["第 1 頁 表 1"]
    assert (ws["A1"].alignment.horizontal, ws["A1"].alignment.vertical) == ("center", "center")
    # 靠下是 Excel 的預設值，XlsxWriter 不寫出，讀回是 None
    assert ws["B3"].alignment.horizontal == "right" and ws["B3"].alignment.vertical in ("bottom", None)
    assert ws["A2"].alignment.vertical == "top" and ws["A2"].alignment.wrap_text  # 多段 → 自動換行
    assert not ws["B2"].alignment.wrap_text
    assert ws["A1"].border.top.style == "thin" and ws["A1"].border.left.style == "thin"
    assert ws["B3"].border.bottom.style == "medium" and ws["B3"].border.bottom.color.rgb.endswith("FF0000")
    assert ws["A4"].border.top.style == "thick" and ws["A4"].border.left.style == "thick"


def test_column_widths_row_heights_and_freeze(wb):
    ws = wb["第 1 頁 表 1"]
    for i, w_pt in enumerate(COL_W):
        width = ws.column_dimensions[get_column_letter(i + 1)].width
        got_pt = xlsx.excel_width_to_px(width) * 72 / 96
        assert abs(got_pt - w_pt) <= 0.5, (i, width, got_pt, w_pt)
    for r, h_pt in enumerate(ROW_H, start=1):
        assert abs(ws.row_dimensions[r].height - h_pt) <= 0.5
    assert ws.freeze_panes == "A2"
    assert wb["第 2 頁 表 1"].freeze_panes is None  # 沒有表頭列就不凍結


def test_degradations_recorded(table_out):
    _, out, _ = table_out
    by = {(d.element_id, d.concept) for d in out.degradations}
    assert any(e == "page-1" and "圖片 1" in c and "文字框 1" in c and "形狀 1" in c for e, c in by)
    assert any(e == "page-2" and "頁面底色" in c for e, c in by)
    assert ("p001-table-main", "框線粗細") in by  # 4 pt 量化到粗線
    assert ("p001-table-main", "底色透明度") in by
    assert ("p001-table-main", "儲存格邊距") in by
    assert ("p001-table-main", "表格在頁面上的位置") in by
    assert all(d.lossy for d in out.degradations)
    assert set(out.fonts_used) == {"Calibri", "Cambria", "Arial", "Microsoft JhengHei"}


def test_sheet_name_rules():
    used: set[str] = set()
    assert xlsx.sanitize_sheet_name("第 1 頁 表 1", used) == "第 1 頁 表 1"
    assert xlsx.sanitize_sheet_name("第 1 頁 表 1", used) == "第 1 頁 表 1 (2)"
    bad = xlsx.sanitize_sheet_name("a[b]c:d*e?f/g\\h", used)
    assert not set("[]:*?/\\") & set(bad)
    long = xlsx.sanitize_sheet_name("x" * 40, used)
    assert len(long) == 31
    assert len(xlsx.sanitize_sheet_name("x" * 40, used)) <= 31
    assert xlsx.sanitize_sheet_name("'quoted'", used) == "quoted"


def test_libreoffice_render_keeps_text_and_pages(table_out, tmp_path):
    _, _, data = table_out
    text, pages = pdf_text_and_pages(data, tmp_path, "report.xlsx")
    assert pages == 3  # 三張工作表，每張一頁
    for s in ["項目名稱", "Amount", "Red", "apple", "第二段", "1,234.50", "25%", "B3 text", "Total", "總計", "A00", "B11"]:
        assert s in text, s


# ---------------------------------------------------------------- 沒有表格


def test_no_table_fallback(store, cfg, tmp_path):
    out = xlsx.export(text_only_doc(), "notes", cfg, store)
    data = store.read_blob(out.file)
    wb = openpyxl.load_workbook(io.BytesIO(data), rich_text=True)
    assert len(wb.sheetnames) == 1 and out.selfcheck_ok
    ws = wb.worksheets[0]
    values = [str(ws.cell(row=r, column=1).value) for r in range(1, ws.max_row + 1)]
    assert values == ["Second box", "third para", "第一頁上方", "Page two"]  # 依閱讀順序、空白段略過
    assert isinstance(ws["A1"].value, CellRichText)
    assert ws["A3"].value.__str__() == "第一頁上方"
    plain = openpyxl.load_workbook(io.BytesIO(data)).worksheets[0]
    assert plain["A3"].font.name == "Microsoft JhengHei" and plain["A3"].font.sz == 14
    assert any(d.concept == "表格" and "沒有偵測到表格" in d.action for d in out.degradations)
    text, pages = pdf_text_and_pages(data, tmp_path, "notes.xlsx")
    assert pages == 1 and all(v in text for v in values)


@pytest.fixture(scope="module")
def real_doc(tmp_path_factory):
    p = Pipeline(tmp_path_factory.mktemp("xlsx-job"))
    p.start(FIXTURES / "doc_a4.pdf", "doc_a4.pdf", Intent())
    p.run()
    return p.store.load("s5/styled_doc", StyledDoc), p.store


def _cell_text(cell) -> str:
    return "\n".join("".join(r.text for r in p.runs) for p in cell.paragraphs)


def test_real_pipeline_doc(real_doc, cfg, tmp_path):
    """真實流水線的成品文件：有表格就逐格比對，沒有表格就比對一列一段的文字。"""
    doc, st = real_doc
    out = xlsx.export(doc, "doc_a4", cfg, st)
    assert out.selfcheck_ok, out.selfcheck_notes
    data = st.read_blob(out.file)
    wb = openpyxl.load_workbook(io.BytesIO(data), rich_text=True)
    tables = [t for pg in doc.pages for t in pg.tables]
    expected_text: list[str] = []
    if tables:
        assert len(wb.sheetnames) == len(tables)
        for ws, t in zip(wb.worksheets, tables):
            assert (ws.max_row, ws.max_column) == (len(t.row_heights_pt), len(t.col_widths_pt))
            assert ws.freeze_panes == (f"A{t.header_rows + 1}" if t.header_rows else None)
            for c in t.cells:
                got = ws.cell(row=c.row + 1, column=c.col + 1)
                if c.value is not None:
                    assert got.value == c.value and got.number_format == (c.number_format or "General")
                else:
                    assert str(got.value) == _cell_text(c)
                    expected_text.append(_cell_text(c))
                for r in (r for p in c.paragraphs for r in p.runs):
                    assert r.font.family.value in out.fonts_used
    else:
        ws = wb.worksheets[0]
        got = [str(ws.cell(row=r, column=1).value) for r in range(1, ws.max_row + 1)]
        for page in doc.pages:
            order = {e: i for i, e in enumerate(page.reading_order)}
            for tb in sorted(page.text_boxes, key=lambda t: (order.get(t.id, len(order)), t.box.y, t.box.x)):
                expected_text += ["".join(r.text for r in p.runs) for p in tb.paragraphs if "".join(r.text for r in p.runs).strip()]
        assert got == expected_text and len(got) > 0
    assert expected_text
    text, pages = pdf_text_and_pages(data, tmp_path, "doc_a4.xlsx")
    assert pages == len(wb.sheetnames)
    flat = text.replace("\r", "").replace("\n", "")
    for s in expected_text:
        assert s.replace("\n", "") in flat, s
