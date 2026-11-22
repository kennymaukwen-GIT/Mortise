"""描圖向量化（recognition/vectorize.py）的測試：答案已知的合成圖。

每張圖都用 vector_raster 的精確面積點陣器畫出來（等於理想的反鋸齒），有些再加干擾（JPEG、雜訊）。
門檻設在實際量到的水準再留一點餘裕（量到的數字寫在各測試的註解裡，2026-11-22 量）。
"""

from __future__ import annotations

import io
import math
from functools import lru_cache
from pathlib import Path

import cv2
import numpy as np
import pytest
from PIL import Image

from mortise.recognition import vector_raster as vr
from mortise.recognition.types import TracedGradient, TracedPath, VectorResult
from mortise.recognition.vectorize import render, to_vector_paths, vectorize

W, H = 240, 180
WHITE = (255, 255, 255)


def _draw(paths: list[TracedPath], size: tuple[int, int] = (W, H), bg=WHITE) -> np.ndarray:
    rgba = vr.render_paths(paths, size, 1.0)
    return np.clip(np.rint(vr.composite_over(rgba, bg)), 0, 255).astype(np.uint8)


def _jpeg(img: np.ndarray, quality: int) -> np.ndarray:
    buf = io.BytesIO()
    Image.fromarray(img).save(buf, "JPEG", quality=quality)
    return np.asarray(Image.open(io.BytesIO(buf.getvalue())).convert("RGB"))


def _scene_rect_circle_line() -> np.ndarray:
    """藍色矩形被紅色圓形蓋住一角，再加一條黑色斜的粗線（320×240）。"""
    return _draw([
        TracedPath(path=vr.rounded_rect_path(120, 110, 140, 100, 0), fill=(40, 90, 200)),
        TracedPath(path=vr.ellipse_path(190, 150, 45, 45), fill=(220, 40, 40)),
        TracedPath(path=[[0, 30, 210], [1, 290, 200]], stroke=(0, 0, 0), stroke_width_px=3.5),
    ], (320, 240))


def _scene_rect_circle() -> np.ndarray:
    return _draw([
        TracedPath(path=vr.rounded_rect_path(120, 90, 140, 90, 0), fill=(40, 90, 200)),
        TracedPath(path=vr.ellipse_path(170, 120, 40, 40), fill=(220, 40, 40)),
    ])


def _photo(seed: int = 1) -> np.ndarray:
    """像照片的影像：多種尺度的平滑隨機明暗加上細雜訊（沒有平坦的色塊）。"""
    rng = np.random.default_rng(seed)
    img = np.zeros((H, W, 3), dtype=np.float32)
    for s in (2, 6, 20):
        img += cv2.GaussianBlur(rng.normal(0, 1, (H, W, 3)).astype(np.float32), (0, 0), s) * (40 * s ** 0.5)
    return np.clip(img + 128 + rng.normal(0, 4, img.shape), 0, 255).astype(np.uint8)


@lru_cache(maxsize=None)
def _traced(name: str) -> VectorResult:
    """同一張合成圖只描一次（多個測試共用）。"""
    makers = {
        "smoke": _scene_rect_circle_line,
        "rect_circle": _scene_rect_circle,
        "jpeg50": lambda: _jpeg(_scene_rect_circle(), 50),
        "jpeg75": lambda: _jpeg(_scene_rect_circle(), 75),
        "photo": _photo,
        "photo_jpeg": lambda: _jpeg(_photo(), 80),
    }
    return vectorize(makers[name]())


def _cov(paths: list[TracedPath], size=(W, H)) -> np.ndarray:
    return vr.render_paths(paths, size, 1.0)[..., 3].astype(np.float32) / 255.0


def _iou(a: np.ndarray, b: np.ndarray) -> float:
    return float(np.minimum(a, b).sum() / max(np.maximum(a, b).sum(), 1e-9))


def _by_kind(res: VectorResult, kind: str) -> list[TracedPath]:
    return [p for p in res.paths if p.primitive is not None and p.primitive.kind == kind]


# ---------------------------------------------------------------- 介面與決定性


def test_interface_and_render_shape():
    res = _traced("smoke")
    assert isinstance(res, VectorResult)
    assert res.size == (320, 240)
    rgba = render(res)
    assert rgba.shape == (240, 320, 4) and rgba.dtype == np.uint8
    big = render(res, 2.0)
    assert big.shape == (480, 640, 4)
    # 路徑編碼：每條路徑以起筆開始，只用 0 起筆、1 直線、2 三次貝茲、3 閉合
    for p in res.paths:
        assert p.path and int(p.path[0][0]) == 0
        for seg in p.path:
            assert int(seg[0]) in (0, 1, 2, 3)
            assert len(seg) == {0: 3, 1: 3, 2: 7, 3: 1}[int(seg[0])]
    assert res.stats["seconds"] > 0 and "timing" in res.stats


def test_deterministic():
    img = _scene_rect_circle_line()
    a, b = vectorize(img), vectorize(img.copy())
    assert [p.path for p in a.paths] == [p.path for p in b.paths]
    assert [(p.fill, p.stroke, p.stroke_width_px, p.fill_rule) for p in a.paths] == [(p.fill, p.stroke, p.stroke_width_px, p.fill_rule) for p in b.paths]
    assert a.render_error == b.render_error and a.node_count == b.node_count and a.confidence == b.confidence


def test_tiny_and_float_input():
    tiny = vectorize(np.zeros((1, 1, 3), dtype=np.uint8))
    assert not tiny.accepted and tiny.paths == [] and tiny.notes
    # 浮點數輸入（超出 0 到 255 的過衝）要先限制範圍，不能繞回去（256 變成 0）
    img = _scene_rect_circle().astype(np.float32)
    img[img >= 254.5] = 256.0
    res = vectorize(img)
    assert res.accepted and res.background == WHITE


# ---------------------------------------------------------------- 整體品質（煙霧測試）


def test_smoke_scene():
    # 量到：ΔE 0.007、IoU 0.9997、3 條路徑 9 個節點、線寬 3.51
    res = _traced("smoke")
    assert res.accepted and res.confidence > 0.8
    assert res.render_error < 0.3 and res.iou > 0.99
    assert len(res.paths) == 3 and res.node_count <= 12
    assert res.background == WHITE


def test_completion_of_occluded_rect():
    # 矩形右下角被圓蓋住：看得到的三邊與兩段邊擬合出完整矩形，放在圓的下面
    res = _traced("smoke")
    rects = _by_kind(res, "rect")
    assert len(rects) == 1
    r = rects[0]
    assert r.completed and r.fill == (40, 90, 200)
    q = r.primitive
    assert abs(q.cx - 120) < 0.3 and abs(q.cy - 110) < 0.3 and abs(q.w - 140) < 0.5 and abs(q.h - 100) < 0.5
    circ = _by_kind(res, "circle")[0]
    assert res.paths.index(r) < res.paths.index(circ)  # 由下到上：矩形在下
    assert abs(circ.primitive.w - 90) < 0.5 and abs(circ.primitive.cx - 190) < 0.3


def test_stroke_recovery():
    # 斜的黑色粗線還原成「中心線加線寬」：兩個節點、線寬 3.5 ± 0.2、端點在 0.5 像素內
    res = _traced("smoke")
    strokes = [p for p in res.paths if p.stroke is not None]
    assert len(strokes) == 1
    s = strokes[0]
    assert s.fill is None and s.stroke == (0, 0, 0)
    assert abs(s.stroke_width_px - 3.5) < 0.2
    assert len(s.path) == 2
    ends = sorted([tuple(s.path[0][1:3]), tuple(s.path[-1][-2:])])
    assert math.dist(ends[0], (30, 210)) < 0.6 and math.dist(ends[1], (290, 200)) < 0.6


# ---------------------------------------------------------------- 基本幾何


def test_primitives_rounded_rect_and_rotated_ellipse():
    img = _draw([
        TracedPath(path=vr.rounded_rect_path(80, 70, 100, 70, 14), fill=(20, 120, 80)),
        TracedPath(path=vr.ellipse_path(170, 120, 50, 25, 20), fill=(240, 160, 20)),
    ])
    res = vectorize(img)
    assert res.accepted and res.render_error < 0.2
    rr = _by_kind(res, "rounded_rect")
    el = _by_kind(res, "ellipse")
    assert len(rr) == 1 and len(el) == 1
    q = rr[0].primitive
    assert abs(q.cx - 80) < 0.2 and abs(q.cy - 70) < 0.2 and abs(q.w - 100) < 0.3 and abs(q.h - 70) < 0.3
    assert abs(q.corner_radius - 14) < 0.6 and q.angle_deg == 0.0
    e = el[0].primitive
    assert abs(e.cx - 170) < 0.2 and abs(e.cy - 120) < 0.2 and abs(e.w - 100) < 0.3 and abs(e.h - 50) < 0.3
    assert abs(e.angle_deg - 20) < 0.5
    assert res.node_count <= 14


def test_rotated_rect_and_polygon():
    tri = vr.polygon_path([(40, 150), (110, 30), (180, 160)])
    img = _draw([
        TracedPath(path=tri, fill=(90, 40, 160)),
        TracedPath(path=vr.rounded_rect_path(190, 60, 60, 40, 0, 15), fill=(10, 150, 140)),
    ])
    res = vectorize(img)
    assert res.accepted and res.render_error < 0.3
    rect = [p for p in res.paths if p.primitive is not None and p.primitive.kind == "rect"]
    assert len(rect) == 1 and abs(rect[0].primitive.angle_deg - 15) < 0.5
    poly = [p for p in res.paths if p.primitive is not None and p.primitive.kind == "polygon"]
    assert len(poly) == 1 and len(poly[0].primitive.points) == 3
    got = sorted(tuple(round(v) for v in pt) for pt in poly[0].primitive.points)
    assert got == sorted([(40, 150), (110, 30), (180, 160)])


# ---------------------------------------------------------------- 孔洞與填色規則


def test_hole_uses_evenodd_and_stays_open():
    # 圓環：洞裡看得到底色，輸出一條 evenodd 複合路徑，重畫時洞是空的
    ring = vr.ellipse_path(120, 90, 60, 60) + vr.ellipse_path(120, 90, 30, 30)
    img = _draw([TracedPath(path=ring, fill=(200, 30, 60), fill_rule="evenodd")])
    res = vectorize(img)
    assert res.accepted and res.render_error < 0.2 and res.iou > 0.99
    assert len(res.paths) == 1
    p = res.paths[0]
    assert p.fill_rule == "evenodd" and sum(1 for s in p.path if int(s[0]) == 0) == 2
    a = render(res)[..., 3]
    assert a[90, 120] == 0 and a[90, 120 + 45] == 255 and a[90, 120 + 75] == 0


def test_hole_filled_by_upper_shape():
    # 洞裡是另一種顏色（不是底色）：外面的形狀補成實心放在下面，洞裡的形狀疊在上面
    img = _draw([
        TracedPath(path=vr.rounded_rect_path(120, 90, 160, 120, 0), fill=(30, 60, 140)),
        TracedPath(path=vr.ellipse_path(120, 90, 35, 35), fill=(250, 200, 40)),
    ])
    res = vectorize(img)
    assert res.accepted and res.render_error < 0.2
    rect = _by_kind(res, "rect")
    circ = _by_kind(res, "circle")
    assert len(rect) == 1 and len(circ) == 1
    assert rect[0].fill_rule == "nonzero" and res.paths.index(rect[0]) < res.paths.index(circ[0])


# ---------------------------------------------------------------- 曲線、多色圖示、圖表


def _blob(cx, cy, r, n=6, seed=3) -> list[list[float]]:
    rng = np.random.default_rng(seed)
    ang = np.linspace(0, 2 * math.pi, n, endpoint=False)
    rad = r * (1 + 0.3 * rng.uniform(-1, 1, n))
    pts = np.stack([cx + rad * np.cos(ang), cy + rad * np.sin(ang)], 1)
    path: list[list[float]] = [[0, *pts[0]]]
    for i in range(n):  # Catmull-Rom 轉三次貝茲：平滑的封閉曲線
        p0, p1, p2, p3 = pts[i - 1], pts[i], pts[(i + 1) % n], pts[(i + 2) % n]
        c1, c2 = p1 + (p2 - p0) / 6, p2 - (p3 - p1) / 6
        path.append([2, *c1, *c2, *p2])
    path.append([3])
    return path


def test_bezier_blob():
    # 量到：ΔE 約 0.02、IoU 0.998、節點數約為原圖的 2 到 3 倍（原圖 6 段）
    path = _blob(120, 90, 60)
    img = _draw([TracedPath(path=path, fill=(230, 90, 20))])
    res = vectorize(img)
    assert res.accepted and res.render_error < 0.3 and res.iou > 0.99
    assert len(res.paths) == 1 and res.node_count <= 24
    truth = _cov([TracedPath(path=path, fill=(0, 0, 0))])
    assert _iou(truth, render(res)[..., 3] / 255.0) > 0.995
    assert res.paths[0].fit_error_px is not None and res.paths[0].fit_error_px < 0.15


def test_multicolor_icon():
    shapes = [
        TracedPath(path=vr.ellipse_path(120, 90, 80, 80), fill=(40, 40, 60)),
        TracedPath(path=vr.rounded_rect_path(120, 70, 70, 50, 10), fill=(225, 225, 235)),
        TracedPath(path=vr.polygon_path([(95, 110), (145, 110), (120, 150)]), fill=(240, 80, 70)),
        TracedPath(path=vr.ellipse_path(120, 70, 12, 12), fill=(60, 170, 230)),
    ]
    img = _draw(shapes)
    res = vectorize(img)
    assert res.accepted and res.render_error < 0.3 and res.iou > 0.99
    fills = {p.fill for p in res.paths}
    assert {(40, 40, 60), (225, 225, 235), (240, 80, 70), (60, 170, 230)} <= fills
    assert len(_by_kind(res, "circle")) >= 2 and len(_by_kind(res, "rounded_rect")) == 1


def _chart_paths() -> list[TracedPath]:
    out = []
    for y in (50.5, 90.5, 130.5):  # 1 像素的淡灰格線（對齊像素）
        out.append(TracedPath(path=[[0, 30, y], [1, 230, y]], stroke=(190, 195, 205), stroke_width_px=1.0))
    out.append(TracedPath(path=[[0, 30, 20], [1, 30, 160], [1, 230, 160]], stroke=(50, 50, 60), stroke_width_px=3.0))
    out.append(TracedPath(path=[[0, 40, 120], [1, 80, 80], [1, 120, 100], [1, 160, 60], [1, 220, 70]], stroke=(0, 150, 190), stroke_width_px=4.0, stroke_join="round"))
    return out


def test_thin_line_chart():
    # 量到：格線線寬 1.0、軸線 3.0、資料線 4.0；IoU 0.997
    img = _draw(_chart_paths())
    res = vectorize(img)
    assert res.accepted and res.render_error < 0.3
    strokes = [p for p in res.paths if p.stroke is not None]
    assert len(strokes) >= 5
    grid = [p for p in strokes if p.stroke == (190, 195, 205)]
    assert grid and all(abs(p.stroke_width_px - 1.0) < 0.25 for p in grid)
    data = [p for p in strokes if max(abs(a - b) for a, b in zip(p.stroke, (0, 150, 190))) <= 6]
    assert data and all(abs(p.stroke_width_px - 4.0) < 0.5 for p in data)
    truth = _cov(_chart_paths())
    assert _iou(truth, render(res)[..., 3] / 255.0) > 0.97


# ---------------------------------------------------------------- 漸層


def test_linear_gradient():
    g = TracedGradient("linear", [30, 30, 210, 150], [(0.0, (240, 60, 40)), (1.0, (40, 80, 230))])
    img = _draw([TracedPath(path=vr.rounded_rect_path(120, 90, 180, 120, 0), fill=(0, 0, 0), gradient=g)])
    res = vectorize(img)
    assert res.accepted and res.render_error < 0.5 and len(res.paths) == 1
    p = res.paths[0]
    assert p.gradient is not None and p.gradient.kind == "linear"
    x0, y0, x1, y1 = p.gradient.coords
    assert math.dist((x0, y0), (30, 30)) < 2.0 and math.dist((x1, y1), (210, 150)) < 2.0
    assert p.primitive is not None and p.primitive.kind == "rect"


def test_radial_gradient():
    g = TracedGradient("radial", [110, 80, 80], [(0.0, (255, 240, 120)), (1.0, (220, 60, 20))])
    img = _draw([TracedPath(path=vr.ellipse_path(120, 90, 70, 70), fill=(0, 0, 0), gradient=g)])
    res = vectorize(img)
    assert res.accepted and res.render_error < 0.5 and len(res.paths) == 1
    p = res.paths[0]
    assert p.gradient is not None and p.gradient.kind == "radial"
    assert math.dist(p.gradient.coords[:2], (110, 80)) < 1.0
    assert p.primitive is not None and p.primitive.kind == "circle"


# ---------------------------------------------------------------- 干擾：JPEG、雜訊


@pytest.mark.parametrize("name", ["jpeg50", "jpeg75"])
def test_jpeg_keeps_clean_geometry(name):
    # JPEG 的色度縮小取樣與振鈴不能變成一堆碎片：仍是一個矩形加一個圓（量到：2 到 3 條路徑、邊界誤差 < 0.1 px）
    res = _traced(name)
    assert res.accepted
    assert res.stats["chroma_weight"] < 1.0  # 偵測到色度被縮小取樣
    assert len(res.paths) <= 3
    rect, circ = _by_kind(res, "rect"), _by_kind(res, "circle")
    assert len(rect) == 1 and len(circ) == 1
    assert abs(rect[0].primitive.w - 140) < 0.6 and abs(rect[0].primitive.h - 90) < 0.6
    assert abs(circ[0].primitive.w - 80) < 0.6 and math.dist((circ[0].primitive.cx, circ[0].primitive.cy), (170, 120)) < 0.4


def test_gaussian_noise():
    rng = np.random.default_rng(7)
    img = np.clip(_scene_rect_circle().astype(np.float32) + rng.normal(0, 4, (H, W, 3)), 0, 255).astype(np.uint8)
    res = vectorize(img)
    assert res.accepted and len(res.paths) <= 3
    assert len(_by_kind(res, "rect")) == 1 and len(_by_kind(res, "circle")) == 1


# ---------------------------------------------------------------- 不適合向量化


@pytest.mark.parametrize("name", ["photo", "photo_jpeg"])
def test_photo_is_rejected(name):
    res = _traced(name)
    assert not res.accepted and res.paths == [] and res.confidence == 0.0
    assert res.notes and "不適合向量化" in res.notes[0]


# ---------------------------------------------------------------- 遮罩與座標換算


def test_mask_limits_tracing():
    img = _scene_rect_circle()
    m = np.zeros((H, W), dtype=bool)
    m[10:170, 20:220] = True
    res = vectorize(img, m)
    assert res.accepted and res.background is None
    a = render(res)[..., 3]
    assert a[~m].max() <= 1  # 遮罩外沒有畫東西
    assert len(_by_kind(res, "circle")) == 1


def test_to_vector_paths_coordinates():
    from mortise.artifacts import VectorPath

    res = _traced("smoke")
    ox, oy, s = 0.1, 0.2, 0.001
    vps = to_vector_paths(res, (ox, oy), s)
    assert len(vps) == len(res.paths) and all(isinstance(v, VectorPath) for v in vps)
    for tp, vp in zip(res.paths, vps):
        assert len(vp.path) == len(tp.path)
        for a, b in zip(tp.path, vp.path):
            assert int(a[0]) == int(b[0])
            for i in range(1, len(a), 2):
                assert b[i] == pytest.approx(ox + a[i] * s) and b[i + 1] == pytest.approx(oy + a[i + 1] * s)
        if tp.stroke_width_px:
            assert vp.stroke_width == pytest.approx(tp.stroke_width_px * s)
        if tp.primitive is not None:
            assert vp.primitive.kind == tp.primitive.kind
            assert vp.primitive.cx == pytest.approx(ox + tp.primitive.cx * s) and vp.primitive.w == pytest.approx(tp.primitive.w * s)
        if tp.fill is not None:
            assert (vp.fill.r, vp.fill.g, vp.fill.b) == tp.fill
    g = TracedGradient("radial", [110, 80, 80], [(0.0, (255, 240, 120)), (1.0, (220, 60, 20))])
    gres = vectorize(_draw([TracedPath(path=vr.ellipse_path(120, 90, 70, 70), fill=(0, 0, 0), gradient=g)]))
    gv = to_vector_paths(gres, (ox, oy), s)[0].gradient
    gc = gres.paths[0].gradient.coords
    assert gv.kind == "radial" and gv.coords == pytest.approx([ox + gc[0] * s, oy + gc[1] * s, gc[2] * s])


# ---------------------------------------------------------------- 不使用大型 AI


def test_no_ai_dependencies():
    root = Path(__file__).resolve().parents[2] / "mortise" / "recognition"
    for f in [root / "vectorize.py", *sorted(root.glob("vector_*.py"))]:
        src = f.read_text(encoding="utf-8")
        for bad in ("from ..ai", "import torch", "onnxruntime", "transformers", "vtracer"):
            assert bad not in src, f"{f.name} 不應該用到 {bad}"


# ---------------------------------------------------------------- 影像產線端到端


def _star(cx, cy, ro, ri, n=5) -> list[list[float]]:
    pts = []
    for i in range(2 * n):
        r = ro if i % 2 == 0 else ri
        a = -math.pi / 2 + i * math.pi / n
        pts.append((cx + r * math.cos(a), cy + r * math.sin(a)))
    return vr.polygon_path(pts)


@pytest.fixture(scope="module")
def logo_page(tmp_path_factory):
    """白底 1280×720 的頁面上一個多色標誌（兩個曲線色塊、一顆星、一個白色缺口），整條流水線跑一次（約 6 秒）。"""
    from mortise.artifacts import Intent
    from mortise.pipeline import Pipeline

    tmp = tmp_path_factory.mktemp("vec")
    paths = [
        TracedPath(path=_blob(400, 360, 150, n=7, seed=5), fill=(30, 110, 200)),
        TracedPath(path=_blob(470, 330, 90, n=6, seed=9), fill=(250, 190, 40)),
        TracedPath(path=_star(400, 380, 80, 34), fill=(220, 50, 60)),
        TracedPath(path=vr.ellipse_path(330, 300, 26, 26), fill=(255, 255, 255)),
    ]
    png = tmp / "logo_page.png"
    Image.fromarray(_draw(paths, (1280, 720))).save(png)
    pipe = Pipeline(tmp / "job")
    pipe.start(png, png.name, Intent())
    pipe.run()
    return pipe


def test_pipeline_traces_illustration(logo_page):
    from mortise.artifacts import ElementList, Provenance

    el = logo_page.store.load("s3/page-001", ElementList)
    assert len(el.vectors) == 1
    v = el.vectors[0]
    img = next(i for i in el.images if i.id == v.image_id)  # 對應的圖片照樣留著
    assert v.quad == img.quad and v.z == img.z
    assert v.origin.value == "traced" and v.origin.source == Provenance.MEASURED and v.origin.writer.startswith("s3.vectorize@")
    assert 0.5 < v.origin.confidence <= 1.0
    assert v.render_error is not None and v.render_error < 1.0  # 量到 0.30
    fills = {(p.fill.r, p.fill.g, p.fill.b) for p in v.paths if p.fill is not None}
    assert {(30, 110, 200), (250, 190, 40), (220, 50, 60)} <= fills
    # 座標是頁面相對單位（頁寬 = 1），曲線落在對應圖片的框裡（容許一像素；貝茲的控制點可以在框外）
    from mortise.recognition.vector_fit import path_points

    pts = np.vstack([path_points(p.path, 0.0002) for p in v.paths])
    xs, ys = pts[:, 0].tolist(), pts[:, 1].tolist()
    px = 1.0 / 1280
    assert img.quad.tl.x - px <= min(xs) and max(xs) <= img.quad.br.x + px
    assert img.quad.tl.y - px <= min(ys) and max(ys) <= img.quad.br.y + px
    assert 0.18 < min(xs) < 0.22 and 0.43 < max(xs) < 0.47  # 標誌在 250 到 590 像素之間


def test_pipeline_styled_doc_links_vector(logo_page):
    from mortise.artifacts import StyledDoc

    sd = logo_page.store.load("s5/styled_doc", StyledDoc)
    page = sd.pages[0]
    assert len(page.vectors) == 1
    sv = page.vectors[0]
    assert sv.origin == "traced" and len(sv.paths) >= 3
    linked = [im for im in page.images if im.vector_id == sv.id]
    assert len(linked) == 1 and linked[0].id == sv.image_id


def test_pipeline_does_not_trace_photos(tmp_path):
    from mortise.artifacts import ElementList, Intent
    from mortise.pipeline import Pipeline

    page = np.full((720, 1280, 3), 255, dtype=np.uint8)
    page[200:380, 300:540] = _photo(seed=4)
    png = tmp_path / "photo_page.png"
    Image.fromarray(page).save(png)
    pipe = Pipeline(tmp_path / "job")
    pipe.start(png, png.name, Intent())
    pipe.run()
    el = pipe.store.load("s3/page-001", ElementList)
    assert el.vectors == [] and len(el.images) == 1
    # 區域分類若把它當成插畫，描圖這一步也要拒絕（重畫驗證前就判成照片），並在 notes 說明只留圖片
    if el.images[0].kind.value == "illustration":
        assert any("沒有描成向量" in n and "不適合向量化" in n for n in el.notes)
