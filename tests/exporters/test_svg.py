"""SVG 輸出與驗證的測試：五個 fixture 跑整條流水線（輸出、自檢、目標驗證）、手工建的成品文件
（向量圖、漸層、基本圖形、奇偶填色的孔洞、筆畫）輸出後用 resvg 渲染並與中性渲染（ai_pdf 加 PDFium）比對、
svg_assets 的 zip、文字轉外框、多頁 zip、外部圖檔、寫出後的安全檢查、目標驗證抓得到被改壞的檔案，
以及有 Chromium 時的交叉比對（沒有就略過）。"""

from __future__ import annotations

import base64
import copy
import io
import json
import math
import re
import shutil
import subprocess
import zipfile
from pathlib import Path

import numpy as np
import pytest
from PIL import Image

from mortise.artifacts import (
    Align,
    Border,
    Box,
    Color,
    FontChoice,
    Gradient,
    GradientStop,
    Intent,
    Layout,
    LineSpacing,
    LineSpacingMode,
    OutputFile,
    OutputFormat,
    PageSpec,
    Point,
    Provenance,
    Severity,
    StyledCell,
    StyledDoc,
    StyledImage,
    StyledMaster,
    StyledPage,
    StyledParagraph,
    StyledRun,
    StyledShape,
    StyledTable,
    StyledTextBox,
    StyledVector,
    StyledVectorPath,
    TargetMode,
    VectorPrimitive,
    measured,
)
from mortise.config import Config
from mortise.exporters import ai_pdf, svg, svg_assets
from mortise.pipeline import Pipeline
from mortise.pipeline.store import Store
from mortise.stages import s6_export
from mortise.stages.s7_verify import similarity
from mortise.tools import pdfium as mp
from mortise.verify import svg_target, verifier

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"
REPO = Path(__file__).resolve().parents[2]
NAMES = ["slide_basic", "slide_dark", "doc_a4", "poster", "deck"]
W = "test@1"
Q = "{" + svg.SVG_NS + "}"
XLINK = "{" + svg.XLINK_NS + "}href"
INK = "{" + svg.INKSCAPE_NS + "}"
K = 0.5522847498  # 用四段三次貝茲畫圓的控制點比例


# ---------------------------------------------------------------- 建構測試用成品文件


def run(text, family="Liberation Sans", size=12.0, bold=False, italic=False, color=(0, 0, 0), ea=False, a=1.0, verify=None):
    font = FontChoice(family=measured(family, 0.9, Provenance.INFERRED, W), verification_family=verify or family, is_substituted=verify is not None and verify != family, east_asian=ea)
    return StyledRun(text=text, font=font, size_pt=measured(float(size), 0.9, Provenance.MEASURED, W), bold=bold, italic=italic, color=Color(r=color[0], g=color[1], b=color[2], a=a))


def para(pid, *runs, align=Align.LEFT, lines=1, exact=None):
    ls = measured(LineSpacing(mode=LineSpacingMode.EXACT, value=exact), 0.8, Provenance.MEASURED, W) if exact else None
    return StyledParagraph(id=pid, runs=list(runs), align=align, source_line_count=lines, line_spacing=ls)


def tbox(tid, x, y, w, h, *paras, z=10, rot=0.0, wrap=False):
    return StyledTextBox(id=tid, box=Box(x=x, y=y, w=w, h=h, rotation=rot), z=z, paragraphs=list(paras), wrap=wrap)


def png_blob(store: Store, color=(200, 30, 30, 255), size=(40, 30)):
    buf = io.BytesIO()
    Image.new("RGBA", size, color).save(buf, "PNG")
    return store.put_blob(buf.getvalue(), "image/png", "png")


def col(r, g, b, a=1.0) -> Color:
    return Color(r=r, g=g, b=b, a=a)


def _rot(dx, dy, ang):
    th = math.radians(ang)
    return dx * math.cos(th) - dy * math.sin(th), dx * math.sin(th) + dy * math.cos(th)


def rect_path(cx, cy, w, h, ang=0.0):
    pts = [(cx + p[0], cy + p[1]) for p in (_rot(dx, dy, ang) for dx, dy in ((-w / 2, -h / 2), (w / 2, -h / 2), (w / 2, h / 2), (-w / 2, h / 2)))]
    return [[0, *pts[0]], [1, *pts[1]], [1, *pts[2]], [1, *pts[3]], [3]]


def ellipse_path(cx, cy, w, h, ang=0.0):
    a, b = w / 2, h / 2

    def p(dx, dy):
        x, y = _rot(dx, dy, ang)
        return [cx + x, cy + y]

    return [
        [0, *p(a, 0)],
        [2, *p(a, b * K), *p(a * K, b), *p(0, b)],
        [2, *p(-a * K, b), *p(-a, b * K), *p(-a, 0)],
        [2, *p(-a, -b * K), *p(-a * K, -b), *p(0, -b)],
        [2, *p(a * K, -b), *p(a, -b * K), *p(a, 0)],
        [3],
    ]


def rrect_path(cx, cy, w, h, r):
    x0, y0, x1, y1, k = cx - w / 2, cy - h / 2, cx + w / 2, cy + h / 2, r * (1 - K)
    return [
        [0, x0 + r, y0], [1, x1 - r, y0], [2, x1 - k, y0, x1, y0 + k, x1, y0 + r],
        [1, x1, y1 - r], [2, x1, y1 - k, x1 - k, y1, x1 - r, y1],
        [1, x0 + r, y1], [2, x0 + k, y1, x0, y1 - k, x0, y1 - r],
        [1, x0, y0 + r], [2, x0, y0 + k, x0 + k, y0, x0 + r, y0], [3],
    ]


def poly_path(points):
    return [[0, *points[0]]] + [[1, *p] for p in points[1:]] + [[3]]


def prim(kind, cx, cy, w, h, ang=0.0, r=0.0, points=None):
    return VectorPrimitive(kind=kind, cx=cx, cy=cy, w=w, h=h, angle_deg=ang, corner_radius=r, points=points or [])


LOGO_BOX = Box(x=320, y=40, w=240, h=200)
TRIANGLE = [[130.0, 190.0], [150.0, 150.0], [170.0, 190.0]]


def logo_paths() -> list[StyledVectorPath]:
    """一個向量圖用到的各種寫法。座標相對於框的左上角（pt）。路徑與 primitive 描述同一個形狀。"""
    lin = Gradient(kind="linear", coords=[0.0, 0.0, 240.0, 0.0], stops=[GradientStop(offset=0.0, color=col(20, 60, 160)), GradientStop(offset=1.0, color=col(40, 180, 170))])
    rad = Gradient(kind="radial", coords=[180.0, 60.0, 30.0], stops=[GradientStop(offset=0.0, color=col(255, 240, 80)), GradientStop(offset=1.0, color=col(240, 120, 20))])
    line_dx, line_dy = _rot(30.0, 0.0, -30.0)
    return [
        StyledVectorPath(path=rect_path(120, 100, 240, 200), fill=col(30, 120, 165), gradient=lin, primitive=prim("rect", 120, 100, 240, 200)),  # 線性漸層的底
        StyledVectorPath(path=rect_path(60, 60, 60, 30, 30.0), fill=col(220, 40, 40, 0.8), primitive=prim("rect", 60, 60, 60, 30, 30.0)),  # 旋轉的矩形、半透明
        StyledVectorPath(path=ellipse_path(180, 60, 60, 60), fill=col(248, 180, 50), gradient=rad, primitive=prim("circle", 180, 60, 60, 60)),  # 放射漸層的圓
        StyledVectorPath(path=ellipse_path(60, 150, 80, 40, 20.0), fill=col(60, 170, 90), stroke=col(10, 40, 20), stroke_width_pt=2.0, primitive=prim("ellipse", 60, 150, 80, 40, 20.0)),
        StyledVectorPath(path=rrect_path(200, 120, 60, 30, 8.0), fill=col(20, 30, 70), primitive=prim("rounded_rect", 200, 120, 60, 30, 0.0, 8.0)),
        StyledVectorPath(path=poly_path(TRIANGLE), fill=col(130, 60, 170), stroke=col(255, 255, 255), stroke_width_pt=1.5, stroke_join="bevel", primitive=prim("polygon", 150, 170, 40, 40, 0.0, 0.0, TRIANGLE)),
        StyledVectorPath(path=[[0, 120 - line_dx, 30 - line_dy], [1, 120 + line_dx, 30 + line_dy]], stroke=col(255, 255, 255), stroke_width_pt=4.0, stroke_cap="round", primitive=prim("line", 120, 30, 60, 0, -30.0)),
        # 奇偶填色：外框與內框同方向，中間是孔洞（非零填色的話孔洞會被填滿）
        StyledVectorPath(path=rect_path(200, 170, 50, 40)[:-1] + [[3]] + rect_path(200, 170, 24, 16), fill=col(255, 255, 255), fill_rule="evenodd"),
        StyledVectorPath(path=[[0, 20, 190], [2, 40, 160, 70, 220, 100, 185]], stroke=col(250, 220, 60), stroke_width_pt=3.0, stroke_join="round", stroke_cap="square"),  # 只有筆畫的曲線
        StyledVectorPath(path=[[0, 10, 10], [1, 230, 10]], stroke=col(255, 255, 255)),  # 零寬細線（沒有寬度）
        StyledVectorPath(path=rect_path(20, 20, 10, 10)),  # 沒有填色也沒有筆畫：看不見，不寫
    ]


def mark_vector(page: int) -> tuple[StyledImage, StyledVector]:
    """每頁右下角都有的小標記（母片成員），來源是描圖重建。"""
    box = Box(x=520, y=340, w=40, h=40)
    vec = StyledVector(
        id=f"p{page + 1:03d}-vec-mark", image_id=f"p{page + 1:03d}-img-mark", box=box, z=3, origin="traced", confidence=0.82, kind="icon", render_error=2.5,
        paths=[
            StyledVectorPath(path=ellipse_path(20, 20, 36, 36), fill=col(30, 60, 110), primitive=prim("circle", 20, 20, 36, 36)),
            StyledVectorPath(path=poly_path([[20.0, 8.0], [30.0, 30.0], [10.0, 30.0]]), fill=col(255, 255, 255)),
        ],
    )
    return vec, box


def build_doc(store: Store) -> StyledDoc:
    logo = StyledVector(id="p001-vec-logo", image_id="p001-img-logo", box=LOGO_BOX, z=2, paths=logo_paths(), origin="pdf_paths", kind="logo")
    mark1, mark_box = mark_vector(0)
    mark2, _ = mark_vector(1)
    b4 = [Border(color=col(0, 0, 0), width_pt=0.75)] * 4
    cells = [
        StyledCell(row=0, col=0, col_span=2, header=True, paragraphs=[para("c-h", run("項目 Item", "WenQuanYi Zen Hei", 10, color=(255, 255, 255), ea=True), align=Align.CENTER)], fill=col(31, 58, 95), borders=b4, v_align="middle"),
        StyledCell(row=1, col=0, paragraphs=[para("c-a", run("營收", "WenQuanYi Zen Hei", 10, ea=True))], borders=b4, margins_pt=[2.0, 3.0, 2.0, 3.0]),
        StyledCell(row=1, col=1, paragraphs=[para("c-b", run("1,250", size=10), align=Align.RIGHT)], borders=b4, margins_pt=[2.0, 3.0, 2.0, 3.0], value=1250.0, number_format="#,##0"),
    ]
    table = StyledTable(id="p001-table-1", box=Box(x=40, y=330, w=200, h=44), z=6, col_widths_pt=[100.0, 100.0], row_heights_pt=[22.0, 22.0], cells=cells, header_rows=1)
    p1 = StyledPage(
        page_index=0, width_pt=600.0, height_pt=400.0, background_color=col(250, 248, 240),
        text_boxes=[
            tbox("p001-box-title", 40, 30, 260, 36, para("t1", run("Vector ", size=24, bold=True, color=(20, 40, 90)), run("向量圖", "WenQuanYi Zen Hei", 24, bold=True, color=(20, 40, 90), ea=True))),
            tbox("p001-box-body", 40, 80, 250, 60, para("t2", run("AVATAR Wave To the quick brown fox jumps over the lazy dog and keeps running far away from the river bank today", size=12), lines=3, exact=16.0, align=Align.JUSTIFY), wrap=True),
            tbox("p001-box-center", 40, 150, 250, 24, para("t3", run("AVATAR To Wave", size=18, color=(150, 30, 30)), align=Align.CENTER)),
            tbox("p001-box-right", 40, 180, 250, 20, para("t4", run("Total ", size=12), run("1,250", "Liberation Serif", 12, italic=True, color=(0, 100, 0)), align=Align.RIGHT)),
            tbox("p001-box-rot", 60, 260, 160, 24, para("t5", run("Rotated 轉", size=16, color=(60, 60, 60))), rot=-15.0),
            tbox("p001_box/odd id", 40, 215, 250, 18, para("t6", run("A <b> & \"q\" 'x'  two  spaces", size=11))),
        ],
        images=[
            StyledImage(id="p001-img-photo", box=Box(x=260, y=260, w=60, h=45, rotation=10.0), z=4, image=png_blob(store, (40, 110, 200, 255))),
            StyledImage(id="p001-img-logo", box=LOGO_BOX, z=2, image=png_blob(store, (128, 128, 128, 255), (48, 40)), vector_id="p001-vec-logo"),
            StyledImage(id="p001-img-mark", box=mark_box, z=3, image=png_blob(store, (30, 60, 110, 255), (20, 20)), vector_id="p001-vec-mark"),
        ],
        shapes=[
            StyledShape(id="p001-shp-rect", box=Box(x=340, y=260, w=60, h=40), z=1, geometry="rect", fill=col(0, 128, 0)),
            StyledShape(id="p001-shp-round", box=Box(x=410, y=260, w=60, h=40, rotation=20.0), z=1, geometry="rounded_rect", corner_radius_pt=10.0, fill=col(0, 0, 200), stroke=col(0, 0, 0), stroke_width_pt=1.0),
            StyledShape(id="p001-shp-oval", box=Box(x=340, y=320, w=60, h=40), z=1, geometry="ellipse", fill=col(200, 0, 200, 0.6)),
            StyledShape(id="p001-shp-line", box=Box(x=410, y=320, w=60, h=40, rotation=1.0), z=1, geometry="line", stroke=col(255, 0, 0), stroke_width_pt=3.0),
            StyledShape(id="p001-shp-path", box=Box(x=480, y=260, w=30, h=60), z=1, geometry="path", fill=col(255, 128, 0), path=[[0, 0, 60], [1, 30, 60], [2, 30, 30, 20, 0, 15, 0], [2, 10, 0, 0, 30, 0, 60], [3]]),
        ],
        tables=[table], vectors=[logo, mark1],
        reading_order=["p001-box-title", "p001-box-body", "p001-box-center", "p001-box-right", "p001_box/odd id", "p001-box-rot", "p001-table-1"],
    )
    p2 = StyledPage(
        page_index=1, width_pt=600.0, height_pt=400.0,
        text_boxes=[tbox("p002-box-title", 40, 30, 300, 30, para("s1", run("第二頁 Page two", "WenQuanYi Zen Hei", 20, ea=True)))],
        images=[StyledImage(id="p002-img-mark", box=mark_box, z=3, image=png_blob(store, (30, 60, 110, 255), (20, 20)), vector_id="p002-vec-mark")],
        vectors=[mark2],
    )
    master = StyledMaster(id="master-1", images=[p1.images[2]], member_ids=["p001-img-mark", "p002-img-mark"], page_indexes=[0, 1])
    return StyledDoc(target_mode=measured(TargetMode.CROSS_SOURCE, 0.5, Provenance.INFERRED, W), source_app=measured("unknown", 0.0, Provenance.INFERRED, W), pages=[p1, p2], masters=[master])


def cfg_with(export=None, verify=None) -> Config:
    data = copy.deepcopy(Config.load().data)
    data.setdefault("export", {}).update(export or {})
    data.setdefault("verify", {}).update(verify or {})
    return Config(data)


def roots(out: OutputFile, store: Store):
    pages, others = svg.unpack(out, store)
    return {i: svg.parse(data) for i, data in pages.items()}, others


def group(root, element_id: str):
    hit = [g for g in root.iter(Q + "g") if g.get("id") == element_id or g.get("data-element-id") == element_id]
    assert len(hit) == 1, f"{element_id}: {len(hit)} 個群組"
    return hit[0]


def resvg_page(data: bytes, others: dict, width_px: int, emulate_bold: bool = True) -> Image.Image:
    rs, _ = svg_target.engine()
    assert rs is not None
    book = svg_target.font_book()
    text, _ = svg_target.render_copy(svg.parse(data), book, others, emulate_bold)
    return svg_target.render(rs, text, width_px, book)


def neutral_pages(doc: StyledDoc, store: Store, dpi: float) -> list[Image.Image]:
    """中性渲染：ai_pdf 輸出後用 PDFium 畫出來（與⑦的 7A 相同）。"""
    out = ai_pdf.export(doc, "neutral", Config.load(), store)
    d = mp.open_document(str(store.blob_path(out.file)))
    images = []
    try:
        for i in range(len(d)):
            r = mp.PageReader(d, i)
            try:
                images.append(r.render(dpi).convert("RGB"))
            finally:
                r.close()
    finally:
        d.close()
    return images


def diff_ratio(a: Image.Image, b: Image.Image, threshold: int = 48, box=None) -> float:
    """兩張圖差異超過門檻的像素比例。容許一個像素以內的位移（各渲染器的反鋸齒不同，邊緣會差一點），
    所以算進來的是真的不一樣的地方：少畫了東西、字型不同、位置差超過一個像素。box 是只比較的範圍（左、上、右、下，0 到 1）。"""
    if b.size != a.size:
        b = b.resize(a.size, Image.LANCZOS)
    x = np.asarray(a.convert("L"), dtype=np.int16)
    y = np.asarray(b.convert("L"), dtype=np.int16)
    best = np.full(x.shape, 255, dtype=np.int16)
    for dy in (-1, 0, 1):
        for dx in (-1, 0, 1):
            best = np.minimum(best, np.abs(x - np.roll(np.roll(y, dy, axis=0), dx, axis=1)))
    best = best[1:-1, 1:-1]
    if box is not None:
        h, w = best.shape
        best = best[int(box[1] * h):int(box[3] * h), int(box[0] * w):int(box[2] * w)]
    return float((best > threshold).mean())


def pixel(im: Image.Image, x_pt: float, y_pt: float, page_w: float) -> tuple[int, int, int]:
    s = im.size[0] / page_w
    return im.getpixel((int(x_pt * s), int(y_pt * s)))[:3]


def near(c, want, tol=12) -> bool:
    return all(abs(a - b) <= tol for a, b in zip(c, want))


# ---------------------------------------------------------------- fixtures


@pytest.fixture(scope="module")
def store(tmp_path_factory) -> Store:
    return Store(tmp_path_factory.mktemp("svg-store"))


@pytest.fixture(scope="module")
def doc(store) -> StyledDoc:
    return build_doc(store)


@pytest.fixture(scope="module")
def out(doc, store) -> OutputFile:
    return svg.export(doc, "hand", Config.load(), store)


@pytest.fixture(scope="module")
def page1(out, store):
    r, _ = roots(out, store)
    return r[0]


@pytest.fixture(scope="module", params=NAMES)
def job(request, tmp_path_factory):
    """整條流水線：只要求 SVG 輸出，⑦會做 7A 中性驗證與 SVG 的目標驗證。"""
    name = request.param
    pipe = Pipeline(tmp_path_factory.mktemp(f"svg-{name}"))
    pipe.start(FIXTURES / f"{name}.pdf", f"{name}.pdf", Intent(outputs=[OutputFormat.SVG]))
    report = pipe.run()
    return name, pipe, report


# ---------------------------------------------------------------- 五個 fixture：整條流水線


def test_registered():
    assert OutputFormat.SVG in s6_export.available() and OutputFormat.SVG_ASSETS in s6_export.available()
    assert verifier(OutputFormat.SVG) is svg_target.verify


def test_fixture_pipeline_output_and_verification(job):
    name, pipe, report = job
    styled = pipe.store.load("s5/styled_doc", StyledDoc)
    out = pipe.store.load("s6/svg", OutputFile)
    assert out.format == OutputFormat.SVG and out.selfcheck_ok, out.selfcheck_notes
    multi = len(styled.pages) > 1
    assert out.filename == (f"{name}_svg.zip" if multi else f"{name}.svg")
    assert out.file.media_type == ("application/zip" if multi else "image/svg+xml")
    pages, others = svg.unpack(out, pipe.store)
    assert sorted(pages) == list(range(len(styled.pages))) and not others
    fr = next(r for r in report.format_reports if r.format == OutputFormat.SVG)
    assert fr.ok, [(i.code, i.message) for p in fr.pages for i in p.issues]
    assert "resvg" in fr.engine and "不是" in fr.engine
    assert len(fr.pages) == len(styled.pages)
    for pr, neutral in zip(fr.pages, report.pages):
        assert pr.text_match_ratio == 1.0
        assert pr.position_error_pt is not None and pr.position_error_pt <= 0.05
        assert pr.similarity is not None and pr.similarity >= 0.975
        assert pr.similarity >= neutral.neutral_similarity - 0.005  # 不比自家的中性渲染差
        assert not [i for i in pr.issues if i.severity == Severity.ERROR]
    # 渲染結果收成一份 PDF，頁數與成品文件相同
    assert fr.render is not None and fr.render.media_type == "application/pdf"
    d = mp.open_document(str(pipe.store.blob_path(fr.render)))
    try:
        assert len(d) == len(styled.pages)
    finally:
        d.close()


def test_fixture_elements_have_ids_in_z_order(job):
    name, pipe, _ = job
    styled = pipe.store.load("s5/styled_doc", StyledDoc)
    out = pipe.store.load("s6/svg", OutputFile)
    rs, _ = roots(out, pipe.store)
    members = {eid for m in styled.masters for eid in m.member_ids}
    for page in styled.pages:
        root = rs[page.page_index]
        assert root.get("viewBox") == f"0 0 {svg.num(page.width_pt)} {svg.num(page.height_pt)}"
        assert root.get("width") == svg.num(page.width_pt) + "pt" and root.get("height") == svg.num(page.height_pt) + "pt"
        top = [g for g in root if g.tag == Q + "g"]
        want = [item.id for _, item in svg.page_items(page)]
        got = [g.get("id") for g in top if g.get("data-kind") != "background"]
        assert got == want  # 每個元素一個群組、編號就是元素編號、順序是圖層由下到上；母片成員不重複
        kinds = {g.get("id"): g.get("data-kind") for g in top}
        for kind, item in svg.page_items(page):
            assert kinds[item.id] == kind
        assert {g.get("id") for g in top if g.get("data-master")} == {i for i in want if i in members}
        assert (page.background_color is not None) == any(g.get("data-kind") == "background" for g in top)
        assert all(t.get("{http://www.w3.org/XML/1998/namespace}space") == "preserve" for t in root.iter(Q + "text"))
        assert not [t for t in root.iter(Q + "text") if t.get("text-anchor")]


# ---------------------------------------------------------------- 手工建的成品文件：結構


def test_selfcheck_and_degradations(out, doc):
    assert out.format == OutputFormat.SVG and out.selfcheck_ok, out.selfcheck_notes
    assert out.filename == "hand_svg.zip" and out.file.ext == "zip"
    assert out.fonts_used == ["Liberation Sans", "Liberation Serif", "WenQuanYi Zen Hei"]
    concepts = {d.concept for d in out.degradations}
    assert {"表格", "母片", "字型", "左右對齊", "零寬細線", "數字格式與公式", "段落與文字框", "字距微調", "粗體", "閱讀順序"} <= concepts
    assert any("未經實測" in n for n in out.selfcheck_notes)


def test_groups_ids_and_layers(page1, doc):
    top = [g for g in page1 if g.tag == Q + "g"]
    ids = [g.get("id") for g in top]
    assert len(ids) == len(set(ids)) and all(re.fullmatch(r"[A-Za-z][A-Za-z0-9\-]*", i) for i in ids)
    assert ids[0] == "p001-background" and top[0].get("data-kind") == "background"
    # 圖層由下到上；向量圖的群組用向量圖的編號，並指回它的圖片
    assert ids[1:] == ["p001-shp-rect", "p001-shp-round", "p001-shp-oval", "p001-shp-line", "p001-shp-path", "p001-vec-logo", "p001-vec-mark", "p001-img-photo",
                       "p001-table-1", "p001-box-title", "p001-box-body", "p001-box-center", "p001-box-right", "p001-box-rot", "p001-box-odd-id"]
    odd = group(page1, "p001_box/odd id")  # 不合法的編號：id 只留英數與連字號，原編號另外寫
    assert odd.get("id") == "p001-box-odd-id" and odd.get("data-element-id") == "p001_box/odd id"
    assert all(g.get(INK + "groupmode") == "layer" and g.get(INK + "label") for g in top)
    assert group(page1, "p001-box-title").get(INK + "label") == "文字 Vector"
    assert group(page1, "p001-vec-mark").get("data-master") == "master-1"
    assert page1.get("width") == "600pt" and page1.get("viewBox") == "0 0 600 400"


def test_vector_group_primitives_gradients_and_strokes(page1):
    g = group(page1, "p001-vec-logo")
    assert g.get("data-kind") == "vector" and g.get("data-origin") == "pdf_paths" and g.get("data-image-id") == "p001-img-logo"
    assert not list(page1.iter(Q + "image")) or all(im.getparent().get("id") != "p001-img-logo" for im in page1.iter(Q + "image"))  # 圖片被向量圖取代
    kids = list(g)
    assert [k.tag.replace(Q, "") for k in kids] == ["rect", "rect", "circle", "ellipse", "rect", "polygon", "line", "path", "path", "path"]
    base, tilted, circle, ellipse, rounded, polygon, line, ring, curve, hair = kids
    # 座標是頁面座標（框的左上角在 320, 40）
    assert (base.get("x"), base.get("y"), base.get("width"), base.get("height")) == ("320", "40", "240", "200")
    assert tilted.get("transform") == "rotate(30 380 100)" and tilted.get("fill-opacity") == "0.8"
    assert (circle.get("cx"), circle.get("cy"), circle.get("r")) == ("500", "100", "30")
    assert ellipse.get("transform") == "rotate(20 380 190)" and ellipse.get("stroke-width") == "2"
    assert rounded.get("rx") == "8" and rounded.get("ry") == "8"
    assert polygon.get("points") == "450,230 470,190 490,230" and polygon.get("stroke-linejoin") == "bevel"
    assert line.get("stroke-linecap") == "round" and float(line.get("x1")) == pytest.approx(440 - 30 * math.cos(math.radians(30)), abs=0.01)
    assert ring.get("fill-rule") == "evenodd" and ring.get("d").count("Z") == 2
    assert curve.get("fill") == "none" and curve.get("stroke-linejoin") == "round" and curve.get("stroke-linecap") == "square" and "C" in curve.get("d")
    assert hair.get("stroke-width") == "0.25"  # PDF 的零寬細線
    assert polygon.get("stroke-miterlimit") is None and ring.get("stroke") is None
    # 漸層在檔頭的 defs，用使用者座標
    defs = page1.find(Q + "defs")
    lin, rad = defs.find(Q + "linearGradient"), defs.find(Q + "radialGradient")
    assert lin.get("gradientUnits") == rad.get("gradientUnits") == "userSpaceOnUse"
    assert (lin.get("x1"), lin.get("x2")) == ("320", "560") and (rad.get("cx"), rad.get("cy"), rad.get("r")) == ("500", "100", "30")
    assert base.get("fill") == f"url(#{lin.get('id')})" and circle.get("fill") == f"url(#{rad.get('id')})"
    assert [s.get("stop-color") for s in lin] == ["#143ca0", "#28b4aa"]
    traced = group(page1, "p001-vec-mark")
    assert traced.get("data-origin") == "traced" and [k.tag.replace(Q, "") for k in traced] == ["circle", "path"]


def test_shapes_images_and_table(page1):
    assert group(page1, "p001-shp-rect")[0].tag == Q + "rect"
    rr = group(page1, "p001-shp-round")
    assert rr.get("transform") == "rotate(20 440 280)" and rr[0].get("rx") == "10" and rr[0].get("stroke") == "#000000"
    oval = group(page1, "p001-shp-oval")[0]
    assert oval.tag == Q + "ellipse" and oval.get("fill-opacity") == "0.6"
    line = group(page1, "p001-shp-line")[0]  # 直線的 rotation 非 0 表示左下到右上
    assert (line.get("x1"), line.get("y1"), line.get("x2"), line.get("y2")) == ("410", "360", "470", "320")
    path = group(page1, "p001-shp-path")[0]
    assert path.get("d").startswith("M480 320 L510 320 C") and path.get("fill-rule") == "evenodd"
    photo = group(page1, "p001-img-photo")
    assert photo.get("transform") == "rotate(10 290 282.5)"
    im = photo[0]
    assert im.get(XLINK).startswith("data:image/png;base64,") and im.get("href") is None and im.get("preserveAspectRatio") == "none"
    assert Image.open(io.BytesIO(base64.b64decode(im.get(XLINK).split(",", 1)[1]))).size == (40, 30)  # 原圖內嵌，不重新壓縮
    table = group(page1, "p001-table-1")
    assert [k.tag.replace(Q, "") for k in table] == ["rect"] + ["line"] * 11 + ["g"] * 3  # 三格各四條框線，兩格共用的那一條只寫一次
    cells = [k for k in table if k.tag == Q + "g"]
    assert [(c.get("data-kind"), c.get("data-row"), c.get("data-col")) for c in cells] == [("cell", "0", "0"), ("cell", "1", "0"), ("cell", "1", "1")]
    assert cells[2][0].tag == Q + "text" and cells[2][0].text == "1,250"


def test_text_writing(page1):
    title = group(page1, "p001-box-title")[0]
    assert title.tag == Q + "text" and title.get("font-size") == "24" and title.get("font-weight") == "700" and title.get("fill") == "#14285a"
    spans = list(title)
    assert [s.tag for s in spans] == [Q + "tspan"] * 2 and [s.text for s in spans] == ["Vector ", "向量圖"]
    assert [s.get("font-family") for s in spans] == ["'Liberation Sans', sans-serif", "'WenQuanYi Zen Hei', sans-serif"]
    assert all(s.get("x") is None and s.get("y") is None for s in spans) and title.get("font-family") is None  # 行內片段不帶座標
    body = list(group(page1, "p001-box-body"))
    assert len(body) == 3 and all(t.get("data-paragraph") == "t2" for t in body)
    assert float(body[0].get("word-spacing")) > 0 and body[2].get("word-spacing") is None  # 左右對齊：末行靠左
    assert [float(t.get("y")) for t in body] == pytest.approx([float(body[0].get("y")) + 16.0 * i for i in range(3)], abs=0.011)
    right = group(page1, "p001-box-right")[0]
    assert [(s.text, s.get("font-style"), s.get("fill")) for s in right] == [("Total ", "normal", "#000000"), ("1,250", "italic", "#006400")]
    assert right.get("font-size") == "12" and right.get("font-style") is None
    rot = group(page1, "p001-box-rot")
    assert rot.get("transform") == "rotate(-15 140 272)"
    odd = group(page1, "p001_box/odd id")[0]
    assert odd.text == "A <b> & \"q\" 'x'  two  spaces"  # 跳脫正確、連續空白保留
    for t in page1.iter(Q + "text"):
        assert t.get("text-anchor") is None and not re.search(r"[a-z%]", t.get("font-size") or "") and t.tail.strip() == ""


def test_intended_lines_match_neutral_layout(doc, store):
    """文字行的位置與 ai_pdf（7A 中性渲染）是同一套排版：行數、每行文字、基線都相同。"""
    out = ai_pdf.export(doc, "n", Config.load(), store)
    d = mp.open_document(str(store.blob_path(out.file)))
    r = mp.PageReader(d, 0)
    chars = [c for c in r.chars() if c.text.strip() and not c.generated]
    r.close()
    d.close()
    page = doc.pages[0]
    lines = [ln for ln in svg.intended_lines(doc, Config.load())[0] if not ln.rotation]
    baselines = sorted({round(page.height_pt - c.origin[1], 1) for c in chars})
    for ln in lines:
        assert min(abs(ln.y - b) for b in baselines) <= 0.06, ln.text
    body = [ln for ln in lines if ln.element_id == "p001-box-body"]
    assert len(body) == 3 and body[0].x == pytest.approx(40.0) and body[0].word_space > 0


# ---------------------------------------------------------------- 手工建的成品文件：渲染


def test_render_matches_neutral_render(out, doc, store):
    """SVG 用 resvg 畫出來，與同一份成品文件的中性渲染（ReportLab 加 PDFium）比對。"""
    pages, others = svg.unpack(out, store)
    neutral = neutral_pages(doc, store, 144.0)
    for i, ref in enumerate(neutral):
        im = resvg_page(pages[i], others, ref.size[0])
        assert similarity(ref, im) >= 0.99
        assert diff_ratio(ref, im) < 0.02  # 整頁的差異只在文字：SVG 的文字有字距微調，中性渲染沒有
        if i == 0:  # 圖形的部分（向量圖、形狀、圖片）兩邊畫出來要一樣
            assert diff_ratio(ref, im, box=(320 / 600, 40 / 400, 560 / 600, 240 / 400)) < 0.002
            assert diff_ratio(ref, im, box=(250 / 600, 250 / 400, 1.0, 1.0)) < 0.002


def test_render_pixels(out, store):
    pages, others = svg.unpack(out, store)
    im = resvg_page(pages[0], others, 2400)
    px = lambda x, y: pixel(im, x, y, 600.0)  # noqa: E731
    assert near(px(10, 10), (250, 248, 240))  # 頁面底色
    assert near(px(322, 235), (20, 60, 160), 8) and near(px(558, 235), (40, 180, 170), 8)  # 線性漸層的兩端
    assert near(px(500, 100), (255, 240, 80), 10) and near(px(500, 128), (241, 128, 24), 14)  # 放射漸層：中心與邊緣
    assert near(px(503, 205), (255, 255, 255))  # 奇偶填色的外環
    hole = px(520, 210)
    assert not near(hole, (255, 255, 255), 40) and hole[2] > hole[0]  # 孔洞露出底下的漸層，不是白色
    assert near(px(380, 190), (60, 170, 90))  # 橢圓
    assert near(px(520, 160), (20, 30, 70))  # 圓角矩形
    assert near(px(470, 222), (130, 60, 170))  # 多邊形
    assert near(px(440, 70), (255, 255, 255), 20)  # 圓頭的粗線中央
    assert near(px(540, 374), (30, 60, 110)) and near(px(540, 360), (255, 255, 255))  # 母片上的標記（描圖重建的向量圖）：圓底與白色三角形
    assert near(px(360, 280), (0, 128, 0)) and near(px(95, 341), (31, 58, 95))  # 形狀、表格的表頭底色


def _ink_span(im: Image.Image, page_w: float, y0: float, y1: float, x0: float, x1: float) -> tuple[float, float]:
    """某個橫帶裡深色墨跡的左右範圍（pt）。"""
    s = im.size[0] / page_w
    a = np.asarray(im.convert("L"))[int(y0 * s):int(y1 * s), int(x0 * s):int(x1 * s)]
    xs = np.where((a < 200).any(axis=0))[0]
    return x0 + xs.min() / s, x0 + (xs.max() + 1) / s


def test_centered_and_right_lines_account_for_kerning(doc, store):
    """各軟體畫字時會套用字距微調，行會比自家排版量到的窄；置中的行要真的置中、靠右的行要真的貼右。"""
    lines = {ln.element_id: ln for ln in svg.intended_lines(doc, Config.load())[0]}
    center = lines["p001-box-center"]
    shrink = center.width - center.shaped_width
    assert shrink > 3.0  # 這一行的字距微調很明顯（AV、AT、To、Wa）
    aware = svg.export(doc, "k1", Config.load(), store)
    plain = svg.export(doc, "k2", cfg_with({"svg_kerning_aware": False}), store)
    mid = {}
    for key, o in (("aware", aware), ("plain", plain)):
        pages, others = svg.unpack(o, store)
        im = resvg_page(pages[0], others, 2400)
        x0, x1 = _ink_span(im, 600.0, 152, 172, 40, 290)
        mid[key] = (x0 + x1) / 2
        if key == "aware":
            r0, r1 = _ink_span(im, 600.0, 182, 198, 150, 295)
            assert abs(r1 - 290.0) < 0.8  # 靠右的行：墨跡右緣貼著框的右緣
    assert abs(mid["aware"] - 165.0) < 0.8  # 框的中心在 x = 165
    assert mid["plain"] < 165.0 - shrink / 2 + 0.8  # 不修正的話整行偏左半個微調量


# ---------------------------------------------------------------- 設定


def test_size_unit_precision_and_layers(doc, store):
    o = svg.export(doc, "u", cfg_with({"svg_size_unit": "none", "svg_precision": 1, "svg_layers": "none", "svg_tspan_y": True}), store)
    assert o.selfcheck_ok, o.selfcheck_notes
    pages, _ = svg.unpack(o, store)
    root = svg.parse(pages[0])
    assert root.get("width") == "600" and root.get("height") == "400" and root.get("viewBox") == "0 0 600 400"
    assert b"inkscape" not in pages[0]
    title = group(root, "p001-box-title")[0]
    assert all(s.get("y") == title.get("y") and s.get("x") is None for s in title)
    assert all(len(v.split(".")[-1]) <= 1 for t in root.iter(Q + "text") for v in (t.get("x"), t.get("y")) if "." in v)
    for bad in ({"svg_text": "curves"}, {"svg_size_unit": "mm"}, {"svg_images": "link"}):
        with pytest.raises(ValueError):
            svg.export(doc, "bad", cfg_with(bad), store)


def test_vectors_off_draws_the_image(doc, store):
    o = svg.export(doc, "v", cfg_with({"svg_vectors": False, "svg_primitives": False}), store)
    r, _ = roots(o, store)
    g = group(r[0], "p001-img-logo")
    assert g.get("data-kind") == "image" and g[0].tag == Q + "image"
    assert not [x for x in r[0].iter(Q + "g") if x.get("data-kind") == "vector"]
    o2 = svg.export(doc, "v2", cfg_with({"svg_primitives": False}), store)
    r2, _ = roots(o2, store)
    assert {k.tag for k in group(r2[0], "p001-vec-logo")} == {Q + "path"}


def test_multi_page_zip_and_external_images(doc, store, out):
    with zipfile.ZipFile(store.blob_path(out.file)) as z:
        assert z.namelist() == ["page-001.svg", "page-002.svg"]
        assert "hand（第 2 頁）" in z.read("page-002.svg").decode("utf-8")
    again = svg.export(doc, "hand", Config.load(), store)
    assert again.file.sha256 == out.file.sha256  # 同樣的輸入每次得到同樣的檔案
    ext = svg.export(doc, "ext", cfg_with({"svg_images": "external"}), store)
    assert ext.selfcheck_ok, ext.selfcheck_notes
    with zipfile.ZipFile(store.blob_path(ext.file)) as z:
        names = z.namelist()
        images = [n for n in names if n.startswith("images/")]
        assert len(images) == 1 and names[-2:] == ["page-001.svg", "page-002.svg"]
        page = z.read("page-001.svg")
        assert Image.open(io.BytesIO(z.read(images[0]))).size == (40, 30)
    assert b"base64" not in page
    assert svg.parse(page).find(f".//{Q}image").get(XLINK) == images[0]
    # 單頁文件：內嵌時是單一 .svg，外部圖檔時才打包
    single = doc.model_copy(update={"pages": [doc.pages[0]], "masters": []})
    s1 = svg.export(single, "one", Config.load(), store)
    assert s1.filename == "one.svg" and s1.file.media_type == "image/svg+xml" and store.read_blob(s1.file).startswith(b"<?xml")
    s2 = svg.export(single, "one", cfg_with({"svg_images": "external"}), store)
    assert s2.filename == "one_svg.zip" and any("zip" in n for n in s2.selfcheck_notes)
    # 外部圖檔的 zip 一樣驗證得過（渲染前換成內嵌）
    fr = svg_target.verify(ext, [], [], Layout(pages=[]), doc, cfg_with({"svg_images": "external"}), store)
    assert fr.ok and any("外部圖檔換成內嵌" in n for n in fr.notes)


# ---------------------------------------------------------------- 文字轉外框


def test_outline_mode(doc, store, out):
    cfg = cfg_with({"svg_text": "outline"})
    o = svg.export(doc, "outline", cfg, store)
    assert o.selfcheck_ok, o.selfcheck_notes
    assert o.fonts_used == []
    assert any(d.concept == "文字" and "不能再編輯" in d.action and d.lossy for d in o.degradations)
    assert any(d.concept == "粗體" and "描邊" in d.action for d in o.degradations)
    pages, others = svg.unpack(o, store)
    root = svg.parse(pages[0])
    assert not list(root.iter(Q + "text"))
    paths = [p for p in root.iter(Q + "path") if p.get("data-text") is not None]
    assert "".join(p.get("data-text") for p in group(root, "p001-box-title")) == "Vector 向量圖"
    assert all(p.get("d").startswith("M") and p.get("fill", "").startswith("#") for p in paths)
    cjk = [p for p in group(root, "p001-box-title") if p.get("data-text") == "向量圖"][0]
    assert cjk.get("stroke") == cjk.get("fill") and float(cjk.get("stroke-width")) == pytest.approx(24 * 0.03)  # 文泉驛沒有粗體檔：描邊模擬
    # 外框畫出來與文字版幾乎一樣（字形位置都由 harfbuzz 排字決定）
    text_pages, _ = svg.unpack(out, store)
    a = resvg_page(text_pages[0], {}, 1800)
    b = resvg_page(pages[0], others, 1800)
    assert similarity(a, b) >= 0.997 and diff_ratio(a, b) < 0.002
    # 目標驗證：文字內容以 data-text 比對，沒有可讀回的位置
    fr = svg_target.verify(o, [], [], Layout(pages=[]), doc, cfg, store)
    assert fr.ok and fr.pages[0].text_match_ratio == 1.0 and fr.pages[0].position_error_pt is None
    assert any("外框" in n for n in fr.notes)


# ---------------------------------------------------------------- 單一向量圖與 svg_assets


def test_vector_svg_standalone(doc):
    vec = doc.pages[0].vectors[0]
    text = svg.vector_svg(vec, Config.load())
    problems, root = svg.check(text.encode("utf-8"))
    assert not problems
    assert root.get("viewBox") == "0 0 240 200" and root.get("width") == "240pt" and root.get("height") == "200pt"
    g = root.find(Q + "g")
    assert g.get("id") == "p001-vec-logo" and g.get("data-origin") == "pdf_paths" and len(g) == 10
    assert (g[0].get("x"), g[0].get("y")) == ("0", "0")  # 座標相對於圖形外框
    assert root.find(Q + "defs").find(Q + "linearGradient").get("x2") == "240"
    rs, _ = svg_target.engine()
    im = svg_target.render(rs, text, 960, svg_target.font_book())
    assert im.size[0] == 960 and abs(im.size[1] - 800) <= 1
    assert near(pixel(im, 180, 60, 240.0), (255, 240, 80), 10) and near(pixel(im, 200, 120, 240.0), (20, 30, 70))


def test_svg_assets_zip(doc, store):
    o = svg_assets.export(doc, "hand", Config.load(), store)
    assert o.format == OutputFormat.SVG_ASSETS and o.selfcheck_ok, o.selfcheck_notes
    assert o.filename == "hand_vectors.zip" and o.fonts_used == []
    assert any(d.concept == "向量圖以外的內容" for d in o.degradations)
    with zipfile.ZipFile(store.blob_path(o.file)) as z:
        names = z.namelist()
        manifest = json.loads(z.read("manifest.json").decode("utf-8"))
        logo = z.read("page-001-p001-vec-logo.svg")
    assert names == ["manifest.json", "page-001-p001-vec-logo.svg", "page-001-p001-vec-mark.svg"]  # 母片上的標記只輸出一次
    assert manifest["count"] == 2 and manifest["unit"] == "pt"
    a, b = manifest["items"]
    assert a["file"] == "page-001-p001-vec-logo.svg" and a["element_id"] == "p001-vec-logo" and a["image_id"] == "p001-img-logo"
    assert a["origin"] == "pdf_paths" and a["origin_label"] == "精確擷取" and a["render_error"] is None and a["confidence"] == 1.0
    assert a["paths"] == 10 and a["paths_in_document"] == 11 and a["primitives"] == 7 and a["gradients"] == 2
    assert a["page"] == 1 and a["box_pt"] == {"x": 320.0, "y": 40.0, "w": 240.0, "h": 200.0} and a["page_size_pt"] == {"w": 600.0, "h": 400.0}
    assert b["origin"] == "traced" and b["origin_label"] == "描圖重建" and b["confidence"] == 0.82 and b["render_error"] == 2.5
    assert b["master"] == "master-1" and b["also_on_pages"] == [2] and b["kind"] == "icon"
    problems, root = svg.check(logo)
    assert not problems and root.get("viewBox") == "0 0 240 200" and a["bytes"] == len(logo)
    assert s6_export.run(doc, OutputFormat.SVG_ASSETS, "hand", Config.load(), store).file.sha256 == o.file.sha256


def test_svg_assets_without_vectors(doc, store):
    page = doc.pages[0].model_copy(update={"vectors": [], "images": [im.model_copy(update={"vector_id": None}) for im in doc.pages[0].images]})
    plain = doc.model_copy(update={"pages": [page], "masters": []})
    o = svg_assets.export(plain, "plain", Config.load(), store)
    assert o.selfcheck_ok and any("沒有向量圖" in n for n in o.selfcheck_notes)
    with zipfile.ZipFile(store.blob_path(o.file)) as z:
        assert z.namelist() == ["README.txt", "manifest.json"]
        assert "沒有可以輸出的向量圖" in z.read("README.txt").decode("utf-8")
        assert json.loads(z.read("manifest.json"))["items"] == []


# ---------------------------------------------------------------- 寫出後的安全檢查


def test_no_scripts_styles_or_external_references(out, store):
    pages, _ = svg.unpack(out, store)
    for data in pages.values():
        low = data.lower()
        for bad in (b"<script", b"<style", b" style=", b" class=", b"<foreignobject", b"<!doctype", b"<!entity", b"http://www.w3.org/1999/xhtml", b"javascript:", b"<use", b"<a "):
            assert bad not in low
        root = svg.parse(data)
        assert {svg._local(el.tag) for el in root.iter()} <= svg.ALLOWED_ELEMENTS
        assert not [k for el in root.iter() for k in el.attrib if svg._local(k).lower().startswith("on")]
        hrefs = [v for el in root.iter() for k, v in el.attrib.items() if svg._local(k) == "href"]
        assert all(v.startswith("data:image/png;base64,") for v in hrefs)
        assert len(hrefs) == (1 if root.get("data-page") == "1" else 0)  # 第一頁有一張點陣圖；帶 vector_id 的圖片都改畫向量圖


def test_check_rejects_unsafe_svg():
    def wrap(body: str, head: str = "") -> bytes:
        return f'{head}<svg xmlns="{svg.SVG_NS}" xmlns:xlink="{svg.XLINK_NS}" viewBox="0 0 10 10">{body}</svg>'.encode("utf-8")

    assert svg.check(wrap('<g id="a"><rect width="1" height="1" fill="#000000"/></g>'))[0] == []
    cases = {
        "script": wrap("<script>alert(1)</script>"),
        "事件屬性": wrap('<rect width="1" height="1" onclick="x()"/>'),
        "style 屬性": wrap('<rect width="1" height="1" style="fill:red"/>'),
        "foreignObject": wrap("<foreignObject/>"),
        "連結": wrap('<a xlink:href="https://example.com"><rect width="1" height="1"/></a>'),
        "外部圖片": wrap('<image xlink:href="https://example.com/a.png" width="1" height="1"/>'),
        "本機圖片": wrap('<image xlink:href="/etc/hostname" width="1" height="1"/>'),
        "外部 url": wrap('<rect width="1" height="1" fill="url(other.svg#g)"/>'),
        "不存在的漸層": wrap('<rect width="1" height="1" fill="url(#nope)"/>'),
        "重複的 id": wrap('<g id="a"/><g id="a"/>'),
        "不合法的 id": wrap('<g id="a_b"/>'),
        "DOCTYPE": wrap("<g/>", '<!DOCTYPE svg [<!ENTITY x "y">]>'),
        "use": wrap('<g id="a"/><use xlink:href="#a"/>'),
        "不是 XML": b"<svg",
    }
    for name, data in cases.items():
        assert svg.check(data)[0], name
    ok, notes = svg.selfcheck(wrap('<text x="0" y="5">abc</text>'), ["abd"], False)
    assert not ok and any("文字不符" in n for n in notes)
    ok, notes = svg.selfcheck(wrap('<text x="0" y="5">abc</text>'), ["a", "bc"], False)
    assert not ok and any("讀回 1" in n for n in notes)


def test_helpers():
    assert [svg.num(v) for v in (1.0, 1.005, -0.001, 12.3456, 1e-9)] == ["1", "1", "0", "12.35", "0"]
    ids = svg._Ids()
    assert [ids.make(v) for v in ("p001-box-1", "p001_box 1", "p001-box-1", "001", "", "表")] == ["p001-box-1", "p001-box-1-2", "p001-box-1-3", "m-001", "m", "m-2"]
    names = ["Noto Sans TC", "Inter", "It's", "微軟正黑體", "sans-serif"]
    attr = svg.family_attr(names)
    assert attr == "'Noto Sans TC', Inter, 'It\\'s', '微軟正黑體', sans-serif" and svg.parse_family(attr) == names
    assert svg.split_weight("Inter SemiBold") == ("Inter", 600) and svg.split_weight("Arial Black") == ("Arial", 900) and svg.split_weight("Arial") == (None, None)
    assert svg.generic_family("PMingLiU") == "serif" and svg.generic_family("Microsoft JhengHei") == "sans-serif" and svg.generic_family("DejaVu Sans Mono") == "monospace"
    assert svg.esc_text("a<b>&\x00\x0b") == "a&lt;b&gt;&amp;" and svg.esc_attr('"\n') == "&quot;&#10;"
    m = svg._matrix("rotate(90 10 10)")
    assert (m[0] * 20 + m[2] * 10 + m[4], m[1] * 20 + m[3] * 10 + m[5]) == pytest.approx((10.0, 20.0))


def test_substituted_and_legacy_family_names(store):
    page = StyledPage(page_index=0, width_pt=300.0, height_pt=100.0, text_boxes=[
        tbox("b1", 10, 10, 280, 20, para("p1", run("Calibri text", "Calibri", 14, verify="Carlito"))),
        tbox("b2", 10, 40, 280, 20, para("p2", run("Semi", "Inter SemiBold", 14))),
    ])
    d = StyledDoc(target_mode=measured(TargetMode.CROSS_SOURCE, 0.5, Provenance.INFERRED, W), source_app=measured("unknown", 0.0, Provenance.INFERRED, W), pages=[page])
    o = svg.export(d, "fam", Config.load(), store)
    root = svg.parse(store.read_blob(o.file))
    t1, t2 = group(root, "b1")[0], group(root, "b2")[0]
    assert t1.get("font-family") == "Calibri, Carlito, sans-serif"  # 推論的字型在前，伺服器上實際量行寬的字型當後備
    assert t2.get("font-family") == "'Inter SemiBold', Inter, sans-serif" and t2.get("font-weight") == "600"
    assert o.fonts_used == ["Calibri", "Inter SemiBold"]
    assert any(x.concept == "字型" and "Calibri" in x.action and "Carlito" in x.action for x in o.degradations)
    fr = svg_target.verify(o, [], [], Layout(pages=[]), d, Config.load(), store)
    assert fr.ok and fr.pages[0].position_error_pt <= 0.05
    codes = {(i.code, i.element_id) for i in fr.pages[0].issues}
    assert ("font_substituted", "b1") in codes and ("font_substituted", "b2") in codes  # 字型對不上要自己擋，不能靠渲染結果
    assert any("舊式家族名" in i.message for i in fr.pages[0].issues)


# ---------------------------------------------------------------- 目標驗證


def _spec_for(doc: StyledDoc, store: Store, dpi: float = 100.0) -> list[PageSpec]:
    """拿中性渲染當工作影像，替手工建的成品文件做出頁面規格。"""
    specs = []
    for page, im in zip(doc.pages, neutral_pages(doc, store, dpi)):
        buf = io.BytesIO()
        im.save(buf, "PNG")
        w, h = page.width_pt, page.height_pt
        specs.append(PageSpec(
            page_index=page.page_index, source_corners=[Point(x=0, y=0), Point(x=w, y=0), Point(x=w, y=h), Point(x=0, y=h)], aspect_ratio=h / w,
            source_to_page=[1 / w, 0, 0, 0, 1 / w, 0, 0, 0, 1], working_image=store.put_blob(buf.getvalue(), "image/png", "png"), working_image_size=im.size,
            page_width_pt=measured(w, 1.0, Provenance.EXACT, W), effective_resolution=dpi / 72.0,
        ))
    return specs


def test_verify_hand_built_doc(doc, out, store):
    fr = svg_target.verify(out, _spec_for(doc, store), [], Layout(pages=[]), doc, Config.load(), store)
    assert fr.ok and fr.format == OutputFormat.SVG, [(i.code, i.message) for p in fr.pages for i in p.issues]
    assert [p.text_match_ratio for p in fr.pages] == [1.0, 1.0]
    assert all(p.position_error_pt <= 0.05 and p.similarity >= 0.99 for p in fr.pages)
    codes = {i.code for p in fr.pages for i in p.issues}
    assert "font_bold_missing" in codes and not ({"low_similarity", "font_missing"} & codes)
    assert any("tspan 補上 y" in n for n in fr.notes) and fr.render is not None


def _tampered(out: OutputFile, store: Store, edit) -> OutputFile:
    pages, others = svg.unpack(out, store)
    files = {svg.page_name(i): (edit(data) if i == 0 else data) for i, data in pages.items()}
    files.update(others)
    return out.model_copy(update={"file": store.put_blob(svg.zip_bytes(files), "application/zip", "zip")})


def test_verify_catches_broken_files(doc, out, store):
    cfg = Config.load()

    def issues(o: OutputFile) -> tuple[bool, set[str]]:
        fr = svg_target.verify(o, [], [], Layout(pages=[]), doc, cfg, store)
        return fr.ok, {i.code for p in fr.pages for i in p.issues if i.severity == Severity.ERROR}

    assert issues(out) == (True, set())
    assert issues(_tampered(out, store, lambda d: d.replace(b"two  spaces", b"two  spices"))) == (False, {"text_mismatch"})
    x = re.search(rb'<text xml:space="preserve" x="([0-9.]+)"', svg.unpack(out, store)[0][0]).group(1)
    assert issues(_tampered(out, store, lambda d: d.replace(b'x="' + x + b'"', b'x="' + str(float(x) + 3).encode() + b'"', 1))) == (False, {"position_offset"})
    assert issues(_tampered(out, store, lambda d: d.replace(b"</svg>", b"<script>alert(1)</script></svg>"))) == (False, {"svg_structure"})
    assert issues(_tampered(out, store, lambda d: re.sub(rb'<g id="p001-box-center".*?</g>\n', b"", d, flags=re.S))) == (False, {"text_missing", "line_count_mismatch"})
    one_page = out.model_copy(update={"file": store.put_blob(svg.zip_bytes({"page-001.svg": svg.unpack(out, store)[0][0]}), "application/zip", "zip")})
    assert issues(one_page) == (False, {"page_missing"})
    # 置中的行沒有依字距微調修正：讀回的位置看不出來，要靠「用實際字型量行寬」才抓得到
    plain = svg.export(doc, "plain", cfg_with({"svg_kerning_aware": False}), store)
    fr = svg_target.verify(plain, [], [], Layout(pages=[]), doc, cfg, store)
    assert not fr.ok and fr.pages[0].position_error_pt > 1.5


def test_verify_falls_back_to_structure_only(doc, out, store, monkeypatch):
    monkeypatch.setattr(svg_target, "engine", lambda: (None, "只做結構檢查與文字讀回（lxml）；resvg-py 無法載入（測試），沒有渲染比對"))
    fr = svg_target.verify(out, _spec_for(doc, store), [], Layout(pages=[]), doc, Config.load(), store)
    assert fr.ok and "只做結構檢查" in fr.engine and fr.render is None
    assert all(p.similarity is None and p.text_match_ratio == 1.0 and p.position_error_pt <= 0.05 for p in fr.pages)
    assert any("退回只做結構檢查" in n for n in fr.notes)
    monkeypatch.undo()

    class Broken:
        @staticmethod
        def svg_to_bytes(**kw):
            raise ValueError("SVG has an invalid size")

    monkeypatch.setattr(svg_target, "engine", lambda: (Broken, "resvg-py（測試用，會失敗）"))
    fr = svg_target.verify(out, _spec_for(doc, store), [], Layout(pages=[]), doc, Config.load(), store)
    assert fr.ok and all(p.similarity is None for p in fr.pages) and sum("渲染失敗" in n for n in fr.notes) == 2
    off = svg_target.verify(out, [], [], Layout(pages=[]), doc, cfg_with(verify={"svg_render": False}), store)
    assert off.ok and off.render is None and any("svg_render" in n for n in off.notes)


def test_font_book_matches_like_resvg():
    book = svg_target.font_book()
    info, used, generic = book.resolve(["Nope", "Liberation Sans", "sans-serif"], 700, False)
    assert used == "Liberation Sans" and not generic and info.weight == 700 and not info.italic
    info, used, generic = book.resolve(["Liberation Serif"], 400, True)
    assert info.italic and info.weight == 400
    assert book.resolve(["liberation sans"], 400, False)[0] is None  # resvg 的家族名稱比對分大小寫
    info, used, generic = book.resolve(["Nope", "serif"], 400, False)
    assert generic and used == book.generic["serif"]
    info, _, _ = book.resolve(["WenQuanYi Zen Hei"], 700, False)
    assert info.weight < 600  # 文泉驛正黑沒有粗體檔
    assert "文泉驛正黑" in book.by_family and "Inter SemiBold" in book.legacy and "Inter SemiBold" not in book.by_family


# ---------------------------------------------------------------- Chromium 交叉比對（有才做）

_SHOT_JS = """
const { chromium } = require('playwright');
(async () => {
  const [w, h, ...files] = process.argv.slice(2);
  const browser = await chromium.launch();
  const ctx = await browser.newContext({ viewport: { width: +w, height: +h }, deviceScaleFactor: 2, javaScriptEnabled: false });
  const page = await ctx.newPage();
  for (let i = 0; i < files.length; i += 2) {
    await page.goto('file://' + files[i]);
    await page.screenshot({ path: files[i + 1] });
  }
  await browser.close();
})().catch(e => { console.error(e); process.exit(1); });
"""


def _chromium_ready() -> bool:
    if shutil.which("node") is None:
        return False
    try:
        return subprocess.run(["node", "-e", "require('playwright')"], cwd=REPO, capture_output=True, timeout=60).returncode == 0
    except Exception:
        return False


def test_chromium_cross_check(doc, out, store, tmp_path):
    """同一份 SVG 交給 Chromium 畫（原檔，不做任何調整），與 resvg 的結果比對。只當第二意見，正式流程不依賴它。"""
    if not _chromium_ready():
        pytest.skip("這台機器沒有 Node 版的 Playwright 與 Chromium")
    pages, others = svg.unpack(out, store)
    script = tmp_path / "shot.js"
    script.write_text(_SHOT_JS, encoding="utf-8")
    args = []
    for i, data in pages.items():
        (tmp_path / f"p{i}.svg").write_bytes(data)
        args += [str(tmp_path / f"p{i}.svg"), str(tmp_path / f"p{i}.png")]
    r = subprocess.run(["node", str(script), "800", "534", *args], cwd=REPO, capture_output=True, text=True, timeout=180)  # 視窗 800 × 534 CSS px，裝置像素比 2
    if r.returncode != 0:
        pytest.skip(f"Chromium 無法啟動：{r.stderr[-200:]}")
    for i, data in pages.items():
        chrome = Image.open(tmp_path / f"p{i}.png").convert("RGB")
        assert chrome.size == (1600, 1068)  # 600pt × 400pt 在瀏覽器裡是 800 × 533.3 px（這裡兩倍取樣）：帶 pt 單位的實體尺寸正確
        chrome = chrome.crop((0, 0, 1600, 1066))
        mine = resvg_page(data, others, 1600, emulate_bold=False).crop((0, 0, 1600, 1066))
        assert similarity(chrome, mine) >= 0.995
        # 兩個渲染器只差在邊緣的反鋸齒；中英混排、置中、靠右、左右對齊、旋轉的行位置都一致
        assert diff_ratio(chrome, mine, 96) < 0.0015
        if i == 0:
            assert diff_ratio(chrome, mine, 96, box=(30 / 600, 25 / 400, 300 / 600, 70 / 400)) < 0.008  # 中英混排的標題
            assert diff_ratio(chrome, mine, 96, box=(320 / 600, 40 / 400, 560 / 600, 240 / 400)) < 0.0005  # 向量圖


def test_resvg_needs_tspan_y_for_mixed_script_lines(out, store):
    """resvg 0.48 的行為（升版時這個測試若失敗，代表它改了，svg_target.render_copy 補 y 的調整可以重新檢討）：
    同一行裡中文與拉丁字分屬不同字型的 tspan，不補 y 時拉丁字會被換成中文字型的字形；補了 y 才逐片段選字型。"""
    pages, others = svg.unpack(out, store)
    rs, _ = svg_target.engine()
    book = svg_target.font_book()
    raw = svg_target.render(rs, pages[0].decode("utf-8"), 1600, book)
    fixed = resvg_page(pages[0], others, 1600, emulate_bold=False)
    title = (30 / 600, 25 / 400, 300 / 600, 70 / 400)
    assert diff_ratio(raw, fixed, 96, box=title) > 0.03  # 標題「Vector 向量圖」：整行被換成文泉驛
    assert diff_ratio(raw, fixed, 96, box=(30 / 600, 75 / 400, 300 / 600, 140 / 400)) == 0.0  # 只有一種字型的行不受影響
    # 輸出檔直接寫 y（svg_tspan_y）時，不必調整就畫得對
    o = svg.export(build_doc(store), "ty", cfg_with({"svg_tspan_y": True}), store)
    direct = svg_target.render(rs, svg.unpack(o, store)[0][0].decode("utf-8"), 1600, book)
    assert diff_ratio(direct, fixed, 96, box=title) == 0.0
