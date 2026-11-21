"""給 Illustrator 的 PDF 輸出器的測試：重新讀入、每個字的位置（pypdfium2 讀回，與文字框比對）、字型與 ToUnicode、
一行一個文字物件、斷行、對齊、表格、形狀、圖片、OCG 圖層、降級紀錄、LibreOffice 匯入，以及真實流水線產出的成品文件
（與原始 PDF 逐字比對位置）。"""

from __future__ import annotations

import copy
import io
import math
import statistics
from pathlib import Path

import pikepdf
import pypdfium2 as pdfium
import pytest
from fontTools.ttLib import TTFont
from PIL import Image

from mortise.artifacts import (
    Align,
    Border,
    Box,
    Color,
    FontChoice,
    Intent,
    LineSpacing,
    LineSpacingMode,
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
from mortise.exporters import ai_pdf
from mortise.pipeline import Pipeline
from mortise.pipeline.store import Store
from mortise.tools import libreoffice
from mortise.tools import pdfium as mp

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"
W = "test@1"
LIB = "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf"
WQY = "/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc"


def hhea_ascent(path: str, index: int = 0) -> float:
    f = TTFont(path, fontNumber=index, lazy=True)
    return round(f["hhea"].ascent * 1000 / f["head"].unitsPerEm) / 1000


# ---------------------------------------------------------------- 建構測試用成品文件


def run(text, family="Liberation Sans", size=12.0, bold=False, italic=False, color=(0, 0, 0), ea=False, a=1.0, verify=None):
    font = FontChoice(family=measured(family, 0.9, Provenance.INFERRED, W), verification_family=verify or family, is_substituted=verify is not None and verify != family, east_asian=ea)
    return StyledRun(text=text, font=font, size_pt=measured(float(size), 0.9, Provenance.MEASURED, W), bold=bold, italic=italic, color=Color(r=color[0], g=color[1], b=color[2], a=a))


def para(pid, *runs, align=Align.LEFT, lines=1, exact=None, after=0.0, indent=0.0, style=None):
    ls = measured(LineSpacing(mode=LineSpacingMode.EXACT, value=exact), 0.8, Provenance.MEASURED, W) if exact else None
    return StyledParagraph(id=pid, runs=list(runs), align=align, source_line_count=lines, line_spacing=ls, space_after_pt=after, first_line_indent_pt=indent, style_id=style)


def tbox(tid, x, y, w, h, *paras, z=1, rot=0.0, wrap=False):
    return StyledTextBox(id=tid, box=Box(x=x, y=y, w=w, h=h, rotation=rot), z=z, paragraphs=list(paras), wrap=wrap)


def png_blob(store: Store, color=(200, 30, 30, 255), size=(40, 30)):
    buf = io.BytesIO()
    Image.new("RGBA", size, color).save(buf, "PNG")
    return store.put_blob(buf.getvalue(), "image/png", "png")


def border(w, color=(0, 0, 0)):
    return Border(color=Color(r=color[0], g=color[1], b=color[2]), width_pt=w)


CJK_TEXT = "一二三四五六七八九十" * 3
EN_TEXT = "The quick brown fox jumps over the lazy dog again and again"


def table() -> StyledTable:
    b4 = [border(0.75)] * 4
    cells = [
        StyledCell(row=0, col=0, col_span=2, header=True, paragraphs=[para("h1", run("合併標題", "WenQuanYi Zen Hei", 12, color=(255, 255, 255), ea=True), align=Align.CENTER)], fill=Color(r=0x1F, g=0x3A, b=0x5F), borders=b4, v_align="middle"),
        StyledCell(row=0, col=2, header=True, paragraphs=[para("h2", run("Amount", size=12, bold=True, color=(255, 255, 255)), align=Align.RIGHT)], fill=Color(r=0x1F, g=0x3A, b=0x5F), borders=b4, v_align="middle"),
        StyledCell(row=1, col=0, row_span=2, paragraphs=[para("a1", run("跨兩列", "WenQuanYi Zen Hei", 10, ea=True))], borders=b4, margins_pt=[2.0, 3.0, 2.0, 3.0]),
        StyledCell(row=1, col=1, paragraphs=[para("n1", run("1,234.50", size=10), align=Align.RIGHT)], value=1234.5, number_format="#,##0.00", borders=b4),
        StyledCell(row=1, col=2, paragraphs=[para("n2", run("25%", size=10), align=Align.RIGHT)], borders=[border(2.0, (255, 0, 0))] * 4, fill=Color(r=255, g=255, b=0)),
        StyledCell(row=2, col=1, col_span=2, paragraphs=[para("b3", run("Bottom", size=10), align=Align.CENTER)], v_align="bottom", borders=b4),
    ]
    return StyledTable(id="p001-table", box=Box(x=300, y=600, w=240, h=90), z=5, col_widths_pt=[60.0, 80.0, 100.0], row_heights_pt=[30.0, 30.0, 30.0], cells=cells, header_rows=1)


def build_doc(store: Store) -> StyledDoc:
    p1 = StyledPage(
        page_index=0, width_pt=595.0, height_pt=842.0,
        text_boxes=[
            tbox("p1-latin", 50, 40, 300, 30, para("t1", run("Hello Mortise", size=24, color=(0x12, 0x34, 0x56)))),
            tbox("p1-mixed", 50, 90, 400, 30, para("t2", run("Hello ", size=18, bold=True), run("你好世界", "Microsoft JhengHei", 18, color=(200, 0, 0), ea=True, verify="WenQuanYi Zen Hei"), run(" end", "Liberation Serif", 14, italic=True))),
            tbox("p1-cjk", 50, 140, 101.5, 50, para("t3", run(CJK_TEXT, "WenQuanYi Zen Hei", 10, ea=True), lines=3, exact=14.0), wrap=True),
            tbox("p1-en", 200, 140, 151.5, 80, para("t4", run(EN_TEXT, size=12), lines=3, exact=15.0, align=Align.JUSTIFY), wrap=True),
            tbox("p1-center", 50, 240, 200, 20, para("t5", run("Centered", size=14), align=Align.CENTER)),
            tbox("p1-right", 300, 240, 200, 20, para("t6", run("Right side", size=14), align=Align.RIGHT)),
            tbox("p1-alpha", 50, 280, 200, 20, para("t7", run("Half alpha", size=14, color=(0, 0, 255), a=0.5))),
            tbox("p1-two", 50, 320, 300, 60, para("t8", run("First paragraph", size=12), exact=16.0, after=6.0), para("t9", run("Second paragraph", size=12), exact=16.0)),
        ],
        images=[StyledImage(id="p1-img", box=Box(x=400, y=40, w=80, h=60), z=2, image=png_blob(store))],
        shapes=[
            StyledShape(id="p1-rect", box=Box(x=50, y=420, w=100, h=60), z=0, geometry="rect", fill=Color(r=0, g=128, b=0)),
            StyledShape(id="p1-round", box=Box(x=170, y=420, w=100, h=60), z=0, geometry="rounded_rect", corner_radius_pt=20.0, fill=Color(r=0, g=0, b=200), stroke=Color(r=0, g=0, b=0), stroke_width_pt=1.0),
            StyledShape(id="p1-oval", box=Box(x=290, y=420, w=100, h=60), z=0, geometry="ellipse", fill=Color(r=200, g=0, b=200)),
            StyledShape(id="p1-line", box=Box(x=50, y=500, w=200, h=0), z=0, geometry="line", stroke=Color(r=255, g=0, b=0), stroke_width_pt=3.0),
            StyledShape(id="p1-path", box=Box(x=410, y=420, w=100, h=100), z=0, geometry="path", fill=Color(r=255, g=128, b=0, a=0.8),
                        path=[[0, 0, 100], [1, 100, 100], [2, 100, 40, 60, 0, 50, 0], [2, 40, 0, 0, 40, 0, 100], [3]]),
        ],
        tables=[table()],
        reading_order=["p1-latin", "p1-mixed", "p1-cjk", "p1-en", "p1-center", "p1-right", "p1-alpha", "p1-two", "p001-table"],
    )
    p2 = StyledPage(
        page_index=1, width_pt=960.0, height_pt=540.0, background_color=Color(r=20, g=30, b=48),
        text_boxes=[
            tbox("p2-rot", 400, 200, 200, 40, para("r1", run("Rotated 30", size=20, color=(255, 255, 255))), rot=30.0),
            tbox("p2-title", 60, 40, 600, 50, para("r2", run("產品路線圖 Roadmap", "WenQuanYi Zen Hei", 32, color=(255, 255, 255), ea=True))),
        ],
        reading_order=["p2-title", "p2-rot"],
    )
    p3 = StyledPage(page_index=2, width_pt=300.0, height_pt=300.0, background_image=png_blob(store, (0, 0, 255, 255), (30, 30)),
                    text_boxes=[tbox("p3-t", 20, 20, 200, 30, para("s1", run("Page three", size=16, color=(255, 255, 255))))])
    return StyledDoc(target_mode=measured(TargetMode.CROSS_SOURCE, 0.5, Provenance.INFERRED, W), source_app=measured("unknown", 0.0, Provenance.INFERRED, W), pages=[p1, p2, p3])


def cfg_with(**ai) -> Config:
    base = Config.load()
    data = copy.deepcopy(base.data)
    data.setdefault("export", {})["ai_pdf"] = {"ascent_metric": "hhea", **ai}
    return Config(data)


# ---------------------------------------------------------------- fixtures


@pytest.fixture(scope="module")
def store(tmp_path_factory) -> Store:
    return Store(tmp_path_factory.mktemp("ai-pdf-store"))


@pytest.fixture(scope="module")
def doc(store) -> StyledDoc:
    return build_doc(store)


@pytest.fixture(scope="module")
def out(doc, store):
    return ai_pdf.export(doc, "test", cfg_with(), store)


@pytest.fixture(scope="module")
def pdf_path(out, store) -> str:
    return str(store.blob_path(out.file))


@pytest.fixture(scope="module")
def chars(pdf_path):
    d = mp.open_document(pdf_path)
    pages = []
    for i in range(len(d)):
        r = mp.PageReader(d, i)
        pages.append([c for c in r.chars() if c.text.strip() and not c.generated])
        r.close()
    return pages


def box_chars(page_chars, box: Box, page_h: float, pad: float = 2.0):
    """落在框內（未旋轉）的字。"""
    return [c for c in page_chars if box.x - pad <= c.origin[0] <= box.x + box.w + pad and box.y - pad <= page_h - c.origin[1] <= box.y + box.h + pad]


def lines_of(cs):
    rows: dict[float, list] = {}
    for c in cs:
        rows.setdefault(round(c.origin[1], 1), []).append(c)
    return [sorted(rows[k], key=lambda c: c.origin[0]) for k in sorted(rows, reverse=True)]


# ---------------------------------------------------------------- 重新讀入與結構


def test_reopen_pages_and_sizes(out, pdf_path, doc):
    assert out.selfcheck_ok, out.selfcheck_notes
    pdf = pikepdf.open(pdf_path)
    assert len(pdf.pages) == 3
    for pg, sp in zip(pdf.pages, doc.pages):
        mb = [float(v) for v in pg.mediabox]
        assert mb[2] - mb[0] == pytest.approx(sp.width_pt) and mb[3] - mb[1] == pytest.approx(sp.height_pt)
    d = pdfium.PdfDocument(pdf_path)
    assert len(d) == 3
    text = "".join(d[i].get_textpage().get_text_range() for i in range(3)).replace("\r", "").replace("\n", "")
    for needle in ("Hello Mortise", "你好世界", "Rotated 30", "Page three", "合併標題", "1,234.50"):
        assert needle in text.replace(" ", "") or needle in text


def test_fonts_embedded_with_tounicode_and_names(pdf_path, out):
    pdf = pikepdf.open(pdf_path)
    bases = set()
    for pg in pdf.pages:
        for _, f in dict(pg.obj.Resources.Font).items():
            if f.get("/FontDescriptor") is None:
                continue
            assert "/FontFile2" in f.FontDescriptor, f.BaseFont
            assert "/ToUnicode" in f
            bases.add(str(f.BaseFont).split("+", 1)[-1])
    assert "LiberationSans" in bases and "LiberationSans-Bold" in bases and "WenQuanYiZenHei" in bases
    assert not any(b.endswith("-0") for b in bases)  # TTC 抽成單一字型，沒有「-序號」字尾
    assert {"Liberation Sans", "WenQuanYi Zen Hei", "Liberation Serif"} <= set(out.fonts_used)


def test_one_text_object_per_line_no_per_glyph_positioning(pdf_path, chars):
    """H1、H2：每個 BT…ET 只有一個 Tm，只用 Tj 畫字，沒有 TJ 與行內移位。"""
    pdf = pikepdf.open(pdf_path)
    for pi, pg in enumerate(pdf.pages):
        n_bt = 0
        in_bt = False
        tm = tj = 0
        for operands, op in pikepdf.parse_content_stream(pg):
            op = str(op)
            if op == "BT":
                in_bt, tm, tj = True, 0, 0
            elif op == "ET":
                if tj:  # ReportLab 每頁開頭有一個只設字型、不畫字的文字物件，不算
                    assert tm == 1
                    n_bt += 1
                in_bt = False
            elif not in_bt and op in ("Tj", "TJ", "Tf", "Tm"):
                raise AssertionError(f"{op} 出現在 BT…ET 之外")
            elif in_bt:
                assert op not in ("TJ", "Td", "TD", "T*", "'", '"'), op
                tm += op == "Tm"
                tj += op == "Tj"
        if pi == 1:  # 第 2 頁沒有表格：文字物件數 = 行數
            assert n_bt == 2


# ---------------------------------------------------------------- 位置、字級、顏色、字型


def test_latin_position_size_color(chars, doc):
    tb = doc.pages[0].text_boxes[0]
    cs = box_chars(chars[0], tb.box, 842.0)
    first = cs[0]
    asc = hhea_ascent(LIB)
    assert first.text == "H"
    assert abs(first.origin[0] - 50.0) < 0.5
    assert abs(first.origin[1] - (842.0 - (40.0 + asc * 24))) < 0.5
    assert first.size == pytest.approx(24.0, abs=0.01)
    assert first.fill[:3] == (0x12, 0x34, 0x56)
    assert "LiberationSans" in first.font_base


def test_mixed_line_latin_and_east_asian_fonts(chars, doc, out):
    tb = doc.pages[0].text_boxes[1]
    cs = box_chars(chars[0], tb.box, 842.0)
    by = {c.text: c for c in cs}
    assert "LiberationSans-Bold" in by["H"].font_base
    assert "WenQuanYiZenHei" in by["你"].font_base and by["你"].fill[:3] == (200, 0, 0)
    assert "LiberationSerif-Italic" in by["e"].font_base and by["e"].size == pytest.approx(14.0, abs=0.01)
    ys = {round(c.origin[1], 2) for c in cs}
    assert len(ys) == 1  # 同一條基線
    # 首行基線 = 框上緣 + 這行最大的上緣（WQY 的上緣比 Liberation 高）
    asc = max(hhea_ascent(WQY) * 18, hhea_ascent(LIB) * 18)
    assert abs(cs[0].origin[1] - (842.0 - (90.0 + asc))) < 0.5
    sub = [d for d in out.degradations if d.concept == "字型" and "Microsoft JhengHei" in d.action]
    assert sub and sub[0].lossy


def test_cjk_breaks_and_line_pitch(chars, doc):
    tb = doc.pages[0].text_boxes[2]
    ls = lines_of(box_chars(chars[0], tb.box, 842.0))
    assert ["".join(c.text for c in ln) for ln in ls] == ["一二三四五六七八九十"] * 3
    ys = [ln[0].origin[1] for ln in ls]
    assert ys[0] - ys[1] == pytest.approx(14.0, abs=0.01) and ys[1] - ys[2] == pytest.approx(14.0, abs=0.01)
    assert all(abs(ln[0].origin[0] - 50.0) < 0.5 for ln in ls)


def test_english_breaks_at_spaces_and_justify(chars, doc, store, out):
    tb = doc.pages[0].text_boxes[3]
    ls = lines_of(box_chars(chars[0], tb.box, 842.0))
    words = [w for ln in ls for w in "".join(c.text for c in ln).split()]
    assert len(ls) == 3
    # 每行都是完整的字（字與字之間的空白由 PDFium 依位置還原，比對時去掉空白）
    assert "".join(words) == EN_TEXT.replace(" ", "")
    right = tb.box.x + tb.box.w - 1.5  # 斷行寬度 = 框寬 − ⑤多留的 1.5 pt
    for ln in ls[:-1]:  # 左右對齊：除末行外右緣對齊
        assert abs(ln[-1].loose_box[2] - right) < 0.5
    assert ls[-1][-1].loose_box[2] < right - 5
    assert not [d for d in out.degradations if d.element_id == "p1-en" and d.concept == "斷行"]


def test_center_right_alpha_and_paragraph_gap(chars, doc):
    page = doc.pages[0]
    c_cs = box_chars(chars[0], page.text_boxes[4].box, 842.0)
    mid = (c_cs[0].origin[0] + c_cs[-1].loose_box[2]) / 2
    assert abs(mid - 150.0) < 0.5
    r_cs = box_chars(chars[0], page.text_boxes[5].box, 842.0)
    assert abs(r_cs[-1].loose_box[2] - 500.0) < 0.5
    a_cs = box_chars(chars[0], page.text_boxes[6].box, 842.0)
    assert a_cs[0].fill == (0, 0, 255, 128)  # 不透明度寫成 ExtGState，PDF 無損
    two = lines_of(box_chars(chars[0], page.text_boxes[7].box, 842.0))
    assert len(two) == 2 and two[0][0].origin[1] - two[1][0].origin[1] == pytest.approx(16.0 + 6.0, abs=0.01)


def test_rotated_text_box_position_and_angle(chars, doc):
    tb = doc.pages[1].text_boxes[0]
    b = tb.box
    first = next(c for c in chars[1] if c.text == "R")
    asc = hhea_ascent(LIB) * 20
    th = math.radians(30)
    dx, dy = -b.w / 2, -b.h / 2 + asc
    px = b.x + b.w / 2 + math.cos(th) * dx - math.sin(th) * dy
    py = b.y + b.h / 2 + math.sin(th) * dx + math.cos(th) * dy
    assert abs(first.origin[0] - px) < 0.5 and abs(first.origin[1] - (540.0 - py)) < 0.5
    assert math.degrees(first.angle) == pytest.approx(-30.0, abs=0.1)  # PDF 空間逆時針為正：順時針 30 度


# ---------------------------------------------------------------- 表格、形狀、圖片、底圖


@pytest.fixture(scope="module")
def renders(pdf_path):
    d = pdfium.PdfDocument(pdf_path)
    imgs = [d[i].render(scale=2).to_pil().convert("RGB") for i in range(len(d))]
    d.close()
    return imgs


def px(img, x, y):
    return img.getpixel((round(x * 2), round(y * 2)))


def close(a, b, tol=12):
    return all(abs(int(u) - int(v)) <= tol for u, v in zip(a, b))


def test_table_merges_text_fill_borders(chars, renders, out):
    page_h = 842.0
    t = table()
    cs = [c for c in chars[0] if t.box.y - 1 <= page_h - c.origin[1] <= t.box.y + t.box.h + 1 and t.box.x - 1 <= c.origin[0] <= t.box.x + t.box.w + 1]
    text = "".join(c.text for c in cs)
    for needle in ("合併標題", "Amount", "跨兩列", "1,234.50", "25%", "Bottom"):
        assert needle in text
    row = lambda r: [c for c in cs if t.box.y + 30 * r <= page_h - c.origin[1] <= t.box.y + 30 * (r + 1)]  # noqa: E731
    head = [c for c in row(0) if c.text in "合併標題"]
    mid = (head[0].origin[0] + head[-1].loose_box[2]) / 2
    assert abs(mid - (300 + 140 / 2)) < 0.5  # 跨兩欄、置中
    amount = [c for c in row(0) if c.text in "Amount"]
    assert abs(amount[-1].loose_box[2] - 540.0) < 0.5  # 靠右、沒有邊距
    bottom = [c for c in row(2) if c.text in "Bottom"]
    assert page_h - bottom[0].origin[1] + (-bottom[0].descent) == pytest.approx(690.0, abs=0.5)  # 靠下
    img = renders[0]
    assert close(px(img, 302, 602), (0x1F, 0x3A, 0x5F))  # 標題列底色
    assert close(px(img, 445, 657), (255, 255, 0))  # 黃底
    assert close(px(img, 540, 645), (255, 0, 0), 40)  # 紅框（2 pt）
    assert any(d.concept == "表格" and d.lossy for d in out.degradations)
    assert any(d.concept == "數字格式與公式" for d in out.degradations)


def test_shapes_render(renders):
    img = renders[0]
    white = (255, 255, 255)
    assert close(px(img, 100, 450), (0, 128, 0))
    assert close(px(img, 220, 450), (0, 0, 200)) and close(px(img, 172, 422), white)  # 圓角：角落沒有填色
    assert close(px(img, 340, 450), (200, 0, 200)) and close(px(img, 292, 422), white)  # 橢圓
    assert close(px(img, 150, 500), (255, 0, 0), 30)  # 直線
    assert close(px(img, 460, 500), (255, 153, 51), 20) and close(px(img, 412, 425), white)  # 自訂路徑（含貝茲，80% 不透明）


def test_image_original_resolution_and_background(pdf_path, renders):
    pdf = pikepdf.open(pdf_path)
    xobjs = [x for x in dict(pdf.pages[0].Resources.XObject).values() if x.get("/Subtype") == "/Image"]
    assert any(int(x.Width) == 40 and int(x.Height) == 30 for x in xobjs)
    assert all("/SMask" in x for x in xobjs if int(x.Width) == 40)
    assert close(px(renders[0], 440, 70), (200, 30, 30))
    assert close(px(renders[1], 5, 5), (20, 30, 48))  # 第 2 頁底色
    assert close(px(renders[2], 290, 290), (0, 0, 255))  # 第 3 頁底圖


def test_ocg_layers(pdf_path):
    pdf = pikepdf.open(pdf_path)
    ocp = pdf.Root.OCProperties
    assert [str(o.Name) for o in ocp.OCGs] == ["背景", "圖片", "形狀", "文字"]
    content = pdf.pages[0].Contents.read_bytes() if not isinstance(pdf.pages[0].Contents, pikepdf.Array) else b"".join(c.read_bytes() for c in pdf.pages[0].Contents)
    assert b"/OC /MOC3 BDC" in content and b"/OC /MOC2 BDC" in content
    assert "/MOC0" in pdf.pages[1].Resources.Properties


def test_degradations_for_lossy_concepts(out):
    concepts = {d.concept for d in out.degradations}
    assert {"段落與文字框", "表格", "字型", "尚未轉成可編輯物件的內容"} <= concepts
    assert all(d.element_id for d in out.degradations)


def test_line_count_mismatch_recorded(store):
    page = StyledPage(page_index=0, width_pt=200, height_pt=100, text_boxes=[tbox("lc", 10, 10, 180, 60, para("c", run("short text", size=12), lines=3), wrap=True)])
    doc = StyledDoc(target_mode=measured(TargetMode.CROSS_SOURCE, 0.5, Provenance.INFERRED, W), source_app=measured("unknown", 0.0, Provenance.INFERRED, W), pages=[page])
    o = ai_pdf.export(doc, "lc", cfg_with(), store)
    assert any(d.concept == "斷行" and d.element_id == "lc" and d.lossy for d in o.degradations)


# ---------------------------------------------------------------- 字型選項與退回


def test_cff_font_falls_back_with_degradation(store):
    page = StyledPage(page_index=0, width_pt=200, height_pt=100, text_boxes=[tbox("cff", 10, 10, 180, 30, para("c", run("Inter text", "Inter", 12, verify="Liberation Sans")))])
    doc = StyledDoc(target_mode=measured(TargetMode.CROSS_SOURCE, 0.5, Provenance.INFERRED, W), source_app=measured("unknown", 0.0, Provenance.INFERRED, W), pages=[page])
    o = ai_pdf.export(doc, "cff", cfg_with(), store)
    assert o.selfcheck_ok, o.selfcheck_notes
    assert any(d.concept == "字型（CFF 外框）" for d in o.degradations)
    assert o.fonts_used == ["Liberation Sans"]


def test_basefont_inferred_name_option(store):
    page = StyledPage(page_index=0, width_pt=200, height_pt=100, text_boxes=[tbox("cal", 10, 10, 180, 30, para("c", run("Calibri text", "Calibri", 12, bold=True, verify="Carlito")))])
    doc = StyledDoc(target_mode=measured(TargetMode.CROSS_SOURCE, 0.5, Provenance.INFERRED, W), source_app=measured("unknown", 0.0, Provenance.INFERRED, W), pages=[page])
    o = ai_pdf.export(doc, "cal", cfg_with(basefont_name="inferred"), store)
    pdf = pikepdf.open(store.blob_path(o.file))
    bases = [str(f.BaseFont) for f in dict(pdf.pages[0].Resources.Font).values() if "/FontDescriptor" in f]
    assert any(b.endswith("+Calibri-Bold") for b in bases), bases
    assert o.fonts_used == ["Calibri"]
    o2 = ai_pdf.export(doc, "cal", cfg_with(), store)  # 預設：寫內嵌字型自己的名稱
    assert o2.fonts_used == ["Carlito"]
    assert any(d.concept == "字型" and "Calibri" in d.action for d in o2.degradations)


# ---------------------------------------------------------------- 第三方匯入：LibreOffice Draw 開 PDF（類似 Illustrator 的逐行文字物件）


def test_libreoffice_imports_text_and_pages(pdf_path):
    rendered = libreoffice.to_pdf(Path(pdf_path))
    d = pdfium.PdfDocument(rendered)
    assert len(d) == 3
    text = "".join(d[i].get_textpage().get_text_range() for i in range(len(d))).replace(" ", "")
    for needle in ("HelloMortise", "你好世界", "Rotated30", "Pagethree", "合併標題", "一二三四五六七八九十"):
        assert needle in text


# ---------------------------------------------------------------- 真實流水線：與原始 PDF 逐字比對位置


def _src_lines(path: str):
    d = mp.open_document(path)
    pages = []
    for i in range(len(d)):
        r = mp.PageReader(d, i)
        pages.append(lines_of([c for c in r.chars() if c.text.strip() and not c.generated]))
        r.close()
    return pages


def position_errors(src_pdf: str, out_pdf: str) -> dict:
    """逐行配對（同一行文字）後逐字比較原點。回傳統計值（pt）。"""
    a, b = _src_lines(src_pdf), _src_lines(out_pdf)
    dx, dy, matched, total = [], [], 0, 0
    for la, lb in zip(a, b):
        pool = {"".join(c.text for c in ln): ln for ln in lb}
        for ln in la:
            total += 1
            other = pool.get("".join(c.text for c in ln))
            if other is None:
                continue
            matched += 1
            for c1, c2 in zip(ln, other):
                dx.append(abs(c1.origin[0] - c2.origin[0]))
                dy.append(abs(c1.origin[1] - c2.origin[1]))
    q = lambda v, p: sorted(v)[min(len(v) - 1, int(p * len(v)))]  # noqa: E731
    return {"lines": total, "matched": matched, "chars": len(dx), "dx_median": statistics.median(dx), "dx_p95": q(dx, 0.95), "dx_max": max(dx),
            "dy_median": statistics.median(dy), "dy_p95": q(dy, 0.95), "dy_max": max(dy)}


@pytest.fixture(scope="module", params=["slide_basic", "doc_a4"])
def real(request, tmp_path_factory):
    name = request.param
    p = Pipeline(tmp_path_factory.mktemp(f"aipdf-{name}"))
    # 只要求 ai_pdf 輸出：⑦不會為 PPTX 的渲染差異平移文字框，成品文件的位置就是③量到的原值
    p.start(FIXTURES / f"{name}.pdf", name, Intent(outputs=[OutputFormat.AI_PDF]))
    p.run()
    doc = p.store.load("s5/styled_doc", StyledDoc)
    o = ai_pdf.export(doc, name, Config.load(), p.store)
    return name, doc, o, p.store


def test_real_fixture_positions_match_source(real):
    name, doc, o, st = real
    assert o.selfcheck_ok, o.selfcheck_notes
    stats = position_errors(str(FIXTURES / f"{name}.pdf"), str(st.blob_path(o.file)))
    print(f"\n[ai_pdf 位置誤差] {name}: {stats}")
    assert stats["matched"] / stats["lines"] >= 0.85
    # 中位數反映輸出器本身的精度；p95 含成品文件的模型誤差（原檔各行行距不一，成品文件只有一個固定行距）
    assert stats["dy_median"] < 0.1 and stats["dy_p95"] < 1.5
    assert stats["dx_median"] < 0.5
    d = pdfium.PdfDocument(str(st.blob_path(o.file)))
    assert len(d) == len(doc.pages)
