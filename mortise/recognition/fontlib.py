"""字型庫：列舉這台機器的字型、讀出量度、用 HarfBuzz 排字、用 FreeType 畫字。

這是 3B.5「字級與字型聯合求解」的基礎設施（見 docs/architecture/03-recognize.md 3B.5 第 (1) 點）：
text_attributes.py 對每個候選字型都要能回答三件事——
這串字在 em 為 1 時排出來多寬（含字距調整）、每個字的墨跡在哪裡、用某個字級畫出來長什麼樣子。

做法
----
1. 列舉：呼叫 ``fc-list`` 取得每個字型檔的路徑、集合檔內的索引、字族名、樣式；
   沒有 fontconfig 時退回掃描常見字型目錄（用 fontTools 讀 name 表）。可另加字型目錄。
   只收 HarfBuzz 與 FreeType 都能處理的向量字型（.ttf、.otf、.ttc），彩色表情字型等排除（見 DEFAULT_PARAMS）。
2. 量度（延遲載入、快取）：fontTools（MIT）讀 head、hhea、OS/2、post 表：
   units_per_em、上伸與下伸（hhea；可改用 typo 或 win）、x 字高與大寫字高（OS/2 第 2 版以上；
   沒有就以 HarfBuzz 量 'x' 與 'H' 的墨跡）、字重、斜體角度、是否等寬。全部換算成 em 為 1 的比例。
3. 排字：uharfbuzz（Apache-2.0；HarfBuzz 本身是 Old MIT），預設開 kern、關 liga
   （Office 預設不套連字；可由參數開啟）。以字碼點逐一加入緩衝區，所以 cluster 就是字的索引。
   交出每個字的起點、每個字的墨跡外框（字形外框的聯集），以及整串字的總寬。
4. 畫字：freetype-py（FreeType License），不做 hinting，以 FT_Set_Transform 帶入精確的分數縮放、
   次像素位移、水平縮放與傾斜（假斜體），所以字級不會被捨入到整數 ppem。
   多個字形以逐像素取最大值合成，得到 0 到 1 的覆蓋率遮罩。
5. 衍生量度（延遲計算、快取）：筆畫粗細比（以 64 px 畫探針字串，距離轉換的脊線中位數 × 2 ÷ 字級）、
   量度簽章（ASCII 可列印字元的前進寬度，用來找字寬相容的「量度等價群」）。

已知限制
--------
- 可變字型沒有切成各字重的靜態實例，只用預設實例（這台機器沒有可變字型）。
- Type1（.pfb）字型 HarfBuzz 不支援，預設排除。
- 不處理直排（vertical）排字，也不處理 OpenType 的 vhea/vmtx。
- 合成粗體（embolden）用 FreeType 的 FT_Outline_Embolden；不能用時退回形態學膨脹，粗細較不準。

可單獨升級的點
--------------
- 量度資料庫：只存字寬數字、不存字形的商用字型量度（Calibri、微軟正黑體等），可在 FontLibrary
  加入「只有量度、不能畫」的虛擬 FontFace（需法務確認來源）。
- 列舉來源可換成伺服器的字型清單設定檔（font.library），不必依賴 fc-list。
"""

from __future__ import annotations

import ctypes
import os
import subprocess
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, Optional

import logging

import numpy as np

logging.getLogger("fontTools").setLevel(logging.ERROR)  # 部分字型的時間戳記欄位會觸發無害的警告

DEFAULT_PARAMS: dict = {
    # 額外的字型目錄（字串清單）；會遞迴掃描其中的 .ttf/.otf/.ttc
    "font_dirs": [],
    # 是否使用 fc-list 列舉系統字型（False 時只掃描 font_dirs 與 fallback_dirs）
    "use_fc_list": True,
    # 沒有 fc-list 時掃描的目錄；None 表示這台電腦的系統字型目錄（Linux、macOS、Windows 各自的位置，見 mortise.tools.fontfind）
    "fallback_dirs": None,
    # 接受的副檔名（HarfBuzz 與 FreeType 都能處理的向量格式）
    "extensions": [".ttf", ".otf", ".ttc", ".otc"],
    # 排除的字族（彩色點陣表情、符號字型不適合拿來比對文字）
    "exclude_families": ["Noto Color Emoji", "OpenSymbol"],
    # 上伸與下伸取哪一組量度："hhea"、"typo" 或 "win"
    "vertical_metrics": "hhea",
    # HarfBuzz 排字特性：kern 為字距調整；liga 為標準連字（Office 預設不套）
    "features": {"kern": True, "liga": False},
    # 畫字時遮罩四周留白（像素）
    "render_pad_px": 2,
    # 量筆畫粗細比時的探針字級（像素）
    "stem_probe_size_px": 64.0,
    # 拉丁文字的筆畫粗細探針字串（取直筆畫多的小寫）
    "stem_probe_latin": "nmhuil",
    # 中日韓文字的筆畫粗細探針字串
    "stem_probe_cjk": "十工中田日",
}

_STYLE_ALIASES = {
    "book": "Regular",
    "roman": "Regular",
    "normal": "Regular",
    "regular": "Regular",
    "oblique": "Italic",
    "bold oblique": "Bold Italic",
    "boldoblique": "Bold Italic",
    "bolditalic": "Bold Italic",
}


def _merge(params: dict | None) -> dict:
    out = dict(DEFAULT_PARAMS)
    if params:
        out.update(params)
    return out


def normalize_style(style: str) -> str:
    """把字型樣式名稱正規化：Book → Regular、Oblique → Italic、Bold Oblique → Bold Italic。"""
    s = (style or "Regular").strip()
    low = s.lower()
    if low in _STYLE_ALIASES:
        return _STYLE_ALIASES[low]
    return s.replace("Oblique", "Italic")


def is_cjk_char(ch: str) -> bool:
    """全形字（東亞寬度 W 或 F）：漢字、假名、全形標點。"""
    if not ch or ch.isspace():
        return False
    return unicodedata.east_asian_width(ch) in ("W", "F")


# ====================================================================== 排字結果


@dataclass
class TextLayout:
    """HarfBuzz 排字結果；所有長度以 em 為 1，y 向上（基線為 0）。"""

    text: str
    advance: float  # 整串字的總寬（最後一個字的起點加上它的前進寬度）
    starts: list[float]  # 每個字的起點（與 text 一一對應；連字中後面的字取連字起點按比例內插）
    ink: list[Optional[tuple[float, float, float, float]]]  # 每個字的墨跡 (x0, x1, top, bottom)；空白或無墨跡為 None
    glyphs: list[tuple[int, float, float, int]]  # (glyph id, 筆位 x, 筆位 y, cluster)

    def ink_bounds(self, i0: int = 0, i1: Optional[int] = None) -> Optional[tuple[float, float, float, float]]:
        """第 i0 到 i1（不含）個字的墨跡聯集；沒有墨跡回傳 None。"""
        boxes = [b for b in self.ink[i0:i1] if b is not None]
        if not boxes:
            return None
        return (min(b[0] for b in boxes), max(b[1] for b in boxes), max(b[2] for b in boxes), min(b[3] for b in boxes))


@dataclass
class RenderedText:
    """畫出來的字：mask 為 0 到 1 的覆蓋率（float32），筆位原點在 (origin_x, baseline_y)（連續像素座標）。"""

    mask: np.ndarray
    origin_x: float
    baseline_y: float
    size_px: float
    layout: TextLayout


# ====================================================================== 字型


@dataclass(eq=False)
class FontFace:
    """一個字型（集合檔中的一個 face）。量度全部換算成 em 為 1；ascender 為正、descender 為負。"""

    family: str
    style: str
    path: str
    index: int = 0
    style_raw: str = ""
    params: dict = field(default_factory=lambda: dict(DEFAULT_PARAMS), repr=False)
    _m: Optional[dict] = field(default=None, repr=False)
    _hb: Optional[tuple] = field(default=None, repr=False)
    _ft: object = field(default=None, repr=False)
    _layout_cache: dict = field(default_factory=dict, repr=False)
    _cover_cache: dict = field(default_factory=dict, repr=False)
    _stem: dict = field(default_factory=dict, repr=False)

    # ------------------------------------------------------------ 量度

    def _metrics(self) -> dict:
        if self._m is None:
            self._m = _read_metrics(self.path, self.index, self.params.get("vertical_metrics", "hhea"))
            if not self._m.get("x_height"):
                self._m["x_height"] = self._probe_height("x") or 0.5
            if not self._m.get("cap_height"):
                self._m["cap_height"] = self._probe_height("H") or 0.7
        return self._m

    def _probe_height(self, ch: str) -> Optional[float]:
        font, upem = self._hb_font()
        gid = font.get_nominal_glyph(ord(ch))
        if not gid:
            return None
        ext = font.get_glyph_extents(gid)
        return ext.y_bearing / upem if ext else None

    @property
    def units_per_em(self) -> int:
        return self._metrics()["upem"]

    @property
    def ascender(self) -> float:
        return self._metrics()["ascender"]

    @property
    def descender(self) -> float:
        return self._metrics()["descender"]

    @property
    def x_height(self) -> float:
        return self._metrics()["x_height"]

    @property
    def cap_height(self) -> float:
        return self._metrics()["cap_height"]

    @property
    def weight(self) -> int:
        return self._metrics()["weight"]

    @property
    def italic_angle(self) -> float:
        """傾斜角（度，向右傾為正；字型檔 post 表的值向右傾為負，這裡已反號）。"""
        return self._metrics()["italic_angle"]

    @property
    def is_italic(self) -> bool:
        return self._metrics()["is_italic"] or "italic" in self.style.lower()

    @property
    def is_bold(self) -> bool:
        return self.weight >= 600

    @property
    def is_monospace(self) -> bool:
        return self._metrics()["is_monospace"]

    @property
    def coverage(self) -> frozenset[int]:
        """這個字型涵蓋的字碼點（延遲載入；大字型讀一次約數十毫秒）。"""
        m = self._metrics()
        if m.get("coverage") is None:
            from fontTools.ttLib import TTFont

            with TTFont(self.path, fontNumber=self.index, lazy=True) as tt:
                m["coverage"] = frozenset((tt.getBestCmap() or {}).keys())
        return m["coverage"]

    def covers(self, text: str) -> bool:
        """這個字型是否涵蓋 text 中所有非空白字。"""
        font, _ = self._hb_font()
        for ch in set(text):
            if ch.isspace():
                continue
            hit = self._cover_cache.get(ch)
            if hit is None:
                hit = bool(font.get_nominal_glyph(ord(ch)))
                self._cover_cache[ch] = hit
            if not hit:
                return False
        return True

    @property
    def key(self) -> str:
        return f"{self.family}|{self.style}"

    # ------------------------------------------------------------ HarfBuzz

    def _hb_font(self):
        if self._hb is None:
            import uharfbuzz as hb

            blob = hb.Blob.from_file_path(self.path)
            face = hb.Face(blob, self.index)
            font = hb.Font(face)
            upem = face.upem
            font.scale = (upem, upem)
            self._hb = (font, upem)
        return self._hb

    def layout(self, text: str) -> TextLayout:
        """用 HarfBuzz 排字（含字距調整），交出每個字的起點與墨跡外框（em 為 1）。"""
        hit = self._layout_cache.get(text)
        if hit is not None:
            return hit
        import uharfbuzz as hb

        font, upem = self._hb_font()
        n = len(text)
        starts = [0.0] * n
        ink: list[Optional[tuple[float, float, float, float]]] = [None] * n
        glyphs: list[tuple[int, float, float, int]] = []
        if n == 0:
            out = TextLayout(text, 0.0, [], [], [])
            self._layout_cache[text] = out
            return out
        buf = hb.Buffer()
        buf.add_codepoints([ord(c) for c in text])
        buf.guess_segment_properties()
        hb.shape(font, buf, dict(self.params.get("features") or {}))
        pen = 0.0
        cluster_start: dict[int, float] = {}
        for info, pos in zip(buf.glyph_infos, buf.glyph_positions):
            gx = (pen + pos.x_offset) / upem
            gy = pos.y_offset / upem
            c = info.cluster
            cluster_start.setdefault(c, pen / upem)
            glyphs.append((info.codepoint, gx, gy, c))
            ext = font.get_glyph_extents(info.codepoint)
            if ext and ext.width != 0 and ext.height != 0 and 0 <= c < n:
                x0 = gx + ext.x_bearing / upem
                x1 = x0 + ext.width / upem
                top = gy + ext.y_bearing / upem
                bot = top + ext.height / upem  # height 為負
                b = ink[c]
                ink[c] = (x0, x1, top, bot) if b is None else (min(b[0], x0), max(b[1], x1), max(b[2], top), min(b[3], bot))
            pen += pos.x_advance
        total = pen / upem
        # 每個字的起點：cluster 起點；連字中後面的字按比例內插到下一個 cluster
        keys = sorted(cluster_start)
        for k_i, k in enumerate(keys):
            nxt = keys[k_i + 1] if k_i + 1 < len(keys) else n
            s0 = cluster_start[k]
            s1 = cluster_start[keys[k_i + 1]] if k_i + 1 < len(keys) else total
            span = max(1, nxt - k)
            for j in range(k, min(nxt, n)):
                starts[j] = s0 + (s1 - s0) * (j - k) / span
        out = TextLayout(text, total, starts, ink, glyphs)
        if len(self._layout_cache) > 4096:
            self._layout_cache.clear()
        self._layout_cache[text] = out
        return out

    def advance(self, text: str) -> float:
        """排版後的總寬（em 為 1），含字距調整。"""
        return self.layout(text).advance

    def advances(self, text: str) -> list[float]:
        """每個字的起點（em 為 1）。"""
        return list(self.layout(text).starts)

    def ink_bounds(self, text: str) -> Optional[tuple[float, float, float, float]]:
        """整串字的墨跡外框 (x0, x1, top, bottom)（em 為 1，y 向上）。"""
        return self.layout(text).ink_bounds()

    # ------------------------------------------------------------ FreeType

    def _ft_face(self):
        if self._ft is None:
            import freetype

            self._ft = freetype.Face(self.path, index=self.index)
        return self._ft

    def render(self, text: str, size_px: float) -> np.ndarray:
        """把 text 以字級 size_px（1 em 的像素數）畫成 0 到 1 的覆蓋率遮罩（float32）。

        版面：四周留白 render_pad_px；第一個字的筆位原點在左側留白處，基線在
        ``pad + ascender × size_px`` 這一列。需要原點位置時用 render_layout。
        """
        return self.render_layout(text, size_px).mask

    def render_layout(
        self,
        text: str,
        size_px: float,
        *,
        x_scale: float = 1.0,
        slant_deg: float = 0.0,
        embolden_px: float = 0.0,
        pad: Optional[int] = None,
        origin_frac: float = 0.0,
    ) -> RenderedText:
        """畫字並交出原點位置。

        x_scale：水平縮放（1 為不縮放）。slant_deg：假斜體角度（向右傾為正）。
        embolden_px：合成粗體時每側加粗的像素數。origin_frac：原點的次像素位移（0 到 1）。
        """
        import freetype

        pad = int(self.params.get("render_pad_px", 2) if pad is None else pad)
        lay = self.layout(text)
        s = float(size_px)
        sx = s * x_scale
        shear = np.tan(np.radians(slant_deg))
        asc = max(self.ascender, lay.ink_bounds()[2] if lay.ink_bounds() else 0.0)
        desc = min(self.descender, lay.ink_bounds()[3] if lay.ink_bounds() else 0.0)
        ib = lay.ink_bounds()
        x_min = min(0.0, ib[0] if ib else 0.0)
        x_max = max(lay.advance, ib[1] if ib else 0.0)
        extra = abs(shear) * max(asc, -desc) * s + 2 * embolden_px + 2
        width = int(np.ceil((x_max - x_min) * sx + 2 * pad + extra))
        height = int(np.ceil((asc - desc) * s + 2 * pad + 2 * embolden_px + 2))
        canvas = np.zeros((max(height, 1), max(width, 1)), np.float32)
        origin_x = pad + embolden_px + max(0.0, -x_min * sx) + (abs(shear) * (-desc) * s if shear > 0 else abs(shear) * asc * s) + origin_frac
        baseline_y = pad + embolden_px + asc * s
        face = self._ft_face()
        ppem = max(1, int(np.ceil(s)))
        face.set_char_size(ppem * 64)
        scale = s / ppem
        matrix = freetype.Matrix(int(round(scale * x_scale * 65536)), int(round(shear * scale * 65536)), 0, int(round(scale * 65536)))
        flags = freetype.FT_LOAD_NO_HINTING | freetype.FT_LOAD_NO_BITMAP
        do_embolden = embolden_px > 0
        if not do_embolden:
            flags |= freetype.FT_LOAD_RENDER
        for gid, gx, gy, _ in lay.glyphs:
            px = origin_x + gx * sx + shear * gy * s
            py = baseline_y - gy * s
            ix, iy = int(np.floor(px)), int(np.floor(py))
            fx, fy = px - ix, py - iy
            face.set_transform(matrix, freetype.Vector(int(round(fx * 64)), int(round(-fy * 64))))
            try:
                face.load_glyph(gid, flags)
                if do_embolden:
                    _embolden(face, embolden_px)
                    face.glyph.render(freetype.FT_RENDER_MODE_NORMAL)
            except Exception:  # 個別字形載入失敗時略過（極少見）
                continue
            bmp = face.glyph.bitmap
            if bmp.width == 0 or bmp.rows == 0:
                continue
            arr = np.array(bmp.buffer, dtype=np.uint8).reshape(bmp.rows, bmp.pitch)[:, : bmp.width].astype(np.float32) / 255.0
            x0 = ix + face.glyph.bitmap_left
            y0 = iy - face.glyph.bitmap_top
            _paste_max(canvas, arr, x0, y0)
        face.set_transform(freetype.Matrix(65536, 0, 0, 65536), freetype.Vector(0, 0))
        return RenderedText(canvas, origin_x, baseline_y, s, lay)

    # ------------------------------------------------------------ 衍生量度

    def stem_ratio(self, cjk: bool = False) -> float:
        """筆畫粗細 ÷ 字級（以探針字串量；2 × 面積 ÷ 輪廓總長，見 stroke_width_soft）。"""
        key = "cjk" if cjk else "latin"
        if key not in self._stem:
            probe = self.params.get("stem_probe_cjk" if cjk else "stem_probe_latin", "nmhuil")
            probe = "".join(c for c in probe if self.covers(c)) or "".join(c for c in "nmhuilHIl1" if self.covers(c))
            size = float(self.params.get("stem_probe_size_px", 64.0))
            if not probe:
                self._stem[key] = 0.08
            else:
                m = self.render_layout(probe, size).mask
                self._stem[key] = stroke_width_soft(m) / size
        return self._stem[key]

    @property
    def metric_signature(self) -> tuple:
        """ASCII 可列印字元的前進寬度（千分之一 em）；兩個字型簽章相同即為字寬相容的量度等價群。"""
        m = self._metrics()
        if "signature" not in m:
            font, upem = self._hb_font()
            sig = []
            for cp in range(32, 127):
                gid = font.get_nominal_glyph(cp)
                sig.append(round(1000 * font.get_glyph_h_advance(gid) / upem) if gid else -1)
            m["signature"] = tuple(sig)
        return m["signature"]


def _embolden(face, px: float) -> None:
    """以 FT_Outline_Embolden 加粗目前載入的字形外框（每側 px 像素）。"""
    import freetype

    try:
        outline = face.glyph._FT_GlyphSlot.contents.outline
        freetype.raw.FT_Outline_Embolden(ctypes.byref(outline), int(round(2 * px * 64)))
    except Exception:  # 退路：不加粗（呼叫端可另以膨脹補償）
        pass


def _paste_max(canvas: np.ndarray, arr: np.ndarray, x0: int, y0: int) -> None:
    h, w = arr.shape
    H, W = canvas.shape
    cx0, cy0 = max(0, x0), max(0, y0)
    cx1, cy1 = min(W, x0 + w), min(H, y0 + h)
    if cx1 <= cx0 or cy1 <= cy0:
        return
    sub = arr[cy0 - y0 : cy1 - y0, cx0 - x0 : cx1 - x0]
    np.maximum(canvas[cy0:cy1, cx0:cx1], sub, out=canvas[cy0:cy1, cx0:cx1])


def stroke_width_soft(cov: np.ndarray) -> float:
    """筆畫粗細（像素）的次像素估計：2 × 墨跡面積 ÷ 輪廓總長（總變差）。

    對細長筆畫，面積 = 寬 × 長、輪廓 ≈ 2 × 長，所以寬 = 2A ÷ P。面積與總變差在對稱模糊下都不變，
    所以這個估計不受模糊影響（模糊讓相鄰筆畫黏在一起時才會偏小）。
    """
    import cv2

    c = np.asarray(cov, np.float32)
    if c.size == 0 or c.max() <= 0:
        return 0.0
    c = np.pad(c, 1)
    gx = cv2.Sobel(c, cv2.CV_32F, 1, 0, ksize=3) / 8.0
    gy = cv2.Sobel(c, cv2.CV_32F, 0, 1, ksize=3) / 8.0
    tv = float(np.hypot(gx, gy).sum())
    return 2.0 * float(c.sum()) / tv if tv > 0 else 0.0


def stroke_width_px(binary: np.ndarray) -> float:
    """筆畫粗細（像素）：距離轉換在脊線（局部極大）上的中位數 × 2。沒有筆畫回傳 0。"""
    import cv2

    b = np.ascontiguousarray(binary.astype(np.uint8))
    if b.sum() == 0:
        return 0.0
    dt = cv2.distanceTransform(np.pad(b, 1), cv2.DIST_L2, 5)[1:-1, 1:-1]
    mx = cv2.dilate(dt, np.ones((3, 3), np.uint8))
    ridge = (dt >= mx - 1e-6) & (dt >= 0.5)
    vals = dt[ridge]
    if vals.size == 0:
        return 0.0
    # 距離轉換在 1 px 寬的筆畫上是 1（中心像素到邊界外的距離），所以寬度約為 2 × dt − 1
    return float(max(0.5, 2.0 * np.median(vals) - 1.0))


# ====================================================================== 讀量度


def _read_metrics(path: str, index: int, vertical: str) -> dict:
    from fontTools.ttLib import TTFont

    with TTFont(path, fontNumber=index, lazy=True) as tt:
        upem = tt["head"].unitsPerEm
        hhea = tt["hhea"] if "hhea" in tt else None
        os2 = tt["OS/2"] if "OS/2" in tt else None
        post = tt["post"] if "post" in tt else None
        asc = hhea.ascent if hhea else upem * 0.8
        desc = hhea.descent if hhea else -upem * 0.2
        if vertical == "typo" and os2 is not None:
            asc, desc = os2.sTypoAscender, os2.sTypoDescender
        elif vertical == "win" and os2 is not None:
            asc, desc = os2.usWinAscent, -os2.usWinDescent
        xh = getattr(os2, "sxHeight", 0) if os2 is not None and os2.version >= 2 else 0
        ch = getattr(os2, "sCapHeight", 0) if os2 is not None and os2.version >= 2 else 0
        weight = os2.usWeightClass if os2 is not None else 400
        fs_sel = os2.fsSelection if os2 is not None else 0
        ia = float(post.italicAngle) if post is not None else 0.0
        mono = bool(post.isFixedPitch) if post is not None else False
        line_gap = hhea.lineGap if hhea else 0
    return {
        "upem": int(upem),
        "ascender": asc / upem,
        "descender": desc / upem,
        "line_gap": line_gap / upem,
        "x_height": xh / upem if xh else 0.0,
        "cap_height": ch / upem if ch else 0.0,
        "weight": int(weight),
        "is_italic": bool(fs_sel & 1) or ia != 0,
        "italic_angle": -ia,
        "is_monospace": mono,
        "coverage": None,
    }


# ====================================================================== 字型庫


class FontLibrary:
    """候選字型庫。``FontLibrary.default()`` 列舉這台機器的字型（結果快取）。"""

    def __init__(self, faces: Iterable[FontFace], params: dict | None = None):
        self.params = _merge(params)
        self._faces = list(faces)
        self._cover_cache: dict[str, list[FontFace]] = {}

    @classmethod
    def default(cls, params: dict | None = None, font_dirs: Iterable[str] = ()) -> "FontLibrary":
        """列舉這台機器的字型（fc-list），可另加字型目錄。同樣的參數只列舉一次。"""
        p = _merge(params)
        dirs = tuple(str(d) for d in list(p.get("font_dirs") or []) + list(font_dirs))
        key = (dirs, bool(p["use_fc_list"]), tuple(p["extensions"]), tuple(p["exclude_families"]), p["vertical_metrics"], tuple(sorted((p.get("features") or {}).items())))
        return _build_library(key, p)

    def __len__(self) -> int:
        return len(self._faces)

    def __iter__(self):
        return iter(self._faces)

    def faces(self, text: str | None = None) -> list[FontFace]:
        """全部字型；給 text 時只回傳涵蓋其中所有非空白字的字型。"""
        if not text:
            return list(self._faces)
        key = "".join(sorted(set(c for c in text if not c.isspace())))
        hit = self._cover_cache.get(key)
        if hit is None:
            hit = [f for f in self._faces if _safe_covers(f, key)]
            self._cover_cache[key] = hit
        return list(hit)

    def find(self, family: str, style: str = "Regular") -> FontFace | None:
        """依字族名與樣式找字型（不分大小寫；樣式名先正規化）。"""
        fam = family.strip().lower()
        sty = normalize_style(style).lower()
        same = [f for f in self._faces if f.family.lower() == fam]
        for f in same:
            if f.style.lower() == sty:
                return f
        return None

    def families(self) -> dict[str, list[FontFace]]:
        out: dict[str, list[FontFace]] = {}
        for f in self._faces:
            out.setdefault(f.family, []).append(f)
        return out

    def styles_of(self, family: str) -> list[FontFace]:
        return [f for f in self._faces if f.family == family]

    def equivalents(self, face: FontFace) -> list[FontFace]:
        """與 face 字寬相容（ASCII 前進寬度完全相同）的其他字族、同樣式的字型。"""
        try:
            sig = face.metric_signature
        except Exception:
            return []
        return [f for f in self._faces if f is not face and f.family != face.family and f.style == face.style and _safe_sig(f) == sig]


def _safe_covers(f: FontFace, text: str) -> bool:
    try:
        return f.covers(text)
    except Exception:
        return False


def _safe_sig(f: FontFace):
    try:
        return f.metric_signature
    except Exception:
        return None


_LIB_CACHE: dict[tuple, "FontLibrary"] = {}


def _build_library(key: tuple, p: dict) -> FontLibrary:
    if key in _LIB_CACHE:
        return _LIB_CACHE[key]
    dirs, use_fc = key[0], key[1]
    entries: list[tuple[str, int, str, str]] = []
    from ..tools.fontfind import fontconfig_available

    if use_fc and fontconfig_available():
        entries += _fc_list()
    if not entries:
        from ..tools.fontfind import system_font_dirs

        fallback = system_font_dirs() if p["fallback_dirs"] is None else [os.path.expanduser(d) for d in p["fallback_dirs"]]
        for d in fallback:
            entries += _scan_dir(d, p["extensions"])
    for d in dirs:
        entries += _scan_dir(os.path.expanduser(d), p["extensions"])
    exts = tuple(e.lower() for e in p["extensions"])
    excl = {e.lower() for e in p["exclude_families"]}
    seen: set[tuple[str, int]] = set()
    seen_style: set[tuple[str, str]] = set()
    faces: list[FontFace] = []
    for path, index, family, style_raw in sorted(entries):
        if not path.lower().endswith(exts) or family.lower() in excl:
            continue
        if (path, index) in seen:
            continue
        seen.add((path, index))
        style = normalize_style(style_raw)
        if (family, style) in seen_style:  # 同字族同樣式的重複檔案（例如符號連結）只留一個
            continue
        seen_style.add((family, style))
        faces.append(FontFace(family=family, style=style, path=path, index=index, style_raw=style_raw, params=p))
    lib = FontLibrary(faces, p)
    _LIB_CACHE[key] = lib
    return lib


def _fc_list() -> list[tuple[str, int, str, str]]:
    try:
        out = subprocess.run(
            ["fc-list", "--format", "%{file}\t%{index}\t%{family[0]}\t%{style[0]}\n"],
            capture_output=True, text=True, timeout=30, check=False,
        ).stdout
    except (OSError, subprocess.SubprocessError):
        return []
    rows = []
    for line in out.splitlines():
        parts = line.split("\t")
        if len(parts) < 4 or not parts[0]:
            continue
        try:
            idx = int(parts[1] or 0)
        except ValueError:
            idx = 0
        rows.append((parts[0], idx, parts[2].strip(), parts[3].strip() or "Regular"))
    return rows


def _scan_dir(d: str, extensions: Iterable[str]) -> list[tuple[str, int, str, str]]:
    from fontTools.ttLib import TTCollection, TTFont

    rows: list[tuple[str, int, str, str]] = []
    if not os.path.isdir(d):
        return rows
    exts = tuple(e.lower() for e in extensions)
    for p in sorted(Path(d).rglob("*")):
        if not p.is_file() or not p.name.lower().endswith(exts):
            continue
        try:
            n = len(TTCollection(str(p), lazy=True).fonts) if p.suffix.lower() in (".ttc", ".otc") else 1
            for i in range(n):
                with TTFont(str(p), fontNumber=i, lazy=True) as tt:
                    name = tt["name"]
                    fam = name.getDebugName(16) or name.getDebugName(1) or p.stem
                    sty = name.getDebugName(17) or name.getDebugName(2) or "Regular"
                rows.append((str(p), i, fam, sty))
        except Exception:
            continue
    return rows
