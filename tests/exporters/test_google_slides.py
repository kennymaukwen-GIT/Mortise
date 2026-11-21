"""Google 簡報輸出器的測試：字型換成 Google 字型庫的字型（拉丁、東亞、複雜文字三欄）、
字級顏色位置不變、複雜路徑點陣化、檔案大小預算、降級紀錄、自檢提醒，以及 LibreOffice 轉 PDF。"""

from __future__ import annotations

import io
from pathlib import Path

import pypdfium2 as pdfium
import pytest
from PIL import Image
from pptx import Presentation
from pptx.enum.shapes import MSO_SHAPE_TYPE
from pptx.util import Emu

from mortise.artifacts import (
    Align,
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
from mortise.exporters import google_slides as gs
from mortise.pipeline import Pipeline
from mortise.pipeline.store import Store
from mortise.tools import libreoffice

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"
W = "test@1"
A = "http://schemas.openxmlformats.org/drawingml/2006/main"
P = gs.DEFAULT_PARAMS


def run(text, family, size=18.0, color=(0, 0, 0), ea=False):
    font = FontChoice(family=measured(family, 0.9, Provenance.INFERRED, W), verification_family=family, is_substituted=False, east_asian=ea)
    return StyledRun(text=text, font=font, size_pt=measured(float(size), 0.9, Provenance.MEASURED, W), color=Color(r=color[0], g=color[1], b=color[2]))


def ring_path(n: int, r_out=50.0, r_in=0.0) -> list[list[float]]:
    """n 邊形（可帶同心內圈當洞），座標相對於 100×100 的框。"""
    import math

    def poly(r):
        pts = [(50 + r * math.cos(2 * math.pi * i / n), 50 + r * math.sin(2 * math.pi * i / n)) for i in range(n)]
        return [[0.0, *pts[0]]] + [[1.0, *p] for p in pts[1:]] + [[3.0]]

    return poly(r_out) + (poly(r_in) if r_in else [])


EXPECTED = {  # (推論字型, 是否東亞文字) → Google 字型
    ("Calibri", False): "Carlito",
    ("Cambria", False): "Caladea",
    ("Arial", False): "Arimo",
    ("Times New Roman", False): "Tinos",
    ("Courier New", False): "Cousine",
    ("微軟正黑體", True): "Noto Sans TC",
    ("PMingLiU", True): "Noto Serif TC",
    ("Futura", False): "Arimo",
}


def build_doc(store: Store) -> StyledDoc:
    boxes = []
    for i, ((family, ea), _) in enumerate(EXPECTED.items()):
        text = "中文字型測試" if ea else f"{family} sample"
        boxes.append(StyledTextBox(id=f"p001-box-{i}", box=Box(x=40.5, y=30.25 + i * 48, w=420, h=40), z=10 + i, paragraphs=[StyledParagraph(id=f"p{i}", runs=[run(text, family, 20 + i, color=(10 * i, 50, 100), ea=ea)], align=Align.LEFT)]))
    boxes.append(StyledTextBox(id="p001-box-rot", box=Box(x=600, y=420, w=200, h=40, rotation=-12.0), z=30, paragraphs=[StyledParagraph(id="rot", runs=[run("旋轉 Rotated", "Microsoft JhengHei", 16, ea=True)])]))
    complex_path = StyledShape(id="p001-shape-gear", box=Box(x=600, y=40, w=100, h=100), z=2, geometry="path", fill=Color(r=200, g=0, b=0), stroke=Color(r=0, g=0, b=0), stroke_width_pt=2.0, path=ring_path(20, 50, 20))
    simple_path = StyledShape(id="p001-shape-tri", box=Box(x=750, y=40, w=60, h=60), z=3, geometry="path", fill=Color(r=0, g=128, b=0), path=[[0, 30, 0], [1, 60, 60], [1, 0, 60], [3]])
    rrect = StyledShape(id="p001-shape-rr", box=Box(x=600, y=200, w=200, h=80), z=1, geometry="rounded_rect", fill=Color(r=220, g=230, b=250), corner_radius_pt=12)
    buf = io.BytesIO()
    Image.new("RGB", (300, 200), (0, 90, 180)).save(buf, "PNG")
    img = StyledImage(id="p001-img-photo", box=Box(x=600, y=300, w=150, h=100), z=4, image=store.put_blob(buf.getvalue(), "image/png", "png"))
    cells = [StyledCell(row=r, col=c, paragraphs=[StyledParagraph(id=f"c{r}{c}", runs=[run(f"格{r}{c}", "新細明體", 10, ea=True)])]) for r in range(2) for c in range(2)]
    table = StyledTable(id="p001-table-t", box=Box(x=40, y=440, w=300, h=60), z=40, col_widths_pt=[150.0, 150.0], row_heights_pt=[30.0, 30.0], cells=cells)
    page1 = StyledPage(page_index=0, width_pt=960, height_pt=540, background_color=Color(r=250, g=250, b=245), text_boxes=boxes, shapes=[complex_path, simple_path, rrect], images=[img], tables=[table])
    page2 = StyledPage(page_index=1, width_pt=960, height_pt=540, text_boxes=[StyledTextBox(id="p002-box", box=Box(x=40, y=40, w=400, h=40), z=1, paragraphs=[StyledParagraph(id="q", runs=[run("Second page", "Helvetica Neue", 24)])])])
    return StyledDoc(target_mode=measured(TargetMode.CROSS_SOURCE, 1.0, Provenance.INFERRED, W), source_app=measured("unknown", 1.0, Provenance.INFERRED, W), pages=[page1, page2])


@pytest.fixture(scope="module")
def cfg():
    return Config.load()


@pytest.fixture(scope="module")
def result(tmp_path_factory, cfg):
    store = Store(tmp_path_factory.mktemp("gslides-store"))
    doc = build_doc(store)
    out = gs.export(doc, "deck", cfg, store)
    data = store.read_blob(out.file)
    return doc, out, data, Presentation(io.BytesIO(data)), store


def by_name(slide):
    return {s.name: s for s in slide.shapes}


def pt(emu) -> float:
    return Emu(emu).pt


def run_fonts(r) -> dict[str, str]:
    rpr = r._r.find(f"{{{A}}}rPr")
    return {tag: rpr.find(f"{{{A}}}{tag}").get("typeface") for tag in ("latin", "ea", "cs") if rpr.find(f"{{{A}}}{tag}") is not None}


# ---------------------------------------------------------------- 字型對照


@pytest.mark.parametrize(
    "family,ea,expected",
    [(f, ea, g) for (f, ea), g in EXPECTED.items()]
    + [
        ("Microsoft JhengHei", False, "Noto Sans TC"),  # 中文字型寫的西文字也換成涵蓋中英文的字型
        ("新細明體", True, "Noto Serif TC"),
        ("SimSun", True, "Noto Serif TC"),
        ("標楷體", True, "LXGW WenKai TC"),
        ("WenQuanYi Zen Hei", True, "Noto Sans TC"),
        ("Calibri", True, "Noto Sans TC"),  # 東亞文字配西文字型
        ("Times New Roman", True, "Noto Serif TC"),
        ("Roboto", False, "Roboto"),  # 已在 Google 字型庫
        ("Noto Sans TC", True, "Noto Sans TC"),
        ("Liberation Sans", False, "Arimo"),
    ],
)
def test_map_font(family, ea, expected):
    assert gs.map_font(family, ea, P) == expected


def test_reopens_with_metadata_and_reminder(result):
    doc, out, _, prs, _ = result
    assert out.format == OutputFormat.GOOGLE_SLIDES and out.filename == "deck_google_slides.pptx"
    assert out.selfcheck_ok, out.selfcheck_notes
    assert len(prs.slides) == len(doc.pages)
    assert any("請在 Google 雲端硬碟中以 Google 簡報開啟" in n for n in out.selfcheck_notes)
    assert not any("字型庫清單以外" in n for n in out.selfcheck_notes)


def test_fonts_mapped_in_all_three_slots_and_style_kept(result):
    doc, out, data, prs, _ = result
    shapes = by_name(prs.slides[0])
    for i, ((family, ea), google) in enumerate(EXPECTED.items()):
        src = doc.pages[0].text_boxes[i]
        sh = shapes[src.id]
        assert abs(pt(sh.left) - src.box.x) <= 0.5 and abs(pt(sh.top) - src.box.y) <= 0.5
        assert abs(pt(sh.width) - src.box.w) <= 0.5 and abs(pt(sh.height) - src.box.h) <= 0.5
        r_out = sh.text_frame.paragraphs[0].runs[0]
        r_src = src.paragraphs[0].runs[0]
        assert r_out.text == r_src.text
        assert run_fonts(r_out) == {"latin": google, "ea": google, "cs": google}, family
        assert r_out.font.size.pt == r_src.size_pt.value
        assert str(r_out.font.color.rgb) == r_src.color.hex
    rot = shapes["p001-box-rot"]
    assert abs(rot.rotation - (360 - 12.0)) < 0.01 or abs(rot.rotation + 12.0) < 0.01
    assert run_fonts(rot.text_frame.paragraphs[0].runs[0])["ea"] == "Noto Sans TC"
    assert run_fonts(by_name(prs.slides[1])["p002-box"].text_frame.paragraphs[0].runs[0])["latin"] == "Arimo"
    for original in ("Calibri", "Cambria", "Times New Roman", "Courier New", "微軟正黑體", "PMingLiU", "Futura", "新細明體", "Microsoft JhengHei"):
        assert f'typeface="{original}"'.encode() not in data
    allowed = {"Carlito", "Caladea", "Arimo", "Tinos", "Cousine", "Noto Sans TC", "Noto Serif TC"}
    assert set(out.fonts_used) <= allowed and gs.pptx_fonts(data) <= allowed


def test_font_degradations(result):
    _, out, *_ = result
    fonts = {(d.element_id, d.concept): d for d in out.degradations if d.concept.startswith("字型")}
    assert fonts[("p001-box-0", "字型 Calibri")].lossy is False  # Carlito 字寬相容
    assert fonts[("p001-box-7", "字型 Futura")].lossy is True
    assert "Arimo" in fonts[("p001-box-7", "字型 Futura")].action
    assert fonts[("p001-box-5", "字型 微軟正黑體")].lossy is True
    assert ("p001-table-t", "字型 新細明體") in fonts


def test_complex_path_rasterized_simple_path_kept(result):
    doc, out, _, prs, store = result
    shapes = by_name(prs.slides[0])
    gear = shapes["p001-shape-gear"]
    assert gear.shape_type == MSO_SHAPE_TYPE.PICTURE
    src = next(s for s in doc.pages[0].shapes if s.id == "p001-shape-gear")
    cx, cy = pt(gear.left) + pt(gear.width) / 2, pt(gear.top) + pt(gear.height) / 2
    assert abs(cx - (src.box.x + src.box.w / 2)) <= 0.5 and abs(cy - (src.box.y + src.box.h / 2)) <= 0.5
    assert pt(gear.width) >= src.box.w and pt(gear.width) - src.box.w <= 2 * (src.stroke_width_pt / 2 + 1)
    img = Image.open(io.BytesIO(gear.image.blob)).convert("RGBA")
    w, h = img.size
    assert img.getpixel((w // 2, h // 2))[3] == 0  # 內圈是洞（奇偶規則）
    ring = img.getpixel((int(w * 0.5), int(h * 0.15)))  # 外圈與內圈之間：填色
    assert ring[3] > 200 and ring[0] > 150 and ring[1] < 60
    assert img.getpixel((2, 2))[3] == 0  # 角落透明
    assert any(d.element_id == "p001-shape-gear" and "複雜自訂路徑" in d.concept and d.lossy for d in out.degradations)
    tri = shapes["p001-shape-tri"]
    assert tri.shape_type != MSO_SHAPE_TYPE.PICTURE
    assert abs(pt(tri.left) - 750) <= 0.5 and abs(pt(tri.width) - 60) <= 0.5


def test_rasterize_path_unit():
    tri = StyledShape(id="t", box=Box(x=0, y=0, w=72, h=72), z=0, geometry="path", fill=Color(r=0, g=0, b=255), path=[[0, 0, 0], [1, 72, 0], [1, 0, 72], [3]])
    png, box = gs.rasterize_path(tri, {**P, "raster_dpi": 72.0})
    img = Image.open(io.BytesIO(png)).convert("RGBA")
    assert box.x < 0 and abs(box.w - img.width) <= 1  # 72 dpi：1 pt = 1 像素
    assert img.getpixel((10, 10))[2] > 200 and img.getpixel((10, 10))[3] > 200
    assert img.getpixel((img.width - 5, img.height - 5))[3] == 0
    curve = [[0, 0, 50], [2, 0, 0, 100, 0, 100, 50], [3]]
    pts = gs.flatten_path(curve, 8)
    assert len(pts) == 1 and pts[0][1] is True and len(pts[0][0]) == 9


def test_tables_and_rounded_rect_kept_or_recorded(result):
    _, out, _, prs, _ = result
    shapes = by_name(prs.slides[0])
    frame = shapes.get("p001-table-t")
    if frame is not None and getattr(frame, "has_table", False):
        assert (len(frame.table.rows), len(frame.table.columns)) == (2, 2)
        cell_run = frame.table.cell(0, 0).text_frame.paragraphs[0].runs[0]
        assert cell_run.text == "格00" and run_fonts(cell_run).get("ea") == "Noto Serif TC"
    else:
        assert any(d.element_id == "p001-table-t" and d.concept == "表格" for d in out.degradations)
    assert shapes["p001-img-photo"].shape_type == MSO_SHAPE_TYPE.PICTURE


def test_file_size_budget_shrinks_images(tmp_path, cfg):
    store = Store(tmp_path / "store")
    doc = build_doc(store)
    small = Config({**cfg.data, "export": {**cfg.section("export"), "google_slides": {"file_size_budget_mb": 0.0002}}})
    out = gs.export(doc, "tiny", small, store)
    prs = Presentation(io.BytesIO(store.read_blob(out.file)))
    pic = by_name(prs.slides[0])["p001-img-photo"]
    assert Image.open(io.BytesIO(pic.image.blob)).width < 300
    assert abs(pt(pic.width) - 150) <= 0.5  # 位置大小不變，只降解析度
    assert any(d.element_id == "p001-img-photo" and d.concept == "圖片解析度" for d in out.degradations)


def test_libreoffice_render(result, tmp_path):
    doc, _, data, _, _ = result
    src = tmp_path / "deck_google_slides.pptx"
    src.write_bytes(data)
    pdf = pdfium.PdfDocument(libreoffice.to_pdf(src))
    assert len(pdf) == len(doc.pages)
    text = "".join(pdf[i].get_textpage().get_text_range() for i in range(len(pdf)))
    for tb in [t for pg in doc.pages for t in pg.text_boxes]:
        for p in tb.paragraphs:
            assert "".join(r.text for r in p.runs) in text


# ---------------------------------------------------------------- 真實流水線


def test_real_pipeline_slide(tmp_path, cfg):
    p = Pipeline(tmp_path / "job")
    p.start(FIXTURES / "slide_dark.pdf", "slide_dark.pdf", Intent())
    p.run()
    doc = p.store.load("s5/styled_doc", StyledDoc)
    out = gs.export(doc, "slide_dark", cfg, p.store)
    data = p.store.read_blob(out.file)
    assert out.selfcheck_ok, out.selfcheck_notes
    prs = Presentation(io.BytesIO(data))
    assert len(prs.slides) == len(doc.pages)
    allowed = {"Carlito", "Caladea", "Arimo", "Tinos", "Cousine", "Noto Sans TC", "Noto Serif TC", "LXGW WenKai TC"}
    assert gs.pptx_fonts(data) <= allowed and set(out.fonts_used) <= allowed
    for slide, page in zip(prs.slides, doc.pages):
        shapes = by_name(slide)
        for tb in page.text_boxes:
            sh = shapes[tb.id]
            assert abs(pt(sh.left) - tb.box.x) <= 0.5 and abs(pt(sh.top) - tb.box.y) <= 0.5
            assert sh.text_frame.text.replace("\v", "\n") == "\n".join("".join(r.text for r in p.runs) for p in tb.paragraphs)
            for r_out, r_src in zip((r for para in sh.text_frame.paragraphs for r in para.runs), (r for para in tb.paragraphs for r in para.runs)):
                assert run_fonts(r_out)["ea"] == gs.map_font(r_src.font.family.value, r_src.font.east_asian, P)
    src = tmp_path / "slide_dark.pptx"
    src.write_bytes(data)
    pdf = pdfium.PdfDocument(libreoffice.to_pdf(src))
    assert len(pdf) == len(doc.pages)
    text = "".join(pdf[i].get_textpage().get_text_range() for i in range(len(pdf))).replace("\r", "").replace("\n", "").replace(" ", "")
    for tb in [t for pg in doc.pages for t in pg.text_boxes]:
        for para in tb.paragraphs:
            assert "".join(r.text for r in para.runs).replace(" ", "") in text
