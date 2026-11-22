"""描圖向量化的評測：用已知答案的合成向量圖，量描回來的結果有多準、多好編輯。

用法：
  python -m mortise.eval vector --out 輸出目錄 [--samples 真實樣本目錄] [--limit N] [--variants clean,jpeg,...] [--no-vtracer]

合成真值（固定種子，至少 60 個）：基本幾何、重疊分層、帶孔的形狀、貝茲曲線色塊、細線與圖表、線性與放射漸層、
字型輪廓做的標誌字、多色圖示。每個樣本先用精確面積的點陣器畫成影像，再加干擾：
  clean（不加）、down（縮小 0.5 到 0.8 倍）、up（放大 1.5 到 2 倍）、blur（高斯模糊）、jpeg（品質 60 到 95）、noise（高斯雜訊）。
指標（依干擾類型與圖形類型分組）：
  render_error / error_p95   描圖自己回報的重繪色差（對輸入影像，ΔE）
  de_truth / de_truth_p95    重繪結果對「沒有干擾的真值影像」的色差
  iou                        覆蓋範圍對真值的交集 ÷ 聯集
  chamfer / chamfer_p95      看得到的邊界到真值邊界的距離（像素，雙向平均）
  node_ratio                 輸出節點數 ÷ 真值節點數（越接近 1 越好編輯）
  primitive_rate             基本幾何認對的比例；completion_rate 被遮住的形狀補成完整的比例
  stroke_rate                細線還原成筆畫（線寬誤差在 15% 或 0.3 像素內）的比例；gradient_rate 漸層認對的比例
  color_error                對得上的形狀的顏色誤差（ΔE）；seconds 每張耗時
對照組：VTracer 0.6.15 的原始輸出（預設參數）用同一套指標計分（它不做基本幾何、筆畫、漸層，那幾項必為 0）。
真實樣本目錄（PNG、JPEG）沒有真值，只報重繪誤差、節點數、耗時。
輸出 summary.json、summary.md，以及每個樣本的「原圖｜重繪｜差異」並排圖（panels/）。
"""

from __future__ import annotations

import io
import json
import math
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Optional

import cv2
import numpy as np
from PIL import Image

from ..recognition import vector_fit as vf
from ..recognition import vector_raster as vr
from ..recognition.types import TracedGradient, TracedPath, VectorResult
from ..recognition.vectorize import render, vectorize

SEED = 20261121
VARIANTS = ["clean", "down", "up", "blur", "jpeg", "noise"]
CATEGORIES = ["primitive", "layer", "hole", "bezier", "stroke", "gradient", "glyph", "icon"]
_FONTS = [
    "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationSerif-Bold.ttf",
]


@dataclass
class TruthShape:
    path: TracedPath
    primitive: Optional[str] = None  # 這個形狀該被認成哪種基本幾何（None 表示不是基本幾何）
    occluded: bool = False  # 有一部分被上層蓋住（用來量補全）


@dataclass
class Sample:
    name: str
    category: str
    size: tuple[int, int]
    background: tuple[int, int, int]
    shapes: list[TruthShape] = field(default_factory=list)

    def add(self, path: TracedPath, primitive: Optional[str] = None, occluded: bool = False) -> None:
        self.shapes.append(TruthShape(path, primitive, occluded))


# ---------------------------------------------------------------- 合成樣本


def _fill(path, color, rule="nonzero", gradient=None) -> TracedPath:
    return TracedPath(path=path, fill=tuple(int(v) for v in color), fill_rule=rule, gradient=gradient)


def _stroke(path, color, width, cap="butt", join="miter") -> TracedPath:
    return TracedPath(path=path, stroke=tuple(int(v) for v in color), stroke_width_px=float(width), stroke_cap=cap, stroke_join=join)


_PALETTE = [(30, 90, 200), (240, 180, 20), (200, 40, 60), (20, 150, 90), (120, 60, 170), (240, 120, 30), (0, 150, 190), (60, 60, 70),
            (230, 80, 140), (140, 190, 40), (20, 40, 110), (250, 215, 110), (90, 200, 180), (170, 30, 30)]


def _colors(rng: np.random.Generator, n: int, avoid=(255, 255, 255)) -> list[tuple[int, int, int]]:
    idx = rng.permutation(len(_PALETTE))
    out = []
    for i in idx:
        c = _PALETTE[int(i)]
        if sum(abs(a - b) for a, b in zip(c, avoid)) > 90:
            out.append(c)
        if len(out) == n:
            break
    while len(out) < n:
        out.append(_PALETTE[int(rng.integers(len(_PALETTE)))])
    return out


def _blob_path(rng: np.random.Generator, cx: float, cy: float, r: float, n: int = 5, jitter: float = 0.35) -> list[list[float]]:
    """平滑的封閉曲線色塊：n 個節點繞一圈，每段一條三次貝茲，節點處切線連續。"""
    ang = np.sort(rng.uniform(0, 2 * math.pi, n) * 0.25 + np.arange(n) * 2 * math.pi / n)
    rad = r * (1.0 + rng.uniform(-jitter, jitter, n))
    pts = np.stack([cx + rad * np.cos(ang), cy + rad * np.sin(ang)], axis=1)
    tan = np.roll(pts, -1, axis=0) - np.roll(pts, 1, axis=0)
    path: list[list[float]] = [[0, float(pts[0][0]), float(pts[0][1])]]
    for i in range(n):
        j = (i + 1) % n
        d = float(np.hypot(*(pts[j] - pts[i])))
        c1 = pts[i] + tan[i] / max(np.hypot(*tan[i]), 1e-9) * d * 0.36
        c2 = pts[j] - tan[j] / max(np.hypot(*tan[j]), 1e-9) * d * 0.36
        path.append([2, float(c1[0]), float(c1[1]), float(c2[0]), float(c2[1]), float(pts[j][0]), float(pts[j][1])])
    path.append([3])
    return path


def _star(cx, cy, r_out, r_in, n=5, rot=-math.pi / 2):
    return [(cx + (r_out if i % 2 == 0 else r_in) * math.cos(rot + i * math.pi / n), cy + (r_out if i % 2 == 0 else r_in) * math.sin(rot + i * math.pi / n)) for i in range(2 * n)]


def _regular(cx, cy, r, n, rot=-math.pi / 2):
    return [(cx + r * math.cos(rot + i * 2 * math.pi / n), cy + r * math.sin(rot + i * 2 * math.pi / n)) for i in range(n)]


def _glyph_paths(text: str, font_path: str, size: float, x0: float, baseline: float) -> Optional[list[list[list[float]]]]:
    """用字型輪廓排一行字，回傳每個字的路徑（像素座標，y 向下）。找不到字型回傳 None。"""
    try:
        from fontTools.pens.basePen import BasePen
        from fontTools.ttLib import TTFont

        font = TTFont(font_path)
    except Exception:
        return None
    gs = font.getGlyphSet()
    cmap = font.getBestCmap()
    upm = font["head"].unitsPerEm
    k = size / upm

    class Pen(BasePen):
        def __init__(self, glyph_set, dx):
            super().__init__(glyph_set)
            self.dx = dx
            self.path: list[list[float]] = []

        def _pt(self, p):
            return [x0 + (self.dx + p[0]) * k, baseline - p[1] * k]

        def _moveTo(self, p):
            self.path.append([0, *self._pt(p)])

        def _lineTo(self, p):
            self.path.append([1, *self._pt(p)])

        def _curveToOne(self, p1, p2, p3):
            self.path.append([2, *self._pt(p1), *self._pt(p2), *self._pt(p3)])

        def _closePath(self):
            self.path.append([3])

    out = []
    dx = 0.0
    for ch in text:
        name = cmap.get(ord(ch))
        if name is None:
            continue
        pen = Pen(gs, dx)
        gs[name].draw(pen)
        if pen.path:
            out.append(pen.path)
        dx += gs[name].width
    return out or None


def make_samples(seed: int = SEED) -> list[Sample]:
    """固定種子的合成樣本（同一個種子永遠產生同一批）。"""
    rng = np.random.default_rng(seed)
    S: list[Sample] = []
    W, H = 320, 240
    white = (255, 255, 255)

    def u(a, b):
        return float(rng.uniform(a, b))

    # ---- 基本幾何（10）
    for i in range(10):
        s = Sample(f"primitive_{i:02d}", "primitive", (W, H), white)
        cols = _colors(rng, 4)
        kind = i % 5
        if kind == 0:
            s.add(_fill(vr.rounded_rect_path(u(90, 110), u(90, 110), u(90, 140), u(60, 110), 0, 0), cols[0]), "rect")
            s.add(_fill(vr.ellipse_path(u(225, 245), u(150, 170), r := u(30, 55), r), cols[1]), "circle")
        elif kind == 1:
            s.add(_fill(vr.rounded_rect_path(u(100, 120), u(110, 130), u(120, 170), u(80, 130), u(10, 30), 0), cols[0]), "rounded_rect")
            s.add(_fill(vr.ellipse_path(u(250, 265), u(60, 80), u(28, 40), u(15, 24), u(-40, 40)), cols[1]), "ellipse")
        elif kind == 2:
            s.add(_fill(vr.rounded_rect_path(u(110, 130), u(100, 130), u(110, 150), u(60, 90), 0, u(10, 35)), cols[0]), "rect")
            s.add(_fill(vr.polygon_path(_regular(u(245, 260), u(160, 175), u(35, 50), 3, u(0, 1))), cols[1]), "polygon")
        elif kind == 3:
            s.add(_fill(vr.polygon_path(_star(u(100, 120), u(110, 130), u(70, 90), u(28, 38))), cols[0]), "polygon")
            s.add(_fill(vr.rounded_rect_path(u(240, 255), u(110, 130), u(70, 90), u(120, 150), u(34, 44), 0), cols[1]), "rounded_rect")
        else:
            s.add(_fill(vr.ellipse_path(u(100, 120), u(110, 130), u(60, 85), u(35, 50), 0), cols[0]), "ellipse")
            s.add(_fill(vr.polygon_path(_regular(u(240, 255), u(80, 95), u(35, 48), 6, u(0, 1))), cols[1]), "polygon")
            s.add(_fill(vr.rounded_rect_path(u(240, 255), u(180, 195), u(80, 110), u(30, 44), 0, 0), cols[2]), "rect")
        S.append(s)
    # ---- 重疊分層（8）
    for i in range(8):
        s = Sample(f"layer_{i:02d}", "layer", (W, H), white)
        cols = _colors(rng, 4)
        kind = i % 4
        if kind == 0:  # 圓蓋住矩形的一角
            cx, cy, w, h = u(120, 140), u(100, 120), u(150, 180), u(100, 130)
            s.add(_fill(vr.rounded_rect_path(cx, cy, w, h, 0, 0), cols[0]), "rect", True)
            s.add(_fill(vr.ellipse_path(cx + w / 2 - u(5, 25), cy + h / 2 - u(5, 25), r := u(38, 52), r), cols[1]), "circle")
        elif kind == 1:  # 三張卡片疊放
            for j in range(3):
                s.add(_fill(vr.rounded_rect_path(90 + 55 * j + u(-5, 5), 80 + 35 * j + u(-5, 5), 130, 90, u(8, 16), 0), cols[j]), "rounded_rect", j < 2)
        elif kind == 2:  # 矩形橫過圓
            s.add(_fill(vr.ellipse_path(u(140, 170), u(110, 130), r := u(70, 85), r), cols[0]), "circle", True)
            s.add(_fill(vr.rounded_rect_path(u(170, 200), u(110, 130), u(150, 190), u(36, 50), 0, 0), cols[1]), "rect")
        else:  # 矩形上面放圓與圓角矩形（都在裡面）
            s.add(_fill(vr.rounded_rect_path(160, 120, u(240, 270), u(160, 190), 0, 0), cols[0]), "rect", True)
            s.add(_fill(vr.ellipse_path(u(100, 120), u(100, 130), r := u(35, 48), r), cols[1]), "circle")
            s.add(_fill(vr.rounded_rect_path(u(205, 220), u(120, 140), u(80, 100), u(60, 90), u(10, 18), 0), cols[2]), "rounded_rect")
        S.append(s)
    # ---- 帶孔的形狀（6）
    for i in range(6):
        s = Sample(f"hole_{i:02d}", "hole", (W, H), white)
        cols = _colors(rng, 3)
        kind = i % 3
        if kind == 0:  # 圓環
            cx, cy, r = u(150, 170), u(110, 130), u(70, 90)
            s.add(_fill(vr.ellipse_path(cx, cy, r, r) + vr.ellipse_path(cx, cy, r * u(0.45, 0.6), r * u(0.45, 0.6)), cols[0], "evenodd"))
        elif kind == 1:  # 矩形框
            cx, cy, w, h = u(150, 170), u(110, 130), u(200, 240), u(140, 170)
            s.add(_fill(vr.rounded_rect_path(cx, cy, w, h, 0, 0) + vr.rounded_rect_path(cx, cy, w - u(50, 80), h - u(50, 80), 0, 0), cols[0], "evenodd"))
        else:  # 色塊上有兩個洞，另一個形狀疊在旁邊
            cx, cy = u(130, 150), u(110, 130)
            s.add(_fill(vr.rounded_rect_path(cx, cy, 200, 150, 20, 0) + vr.ellipse_path(cx - 45, cy, 28, 28) + vr.rounded_rect_path(cx + 45, cy, 50, 70, 0, 0), cols[0], "evenodd"))
            s.add(_fill(vr.ellipse_path(u(260, 280), u(170, 190), r := u(24, 32), r), cols[1]), "circle")
        S.append(s)
    # ---- 貝茲曲線色塊（8）
    for i in range(8):
        s = Sample(f"bezier_{i:02d}", "bezier", (W, H), white)
        cols = _colors(rng, 3)
        s.add(_fill(_blob_path(rng, u(110, 130), u(110, 130), u(60, 80), int(rng.integers(4, 7))), cols[0]))
        if i % 2:
            s.add(_fill(_blob_path(rng, u(240, 255), u(100, 150), u(35, 48), int(rng.integers(4, 6)), 0.25), cols[1]))
        S.append(s)
    # ---- 細線與圖表（10）
    for i in range(10):
        s = Sample(f"stroke_{i:02d}", "stroke", (W, H), white)
        cols = _colors(rng, 4)
        kind = i % 5
        dark = (50, 50, 60)
        if kind == 0:  # 折線圖：軸線、格線、折線
            w_ax = float(rng.choice([2.0, 3.0]))
            for gy in (70.5, 110.5, 150.5):
                s.add(_stroke([[0, 40.0, gy], [1, 290.0, gy]], (190, 195, 205), 1.0), "line")
            s.add(_stroke([[0, 40.0, 30.0], [1, 40.0, 200.0], [1, 290.0, 200.0]], dark, w_ax))
            pts = [(55 + 45 * j, u(60, 180)) for j in range(6)]
            s.add(_stroke([[0, *pts[0]]] + [[1, *p] for p in pts[1:]], cols[0], float(rng.choice([2.5, 3.0, 4.0])), "round", "round"))
        elif kind == 1:  # 長條圖
            s.add(_stroke([[0, 40.0, 200.0], [1, 290.0, 200.0]], dark, 2.0), "line")
            for j in range(5):
                hgt = u(40, 150)
                s.add(_fill(vr.rounded_rect_path(70 + 45 * j, 199.0 - hgt / 2, 28, hgt, 0, 0), cols[j % 2]), "rect")
        elif kind == 2:  # 線條圖示：放大鏡（圓框加斜線）、圓角方框
            wv = float(rng.choice([3.0, 4.0, 5.0]))
            cx, cy, r = u(90, 110), u(100, 120), u(38, 50)
            s.add(_stroke(vr.ellipse_path(cx, cy, r, r), dark, wv), "circle")
            a = math.radians(45)
            s.add(_stroke([[0, cx + (r + wv / 2) * math.cos(a), cy + (r + wv / 2) * math.sin(a)], [1, cx + (r + 45) * math.cos(a), cy + (r + 45) * math.sin(a)]], dark, wv, "round"), "line")
            s.add(_stroke(vr.rounded_rect_path(u(235, 250), u(110, 130), u(80, 100), u(80, 100), u(12, 20), 0), cols[0], wv), "rounded_rect")
        elif kind == 3:  # 曲線筆畫
            wv = float(rng.choice([2.0, 3.0, 4.0]))
            s.add(_stroke([[0, 30.0, u(150, 190)], [2, 100.0, u(20, 60), 180.0, u(180, 220), 290.0, u(60, 100)]], cols[0], wv, "round"))
            s.add(_stroke([[0, 40.0, 215.5], [1, 280.0, 215.5]], dark, 1.0), "line")
        else:  # 格線（交叉的細線）
            wv = float(rng.choice([1.0, 2.0]))
            for gx in (80.5, 140.5, 200.5, 260.5):
                s.add(_stroke([[0, gx, 30.0], [1, gx, 210.0]], dark, wv), "line")
            for gy in (60.5, 120.5, 180.5):
                s.add(_stroke([[0, 30.0, gy], [1, 290.0, gy]], dark, wv), "line")
        S.append(s)
    # ---- 漸層（8）
    for i in range(8):
        s = Sample(f"gradient_{i:02d}", "gradient", (W, H), white)
        cols = _colors(rng, 4)
        kind = i % 4
        if kind == 0:  # 兩色線性
            cx, cy, w, h = u(150, 170), u(110, 130), u(200, 240), u(130, 160)
            a = u(0, math.pi)
            d = np.array([math.cos(a), math.sin(a)]) * max(w, h) * 0.5
            g = TracedGradient("linear", [cx - d[0], cy - d[1], cx + d[0], cy + d[1]], [(0.0, cols[0]), (1.0, cols[1])])
            s.add(_fill(vr.rounded_rect_path(cx, cy, w, h, u(0, 24), 0), (128, 128, 128), gradient=g), "rounded_rect")
        elif kind == 1:  # 放射
            cx, cy, r = u(150, 170), u(110, 130), u(75, 95)
            g = TracedGradient("radial", [cx, cy, r], [(0.0, (255, 245, 200)), (1.0, cols[0])])
            s.add(_fill(vr.ellipse_path(cx, cy, r, r), (128, 128, 128), gradient=g), "circle")
        elif kind == 2:  # 三個色標的線性，加一個純色形狀
            cx, cy, w, h = u(120, 140), u(110, 130), u(180, 200), u(150, 170)
            g = TracedGradient("linear", [cx, cy - h / 2, cx, cy + h / 2], [(0.0, cols[0]), (0.5, cols[1]), (1.0, cols[2])])
            s.add(_fill(vr.rounded_rect_path(cx, cy, w, h, 0, 0), (128, 128, 128), gradient=g), "rect")
            s.add(_fill(vr.ellipse_path(u(265, 280), u(110, 130), r := u(24, 32), r), cols[3]), "circle")
        else:  # 漸層底上疊純色圓（漸層被遮住一塊）
            cx, cy, w, h = 160.0, 120.0, u(250, 280), u(170, 200)
            g = TracedGradient("linear", [cx - w / 2, cy, cx + w / 2, cy], [(0.0, cols[0]), (1.0, cols[1])])
            s.add(_fill(vr.rounded_rect_path(cx, cy, w, h, 0, 0), (128, 128, 128), gradient=g), "rect", True)
            s.add(_fill(vr.ellipse_path(u(130, 190), u(100, 140), r := u(35, 50), r), (250, 250, 250)), "circle")
        S.append(s)
    # ---- 字型輪廓做的標誌字（6）
    words = ["Mortise", "ACME", "data", "Go!", "Lab", "Zenith"]
    for i in range(6):
        s = Sample(f"glyph_{i:02d}", "glyph", (W, H), white)
        cols = _colors(rng, 2)
        size = [70.0, 96.0, 110.0, 120.0, 130.0, 72.0][i]
        glyphs = _glyph_paths(words[i], _FONTS[i % len(_FONTS)], size, 14.0, 150.0)
        if not glyphs:
            continue
        if i % 2:
            s.add(_fill(vr.rounded_rect_path(160, 120, 310, 190, 16, 0), cols[1]), "rounded_rect", True)
            cols[0] = (255, 255, 255)
        for gp in glyphs:
            s.add(_fill(gp, cols[0]))
        S.append(s)
    # ---- 多色圖示（8）
    for i in range(8):
        s = Sample(f"icon_{i:02d}", "icon", (W, H), white)
        cols = _colors(rng, 5)
        kind = i % 8
        if kind == 0:  # 徽章：圓加星星
            s.add(_fill(vr.ellipse_path(160, 120, 95, 95), cols[0]), "circle", True)
            s.add(_fill(vr.polygon_path(_star(160, 122, 62, 26)), cols[1]), "polygon")
        elif kind == 1:  # 紅綠燈
            s.add(_fill(vr.rounded_rect_path(160, 120, 90, 210, 24, 0), (50, 55, 65)), "rounded_rect", True)
            for j, c in enumerate([(220, 50, 50), (240, 190, 40), (60, 180, 90)]):
                s.add(_fill(vr.ellipse_path(160, 55 + 65 * j, 24, 24), c), "circle")
        elif kind == 2:  # 靶：同心圓
            for j, r in enumerate([100, 75, 50, 25]):
                s.add(_fill(vr.ellipse_path(160, 120, r, r), cols[j % 2]), "circle", j < 3)
        elif kind == 3:  # 房子：多邊形
            s.add(_fill(vr.rounded_rect_path(160, 150, 150, 110, 0, 0), cols[0]), "rect", True)
            s.add(_fill(vr.polygon_path([(65, 100), (160, 25), (255, 100)]), cols[1]), "polygon")
            s.add(_fill(vr.rounded_rect_path(160, 172, 40, 66, 0, 0), cols[2]), "rect")
            s.add(_fill(vr.rounded_rect_path(110, 135, 34, 30, 0, 0), (250, 250, 250)), "rect")
        elif kind == 4:  # 旗子：橫條
            for j in range(3):
                s.add(_fill(vr.rounded_rect_path(160, 60 + 60 * j, 240, 60, 0, 0), cols[j]), "rect")
        elif kind == 5:  # 對話框：圓角矩形加三角形尾巴，上面三個圓點
            s.add(_fill(vr.rounded_rect_path(160, 100, 220, 130, 26, 0), cols[0]), "rounded_rect", True)
            s.add(_fill(vr.polygon_path([(110, 160), (150, 160), (100, 210)]), cols[0]))
            for j in range(3):
                s.add(_fill(vr.ellipse_path(110 + 50 * j, 100, 13, 13), (250, 250, 250)), "circle")
        elif kind == 6:  # 有外框線的形狀（填色加描邊）
            s.add(_fill(vr.rounded_rect_path(120, 120, 150, 110, 18, 0), cols[0]), "rounded_rect")
            s.add(_stroke(vr.rounded_rect_path(120, 120, 150, 110, 18, 0), (40, 40, 50), 4.0), "rounded_rect")
            s.add(_fill(vr.ellipse_path(250, 120, 40, 40), cols[1]), "circle")
            s.add(_stroke(vr.ellipse_path(250, 120, 40, 40), (40, 40, 50), 4.0), "circle")
        else:  # 色塊加貝茲曲線加小圓
            s.add(_fill(vr.rounded_rect_path(160, 120, 280, 200, 30, 0), cols[0]), "rounded_rect", True)
            s.add(_fill(_blob_path(rng, 150, 125, 62, 5, 0.25), cols[1]))
            s.add(_fill(vr.ellipse_path(245, 70, 20, 20), cols[2]), "circle")
        S.append(s)
    return S


# ---------------------------------------------------------------- 影像與干擾


def truth_image(sample: Sample, scale: float = 1.0) -> np.ndarray:
    """真值影像（RGB，uint8）：精確面積點陣化後疊在底色上。"""
    rgba = vr.render_paths([t.path for t in sample.shapes], sample.size, scale)
    return np.clip(np.round(vr.composite_over(rgba, sample.background)), 0, 255).astype(np.uint8)


def perturb(sample: Sample, variant: str, rng: np.random.Generator) -> tuple[np.ndarray, float, str]:
    """回傳（輸入影像、相對於原尺寸的縮放倍率、干擾的說明）。"""
    img = truth_image(sample)
    H, W = img.shape[:2]
    if variant == "clean":
        return img, 1.0, "無干擾"
    if variant == "down":
        k = float(rng.choice([0.5, 0.625, 0.75]))
        out = cv2.resize(img, (int(round(W * k)), int(round(H * k))), interpolation=cv2.INTER_AREA)
        return out, k, f"縮小 {k:g} 倍"
    if variant == "up":
        k = float(rng.choice([1.5, 2.0]))
        out = cv2.resize(img, (int(round(W * k)), int(round(H * k))), interpolation=cv2.INTER_CUBIC)
        return out, k, f"放大 {k:g} 倍（雙三次）"
    if variant == "blur":
        sg = float(rng.uniform(0.6, 1.1))
        return cv2.GaussianBlur(img, (0, 0), sg), 1.0, f"高斯模糊 σ={sg:.2f}"
    if variant == "jpeg":
        q = int(rng.integers(60, 96))
        buf = io.BytesIO()
        Image.fromarray(img).save(buf, "JPEG", quality=q)
        return np.asarray(Image.open(io.BytesIO(buf.getvalue())).convert("RGB")), 1.0, f"JPEG 品質 {q}"
    if variant == "noise":
        sg = float(rng.uniform(2.0, 5.0))
        noisy = img.astype(np.float32) + rng.normal(0.0, sg, img.shape).astype(np.float32)
        return np.clip(np.round(noisy), 0, 255).astype(np.uint8), 1.0, f"高斯雜訊 σ={sg:.1f}"
    raise ValueError(f"不認得的干擾類型：{variant}")


# ---------------------------------------------------------------- 指標


def _lab(img: np.ndarray) -> np.ndarray:
    return cv2.cvtColor(np.ascontiguousarray(img, dtype=np.float32) / 255.0, cv2.COLOR_RGB2Lab)


def _delta_e_color(a, b) -> float:
    la = _lab(np.asarray([[a]], dtype=np.float32))[0, 0]
    lb = _lab(np.asarray([[b]], dtype=np.float32))[0, 0]
    return float(np.sqrt(((la - lb) ** 2).sum()))


def _scaled(p: TracedPath, k: float) -> TracedPath:
    g = None
    if p.gradient is not None:
        g = TracedGradient(p.gradient.kind, [float(v) * k for v in p.gradient.coords], list(p.gradient.stops))
    return TracedPath(path=[[seg[0]] + [float(v) * k for v in seg[1:]] for seg in p.path], fill=p.fill, fill_rule=p.fill_rule, gradient=g, stroke=p.stroke,
                      stroke_width_px=(p.stroke_width_px * k) if p.stroke_width_px else None, stroke_cap=p.stroke_cap, stroke_join=p.stroke_join)


def _shape_cov(p: TracedPath, W: int, H: int) -> np.ndarray:
    cov = np.zeros((H, W), dtype=np.float32)
    if p.fill is not None or p.gradient is not None:
        cov = np.maximum(cov, vr.path_coverage(p.path, W, H, 1.0, p.fill_rule))
    if p.stroke is not None and p.stroke_width_px:
        cov = np.maximum(cov, vr.stroke_coverage(p.path, p.stroke_width_px, W, H, 1.0, p.stroke_cap, p.stroke_join))
    return cov


def _outline_points(p: TracedPath, step: float = 0.1) -> np.ndarray:
    """形狀外形上的取樣點：填色取路徑本身，筆畫取中心線兩側各偏半個線寬的兩條線（端點與轉角附近略去）。"""
    if p.stroke is not None and p.stroke_width_px and p.fill is None and p.gradient is None:
        out = []
        hw = p.stroke_width_px / 2.0
        for pts, closed in vr.flatten_path(p.path, 0.02):
            q = np.vstack([pts, pts[:1]]) if closed else pts
            seg = q[1:] - q[:-1]
            L = np.hypot(seg[:, 0], seg[:, 1])
            for i in range(len(seg)):
                if L[i] < 1e-6:
                    continue
                d = seg[i] / L[i]
                nrm = np.array([-d[1], d[0]])
                m = hw if not closed else 0.0
                lo, hi = (m, L[i] - m) if L[i] > 2 * m + step else (L[i] / 2, L[i] / 2)
                s = np.arange(lo, hi + 1e-9, step)
                base = q[i][None, :] + d[None, :] * s[:, None]
                out.append(base + nrm * hw)
                out.append(base - nrm * hw)
        return np.vstack(out) if out else np.zeros((0, 2))
    return vf.path_points(p.path, step)


def _visible_boundary(paths: list[TracedPath], W: int, H: int, covs: Optional[list[np.ndarray]] = None) -> np.ndarray:
    """所有形狀看得到的邊界點（被上層蓋住的不算）。"""
    covs = covs if covs is not None else [_shape_cov(p, W, H) for p in paths]
    out = []
    for i, p in enumerate(paths):
        pts = _outline_points(p)
        if len(pts) == 0:
            continue
        ok = (pts[:, 0] >= 0.25) & (pts[:, 0] <= W - 0.25) & (pts[:, 1] >= 0.25) & (pts[:, 1] <= H - 0.25)
        pts = pts[ok]
        if len(pts) == 0:
            continue
        xi = np.clip(pts[:, 0].astype(int), 0, W - 1)
        yi = np.clip(pts[:, 1].astype(int), 0, H - 1)
        hidden = np.zeros(len(pts), dtype=bool)
        for j in range(i + 1, len(paths)):
            hidden |= covs[j][yi, xi] > 0.97
        out.append(pts[~hidden])
    return np.vstack(out) if out else np.zeros((0, 2))


def _fg_alpha(paths: list[TracedPath], covs: list[np.ndarray], background) -> np.ndarray:
    """由下到上疊出「看得出來的前景」的覆蓋率：顏色與底色相同的純色形狀當成橡皮擦。"""
    bg = tuple(int(v) for v in background) if background is not None else None
    H, W = covs[0].shape if covs else (0, 0)
    a = np.zeros((H, W), dtype=np.float32)
    for p, c in zip(paths, covs):
        col = p.stroke if (p.stroke is not None and p.fill is None and p.gradient is None) else p.fill
        if bg is not None and p.gradient is None and col is not None and _delta_e_color(col, bg) < 1.0:
            a = a * (1.0 - c)
        else:
            a = a + c * (1.0 - a)
    return a


def _chamfer(a: np.ndarray, b: np.ndarray) -> tuple[Optional[float], Optional[float]]:
    from scipy.spatial import cKDTree

    if len(a) == 0 or len(b) == 0:
        return None, None
    da, _ = cKDTree(b).query(a)
    db, _ = cKDTree(a).query(b)
    return float(0.5 * (da.mean() + db.mean())), float(max(np.percentile(da, 95), np.percentile(db, 95)))


def _prim_matches(want: str, got) -> bool:
    if got is None:
        return False
    k = got.kind
    if want == k:
        return True
    if want == "circle" and k == "ellipse" and abs(got.w - got.h) <= 0.02 * max(got.w, got.h):
        return True
    if want == "rect" and k == "rounded_rect" and got.corner_radius <= 1.0:
        return True
    if want == "rect" and k == "polygon" and len(got.points) == 4:
        return True
    return False


def evaluate(sample: Sample, paths: list[TracedPath], k: float, background, input_img: np.ndarray) -> dict:
    """用真值替一組路徑計分。k 是輸入影像相對於樣本原尺寸的縮放倍率（路徑座標在輸入影像的像素上）。"""
    H, W = input_img.shape[:2]
    truth = [_scaled(t.path, k) for t in sample.shapes]
    t_img = truth_image(sample, k)
    if t_img.shape[:2] != (H, W):
        t_img = cv2.resize(t_img, (W, H), interpolation=cv2.INTER_AREA)
    res_like = VectorResult(paths=paths, size=(W, H), background=background, render_error=0, error_p95=0, iou=0, node_count=0, accepted=True, confidence=1)
    rgba = render(res_like)
    comp = vr.composite_over(rgba, background if background is not None else sample.background)
    de = np.sqrt(((_lab(comp) - _lab(t_img.astype(np.float32))) ** 2).sum(axis=2))
    out: dict = {"de_truth": float(de.mean()), "de_truth_p95": float(np.percentile(de, 95))}
    t_cov = [_shape_cov(p, W, H) for p in truth]
    r_cov = [_shape_cov(p, W, H) for p in paths]
    # 覆蓋範圍只算「看得出來的前景」：與底色同色的形狀疊在上面等於挖洞（例如黃底上的白字），兩邊都照這個規則算
    t_alpha = _fg_alpha(truth, t_cov, sample.background)
    r_alpha = _fg_alpha(paths, r_cov, background if background is not None else sample.background)
    union = float(np.maximum(t_alpha, r_alpha).sum())
    out["iou"] = float(np.minimum(t_alpha, r_alpha).sum()) / union if union > 0 else 1.0
    ch, ch95 = _chamfer(_visible_boundary(truth, W, H, t_cov), _visible_boundary(paths, W, H, r_cov))
    out["chamfer"], out["chamfer_p95"] = ch, ch95
    t_nodes = sum(vr.path_node_count(p.path) for p in truth)
    r_nodes = sum(vr.path_node_count(p.path) for p in paths)
    out["nodes"], out["truth_nodes"] = r_nodes, t_nodes
    out["node_ratio"] = r_nodes / max(t_nodes, 1)
    out["paths"], out["truth_paths"] = len(paths), len(truth)
    # 逐個真值形狀找對應的輸出路徑（整個形狀的重疊率最高者）
    hits = {"primitive": [0, 0], "completion": [0, 0], "stroke": [0, 0], "gradient": [0, 0]}
    color_err = []
    r_area = [float(c.sum()) for c in r_cov]
    for ti, (t, tp) in enumerate(zip(sample.shapes, truth)):
        ta = float(t_cov[ti].sum())
        if ta < 4:
            continue
        best, best_iou = -1, 0.0
        for ri in range(len(paths)):
            inter = float(np.minimum(t_cov[ti], r_cov[ri]).sum())
            if inter <= 0:
                continue
            iou = inter / (ta + r_area[ri] - inter)
            if iou > best_iou:
                best, best_iou = ri, iou
        rp = paths[best] if best >= 0 else None
        is_stroke = tp.stroke is not None and tp.fill is None and tp.gradient is None
        if t.primitive is not None:
            hits["primitive"][1] += 1
            if rp is not None and best_iou >= 0.7 and _prim_matches(t.primitive, rp.primitive):
                hits["primitive"][0] += 1
        if t.occluded:
            hits["completion"][1] += 1
            if rp is not None and best_iou >= 0.95:
                hits["completion"][0] += 1
        if is_stroke:
            hits["stroke"][1] += 1
            if rp is not None and rp.stroke is not None and rp.stroke_width_px and best_iou >= 0.5:
                if abs(rp.stroke_width_px - tp.stroke_width_px) <= max(0.3, 0.15 * tp.stroke_width_px):
                    hits["stroke"][0] += 1
        if tp.gradient is not None:
            hits["gradient"][1] += 1
            if rp is not None and rp.gradient is not None and rp.gradient.kind == tp.gradient.kind and best_iou >= 0.7:
                hits["gradient"][0] += 1
        elif rp is not None and best_iou >= 0.5:
            tc = tp.stroke if is_stroke else tp.fill
            rc = rp.stroke if (rp.stroke is not None and rp.fill is None) else rp.fill
            if tc is not None and rc is not None and not (is_stroke and tp.stroke_width_px < 2.0):
                color_err.append(_delta_e_color(tc, rc))
    out["hits"] = hits
    out["color_error"] = float(np.mean(color_err)) if color_err else None
    return out


# ---------------------------------------------------------------- VTracer 對照組


def vtracer_paths(img: np.ndarray) -> Optional[list[TracedPath]]:
    """VTracer 0.6.15 預設參數的原始輸出，轉成同一種路徑格式。沒裝 vtracer 回傳 None。"""
    try:
        import vtracer
    except Exception:
        return None
    buf = io.BytesIO()
    Image.fromarray(img).save(buf, "PNG")
    svg = vtracer.convert_raw_image_to_svg(buf.getvalue(), img_format="png")
    out: list[TracedPath] = []
    for m in re.finditer(r'<path d="([^"]*)" fill="#([0-9A-Fa-f]{6})"(?: transform="translate\(([-\d.]+),([-\d.]+)\)")?', svg):
        d, col = m.group(1), m.group(2)
        tx, ty = float(m.group(3) or 0), float(m.group(4) or 0)
        path: list[list[float]] = []
        for cmd, args in re.findall(r"([MLCZ])([^MLCZ]*)", d):
            v = [float(x) for x in re.findall(r"-?\d+(?:\.\d+)?(?:e-?\d+)?", args)]
            if cmd == "M":
                path.append([0, v[0] + tx, v[1] + ty])
            elif cmd == "L":
                for i in range(0, len(v) - 1, 2):
                    path.append([1, v[i] + tx, v[i + 1] + ty])
            elif cmd == "C":
                for i in range(0, len(v) - 5, 6):
                    path.append([2, v[i] + tx, v[i + 1] + ty, v[i + 2] + tx, v[i + 3] + ty, v[i + 4] + tx, v[i + 5] + ty])
            else:
                path.append([3])
        if path:
            out.append(TracedPath(path=path, fill=(int(col[0:2], 16), int(col[2:4], 16), int(col[4:6], 16))))
    return out


def _drop_background(paths: list[TracedPath], W: int, H: int) -> tuple[list[TracedPath], Optional[tuple]]:
    """VTracer 會把底色也描成一條路徑：最底下那條若幾乎蓋滿整張圖，就當成底色拿掉（與本系統把底色當透明一致）。"""
    if paths:
        cov = vr.path_coverage(paths[0].path, W, H)
        if float(cov.mean()) > 0.9 or (cov[0, :].mean() > 0.9 and cov[-1, :].mean() > 0.9):
            return paths[1:], paths[0].fill
    return paths, None


# ---------------------------------------------------------------- 執行


def _panel(inp: np.ndarray, res_rgba: np.ndarray, background, path: Path) -> None:
    comp = np.clip(np.round(vr.composite_over(res_rgba, background)), 0, 255).astype(np.uint8)
    de = np.sqrt(((_lab(comp.astype(np.float32)) - _lab(inp.astype(np.float32))) ** 2).sum(axis=2))
    heat = np.clip(de * 8.0, 0, 255).astype(np.uint8)
    diff = cv2.cvtColor(cv2.applyColorMap(heat, cv2.COLORMAP_INFERNO), cv2.COLOR_BGR2RGB)
    gap = np.full((inp.shape[0], 6, 3), 255, dtype=np.uint8)
    Image.fromarray(np.hstack([inp, gap, comp, gap, diff])).save(path)


def _mean(vals: list) -> Optional[float]:
    v = [float(x) for x in vals if x is not None and math.isfinite(float(x))]
    return round(sum(v) / len(v), 4) if v else None


_AVG_KEYS = ["render_error", "error_p95", "de_truth", "de_truth_p95", "iou", "chamfer", "chamfer_p95", "node_ratio", "color_error", "seconds"]
_RATE_KEYS = ["primitive", "completion", "stroke", "gradient"]


def _aggregate(rows: list[dict], prefix: str = "") -> dict:
    out: dict = {"n": len(rows)}
    for key in _AVG_KEYS:
        out[key] = _mean([r.get(prefix + key) for r in rows])
    for key in _RATE_KEYS:
        got = sum(r[prefix + "hits"][key][0] for r in rows if r.get(prefix + "hits"))
        tot = sum(r[prefix + "hits"][key][1] for r in rows if r.get(prefix + "hits"))
        out[key + "_rate"] = round(got / tot, 4) if tot else None
        out[key + "_total"] = tot
    if not prefix:
        out["accepted_rate"] = round(sum(1 for r in rows if r.get("accepted")) / max(len(rows), 1), 4)
    return out


def run_bench(out_dir: Optional[Path], samples_dir: Optional[Path] = None, variants: Optional[list[str]] = None, limit: Optional[int] = None,
              with_vtracer: bool = True, params: Optional[dict] = None, say: Callable[[str], None] = lambda s: None, seed: int = SEED,
              categories: Optional[list[str]] = None, panels: bool = True) -> dict:
    """跑完整的評測並寫出結果。回傳 summary（也寫進 out_dir/summary.json）。"""
    variants = variants or list(VARIANTS)
    samples = make_samples(seed)
    if categories:
        samples = [s for s in samples if s.category in categories]
    if limit:
        # 每一類平均取，保持各類都有
        by_cat: dict[str, list[Sample]] = {}
        for s in samples:
            by_cat.setdefault(s.category, []).append(s)
        picked: list[Sample] = []
        i = 0
        while len(picked) < limit and any(by_cat.values()):
            for cat in list(by_cat):
                if by_cat[cat] and len(picked) < limit:
                    picked.append(by_cat[cat].pop(0))
            i += 1
        samples = picked
    if out_dir is not None:
        out_dir.mkdir(parents=True, exist_ok=True)
        if panels:
            (out_dir / "panels").mkdir(exist_ok=True)
    rows: list[dict] = []
    for si, s in enumerate(samples):
        for v in variants:
            rng = np.random.default_rng([seed, si, VARIANTS.index(v) if v in VARIANTS else 99])
            img, k, desc = perturb(s, v, rng)
            t0 = time.perf_counter()
            res = vectorize(img, None, params)
            dt = time.perf_counter() - t0
            row: dict = {"sample": s.name, "category": s.category, "variant": v, "perturbation": desc, "seconds": round(dt, 4), "accepted": bool(res.accepted),
                         "render_error": res.render_error if math.isfinite(res.render_error) else None, "error_p95": res.error_p95 if math.isfinite(res.error_p95) else None,
                         "notes": list(res.notes)}
            row.update(evaluate(s, res.paths, k, res.background, img))
            if with_vtracer:
                vp = vtracer_paths(img)
                if vp is not None:
                    vp2, vbg = _drop_background(vp, img.shape[1], img.shape[0])
                    ev = evaluate(s, vp2, k, vbg, img)
                    for key, val in ev.items():
                        row["vtracer_" + key] = val
            rows.append(row)
            if out_dir is not None and panels:
                _panel(img, render(res), res.background or s.background, out_dir / "panels" / f"{s.name}__{v}.png")
        say(f"[{si + 1}/{len(samples)}] {s.name}")
    summary: dict = {
        "seed": seed, "n_samples": len(samples), "n_runs": len(rows), "variants": variants,
        "overall": _aggregate(rows),
        "by_variant": {v: _aggregate([r for r in rows if r["variant"] == v]) for v in variants},
        "by_category": {c: _aggregate([r for r in rows if r["category"] == c]) for c in CATEGORIES if any(r["category"] == c for r in rows)},
    }
    if any("vtracer_de_truth" in r for r in rows):
        vr_rows = [r for r in rows if "vtracer_de_truth" in r]
        summary["vtracer"] = {"overall": _aggregate(vr_rows, "vtracer_"),
                              "by_variant": {v: _aggregate([r for r in vr_rows if r["variant"] == v], "vtracer_") for v in variants},
                              "by_category": {c: _aggregate([r for r in vr_rows if r["category"] == c], "vtracer_") for c in CATEGORIES if any(r["category"] == c for r in vr_rows)}}
    def _why(notes: list) -> str:  # 取真正的原因（「放細門檻重描了幾輪」只是過程）
        return next((n for n in notes if "未通過" in n or "不適合" in n), notes[0] if notes else "未通過")

    failures = [f"{r['sample']}／{r['variant']}：" + _why(r["notes"]) for r in rows if not r["accepted"]]
    summary["not_accepted"] = failures
    if samples_dir is not None:
        summary["real"] = run_real(samples_dir, out_dir, params, say)
    summary["rows"] = rows
    if out_dir is not None:
        (out_dir / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=1), encoding="utf-8")
        (out_dir / "summary.md").write_text(summary_markdown(summary), encoding="utf-8")
    return summary


def run_real(samples_dir: Path, out_dir: Optional[Path], params: Optional[dict] = None, say: Callable[[str], None] = lambda s: None) -> dict:
    """真實樣本（PNG、JPEG）：沒有真值，只報重繪誤差、節點數、耗時，並輸出並排圖。"""
    files = sorted(p for p in samples_dir.rglob("*") if p.suffix.lower() in (".png", ".jpg", ".jpeg"))
    rows = []
    if out_dir is not None:
        (out_dir / "real").mkdir(parents=True, exist_ok=True)
    for f in files:
        try:
            pil = Image.open(f)
            rgba = np.asarray(pil.convert("RGBA"))
        except Exception as e:
            rows.append({"file": f.name, "error": f"讀不進來（{type(e).__name__}）"})
            continue
        mask = rgba[..., 3] >= 128 if (rgba[..., 3] < 255).any() else None
        img = rgba[..., :3]
        t0 = time.perf_counter()
        res = vectorize(img, mask, params)
        dt = time.perf_counter() - t0
        rows.append({"file": str(f.relative_to(samples_dir)), "size": [int(img.shape[1]), int(img.shape[0])], "accepted": bool(res.accepted),
                     "render_error": res.render_error if math.isfinite(res.render_error) else None, "error_p95": res.error_p95 if math.isfinite(res.error_p95) else None,
                     "iou": res.iou, "paths": len(res.paths), "nodes": res.node_count, "seconds": round(dt, 3), "notes": list(res.notes)})
        if out_dir is not None and res.paths:
            _panel(img, render(res), res.background or (255, 255, 255), out_dir / "real" / (f.stem + ".png"))
        say(f"真實樣本 {f.name}：{'通過' if res.accepted else '未通過'}")
    ok = [r for r in rows if "error" not in r]
    return {"n": len(rows), "accepted_rate": round(sum(1 for r in ok if r["accepted"]) / max(len(ok), 1), 4),
            "render_error": _mean([r["render_error"] for r in ok if r["accepted"]]), "nodes": _mean([r["nodes"] for r in ok if r["accepted"]]),
            "seconds": _mean([r["seconds"] for r in ok]), "rows": rows}


def _fmt(v) -> str:
    if v is None:
        return "—"
    if isinstance(v, float):
        return f"{v:.3f}" if abs(v) < 100 else f"{v:.1f}"
    return str(v)


def _table(groups: dict, cols: list[tuple[str, str]]) -> str:
    head = "| 分組 | 次數 | " + " | ".join(t for _, t in cols) + " |\n|---|---|" + "---|" * len(cols) + "\n"
    body = ""
    for name, g in groups.items():
        body += f"| {name} | {g['n']} | " + " | ".join(_fmt(g.get(k)) for k, _ in cols) + " |\n"
    return head + body


def summary_markdown(s: dict) -> str:
    cols = [("de_truth", "對真值色差 ΔE"), ("de_truth_p95", "ΔE 第 95 百分位"), ("iou", "IoU"), ("chamfer", "邊界距離 px"), ("chamfer_p95", "邊界距離 P95"),
            ("node_ratio", "節點數比"), ("primitive_rate", "基本幾何"), ("completion_rate", "分層補全"), ("stroke_rate", "筆畫還原"), ("gradient_rate", "漸層"),
            ("color_error", "顏色誤差 ΔE"), ("seconds", "秒／張")]
    md = "# 描圖向量化評測\n\n"
    md += f"合成樣本 {s['n_samples']} 個、共 {s['n_runs']} 次描圖（種子 {s['seed']}；干擾：{'、'.join(s['variants'])}）。"
    md += f"通過自我驗證的比例 {_fmt(s['overall'].get('accepted_rate'))}。\n\n"
    md += "## 整體\n\n" + _table({"本系統": s["overall"], **({"VTracer 原始輸出": s["vtracer"]["overall"]} if "vtracer" in s else {})}, cols) + "\n"
    md += "## 依干擾類型\n\n" + _table(s["by_variant"], cols) + "\n"
    if "vtracer" in s:
        md += "對照組（VTracer 0.6.15 預設參數的原始輸出）：\n\n" + _table(s["vtracer"]["by_variant"], cols) + "\n"
    md += "## 依圖形類型\n\n" + _table(s["by_category"], cols) + "\n"
    if "vtracer" in s:
        md += "對照組（VTracer）：\n\n" + _table(s["vtracer"]["by_category"], cols) + "\n"
    if s.get("not_accepted"):
        md += f"## 未通過自我驗證的 {len(s['not_accepted'])} 次\n\n" + "".join(f"- {x}\n" for x in s["not_accepted"][:80]) + "\n"
    if "real" in s:
        r = s["real"]
        md += f"## 真實樣本（沒有真值）\n\n共 {r['n']} 張，通過比例 {_fmt(r['accepted_rate'])}，通過者平均重繪色差 {_fmt(r['render_error'])}、平均節點數 {_fmt(r['nodes'])}、平均 {_fmt(r['seconds'])} 秒。\n\n"
        md += "| 檔案 | 尺寸 | 通過 | 重繪 ΔE | 路徑 | 節點 | 秒 | 說明 |\n|---|---|---|---|---|---|---|---|\n"
        for row in r["rows"]:
            if "error" in row:
                md += f"| {row['file']} | — | — | — | — | — | — | {row['error']} |\n"
            else:
                md += f"| {row['file']} | {row['size'][0]}×{row['size'][1]} | {'是' if row['accepted'] else '否'} | {_fmt(row['render_error'])} | {row['paths']} | {row['nodes']} | {row['seconds']} | {(row['notes'][-1] if row['notes'] else '')[:60]} |\n"
    md += "\n指標的定義見 mortise/eval/vector_bench.py 開頭的說明。每個樣本的「原圖｜重繪｜差異」並排圖在 panels/。\n"
    return md


def baseline_view(summary: dict) -> dict:
    """基準檔只留分組後的數字（不含逐筆結果）。"""
    keep = {k: summary[k] for k in ("seed", "n_samples", "n_runs", "variants", "overall", "by_variant", "by_category") if k in summary}
    if "vtracer" in summary:
        keep["vtracer"] = summary["vtracer"]
    keep["not_accepted_count"] = len(summary.get("not_accepted", []))
    return keep


def compare_with_baseline(summary: dict, baseline: dict, tol: Optional[dict] = None) -> list[str]:
    """與基準比較（寬鬆的回歸門檻）。回傳退步的項目說明；沒有退步回傳空清單。"""
    t = {"de_truth": 1.5, "iou": 0.03, "chamfer": 0.25, "node_ratio": 0.6, "primitive_rate": 0.15, "completion_rate": 0.25, "stroke_rate": 0.25, "gradient_rate": 0.25,
         "accepted_rate": 0.15}
    t.update(tol or {})
    higher_better = {"iou", "primitive_rate", "completion_rate", "stroke_rate", "gradient_rate", "accepted_rate"}
    out = []
    cur, base = summary.get("overall", {}), baseline.get("overall", {})
    for key, slack in t.items():
        a, b = cur.get(key), base.get(key)
        if a is None or b is None:
            continue
        if key in higher_better and a < b - slack:
            out.append(f"{key} 由 {b:.3f} 掉到 {a:.3f}（容許 {slack}）")
        if key not in higher_better and a > b + slack:
            out.append(f"{key} 由 {b:.3f} 升到 {a:.3f}（容許 {slack}）")
    return out


def main_cli(a, cfg, say: Callable[[str], None]) -> int:
    """python -m mortise.eval vector 的進入點（參數見 mortise/eval/__main__.py）。"""
    params = {k: v for k, v in cfg.section("recognize").items() if k.startswith("vector_")}
    variants = [v.strip() for v in a.variants.split(",") if v.strip()]
    cats = [c.strip() for c in (a.categories or "").split(",") if c.strip()] or None
    s = run_bench(a.out, a.samples, variants, a.limit, with_vtracer=not a.no_vtracer, params=params, say=say,
                  seed=a.seed if a.seed is not None else SEED, categories=cats, panels=not a.no_panels)
    o = s["overall"]
    print(f"合成樣本 {s['n_samples']} 個、描圖 {s['n_runs']} 次；通過自我驗證 {_fmt(o.get('accepted_rate'))}；"
          f"對真值色差 {_fmt(o.get('de_truth'))}、IoU {_fmt(o.get('iou'))}、邊界距離 {_fmt(o.get('chamfer'))} px、節點數比 {_fmt(o.get('node_ratio'))}、"
          f"每張 {_fmt(o.get('seconds'))} 秒")
    if "vtracer" in s:
        v = s["vtracer"]["overall"]
        print(f"VTracer 0.6.15：對真值色差 {_fmt(v.get('de_truth'))}、IoU {_fmt(v.get('iou'))}、邊界距離 {_fmt(v.get('chamfer'))} px、節點數比 {_fmt(v.get('node_ratio'))}")
    print(f"結果：{a.out / 'summary.md'}")
    if a.save_baseline:
        a.save_baseline.parent.mkdir(parents=True, exist_ok=True)
        a.save_baseline.write_text(json.dumps(baseline_view(s), ensure_ascii=False, indent=1), encoding="utf-8")
        print(f"基準檔：{a.save_baseline}")
    if a.baseline:
        regs = compare_with_baseline(s, json.loads(a.baseline.read_text(encoding="utf-8")))
        if regs:
            print(f"有 {len(regs)} 項退步超過容許範圍：")
            for r in regs:
                print(f"  {r}")
            return 1
        print("與基準相比沒有退步。")
    return 0
