"""regions.py 的測試：合成場景（答案已知）與原生 PDF fixture。

合成場景以 4 倍超取樣畫向量物件再縮小（等於反鋸齒），照片以 1 倍像素貼上，
文字以真字型畫上去，text_mask 取文字外接框略為膨脹（與 fixture 的做法相同）。
干擾：模糊、雜訊、JPEG、低解析度（縮小再放大）、光照不均。
門檻設在實際量到的水準附近（見各測試的註解）。
"""

from __future__ import annotations

import io
import math
import time
from dataclasses import dataclass, field
from pathlib import Path

import cv2
import numpy as np
import pytest
from PIL import Image, ImageDraw, ImageFont

from mortise.recognition.regions import DEFAULT_PARAMS, crop_region, estimate_background, find_regions, fit_shape

SS = 4
FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"
_FONT_PATHS = [
    "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
]


def _font(px: int):
    for p in _FONT_PATHS:
        if Path(p).exists():
            return ImageFont.truetype(p, px)
    return ImageFont.load_default()


# =====================================================================================
# 合成場景
# =====================================================================================


@dataclass
class Obj:
    kind: str  # photo、illustration、shape、table
    geometry: str = ""  # rect、rounded_rect、ellipse、line（shape 才有）
    mask: np.ndarray | None = None  # 1 倍 bool
    quad: np.ndarray | None = None  # (4, 2)，line 為 p0、p1、p1、p0
    fill: tuple | None = None
    radius: float | None = None
    stroke_width: float | None = None
    h_lines: list = field(default_factory=list)
    v_lines: list = field(default_factory=list)
    name: str = ""


class Scene:
    def __init__(self, W: int, H: int, bg: str, seed: int = 0):
        self.W, self.H = W, H
        self.rng = np.random.default_rng(seed)
        yy, xx = np.mgrid[0 : H * SS, 0 : W * SS].astype(np.float32)
        if bg == "light":
            base = np.zeros((H * SS, W * SS, 3), np.float32) + np.array([246, 244, 238], np.float32)
        elif bg == "dark":
            base = np.zeros((H * SS, W * SS, 3), np.float32) + np.array([20, 30, 48], np.float32)
        else:  # 漸層：左上淺藍到右下較深
            t = (0.6 * yy / (H * SS) + 0.4 * xx / (W * SS))[..., None]
            base = np.array([232, 238, 248], np.float32) * (1 - t) + np.array([150, 175, 215], np.float32) * t
        self.img = Image.fromarray(base.astype(np.uint8))
        self.draw = ImageDraw.Draw(self.img)
        self.objs: list[Obj] = []
        self.text_boxes: list[tuple] = []
        self.quads: list[np.ndarray] = []

    # ---- 工具 ----
    def _mask_of(self, painter) -> np.ndarray:
        m = Image.new("L", self.img.size, 0)
        painter(ImageDraw.Draw(m), 255)
        a = np.asarray(m.resize((self.W, self.H), Image.BOX), np.float32) / 255.0
        return a >= 0.5

    @staticmethod
    def _rot_quad(cx, cy, w, h, ang_deg):
        t = math.radians(ang_deg)
        c, s = math.cos(t), math.sin(t)
        loc = [(-w / 2, -h / 2), (w / 2, -h / 2), (w / 2, h / 2), (-w / 2, h / 2)]
        return np.array([(cx + u * c - v * s, cy + u * s + v * c) for u, v in loc])

    @staticmethod
    def _to_ss(pts):
        return [(x * SS - 0.5, y * SS - 0.5) for x, y in pts]

    # ---- 形狀 ----
    def rect(self, x0, y0, x1, y1, fill, name="rect"):
        p = lambda d, f: d.rectangle([x0 * SS, y0 * SS, x1 * SS - 1, y1 * SS - 1], fill=f)
        p(self.draw, fill)
        q = np.array([(x0, y0), (x1, y0), (x1, y1), (x0, y1)], float)
        self.objs.append(Obj("shape", "rect", self._mask_of(p), q, fill, name=name))

    def rot_rect(self, cx, cy, w, h, ang, fill, name="rot_rect"):
        q = self._rot_quad(cx, cy, w, h, ang)
        p = lambda d, f: d.polygon(self._to_ss(q), fill=f)
        p(self.draw, fill)
        self.objs.append(Obj("shape", "rect", self._mask_of(p), q, fill, name=name))

    def rrect(self, x0, y0, x1, y1, r, fill, name="rrect"):
        p = lambda d, f: d.rounded_rectangle([x0 * SS, y0 * SS, x1 * SS - 1, y1 * SS - 1], radius=r * SS, fill=f)
        p(self.draw, fill)
        q = np.array([(x0, y0), (x1, y0), (x1, y1), (x0, y1)], float)
        self.objs.append(Obj("shape", "rounded_rect", self._mask_of(p), q, fill, radius=r, name=name))

    def ellipse(self, cx, cy, a, b, ang, fill, name="ellipse"):
        t = np.linspace(0, 2 * np.pi, 400, endpoint=False)
        c, s = math.cos(math.radians(ang)), math.sin(math.radians(ang))
        pts = [(cx + a * math.cos(u) * c - b * math.sin(u) * s, cy + a * math.cos(u) * s + b * math.sin(u) * c) for u in t]
        p = lambda d, f: d.polygon(self._to_ss(pts), fill=f)
        p(self.draw, fill)
        q = self._rot_quad(cx, cy, 2 * a, 2 * b, ang)
        self.objs.append(Obj("shape", "ellipse", self._mask_of(p), q, fill, name=name))

    def line(self, x0, y0, x1, y1, width, color, name="line"):
        d = np.array([x1 - x0, y1 - y0], float)
        L = np.linalg.norm(d)
        n = np.array([-d[1], d[0]]) / L * width / 2
        poly = [(x0 + n[0], y0 + n[1]), (x1 + n[0], y1 + n[1]), (x1 - n[0], y1 - n[1]), (x0 - n[0], y0 - n[1])]
        p = lambda dr, f: dr.polygon(self._to_ss(poly), fill=f)
        p(self.draw, color)
        q = np.array([(x0, y0), (x1, y1), (x1, y1), (x0, y0)], float)
        self.objs.append(Obj("shape", "line", self._mask_of(p), q, color, stroke_width=width, name=name))

    def photo(self, x0, y0, x1, y1, name="photo", ellipse=False):
        w, h = x1 - x0, y1 - y0
        yy, xx = np.mgrid[0:h, 0:w].astype(np.float32)
        c0, c1, c2 = self.rng.uniform(30, 230, (3, 3))
        base = c0 * (1 - xx / w)[..., None] * (1 - yy / h)[..., None] + c1 * (xx / w)[..., None] + c2 * (yy / h)[..., None] * 0.7
        for _ in range(6):  # 柔和的物件
            cx, cy, r = self.rng.uniform(0, w), self.rng.uniform(0, h), self.rng.uniform(10, 40)
            col = self.rng.uniform(0, 255, 3)
            m = np.exp(-(((xx - cx) ** 2 + (yy - cy) ** 2) / (2 * r * r)))[..., None]
            base = base * (1 - 0.7 * m) + col * 0.7 * m
        base += self.rng.normal(0, 14, base.shape)
        patch = np.clip(base, 0, 255).astype(np.uint8)
        big = np.repeat(np.repeat(patch, SS, 0), SS, 1)
        if ellipse:
            p = lambda d, f: d.ellipse([x0 * SS, y0 * SS, x1 * SS - 1, y1 * SS - 1], fill=f)
        else:
            p = lambda d, f: d.rectangle([x0 * SS, y0 * SS, x1 * SS - 1, y1 * SS - 1], fill=f)
        mk = Image.new("L", self.img.size, 0)
        p(ImageDraw.Draw(mk), 255)
        self.img.paste(Image.fromarray(big), (x0 * SS, y0 * SS), mk.crop((x0 * SS, y0 * SS, x1 * SS, y1 * SS)))
        self.draw = ImageDraw.Draw(self.img)
        q = np.array([(x0, y0), (x1, y0), (x1, y1), (x0, y1)], float)
        self.objs.append(Obj("photo", "ellipse" if ellipse else "rect", self._mask_of(p), q, name=name))

    def illustration(self, x0, y0, s, name="illus"):
        """色數少、邊緣銳利、但不是簡單幾何：雲朵＋波浪＋葉子。"""
        cols = [(70, 160, 90), (240, 170, 40), (60, 90, 170)]
        def painter(d, f):
            for cx, cy, r in ((0.3, 0.45, 0.18), (0.5, 0.35, 0.22), (0.7, 0.45, 0.17), (0.5, 0.55, 0.2)):
                d.ellipse([(x0 + (cx - r) * s) * SS, (y0 + (cy - r) * s) * SS, (x0 + (cx + r) * s) * SS, (y0 + (cy + r) * s) * SS], fill=f if f == 255 else cols[0])
            t = np.linspace(0, 1, 60)
            top = [(x0 + (0.1 + 0.8 * u) * s, y0 + (0.72 + 0.06 * math.sin(u * 4 * math.pi)) * s) for u in t]
            bot = [(x0 + (0.9 - 0.8 * u) * s, y0 + (0.86 + 0.05 * math.sin(u * 3 * math.pi)) * s) for u in t]
            d.polygon(self._to_ss(top + bot), fill=f if f == 255 else cols[1])
            leaf = [(x0 + (0.45 + 0.12 * math.sin(u * math.pi)) * s, y0 + (0.2 + 0.45 * u) * s) for u in t] + \
                   [(x0 + (0.45 - 0.05 * math.sin(u * math.pi)) * s, y0 + (0.65 - 0.45 * u) * s) for u in t]
            d.polygon(self._to_ss(leaf), fill=f if f == 255 else cols[2])
        painter(self.draw, None)
        q = np.array([(x0, y0), (x0 + s, y0), (x0 + s, y0 + s), (x0, y0 + s)], float)
        self.objs.append(Obj("illustration", "", self._mask_of(painter), q, name=name))

    def table(self, x0, y0, col_w, row_h, ncol, nrow, width=1.0, color=(40, 40, 40), name="table"):
        xs = [x0 + i * col_w for i in range(ncol + 1)]
        ys = [y0 + j * row_h for j in range(nrow + 1)]
        def painter(d, f):
            for x in xs:
                d.rectangle([(x - width / 2) * SS, ys[0] * SS - width / 2 * SS, (x + width / 2) * SS - 1, (ys[-1] + width / 2) * SS - 1], fill=f if f == 255 else color)
            for y in ys:
                d.rectangle([xs[0] * SS - width / 2 * SS, (y - width / 2) * SS, (xs[-1] + width / 2) * SS - 1, (y + width / 2) * SS - 1], fill=f if f == 255 else color)
        painter(self.draw, None)
        m = np.zeros((self.H, self.W), bool)
        m[int(ys[0] - 1) : int(math.ceil(ys[-1] + 1)), int(xs[0] - 1) : int(math.ceil(xs[-1] + 1))] = True
        q = np.array([(xs[0], ys[0]), (xs[-1], ys[0]), (xs[-1], ys[-1]), (xs[0], ys[-1])], float)
        self.objs.append(Obj("table", "", m, q, h_lines=ys, v_lines=xs, name=name))
        # 儲存格文字
        for j in range(nrow):
            for i in range(ncol):
                self.text(xs[i] + 6, ys[j] + row_h * 0.25, f"c{j}{i} {(i + 1) * 13}", int(row_h * 0.45), (30, 30, 30))

    def text(self, x, y, s, px, color):
        f = _font(px * SS)
        self.draw.text((x * SS, y * SS), s, font=f, fill=color)
        bb = self.draw.textbbox((x * SS, y * SS), s, font=f)
        b = (bb[0] / SS, bb[1] / SS, bb[2] / SS, bb[3] / SS)
        self.text_boxes.append(b)
        self.quads.append(np.array([(b[0], b[1]), (b[2], b[1]), (b[2], b[3]), (b[0], b[3])], float))

    def render(self) -> tuple[np.ndarray, np.ndarray]:
        img = np.asarray(self.img.resize((self.W, self.H), Image.BOX).convert("RGB")).copy()
        tm = np.zeros((self.H, self.W), np.uint8)
        for b in self.text_boxes:
            cv2.rectangle(tm, (int(b[0]) - 2, int(b[1]) - 2), (int(math.ceil(b[2])) + 2, int(math.ceil(b[3])) + 2), 1, -1)
        return img, tm.astype(bool)


def scene_basic(bg: str, seed: int = 0) -> Scene:
    """每一類都有：矩形、旋轉矩形、圓角矩形、橢圓、直線、照片、插畫、表格；文字壓在色塊上。"""
    s = Scene(960, 640, bg, seed)
    dark = bg == "dark"
    s.rect(40, 40, 230, 160, (46, 134, 171))
    s.text(60, 80, "Quarterly", 26, (255, 255, 255))
    s.rot_rect(370, 110, 170, 90, 17, (232, 106, 51))
    s.rrect(520, 40, 720, 170, 22, (242, 193, 78))
    s.text(545, 90, "FROM $25", 30, (40, 40, 60))
    s.ellipse(850, 110, 70, 48, 20, (140, 190, 120))
    s.line(40, 215, 600, 215, 4, (200, 60, 60) if not dark else (240, 200, 80))
    s.line(640, 200, 920, 260, 3, (90, 60, 160) if not dark else (200, 180, 250))
    s.photo(40, 270, 300, 450)
    s.illustration(340, 270, 170)
    s.table(560, 300, 120, 34, 3, 4, width=1.5, color=(40, 40, 40) if not dark else (220, 220, 220))
    s.text(40, 500, "Body text on the page background, not a region.", 22, (30, 30, 30) if not dark else (230, 230, 230))
    s.ellipse(150, 580, 40, 40, 0, (180, 80, 160), name="circle")
    s.rrect(260, 545, 470, 615, 12, (90, 120, 200), name="rrect_small")
    return s


def scene_overlap(seed: int = 3) -> Scene:
    """可重疊與上下關係：色塊上壓照片、兩個色塊部分重疊、照片圓形裁切。"""
    s = Scene(900, 600, "light", seed)
    s.rect(40, 40, 420, 330, (60, 70, 110), name="card")
    s.photo(110, 100, 350, 270, name="photo_on_card")
    s.rect(480, 60, 700, 220, (230, 120, 60), name="under")
    s.rect(620, 160, 840, 320, (80, 160, 140), name="over")
    s.photo(80, 390, 300, 560, name="round_photo", ellipse=True)
    s.ellipse(560, 470, 120, 70, 0, (240, 200, 60), name="ellipse_text")
    s.text(470, 455, "TEXT OVER SHAPE", 26, (20, 20, 20))
    return s


def distort(img: np.ndarray, kind: str, seed: int = 0) -> np.ndarray:
    rng = np.random.default_rng(seed)
    f = img.astype(np.float32)
    if kind == "blur":
        f = cv2.GaussianBlur(f, (0, 0), 1.2)
    elif kind == "noise":
        f = f + rng.normal(0, 6, f.shape)
    elif kind == "jpeg":
        buf = io.BytesIO()
        Image.fromarray(img).save(buf, "JPEG", quality=60)
        f = np.asarray(Image.open(buf).convert("RGB"), np.float32)
    elif kind == "lowres":
        h, w = img.shape[:2]
        small = cv2.resize(f, (w // 2, h // 2), interpolation=cv2.INTER_AREA)
        f = cv2.resize(small, (w, h), interpolation=cv2.INTER_LINEAR)
    elif kind == "light":
        h, w = img.shape[:2]
        yy, xx = np.mgrid[0:h, 0:w].astype(np.float32)
        g = 0.82 + 0.2 * (xx / w) * (1 - 0.5 * yy / h)
        f = f * g[..., None]
    return np.clip(np.round(f), 0, 255).astype(np.uint8)


# =====================================================================================
# 評估
# =====================================================================================


def full_mask(r, H, W):
    m = np.zeros((H, W), bool)
    x0, y0, x1, y1 = r.bbox
    m[y0:y1, x0:x1] = r.mask
    return m


def iou(a, b):
    return float((a & b).sum()) / max(float((a | b).sum()), 1.0)


def quad_err(pq, gq, geometry):
    pq, gq = np.asarray(pq, float), np.asarray(gq, float)
    if geometry == "line":
        a = np.linalg.norm(pq[0] - gq[0]) + np.linalg.norm(pq[1] - gq[1])
        b = np.linalg.norm(pq[0] - gq[1]) + np.linalg.norm(pq[1] - gq[0])
        return min(a, b) / 2
    best = 1e9
    for k in range(4):
        for qq in (np.roll(pq, k, 0), np.roll(pq[::-1], k, 0)):
            best = min(best, float(np.linalg.norm(qq - gq, axis=1).mean()))
    if geometry == "ellipse":  # 橢圓的外接框有 180° 對稱，上面已涵蓋
        pass
    return best


@dataclass
class Eval:
    n_gt: int = 0
    n_pred: int = 0
    matched_gt: int = 0
    matched_pred: int = 0
    cls_ok: dict = field(default_factory=dict)
    cls_n: dict = field(default_factory=dict)
    quad_errs: list = field(default_factory=list)
    radius_errs: list = field(default_factory=list)
    fill_errs: list = field(default_factory=list)
    geom_ok: int = 0
    geom_n: int = 0
    grid_errs: list = field(default_factory=list)
    missed: list = field(default_factory=list)

    def add(self, o):
        for k in ("n_gt", "n_pred", "matched_gt", "matched_pred", "geom_ok", "geom_n"):
            setattr(self, k, getattr(self, k) + getattr(o, k))
        for k in ("quad_errs", "radius_errs", "fill_errs", "grid_errs", "missed"):
            getattr(self, k).extend(getattr(o, k))
        for k in o.cls_n:
            self.cls_n[k] = self.cls_n.get(k, 0) + o.cls_n[k]
            self.cls_ok[k] = self.cls_ok.get(k, 0) + o.cls_ok.get(k, 0)

    @property
    def recall(self):
        return self.matched_gt / max(self.n_gt, 1)

    @property
    def precision(self):
        return self.matched_pred / max(self.n_pred, 1)

    @property
    def cls_acc(self):
        return sum(self.cls_ok.values()) / max(sum(self.cls_n.values()), 1)

    def summary(self):
        f = lambda v: float(np.mean(v)) if v else float("nan")
        per = {k: f"{self.cls_ok.get(k, 0)}/{self.cls_n[k]}" for k in sorted(self.cls_n)}
        return (f"recall={self.recall:.3f} precision={self.precision:.3f} cls_acc={self.cls_acc:.3f} per_class={per} "
                f"geom={self.geom_ok}/{self.geom_n} quad_err={f(self.quad_errs):.2f}px radius_err={f(self.radius_errs):.2f}px "
                f"fill_err={f(self.fill_errs):.2f} grid_err={f(self.grid_errs):.2f}px missed={self.missed}")


def evaluate(scene: Scene, regions, color_check=True) -> Eval:
    H, W = scene.H, scene.W
    ev = Eval(n_gt=len(scene.objs), n_pred=len(regions))
    pm = [full_mask(r, H, W) for r in regions]
    used = set()
    for o in scene.objs:
        best, bi = 0.0, -1
        for i, m in enumerate(pm):
            if o.kind == "table":
                v = iou(o.mask, m) if regions[i].kind == "table" else 0.0
            else:
                v = iou(o.mask, m)
            if v > best:
                best, bi = v, i
        if best < 0.7:
            ev.missed.append(f"{o.name}({best:.2f})")
            continue
        ev.matched_gt += 1
        used.add(bi)
        r = regions[bi]
        ev.cls_n[o.kind] = ev.cls_n.get(o.kind, 0) + 1
        ev.cls_ok[o.kind] = ev.cls_ok.get(o.kind, 0) + int(r.kind == o.kind)
        if o.kind == "shape" and r.kind == "shape" and r.shape is not None:
            ev.geom_n += 1
            ev.geom_ok += int(r.shape.geometry == o.geometry)
            if r.shape.geometry == o.geometry:
                ev.quad_errs.append(quad_err(r.shape.quad, o.quad, o.geometry))
                if o.geometry == "rounded_rect" and r.shape.corner_radius_px is not None:
                    ev.radius_errs.append(abs(r.shape.corner_radius_px - o.radius))
            col = r.shape.fill if o.geometry != "line" else r.shape.stroke
            if color_check and col is not None:
                ev.fill_errs.append(float(np.linalg.norm(np.array(col, float) - np.array(o.fill, float))))
        if o.kind == "table" and r.table is not None:
            for gl, pl in ((o.h_lines, r.table.h_lines), (o.v_lines, r.table.v_lines)):
                for g in gl:
                    if pl:
                        ev.grid_errs.append(min(abs(g - p) for p in pl))
    ev.matched_pred = len(used)
    return ev


def run_scene(scene: Scene, kind: str = "clean", **kw):
    img, tm = scene.render()
    if kind != "clean":
        img = distort(img, kind)
    t = time.time()
    regs = find_regions(img, tm, text_quads=scene.quads, **kw)
    return regs, time.time() - t, img, tm
