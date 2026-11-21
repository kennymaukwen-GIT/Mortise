"""頁面幾何（page_geometry.py）的測試：把 fixture 頁面以已知的相機模型貼到桌面背景上，量四角、長寬比、攤平對位。

合成方式
    頁面平面在三維空間中旋轉（rx、ry、rz 度），以針孔相機（焦距 f = f_frac × 影像對角線、主點在影像中心）投影，
    得到「頁面像素 → 照片」的單應矩陣 G。頁面邊緣用解析式的覆蓋率做反鋸齒，所以四角的標準答案是精確的。
    干擾：模糊、高斯雜訊、光照漸層、陰影、JPEG 壓縮、降解析度、遮擋（手指、筆）、雜亂背景、低對比。
"""

from __future__ import annotations

import math
from pathlib import Path

import cv2
import numpy as np
import pytest

from mortise.eval.truth import render_truth
from mortise.recognition.page_geometry import enhance, estimate_aspect, find_page_quad, rectify

FIX = Path(__file__).resolve().parents[1] / "fixtures"
_PAGES: dict = {}


def page_image(name: str, dpi: float = 80.0) -> np.ndarray:
    key = (name, dpi)
    if key not in _PAGES:
        im, _, _ = render_truth(FIX / f"{name}.pdf", 0, dpi)
        _PAGES[key] = np.ascontiguousarray(im)
    return _PAGES[key]


# ------------------------------------------------------------------ 合成工具


def _rot(rx: float, ry: float, rz: float) -> np.ndarray:
    ax, ay, az = (math.radians(v) for v in (rx, ry, rz))
    Rx = np.array([[1, 0, 0], [0, math.cos(ax), -math.sin(ax)], [0, math.sin(ax), math.cos(ax)]])
    Ry = np.array([[math.cos(ay), 0, math.sin(ay)], [0, 1, 0], [-math.sin(ay), 0, math.cos(ay)]])
    Rz = np.array([[math.cos(az), -math.sin(az), 0], [math.sin(az), math.cos(az), 0], [0, 0, 1]])
    return Rz @ Ry @ Rx


def camera_homography(pw, ph, iw, ih, f_frac=0.7, rx=0.0, ry=0.0, rz=0.0, fill=0.7, shift=(0.0, 0.0)) -> np.ndarray:
    """頁面連續座標 → 照片連續座標的單應矩陣（針孔相機、主點在影像中心）。"""
    f = f_frac * math.hypot(iw, ih)
    K = np.array([[f, 0, iw / 2.0], [0, f, ih / 2.0], [0, 0, 1.0]])
    R = _rot(rx, ry, rz)
    z0 = f * max(pw / (fill * iw), ph / (fill * ih))
    t = np.array([shift[0] * pw, shift[1] * ph, z0])
    T = np.array([[1, 0, -pw / 2.0], [0, 1, -ph / 2.0], [0, 0, 1.0]])
    M = K @ np.column_stack([R[:, 0], R[:, 1], t]) @ T
    return M / M[2, 2]


def apply_h(G: np.ndarray, pts: np.ndarray) -> np.ndarray:
    pts = np.asarray(pts, dtype=np.float64)
    ph = np.column_stack([pts, np.ones(len(pts))]) @ G.T
    return ph[:, :2] / ph[:, 2:3]


def _shift(G):
    t_in = np.array([[1, 0, 0.5], [0, 1, 0.5], [0, 0, 1]], dtype=np.float64)
    t_out = np.array([[1, 0, -0.5], [0, 1, -0.5], [0, 0, 1]], dtype=np.float64)
    return t_out @ G @ t_in


def coverage(corners: np.ndarray, w: int, h: int) -> np.ndarray:
    """凸四邊形對每個像素的覆蓋率（解析式反鋸齒）。"""
    ys, xs = np.mgrid[0:h, 0:w].astype(np.float64)
    xs += 0.5
    ys += 0.5
    c = corners
    cen = c.mean(axis=0)
    a = np.ones((h, w))
    for k in range(4):
        p0, p1 = c[k], c[(k + 1) % 4]
        d = p1 - p0
        n = np.array([-d[1], d[0]]) / np.linalg.norm(d)
        if (cen - p0) @ n < 0:
            n = -n
        dist = (xs - p0[0]) * n[0] + (ys - p0[1]) * n[1]
        a *= np.clip(dist + 0.5, 0, 1)
    return a


def background(kind: str, w: int, h: int, rng: np.random.Generator) -> np.ndarray:
    def lowfreq(scale, amp):
        n = rng.standard_normal((max(2, h // scale), max(2, w // scale))).astype(np.float32)
        n = cv2.resize(n, (w, h), interpolation=cv2.INTER_CUBIC)
        return n / (n.std() + 1e-6) * amp

    if kind == "wood":
        base = np.array([132, 94, 60], np.float32)
        grain = np.sin(np.linspace(0, 60, w, dtype=np.float32)[None, :] + lowfreq(40, 2.5)) * 10
        img = base[None, None] + (grain + lowfreq(25, 6))[..., None] * np.array([1.0, 0.8, 0.6], np.float32)
    elif kind == "dark":
        img = np.array([48, 48, 54], np.float32)[None, None] + lowfreq(60, 3)[..., None]
    elif kind == "light":
        img = np.array([206, 204, 198], np.float32)[None, None] + lowfreq(60, 3)[..., None]
    elif kind == "samecolor":
        img = np.array([222, 220, 214], np.float32)[None, None] + lowfreq(60, 1.5)[..., None]
    elif kind == "wall":
        img = np.array([170, 178, 186], np.float32)[None, None] + lowfreq(30, 4)[..., None]
    elif kind == "clutter":
        img = background("wood", w, h, rng).astype(np.float32)
        for _ in range(28):
            col = rng.integers(0, 255, 3).tolist()
            x, y = int(rng.integers(0, w)), int(rng.integers(0, h))
            t = rng.integers(0, 3)
            if t == 0:
                cv2.rectangle(img, (x, y), (x + int(rng.integers(30, 250)), y + int(rng.integers(30, 250))), col, -1)
            elif t == 1:
                cv2.circle(img, (x, y), int(rng.integers(15, 120)), col, -1)
            else:
                cv2.line(img, (x, y), (int(rng.integers(0, w)), int(rng.integers(0, h))), col, int(rng.integers(2, 9)))
    else:
        raise ValueError(kind)
    return np.clip(img, 0, 255).astype(np.uint8)


def compose(page: np.ndarray, G: np.ndarray, iw: int, ih: int, bg: np.ndarray) -> np.ndarray:
    ph, pw = page.shape[:2]
    warped = cv2.warpPerspective(page, _shift(G), (iw, ih), flags=cv2.INTER_LINEAR, borderMode=cv2.BORDER_REPLICATE)
    corners = apply_h(G, np.array([[0, 0], [pw, 0], [pw, ph], [0, ph]], dtype=np.float64))
    a = coverage(corners, iw, ih)[..., None]
    return np.clip(a * warped + (1 - a) * bg, 0, 255).astype(np.uint8)


def degrade(img: np.ndarray, rng, blur=0.0, noise=0.0, light=False, shadow=False, jpeg=None) -> np.ndarray:
    out = img.astype(np.float32)
    h, w = out.shape[:2]
    if light:
        gx = np.linspace(0.72, 1.08, w, dtype=np.float32)[None, :]
        gy = np.linspace(1.04, 0.86, h, dtype=np.float32)[:, None]
        out *= (gx * gy)[..., None] * np.array([1.0, 0.97, 0.9], np.float32)  # 偏暖色光
    if shadow:
        ys, xs = np.mgrid[0:h, 0:w].astype(np.float32)
        cx, cy = w * 0.62, h * 0.72
        blob = np.exp(-(((xs - cx) / (0.28 * w)) ** 2 + ((ys - cy) / (0.18 * h)) ** 2))
        out *= (1 - 0.4 * blob)[..., None]
    if blur > 0:
        out = cv2.GaussianBlur(out, (0, 0), blur)
    if noise > 0:
        out += rng.standard_normal(out.shape).astype(np.float32) * noise
    out = np.clip(out, 0, 255).astype(np.uint8)
    if jpeg:
        ok, buf = cv2.imencode(".jpg", out[..., ::-1], [cv2.IMWRITE_JPEG_QUALITY, int(jpeg)])
        out = cv2.imdecode(buf, cv2.IMREAD_COLOR)[..., ::-1].copy()
    return out


def occlude(img: np.ndarray, corners: np.ndarray, side: int = 2, frac: float = 0.5) -> np.ndarray:
    """在第 side 條邊上畫一根手指（膚色橢圓，伸進頁面）與一枝筆（深色粗線，跨過右邊）。"""
    out = img.copy()
    a, b = corners[side], corners[(side + 1) % 4]
    m = a + frac * (b - a)
    d = (b - a) / np.linalg.norm(b - a)
    n = np.array([d[1], -d[0]])
    cen = corners.mean(axis=0)
    if (m - cen) @ n < 0:
        n = -n
    tip = m - n * 60
    base_pt = m + n * 120
    c = 0.5 * (tip + base_pt)
    ang = math.degrees(math.atan2(n[1], n[0]))
    cv2.ellipse(out, (int(c[0]), int(c[1])), (110, 38), ang, 0, 360, (224, 172, 138), -1, cv2.LINE_AA)
    a2, b2 = corners[1], corners[2]
    m2 = a2 + 0.3 * (b2 - a2)
    cv2.line(out, (int(m2[0] - 140), int(m2[1] - 60)), (int(m2[0] + 160), int(m2[1] + 70)), (25, 25, 120), 9, cv2.LINE_AA)
    return out


def make_photo(page: np.ndarray, iw, ih, bg_kind, seed=0, **cam):
    rng = np.random.default_rng(seed)
    ph, pw = page.shape[:2]
    G = camera_homography(pw, ph, iw, ih, **cam)
    bg = background(bg_kind, iw, ih, rng)
    # 曝光：照片裡的白紙通常不會到 255
    page = np.clip(page.astype(np.float32) * 0.93, 0, 255).astype(np.uint8)
    photo = compose(page, G, iw, ih, bg)
    corners = apply_h(G, np.array([[0, 0], [pw, 0], [pw, ph], [0, ph]], dtype=np.float64))
    return photo, G, corners


def evaluate(page: np.ndarray, photo: np.ndarray, G: np.ndarray, corners: np.ndarray, params=None) -> dict:
    ph, pw = page.shape[:2]
    q = find_page_quad(photo, params)
    out = {"method": q.method, "conf": q.confidence, "aspect_method": q.aspect_method}
    if q.method == "full_image":
        return out
    err = np.linalg.norm(q.corners - corners, axis=1)
    true_a = ph / pw
    rect, Hm = rectify(photo, q)
    gy, gx = np.mgrid[0:11, 0:11] / 10.0
    pts = np.column_stack([gx.ravel() * pw, gy.ravel() * ph])
    mapped = apply_h(Hm @ G, pts)
    ideal = pts * np.array([rect.shape[1] / pw, rect.shape[0] / ph])
    al = np.linalg.norm(mapped - ideal, axis=1)
    out.update(
        {
            "corner_mean": float(err.mean()),
            "corner_max": float(err.max()),
            "corner_err_reported": float(np.mean(q.corner_error_px)),
            "aspect_err_pct": float(abs(q.aspect_ratio - true_a) / true_a * 100),
            "aspect_err_reported_pct": float(q.aspect.error / q.aspect.value * 100),
            "align_mean": float(al.mean()),
            "align_max": float(al.max()),
            "rect_shape": rect.shape[:2],
        }
    )
    return out


# 視角組合：(fixture, 照片寬, 高, 背景, 相機參數)
VIEWS = [
    ("slide_basic", 1600, 1200, "wood", dict(f_frac=0.7, rx=25, ry=-10, rz=5)),
    ("slide_dark", 1600, 1200, "light", dict(f_frac=0.65, rx=-20, ry=20, rz=-8)),
    ("doc_a4", 1200, 1600, "wood", dict(f_frac=0.75, rx=30, ry=5, rz=3)),
    ("poster", 1200, 1600, "wall", dict(f_frac=0.6, rx=12, ry=-25, rz=-4)),
    ("doc_a4", 1200, 1600, "dark", dict(f_frac=0.7, rx=0, ry=0, rz=12)),
    ("slide_basic", 1600, 1200, "dark", dict(f_frac=0.55, rx=38, ry=22, rz=10, fill=0.62)),
]

DEGRADE = {
    "clean": dict(blur=0.6, noise=1.0),
    "degraded": dict(blur=1.5, noise=4.0, light=True, shadow=True, jpeg=70),
}


def run_views(condition: str, lowres: bool = False) -> list[dict]:
    res = []
    for i, (name, iw, ih, bg, cam) in enumerate(VIEWS):
        page = page_image(name)
        photo, G, corners = make_photo(page, iw, ih, bg, seed=i, **cam)
        rng = np.random.default_rng(100 + i)
        photo = degrade(photo, rng, **DEGRADE[condition])
        if lowres:
            s = 0.5
            photo = cv2.resize(photo, None, fx=s, fy=s, interpolation=cv2.INTER_AREA)
            photo = degrade(photo, rng, noise=2.0, jpeg=80)
            S = np.diag([s, s, 1.0])
            G = S @ G
            corners = corners * s
        r = evaluate(page, photo, G, corners)
        r["case"] = f"{name}/{bg}/{cam}"
        res.append(r)
    return res


# ------------------------------------------------------------------ 測試


def test_aspect_formula_exact():
    """Zhang–He 長寬比公式：無雜訊的四角應精確還原長寬比與焦距。"""
    for (pw, ph, f, rx, ry, rz) in [(1000, 1414, 0.7, 30, 10, 5), (1600, 900, 0.55, -25, 30, -12), (1000, 1294, 1.2, 15, -20, 30)]:
        G = camera_homography(pw, ph, 1600, 1200, f_frac=f, rx=rx, ry=ry, rz=rz, fill=0.6)
        c = apply_h(G, np.array([[0, 0], [pw, 0], [pw, ph], [0, ph]], dtype=np.float64))
        est, method, focal = estimate_aspect(c, (1600, 1200), np.full(4, 0.01))
        assert method == "perspective"
        assert abs(est.value - ph / pw) / (ph / pw) < 1e-4
        assert abs(focal - f * math.hypot(1600, 1200)) / (f * math.hypot(1600, 1200)) < 1e-3


def test_frontal_falls_back_to_side_ratio():
    G = camera_homography(1000, 1414, 1600, 1200, f_frac=0.7, rz=10, fill=0.6)
    c = apply_h(G, np.array([[0, 0], [1000, 0], [1000, 1414], [0, 1414]], dtype=np.float64))
    est, method, _ = estimate_aspect(c, (1600, 1200), np.full(4, 0.3))
    assert method == "side_ratio"
    assert abs(est.value - 1.414) / 1.414 < 0.002


def test_clean_photos_subpixel():
    res = run_views("clean")
    for r in res:
        assert r["method"] == "edge_fit", r
    cm = np.array([r["corner_mean"] for r in res])
    cx = np.array([r["corner_max"] for r in res])
    ae = np.array([r["aspect_err_pct"] for r in res])
    al = np.array([r["align_mean"] for r in res])
    # 實測（合成、針孔相機、直邊）：四角平均 0.021 px、最大 0.048 px；長寬比最大 0.011%；對位平均 0.020 px
    assert cm.mean() < 0.05 and cx.max() < 0.1, (cm, cx)
    assert ae.mean() < 0.02 and ae.max() < 0.05, ae
    assert al.mean() < 0.05, al
    assert min(r["conf"] for r in res) > 0.75


def test_degraded_photos():
    res = run_views("degraded")
    for r in res:
        assert r["method"] == "edge_fit", r
    cm = np.array([r["corner_mean"] for r in res])
    cx = np.array([r["corner_max"] for r in res])
    ae = np.array([r["aspect_err_pct"] for r in res])
    # 實測：四角平均 0.034 px、最大 0.079 px；長寬比最大 0.020%
    assert cm.mean() < 0.07 and cx.max() < 0.15, (cm, cx)
    assert ae.mean() < 0.03 and ae.max() < 0.06, ae


def test_lowres_photos():
    res = run_views("degraded", lowres=True)
    for r in res:
        assert r["method"] == "edge_fit", r
    cm = np.array([r["corner_mean"] for r in res])
    ae = np.array([r["aspect_err_pct"] for r in res])
    # 實測（半解析度、再加雜訊與 JPEG 80）：四角平均 0.020 px；長寬比平均 0.009%、最大 0.034%
    assert cm.mean() < 0.05, cm
    assert ae.mean() < 0.03 and ae.max() < 0.07, ae


@pytest.mark.parametrize("name", ["slide_basic", "slide_dark", "doc_a4", "poster"])
def test_screenshot_is_full_image(name):
    page = page_image(name)
    for jpeg in (None, 85):
        img = page if jpeg is None else degrade(page, np.random.default_rng(0), jpeg=jpeg)
        q = find_page_quad(img)
        assert q.method == "full_image", (name, jpeg, q.notes)
        assert q.confidence >= 0.85
        h, w = img.shape[:2]
        assert np.allclose(q.corners, [[0, 0], [w, 0], [w, h], [0, h]])
        assert abs(q.aspect_ratio - h / w) < 1e-9


def test_input_type_hint_screenshot():
    page = page_image("slide_basic")
    q = find_page_quad(page, {"input_type": "screenshot"})
    assert q.method == "full_image" and q.confidence >= 0.85


def test_occluded_edge():
    page = page_image("doc_a4")
    photo, G, corners = make_photo(page, 1200, 1600, "wood", seed=7, f_frac=0.7, rx=22, ry=-8, rz=4)
    photo = occlude(photo, corners, side=2, frac=0.45)
    photo = degrade(photo, np.random.default_rng(7), blur=1.0, noise=3.0, jpeg=80)
    r = evaluate(page, photo, G, corners)
    assert r["method"] == "edge_fit", r
    assert r["corner_max"] < 0.06, r  # 實測 0.024 px
    assert r["aspect_err_pct"] < 0.03, r  # 實測 0.005%


def test_corner_occluded():
    """一個角被杯子蓋住：由相鄰兩邊延長相交補回。"""
    page = page_image("slide_basic")
    photo, G, corners = make_photo(page, 1600, 1200, "wood", seed=8, f_frac=0.7, rx=20, ry=15, rz=-6)
    c = corners[1]
    cv2.circle(photo, (int(c[0] - 20), int(c[1] + 25)), 85, (240, 240, 245), -1, cv2.LINE_AA)
    cv2.circle(photo, (int(c[0] - 20), int(c[1] + 25)), 85, (60, 60, 70), 6, cv2.LINE_AA)
    photo = degrade(photo, np.random.default_rng(8), blur=1.0, noise=3.0, jpeg=85)
    r = evaluate(page, photo, G, corners)
    assert r["method"] == "edge_fit", r
    assert r["corner_max"] < 0.3, r
    assert r["aspect_err_pct"] < 0.1, r


def test_low_contrast_and_clutter():
    page = page_image("doc_a4")
    photo, G, corners = make_photo(page, 1200, 1600, "samecolor", seed=3, f_frac=0.7, rx=18, ry=12, rz=-5)
    photo = degrade(photo, np.random.default_rng(3), blur=1.0, noise=2.0, jpeg=85)
    r = evaluate(page, photo, G, corners)
    assert r["method"] == "edge_fit", r
    assert r["corner_max"] < 0.15, r  # 實測 0.084 px（頁面與背景差約 15 色階）
    page = page_image("slide_dark")
    photo, G, corners = make_photo(page, 1600, 1200, "clutter", seed=4, f_frac=0.65, rx=-15, ry=-22, rz=6)
    photo = degrade(photo, np.random.default_rng(4), blur=1.0, noise=3.0, jpeg=80)
    r = evaluate(page, photo, G, corners)
    assert r["method"] == "edge_fit", r
    assert r["corner_max"] < 0.1, r  # 實測 0.047 px


@pytest.mark.parametrize("fill,shift", [(0.95, (0.0, 0.32)), (1.25, (0.0, 0.25))])
def test_partial_page_is_low_confidence_full_image(fill, shift):
    """頁面超出畫面（下緣在畫面外；或拉近到只剩一條邊）：應回報 full_image 低信心，請使用者拉四角。"""
    page = page_image("doc_a4")
    photo, G, corners = make_photo(page, 1200, 1600, "wood", seed=5, f_frac=0.7, rx=15, ry=5, rz=3, fill=fill, shift=shift)
    photo = degrade(photo, np.random.default_rng(5), blur=1.0, noise=3.0, jpeg=80)
    q = find_page_quad(photo)
    assert q.method == "full_image", q
    assert q.confidence <= 0.2
    assert any("手動" in n for n in q.notes)


def test_rectify_size_and_homography():
    page = page_image("slide_basic")
    photo, G, corners = make_photo(page, 1600, 1200, "wood", seed=1, f_frac=0.7, rx=25, ry=-10, rz=5)
    q = find_page_quad(photo)
    rect, Hm = rectify(photo, q)
    c = q.corners
    longest = max(np.linalg.norm(c[1] - c[0]), np.linalg.norm(c[2] - c[3]), np.linalg.norm(c[3] - c[0]), np.linalg.norm(c[2] - c[1]))
    assert max(rect.shape[:2]) == pytest.approx(longest, abs=1.0)
    m = apply_h(Hm, c)
    assert np.allclose(m, [[0, 0], [rect.shape[1], 0], [rect.shape[1], rect.shape[0]], [0, rect.shape[0]]], atol=1e-6)
    # 攤平影像與原頁面的灰階相關係數
    ref = cv2.resize(page, (rect.shape[1], rect.shape[0]), interpolation=cv2.INTER_AREA)
    a = cv2.cvtColor(rect, cv2.COLOR_RGB2GRAY).astype(np.float64).ravel()
    b = cv2.cvtColor(ref, cv2.COLOR_RGB2GRAY).astype(np.float64).ravel()
    assert np.corrcoef(a, b)[0, 1] > 0.95


def test_enhance_flattens_illumination():
    page = page_image("doc_a4", 100)
    rng = np.random.default_rng(0)
    bad = degrade(page, rng, light=True, shadow=True, noise=2.0)
    good = enhance(bad)
    paper = cv2.cvtColor(page, cv2.COLOR_RGB2GRAY) > 250  # 原頁面的白紙像素
    paper = cv2.erode(paper.astype(np.uint8), np.ones((5, 5), np.uint8)) > 0
    before = bad[paper].astype(np.float64)
    after = good[paper].astype(np.float64)
    # 光照不均：紙面亮度的標準差要大幅下降；白平衡：紙面 R−B 差要接近 0
    assert after.mean(axis=1).std() < 0.3 * before.mean(axis=1).std()
    assert abs(np.median(after[:, 0] - after[:, 2])) < 3.0 < abs(np.median(before[:, 0] - before[:, 2]))
    # 文字仍要比紙面暗很多
    ink = cv2.cvtColor(page, cv2.COLOR_RGB2GRAY) < 80
    assert good[ink].mean() < 0.6 * good[paper].mean()


def report() -> None:  # pragma: no cover - 供手動量測用
    for cond, low in (("clean", False), ("degraded", False), ("degraded", True)):
        res = run_views(cond, lowres=low)
        print(f"== {cond}{' lowres' if low else ''}")
        for r in res:
            print({k: (round(v, 3) if isinstance(v, float) else v) for k, v in r.items()})


if __name__ == "__main__":  # pragma: no cover
    report()


def test_blur_error_is_reported_honestly():
    """嚴重模糊：搜尋半徑隨邊緣寬度放大；回報的四角誤差不可低於實際誤差，信心值隨模糊下降。"""
    page = page_image("doc_a4")
    confs = []
    for blur, lim in ((6, 0.5), (16, 2.0)):  # 實測：模糊 σ=6 最大 0.28 px、σ=16 最大 1.38 px
        photo, G, corners = make_photo(page, 1200, 1600, "wood", seed=2, f_frac=0.7, rx=20, ry=8, rz=4)
        photo = degrade(photo, np.random.default_rng(2), blur=blur, noise=3.0, jpeg=80)
        q = find_page_quad(photo)
        assert q.method == "edge_fit"
        e = np.linalg.norm(q.corners - corners, axis=1)
        assert e.max() < lim, (blur, e)
        assert q.corner_error_px.mean() >= e.mean(), (blur, q.corner_error_px, e)
        confs.append(q.confidence)
    assert confs[0] > confs[1] and confs[1] < 0.6


def test_contour_fallback_when_refine_fails():
    """精修全部失敗（以參數強制）：退回縮圖輪廓近似，method="contour"、低信心、誤差數像素。"""
    page = page_image("slide_basic")
    photo, G, corners = make_photo(page, 1600, 1200, "wood", seed=1, f_frac=0.7, rx=25, ry=-10, rz=5)
    photo = degrade(photo, np.random.default_rng(1), blur=1.0, noise=3.0, jpeg=85)
    q = find_page_quad(photo, {"rel_edge_grad": 1e9})
    assert q.method == "contour" and q.confidence < 0.5
    e = np.linalg.norm(q.corners - corners, axis=1)
    assert e.max() < 6.0 and q.corner_error_px.min() >= 2.0, e
