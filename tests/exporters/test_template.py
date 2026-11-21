"""模板輸出器的測試：佔位文字（同長度、保留段落數與樣式）、圖片佔位框、拿掉底圖、表格清空、
複雜路徑換成佔位框、不外流原作內容，以及 LibreOffice 轉 PDF 後的文字與頁數。"""

from __future__ import annotations

import io
from pathlib import Path

import pypdfium2 as pdfium
import pytest
from lxml import etree
from PIL import Image
from pptx import Presentation
from pptx.enum.shapes import MSO_SHAPE_TYPE
from pptx.enum.text import PP_ALIGN
from pptx.util import Emu

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
from mortise.exporters import template
from mortise.pipeline import Pipeline
from mortise.pipeline.store import Store
from mortise.tools import libreoffice

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"
W = "test@1"
A = "http://schemas.openxmlformats.org/drawingml/2006/main"


def run(text, family="Calibri", size=18.0, bold=False, color=(0, 0, 0), ea=False):
    font = FontChoice(family=measured(family, 0.9, Provenance.INFERRED, W), verification_family=family, is_substituted=False, east_asian=ea)
    return StyledRun(text=text, font=font, size_pt=measured(float(size), 0.9, Provenance.MEASURED, W), bold=bold, color=Color(r=color[0], g=color[1], b=color[2]))


def png_blob(store, color, size=(64, 48)):
    buf = io.BytesIO()
    Image.new("RGB", size, color).save(buf, "PNG")
    return store.put_blob(buf.getvalue(), "image/png", "png")


def star_path(n: int) -> list[list[float]]:
    import math

    pts = [[0, 50 + 50 * math.cos(2 * math.pi * i / n), 50 + 50 * math.sin(2 * math.pi * i / n)] for i in range(n)]
    return [[0.0] + pts[0][1:]] + [[1.0] + p[1:] for p in pts[1:]] + [[3.0]]


ORIGINAL_TEXTS = ["季度報告", "Quarterly Review 2026", "營收成長", "Revenue grew", "旋轉標籤", "表頭", "Cell value"]


def build_doc(store: Store) -> StyledDoc:
    title = StyledTextBox(
        id="p001-box-title",
        box=Box(x=60.25, y=40.5, w=600, h=90),
        z=5,
        paragraphs=[
            StyledParagraph(id="t1", runs=[run("季度報告", "Microsoft JhengHei", 40, bold=True, color=(31, 58, 95), ea=True), run(" Quarterly Review 2026", "Calibri", 40, bold=True, color=(31, 58, 95))], align=Align.CENTER),
            StyledParagraph(id="t2", runs=[run("營收成長", "PMingLiU", 20, ea=True), run(" Revenue grew", "Cambria", 20, color=(200, 0, 0))], align=Align.RIGHT, space_after_pt=6),
        ],
    )
    rotated = StyledTextBox(id="p001-box-rot", box=Box(x=700, y=300, w=120, h=30, rotation=15.0), z=6, paragraphs=[StyledParagraph(id="r1", runs=[run("旋轉標籤", "Microsoft JhengHei", 14, ea=True)])], wrap=False)
    photo = StyledImage(id="p001-img-photo", box=Box(x=60, y=160, w=320, h=240), z=2, image=png_blob(store, (10, 120, 200)))
    tilted = StyledImage(id="p001-img-tilt", box=Box(x=450, y=180, w=160, h=100, rotation=30.0), z=3, image=png_blob(store, (250, 200, 0)))
    rrect = StyledShape(id="p001-shape-rr", box=Box(x=40, y=420, w=200, h=60), z=1, geometry="rounded_rect", fill=Color(r=230, g=240, b=250), corner_radius_pt=10)
    tri = StyledShape(id="p001-shape-tri", box=Box(x=300, y=420, w=60, h=60), z=1, geometry="path", fill=Color(r=0, g=150, b=0), path=[[0, 30, 0], [1, 60, 60], [1, 0, 60], [3]])
    logo = StyledShape(id="p001-shape-logo", box=Box(x=820, y=40, w=100, h=100), z=4, geometry="path", fill=Color(r=200, g=0, b=0), path=star_path(30))
    cells = [
        StyledCell(row=0, col=0, col_span=2, header=True, paragraphs=[StyledParagraph(id="c00", runs=[run("表頭", "Microsoft JhengHei", 12, bold=True, ea=True)], align=Align.CENTER)], fill=Color(r=31, g=58, b=95), borders=[Border(color=Color(r=0, g=0, b=0), width_pt=1.0)] * 4),
        StyledCell(row=1, col=0, paragraphs=[StyledParagraph(id="c10", runs=[run("Cell value", size=10)])]),
        StyledCell(row=1, col=1, paragraphs=[StyledParagraph(id="c11", runs=[run("1,234", size=10)], align=Align.RIGHT)], value=1234.0, number_format="#,##0"),
    ]
    table = StyledTable(id="p001-table-t", box=Box(x=450, y=420, w=300, h=60), z=7, col_widths_pt=[150.0, 150.0], row_heights_pt=[30.0, 30.0], cells=cells, header_rows=1)
    page1 = StyledPage(
        page_index=0,
        width_pt=960,
        height_pt=540,
        background_image=png_blob(store, (200, 100, 50), (96, 54)),
        text_boxes=[title, rotated],
        images=[photo, tilted],
        shapes=[rrect, tri, logo],
        tables=[table],
        reading_order=["p001-box-title", "p001-img-photo", "p001-table-t"],
    )
    body = StyledTextBox(id="p002-box-body", box=Box(x=60, y=60, w=400, h=200), z=1, paragraphs=[StyledParagraph(id="b1", runs=[run("Second page text", "Arial", 16)]), StyledParagraph(id="b2", runs=[run("   ", "Arial", 16)])])
    page2 = StyledPage(page_index=1, width_pt=960, height_pt=540, background_color=Color(r=20, g=20, b=40), background_image=png_blob(store, (255, 255, 255), (8, 8)), text_boxes=[body])
    return StyledDoc(target_mode=measured(TargetMode.CROSS_SOURCE, 1.0, Provenance.INFERRED, W), source_app=measured("unknown", 1.0, Provenance.INFERRED, W), pages=[page1, page2])


@pytest.fixture(scope="module")
def cfg():
    return Config.load()


@pytest.fixture(scope="module")
def result(tmp_path_factory, cfg):
    store = Store(tmp_path_factory.mktemp("template-store"))
    doc = build_doc(store)
    out = template.export(doc, "deck", cfg, store)
    data = store.read_blob(out.file)
    return doc, out, data, Presentation(io.BytesIO(data)), store


def shapes_by_name(slide):
    return {sh.name: sh for sh in slide.shapes}


def pt(emu) -> float:
    return Emu(emu).pt


def assert_box(sh, box: Box, tol=0.5):
    assert abs(pt(sh.left) - box.x) <= tol and abs(pt(sh.top) - box.y) <= tol
    assert abs(pt(sh.width) - box.w) <= tol and abs(pt(sh.height) - box.h) <= tol


def run_fonts(r) -> dict[str, str]:
    rpr = r._r.find(f"{{{A}}}rPr")
    return {tag: rpr.find(f"{{{A}}}{tag}").get("typeface") for tag in ("latin", "ea", "cs") if rpr.find(f"{{{A}}}{tag}") is not None}


def width_units(s: str) -> int:
    return sum(2 if template._WIDE.match(ch) else 1 for ch in s)


# ---------------------------------------------------------------- 佔位文字


def test_filler_lengths_and_whitespace():
    f = template.Filler(template.DEFAULT_PARAMS)
    zh = f.line("季度報告總結", True)
    assert zh == "範例文字範例" and len(zh) == 6
    f.reset()
    en = f.line("Quarterly Review", False)
    assert en.startswith("Lorem") and abs(len(en) - len("Quarterly Review")) <= 1 and not en.endswith(" ")
    f.reset()
    assert f.line("  Hello  ", False).startswith("  ") and f.line("  Hello  ", False).endswith("  ")
    assert f.line("   ", False) == "   "
    f.reset()
    assert f.line("Revenue grew", False) == "Lorem ipsum"  # 截在字詞邊界，不以空白結尾
    assert f.text("第一行\n第二行", True).count("\n") == 1


def test_reopens_with_same_pages_and_metadata(result):
    doc, out, _, prs, _ = result
    assert out.format == OutputFormat.TEMPLATE and out.filename == "deck_template.pptx"
    assert out.selfcheck_ok, out.selfcheck_notes
    assert len(prs.slides) == len(doc.pages)
    assert abs(pt(prs.slide_width) - 960) <= 0.5 and abs(pt(prs.slide_height) - 540) <= 0.5


def test_text_replaced_keeping_style_and_position(result):
    doc, out, _, prs, _ = result
    by = shapes_by_name(prs.slides[0])
    src = doc.pages[0].text_boxes[0]
    sh = by[src.id]
    assert_box(sh, src.box)
    paras = sh.text_frame.paragraphs
    assert len(paras) == len(src.paragraphs)
    for p_out, p_src in zip(paras, src.paragraphs):
        assert p_out.alignment == {Align.LEFT: PP_ALIGN.LEFT, Align.CENTER: PP_ALIGN.CENTER, Align.RIGHT: PP_ALIGN.RIGHT}[p_src.align]
        runs = [r for r in p_out.runs]
        assert len(runs) == len(p_src.runs)
        for r_out, r_src in zip(runs, p_src.runs):
            assert r_out.text != r_src.text
            assert abs(width_units(r_out.text) - width_units(r_src.text)) <= 2, (r_out.text, r_src.text)
            assert run_fonts(r_out) == {"latin": r_src.font.family.value, "ea": r_src.font.family.value, "cs": r_src.font.family.value}
            assert r_out.font.size.pt == r_src.size_pt.value
            assert str(r_out.font.color.rgb) == r_src.color.hex
            assert bool(r_out.font.bold) == r_src.bold
    assert paras[0].runs[0].text == "範例文字" and paras[0].runs[1].text.startswith(" Lorem")
    rot = by["p001-box-rot"]
    assert abs(rot.rotation - 15.0) < 0.01 and rot.text_frame.text == "範例文字"
    allowed = template.allowed_placeholder_chars(template.DEFAULT_PARAMS)
    for slide in prs.slides:
        for s in slide.shapes:
            if s.has_text_frame:
                assert set(s.text_frame.text) <= allowed
                for original in ORIGINAL_TEXTS:
                    assert original not in s.text_frame.text


def test_images_become_gray_placeholders(result):
    doc, out, data, prs, _ = result
    for slide in prs.slides:
        assert not [s for s in slide.shapes if s.shape_type == MSO_SHAPE_TYPE.PICTURE]
    assert b"<a:blip" not in data and b"media/image" not in _zip_names(data)
    by = shapes_by_name(prs.slides[0])
    photo = doc.pages[0].images[0]
    rect = by[photo.id]
    assert_box(rect, photo.box)
    assert str(rect.fill.fore_color.rgb) == "D9D9D9"
    d1, d2 = by[f"{photo.id}-diag1"], by[f"{photo.id}-diag2"]
    ends = sorted([(pt(d.begin_x), pt(d.begin_y), pt(d.end_x), pt(d.end_y)) for d in (d1, d2)])
    b = photo.box
    corners = {(b.x, b.y), (b.x + b.w, b.y), (b.x, b.y + b.h), (b.x + b.w, b.y + b.h)}
    for e in ends:
        for q in ((e[0], e[1]), (e[2], e[3])):
            assert min(abs(q[0] - c[0]) + abs(q[1] - c[1]) for c in corners) <= 0.5
    label = by[f"{photo.id}-label"]
    assert label.text_frame.text == "圖片"
    assert abs(pt(label.left) + pt(label.width) / 2 - (b.x + b.w / 2)) <= 0.5
    assert abs(pt(label.top) + pt(label.height) / 2 - (b.y + b.h / 2)) <= 0.5
    # 疊放順序：框 → 對角線 → 字樣
    names = [s.name for s in prs.slides[0].shapes]
    assert names.index(photo.id) < names.index(f"{photo.id}-diag1") < names.index(f"{photo.id}-label")
    tilt = by["p001-img-tilt"]
    assert abs(tilt.rotation - 30.0) < 0.01 and abs(by["p001-img-tilt-label"].rotation - 30.0) < 0.01
    assert sum(1 for d in out.degradations if d.concept == "圖片") == 2


def _zip_names(data: bytes) -> bytes:
    import zipfile

    return "\n".join(zipfile.ZipFile(io.BytesIO(data)).namelist()).encode()


def test_background_image_removed_color_kept(result):
    _, out, data, prs, _ = result
    bg1 = prs.slides[0].background.fill
    assert bg1.type is not None and str(bg1.fore_color.rgb) == "C86432"  # 底圖平均色 (200,100,50)
    assert str(prs.slides[1].background.fill.fore_color.rgb) == "141428"  # 原本的底色
    assert sum(1 for d in out.degradations if d.concept == "整頁底圖") == 2


def test_table_keeps_structure_and_clears_content(result, cfg):
    doc, out, _, prs, store = result
    copy, _ = template.placeholder_doc(doc, store, template.params(cfg))
    t = copy.pages[0].tables[0]
    assert (t.col_widths_pt, t.row_heights_pt, t.header_rows) == ([150.0, 150.0], [30.0, 30.0], 1)
    assert [(c.row, c.col, c.row_span, c.col_span) for c in t.cells] == [(0, 0, 1, 2), (1, 0, 1, 1), (1, 1, 1, 1)]
    assert all(r.text == "" for c in t.cells for p in c.paragraphs for r in p.runs)
    assert all(c.value is None and c.formula is None and c.number_format is None for c in t.cells)
    assert t.cells[0].fill == doc.pages[0].tables[0].cells[0].fill
    frame = shapes_by_name(prs.slides[0]).get(t.id)
    if frame is not None and frame.has_table:  # PPTX 寫出器支援表格：檢查列欄、合併、清空
        tbl = frame.table
        assert (len(tbl.rows), len(tbl.columns)) == (2, 2)
        assert tbl.cell(0, 0).is_merge_origin and tbl.cell(0, 0).span_width == 2
        assert all(tbl.cell(r, c).text == "" for r in range(2) for c in range(2))
    else:  # 不支援時必須記降級，不可默默丟掉
        assert any(d.element_id == t.id and d.concept == "表格" for d in out.degradations)


def test_shapes_simple_kept_complex_replaced(result):
    doc, out, _, prs, _ = result
    by = shapes_by_name(prs.slides[0])
    for sid in ("p001-shape-rr", "p001-shape-tri"):
        src = next(s for s in doc.pages[0].shapes if s.id == sid)
        assert_box(by[sid], src.box)
    logo = by["p001-shape-logo"]
    assert str(logo.fill.fore_color.rgb) == "D9D9D9" and "p001-shape-logo-diag1" in by
    assert any(d.element_id == "p001-shape-logo" and "複雜路徑" in d.concept for d in out.degradations)
    xml_rr = etree.tostring(by["p001-shape-rr"]._element)
    if b'prst="roundRect"' not in xml_rr:
        assert any(d.element_id == "p001-shape-rr" and d.concept == "圓角矩形" for d in out.degradations)


def test_fonts_used_are_inferred_names(result):
    doc, out, *_ = result
    inferred = {r.font.family.value for pg in doc.pages for tb in pg.text_boxes for p in tb.paragraphs for r in p.runs}
    assert inferred <= set(out.fonts_used)


def test_libreoffice_render(result, tmp_path):
    doc, _, data, _, _ = result
    src = tmp_path / "deck_template.pptx"
    src.write_bytes(data)
    pdf = pdfium.PdfDocument(libreoffice.to_pdf(src))
    assert len(pdf) == len(doc.pages)
    text = "".join(pdf[i].get_textpage().get_text_range() for i in range(len(pdf)))
    assert "範例文字" in text and "圖片" in text and "Lorem" in text
    for original in ORIGINAL_TEXTS:
        assert original not in text


# ---------------------------------------------------------------- 真實流水線


@pytest.fixture(scope="module")
def real(tmp_path_factory, cfg):
    p = Pipeline(tmp_path_factory.mktemp("template-job"))
    p.start(FIXTURES / "slide_basic.pdf", "slide_basic.pdf", Intent())
    p.run()
    doc = p.store.load("s5/styled_doc", StyledDoc)
    out = template.export(doc, "slide_basic", cfg, p.store)
    return doc, out, p.store.read_blob(out.file)


def test_real_pipeline_slide(real):
    doc, out, data = real
    assert out.selfcheck_ok, out.selfcheck_notes
    prs = Presentation(io.BytesIO(data))
    assert len(prs.slides) == len(doc.pages)
    originals = ["".join(r.text for r in p.runs) for pg in doc.pages for tb in pg.text_boxes for p in tb.paragraphs]
    allowed = template.allowed_placeholder_chars(template.DEFAULT_PARAMS)
    for slide, page in zip(prs.slides, doc.pages):
        assert not [s for s in slide.shapes if s.shape_type == MSO_SHAPE_TYPE.PICTURE]
        by = shapes_by_name(slide)
        for tb in page.text_boxes:
            sh = by[tb.id]
            assert_box(sh, tb.box)
            assert len(sh.text_frame.paragraphs) == len(tb.paragraphs)
            assert set(sh.text_frame.text) <= allowed
        for im in page.images:
            assert_box(by[im.id], im.box)
    all_text = " ".join(s.text_frame.text for sl in prs.slides for s in sl.shapes if s.has_text_frame)
    assert not any(o.strip() and len(o.strip()) >= 3 and o in all_text for o in originals)
    inferred = {r.font.family.value for pg in doc.pages for tb in pg.text_boxes for p in tb.paragraphs for r in p.runs}
    assert inferred <= set(out.fonts_used)
