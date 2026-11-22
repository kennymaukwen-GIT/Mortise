"""Word 輸出器（版面固定 docx_fixed、可重排 docx_flow）的測試。

驗證四件事：
  1. 檔案能被重新讀入（python-docx、lxml），結構與自檢合規；
  2. 文字、字型名稱（含東亞欄位）、字級、顏色、位置正確寫入（位置以 EMU 讀回，誤差 0）；
  3. 表格的列欄數、合併儲存格、儲存格文字正確；
  4. 用 LibreOffice 轉成 PDF 後，文字都還在、頁數相符；版面固定模式的文字位置與原 PDF 相比誤差在 2 pt 內。
"""

from __future__ import annotations

import io
import statistics
import zipfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pypdfium2 as pdfium
import pytest
from docx import Document
from docx.table import Table
from lxml import etree
from PIL import Image

from mortise.artifacts import (
    Align, Border, Box, Color, FontChoice, Intent, LineSpacing, LineSpacingMode, StyledCell, StyledDoc, StyledImage,
    StyledPage, StyledParagraph, StyledRun, StyledShape, StyledTable, StyledTextBox, Style, TargetMode,
)
from mortise.artifacts.common import Role, exact
from mortise.config import Config
from mortise.exporters import docx_common, docx_fixed, docx_flow
from mortise.exporters.docx_common import emu, extract_texts, norm_text, q
from mortise.pipeline import Pipeline
from mortise.pipeline.store import Store
from mortise.stages import s6_export
from mortise.artifacts import OutputFormat
from mortise.tools import libreoffice
from mortise.tools.pdfium import PageReader

FIXTURES = Path(__file__).parents[1] / "fixtures"
W = "test@1"
CJK = "WenQuanYi Zen Hei"
LAT = "Liberation Sans"
WLAT, WCJK = "Arial", "Microsoft JhengHei"  # 寫進檔案的名稱：多數裝置都有的字型（見 docx_common「寫入的字型名稱」）
needs_lo = pytest.mark.skipif(not libreoffice.available(), reason="沒有 LibreOffice")


# ---------- 建構測試用的成品文件 ----------


def font(fam: str, ea: bool = False) -> FontChoice:
    return FontChoice(family=exact(fam, W), verification_family=fam, is_substituted=False, east_asian=ea)


def run(text, fam=LAT, size=12.0, ea=False, bold=False, color=(0, 0, 0), alpha=1.0) -> StyledRun:
    return StyledRun(text=text, font=font(fam, ea), size_pt=exact(size, W), bold=bold, color=Color(r=color[0], g=color[1], b=color[2], a=alpha))


def para(pid, runs, role=Role.BODY, style=None, align=Align.LEFT) -> StyledParagraph:
    return StyledParagraph(id=pid, runs=runs, role=role, style_id=style, align=align,
                           line_spacing=exact(LineSpacing(mode=LineSpacingMode.MULTIPLE, value=1.0), W))


def tbox(tid, x, y, w, h, paras, z=1, rot=0.0, wrap=True) -> StyledTextBox:
    return StyledTextBox(id=tid, box=Box(x=x, y=y, w=w, h=h, rotation=rot), z=z, paragraphs=paras, wrap=wrap)


def style(sid, name, fam, size, bold=False, role=Role.BODY, color=(0, 0, 0), usage=1) -> Style:
    return Style(id=sid, name=name, font_family=fam, size_pt=size, bold=bold, italic=False, color=Color(r=color[0], g=color[1], b=color[2]), role=role, usage_count=usage)


def black(w=1.0) -> Border:
    return Border(color=Color(r=0, g=0, b=0), width_pt=w)


def make_table(tid="p001-tbl-1", x=300.0, y=400.0) -> StyledTable:
    m = [2, 4, 2, 4]
    cells = [
        StyledCell(row=0, col=0, col_span=2, paragraphs=[para("c00", [run("合併標題 Header", CJK, 11, True)], style="S2")],
                   fill=Color(r=220, g=230, b=240), header=True, borders=[black()] * 4, margins_pt=m),
        StyledCell(row=0, col=2, paragraphs=[para("c02", [run("Q3", size=11)])], borders=[black(0.5)] * 4, margins_pt=m),
        StyledCell(row=1, col=0, row_span=2, paragraphs=[para("c10", [run("縱向合併", CJK, 11, True)])], v_align="middle", margins_pt=m),
        StyledCell(row=1, col=1, paragraphs=[para("c11", [run("1,250", size=11)], align=Align.RIGHT)], margins_pt=m, value=1250.0),
        StyledCell(row=1, col=2, paragraphs=[para("c12", [run("86", size=11)])], margins_pt=m),
        StyledCell(row=2, col=1, paragraphs=[para("c21", [run("1,480", size=11)])], margins_pt=m),
        StyledCell(row=2, col=2, paragraphs=[para("c22", [run("112", size=11)])], margins_pt=m),
    ]
    return StyledTable(id=tid, box=Box(x=x, y=y, w=240, h=60), z=5, col_widths_pt=[100, 70, 70], row_heights_pt=[20, 20, 20], cells=cells, header_rows=1)


def synth_doc(store: Store) -> StyledDoc:
    """兩頁、不同頁面尺寸；含表格、圓角矩形、自訂路徑、直線、旋轉文字框、圖片、頁碼、半透明。"""
    b = io.BytesIO()
    Image.new("RGB", (40, 20), (200, 30, 30)).save(b, "PNG")
    ref = store.put_blob(b.getvalue(), "image/png", "png")
    p1 = StyledPage(
        page_index=0, width_pt=612, height_pt=792, background_color=Color(r=250, g=250, b=240),
        text_boxes=[
            tbox("p001-box-title", 72, 72, 400, 30, [para("t1", [run("年度報告 ", CJK, 24, True, True, (31, 58, 95)), run("Annual Report", LAT, 24, bold=True, color=(31, 58, 95))], Role.TITLE, "S3")], z=3, wrap=False),
            tbox("p001-box-body", 72, 120, 300, 80, [para("b1", [run("Body text line one ", size=12), run("中文內文", CJK, 12, True)], style="S1"),
                                                     para("b2", [run("Second paragraph here.", size=12.3, alpha=0.5)], style="S1")], z=4),
            tbox("p001-box-rot", 400, 200, 120, 20, [para("r1", [run("Rotated 30", size=14, color=(200, 0, 0))])], z=6, rot=30.0, wrap=False),
            tbox("p001-box-pn", 540, 750, 20, 14, [para("pn", [run("1", size=10)], Role.PAGE_NUMBER, "S4")], z=7, wrap=False),
        ],
        images=[StyledImage(id="p001-img-1", box=Box(x=72, y=300, w=80, h=40), z=2, image=ref)],
        shapes=[
            StyledShape(id="p001-shp-rr", box=Box(x=60, y=60, w=430, h=50), z=1, geometry="rounded_rect", fill=Color(r=230, g=106, b=51, a=0.5), corner_radius_pt=10),
            StyledShape(id="p001-shp-path", box=Box(x=200, y=300, w=60, h=60), z=8, geometry="path", fill=Color(r=0, g=120, b=0), stroke=Color(r=0, g=0, b=0),
                        stroke_width_pt=1, path=[[0, 0, 60], [1, 30, 0], [2, 40, 20, 60, 40, 60, 60], [3]]),
            StyledShape(id="p001-shp-line", box=Box(x=72, y=380, w=200, h=0), z=9, geometry="line", stroke=Color(r=0, g=0, b=255), stroke_width_pt=2),
        ],
        tables=[make_table()],
        reading_order=["p001-box-title", "p001-box-body", "p001-img-1", "p001-tbl-1", "p001-box-rot", "p001-box-pn"])
    p2 = StyledPage(
        page_index=1, width_pt=842, height_pt=595,
        text_boxes=[tbox("p002-box-a", 100, 100, 300, 20, [para("a1", [run("Landscape page 2", size=16)], Role.TITLE, "S3")], z=1, wrap=False),
                    tbox("p002-box-pn", 780, 560, 20, 14, [para("pn2", [run("2", size=10)], Role.PAGE_NUMBER, "S4")], z=2, wrap=False)],
        shapes=[StyledShape(id="p002-shp-red", box=Box(x=400, y=300, w=100, h=100), z=3, geometry="rect", fill=Color(r=255, g=0, b=0)),
                StyledShape(id="p002-shp-blue", box=Box(x=450, y=350, w=100, h=100), z=4, geometry="ellipse", fill=Color(r=0, g=0, b=255))],
        reading_order=["p002-box-a", "p002-box-pn"])
    styles = [style("S1", "內文", LAT, 12, usage=2), style("S2", "內文 2", CJK, 11, bold=True),
              style("S3", "標題", LAT, 24, True, Role.TITLE, (31, 58, 95), 2), style("S4", "頁碼", LAT, 10, role=Role.PAGE_NUMBER, usage=2)]
    return StyledDoc(target_mode=exact(TargetMode.SAME_SOURCE, W), source_app=exact("unknown", W), pages=[p1, p2], styles=styles)


def columns_doc() -> StyledDoc:
    """通欄標題下接兩欄內文；左欄是編號清單，右欄有項目符號；第二頁是超大頁（測縮小）。"""
    left = [para(f"l{i}", [run(f"{i}. ", LAT, 11), run(f"第{i}項 清單內容", CJK, 11, True)], style="S1") for i in (1, 2, 3)]
    left += [para("l4", [run("Left column body text that continues.", LAT, 11)], style="S1")]
    right = [para("r1", [run("• ", LAT, 11), run("項目符號一", CJK, 11, True)], style="S1"),
             para("r2", [run("• ", LAT, 11), run("項目符號二", CJK, 11, True)], style="S1"),
             para("r3", [run("Right column text.", LAT, 11)], style="S1")]
    p1 = StyledPage(page_index=0, width_pt=595, height_pt=842, text_boxes=[
        tbox("p001-box-h", 60, 60, 475, 30, [para("h", [run("雙欄文件 Two Columns", CJK, 22, True, True)], Role.TITLE, "S3")], z=1),
        tbox("p001-box-l", 60, 110, 230, 400, left, z=2),
        tbox("p001-box-r", 305, 110, 230, 400, right, z=3),
        tbox("p001-box-hd", 60, 20, 200, 12, [para("hd", [run("Mortise 測試頁首", CJK, 9, True)], Role.HEADER, "S5")], z=4),
        tbox("p001-box-pn", 287, 800, 20, 12, [para("pn", [run("- 1 -", LAT, 9)], Role.PAGE_NUMBER, "S5")], z=5)],
        reading_order=["p001-box-hd", "p001-box-h", "p001-box-l", "p001-box-r", "p001-box-pn"])
    p2 = StyledPage(page_index=1, width_pt=2000, height_pt=1000, text_boxes=[
        tbox("p002-box-a", 100, 100, 600, 40, [para("a", [run("超大頁 Oversize", CJK, 30, True)], Role.SUBTITLE, "S6")], z=1)],
        reading_order=["p002-box-a"])
    styles = [style("S1", "內文", LAT, 11, usage=7), style("S3", "標題", CJK, 22, True, Role.TITLE),
              style("S5", "頁首", CJK, 9, role=Role.HEADER, usage=2), style("S6", "副標", CJK, 30, True, Role.SUBTITLE)]
    return StyledDoc(target_mode=exact(TargetMode.SAME_SOURCE, W), source_app=exact("unknown", W), pages=[p1, p2], styles=styles)


# ---------- 讀回工具 ----------


def flow_text(sp) -> str:
    """可重排模式寫進檔案的段落文字：清單標記移到編號定義後，文字裡就沒有標記。"""
    t = "".join(r.text for r in sp.runs)
    m = docx_flow.detect_marker(t)
    return t[m.length:] if m else t


def pdf_pages(pdf: bytes) -> list[tuple[tuple[float, float], list]]:
    doc = pdfium.PdfDocument(pdf)
    out = []
    for i in range(len(doc)):
        r = PageReader(doc, i)
        out.append((doc[i].get_size(), [c for c in r.chars() if not c.generated and c.text.strip()]))
        r.close()
    return out


def xml_of(data: bytes, part: str = "word/document.xml"):
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        return etree.fromstring(z.read(part))


def anchor_by_name(root, name):
    for a in root.iter(q("wp:anchor")):
        if a.find(q("wp:docPr")).get("name") == name:
            return a
    raise AssertionError(f"找不到浮動物件 {name}")


def runs_of(container):
    return [(r, "".join(t.text for t in r.iter(q("w:t")))) for r in container.iter(q("w:r")) if r.find(q("w:t")) is not None]


def rfonts_of(r):
    f = r.find(f"{q('w:rPr')}/{q('w:rFonts')}")
    return None if f is None else {k: f.get(q(f"w:{k}")) for k in ("ascii", "hAnsi", "eastAsia", "cs")}


def render_rgb(pdf: bytes, page: int, x: float, y: float, scale: float = 1.0):
    img = pdfium.PdfDocument(pdf)[page].render(scale=scale).to_pil().convert("RGB")
    return img.getpixel((int(x * scale), int(y * scale)))


# ---------- 模組範圍的素材：只產生、只轉檔一次 ----------


@pytest.fixture(scope="module")
def synth(tmp_path_factory):
    store = Store(tmp_path_factory.mktemp("docx-synth"))
    doc, cols = synth_doc(store), columns_doc()
    cfg = Config.load()
    outs = {"fixed": docx_fixed.export(doc, "synth", cfg, store), "flow": docx_flow.export(doc, "synth", cfg, store),
            "cols_flow": docx_flow.export(cols, "cols", cfg, store), "cols_fixed": docx_fixed.export(cols, "cols", cfg, store)}
    cfg2 = Config.load()
    cfg2.data["export"]["docx_fixed"] = {"table_container": "floating", "vml_fallback": True}
    outs["floating"] = docx_fixed.export(doc, "synth", cfg2, store)
    pdfs = {}
    if libreoffice.available():
        with ThreadPoolExecutor(5) as ex:
            pdfs = dict(zip(outs, ex.map(lambda o: libreoffice.to_pdf(store.blob_path(o.file)), outs.values())))
    return {"doc": doc, "cols": cols, "store": store, "out": outs, "pdf": pdfs, "data": {k: store.read_blob(o.file) for k, o in outs.items()}}


@pytest.fixture(scope="module")
def real(tmp_path_factory):
    """四份原生 PDF 跑完整條流水線，輸出兩種 Word，再用 LibreOffice 轉回 PDF。"""
    res = {}
    for name in ("slide_basic", "slide_dark", "doc_a4", "poster"):
        p = Pipeline(tmp_path_factory.mktemp(name))
        p.start(FIXTURES / f"{name}.pdf", f"{name}.pdf", Intent())
        p.run()
        doc = p.store.load("s5/styled_doc", StyledDoc)
        res[name] = {"doc": doc, "store": p.store, "fixed": docx_fixed.export(doc, name, p.cfg if hasattr(p, "cfg") else Config.load(), p.store),
                     "flow": docx_flow.export(doc, name, Config.load(), p.store)}
    if libreoffice.available():
        jobs = [(n, m) for n in res for m in ("fixed", "flow")]
        with ThreadPoolExecutor(6) as ex:
            pdfs = list(ex.map(lambda j: libreoffice.to_pdf(res[j[0]]["store"].blob_path(res[j[0]][j[1]].file)), jobs))
        for (n, m), pdf in zip(jobs, pdfs):
            res[n][f"{m}_pdf"] = pdf
    return res


# ---------- 登記 ----------


def test_registered_in_s6():
    avail = s6_export.available()
    assert OutputFormat.DOCX_FIXED in avail and OutputFormat.DOCX_FLOW in avail


# ---------- 版面固定模式 ----------


def test_fixed_reopens_with_sections_per_page(synth):
    out, data = synth["out"]["fixed"], synth["data"]["fixed"]
    assert out.selfcheck_ok, out.selfcheck_notes
    assert out.filename == "synth.docx" and out.format == OutputFormat.DOCX_FIXED
    d = Document(io.BytesIO(data))
    assert len(d.sections) == 2
    for sec, page in zip(d.sections, synth["doc"].pages):
        assert abs(sec.page_width.pt - page.width_pt) < 0.05 and abs(sec.page_height.pt - page.height_pt) < 0.05
        assert sec.left_margin == sec.right_margin == sec.top_margin == sec.bottom_margin == 0
    assert set(out.fonts_used) == {WLAT, WCJK}


def test_fixed_text_fonts_size_color_position(synth):
    root = xml_of(synth["data"]["fixed"])
    a = anchor_by_name(root, "p001-box-title")
    assert a.get("relativeHeight") and a.get("behindDoc") == "0" and a.get("locked") == "1"
    assert a.find(q("wp:positionH")).get("relativeFrom") == "page" and a.find(q("wp:positionV")).get("relativeFrom") == "page"
    x = int(a.find(f"{q('wp:positionH')}/{q('wp:posOffset')}").text)
    y = int(a.find(f"{q('wp:positionV')}/{q('wp:posOffset')}").text)
    ext = a.find(q("wp:extent"))
    shift = docx_fixed._baseline_shift(synth["doc"].pages[0].text_boxes[0])  # 基線補償（依驗證字型的上緣量度）
    assert abs(shift) < 0.1 * 24
    assert abs(x / 12700 - 72) < 0.5 and abs(y / 12700 - (72 + shift)) < 0.5
    assert int(ext.get("cx")) == emu(400 * 1.15) and int(ext.get("cy")) == emu(30)  # 不換行的框往右加寬 15%（靠左對齊，x 不變）
    assert a.find(f".//{q('wps:bodyPr')}").get("wrap") == "none"  # 單行文字框不換行
    runs = runs_of(a)
    assert [t for _, t in runs] == ["年度報告 ", "Annual Report"]
    (r0, _), (r1, _) = runs
    assert rfonts_of(r0) == {"ascii": WLAT, "hAnsi": WLAT, "eastAsia": WCJK, "cs": WLAT}  # 中文段：東亞欄位寫中文字型
    assert rfonts_of(r1) == {"ascii": WLAT, "hAnsi": WLAT, "eastAsia": WCJK, "cs": WLAT}  # 拉丁段的東亞欄位寫鄰近的中文字型
    for r in (r0, r1):
        rpr = r.find(q("w:rPr"))
        assert rpr.find(q("w:sz")).get(q("w:val")) == "48" and rpr.find(q("w:color")).get(q("w:val")) == "1F3A5F"
        assert rpr.find(q("w:b")) is not None and rpr.find(q("w:b")).get(q("w:val")) is None
    # 旋轉文字框：a:xfrm rot 為六萬分之一度
    rot = anchor_by_name(root, "p001-box-rot")
    assert rot.find(f".//{q('a:xfrm')}").get("rot") == str(30 * 60000)


def test_fixed_layers_shapes_and_images(synth):
    root = xml_of(synth["data"]["fixed"])
    rh = {a.find(q("wp:docPr")).get("name"): (int(a.get("relativeHeight")), a.get("behindDoc")) for a in root.iter(q("wp:anchor"))}
    bg = rh.pop("page-1-background-color")
    assert bg[1] == "1" and all(bg[0] < v[0] for v in rh.values())
    page1 = synth["doc"].pages[0]
    order = sorted([(e.z, e.id) for e in page1.text_boxes + page1.images + page1.shapes + page1.tables])
    assert [i for _, i in order] == sorted((n for n in rh if n.startswith("p001")), key=lambda n: rh[n][0])
    rr = anchor_by_name(root, "p001-shp-rr")
    assert rr.find(f".//{q('a:prstGeom')}").get("prst") == "roundRect"
    assert rr.find(f".//{q('a:gd')}").get("fmla") == "val 20000"  # 10 pt ÷ 短邊 50 pt
    assert rr.find(f".//{q('a:alpha')}").get("val") == "50000"
    path = anchor_by_name(root, "p001-shp-path").find(f".//{q('a:path')}")
    assert [etree.QName(c).localname for c in path] == ["moveTo", "lnTo", "cubicBezTo", "close"]
    assert path.find(f"{q('a:moveTo')}/{q('a:pt')}").get("y") == str(emu(60))
    line = anchor_by_name(root, "p001-shp-line")
    assert line.find(f".//{q('a:prstGeom')}").get("prst") == "line" and line.find(f".//{q('a:ln')}").get("w") == str(emu(2))
    img = anchor_by_name(root, "p001-img-1")
    assert img.find(f".//{q('a:blip')}").get(q("r:embed"))
    assert int(img.find(q("wp:extent")).get("cx")) == emu(80)


def _check_table(tbl: Table):
    assert len(tbl.rows) == 3 and len(tbl.columns) == 3
    assert tbl.cell(0, 0)._tc is tbl.cell(0, 1)._tc  # 水平合併
    assert tbl.cell(1, 0)._tc is tbl.cell(2, 0)._tc and tbl.cell(1, 0)._tc.vMerge == "restart"  # 垂直合併（python-docx 解到起始格）
    assert tbl.rows[2]._tr.tc_lst[0].vMerge == "continue" and tbl.rows[0]._tr.tc_lst[0].grid_span == 2
    assert [[c.text for c in r.cells] for r in tbl.rows] == [
        ["合併標題 Header", "合併標題 Header", "Q3"], ["縱向合併", "1,250", "86"], ["縱向合併", "1,480", "112"]]
    assert tbl.rows[0]._tr.find(f"{q('w:trPr')}/{q('w:tblHeader')}") is not None
    assert tbl.cell(0, 0)._tc.find(f"{q('w:tcPr')}/{q('w:shd')}").get(q("w:fill")) == "DCE6F0"
    assert [int(g.get(q("w:w"))) for g in tbl._tbl.find(q("w:tblGrid"))] == [2000, 1400, 1400]


@pytest.mark.parametrize("mode", ["fixed", "floating", "flow"])
def test_table_structure(synth, mode):
    d = Document(io.BytesIO(synth["data"][mode]))
    if mode == "fixed":  # 表格放在文字方塊裡
        tbl_el = d.element.body.find(f".//{q('w:txbxContent')}/{q('w:tbl')}")
        assert tbl_el is not None
        tbl = Table(tbl_el, d._body)
    else:
        assert len(d.tables) == 1
        tbl = d.tables[0]
    _check_table(tbl)
    tpr = tbl._tbl.find(q("w:tblPr"))
    if mode == "floating":
        p = tpr.find(q("w:tblpPr"))
        assert p.get(q("w:horzAnchor")) == "page" and int(p.get(q("w:tblpX"))) == 6000 and int(p.get(q("w:tblpY"))) == 8000
    hrule = tbl.rows[0]._tr.find(f"{q('w:trPr')}/{q('w:trHeight')}").get(q("w:hRule"))
    assert hrule == ("atLeast" if mode == "flow" else "exact")


def test_fixed_degradations_and_selfcheck(synth):
    out = synth["out"]["fixed"]
    concepts = {(d.element_id, d.concept): d.lossy for d in out.degradations}
    assert concepts[("p001-box-body", "文字透明度")] is True
    assert concepts[("p001-box-body", "字級")] is True  # 12.3 pt 量化為 12.5 pt
    assert concepts[("p001-tbl-1", "儲存格數值、數字格式、公式")] is True
    assert ("document", "閱讀順序與文字流") in concepts
    assert out.selfcheck_ok and synth["out"]["floating"].selfcheck_ok


def test_fixed_oversize_page_is_scaled(synth):
    out, data = synth["out"]["cols_fixed"], synth["data"]["cols_fixed"]
    d = Document(io.BytesIO(data))
    assert d.sections[1].page_width.pt <= 1584.01 and abs(d.sections[1].page_width.pt / d.sections[1].page_height.pt - 2.0) < 0.01
    assert any(x.concept == "頁面尺寸" and x.lossy for x in out.degradations)
    a = anchor_by_name(xml_of(data), "p002-box-a")
    assert abs(int(a.find(f"{q('wp:positionH')}/{q('wp:posOffset')}").text) / 12700 - 100 * 0.792) < 0.5


@needs_lo
@pytest.mark.parametrize("mode", ["fixed", "floating"])
def test_fixed_libreoffice_render(synth, mode):
    """LibreOffice 轉 PDF：頁數、頁面尺寸、全部文字都在（VML 退路不重複），文字框位置誤差在 2 pt 內。"""
    pages = pdf_pages(synth["pdf"][mode])
    doc = synth["doc"]
    assert len(pages) == len(doc.pages)
    for (size, chars), page in zip(pages, doc.pages):
        assert abs(size[0] - page.width_pt) < 0.5 and abs(size[1] - page.height_pt) < 0.5
        got = norm_text("".join(c.text for c in chars))
        texts = ["".join(r.text for r in sp.runs) for tb in page.text_boxes for sp in tb.paragraphs]
        texts += ["".join(r.text for r in sp.runs) for t in page.tables for c in t.cells for sp in c.paragraphs]
        for t in texts:
            n = norm_text(t)
            assert (got.count(n) == 1) if len(n) >= 4 else (n in got), t
    # 位置：每個不旋轉文字框第一個字的基線起點 x 與框左緣、第一行上緣（基線 − 最大上緣）與框上緣
    (size, chars), page = pages[0], doc.pages[0]
    errs = []
    for tb in page.text_boxes:
        if tb.box.rotation:
            continue
        first = "".join(r.text for r in tb.paragraphs[0].runs).strip()[0]
        c0 = min((c for c in chars if c.text == first), key=lambda c: abs(c.origin[0] - tb.box.x) + abs(size[1] - c.origin[1] - tb.box.y))
        line = [c for c in chars if abs(c.origin[1] - c0.origin[1]) < 0.1]
        errs.append(abs(c0.origin[0] - tb.box.x))
        if all(not r.font.east_asian for r in tb.paragraphs[0].runs):  # 中文字型的上緣量度兩邊定義不同，只比拉丁行
            errs.append(abs(min(size[1] - c.origin[1] - c.ascent for c in line) - tb.box.y))
    assert max(errs) < 2.0, errs
    # 表格儲存格文字：左緣 = 表格 x + 儲存格左邊距
    c = next(c for c in chars if c.text == "Q")
    assert abs(c.origin[0] - (300 + 170 + 4)) < 2.0


@needs_lo
def test_fixed_libreoffice_layers_and_background(synth):
    pdf = synth["pdf"]["fixed"]
    assert render_rgb(pdf, 0, 5, 5) == (250, 250, 240)  # 整頁底色在最底層
    assert render_rgb(pdf, 1, 420, 320)[0] > 200  # 紅色矩形（z=3）
    r, g, b = render_rgb(pdf, 1, 475, 375)  # 重疊處：藍色橢圓（z=4）在上
    assert b > 200 and r < 60


@needs_lo
def test_fixed_rotated_textbox_known_libreoffice_limit(synth):
    """已知限制：LibreOffice 24.2 不旋轉 wps 文字方塊裡的字，放在旋轉後外接框的左上角。
    Word 依 a:xfrm rot 旋轉（規格行為）。這裡記錄 LibreOffice 的實際行為，行為改變時這個測試會提醒。"""
    (size, chars) = pdf_pages(synth["pdf"]["fixed"])[0]
    c = next(c for c in chars if c.text == "R" and abs(c.size - 14) < 0.5)
    assert abs(c.angle) < 0.01 and abs(c.origin[0] - 403.0) < 3.0


# ---------- 可重排模式 ----------


def test_flow_styles_and_direct_formatting(synth):
    out, data = synth["out"]["flow"], synth["data"]["flow"]
    assert out.selfcheck_ok, out.selfcheck_notes
    d = Document(io.BytesIO(data))
    paras = [p for p in d.paragraphs if p.text]
    assert [p.text for p in paras[:3]] == ["年度報告 Annual Report", "Body text line one 中文內文", "Second paragraph here."]
    title, body = paras[0], paras[1]
    assert title.style.name == "Heading 1"  # 內建「標題 1」
    h1 = d.styles["Heading 1"].element
    assert h1.find(f"{q('w:pPr')}/{q('w:outlineLvl')}").get(q("w:val")) == "0"
    f = h1.find(f"{q('w:rPr')}/{q('w:rFonts')}")
    assert (f.get(q("w:ascii")), f.get(q("w:eastAsia"))) == (WLAT, WCJK)
    assert h1.find(f"{q('w:rPr')}/{q('w:sz')}").get(q("w:val")) == "48"
    assert body.style.name == "內文"
    # 與樣式相同的屬性不寫直接格式；中文段只寫字型
    assert body.runs[0]._r.find(q("w:rPr")) is None
    assert body.runs[1]._r.find(q("w:rPr")) is None  # 中文段的字型欄位也與樣式相同（樣式的 eastAsia 是中文字型）
    s1 = d.styles["內文"].element.find(f"{q('w:rPr')}/{q('w:rFonts')}")
    assert (s1.get(q("w:ascii")), s1.get(q("w:hAnsi")), s1.get(q("w:eastAsia"))) == (WLAT, WLAT, WCJK)
    sec = d.sections[0]
    assert (round(sec.page_width.pt), round(sec.page_height.pt)) == (612, 792)  # 取第一頁
    assert round(sec.left_margin.pt) == 72 and round(sec.top_margin.pt) == 72
    pic = d.inline_shapes[0]
    assert abs(pic.width.pt - 80) < 0.1 and abs(pic.height.pt - 40) < 0.1
    concepts = {(x.element_id, x.concept) for x in out.degradations}
    assert ("p001-shp-path", "形狀 path") in concepts and ("p001-box-rot", "旋轉") in concepts and ("page-2", "各頁尺寸不同") in concepts


def test_flow_page_number_field_in_footer(synth):
    d = Document(io.BytesIO(synth["data"]["flow"]))
    ftr = d.sections[0].footer._element
    assert " PAGE " in "".join(t.text for t in ftr.iter(q("w:instrText")))
    assert ftr.find(f".//{q('w:jc')}").get(q("w:val")) == "right"
    body_text = "".join(p.text for p in d.paragraphs)
    assert "Landscape page 2" in body_text and not any(p.text.strip() in ("1", "2") for p in d.paragraphs)


def test_flow_lists_columns_header(synth):
    out, data = synth["out"]["cols_flow"], synth["data"]["cols_flow"]
    assert out.selfcheck_ok, out.selfcheck_notes
    d = Document(io.BytesIO(data))
    texts = [p.text for p in d.paragraphs if p.text]
    assert "第1項 清單內容" in texts and not any(t.startswith("1.") for t in texts)  # 標記移到編號定義
    num = xml_of(data, "word/numbering.xml")
    lvls = {a.find(f"{q('w:lvl')}/{q('w:numFmt')}").get(q("w:val")): a.find(f"{q('w:lvl')}/{q('w:lvlText')}").get(q("w:val"))
            for a in num.iter(q("w:abstractNum")) if a.find(f"{q('w:lvl')}/{q('w:lvlText')}") is not None}
    assert lvls.get("decimal") == "%1." and lvls.get("bullet") == "•"
    numbered = [p for p in d.paragraphs if p._p.find(f"{q('w:pPr')}/{q('w:numPr')}") is not None]
    assert len(numbered) == 5
    cols = [s.find(q("w:cols")) for s in d.element.body.iter(q("w:sectPr"))]
    assert any(c.get(q("w:num")) == "2" for c in cols)
    assert any(c.get(q("w:val")) == "continuous" for s in d.element.body.iter(q("w:sectPr")) for c in s.iter(q("w:type")))
    assert "Mortise 測試頁首" in "".join(p.text for p in d.sections[0].header.paragraphs)
    assert any(x.concept == "各頁尺寸不同" and x.lossy for x in out.degradations)
    # 副標題給第二層大綱
    sub_p = next(p for p in d.paragraphs if p.text.startswith("超大頁"))
    assert sub_p.style.name == "Heading 2"


@needs_lo
def test_flow_libreoffice_render(synth):
    for key, doc in (("flow", synth["doc"]), ("cols_flow", synth["cols"])):
        pages = pdf_pages(synth["pdf"][key])
        assert len(pages) == len(doc.pages), key
        got = norm_text("".join(c.text for _, cs in pages for c in cs))
        for page in doc.pages:
            for tb in page.text_boxes:
                for sp in tb.paragraphs:
                    if sp.role == Role.PAGE_NUMBER:
                        continue
                    assert norm_text(flow_text(sp)) in got, sp.id
    # 分欄：右欄的字在左欄右邊、和左欄同一個高度帶
    (size, chars) = pdf_pages(synth["pdf"]["cols_flow"])[0]
    lc = next(c for c in chars if c.text == "L")
    rc = next(c for c in chars if c.text == "R")
    assert rc.origin[0] > 290 > lc.origin[0] and abs(rc.origin[1] - lc.origin[1]) < 200
    # 頁碼：功能變數顯示每頁的頁碼
    assert "-1-" in norm_text("".join(c.text for c in chars))


# ---------- 真實素材（完整流水線） ----------


@pytest.mark.parametrize("name", ["slide_basic", "slide_dark", "doc_a4", "poster"])
def test_real_fixtures_selfcheck(real, name):
    r = real[name]
    for mode in ("fixed", "flow"):
        out = r[mode]
        assert out.selfcheck_ok, (mode, out.selfcheck_notes)
        written = {docx_common.make_ctx(r["doc"]).font_name(run.font, record=False)
                   for p in r["doc"].pages for t in p.text_boxes for sp in t.paragraphs for run in sp.runs}
        assert written <= set(out.fonts_used)
        data = r["store"].read_blob(out.file)
        allt = norm_text("".join(extract_texts(data).values()))
        for p in r["doc"].pages:
            for tb in p.text_boxes:
                for sp in tb.paragraphs:
                    t = norm_text(flow_text(sp) if mode == "flow" else "".join(x.text for x in sp.runs))
                    if mode == "flow" and sp.role == Role.PAGE_NUMBER:
                        continue
                    assert t in allt, (mode, t)


def _position_errors(src_pdf: Path, out_pdf: bytes, doc: StyledDoc, first_only: bool = True, aligns=(Align.LEFT, Align.JUSTIFY)) -> list[float]:
    """原 PDF 與 LibreOffice 渲染結果中，同一段落第一個字的基線起點距離（pt，取 x、y 較大者）。
    first_only：只量每個文字框的第一段（文字框位置）；否則每段都量（含行距推論造成的累積誤差）。
    aligns：只量這些對齊方式的段落。置中、靠右的字起點取決於整行寬度；成品文件沒有字距欄位，
    來源與目標引擎的行寬差多少，起點就差一半或全部（slide_dark 的置中標題行寬差 10.8 pt），那不是框的位置誤差。
    以段落開頭 4 個非空白字元在兩邊各只出現一次為準，避免配錯。"""
    src, got = pdf_pages(src_pdf.read_bytes()), pdf_pages(out_pdf)
    errs = []
    for page, (ssize, schars), (gsize, gchars) in zip(doc.pages, src, got):
        stext = "".join(c.text for c in schars)
        gtext = "".join(c.text for c in gchars)
        for tb in page.text_boxes:
            if tb.box.rotation:
                continue
            for sp in tb.paragraphs[:1] if first_only else tb.paragraphs:
                if sp.align not in aligns:
                    continue
                key = norm_text("".join(r.text for r in sp.runs))[:4]
                if len(key) < 2 or stext.count(key) != 1 or gtext.count(key) != 1:
                    continue
                a, b = schars[stext.index(key)], gchars[gtext.index(key)]
                errs.append(max(abs(a.origin[0] - b.origin[0]), abs((ssize[1] - a.origin[1]) - (gsize[1] - b.origin[1]))))
    return errs


@needs_lo
def test_real_fixtures_libreoffice(real):
    """兩種模式：頁數相符、文字都在；版面固定模式的文字位置與原 PDF 相比誤差在 2 pt 內。"""
    all_errs = {}
    for name, r in real.items():
        doc = r["doc"]
        for mode in ("fixed", "flow"):
            pages = pdf_pages(r[f"{mode}_pdf"])
            assert len(pages) == len(doc.pages), (name, mode)
            got = norm_text("".join(c.text for _, cs in pages for c in cs))
            for p in doc.pages:
                for tb in p.text_boxes:
                    for sp in tb.paragraphs:
                        t = norm_text(flow_text(sp) if mode == "flow" else "".join(x.text for x in sp.runs))
                        assert t in got or (mode == "flow" and sp.role == Role.PAGE_NUMBER), (name, mode, t)
        all_errs[name] = _position_errors(FIXTURES / f"{name}.pdf", r["fixed_pdf"], doc)
        para_errs = _position_errors(FIXTURES / f"{name}.pdf", r["fixed_pdf"], doc, first_only=False, aligns=tuple(Align))
        print(f"\n{name} 每段、各種對齊（含行距累積與行寬差）：n={len(para_errs)} 中位數 {statistics.median(para_errs):.2f} 最大 {max(para_errs):.2f}")
    flat = [e for v in all_errs.values() for e in v]
    print("\n文字框位置誤差（pt）（數量, 中位數, 最大）：", {k: (len(v), round(statistics.median(v), 2) if v else None, round(max(v), 2) if v else None) for k, v in all_errs.items()})
    print(f"全部：n={len(flat)} 中位數 {statistics.median(flat):.2f} 最大 {max(flat):.2f}")
    assert len(flat) >= 10
    assert max(flat) < 2.0, all_errs
