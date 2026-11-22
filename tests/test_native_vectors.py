"""原生 PDF 裡的向量圖：精確讀出、分群、對應的圖片、底圖，以及「重新畫出來」與原檔的比對。

三個 fixture（tests/fixtures/make_fixtures.py 的 vector_logo、vector_paint、vector_outlined）的內容都是已知的，
這裡逐項對答案。只跑①②③（不必整條流水線），所以很快。
"""

from __future__ import annotations

import io
import math
from collections import Counter
from pathlib import Path

import numpy as np
import pytest
from PIL import Image

from mortise.artifacts import ElementList, ImageKind, Intent, PageSpec, Provenance, VectorGraphic
from mortise.config import Config
from mortise.pipeline.store import Store
from mortise.stages import s1_input, s2_page, s3_recognize
from mortise.tools import pdfium as pdf

FIX = Path(__file__).parent / "fixtures"
A4_W, A4_H = 595.276, 841.89


# ---------------------------------------------------------------- 共用工具


def _recognize(job_dir: Path, source: Path, cfg: Config | None = None) -> tuple[Store, list[tuple[PageSpec, ElementList]]]:
    """只跑①輸入、②頁面、③辨識。"""
    store = Store(job_dir)
    cfg = cfg or Config.load()
    doc = s1_input.run(source, source.name, Intent(), cfg, store)
    pages = []
    for ref in doc.pages:
        spec = s2_page.run(doc, ref.page_index, cfg, [], store)
        pages.append((spec, s3_recognize.run(doc, spec, cfg, [], store)))
    return store, pages


@pytest.fixture(scope="module")
def logo(tmp_path_factory):
    store, pages = _recognize(tmp_path_factory.mktemp("logo"), FIX / "vector_logo.pdf")
    return store, pages[0][1]


@pytest.fixture(scope="module")
def paint(tmp_path_factory):
    store, pages = _recognize(tmp_path_factory.mktemp("paint"), FIX / "vector_paint.pdf")
    return store, pages[0][1]


@pytest.fixture(scope="module")
def outlined(tmp_path_factory):
    store, pages = _recognize(tmp_path_factory.mktemp("outlined"), FIX / "vector_outlined.pdf")
    return store, [el for _, el in pages]


def _pt(x: float, y: float, page_w: float = A4_W, page_h: float = A4_H) -> tuple[float, float]:
    """PDF 座標（pt，原點左下）→ 頁面相對座標（頁寬為 1，原點左上）。"""
    return x / page_w, (page_h - y) / page_w


def _rgb(color) -> tuple[int, int, int]:
    return color.r, color.g, color.b


def _subpaths(path: list[list[float]]) -> int:
    return sum(1 for sg in path if sg[0] == 0)


def _by_fill(vg: VectorGraphic, hex_color: str):
    return next(p for p in vg.paths if p.fill is not None and p.fill.hex == hex_color)


def _by_stroke(vg: VectorGraphic, hex_color: str):
    return next(p for p in vg.paths if p.stroke is not None and p.stroke.hex == hex_color)


def _redraw(vg: VectorGraphic, size: tuple[int, int]) -> Image.Image:
    """只用 VectorGraphic 裡的資料把向量圖重新畫出來（ReportLab 寫成 PDF，再用 PDFium 轉成像素），透明底。
    與③無關的另一條路：畫得出一樣的圖，才表示幾何、填色規則、筆畫、不透明度、漸層都讀對了。"""
    from reportlab.lib.colors import Color
    from reportlab.pdfgen import canvas

    import pypdfium2

    x0, y0, x1, y1 = vg.quad.bbox
    w, h = size
    sx, sy = w / (x1 - x0), h / (y1 - y0)

    def X(x: float) -> float:
        return (x - x0) * sx

    def Y(y: float) -> float:
        return h - (y - y0) * sy

    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=(w, h))
    caps, joins = {"butt": 0, "round": 1, "square": 2}, {"miter": 0, "round": 1, "bevel": 2}
    for vp in vg.paths:
        p = c.beginPath()
        for sg in vp.path:
            if sg[0] == 0:
                p.moveTo(X(sg[1]), Y(sg[2]))
            elif sg[0] == 1:
                p.lineTo(X(sg[1]), Y(sg[2]))
            elif sg[0] == 2:
                p.curveTo(X(sg[1]), Y(sg[2]), X(sg[3]), Y(sg[4]), X(sg[5]), Y(sg[6]))
            else:
                p.close()
        mode = 0 if vp.fill_rule == "evenodd" else 1
        c.saveState()
        if vp.gradient is not None:
            g = vp.gradient
            colors = [Color(s.color.r / 255, s.color.g / 255, s.color.b / 255) for s in g.stops]
            offsets = [s.offset for s in g.stops]
            c.saveState()
            c.clipPath(p, stroke=0, fill=0, fillMode=mode)
            if g.kind == "linear":
                c.linearGradient(X(g.coords[0]), Y(g.coords[1]), X(g.coords[2]), Y(g.coords[3]), colors, offsets, extend=True)
            else:
                c.radialGradient(X(g.coords[0]), Y(g.coords[1]), g.coords[2] * sx, colors, offsets, extend=True)
            c.restoreState()
        elif vp.fill is not None:
            c.setFillColor(Color(vp.fill.r / 255, vp.fill.g / 255, vp.fill.b / 255, alpha=vp.fill.a))
        if vp.stroke is not None:
            c.setStrokeColor(Color(vp.stroke.r / 255, vp.stroke.g / 255, vp.stroke.b / 255, alpha=vp.stroke.a))
            c.setLineWidth(vp.stroke_width * sx)
            c.setLineCap(caps[vp.stroke_cap])
            c.setLineJoin(joins[vp.stroke_join])
        fills = vp.fill is not None and vp.gradient is None
        if fills or vp.stroke is not None:
            c.drawPath(p, stroke=int(vp.stroke is not None), fill=int(fills), fillMode=mode)
        c.restoreState()
    c.showPage()
    c.save()
    page = pypdfium2.PdfDocument(buf.getvalue())[0]
    image = page.render(scale=1.0, fill_color=(0, 0, 0, 0), rev_byteorder=True).to_pil().convert("RGBA")
    assert image.size == (w, h)
    return image


def _on_white(image: Image.Image) -> np.ndarray:
    a = np.asarray(image.convert("RGBA")).astype(float)
    alpha = a[..., 3:4] / 255
    return a[..., :3] * alpha + 255 * (1 - alpha)


def _difference(a: Image.Image, b: Image.Image, solid: bool = False) -> tuple[float, float]:
    """兩張圖疊在白底上比：回傳（平均差，0 到 255；差超過 32 的像素比例）。
    solid 為真時只算「成片」的差異（3×3 的範圍內全部都差超過 32）：兩張圖的解析度不同、邊緣的反鋸齒本來就不會一樣。"""
    d = np.abs(_on_white(a) - _on_white(b)).max(axis=2)
    if solid:
        from scipy.ndimage import minimum_filter

        return float(d.mean()), float((minimum_filter(d, size=3) > 32).mean())
    return float(d.mean()), float((d > 32).mean())


def _image_of(store: Store, el: ElementList, vg: VectorGraphic) -> tuple[object, Image.Image]:
    element = next(im for im in el.images if im.id == vg.image_id)
    return element, Image.open(store.blob_path(element.image))


# ---------------------------------------------------------------- vector_logo：分群與屬性


def test_logo_page_groups(logo):
    """三個標誌各成一個向量圖；單獨的形狀、三個箭頭、表格、底線與文字框都維持原樣。"""
    _, el = logo
    assert [len(v.paths) for v in el.vectors] == [8, 6, 3]
    assert all(v.kind == "unknown" for v in el.vectors)
    assert Counter(s.geometry.value for s in el.shapes) == {"rect": 2, "rounded_rect": 1, "ellipse": 1, "line": 2, "path": 5}
    assert len(el.texts) == 13
    (table,) = el.table_regions
    assert (len(table.h_lines), len(table.v_lines)) == (4, 4)
    # 沒有東西留在底圖：全部都讀出來了
    assert el.background.image is None and el.background.color.hex == "FFFFFF"
    assert not any("留在底圖" in n for n in el.notes)
    # 三個箭頭（直線、條數少）是三個自訂路徑形狀，不是向量圖
    arrows = [s for s in el.shapes if s.geometry.value == "path" and abs(s.quad.center.y - _pt(0, 450)[1]) < 0.002]
    assert len(arrows) == 3 and {s.fill.hex for s in arrows} == {"1F3A5F", "2E86AB", "8EC07C"}
    # 底線與文字框：一條直線、一個只有邊框的矩形，都沒有被吸進向量圖
    underline = next(s for s in el.shapes if s.geometry.value == "line" and abs(s.quad.center.y - _pt(0, 381)[1]) < 1e-4)
    assert underline.quad.width == pytest.approx(121 / A4_W, abs=1e-4)
    frame = next(s for s in el.shapes if s.geometry.value == "rect" and s.fill is None)
    assert frame.stroke.hex == "2B2D42" and frame.quad.width == pytest.approx(150 / A4_W, abs=1e-4)
    for vg in el.vectors:  # 向量圖的範圍不碰到表格、底線、文字框
        l, t, r, b = vg.quad.bbox
        for other in (table.quad, underline.quad, frame.quad):
            ol, ot, o_r, ob = other.bbox
            assert r < ol or o_r < l or b < ot or ob < t


def test_vector_has_matching_image(logo):
    """每個向量圖都有一張對應的圖片：同位置、同圖層、透明底、精確擷取的插畫。"""
    store, el = logo
    for vg in el.vectors:
        assert vg.origin.value == "pdf_paths" and vg.origin.source == Provenance.EXACT and vg.origin.confidence == 1.0
        assert vg.origin.writer.startswith("s3.pdf_vectors@")
        assert vg.render_error is None and all(p.fit_error is None for p in vg.paths)  # 精確擷取，不是描圖
        element, bitmap = _image_of(store, el, vg)
        assert element.quad == vg.quad and element.z == vg.z
        assert element.kind.value == ImageKind.ILLUSTRATION and element.kind.source == Provenance.EXACT
        assert bitmap.mode == "RGBA" and bitmap.size == element.pixel_size
        alpha = np.asarray(bitmap)[..., 3]
        assert alpha[0, 0] == 0 and alpha.max() == 255  # 角落是透明的，圖形本身不透明
        # 解析度：設定是 300 dpi
        assert element.pixel_size[0] / (element.quad.width * A4_W / 72) == pytest.approx(300, rel=0.01)
    assert len({v.id for v in el.vectors}) == 3 and len(el.images) == 3


def test_logo_path_attributes(logo):
    """標誌 A 的八條路徑：填色規則、挖孔、筆畫寬度、線帽、接合、不透明度、虛線。"""
    _, el = logo
    vg = el.vectors[0]
    assert vg.quad.bbox[0] < _pt(70, 0)[0] and vg.quad.bbox[2] > _pt(190, 0)[0]

    shield = vg.paths[0]
    assert shield.fill.hex == "1F3A5F" and shield.stroke is None and _subpaths(shield.path) == 1
    assert shield.path[0][1:] == pytest.approx(list(_pt(70, 740)), abs=1e-6)  # 座標是精確的
    assert shield.path[1] == pytest.approx([2, *_pt(110, 750), *_pt(150, 750), *_pt(190, 740)], abs=1e-6)

    ring = _by_fill(vg, "F2C14E")  # 兩個同方向的圓，靠奇偶規則挖孔
    assert ring.fill_rule == "evenodd" and _subpaths(ring.path) == 2
    diamond = _by_fill(vg, "2E86AB")  # 內圈反方向，靠非零規則挖孔
    assert diamond.fill_rule == "nonzero" and _subpaths(diamond.path) == 2
    square = _by_fill(vg, "C8553D")  # 夾在標誌中間的小方塊：算進這一群，並標出它是矩形
    assert square.primitive.kind == "rect" and square.primitive.w == pytest.approx(12 / A4_W, abs=1e-6)
    assert (square.primitive.cx, square.primitive.cy) == pytest.approx(_pt(130, 690), abs=1e-6)

    wave = _by_stroke(vg, "E86A33")
    assert wave.fill is None and (wave.stroke_cap, wave.stroke_join) == ("round", "round")
    assert wave.stroke_width * A4_W == pytest.approx(4.0, abs=1e-4)
    zigzag = next(p for p in vg.paths if p.stroke is not None and p.stroke.hex == "2B2D42" and p.fill is None)
    assert (zigzag.stroke_cap, zigzag.stroke_join) == ("square", "bevel")
    assert zigzag.stroke_width * A4_W == pytest.approx(3.0, abs=1e-4)

    blob = _by_fill(vg, "8EC07C")  # 半透明：填色 50%、筆畫 80%
    assert blob.fill.a == pytest.approx(0.5, abs=0.01) and blob.stroke.a == pytest.approx(0.8, abs=0.01)
    assert blob.stroke_width * A4_W == pytest.approx(1.5, abs=1e-4) and (blob.stroke_cap, blob.stroke_join) == ("butt", "miter")

    dashed = _by_stroke(vg, "C8553D")  # 虛線展開成一段一段的實線，總長度約為原長的 6 ÷ (6 + 3)
    assert _subpaths(dashed.path) >= 10 and dashed.stroke_width * A4_W == pytest.approx(2.0, abs=1e-4)
    assert any("虛線" in n for n in vg.notes)


def test_nested_forms_accumulate_matrices(logo):
    """標誌 B 畫在兩層表單裡（外層轉 -15 度放大 1.2 倍，內層再轉 45 度縮成 0.8 倍）：矩陣要一路乘下來。"""
    _, el = logo
    vg = el.vectors[1]
    assert len(vg.paths) == 6
    cx, cy = _pt(430, 680)
    disc = vg.paths[0]
    assert disc.fill.hex == "141E30" and disc.primitive is None  # 不規則的圓盤：不可以被認成圓或圓角矩形
    petals = vg.paths[1:5]
    assert [p.fill.hex for p in petals] == ["F2C14E", "E86A33", "8EC07C", "2E86AB"]
    tips = []
    for p in petals:  # 花瓣的尖端離中心 40 × 0.8 × 1.2 = 38.4 pt
        ends = [(sg[-2], sg[-1]) for sg in p.path if sg[0] in (0, 1, 2)]
        far = max(ends, key=lambda q: math.hypot(q[0] - cx, q[1] - cy))
        assert math.hypot(far[0] - cx, far[1] - cy) * A4_W == pytest.approx(38.4, abs=0.01)
        tips.append(math.degrees(math.atan2(-(far[1] - cy), far[0] - cx)) % 360)
    assert tips == pytest.approx([30, 120, 210, 300], abs=0.01)  # -15 + 45 度起，每片再轉 90 度
    star = vg.paths[5]
    assert star.fill.hex == "FFFFFF" and star.stroke.hex == "C8553D" and star.stroke_join == "round"
    assert star.stroke_width * A4_W == pytest.approx(1.2, abs=1e-4)  # 筆畫寬度也跟著放大
    assert star.primitive.kind == "polygon" and len(star.primitive.points) == 10


def test_text_in_between_splits_the_group(logo):
    """標誌 C：底板、文字、蓋在文字上的裝飾。合成一群的話文字會被底板蓋住或跑到裝飾上面，所以要分開。"""
    _, el = logo
    word = next(t for t in el.texts if t.text == "MORTISE")
    plate = next(s for s in el.shapes if s.geometry.value == "path" and s.fill.hex == "F4EBD9")
    decoration = el.vectors[2]
    assert plate.z < word.z < decoration.z
    assert Counter((p.stroke.hex if p.stroke else None, p.fill.hex if p.fill else None) for p in decoration.paths) == {("C8553D", None): 2, (None, "F2C14E"): 1}


# ---------------------------------------------------------------- vector_paint：漸層與讀不出來的東西


def test_gradients_are_read(paint):
    _, el = paint
    assert [len(v.paths) for v in el.vectors] == [3, 1, 1, 1]
    p = lambda x, y: _pt(x, y, 600, 400)

    blob = el.vectors[0].paths[0]  # 花紋填色的線性漸層，花紋矩陣轉了 36.87 度
    assert blob.gradient.kind == "linear"
    assert blob.gradient.coords == pytest.approx([*p(70, 270), *p(150, 330)], abs=1e-6)
    assert [(s.offset, _rgb(s.color)) for s in blob.gradient.stops] == [(0.0, (242, 194, 79)), (1.0, (199, 84, 61))]
    assert _rgb(blob.fill) == (220, 139, 70)  # 平均色，給不支援漸層的輸出器
    assert any("漸層" in n for n in el.vectors[0].notes)

    disc = el.vectors[1].paths[0]  # sh 運算子畫的放射漸層，範圍是圓形的裁切區
    assert disc.gradient.kind == "radial" and disc.primitive.kind == "circle"
    assert disc.gradient.coords == pytest.approx([*p(300, 320), 40 / 600], abs=1e-6)
    assert [_rgb(s.color) for s in disc.gradient.stops] == [(255, 255, 255), (46, 135, 171)]

    band = el.vectors[2].paths[0]  # 三個色標（兩段接起來）
    assert band.primitive.kind == "rect" and band.gradient.coords == pytest.approx([*p(380, 0), *p(480, 0)], abs=1e-6)
    assert sorted({s.offset for s in band.gradient.stops}) == pytest.approx([0.0, 0.4, 1.0])

    house = el.vectors[3].paths[0]  # 表單裡的漸層：座標要跟著表單的矩陣移到頁面上
    assert house.gradient.coords == pytest.approx([*p(320, 70), *p(400, 70)], abs=1e-6)


def test_unreadable_objects_stay_in_background_with_reasons(paint):
    store, el = paint
    (note,) = [n for n in el.notes if "留在底圖" in n]
    assert note.startswith("有 11 個向量物件留在底圖中")
    for part in ("被裁切的圖形 3 個", "整組套用的透明效果 2 個", "整頁的漸層底 1 個", "讀不出參數的漸層 1 個（漸層的顏色不是直線變化）",
                 "花紋填色 1 個", "被上面留在底圖的物件蓋住 1 個", "混合模式 1 個", "透明遮罩 1 個"):
        assert part in note
    # 底圖：取出的東西不在裡面，留下的東西還在
    bg = np.asarray(Image.open(store.blob_path(el.background.image)).convert("RGB")).astype(int)
    h, w = bg.shape[:2]

    def pixel(x: float, y: float) -> np.ndarray:
        return bg[int((400 - y) / 400 * h), int(x / 600 * w)]

    def page_gradient(y: float) -> np.ndarray:  # 整頁的漸層底：由下到上
        lo, hi = np.array([0.96, 0.92, 0.85]), np.array([0.85, 0.9, 0.96])
        return 255 * (lo + (hi - lo) * y / 400)

    for x, y in ((125, 320), (300, 320), (430, 320), (360, 100), (275, 212), (545, 210), (90, 110)):  # 已取出的向量圖與形狀
        assert np.abs(pixel(x, y) - page_gradient(y)).max() <= 3, (x, y)
    # 非線性漸層、被混合模式蓋住的三角形、混合模式、透明遮罩、被裁切、透明群組、表單外的裁切
    for x, y in ((572, 320), (190, 190), (240, 215), (350, 200), (445, 205), (230, 100), (448, 85)):
        assert np.abs(pixel(x, y) - page_gradient(y)).max() > 40, (x, y)


def test_clips_that_can_be_resolved_exactly(paint):
    """矩形色塊被裁切：看得到的就是裁切區，直接寫成那個形狀。裁切框整個包住物件：當成沒有裁切。"""
    _, el = paint
    p = lambda x, y: _pt(x, y, 600, 400)
    assert Counter(s.geometry.value for s in el.shapes) == {"path": 4, "rect": 1}
    (rect,) = [s for s in el.shapes if s.geometry.value == "rect"]  # 矩形裁矩形 → 交集
    assert rect.quad.bbox == pytest.approx((*p(60, 120), *p(120, 100)), abs=1e-6) and rect.fill.hex == "2E87AB"
    star = next(s for s in el.shapes if s.fill.hex == "8FBF7D")  # 矩形被星形裁切 → 星形
    assert sum(1 for sg in star.path if sg[0] == 1) in (9, 10) and not any(sg[0] == 2 for sg in star.path)
    # 畫表單之前有一個轉了 30 度的矩形裁切框，整個包住表單的內容：兩條路徑照常取出
    kept = [s for s in el.shapes if s.quad.center.x > 500 / 600 and s.quad.center.y > p(0, 130)[1]]
    assert sorted(s.fill.hex for s in kept) == ["2E87AB", "F2C24F"]


# ---------------------------------------------------------------- vector_outlined：文字轉外框


def test_outlined_text_is_its_own_group(outlined):
    _, (first, second) = outlined
    # 第 1 頁：有真的文字。兩行（一字一條路徑）一群、整段畫成一條路徑的一群；四個字的 SALE 不夠認定，維持是形狀
    assert [(v.kind, len(v.paths)) for v in first.vectors] == [("outlined_text", 40), ("outlined_text", 1)]
    assert all(any("文字轉成的外框" in n for n in v.notes) for v in first.vectors)
    assert any("轉成外框的文字" in n for n in first.notes)
    assert "2 行、40 個字形" in first.vectors[0].notes[0] and "2 行、34 個字形" in first.vectors[1].notes[0]
    assert Counter(s.geometry.value for s in first.shapes) == {"ellipse": 1, "path": 12}  # 太陽（圓盤加八道光芒）、SALE 四個字
    sun = next(s for s in first.shapes if s.geometry.value == "ellipse")
    assert first.vectors[0].quad.bbox[2] < sun.quad.bbox[0]  # 緊貼行尾的插畫沒有混進文字群
    assert all(p.fill.hex == "2B2D42" and p.stroke is None for p in first.vectors[0].paths)
    assert len(first.texts) == 1 and first.background.image is None

    # 第 2 頁：完全沒有文字（①已起疑），四個字也認得；密集插畫（320 片葉子加一個 500 多段的鋸齒圓）不受數量上限影響
    assert [(v.kind, len(v.paths)) for v in second.vectors] == [("outlined_text", 4), ("unknown", 321)]
    assert second.shapes == [] and second.background.image is None and not any("留在底圖" in n for n in second.notes)
    assert max(len(p.path) for p in second.vectors[1].paths) > 500
    assert [v.z for v in second.vectors] == sorted(v.z for v in second.vectors)


def test_glyph_rows_work_on_vector_paths(outlined):
    """glyph_rows 可以直接吃向量圖的路徑（之後把外框變回文字的步驟會用到）。"""
    _, (first, _) = outlined
    cfg = Config.load().section("recognize")
    rows = sorted(s3_recognize.glyph_rows([p.path for p in first.vectors[0].paths], cfg), key=lambda r: r.baseline)
    assert [len(r.boxes) for r in rows] == [21, 19]  # Summer sale starts today／Riverside Park, 10:00（i 的點與冒號各算一個字形）
    # 基線：圓弧形的字母會略低於基線，容許 0.6 pt
    assert rows[0].baseline == pytest.approx(_pt(0, 700)[1], abs=1e-3) and rows[1].baseline == pytest.approx(_pt(0, 700 - 26 * 1.25)[1], abs=1e-3)
    assert rows[1].height * A4_W == pytest.approx(26 * 0.72, rel=0.08)  # 第二行多半是大寫與數字：字高約為字級的 0.72 倍
    assert len(rows[1].pieces) == 20  # 冒號的兩點原本是兩塊，併成一個字形
    left_to_right = [b[0] for b in rows[0].boxes]
    assert left_to_right == sorted(left_to_right) and all(o == i for i, o in enumerate(rows[0].owners))  # 一字一條路徑，依序對應
    whole = sorted(s3_recognize.glyph_rows([p.path for p in first.vectors[1].paths], cfg), key=lambda r: r.baseline)
    assert [len(r.boxes) for r in whole] == [19, 15]  # 兩行畫成同一條路徑：照樣分得出行與字形（Whole paragraph drawn／as one single path）
    assert whole[1].baseline - whole[0].baseline == pytest.approx(18 * 1.25 / A4_W, abs=1e-3)


# ---------------------------------------------------------------- 重新畫出來與原檔比較


def test_redrawn_vectors_match_their_images(logo, paint, outlined):
    """把每個向量圖重新畫出來，與「原檔只畫這一群物件」的圖片逐像素比較。"""
    cases = [(logo[0], logo[1]), (paint[0], paint[1])] + [(outlined[0], el) for el in outlined[1]]
    checked = 0
    for store, el in cases:
        for vg in el.vectors:
            element, original = _image_of(store, el, vg)
            mean, bad = _difference(original, _redraw(vg, element.pixel_size))
            assert mean < 1.0 and bad < 0.001, (vg.id, mean, bad)
            checked += 1
    assert checked == 11


def test_redrawn_logo_matches_the_original_page(logo):
    """標誌 A、B 附近沒有別的東西：重畫的結果直接與原頁的渲染比（不經過③的圖片）。"""
    _, el = logo
    doc = pdf.open_document(str(FIX / "vector_logo.pdf"))
    reader = pdf.PageReader(doc, 0)
    try:
        dpi = 200.0
        page = reader.render(dpi)
    finally:
        reader.close()
        doc.close()
    for vg in el.vectors[:2]:
        l, t, r, b = vg.quad.bbox
        box = (round(l * page.width), round(t * page.width), round(r * page.width), round(b * page.width))
        crop = page.crop(box)
        fitted = vg.model_copy(update={"quad": vg.quad.from_rect(box[0] / page.width, box[1] / page.width, box[2] / page.width, box[3] / page.width)})
        mean, bad = _difference(crop, _redraw(fitted, crop.size))
        assert mean < 1.0 and bad < 0.002, (vg.id, mean, bad)


def test_background_plus_vectors_equals_original(paint):
    """底圖疊上各個向量圖的圖片，要等於「原頁隱藏文字與形狀」的渲染：取出的沒有漏、沒有重複、前後順序對。"""
    store, el = paint
    bg = Image.open(store.blob_path(el.background.image)).convert("RGBA")
    for vg in sorted(el.vectors, key=lambda v: v.z):
        _, bitmap = _image_of(store, el, vg)
        l, t, r, b = vg.quad.bbox
        box = [round(v * bg.width) for v in (l, t, r, b)]
        layer = Image.new("RGBA", bg.size, (0, 0, 0, 0))
        layer.paste(bitmap.resize((box[2] - box[0], box[3] - box[1]), Image.LANCZOS), (box[0], box[1]))
        bg = Image.alpha_composite(bg, layer)
    doc = pdf.open_document(str(FIX / "vector_paint.pdf"))
    reader = pdf.PageReader(doc, 0)
    try:
        expected = reader.render(bg.width / (600 / 72.0), hide_text=True, hide_z={s.z for s in el.shapes})
    finally:
        reader.close()
        doc.close()
    assert expected.size == bg.size
    mean, bad = _difference(expected, bg, solid=True)  # 向量圖的圖片是 300 dpi 縮下來的，邊緣一個像素的差異不算
    assert mean < 0.5 and bad < 0.0002, (mean, bad)


# ---------------------------------------------------------------- 既有 fixture 不受影響


@pytest.mark.parametrize("name,expected", [
    ("slide_basic", [{"rect": 1}]),
    ("slide_dark", [{"rect": 3, "ellipse": 1, "line": 1}]),
    ("poster", [{"rect": 1, "rounded_rect": 1, "line": 1}]),
    ("doc_a4", [{}, {}]),
    ("deck", [{"rect": 1}] * 3),
])
def test_existing_fixtures_have_no_vectors(tmp_path, name, expected):
    """既有的五個 fixture 只有簡單形狀：不可以冒出向量圖，形狀的種類與數量照舊，也沒有東西留在底圖。"""
    _, pages = _recognize(tmp_path, FIX / f"{name}.pdf")
    assert [dict(Counter(s.geometry.value for s in el.shapes)) for _, el in pages] == expected
    for _, el in pages:
        assert el.vectors == [] and el.background.image is None
        assert not any(im.kind.value == ImageKind.ILLUSTRATION for im in el.images)


# ---------------------------------------------------------------- 一整頁密密麻麻的圖形


def test_dense_page_is_merged_into_layers(tmp_path):
    """幾百個小圖形散佈整頁（地圖、圖示牆）：不可以分成上百個向量圖，要依繪製順序合併成幾層，
    只在文字夾在中間的地方切開；一排一模一樣的圖示不可以被當成文字轉外框。"""
    import random

    from reportlab.lib.colors import HexColor
    from reportlab.pdfgen import canvas

    rng = random.Random(7)
    path = tmp_path / "dense.pdf"
    c = canvas.Canvas(str(path), pagesize=(842, 595), invariant=1)

    def leaf(x: float, y: float, color: str) -> None:
        c.setFillColor(HexColor(color))
        p = c.beginPath()
        p.moveTo(x, y)
        p.curveTo(x + 3, y + 4, x + 6, y + 4, x + 8, y)
        p.curveTo(x + 6, y - 3, x + 3, y - 3, x, y)
        p.close()
        c.drawPath(p, stroke=0, fill=1)

    palette = ["#2E86AB", "#F2C14E", "#E86A33", "#8EC07C", "#1F3A5F"]
    for i in range(400):
        leaf(rng.uniform(20, 800), rng.uniform(20, 480), palette[i % 5])
        if i in (100, 200, 300):  # 文字夾在中間，後面緊接著一片葉子蓋在它上面
            x, y = 100 + i, 150 + i
            c.setFillColor(HexColor("#000000"))
            c.setFont("Helvetica", 12)
            c.drawString(x, y, f"Label {i}")
            leaf(x + 10, y + 3, "#C8553D")
    c.setFillColor(HexColor("#2B2D42"))
    for k in range(8):  # 一排八個一模一樣的小三角形
        p = c.beginPath()
        p.moveTo(100 + 14 * k, 540)
        p.lineTo(110 + 14 * k, 546)
        p.lineTo(100 + 14 * k, 552)
        p.close()
        c.drawPath(p, stroke=0, fill=1)
    c.showPage()
    c.save()
    override = tmp_path / "override.toml"
    override.write_text("[recognize]\npdf_vector_max_groups = 10\n", encoding="utf-8")

    store, pages = _recognize(tmp_path / "job", path, Config.load(override))
    el = pages[0][1]
    assert len(el.vectors) == 4 and all(v.kind == "unknown" for v in el.vectors)  # 三段文字把它切成四層
    assert sum(len(v.paths) for v in el.vectors) == 411 and el.shapes == []
    assert all(any("依繪製順序合成" in n for n in v.notes) for v in el.vectors)
    labels = sorted(t.z for t in el.texts)
    layers = [v.z for v in el.vectors]
    assert len(labels) == 3 and layers[0] < labels[0] < layers[1] < labels[1] < layers[2] < labels[2] < layers[3]
    # 圖片的像素總量有上限（四層都接近整頁大小）
    images = {im.id: im for im in el.images}
    assert sum(images[v.image_id].pixel_size[0] * images[v.image_id].pixel_size[1] for v in el.vectors) <= 24_000_000 * 1.05
    # 四層依序疊起來，等於原頁隱藏文字的樣子
    doc = pdf.open_document(str(path))
    reader = pdf.PageReader(doc, 0)
    try:
        expected = reader.render(100.0, hide_text=True)
    finally:
        reader.close()
        doc.close()
    page = Image.new("RGBA", expected.size, (255, 255, 255, 255))
    for vg in el.vectors:
        l, t, r, b = vg.quad.bbox
        box = [round(v * page.width) for v in (l, t, r, b)]
        fitted = vg.model_copy(update={"quad": vg.quad.from_rect(*(v / page.width for v in box))})
        layer = Image.new("RGBA", page.size, (0, 0, 0, 0))
        layer.paste(_redraw(fitted, (box[2] - box[0], box[3] - box[1])), (box[0], box[1]))
        page = Image.alpha_composite(page, layer)
    mean, bad = _difference(expected, page, solid=True)  # 各層分開畫再疊，邊緣一個像素的反鋸齒會略有不同，不算
    assert mean < 0.5 and bad < 0.0002, (mean, bad)


# ---------------------------------------------------------------- 讀取層的細節


def _hand_written_pdf(path: Path, content: bytes, resources=None, size=(300, 200)) -> Path:
    import pikepdf

    out = pikepdf.new()
    page = out.add_blank_page(page_size=size)
    if resources is not None:
        page.Resources = resources(out)
    page.Contents = pikepdf.Stream(out, content)
    out.save(path)
    return path


def test_clip_on_a_form_applies_to_its_contents(tmp_path):
    """畫表單之前設的裁切，PDFium 只記在表單物件上，不會抄到表單裡的路徑：要自己往外層找。"""
    import pikepdf
    from pikepdf import Dictionary, Name, Stream

    def resources(out):
        form = Stream(out, b"1 0 0 rg 0 0 m 100 0 l 100 100 l 0 100 l h f 0 0 1 rg 20 20 m 60 20 l 40 60 l h f",
                      Type=Name.XObject, Subtype=Name.Form, BBox=[-10, -10, 110, 110])
        return Dictionary(XObject=Dictionary(Fm=out.make_indirect(form)))

    # 左：被三角形切到。右：裁切框是轉了 45 度的大正方形，整個包住表單，沒有影響
    path = _hand_written_pdf(tmp_path / "clip.pdf", b"q 20 50 m 120 50 l 70 150 l h W n 1 0 0 1 20 50 cm /Fm Do Q "
                             b"q 220 -20 m 370 130 l 220 280 l 70 130 l h W n 1 0 0 1 170 50 cm /Fm Do Q", resources)
    doc = pdf.open_document(str(path))
    reader = pdf.PageReader(doc, 0, source_path=str(path))
    try:
        paths = reader.paths()
    finally:
        reader.close()
        doc.close()
    assert [bool(p.clip) for p in paths] == [True, True, False, False]
    assert paths[0].forms == (0,) and len(paths[0].clip[0]) == 4  # 裁切路徑（三角形）已換到頁面座標
    assert [(round(x), round(y)) for _, x, y, _ in paths[0].clip[0]][:3] == [(20, 50), (120, 50), (70, 150)]


def test_convex_polygon_and_rounded_rect_are_strict():
    M, L = pdf.SEG_MOVETO, pdf.SEG_LINETO
    diamond = [(M, 0, 5, False), (L, 5, 0, False), (L, 10, 5, False), (L, 5, 10, True)]
    assert pdf.convex_polygon(diamond) is not None and pdf.segments_rect(diamond) is None
    pentagram = [(M, 0, 3, False), (L, 10, 3, False), (L, 2, -3, False), (L, 5, 7, False), (L, 8, -3, True)]
    assert pdf.convex_polygon(pentagram) is None  # 每個彎都同方向，但繞了兩圈：不是凸多邊形
    dented = [(M, 0, 0, False), (L, 10, 0, False), (L, 5, 2, False), (L, 10, 10, False), (L, 0, 10, True)]
    assert pdf.convex_polygon(dented) is None
    # 四段曲線圍成的不規則圓盤轉 15 度：端點不在外框的邊上，不是圓角矩形
    c, s = math.cos(math.radians(15)), math.sin(math.radians(15))
    rot = lambda x, y: (x * c - y * s, x * s + y * c)
    blob = [[0, *rot(50, 0)], [2, *rot(50, 30), *rot(28, 52), *rot(0, 50)], [2, *rot(-30, 48), *rot(-52, 28), *rot(-50, 0)],
            [2, *rot(-48, -30), *rot(-28, -52), *rot(0, -50)], [2, *rot(30, -48), *rot(52, -28), *rot(50, 0)], [3]]
    assert s3_recognize._rounded_rect_of(blob, 0.02) is None
    k = 10 * 0.5523
    rounded = [[0, 10, 0], [1, 90, 0], [2, 90 + k, 0, 100, 10 - k, 100, 10], [1, 100, 50], [2, 100, 50 + k, 90 + k, 60, 90, 60], [1, 10, 60],
               [2, 10 - k, 60, 0, 50 + k, 0, 50], [1, 0, 10], [2, 0, 10 - k, 10 - k, 0, 10, 0], [3]]
    assert s3_recognize._rounded_rect_of(rounded, 0.02) == ((0, 0, 100, 60), pytest.approx(10.0))


def test_dash_expansion_keeps_total_length():
    """虛線展開：每一段都在原路徑上，實線部分的總長度等於「原長 × 實線比例」。"""
    line = [[0, 0.0, 0.0], [1, 0.9, 0.0]]
    dashes = s3_recognize._dash_path(line, [0.2, 0.1], 0.0)
    assert [sg[1] for sg in dashes if sg[0] == 0] == pytest.approx([0.0, 0.3, 0.6])
    assert [sg[1] for sg in dashes if sg[0] == 1] == pytest.approx([0.2, 0.5, 0.8])
    shifted = s3_recognize._dash_path(line, [0.2, 0.1], 0.25)  # 相位：從空白的中間開始
    assert [sg[1] for sg in shifted if sg[0] == 0] == pytest.approx([0.05, 0.35, 0.65])
    arc = [[0, 1.0, 0.0], [2, 1.0, 0.5523, 0.5523, 1.0, 0.0, 1.0]]  # 四分之一圓，長度約 1.5708
    pieces = s3_recognize._dash_path(arc, [0.3, 0.2], 0.0)
    total, cur = 0.0, None
    for sg in pieces:
        if sg[0] == 0:
            cur = (sg[1], sg[2])
        elif sg[0] == 2:
            total += s3_recognize._cubic_length(cur, (sg[1], sg[2]), (sg[3], sg[4]), (sg[5], sg[6]))
            cur = (sg[5], sg[6])
            assert math.hypot(*cur) == pytest.approx(1.0, abs=3e-4)  # 切點仍在圓弧上
    assert total == pytest.approx(0.3 * 3 + 0.0708, abs=2e-3)  # 三段完整的實線加最後不滿一段的


def test_non_bmp_characters_are_one_character(tmp_path):
    """基本平面以外的字（數學字母等）PDFium 會回報成兩個代理碼：要併成一個字，否則整頁會在編號時失敗。"""
    from pikepdf import Dictionary, Name, Stream

    cmap = (b"/CIDInit /ProcSet findresource begin 12 dict begin begincmap /CMapName /Adobe-Identity-UCS def /CMapType 2 def\n"
            b"1 begincodespacerange <00> <FF> endcodespacerange\n2 beginbfchar\n<41> <D835DC4E>\n<42> <0062>\nendbfchar\n"
            b"endcmap CMapName currentdict /CMap defineresource pop end end")

    def resources(out):
        return Dictionary(Font=Dictionary(F1=Dictionary(Type=Name.Font, Subtype=Name.Type1, BaseFont=Name.Helvetica, ToUnicode=out.make_indirect(Stream(out, cmap)))))

    path = _hand_written_pdf(tmp_path / "astral.pdf", b"BT /F1 20 Tf 20 40 Td (BABBBBB) Tj ET", resources, size=(300, 100))
    _, pages = _recognize(tmp_path / "job", path)
    assert [t.text for t in pages[0][1].texts] == ["b\U0001D44Ebbbbb"]
