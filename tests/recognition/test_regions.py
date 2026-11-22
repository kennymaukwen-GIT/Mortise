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


def scene_stroke(seed: int = 5) -> Scene:
    """有外框線的形狀：實心加框、中空框、加框橢圓、加框圓角矩形。"""
    s = Scene(900, 600, "light", seed)
    def framed(kind, box, fill, stroke, sw, r=0.0, name=""):
        x0, y0, x1, y1 = box
        if kind == "rect":
            p_out = lambda d, f: d.rectangle([x0 * SS, y0 * SS, x1 * SS - 1, y1 * SS - 1], fill=f)
            p_in = lambda d, f: d.rectangle([(x0 + sw) * SS, (y0 + sw) * SS, (x1 - sw) * SS - 1, (y1 - sw) * SS - 1], fill=f)
        elif kind == "rounded_rect":
            p_out = lambda d, f: d.rounded_rectangle([x0 * SS, y0 * SS, x1 * SS - 1, y1 * SS - 1], radius=r * SS, fill=f)
            p_in = lambda d, f: d.rounded_rectangle([(x0 + sw) * SS, (y0 + sw) * SS, (x1 - sw) * SS - 1, (y1 - sw) * SS - 1], radius=(r - sw) * SS, fill=f)
        else:
            p_out = lambda d, f: d.ellipse([x0 * SS, y0 * SS, x1 * SS - 1, y1 * SS - 1], fill=f)
            p_in = lambda d, f: d.ellipse([(x0 + sw) * SS, (y0 + sw) * SS, (x1 - sw) * SS - 1, (y1 - sw) * SS - 1], fill=f)
        p_out(s.draw, stroke)
        p_in(s.draw, fill if fill is not None else (246, 244, 238))
        # quad 取線的中心線（與 regions.py 的約定相同）
        h = sw / 2
        q = np.array([(x0 + h, y0 + h), (x1 - h, y0 + h), (x1 - h, y1 - h), (x0 + h, y1 - h)], float)
        o = Obj("shape", kind, s._mask_of(p_out), q, fill, radius=(r - h) if r else None, stroke_width=sw, name=name)
        o.stroke = stroke
        s.objs.append(o)
    framed("rect", (60, 60, 300, 220), (90, 150, 220), (20, 40, 90), 4, name="filled_framed")
    framed("rect", (360, 60, 600, 220), None, (200, 50, 50), 3, name="hollow_frame")
    framed("ellipse", (650, 60, 860, 220), (250, 220, 120), (120, 80, 20), 5, name="framed_ellipse")
    framed("rounded_rect", (60, 300, 360, 520), (230, 240, 230), (40, 120, 60), 3, r=24, name="framed_rrect")
    return s


def scene_borderless(two_column: bool = False) -> Scene:
    """無框線表格（文字對齊成欄）；two_column=True 時改成兩欄長文（不應判為表格）。"""
    s = Scene(900, 700, "light", 7)
    if two_column:
        for j in range(8):
            s.text(60, 80 + j * 30, "Long paragraph text that runs across the column", 18, (30, 30, 30))
            s.text(480, 80 + j * 30, "Another column of body text with long lines", 18, (30, 30, 30))
        return s
    xs = [80, 300, 520, 700]
    for j in range(6):
        for i, x in enumerate(xs):
            s.text(x, 100 + j * 36, ["Item", "Qty", "Price", "Total"][i] if j == 0 else f"{(i + 1) * (j + 3) * 7}", 20, (30, 30, 30))
    s.objs.append(Obj("table", "", np.zeros((s.H, s.W), bool), None, name="borderless", v_lines=[300 - 10, 520 - 10, 700 - 10]))
    return s


# =====================================================================================
# 測試
# =====================================================================================

_CACHE: dict = {}


def _run_cached(name: str, kind: str):
    key = (name, kind)
    if key not in _CACHE:
        sc = {"overlap": scene_overlap, "stroke": scene_stroke}.get(name, lambda: scene_basic(name))()
        regs, dt, img, tm = run_scene(sc, kind)
        _CACHE[key] = (sc, regs, dt, img, tm)
    return _CACHE[key]


def _split_fill(sc, regs):
    """把填色誤差分成面狀形狀與線條兩組（細線的顏色在模糊下本來就量不準）。"""
    area, line = [], []
    for o in sc.objs:
        if o.kind != "shape":
            continue
        r = max(regs, key=lambda r: iou(o.mask, full_mask(r, sc.H, sc.W)))
        if r.shape is None or iou(o.mask, full_mask(r, sc.H, sc.W)) < 0.7:
            continue
        col = r.shape.stroke if o.geometry == "line" else r.shape.fill
        if col is not None:
            (line if o.geometry == "line" else area).append(float(np.linalg.norm(np.array(col, float) - np.array(o.fill, float))))
    return area, line


def test_default_params_documented():
    assert isinstance(DEFAULT_PARAMS, dict) and len(DEFAULT_PARAMS) > 40
    for k in ("fg_min_diff", "min_area_rel", "fit_tol_px", "min_corner_radius_px", "table_min_line_rel", "photo_colors_hi"):
        assert k in DEFAULT_PARAMS


@pytest.mark.parametrize("bg,kind,color", [("light", "flat", (246, 244, 238)), ("dark", "flat", (20, 30, 48)), ("gradient", "gradient", None)])
def test_background_model(bg, kind, color):
    sc, regs, dt, img, tm = _run_cached(bg, "clean")
    m = estimate_background(img, tm)
    assert m.kind == kind
    if color is not None:
        assert np.abs(np.array(m.color) - color).max() <= 2
    assert m.coverage > 0.4 and m.confidence > 0.5
    # 漸層底：模型殘差小（量到 < 3 色階）
    assert m.residual < 4.0


@pytest.mark.parametrize("bg", ["light", "gradient", "dark"])
def test_synthetic_clean(bg):
    sc, regs, dt, img, tm = _run_cached(bg, "clean")
    ev = evaluate(sc, regs)
    area, line = _split_fill(sc, regs)
    print(f"\n[clean {bg}] {ev.summary()} area_fill_err={np.mean(area):.2f} line_color_err={np.mean(line):.2f} t={dt:.2f}s")
    # 實測：recall＝precision＝分類＝1.000、幾何 8/8、外框誤差 0.13–0.14 px、圓角半徑誤差 0.03–0.09 px、填色誤差 0、格線誤差 0
    assert ev.recall == 1.0 and ev.precision == 1.0
    assert ev.cls_acc == 1.0 and ev.geom_ok == ev.geom_n
    assert np.mean(ev.quad_errs) < 0.25
    assert np.mean(ev.radius_errs) < 0.2
    assert max(area) < 2.0
    assert np.mean(ev.grid_errs) < 0.2
    assert dt < 6.0


DISTORTIONS = ["blur", "noise", "jpeg", "lowres", "light"]


@pytest.mark.parametrize("bg", ["light", "dark"])
def test_synthetic_distorted(bg):
    tot = Eval()
    areas, lines = [], []
    for kind in DISTORTIONS:
        sc, regs, dt, img, tm = _run_cached(bg, kind)
        ev = evaluate(sc, regs, color_check=False)
        if kind != "light":  # 光照不均會改變物件本身的顏色，不算填色誤差
            a, l = _split_fill(sc, regs)
            areas += a
            lines += l
        print(f"\n[{kind} {bg}] {ev.summary()} t={dt:.2f}s")
        tot.add(ev)
    print(f"\n[distorted {bg} 合計] {tot.summary()} area_fill_err={np.mean(areas):.2f}(max {max(areas):.1f}) line_color_err={np.mean(lines):.2f}")
    # 實測（light、dark 各 5 種干擾）：recall 1.000、precision 1.000、分類 1.000、幾何全對、外框誤差平均約 0.2 px
    assert tot.recall >= 0.98
    assert tot.precision >= 0.95
    assert tot.cls_acc >= 0.98
    assert tot.geom_ok >= tot.geom_n - 1
    assert np.mean(tot.quad_errs) < 0.4
    assert np.mean(tot.radius_errs) < 0.3
    assert np.mean(tot.grid_errs) < 0.2
    assert np.mean(areas) < 4.0  # 面狀形狀的填色（中位數）在模糊、雜訊、JPEG、低解析度下仍準
    assert np.mean(lines) < 60.0  # 細線顏色在模糊／低解析度下會被稀釋（回報的信心值也較低）


def test_text_over_shape_not_fragmented_and_layers():
    sc, regs, dt, img, tm = _run_cached("overlap", "clean")
    ev = evaluate(sc, regs)
    print(f"\n[overlap clean] {ev.summary()}")
    assert ev.recall == 1.0 and ev.precision == 1.0 and ev.cls_acc == 1.0
    H, W = sc.H, sc.W
    by = {o.name: max(regs, key=lambda r: iou(o.mask, full_mask(r, H, W))) for o in sc.objs}
    # 文字壓在橢圓上：橢圓仍完整認出
    o = [o for o in sc.objs if o.name == "ellipse_text"][0]
    assert by["ellipse_text"].shape.geometry == "ellipse" and iou(o.mask, full_mask(by["ellipse_text"], H, W)) > 0.97
    assert by["ellipse_text"].occluded_mask.sum() > 0  # 文字蓋住的部分記為補全
    # 色塊上壓照片：照片在上，色塊仍是完整矩形
    assert by["photo_on_card"].z > by["card"].z
    assert by["card"].shape.geometry == "rect" and by["card"].mask.all()
    # 部分重疊：後畫的在上，下層補全成完整矩形
    assert by["over"].z > by["under"].z
    assert by["under"].shape.geometry == "rect" and quad_err(by["under"].shape.quad, sc.objs[2].quad, "rect") < 0.5
    # 圓形裁切的照片：外形是橢圓
    assert by["round_photo"].kind == "photo" and by["round_photo"].outline.geometry == "ellipse"


def test_overlap_distorted():
    for kind in ("blur", "jpeg"):
        sc, regs, dt, img, tm = _run_cached("overlap", kind)
        ev = evaluate(sc, regs)
        print(f"\n[overlap {kind}] {ev.summary()}")
        assert ev.recall == 1.0 and ev.precision == 1.0 and ev.cls_acc == 1.0


def test_crop_region_alpha():
    sc, regs, dt, img, tm = _run_cached("overlap", "clean")
    rp = [r for r in regs if r.kind == "photo" and r.outline is not None and r.outline.geometry == "ellipse"][0]
    rgba = crop_region(img, rp)
    h, w = rgba.shape[:2]
    assert rgba.shape[2] == 4 and (h, w) == (rp.bbox[3] - rp.bbox[1], rp.bbox[2] - rp.bbox[0])
    a = rgba[..., 3]
    assert a[0, 0] == 0 and a[-1, -1] == 0 and a[h // 2, w // 2] == 255
    assert ((a > 0) & (a < 255)).sum() > 0.5 * (w + h)  # 邊緣有羽化（反鋸齒）
    # 有 inpainted 時優先取用
    inp = np.full_like(img, 7)
    assert (crop_region(img, rp, inpainted=inp)[..., :3] == 7).all()
    # 矩形照片：整塊不透明
    rr = [r for r in regs if r.kind == "photo" and r.outline is not None and r.outline.geometry == "rect"][0]
    assert (crop_region(img, rr)[..., 3] >= 254).mean() > 0.99


def test_stroke_shapes():
    errs_w, errs_c = [], []
    for kind in ("clean", "noise"):
        sc, regs, dt, img, tm = _run_cached("stroke", kind)
        assert len(regs) == 4
        for o in sc.objs:
            r = max(regs, key=lambda r: iou(o.mask, full_mask(r, sc.H, sc.W)))
            assert iou(o.mask, full_mask(r, sc.H, sc.W)) > 0.95 and r.shape.geometry == o.geometry
            assert r.shape.stroke is not None and r.shape.stroke_width_px is not None
            errs_w.append(abs(r.shape.stroke_width_px - o.stroke_width))
            errs_c.append(float(np.linalg.norm(np.array(r.shape.stroke, float) - np.array(o.stroke, float))))
            if o.fill is None:
                assert r.shape.fill is None
            elif kind == "clean":
                assert np.linalg.norm(np.array(r.shape.fill, float) - o.fill) < 3
            if o.geometry == "rounded_rect":
                assert abs(r.shape.corner_radius_px - o.radius) < 0.6
    print(f"\n[stroke] width_err mean={np.mean(errs_w):.2f} max={max(errs_w):.2f}px stroke_color_err mean={np.mean(errs_c):.2f}")
    # 實測：線寬誤差平均約 0.2 px（橢圓框低估約 0.6 px），線色誤差 < 2
    assert np.mean(errs_w) < 0.4 and max(errs_w) < 0.8
    assert np.mean(errs_c) < 3.0


def test_borderless_table_and_prose_guard():
    sc = scene_borderless(False)
    regs, dt, img, tm = run_scene(sc)
    t = [r for r in regs if r.kind == "table"]
    assert len(t) == 1 and t[0].table.has_ruling is False
    assert 0.3 <= t[0].confidence <= 0.7  # 無框表格信心值較低
    assert len(t[0].table.v_lines) == 5 and len(t[0].table.h_lines) == 7
    sc2 = scene_borderless(True)
    regs2, *_ = run_scene(sc2)
    assert not [r for r in regs2 if r.kind == "table"]  # 兩欄長文不是表格


def test_fit_shape_direct_and_params():
    s = Scene(400, 300, "light")
    s.rot_rect(200, 150, 180, 90, 30, (10, 120, 200))
    img, tm = s.render()
    sf = fit_shape(s.objs[0].mask, img)
    assert sf.geometry == "rect" and quad_err(sf.quad, s.objs[0].quad, "rect") < 0.4
    assert abs(sf.estimates["angle_deg"].value - 30) < 0.3 and sf.estimates["angle_deg"].error < 1.0
    assert sf.fill == (10, 120, 200)
    # params 覆寫：最小面積設成整張影像 → 沒有任何區域
    assert find_regions(img, tm, params={"min_area_rel": 1.0}) == []


def test_uncertainty_is_honest():
    """細線、近圓的角度等量不準的值，要回報大誤差與低信心。"""
    sc, regs, dt, img, tm = _run_cached("light", "blur")
    circ = [r for r in regs if r.shape is not None and r.shape.geometry == "ellipse" and abs(r.bbox[2] - r.bbox[0] - (r.bbox[3] - r.bbox[1])) <= 2][0]
    assert circ.shape.estimates["angle_deg"].error >= 20 and circ.shape.estimates["angle_deg"].confidence < 0.2
    big = [r for r in regs if r.shape is not None and r.shape.geometry == "rect"][0]
    assert big.shape.estimates["angle_deg"].error < 0.5
    for r in regs:
        assert 0.0 <= r.confidence <= 1.0 and r.kind_candidates and r.kind_candidates[0][1] >= r.kind_candidates[-1][1]


# ---------------------------------------------------------------- fixture（原生 PDF 畫成影像）


def _fixture(name: str, dpi: float = 100):
    from mortise.eval.truth import render_truth

    img, truth, spec = render_truth(FIXTURES / f"{name}.pdf", 0, dpi)
    H, W = img.shape[:2]
    tm = np.zeros((H, W), np.uint8)
    quads = []
    for t in truth.texts:
        q = np.array([(p.x * W, p.y * W) for p in t.quad.points])
        quads.append(q)
        cv2.fillPoly(tm, [np.round(q).astype(np.int32)], 1)
    tm = cv2.dilate(tm, np.ones((5, 5), np.uint8)).astype(bool)
    regs = find_regions(img, tm, text_quads=quads)
    gts = []
    for s in truth.shapes:
        gts.append(("shape", s.geometry.value, [v * W for v in s.quad.bbox]))
    shape_boxes = [[v * W for v in s.quad.bbox] for s in truth.shapes]
    for im in truth.images:
        b = [v * W for v in im.quad.bbox]
        # 陰影與「形狀特效的點陣化」（外框落在某個形狀外擴 8 px 之內）不是獨立的圖片
        mg = 8 * dpi / 100
        effect = any(sb[0] - mg <= b[0] and sb[1] - mg <= b[1] and b[2] <= sb[2] + mg and b[3] <= sb[3] + mg for sb in shape_boxes)
        if "shadow" not in str(im.kind.value).lower() and not effect:
            gts.append(("photo", "", b))
    for t in truth.table_regions:
        gts.append(("table", "", [v * W for v in t.quad.bbox], [v * W for v in t.h_lines], [v * W for v in t.v_lines]))
    found, missed, matched = [], [], set()
    for g in gts:
        bx = g[2]
        best, bi = 0.0, -1
        for i, r in enumerate(regs):
            rb = r.bbox
            ix = max(0, min(rb[2], bx[2]) - max(rb[0], bx[0]))
            iy = max(0, min(rb[3], bx[3]) - max(rb[1], bx[1]))
            if g[1] == "line":  # 線的外框沒有厚度：比長度與位置
                v = ix / max(bx[2] - bx[0], 1) if abs((rb[1] + rb[3]) / 2 - (bx[1] + bx[3]) / 2) < 3 else 0.0
            else:
                inter = ix * iy
                v = inter / max((rb[2] - rb[0]) * (rb[3] - rb[1]) + (bx[2] - bx[0]) * (bx[3] - bx[1]) - inter, 1)
            if v > best:
                best, bi = v, i
        if best >= 0.7:
            matched.add(bi)
            found.append((g, regs[bi]))
        else:
            missed.append(g[:2])
    extra = [(r.kind, r.bbox) for i, r in enumerate(regs) if i not in matched and not _is_overlay(r)]
    return found, missed, extra, regs


def _is_overlay(r) -> bool:
    """從照片裡拆出來的平塗圖形（照片本身有一個黃橢圓、一個藍矩形；fixture 的答案只記整張圖片）。"""
    return r.kind == "shape" and r.features.get("overlay", 0.0) == 1.0


@pytest.mark.parametrize("name,n_expected", [("slide_dark", 6), ("poster", 4), ("doc_a4", 1), ("slide_basic", 2)])
def test_fixtures(name, n_expected):
    found, missed, extra, regs = _fixture(name)
    print(f"\n[fixture {name}] found={[(g[0], g[1], r.kind, r.shape.geometry if r.shape else '') for g, r in found]} missed={missed} extra={extra}")
    assert len(found) == n_expected and not missed
    kinds_ok = sum(1 for g, r in found if g[0] == r.kind or (g[0] == "photo" and r.kind == "illustration"))
    if name == "slide_basic":
        # 這張「圖片」其實是純色底加黃色橢圓：以形狀（矩形＋橢圓）表達是合理的讀法，類型不計
        assert not extra or all(k == "shape" for k, _ in extra)
    else:
        assert kinds_ok == len(found) and not extra
    # slide_dark 與 poster 的照片（make_fixtures._photo）上畫了平塗的黃橢圓與藍矩形：要拆成疊在照片上的形狀
    over = sorted((r.shape.geometry, r.shape.fill) for r in regs if _is_overlay(r))
    if name in ("slide_dark", "poster"):
        assert [g for g, _ in over] == ["ellipse", "rect"]
        for (_, got), want in zip(over, [(235, 200, 70), (40, 60, 90)]):
            assert np.abs(np.subtract(got, want)).max() <= 3  # 縮小畫圖時邊緣混色，顏色差一兩個色階
        photo = next(r for g, r in found if g[0] == "photo")
        for r in regs:
            if _is_overlay(r):
                pb = photo.bbox
                assert pb[0] <= r.bbox[0] and pb[1] <= r.bbox[1] and r.bbox[2] <= pb[2] and r.bbox[3] <= pb[3] and r.z > photo.z
    else:
        assert not over
    for g, r in found:
        if g[0] == "shape" and r.shape is not None:
            assert r.shape.geometry == g[1]
        if name == "poster" and g[1] == "rounded_rect":
            # make_fixtures.py：roundRect 半徑 18 pt → 100 dpi 下 25.0 px（實測 25.7 px）
            est = r.shape.estimates["corner_radius_px"]
            print(f"  poster corner radius {r.shape.corner_radius_px:.2f}px (truth 25.00) ±{est.error:.2f} conf {est.confidence:.2f}")
            assert abs(r.shape.corner_radius_px - 25.0) < 1.0
        if g[0] == "table":
            errs = [min(abs(y - p) for p in r.table.h_lines) for y in g[3]] + [min(abs(x - p) for p in r.table.v_lines) for x in g[4]]
            print(f"  table grid err mean={np.mean(errs):.2f}px max={max(errs):.2f}px")
            assert np.mean(errs) < 0.6 and max(errs) < 1.5
