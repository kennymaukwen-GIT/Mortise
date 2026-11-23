"""疊在照片上的平塗形狀（regions.py 的 overlay 判斷與 fill_covered），以及描圖遇到照片時的早期放棄（vectorize.py）。

(a) 展示海報：tests/fixtures/poster.pdf 以 200 dpi 畫成影像（2339×3308），照片裡有一個平塗的黃橢圓與深藍矩形。
    文字遮罩直接用 PDF 的字元框（不跑文字辨識，結果固定、也比較快）。答案由 make_fixtures.py 的數字算出來。
(b) 合成：有顆粒的漸層、多尺度紋理上疊平塗的圓、矩形、圓角矩形、三角形（4 倍超取樣畫，等於反鋸齒）；
    不是基本幾何的心形要描成向量圖（regions 與影像產線①→③兩層都測）。
(c) 合成的「真照片」：多尺度紋理、乾淨但柔邊的色塊、平滑的天空與湖（整張有同樣的感光顆粒）——不可拆出形狀。
(d) 早期放棄：大張雜訊圖、海報的照片、大張 JPEG 照片（1800 寬以上），描圖要在 1 秒內放棄（測試門檻放寬到 2 秒）；
    插畫（含放大三、四倍的雜訊圖）不可被擋。
審查後補的案例：滿版照片（沒有頁面底色）、細條、JPEG 壓縮過的形狀（含三角形的尖角）；照片裡截平成純黑／純白的區塊、
磚牆上的油漆招牌（JPEG）、透明度 0.95 的色塊都不可拆；照片 notes 合併成一句；補圖品質（fill_quality）。
"""

from __future__ import annotations

import math
import time
from pathlib import Path

import cv2
import numpy as np
import pytest
from PIL import Image, ImageDraw

from mortise.recognition.regions import crop_region, fill_covered, find_regions

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"
SS = 4


def _lab(c) -> np.ndarray:
    return cv2.cvtColor(np.array([[c]], np.float32) / 255.0, cv2.COLOR_RGB2Lab)[0, 0]


def _de(a, b) -> float:
    return float(np.linalg.norm(_lab(a) - _lab(b)))


def _grain(px: np.ndarray, mask: np.ndarray) -> float:
    """高通殘差（減去 σ2 高斯平滑）的中位數，量顆粒用（與 regions.py 同一種量法）。"""
    f = px.astype(np.float32)
    r = np.abs(f - cv2.GaussianBlur(f, (0, 0), 2.0)).mean(axis=2)
    return float(np.median(r[mask])) * 1.25


# =====================================================================================
# (a) 展示海報
# =====================================================================================

DPI = 200
K = DPI / 72.0
# make_fixtures.poster()：照片 900×600 畫在 (60, 380) pt，寬 721.89、高 440 pt；A3 直 841.89 × 1190.551 pt
PH_X0, PH_Y0 = 60 * K, (1190.551 - 380 - 440) * K
SX, SY = 721.89 / 900 * K, 440 / 600 * K
# _photo()：橢圓 (w·0.2, h·0.2, w·0.6, h·0.8) → 像素 180–540、120–480（含端點）；矩形 (w·0.55, h·0.45, w·0.9, h·0.85) → 495–810、270–510
GT_ELLIPSE = (PH_X0 + 360.5 * SX, PH_Y0 + 300.5 * SY, 180.5 * SX, 180.5 * SY)  # 中心 x、y、半軸 a、b
GT_RECT = (PH_X0 + 495 * SX, PH_Y0 + 270 * SY, PH_X0 + 811 * SX, PH_Y0 + 511 * SY)


@pytest.fixture(scope="module")
def poster():
    import pypdfium2 as pdfium

    page = pdfium.PdfDocument(str(FIXTURES / "poster.pdf"))[0]
    img = np.asarray(page.render(scale=K).to_pil().convert("RGB")).copy()
    H, W = img.shape[:2]
    tp = page.get_textpage()
    ph = page.get_height()
    tm = np.zeros((H, W), bool)
    for i in range(tp.count_chars()):
        l, b, r, t = tp.get_charbox(i)
        x0, x1 = int(l * K) - 3, int(math.ceil(r * K)) + 3
        y0, y1 = int((ph - t) * K) - 3, int(math.ceil((ph - b) * K)) + 3
        if x1 > x0 and y1 > y0:
            tm[max(0, y0) : y1, max(0, x0) : x1] = True
    t0 = time.time()
    regs = find_regions(img, tm)
    return img, tm, regs, time.time() - t0


def test_poster_shapes_are_split_from_the_photo(poster):
    img, tm, regs, dt = poster
    print(f"\nfind_regions 2339×3308：{dt:.1f} 秒；" + "；".join(f"{r.kind} {r.bbox} z{r.z}" for r in regs))
    photos = [r for r in regs if r.kind == "photo"]
    assert len(photos) == 1  # 漸層底仍是一張圖片
    photo = photos[0]
    over = [r for r in regs if r.kind == "shape" and r.features.get("overlay") == 1.0]
    assert sorted(r.shape.geometry for r in over) == ["ellipse", "rect"]
    ell = next(r for r in over if r.shape.geometry == "ellipse")
    rect = next(r for r in over if r.shape.geometry == "rect")
    q = np.asarray(ell.shape.quad, float)
    cx, cy = q.mean(axis=0)
    a, b = np.linalg.norm(q[1] - q[0]) / 2, np.linalg.norm(q[3] - q[0]) / 2
    err_e = np.abs(np.array([cx, cy, a, b]) - GT_ELLIPSE)
    q = np.asarray(rect.shape.quad, float)
    err_r = np.abs(np.r_[q.min(axis=0), q.max(axis=0)] - GT_RECT)
    de_e, de_r = _de(ell.shape.fill, (235, 200, 70)), _de(rect.shape.fill, (40, 60, 90))
    print(f"橢圓誤差（中心 x、y、半軸 a、b）{np.round(err_e, 2)} px，ΔE {de_e:.2f}；矩形四邊誤差 {np.round(err_r, 2)} px，ΔE {de_r:.2f}")
    assert err_e.max() < 2.0 and err_r.max() < 2.0  # 成果標準：位置誤差 2 px 內
    assert de_e < 3.0 and de_r < 3.0  # 顏色誤差 ΔE < 3
    # 圖層：兩個形狀都在照片之上；矩形壓在橢圓上（橢圓被遮住的部分已補全）
    assert ell.z > photo.z and rect.z > photo.z and rect.z > ell.z
    assert ell.occluded_mask is not None and ell.occluded_mask.sum() > 10000
    # 判斷依據寫在 notes 與 features（可解釋）
    for r in over:
        assert any("疊在照片上的平塗圖形" in n for n in r.notes)
        assert r.features["overlay_grain_ratio"] < 0.35 and r.features["overlay_edge_continuity"] >= 0.85
    assert any("疊了 2 個平塗圖形" in n for n in photo.notes)
    # 其他元素不受影響：上方色帶、FROM $25 的圓角矩形、橫線
    others = [r for r in regs if r not in over and r is not photo]
    geoms = sorted(r.shape.geometry for r in others if r.shape is not None)
    assert geoms == ["line", "rect", "rounded_rect"]
    band = next(r for r in others if r.shape is not None and r.shape.geometry == "rect")
    assert band.shape.fill == (200, 85, 61) and band.bbox[1] == 0


def test_poster_photo_crop_is_filled_under_the_shapes(poster):
    img, tm, regs, _ = poster
    photo = next(r for r in regs if r.kind == "photo")
    cover = np.zeros(img.shape[:2], bool)
    for r in regs:
        if r.kind == "shape" and r.z > photo.z:
            x0, y0, x1, y1 = r.bbox
            cover[y0:y1, x0:x1] |= r.mask
    t0 = time.time()
    filled, gen = fill_covered(img, photo, cover)
    dt = time.time() - t0
    x0, y0, x1, y1 = photo.bbox
    crop = crop_region(img, photo, inpainted=filled)[..., :3]
    g = gen[y0:y1, x0:x1]
    under = cover[y0:y1, x0:x1]
    assert (g & under).sum() >= 0.99 * under.sum()  # 形狀的位置全部補掉
    assert np.array_equal(filled[~gen], img[~gen])  # 其餘像素照抄
    # 補出來的地方不可留下黃、藍的殘影
    px = crop[g].astype(np.float32)
    left = np.mean([(np.abs(px - np.array(c, np.float32)).max(axis=1) <= 30).mean() for c in ((235, 200, 70), (40, 60, 90))])
    # 平滑的部分接近照片原本的漸層（_photo 的底：R = 80 + 120x/w、G = 60 + 140y/h、B = 160 − 80x/w）
    yy, xx = np.mgrid[y0:y1, x0:x1].astype(np.float32)
    u = (xx + 0.5 - PH_X0) / SX - 0.5
    v = (yy + 0.5 - PH_Y0) / SY - 0.5
    truth = np.stack([80 + 120 * u / 900, 60 + 140 * v / 600, 160 - 80 * u / 900], axis=2)
    smooth = cv2.GaussianBlur(crop.astype(np.float32), (0, 0), 6.0)
    inner = cv2.erode(g.astype(np.uint8), np.ones((25, 25), np.uint8)).astype(bool)
    err = float(np.abs(smooth - truth)[inner].mean())
    # 顆粒：補出來的地方與照片其他地方差不多
    gr_fill = _grain(crop, inner)
    gr_photo = _grain(crop, ~cv2.dilate(g.astype(np.uint8), np.ones((25, 25), np.uint8)).astype(bool))
    print(f"\n補照片 {dt:.1f} 秒；殘影比例 {left:.4f}；與原漸層的平均差 {err:.2f} 色階；顆粒 {gr_fill:.2f}（照片 {gr_photo:.2f}）")
    assert left < 0.005
    assert err < 6.0
    assert 0.6 * gr_photo <= gr_fill <= 1.5 * gr_photo


# =====================================================================================
# 合成場景
# =====================================================================================


def _correlated_noise(h: int, w: int, sigma: float, rng, scale: int = 2) -> np.ndarray:
    n = rng.normal(0, sigma, (h // scale + 2, w // scale + 2, 3)).astype(np.float32)
    return cv2.resize(n, ((w // scale + 2) * scale, (h // scale + 2) * scale), interpolation=cv2.INTER_LINEAR)[:h, :w]


def _fractal(h: int, w: int, rng, octaves: int = 6, amp: float = 50.0) -> np.ndarray:
    out = np.zeros((h, w, 3), np.float32)
    for o in range(octaves):
        s = 2 ** (o + 2)
        out += cv2.resize(rng.normal(0, 1, (s, s, 3)).astype(np.float32), (w, h), interpolation=cv2.INTER_CUBIC) * amp / (1.6**o)
    return out


def photo_content(kind: str, h: int, w: int, rng) -> np.ndarray:
    yy, xx = np.mgrid[0:h, 0:w].astype(np.float32)
    if kind == "grain":  # 海報那種：三色漸層加細顆粒（相關長度約 2 px）
        base = np.stack([80 + 120 * xx / w, 60 + 140 * yy / h, 160 - 80 * xx / w], axis=2)
        return base + _correlated_noise(h, w, 10.0, rng)
    if kind == "texture":  # 多尺度紋理加感光顆粒
        return np.array([120, 130, 110], np.float32) + _fractal(h, w, rng) + _correlated_noise(h, w, 3.0, rng)
    if kind == "blobs":  # 漸層底加上「乾淨、但邊緣模糊」的色塊：內部沒有顆粒，只能靠邊緣的銳利度分辨
        base = np.stack([90 + 100 * xx / w, 120 + 60 * yy / h, 170 - 60 * xx / w], axis=2) + _correlated_noise(h, w, 8.0, rng)
        for cx, cy, r, col in ((0.25, 0.35, 0.16, (240, 210, 90)), (0.65, 0.4, 0.14, (40, 60, 120)), (0.45, 0.75, 0.12, (220, 90, 80))):
            m = np.zeros((h, w), np.float32)
            cv2.circle(m, (int(cx * w), int(cy * h)), int(r * min(w, h) * 1.6), 1.0, -1)
            m = cv2.GaussianBlur(m, (0, 0), 6.0)[..., None]
            base = base * (1 - m) + np.array(col, np.float32) * m
        return base
    if kind == "landscape":  # 平滑的天空、起伏的地平線、有紋理的地面、地面中間一片邊緣清楚的平靜湖面；整張有同樣的感光顆粒
        sky = np.array([110, 160, 230], np.float32) * (1 - yy / h)[..., None] + np.array([225, 235, 250], np.float32) * (yy / h)[..., None]
        horizon = 0.42 * h + 18 * np.sin(xx / 60) + 9 * np.sin(xx / 21 + 1)
        ground = np.array([80, 115, 55], np.float32) + _fractal(h, w, rng, amp=40)
        img = np.where((yy > horizon)[..., None], ground, sky)
        lake = ((xx - 0.6 * w) / (0.18 * w)) ** 2 + ((yy - 0.75 * h) / (0.09 * h)) ** 2 + 0.15 * np.sin(xx / 9) < 1.0
        img = np.where(lake[..., None], np.array([70, 120, 170], np.float32) + 6 * (xx / w)[..., None], img)
        return img + _correlated_noise(h, w, 4.0, rng)
    raise ValueError(kind)


class Canvas:
    """頁面（淺色底）上一張照片，照片上可再疊平塗形狀；形狀以 4 倍超取樣畫，縮小後就是反鋸齒的邊緣。"""

    def __init__(self, W: int = 900, H: int = 620):
        self.W, self.H = W, H
        self.img = Image.new("RGB", (W * SS, H * SS), (246, 244, 238))
        self.draw = ImageDraw.Draw(self.img)
        self.truth: list = []

    def photo(self, box, kind: str, seed: int = 0) -> None:
        x0, y0, x1, y1 = box
        c = np.clip(photo_content(kind, y1 - y0, x1 - x0, np.random.default_rng(seed)), 0, 255).astype(np.uint8)
        self.img.paste(Image.fromarray(np.repeat(np.repeat(c, SS, 0), SS, 1)), (x0 * SS, y0 * SS))
        self.draw = ImageDraw.Draw(self.img)
        self.photo_box = box

    def circle(self, cx, cy, r, col):
        self.draw.ellipse([(cx - r) * SS, (cy - r) * SS, (cx + r) * SS, (cy + r) * SS], fill=col)
        self.truth.append(("ellipse", (cx, cy, r), col))

    def rect(self, x0, y0, x1, y1, col):
        self.draw.rectangle([x0 * SS, y0 * SS, x1 * SS - 1, y1 * SS - 1], fill=col)
        self.truth.append(("rect", (x0, y0, x1, y1), col))

    def rrect(self, x0, y0, x1, y1, r, col):
        self.draw.rounded_rectangle([x0 * SS, y0 * SS, x1 * SS - 1, y1 * SS - 1], radius=r * SS, fill=col)
        self.truth.append(("rounded_rect", (x0, y0, x1, y1, r), col))

    def polygon(self, pts, col):
        self.draw.polygon([(x * SS, y * SS) for x, y in pts], fill=col)
        self.truth.append(("polygon", np.array(pts, float), col))

    def render(self) -> np.ndarray:
        return np.asarray(self.img.resize((self.W, self.H), Image.BOX).convert("RGB")).copy()


def _overlay_scene(kind: str, seed: int = 7) -> Canvas:
    cv = Canvas()
    cv.photo((60, 60, 840, 560), kind, seed=seed)
    cv.circle(230, 200, 90, (235, 200, 70))
    cv.rect(430, 110, 650, 260, (40, 60, 90))
    cv.rrect(140, 360, 390, 500, 30, (220, 60, 80))
    cv.polygon([(520, 330), (780, 370), (610, 525)], (60, 170, 120))
    return cv


# =====================================================================================
# (b) 合成：有顆粒或紋理的照片上疊平塗形狀
# =====================================================================================


@pytest.mark.parametrize("kind", ["grain", "texture"])
def test_synthetic_shapes_over_photo(kind):
    cv = _overlay_scene(kind)
    img = cv.render()
    t0 = time.time()
    regs = find_regions(img, np.zeros(img.shape[:2], bool))
    dt = time.time() - t0
    photo = [r for r in regs if r.kind == "photo"]
    assert len(photo) == 1 and photo[0].bbox == (60, 60, 840, 560)
    over = {r.shape.geometry: r for r in regs if r.kind == "shape" and r.features.get("overlay") == 1.0}
    assert sorted(over) == ["ellipse", "polygon", "rect", "rounded_rect"]
    errs = {}
    for geom, prm, col in cv.truth:
        r = over[geom]
        assert r.z > photo[0].z
        assert _de(r.shape.fill, col) < 3.0
        q = np.asarray(r.shape.quad, float)
        if geom == "ellipse":
            cx, cy, rad = prm
            a, b = np.linalg.norm(q[1] - q[0]) / 2, np.linalg.norm(q[3] - q[0]) / 2
            e = max(np.abs(q.mean(axis=0) - (cx, cy)).max(), abs(a - rad), abs(b - rad))
        elif geom in ("rect", "rounded_rect"):
            e = float(np.abs(np.r_[q.min(axis=0), q.max(axis=0)] - prm[:4]).max())
            if geom == "rounded_rect":
                e = max(e, abs(r.shape.corner_radius_px - prm[4]) / 1.5)  # 圓角半徑容許 3 px
        else:
            poly = np.asarray(r.shape.polygon, float)
            assert len(poly) == 3
            e = float(max(np.linalg.norm(poly - p, axis=1).min() for p in prm))
        errs[geom] = round(float(e), 2)
    print(f"\n{kind}：{dt:.2f} 秒，最大誤差 {errs} px")
    assert max(errs.values()) < 2.0


# =====================================================================================
# (c) 合成的真照片：不可拆出形狀
# =====================================================================================


@pytest.mark.parametrize("kind,seed", [("texture", 1), ("texture", 3), ("blobs", 1), ("landscape", 1), ("landscape", 3)])
def test_true_photos_are_not_split(kind, seed):
    cv = Canvas()
    cv.photo((60, 60, 840, 560), kind, seed=seed)
    img = cv.render()
    tm = np.zeros(img.shape[:2], bool)
    dbg: list = []
    regs = find_regions(img, tm, params={"_debug": dbg})
    assert not [r for r in regs if r.features.get("overlay") == 1.0]
    # 與「關掉這個功能」的結果完全相同：新判斷沒有改動真照片的讀法
    regs_off = find_regions(img, tm, params={"overlay_enabled": False})
    assert sorted((r.kind, r.bbox) for r in regs) == sorted((r.kind, r.bbox) for r in regs_off)
    judged = [c for c in dbg[0]["candidates"] if "overlay_grain" in c["feats"]]
    print(f"\n{kind}/{seed}：判斷過 {len(judged)} 塊平坦色塊，" + "；".join(
        f"顆粒比 {c['feats']['overlay_grain_ratio']:.2f}、邊緣 {c['feats']['overlay_edge_continuity']:.0%}" for c in judged))
    if kind == "blobs":  # 乾淨但柔邊的色塊：被量到了，因為邊緣不銳利而沒拆
        assert len(judged) == 3 and all(c["feats"]["overlay_edge_continuity"] < 0.5 for c in judged)
    if kind == "landscape":  # 湖面邊緣清楚，但帶著照片自己的顆粒
        lake = [c for c in judged if c["feats"]["area_px"] > 10000]
        assert lake and all(c["feats"]["overlay_grain_ratio"] > 0.5 for c in lake)


def _heart(cx: float, cy: float, k: float, n: int = 400) -> list:
    t = np.linspace(0, 2 * np.pi, n, endpoint=False)
    hx = 16 * np.sin(t) ** 3
    hy = -(13 * np.cos(t) - 5 * np.cos(2 * t) - 2 * np.cos(3 * t) - np.cos(4 * t))
    return [(cx + k * a, cy + k * b) for a, b in zip(hx, hy)]


@pytest.mark.parametrize("kind", ["grain", "texture"])
def test_non_primitive_overlay_is_traced(kind):
    """外形不是基本幾何的平塗圖形（心形）：描圖過關才拆成插畫（之後描成向量圖），疊在照片上。
    有顆粒的照片上，描圖前要先把形狀外面換成單色，不然會因「色塊太多」被拒絕而留在照片裡。"""
    cv = Canvas()
    cv.photo((60, 60, 840, 560), kind, seed=7)
    cv.draw.polygon([(x * SS, y * SS) for x, y in _heart(300, 300, 9)], fill=(220, 40, 90))
    img = cv.render()
    regs = find_regions(img, np.zeros(img.shape[:2], bool))
    photo = next(r for r in regs if r.kind == "photo")
    ill = [r for r in regs if r.kind == "illustration" and r.features.get("overlay") == 1.0]
    assert len(ill) == 1 and ill[0].z > photo.z
    assert ill[0].features["overlay_trace_paths"] >= 1 and any("描圖過關" in n for n in ill[0].notes)
    x0, y0, x1, y1 = ill[0].bbox
    assert abs(x0 - 156) <= 2 and abs(y0 - 192) <= 2 and abs(x1 - 444) <= 2 and abs(y1 - 449) <= 2


def test_traced_overlay_in_the_image_line(tmp_path):
    """影像產線（①→③）：心形描成向量圖、矩形成為形狀，都疊在照片上；插畫的裁圖取自沒補過的影像（是心形本身，
    不是補出來的照片），照片的裁圖在兩個圖形的位置已補掉，並照實記成生成。"""
    import io

    from mortise.artifacts import Intent
    from mortise.config import Config
    from mortise.pipeline.store import Store
    from mortise.stages import s1_input, s2_page, s3_recognize

    cv = Canvas()
    cv.photo((60, 60, 840, 560), "grain", seed=7)
    heart = _heart(300, 300, 9)
    cv.draw.polygon([(x * SS, y * SS) for x, y in heart], fill=(220, 40, 90))
    cv.rect(560, 200, 760, 330, (40, 60, 90))
    png = tmp_path / "heart.png"
    Image.fromarray(cv.render()).save(png)
    cfg, store = Config.load(), Store(tmp_path / "job")
    doc = s1_input.run(png, png.name, Intent(), cfg, store)
    spec = s2_page.run(doc, 0, cfg, [], store)
    el = s3_recognize.run(doc, spec, cfg, [], store)
    photo = next(i for i in el.images if i.kind.value == "photo")
    ill = next(i for i in el.images if i.kind.value == "illustration")
    assert ill.z > photo.z
    assert [s.geometry.value for s in el.shapes] == ["rect"] and el.shapes[0].z > photo.z
    vec = next(v for v in el.vectors if v.image_id == ill.id)
    f = vec.paths[0].fill
    assert len(vec.paths) == 1 and _de((f.r, f.g, f.b), (220, 40, 90)) < 3.0
    # 插畫的裁圖：不透明的像素是心形的顏色
    a = np.asarray(Image.open(io.BytesIO(store.read_blob(ill.image))))
    solid = a[..., 3] >= 250
    assert solid.sum() > 40000 and (np.abs(a[solid][:, :3].astype(int) - (220, 40, 90)).max(axis=1) <= 30).mean() > 0.99
    # 照片的裁圖：心形與矩形的位置都補掉了，沒有殘影
    p = np.asarray(Image.open(io.BytesIO(store.read_blob(photo.image))))[..., :3].astype(int)
    m = Image.new("L", (900, 620), 0)
    ImageDraw.Draw(m).polygon(heart, fill=255)
    ImageDraw.Draw(m).rectangle([560, 200, 759, 329], fill=255)
    m = np.asarray(m)[60:560, 60:840] > 0
    for col in ((220, 40, 90), (40, 60, 90)):
        assert (np.abs(p[m] - col).max(axis=1) <= 30).mean() < 0.005
    assert any(photo.id in n and "生成" in n for n in el.notes)


def test_overlay_can_be_turned_off():
    img = _overlay_scene("grain").render()
    regs = find_regions(img, np.zeros(img.shape[:2], bool), params={"overlay_enabled": False})
    assert [r.kind for r in regs] == ["photo"]  # 舊的讀法：整塊是照片


# =====================================================================================
# (d) 描圖遇到照片要很快放棄
# =====================================================================================


def test_vectorize_gives_up_fast_on_big_noise():
    from mortise.recognition.vectorize import vectorize

    img = np.random.default_rng(0).integers(0, 256, (2000, 2000, 3), dtype=np.uint8)
    t0 = time.time()
    res = vectorize(img)
    dt = time.time() - t0
    print(f"\n2000×2000 雜訊圖：{dt:.2f} 秒；{res.notes[0]}")
    assert not res.accepted and not res.paths and dt < 2.0
    assert "不適合" in res.notes[0] and "快速檢查" in res.notes[0]


def test_vectorize_gives_up_fast_on_the_poster_photo(poster):
    """原本 57.6 秒才放棄（色塊太多）。"""
    from mortise.recognition.vectorize import vectorize

    img, _, regs, _ = poster
    x0, y0, x1, y1 = next(r for r in regs if r.kind == "photo").bbox
    t0 = time.time()
    res = vectorize(np.ascontiguousarray(img[y0:y1, x0:x1]))
    dt = time.time() - t0
    print(f"\n海報的照片 {x1 - x0}×{y1 - y0}：{dt:.2f} 秒；{res.notes[0]}")
    assert not res.accepted and dt < 2.0 and "不適合" in res.notes[0]


def test_precheck_lets_illustrations_through():
    """早期判斷不可擋掉插畫：評測樣本（含加雜訊、JPEG）與放大三倍後的雜訊版（相關雜訊，最像照片的插畫）。"""
    from mortise.eval.vector_bench import SEED, VARIANTS, make_samples, perturb
    from mortise.recognition.vector_segment import photo_precheck
    from mortise.recognition.vectorize import _params

    P = _params(None)
    samples = make_samples(SEED)
    worst = worst_small = 0.0
    for si in range(0, len(samples), 4):  # 每類別都取到（樣本依類別排列，每類 8 個）
        s = samples[si]
        for v in ("clean", "jpeg", "noise"):
            img, _, _ = perturb(s, v, np.random.default_rng([SEED, si, VARIANTS.index(v)]))
            for k in (1, 3, 4):  # 放大後大於 560 px，也會走縮小的第二個尺度
                im = img if k == 1 else cv2.resize(img, (img.shape[1] * k, img.shape[0] * k), interpolation=cv2.INTER_CUBIC)
                why, st = photo_precheck(im, None, P)
                assert why is None, f"{s.name}/{v}/×{k}：{why}"
                worst = max(worst, st.get("photo_check_texture_de", 0.0))
                worst_small = max(worst_small, st.get("photo_check_small_texture_de", 0.0))
    print(f"\n插畫的平坦方塊殘差中位數最大 ΔE {worst:.2f}（門檻 {P['photo_check_texture_de']}）；"
          f"縮小後最大 ΔE {worst_small:.2f}（門檻 {P['photo_check_texture_de_small']}）")


# =====================================================================================
# 審查後補的案例
# =====================================================================================


def _jpeg(img: np.ndarray, q: int) -> np.ndarray:
    import io

    b = io.BytesIO()
    Image.fromarray(img).save(b, "JPEG", quality=q)
    return np.asarray(Image.open(io.BytesIO(b.getvalue())).convert("RGB")).copy()


def _shape_error(geom: str, prm, r) -> float:
    q = np.asarray(r.shape.quad, float)
    if geom == "ellipse":
        cx, cy, rad = prm
        a, b = np.linalg.norm(q[1] - q[0]) / 2, np.linalg.norm(q[3] - q[0]) / 2
        return float(max(np.abs(q.mean(axis=0) - (cx, cy)).max(), abs(a - rad), abs(b - rad)))
    if geom in ("rect", "rounded_rect"):
        return float(np.abs(np.r_[q.min(axis=0), q.max(axis=0)] - prm[:4]).max())
    poly = np.asarray(r.shape.polygon, float)
    return float(max(np.linalg.norm(poly - p, axis=1).min() for p in prm)) if len(poly) == len(prm) else 99.0


@pytest.mark.parametrize("kind,n", [("grain", 1), ("grain", 3), ("texture", 1), ("texture", 3)])
def test_full_bleed_photo_shapes_are_split(kind, n):
    """滿版照片（整張都是照片，沒有頁面底色）：原本唯一、最大的那個形狀會被當成「頁面底色」而拆不出來。"""
    cv = Canvas()
    cv.photo((0, 0, 900, 620), kind, seed=7)
    if n == 1:
        cv.rect(200, 380, 700, 520, (40, 60, 90))
    else:
        cv.circle(230, 200, 90, (235, 200, 70))
        cv.rect(430, 110, 650, 260, (40, 60, 90))
        cv.rrect(140, 360, 390, 500, 30, (220, 60, 80))
    img = cv.render()
    regs = find_regions(img, np.zeros(img.shape[:2], bool))
    photo = [r for r in regs if r.kind == "photo"]
    assert len(photo) == 1 and photo[0].bbox == (0, 0, 900, 620)
    over = {r.shape.geometry: r for r in regs if r.kind == "shape" and r.features.get("overlay") == 1.0}
    assert sorted(over) == sorted(g for g, _, _ in cv.truth)
    for geom, prm, col in cv.truth:
        assert over[geom].z > photo[0].z and _de(over[geom].shape.fill, col) < 3.0
        assert _shape_error(geom, prm, over[geom]) < 2.0


@pytest.mark.parametrize("kind", ["grain", "texture"])
def test_full_bleed_photo_without_shapes_keeps_the_old_reading(kind):
    """滿版照片上沒有疊上去的圖形：照原本的讀法，結果與關掉這個功能時相同。"""
    cv = Canvas()
    cv.photo((0, 0, 900, 620), kind, seed=7)
    img = cv.render()
    tm = np.zeros(img.shape[:2], bool)
    regs = find_regions(img, tm)
    off = find_regions(img, tm, params={"overlay_enabled": False})
    assert sorted((r.kind, r.bbox) for r in regs) == sorted((r.kind, r.bbox) for r in off)


def test_thin_bar_over_photo_is_split():
    """12 px 高的細條：內縮 7 px 後量不到顆粒，改用較小的高通再量（原本被一個哨兵值判成「顆粒與照片差不多」）。"""
    cv = Canvas()
    cv.photo((60, 60, 840, 560), "grain", seed=7)
    cv.rect(150, 200, 650, 212, (235, 200, 70))
    img = cv.render()
    regs = find_regions(img, np.zeros(img.shape[:2], bool))
    over = [r for r in regs if r.features.get("overlay") == 1.0]
    assert len(over) == 1 and over[0].shape.geometry == "rect"
    assert _shape_error("rect", cv.truth[0][1], over[0]) < 2.0 and over[0].features["overlay_hp_sigma"] == 1.0


@pytest.mark.parametrize("kind,q,seed,strict", [("grain", 90, 7, True), ("texture", 90, 7, True), ("grain", 75, 7, True), ("texture", 75, 7, False),
                                              ("grain", 60, 7, True), ("texture", 75, 11, True), ("texture", 60, 11, True)])
def test_jpeg_shapes_over_photo(kind, q, seed, strict):
    """JPEG 壓縮過的照片加形狀：橢圓、矩形、圓角矩形要拆對；三角形的尖角會被壓縮沿 8×8 區塊削掉，要補回來。
    strict 的組合三角形一定要拆成三個頂點、誤差 2 px 內。texture 種子 7 的左上尖角旁邊，照片的顏色接近三角形，
    壓縮後尖角被吃掉十幾個像素，q85 以下補不回來（已知限制），這裡只檢查拆出來時不能錯。
    拆出來的三角形要把真的三角形整個蓋住（不然照片裡會留下殘角）。"""
    cv = _overlay_scene(kind, seed=seed)
    img = _jpeg(cv.render(), q)
    regs = find_regions(img, np.zeros(img.shape[:2], bool))
    photo = next(r for r in regs if r.kind == "photo")
    over = {r.shape.geometry: r for r in regs if r.kind == "shape" and r.features.get("overlay") == 1.0}
    errs = {}
    for geom, prm, col in cv.truth:
        r = over.get(geom)
        if geom == "polygon":
            tri = [r for r in regs if r.features.get("overlay") == 1.0 and r.bbox[0] > 480 and r.bbox[1] > 300]
            if strict:
                assert r is not None and len(r.shape.polygon) == 3
            if tri and r is not None and len(r.shape.polygon) == 3:
                errs[geom] = round(_shape_error(geom, prm, r), 2)
                assert errs[geom] < 2.0
                m = Image.new("L", (900, 620), 0)
                ImageDraw.Draw(m).polygon([tuple(p) for p in prm], fill=255)
                truth = cv2.erode((np.asarray(m) > 0).astype(np.uint8), np.ones((3, 3), np.uint8)).astype(bool)
                x0, y0, x1, y1 = r.bbox
                cov = np.zeros(truth.shape, bool)
                cov[y0:y1, x0:x1] = r.mask
                assert (truth & ~cov).sum() == 0
            else:
                errs[geom] = "沒拆成三角形" if tri else "沒拆"
            continue
        assert r is not None and r.z > photo.z and _de(r.shape.fill, col) < 3.0
        errs[geom] = round(_shape_error(geom, prm, r), 2)
        assert errs[geom] < 2.0
    print(f"\n{kind}（種子 {seed}）JPEG{q}：{errs}")


def _wall(h: int, w: int, rng, base) -> np.ndarray:
    return np.array(base, np.float32) + _fractal(h, w, rng, amp=35)


def _brick(h: int, w: int, rng) -> np.ndarray:
    yy, xx = np.mgrid[0:h, 0:w].astype(np.float32)
    off = ((yy // 28).astype(int) % 2) * 30
    mortar = ((yy % 28) < 3) | (((xx + off) % 60) < 3)
    base = np.array([150, 75, 55], np.float32) + _fractal(h, w, rng, amp=18)
    return np.where(mortar[..., None], np.array([190, 185, 175], np.float32), base)


def _true_photo_scene(name: str, seed: int) -> np.ndarray:
    """照片內容與照片同一次「曝光」：最後才加整張的感光顆粒，所以照片裡的平坦處也帶著同樣的顆粒（除非截平）。"""
    h, w = 500, 780
    rng = np.random.default_rng(seed)
    yy, xx = np.mgrid[0:h, 0:w].astype(np.float32)
    if name == "black_doorway":  # 有紋理的牆上一個暗到截成純黑的門洞
        img = _wall(h, w, rng, (170, 160, 140))
        m = (xx > 0.35 * w) & (xx < 0.6 * w) & (yy > 0.25 * h) & (yy < 0.9 * h)
        img = np.where(m[..., None], np.float32(-30), img)
    elif name == "white_window":  # 過曝成純白的窗
        img = _wall(h, w, rng, (120, 110, 100))
        m = (xx > 0.35 * w) & (xx < 0.6 * w) & (yy > 0.25 * h) & (yy < 0.7 * h)
        img = np.where(m[..., None], np.float32(300), img)
    elif name == "sign":  # 磚牆上一塊油漆招牌（均勻、邊緣銳利），整張同樣的顆粒
        img = _brick(h, w, rng)
        m = (xx > 0.3 * w) & (xx < 0.7 * w) & (yy > 0.3 * h) & (yy < 0.6 * h)
        img = np.where(m[..., None], np.array([30, 90, 160], np.float32), img)
    else:
        raise ValueError(name)
    img = img + _correlated_noise(h, w, 3.0, rng)
    page = np.full((620, 900, 3), (246, 244, 238), np.uint8)
    page[60:560, 60:840] = np.clip(np.rint(img), 0, 255).astype(np.uint8)
    return page


@pytest.mark.parametrize("name,q", [("black_doorway", 0), ("black_doorway", 75), ("white_window", 0), ("white_window", 85),
                                    ("sign", 0), ("sign", 65), ("sign", 75)])
def test_photo_content_is_not_split(name, q):
    """照片自己的內容不可拆：截平成純黑、純白的門洞與窗（內部同樣沒有顆粒、邊緣同樣銳利）；磚牆上的招牌經 JPEG 後，
    均勻處的顆粒被壓得比磚紋少（照片沒有本身平坦的地方，比值要求更嚴）。沒拆的原因合併成照片 notes 的一句。"""
    img = _true_photo_scene(name, seed=1)
    if q:
        img = _jpeg(img, q)
    regs = find_regions(img, np.zeros(img.shape[:2], bool))
    assert not [r for r in regs if r.features.get("overlay") == 1.0]
    photo = next(r for r in regs if r.kind == "photo")
    said = [n for n in photo.notes if "沒有拆出來" in n]
    assert len(said) <= 1  # 合併成一句（磚牆原本每塊磚各一句，四十幾句）；招牌沒壓縮時帶著顆粒，根本不是平坦候選
    if name != "sign":
        assert len(said) == 1 and ("純黑" in said[0] or "純白" in said[0])
    print(f"\n{name} q{q}：{said[0][:120] if said else '（沒有平坦候選）'}")


@pytest.mark.parametrize("alpha", [0.8, 0.95])
def test_translucent_box_is_not_split(alpha):
    """半透明的色塊壓在照片上：透明度 0.95 時內部只透出一點照片的顆粒，頁面底色又完全乾淨——當成半透明，不拆。"""
    cv = Canvas()
    cv.photo((60, 60, 840, 560), "grain", seed=7)
    base = np.asarray(cv.img).astype(np.float32)
    x0, y0, x1, y1 = 200 * SS, 150 * SS, 600 * SS, 400 * SS
    base[y0:y1, x0:x1] = base[y0:y1, x0:x1] * (1 - alpha) + np.array((40, 60, 90), np.float32) * alpha
    cv.img = Image.fromarray(np.clip(np.rint(base), 0, 255).astype(np.uint8))
    img = cv.render()
    regs = find_regions(img, np.zeros(img.shape[:2], bool))
    assert not [r for r in regs if r.features.get("overlay") == 1.0]


def test_fill_quality_tells_smooth_fills_apart():
    """補圖品質：漸層加顆粒的照片補得好（中尺度紋理接近周圍），多尺度紋理的照片補出來是一團平滑的色塊。"""
    from mortise.recognition.regions import fill_quality

    q = {}
    for kind in ("grain", "texture"):
        img = _overlay_scene(kind).render()
        regs = find_regions(img, np.zeros(img.shape[:2], bool))
        photo = next(r for r in regs if r.kind == "photo")
        cover = np.zeros(img.shape[:2], bool)
        for r in regs:
            if r.kind in ("shape", "illustration") and r.z > photo.z:
                x0, y0, x1, y1 = r.bbox
                cover[y0:y1, x0:x1] |= r.mask
        filled, gen = fill_covered(img, photo, cover)
        q[kind] = fill_quality(filled, gen, photo)
    print(f"\n補圖品質（中尺度紋理比）：{q}")
    assert q["grain"] >= 0.6 and q["texture"] < 0.5


@pytest.mark.parametrize("kind,size", [("landscape", (1800, 1200)), ("texture", (2400, 1600)), ("landscape", (3000, 2000))])
def test_vectorize_gives_up_fast_on_big_jpeg_photos(kind, size):
    """大張、壓縮過的照片：固定 40 px 的方塊在高解析度下太平滑，原本擋不住（風景 2200×1500 的 JPEG 描了 498 秒）；
    現在另外縮到 560 px 再看一次。"""
    from mortise.recognition.vectorize import vectorize

    w, h = size
    img = _jpeg(np.clip(photo_content(kind, h, w, np.random.default_rng(5)), 0, 255).astype(np.uint8), 75)
    t0 = time.time()
    res = vectorize(img)
    dt = time.time() - t0
    print(f"\n{kind} {w}×{h} JPEG75：{dt:.2f} 秒；{res.notes[0]}")
    assert not res.accepted and not res.paths and dt < 2.0 and "快速檢查" in res.notes[0]
