"""PDF 讀取層：把 PDFium 的原始介面包成本產品需要的資料。

授權：PDFium 為 BSD-3-Clause 加 Apache-2.0（見 docs/architecture/research/pdf.md）。
PDFium 不是執行緒安全，所有呼叫都經過同一把鎖。
座標一律是 PDF 使用者空間（pt，原點左下，y 向上）；轉成頁面相對座標是②③的工作。

PDFium 的公開介面讀得到、讀不到什麼（pypdfium2 5.13、PDFium 7999 實測）：
  讀得到：路徑的線段、矩陣、填色規則（非零或奇偶）、是否描邊、填色與筆畫的顏色及不透明度、筆畫寬度、
          線帽、接合、虛線樣式、裁切路徑的幾何、表單（Form XObject）內的物件與各層矩陣。
  讀不到：填色是不是花紋或漸層（遇到時顏色會回報成白色或灰色，而且不報錯）、漸層的參數、
          混合模式、透明遮罩、裁切用的填色規則、斜接上限。
讀不到的部分由「內容串流掃描」補上（本檔後半，使用 pikepdf，MPL-2.0）：照 PDFium 產生頁面物件的規則
走一遍內容串流，記下每個路徑與漸層物件當時的狀態，再與 PDFium 的物件逐一核對；對不上就整頁放棄，
由呼叫端把這一頁的路徑當成「無法確認」處理，不會拿猜的結果去用。
"""

from __future__ import annotations

import ctypes
import math
import struct
import threading
from dataclasses import dataclass, field
from typing import Optional

import pypdfium2 as pdfium
import pypdfium2.raw as raw
from PIL import Image

from .geometry import IDENTITY, affine_apply, affine_mul

LOCK = threading.RLock()

RENDER_MODE_INVISIBLE = 3
RENDER_MODES_STROKED = (1, 2, 5, 6)  # 含描邊：常被用來模擬粗體


class PdfOpenError(Exception):
    pass


@dataclass
class RawChar:
    index: int
    text: str
    origin: tuple[float, float]  # 字的基線起點
    loose_box: tuple[float, float, float, float]  # 左、下、右、上（含字型上下緣）
    font_base: str  # 檔案中的字型名稱（可能帶子集前綴）
    font_family: str  # 內嵌字型自己記的字族名（可能為空）
    font_weight: int
    font_italic_angle: int
    size: float  # 有效字級（pt）：字級 × 文字矩陣的縱向縮放
    fill: Optional[tuple[int, int, int, int]]
    stroke: Optional[tuple[int, int, int, int]]
    render_mode: int
    angle: float  # 弧度；PDF 空間中逆時針為正
    generated: bool  # PDFium 自己補上的字（空白、換行），檔案裡沒有
    map_error: bool  # 缺 Unicode 對照
    obj_key: int  # 所屬文字物件的識別值
    ascent: float  # 字型上緣（pt，正值）
    descent: float  # 字型下緣（pt，負值）
    hscale: float  # 水平縮放比例（1.0 為未縮放）


@dataclass
class RawImage:
    z: int
    corners: list[tuple[float, float]]  # 左上、右上、右下、左下（影像自身方向）
    pixel_size: tuple[int, int]
    nested: bool
    _obj: object = field(repr=False, default=None)


SEG_LINETO, SEG_BEZIERTO, SEG_MOVETO = 0, 1, 2  # 線段種類（與 PDFium 的 FPDF_SEGMENT_* 相同）
Segment = tuple[int, float, float, bool]  # （線段種類、x、y、是否閉合）

PAINT_PLAIN = "plain"  # 這一頁沒有花紋、漸層、混合模式、透明遮罩：PDFium 回報的顏色可以直接相信
PAINT_OK = "ok"  # 內容串流掃描的結果與 PDFium 的物件一一對上
PAINT_FAILED = "failed"  # 掃描失敗或對不上：這一頁路徑的填色方式無法確認


@dataclass
class RawGradient:
    """漸層（PDF 的軸向或放射漸層），座標已換到 PDF 頁面空間。"""

    kind: str  # "linear"、"radial"
    coords: tuple[float, ...]  # linear：x0、y0、x1、y1；radial：圓心 x、圓心 y、半徑
    stops: list[tuple[float, tuple[int, int, int]]]  # （位置 0 到 1、RGB）
    extend: tuple[bool, bool] = (True, True)  # 起點之前、終點之後是否延續用端點的顏色；False 表示那一側不畫


@dataclass
class PaintInfo:
    """一個物件在繪製當下、PDFium 的公開介面讀不到的狀態（由內容串流掃描得到）。"""

    fill_pattern: str = ""  # ""（一般顏色）、"shading"（漸層）、"tiling"（花紋）、"unset"（選了花紋色彩空間但沒指定花紋）
    stroke_pattern: str = ""
    gradient: Optional[RawGradient] = None  # fill_pattern 是 "shading" 而且參數讀得出來時才有
    gradient_problem: str = ""  # 漸層讀不出來的原因（白話）
    blend: bool = False  # 混合模式不是一般（Normal）
    soft_mask: bool = False  # 套了透明遮罩
    in_group: bool = False  # 在「整組一起套透明效果」的表單裡（個別物件的不透明度不足以重現）
    clip_rule: str = "nonzero"  # 裁切用的填色規則："nonzero"、"evenodd"；先後用過兩種時是 "mixed"（不知道哪一條裁切路徑用哪一種）。四邊形的裁切不列入，因為兩種規則結果相同


@dataclass
class RawPath:
    z: int
    points: list[Segment]
    fill: Optional[tuple[int, int, int, int]]  # 沒有填色就是 None；第四個數是不透明度（0 到 255）
    stroke: Optional[tuple[int, int, int, int]]
    stroke_width: float  # 已乘上矩陣的縮放（pt）
    nested: bool
    _obj: object = field(repr=False, default=None)
    fill_rule: str = "nonzero"  # 填色規則："nonzero"（非零環繞）或 "evenodd"（奇偶）；沒有填色時沒有意義
    stroke_cap: str = "butt"  # 線帽：butt、round、square
    stroke_join: str = "miter"  # 接合：miter、round、bevel
    dash: tuple[float, ...] = ()  # 虛線樣式（pt，已乘上矩陣的縮放）；空的表示實線
    dash_phase: float = 0.0
    uniform_scale: bool = True  # 矩陣是等比縮放（筆畫在各方向一樣粗）；False 時 stroke_width 是平均值
    visual_bounds: Optional[tuple[float, float, float, float]] = None  # PDFium 算的外框，含筆畫寬度（左、下、右、上）
    clip: list[list[Segment]] = field(default_factory=list)  # 真的會切到這個物件的裁切路徑；空的表示沒有被裁切
    transparent: bool = False  # PDFium 說這個物件有透明效果（不透明度、混合模式或透明遮罩其中之一）
    paint: Optional[PaintInfo] = None  # 內容串流掃描的結果；None 表示沒有掃描（見 PageReader.paint_status）
    forms: tuple[int, ...] = ()  # 所在表單物件的繪製順序（由外到內）；直接畫在頁面上的是空的

    @property
    def bounds(self) -> tuple[float, float, float, float]:
        xs = [p[1] for p in self.points]
        ys = [p[2] for p in self.points]
        return min(xs), min(ys), max(xs), max(ys)


@dataclass
class RawShading:
    """用 sh 運算子直接畫的漸層：範圍就是當時的裁切區，所以幾何要從裁切路徑來。"""

    z: int
    bounds: tuple[float, float, float, float]  # 左、下、右、上
    clip: list[list[Segment]]  # 全部的裁切路徑（PDF 頁面空間）
    nested: bool
    paint: Optional[PaintInfo] = None
    forms: tuple[int, ...] = ()
    _obj: object = field(repr=False, default=None)


def _rgba(fn, handle, *extra) -> Optional[tuple[int, int, int, int]]:
    r, g, b, a = (ctypes.c_uint() for _ in range(4))
    ok = fn(handle, *extra, ctypes.byref(r), ctypes.byref(g), ctypes.byref(b), ctypes.byref(a))
    return (r.value, g.value, b.value, a.value) if ok else None


def _key(ptr) -> int:
    return ctypes.cast(ptr, ctypes.c_void_p).value or 0


MAX_FORM_DEPTH = 8  # 表單（Form XObject）最多往下走幾層；更深的內容不拆開，留在底圖
CLIP_TOLERANCE = 0.05  # 裁切框比物件小不到這個量（pt）就當成沒有裁切（浮點誤差、貼齊頁邊的物件）
_CAPS = {raw.FPDF_LINECAP_BUTT: "butt", raw.FPDF_LINECAP_ROUND: "round", raw.FPDF_LINECAP_PROJECTING_SQUARE: "square"}
_JOINS = {raw.FPDF_LINEJOIN_MITER: "miter", raw.FPDF_LINEJOIN_ROUND: "round", raw.FPDF_LINEJOIN_BEVEL: "bevel"}


def _own_bounds(handle) -> Optional[tuple[float, float, float, float]]:
    """物件在它所在的那一層（頁面或表單）座標中的外框，含筆畫寬度。"""
    l, b, r, t = (ctypes.c_float() for _ in range(4))
    if not raw.FPDFPageObj_GetBounds(handle, ctypes.byref(l), ctypes.byref(b), ctypes.byref(r), ctypes.byref(t)):
        return None
    return l.value, b.value, r.value, t.value


def _page_bounds(handle, ctm: tuple[float, ...]) -> Optional[tuple[float, float, float, float]]:
    """物件的外框換到頁面空間（表單有旋轉時是轉過去之後的外接矩形）。"""
    box = _own_bounds(handle)
    if box is None:
        return None
    l, b, r, t = box
    pts = [affine_apply(ctm, x, y) for x, y in ((l, b), (r, b), (r, t), (l, t))]
    return min(p[0] for p in pts), min(p[1] for p in pts), max(p[0] for p in pts), max(p[1] for p in pts)


def _clip_paths(handle) -> list[list[Segment]]:
    """物件身上的裁切路徑（座標在物件所在的那一層）。PDFium 已經把「單一矩形而且完全包住物件」的裁切拿掉了。"""
    clip = raw.FPDFPageObj_GetClipPath(handle)
    n = raw.FPDFClipPath_CountPaths(clip) if clip else -1
    out: list[list[Segment]] = []
    for i in range(max(n, 0)):
        pts: list[Segment] = []
        for j in range(max(raw.FPDFClipPath_CountPathSegments(clip, i), 0)):
            seg = raw.FPDFClipPath_GetPathSegment(clip, i, j)
            x, y = ctypes.c_float(), ctypes.c_float()
            raw.FPDFPathSegment_GetPoint(seg, ctypes.byref(x), ctypes.byref(y))
            pts.append((int(raw.FPDFPathSegment_GetType(seg)), x.value, y.value, bool(raw.FPDFPathSegment_GetClose(seg))))
        out.append(pts)
    return out


def segments_rect(pts: list[Segment], tol: float = 1e-3) -> Optional[tuple[float, float, float, float]]:
    """這串線段若是一個軸對齊的矩形，回傳（左、下、右、上）。"""
    if len(pts) not in (4, 5) or any(k == SEG_BEZIERTO for k, *_ in pts) or sum(1 for k, *_ in pts if k == SEG_MOVETO) != 1:
        return None
    xy = [(x, y) for _, x, y, _ in pts]
    if len(xy) == 5:
        if abs(xy[0][0] - xy[4][0]) > tol or abs(xy[0][1] - xy[4][1]) > tol:
            return None
        xy = xy[:4]
    for (x0, y0), (x1, y1) in zip(xy, xy[1:] + xy[:1]):
        if abs(x0 - x1) > tol and abs(y0 - y1) > tol:
            return None
    xs, ys = [q[0] for q in xy], [q[1] for q in xy]
    l, r, b, t = min(xs), max(xs), min(ys), max(ys)
    if any(min(abs(x - l), abs(x - r)) > tol or min(abs(y - b), abs(y - t)) > tol for x, y in xy):
        return None
    return l, b, r, t


def convex_polygon(pts: list[Segment]) -> Optional[list[tuple[float, float]]]:
    """這串線段若是一個凸多邊形（只有直線、只有一圈；矩形經過旋轉或傾斜後仍然是），回傳頂點（逆時針）。"""
    if any(k == SEG_BEZIERTO for k, *_ in pts) or sum(1 for k, *_ in pts if k == SEG_MOVETO) != 1:
        return None
    xy: list[tuple[float, float]] = []
    for _, x, y, _ in pts:
        if not xy or abs(xy[-1][0] - x) > 1e-9 or abs(xy[-1][1] - y) > 1e-9:
            xy.append((x, y))
    if len(xy) >= 2 and abs(xy[0][0] - xy[-1][0]) <= 1e-9 and abs(xy[0][1] - xy[-1][1]) <= 1e-9:
        xy.pop()
    if not 3 <= len(xy) <= 64:
        return None
    area = sum(x0 * y1 - x1 * y0 for (x0, y0), (x1, y1) in zip(xy, xy[1:] + xy[:1])) / 2
    if abs(area) < 1e-9:
        return None
    if area < 0:
        xy.reverse()
    # 每一個頂點都在每一條邊的左邊（或邊上）才是凸的；只看轉彎方向不夠（五角星每個彎都同方向）
    for (x0, y0), (x1, y1) in zip(xy, xy[1:] + xy[:1]):
        length = math.hypot(x1 - x0, y1 - y0)
        if length < 1e-12 or any((x1 - x0) * (y - y0) - (y1 - y0) * (x - x0) < -1e-6 * length for x, y in xy):
            return None
    return xy


def _inside_convex(poly: list[tuple[float, float]], x: float, y: float, tol: float) -> bool:
    """點是不是在凸多邊形（頂點逆時針）裡面；在邊外不到 tol 也算。"""
    for (x0, y0), (x1, y1) in zip(poly, poly[1:] + poly[:1]):
        if (x1 - x0) * (y - y0) - (y1 - y0) * (x - x0) < -tol * math.hypot(x1 - x0, y1 - y0):
            return False
    return True


def _cut_to_box(poly: list[tuple[float, float]], box: tuple[float, float, float, float]) -> list[tuple[float, float]]:
    """凸多邊形與軸對齊矩形（左、下、右、上）的交集（Sutherland–Hodgman）。沒有交集回傳空的清單。"""
    l, b, r, t = box
    for axis, limit, keep_greater in ((0, l, True), (0, r, False), (1, b, True), (1, t, False)):
        out: list[tuple[float, float]] = []
        for i, cur in enumerate(poly):
            prev = poly[i - 1]
            cur_in = cur[axis] >= limit if keep_greater else cur[axis] <= limit
            prev_in = prev[axis] >= limit if keep_greater else prev[axis] <= limit
            if cur_in != prev_in:
                k = (limit - prev[axis]) / (cur[axis] - prev[axis])
                out.append((prev[0] + k * (cur[0] - prev[0]), prev[1] + k * (cur[1] - prev[1])))
            if cur_in:
                out.append(cur)
        poly = out
        if not poly:
            return []
    return poly


def _visible_hull(handle, ctm: tuple[float, ...], page_box: tuple[float, float, float, float]) -> Optional[list[tuple[float, float]]]:
    """物件的外框（含筆畫寬度）換到頁面空間、再去掉頁面以外的部分。回傳這塊範圍的頂點；
    讀不到外框回傳 None；整個物件都在頁面外回傳空的清單。"""
    own = _own_bounds(handle)
    if own is None:
        return None
    l, b, r, t = own
    return _cut_to_box([affine_apply(ctm, x, y) for x, y in ((l, b), (r, b), (r, t), (l, t))], page_box)


def _effective_clip(handle, ctm: tuple[float, ...], containers: list[tuple[object, tuple[float, ...]]], page_box: tuple[float, float, float, float]) -> list[list[Segment]]:
    """真的會切到這個物件的裁切路徑（換到頁面空間）。
    來源有兩個：物件自己身上的，以及外層每一個表單物件身上的（containers：表單物件與它所在那一層的矩陣）。
    表單物件的裁切會套到表單裡的所有內容，但 PDFium 不會把它抄到裡面的物件上，要自己往外找。
    裁切區若是凸多邊形（矩形，或矩形被旋轉、傾斜之後的樣子），而且包住「物件在頁面內的部分」，就不算：
    軟體常常替每個物件都加一個整頁或整個圖框的裁切，那不影響畫面。"""
    hull = _visible_hull(handle, ctm, page_box)
    if hull is not None and not hull:
        return []  # 整個物件在頁面外：看不到，有沒有裁切都一樣
    out: list[list[Segment]] = []
    for owner, matrix in [(handle, ctm)] + list(containers):
        for pts in _clip_paths(owner):
            moved = [(k, *affine_apply(matrix, x, y), close) for k, x, y, close in pts]
            poly = convex_polygon(moved)
            if poly is not None and hull is not None and all(_inside_convex(poly, x, y, CLIP_TOLERANCE) for x, y in hull):
                continue
            out.append(moved)
    return out


def open_document(path: str, password: Optional[str] = None) -> pdfium.PdfDocument:
    try:
        with LOCK:
            return pdfium.PdfDocument(path, password=password)
    except pdfium.PdfiumError as e:  # 密碼錯誤、檔案損壞
        raise PdfOpenError(str(e)) from e


class PageReader:
    """讀一頁 PDF。用完請呼叫 close()。"""

    def __init__(self, doc: pdfium.PdfDocument, page_index: int, source_path: Optional[str] = None):
        """source_path 是這份 PDF 的檔案路徑：有給才能做內容串流掃描（讀花紋、漸層、混合模式）。
        沒給時 paint_status 是 PAINT_FAILED；只讀文字、圖片或只要渲染的呼叫端不必給。"""
        self.doc = doc
        self.page_index = page_index
        self.source_path = source_path
        with LOCK:
            self.page = doc[page_index]
            self._textpage = None
            self._objects: Optional[list] = None
            self._forms: dict[int, tuple[int, ...]] = {}
            self._containers: dict[int, list[tuple]] = {}  # 繪製順序 → 外層的表單物件（由內到外），每項是（表單物件、它所在那一層的矩陣）
            self._paint: Optional[tuple[str, dict[int, PaintInfo]]] = None
            self._paths: Optional[list[RawPath]] = None

    def close(self) -> None:
        with LOCK:
            if self._textpage is not None:
                self._textpage.close()
                self._textpage = None
            self.page.close()

    # ---------- 頁框 ----------
    def boxes(self) -> dict[str, tuple[float, float, float, float]]:
        """回傳各頁框（左、下、右、上）。沒有設定的頁框不會出現。"""
        with LOCK:
            out = {"media": tuple(self.page.get_mediabox())}
            for name, getter in (("crop", self.page.get_cropbox), ("trim", self.page.get_trimbox), ("bleed", self.page.get_bleedbox), ("art", self.page.get_artbox)):
                try:
                    box = getter(fallback_ok=False)
                except TypeError:
                    box = getter()
                if box:
                    out[name] = tuple(box)
            return out

    def rotation(self) -> int:
        with LOCK:
            return int(self.page.get_rotation())

    # ---------- 文字 ----------
    def chars(self) -> list[RawChar]:
        with LOCK:
            if self._textpage is None:
                self._textpage = self.page.get_textpage()
            tp = self._textpage
            n = raw.FPDFText_CountChars(tp)
            out: list[RawChar] = []
            font_cache: dict[int, tuple[str, str, int, int]] = {}
            buf = ctypes.create_string_buffer(512)
            for i in range(n):
                code = raw.FPDFText_GetUnicode(tp, i)
                # 基本平面以外的字（數學字母、表情符號）PDFium 會拆成兩個代理碼回報：併回一個字，否則下游無法編碼
                if 0xDC00 <= code <= 0xDFFF:
                    if out and len(out[-1].text) == 1 and 0xD800 <= ord(out[-1].text) <= 0xDBFF:
                        out[-1].text = chr(0x10000 + ((ord(out[-1].text) - 0xD800) << 10) + (code - 0xDC00))
                        continue
                    code = 0xFFFD  # 落單的代理碼：換成「無法辨識」的替代字
                if out and len(out[-1].text) == 1 and 0xD800 <= ord(out[-1].text) <= 0xDBFF:
                    out[-1].text = "\ufffd"
                text = chr(code) if code else ""
                generated = bool(raw.FPDFText_IsGenerated(tp, i))
                ox, oy = ctypes.c_double(), ctypes.c_double()
                raw.FPDFText_GetCharOrigin(tp, i, ctypes.byref(ox), ctypes.byref(oy))
                rect = raw.FS_RECTF()
                raw.FPDFText_GetLooseCharBox(tp, i, ctypes.byref(rect))
                obj = raw.FPDFText_GetTextObject(tp, i)
                base = family = ""
                weight, italic_angle, mode = 400, 0, 0
                ascent = descent = 0.0
                raw_size = float(raw.FPDFText_GetFontSize(tp, i))
                if obj:
                    mode = int(raw.FPDFTextObj_GetTextRenderMode(obj))
                    font = raw.FPDFTextObj_GetFont(obj)
                    if font:
                        fkey = _key(font)
                        if fkey not in font_cache:
                            ln = raw.FPDFFont_GetBaseFontName(font, buf, 512)
                            b = buf.value.decode("utf-8", "replace") if ln else ""
                            ln = raw.FPDFFont_GetFamilyName(font, buf, 512)
                            f = buf.value.decode("utf-8", "replace") if ln else ""
                            ia = ctypes.c_int()
                            raw.FPDFFont_GetItalicAngle(font, ctypes.byref(ia))
                            font_cache[fkey] = (b, f, int(raw.FPDFFont_GetWeight(font)), ia.value)
                        base, family, weight, italic_angle = font_cache[fkey]
                        a, d = ctypes.c_float(), ctypes.c_float()
                        if raw.FPDFFont_GetAscent(font, raw_size, ctypes.byref(a)):
                            ascent = a.value
                        if raw.FPDFFont_GetDescent(font, raw_size, ctypes.byref(d)):
                            descent = d.value
                m = raw.FS_MATRIX()
                vscale = hscale = 1.0
                # PDFium 回報的字元角度是順時針的；這裡統一成 PDF 空間中逆時針為正
                angle = -float(raw.FPDFText_GetCharAngle(tp, i)) % (2 * math.pi)
                if raw.FPDFText_GetMatrix(tp, i, ctypes.byref(m)):
                    vscale = math.hypot(m.c, m.d) or 1.0
                    hscale = (math.hypot(m.a, m.b) or 1.0) / vscale
                    if abs(m.a) + abs(m.b) > 1e-9:
                        angle = math.atan2(m.b, m.a)  # 文字矩陣的 x 軸方向最可靠
                if angle > math.pi:
                    angle -= 2 * math.pi
                size = raw_size * vscale
                out.append(
                    RawChar(
                        index=i, text=text, origin=(ox.value, oy.value),
                        loose_box=(rect.left, rect.bottom, rect.right, rect.top),
                        font_base=base, font_family=family, font_weight=weight, font_italic_angle=italic_angle,
                        size=size, fill=_rgba(raw.FPDFText_GetFillColor, tp, i), stroke=_rgba(raw.FPDFText_GetStrokeColor, tp, i),
                        render_mode=mode, angle=angle, generated=generated,
                        map_error=bool(raw.FPDFText_HasUnicodeMapError(tp, i)), obj_key=_key(obj) if obj else 0,
                        ascent=(ascent * vscale) or size * 0.9, descent=(descent * vscale) or -size * 0.2, hscale=hscale,
                    )
                )
            if out and len(out[-1].text) == 1 and 0xD800 <= ord(out[-1].text) <= 0xDBFF:
                out[-1].text = "\ufffd"
            return out

    # ---------- 物件 ----------
    def _walk(self) -> list:
        """依繪製順序列出所有物件（含表單內的），附上累積的變換矩陣。"""
        if self._objects is None:
            objs = []
            form_z: dict[int, int] = {}  # 表單物件的識別值 → 繪製順序
            layer: dict[int, tuple] = {}  # 表單物件的繪製順序 →（表單物件、它所在那一層的矩陣）
            for z, obj in enumerate(self.page.get_objects(max_depth=MAX_FORM_DEPTH)):
                ctm = IDENTITY
                chain: list[int] = []
                container = obj.container
                while container is not None:
                    ctm = affine_mul(ctm, tuple(container.get_matrix().get()))
                    chain.append(form_z.get(_key(container.raw), -1))
                    container = container.container
                if obj.type == raw.FPDF_PAGEOBJ_FORM:
                    form_z[_key(obj.raw)] = z
                    layer[z] = (obj.raw, ctm)
                self._forms[z] = tuple(reversed(chain))
                self._containers[z] = [layer[fz] for fz in chain if fz in layer]
                objs.append((z, obj, ctm))
            self._objects = objs
        return self._objects

    def object_z(self) -> dict[str, list[int]]:
        """各種物件的繪製順序：text、path、image、shading、form、other。分群時用來判斷兩條路徑中間隔了什麼。"""
        names = {raw.FPDF_PAGEOBJ_TEXT: "text", raw.FPDF_PAGEOBJ_PATH: "path", raw.FPDF_PAGEOBJ_IMAGE: "image", raw.FPDF_PAGEOBJ_SHADING: "shading", raw.FPDF_PAGEOBJ_FORM: "form"}
        with LOCK:
            out: dict[str, list[int]] = {}
            for z, obj, _ in self._walk():
                out.setdefault(names.get(obj.type, "other"), []).append(z)
            return out

    def object_bounds(self, kinds: tuple[str, ...] = ("text", "image")) -> list[tuple[int, tuple[float, float, float, float]]]:
        """指定種類的物件的（繪製順序、外框）。外框是 PDFium 算的，已換到頁面空間（左、下、右、上）。"""
        types = {"text": raw.FPDF_PAGEOBJ_TEXT, "path": raw.FPDF_PAGEOBJ_PATH, "image": raw.FPDF_PAGEOBJ_IMAGE, "shading": raw.FPDF_PAGEOBJ_SHADING}
        wanted = {types[k] for k in kinds}
        with LOCK:
            out = []
            for z, obj, ctm in self._walk():
                if obj.type in wanted:
                    box = _page_bounds(obj.raw, ctm)
                    if box is not None:
                        out.append((z, box))
            return out

    def text_object_z(self) -> dict[int, int]:
        """文字物件識別值 → 繪製順序。"""
        with LOCK:
            return {_key(obj.raw): z for z, obj, _ in self._walk() if obj.type == raw.FPDF_PAGEOBJ_TEXT}

    def images(self) -> list[RawImage]:
        with LOCK:
            out = []
            for z, obj, ctm in self._walk():
                if obj.type != raw.FPDF_PAGEOBJ_IMAGE:
                    continue
                m = affine_mul(tuple(obj.get_matrix().get()), ctm)
                # 影像的單位方形：(0,0) 是左下、(0,1) 是左上
                corners = [affine_apply(m, 0, 1), affine_apply(m, 1, 1), affine_apply(m, 1, 0), affine_apply(m, 0, 0)]
                w, h = ctypes.c_uint(), ctypes.c_uint()
                raw.FPDFImageObj_GetImagePixelSize(obj.raw, ctypes.byref(w), ctypes.byref(h))
                out.append(RawImage(z=z, corners=corners, pixel_size=(w.value, h.value), nested=obj.level > 0, _obj=obj))
            return out

    def image_bitmap(self, image: RawImage) -> Image.Image:
        """取出影像（已套用遮罩與方向，保留原始解析度）。"""
        with LOCK:
            try:
                bitmap = image._obj.get_bitmap(render=True, scale_to_original=True)
            except pdfium.PdfiumError:
                bitmap = image._obj.get_bitmap(render=False)
            return bitmap.to_pil().copy()

    def image_natural_bitmap(self, image: RawImage) -> Optional[Image.Image]:
        """取出影像原本的像素（影像自己的方向與長寬比，還沒套上擺放的矩陣；不含透明遮罩）。
        影像被旋轉、翻轉或非等比縮放時，在這個座標上分析比較準。取不出來回傳 None。"""
        with LOCK:
            try:
                return image._obj.get_bitmap(render=False).to_pil().copy()
            except Exception:  # 特殊的色彩空間或壓縮方式：呼叫端改用 image_bitmap 轉回去
                return None

    def paths(self) -> list[RawPath]:
        """所有路徑物件（含表單內的），座標已換到頁面空間。能從 PDFium 讀到的屬性都讀出來；
        讀不到的（花紋、漸層、混合模式、透明遮罩）放在 paint，來自內容串流掃描（見 paint_status）。"""
        with LOCK:
            if self._paths is not None:
                return self._paths
            out = []
            page_box = self._page_box()
            _, paint = self._paint_scan()
            for z, obj, ctm in self._walk():
                if obj.type != raw.FPDF_PAGEOBJ_PATH:
                    continue
                m = affine_mul(tuple(obj.get_matrix().get()), ctm)
                pts = []
                for i in range(raw.FPDFPath_CountSegments(obj.raw)):
                    seg = raw.FPDFPath_GetPathSegment(obj.raw, i)
                    x, y = ctypes.c_float(), ctypes.c_float()
                    raw.FPDFPathSegment_GetPoint(seg, ctypes.byref(x), ctypes.byref(y))
                    px, py = affine_apply(m, x.value, y.value)
                    pts.append((int(raw.FPDFPathSegment_GetType(seg)), px, py, bool(raw.FPDFPathSegment_GetClose(seg))))
                if not pts:
                    continue
                fill_mode, has_stroke = ctypes.c_int(), ctypes.c_int()
                raw.FPDFPath_GetDrawMode(obj.raw, ctypes.byref(fill_mode), ctypes.byref(has_stroke))
                width = ctypes.c_float()
                raw.FPDFPageObj_GetStrokeWidth(obj.raw, ctypes.byref(width))
                scale = math.sqrt(abs(m[0] * m[3] - m[1] * m[2])) or 1.0
                sx, sy = math.hypot(m[0], m[1]), math.hypot(m[2], m[3])
                uniform = abs(sx - sy) <= 0.02 * max(sx, sy, 1e-12) and abs(m[0] * m[2] + m[1] * m[3]) <= 0.02 * max(sx * sy, 1e-12)
                dash: tuple[float, ...] = ()
                phase = ctypes.c_float()
                n_dash = raw.FPDFPageObj_GetDashCount(obj.raw) if has_stroke.value else 0
                if n_dash > 0:
                    arr = (ctypes.c_float * n_dash)()
                    if raw.FPDFPageObj_GetDashArray(obj.raw, arr, n_dash):
                        dash = tuple(v * scale for v in arr)
                    raw.FPDFPageObj_GetDashPhase(obj.raw, ctypes.byref(phase))
                out.append(
                    RawPath(
                        z=z, points=pts,
                        fill=_rgba(raw.FPDFPageObj_GetFillColor, obj.raw) if fill_mode.value else None,
                        stroke=_rgba(raw.FPDFPageObj_GetStrokeColor, obj.raw) if has_stroke.value else None,
                        stroke_width=width.value * scale, nested=obj.level > 0, _obj=obj,
                        fill_rule="evenodd" if fill_mode.value == raw.FPDF_FILLMODE_ALTERNATE else "nonzero",
                        stroke_cap=_CAPS.get(raw.FPDFPageObj_GetLineCap(obj.raw), "butt"),
                        stroke_join=_JOINS.get(raw.FPDFPageObj_GetLineJoin(obj.raw), "miter"),
                        dash=dash, dash_phase=phase.value * scale, uniform_scale=uniform,
                        visual_bounds=_page_bounds(obj.raw, ctm), clip=_effective_clip(obj.raw, ctm, self._containers.get(z, []), page_box),
                        transparent=bool(raw.FPDFPageObj_HasTransparency(obj.raw)),
                        paint=paint.get(z), forms=self._forms.get(z, ()),
                    )
                )
            self._paths = out
            return out

    def shadings(self) -> list[RawShading]:
        """用 sh 運算子直接畫的漸層物件。PDFium 只給得出外框與裁切路徑；漸層的參數在 paint（內容串流掃描）。"""
        with LOCK:
            out = []
            _, paint = self._paint_scan()
            for z, obj, ctm in self._walk():
                if obj.type != raw.FPDF_PAGEOBJ_SHADING:
                    continue
                box = _page_bounds(obj.raw, ctm)
                if box is None:
                    continue
                clip = [[(k, *affine_apply(matrix, x, y), c) for k, x, y, c in path]
                        for owner, matrix in [(obj.raw, ctm)] + self._containers.get(z, []) for path in _clip_paths(owner)]  # 外層表單物件的裁切也算
                out.append(RawShading(z=z, bounds=box, clip=clip, nested=obj.level > 0, paint=paint.get(z), forms=self._forms.get(z, ()), _obj=obj))
            return out

    def _page_box(self) -> tuple[float, float, float, float]:
        """頁面看得到的範圍（裁切框與媒體框的交集）。"""
        boxes = self.boxes()
        media = boxes["media"]
        crop = boxes.get("crop", media)
        box = (max(crop[0], media[0]), max(crop[1], media[1]), min(crop[2], media[2]), min(crop[3], media[3]))
        return box if box[2] > box[0] and box[3] > box[1] else media

    # ---------- 內容串流掃描 ----------
    @property
    def paint_status(self) -> str:
        """路徑的填色方式能不能相信：
        PAINT_OK（掃描結果與 PDFium 的物件一一對上，paint 欄位可用）、
        PAINT_PLAIN（掃描沒對上，但這一頁沒有花紋、漸層、混合模式、透明遮罩、透明群組：PDFium 回報的顏色仍然可信，只是沒有 paint 欄位）、
        PAINT_FAILED（掃描失敗或對不上，而且這一頁有上述內容：PDFium 回報的填色可能是假的，呼叫端不應該相信）。"""
        with LOCK:
            return self._paint_scan()[0]

    def _paint_scan(self) -> tuple[str, dict[int, PaintInfo]]:
        if self._paint is None:
            self._paint = self._run_paint_scan()
        return self._paint

    def _run_paint_scan(self) -> tuple[str, dict[int, PaintInfo]]:
        needed = True  # 不知道這一頁有沒有花紋之類的東西時，先當成有
        try:
            import pikepdf

            with pikepdf.open(self.source_path) as pdf_file:
                page = pdf_file.pages[self.page_index]
                needed = _needs_paint_scan(page)
                records = _PaintScanner(page).run()
        except Exception:  # 沒給檔案路徑、pikepdf 打不開、內容串流讀不懂：掃描只是補充資訊，不中斷整頁
            return (PAINT_FAILED if needed else PAINT_PLAIN), {}
        # 與 PDFium 的物件逐一核對：種類的順序要一樣，路徑的點數也要一樣
        kinds = {raw.FPDF_PAGEOBJ_PATH: "path", raw.FPDF_PAGEOBJ_SHADING: "shading", raw.FPDF_PAGEOBJ_FORM: "form"}
        mine = [(z, kinds[obj.type], obj) for z, obj, _ in self._walk() if obj.type in kinds]
        failed = (PAINT_FAILED if needed else PAINT_PLAIN), {}
        if len(mine) != len(records):
            return failed
        out: dict[int, PaintInfo] = {}
        for (z, kind, obj), (r_kind, r_points, info) in zip(mine, records):
            if kind != r_kind or (kind == "path" and raw.FPDFPath_CountSegments(obj.raw) != r_points):
                return failed
            if info is not None:
                out[z] = info
        return PAINT_OK, out

    def object_counts(self) -> dict[str, int]:
        names = {raw.FPDF_PAGEOBJ_TEXT: "text", raw.FPDF_PAGEOBJ_PATH: "path", raw.FPDF_PAGEOBJ_IMAGE: "image", raw.FPDF_PAGEOBJ_SHADING: "shading", raw.FPDF_PAGEOBJ_FORM: "form"}
        with LOCK:
            counts: dict[str, int] = {}
            for _, obj, _ in self._walk():
                name = names.get(obj.type, "other")
                counts[name] = counts.get(name, 0) + 1
            return counts

    # ---------- 渲染 ----------
    def render(self, dpi: float, hide_text: bool = False, hide_z: Optional[set[int]] = None) -> Image.Image:
        """把頁面畫成影像（已套用頁面旋轉與裁切框）。可暫時隱藏文字或指定物件，用來取得底圖。"""
        with LOCK:
            hidden = []
            if hide_text or hide_z:
                for z, obj, _ in self._walk():
                    if (hide_text and obj.type == raw.FPDF_PAGEOBJ_TEXT) or (hide_z and z in hide_z):
                        if raw.FPDFPageObj_SetIsActive(obj.raw, False):
                            hidden.append(obj)
            try:
                bitmap = self.page.render(scale=dpi / 72.0, may_draw_forms=True)
                return bitmap.to_pil().convert("RGB")
            finally:
                for obj in hidden:
                    raw.FPDFPageObj_SetIsActive(obj.raw, True)

    def render_size(self, dpi: float) -> tuple[int, int]:
        """整頁以這個解析度渲染時的像素大小（與 render 的結果一致）。"""
        with LOCK:
            scale = dpi / 72.0
            return math.ceil(self.page.get_width() * scale), math.ceil(self.page.get_height() * scale)

    def render_only(self, groups: list[tuple[set[int], float, tuple[int, int, int, int]]], each=None) -> list[Image.Image]:
        """每一組只畫指定的物件（其餘全部隱藏），透明底，得到 RGBA 影像（顏色沒有預乘不透明度）。
        groups 的每一項是（要畫的物件的繪製順序、解析度 dpi、像素範圍）。像素範圍（左、上、右、下）以「整頁用這個
        解析度渲染」的像素格為準（見 render_size），只畫這一塊。不畫註解與表單欄位。
        有給 each 時，每畫好一張就呼叫 each(第幾組, 影像)，不把影像留在記憶體裡（一頁可能有上百組），回傳空的清單；
        沒給就回傳全部影像。"""
        with LOCK:
            objs = self._walk()
            by_z = {z: obj for z, obj, _ in objs}
            hidden = []
            for z, obj, _ in objs:  # 先把表單以外的物件全部隱藏；表單留著，裡面的物件個別開關
                if obj.type == raw.FPDF_PAGEOBJ_FORM:
                    continue
                active = ctypes.c_int(1)
                raw.FPDFPageObj_GetIsActive(obj.raw, ctypes.byref(active))
                if active.value and raw.FPDFPageObj_SetIsActive(obj.raw, False):
                    hidden.append(obj)
            out: list[Image.Image] = []
            try:
                for index, (show, dpi, (x0, y0, x1, y1)) in enumerate(groups):
                    full_w, full_h = self.render_size(dpi)
                    shown = [by_z[z] for z in show if z in by_z]
                    for obj in shown:
                        raw.FPDFPageObj_SetIsActive(obj.raw, True)
                    try:
                        w, h = max(x1 - x0, 1), max(y1 - y0, 1)
                        bitmap = pdfium.PdfBitmap.new_native(w, h, raw.FPDFBitmap_BGRA, rev_byteorder=True)
                        bitmap.fill_rect((0, 0, 0, 0), 0, 0, w, h)
                        raw.FPDF_RenderPageBitmap(bitmap, self.page, -x0, -y0, full_w, full_h, 0, raw.FPDF_REVERSE_BYTE_ORDER)
                        image = bitmap.to_pil().convert("RGBA").copy()
                        bitmap.close()
                    finally:
                        for obj in shown:
                            raw.FPDFPageObj_SetIsActive(obj.raw, False)
                    if each is not None:
                        each(index, image)
                    else:
                        out.append(image)
            finally:
                for obj in hidden:
                    raw.FPDFPageObj_SetIsActive(obj.raw, True)
            return out


def document_metadata(doc: pdfium.PdfDocument) -> dict[str, str]:
    with LOCK:
        try:
            meta = doc.get_metadata_dict(skip_empty=True)
        except TypeError:
            meta = {k: v for k, v in doc.get_metadata_dict().items() if v}
        return {str(k): str(v) for k, v in meta.items()}


# ====================================================================== 內容串流掃描（pikepdf）
#
# PDFium 的公開介面不會說一條路徑是不是用花紋或漸層填的，也讀不到混合模式與透明遮罩。
# 這裡照 PDFium 產生頁面物件的規則（core/fpdfapi/page/cpdf_streamcontentparser.cpp）走一遍內容串流，
# 依序記下每個「路徑」「漸層」「表單」物件當時的狀態，順序與 PageReader._walk 相同。
# PageReader._run_paint_scan 會把結果與 PDFium 的物件逐一核對（種類、路徑點數），對不上就整頁放棄。

_PT_MOVE, _PT_LINE, _PT_BEZIER = 0, 1, 2
_PLAIN = PaintInfo()  # 沒有任何特殊狀態時共用同一份（當成唯讀）
_UNSET = "unset"  # 選了花紋色彩空間、但還沒指定花紋


def _f32(v) -> float:
    """PDFium 用單精度浮點數存座標；比較「是不是同一點」時要用同樣的精度。"""
    try:
        return struct.unpack("f", struct.pack("f", float(v)))[0]
    except (OverflowError, ValueError, TypeError):
        return 0.0


def _inherited(obj, key: str):
    """頁面字典的可繼承項目（Resources 可以寫在上層的 Pages 節點）。"""
    seen = 0
    while obj is not None and seen < 64:
        value = obj.get(key)
        if value is not None:
            return value
        obj = obj.get("/Parent")
        seen += 1
    return None


def _is_dict(obj) -> bool:
    import pikepdf

    return isinstance(obj, (pikepdf.Dictionary, pikepdf.Stream))


def _needs_paint_scan(page) -> bool:
    """這一頁（含它用到的表單）有沒有花紋、漸層、混合模式、透明遮罩、透明群組。都沒有就不必掃描。"""
    import pikepdf

    seen: set = set()

    def special(res, depth: int) -> bool:
        if not _is_dict(res) or depth > 40:
            return False
        for key in ("/Pattern", "/Shading"):
            holder = res.get(key)
            if _is_dict(holder) and len(holder.keys()) > 0:
                return True
        gs = res.get("/ExtGState")
        if _is_dict(gs):
            for name in gs.keys():
                state = gs.get(name)
                if not _is_dict(state):
                    continue
                bm = state.get("/BM")
                if isinstance(bm, pikepdf.Array):
                    bm = bm[0] if len(bm) else None
                if bm is not None and str(bm) not in ("/Normal", "/Compatible"):
                    return True
                if _is_dict(state.get("/SMask")):
                    return True
        cs = res.get("/ColorSpace")
        if _is_dict(cs):
            for name in cs.keys():
                value = cs.get(name)
                if value == "/Pattern" or (isinstance(value, pikepdf.Array) and len(value) and value[0] == "/Pattern"):
                    return True
        xo = res.get("/XObject")
        if _is_dict(xo):
            for name in xo.keys():
                form = xo.get(name)
                if not isinstance(form, pikepdf.Stream) or form.get("/Subtype") != "/Form":
                    continue
                if form.objgen != (0, 0):
                    if form.objgen in seen:
                        continue
                    seen.add(form.objgen)
                group = form.get("/Group")
                if _is_dict(group) and group.get("/S") == "/Transparency":
                    return True  # 透明群組：要知道它是不是被整組套了不透明度
                if special(form.get("/Resources"), depth + 1):
                    return True
        return False

    return special(_inherited(page.obj, "/Resources"), 0)


class _ScanState:
    """掃描時跟著 q／Q 存取的繪圖狀態（只留判斷需要的部分）。"""

    __slots__ = ("ctm", "fill_pat", "stroke_pat", "blend", "smask", "ca", "CA", "cap", "clip_rule", "clip_seen", "group")

    def __init__(self) -> None:
        self.ctm = IDENTITY
        self.fill_pat = None  # None：一般顏色；_UNSET；或花紋物件
        self.stroke_pat = None
        self.blend = False
        self.smask = False
        self.ca = 1.0  # 填色的不透明度
        self.CA = 1.0  # 筆畫的不透明度
        self.cap = 0
        self.clip_rule = "nonzero"
        self.clip_seen = False  # 有沒有出現過「填色規則會影響結果」的裁切路徑
        self.group = False

    def copy(self) -> "_ScanState":
        other = _ScanState.__new__(_ScanState)
        for name in _ScanState.__slots__:
            setattr(other, name, getattr(self, name))
        return other


class _PaintScanner:
    def __init__(self, page) -> None:
        self.page = page
        self.page_res = _inherited(page.obj, "/Resources")
        self.records: list[tuple[str, int, Optional[PaintInfo]]] = []  # （種類、路徑點數、狀態）
        self.active: set = set()  # 正在掃描的表單（避免表單自己包自己）
        self.pattern_cache: dict = {}
        # 目前這個內容串流的路徑暫存（對應 PDFium 的 path_points_ 等）
        self.pts: list[list] = []
        self.cur = (0.0, 0.0)
        self.start = (0.0, 0.0)
        self.clip_pending: Optional[str] = None

    def run(self) -> list[tuple[str, int, Optional[PaintInfo]]]:
        self._stream(self.page, self.page_res, _ScanState(), IDENTITY, 0)
        return self.records

    # ---------- 資源 ----------
    def _find(self, res, kind: str, name):
        """照 PDFium 的找法：先看目前內容串流的資源有沒有這一類；沒有才退回頁面的資源。"""
        holder = res.get(kind) if _is_dict(res) else None
        if not _is_dict(holder):
            if res is self.page_res or not _is_dict(self.page_res):
                return None
            holder = self.page_res.get(kind)
            if not _is_dict(holder):
                return None
        return holder.get(str(name))

    # ---------- 路徑暫存（照 PDFium 的 AddPathPoint 等） ----------
    def _point(self, x, y, kind: int) -> None:
        pt = (_f32(x), _f32(y))
        pts = self.pts
        if kind == _PT_MOVE and pts and not pts[-1][3] and pts[-1][2] == _PT_MOVE and self.cur == pt:
            return
        self.cur = pt
        if kind == _PT_MOVE:
            self.start = pt
            if pts and pts[-1][2] == _PT_MOVE and not pts[-1][3]:
                pts[-1][0], pts[-1][1] = pt
                return
        elif not pts:
            return
        pts.append([pt[0], pt[1], kind, False])

    def _point_close(self, pt: tuple[float, float]) -> None:
        self.cur = pt
        if self.pts:
            self.pts.append([pt[0], pt[1], _PT_LINE, True])

    def _close(self) -> None:
        if not self.pts:
            return
        if self.start != self.cur:
            self._point_close(self.start)
        else:
            self.pts[-1][3] = True

    def _paint(self, st: _ScanState, base: tuple[float, ...], fills: bool, strokes: bool) -> None:
        """畫路徑的運算子：照 PDFium 的 AddPathObject 決定會不會產生一個路徑物件。"""
        pts, self.pts = self.pts, []
        clip, self.clip_pending = self.clip_pending, None
        if not pts:
            return
        if len(pts) == 1:
            if clip:
                return
            only = pts[0]
            if only[2] != _PT_MOVE or not only[3] or st.cap != 1:
                return
            only[3] = False  # 圓頭線帽時，「移到一點就閉合」會畫成一個圓點
            pts.append([only[0], only[1], _PT_LINE, True])
        if pts[-1][2] == _PT_MOVE and not pts[-1][3]:
            pts.pop()
        if fills or strokes:
            self.records.append(("path", len(pts), self._info(st, base, fills, strokes)))
        corners = len(pts) - (1 if len(pts) >= 2 and pts[-1][:2] == pts[0][:2] else 0)
        if clip and not (corners <= 4 and all(q[2] != _PT_BEZIER for q in pts)):
            # 三角形、四邊形的裁切（最常見的是矩形）用哪一種規則結果都一樣，不列入
            if not st.clip_seen:
                st.clip_rule, st.clip_seen = clip, True
            elif st.clip_rule != clip:
                st.clip_rule = "mixed"

    # ---------- 狀態 → PaintInfo ----------
    def _info(self, st: _ScanState, base: tuple[float, ...], fills: bool, strokes: bool) -> PaintInfo:
        fill_pat = st.fill_pat if fills else None
        stroke_pat = st.stroke_pat if strokes else None
        if fill_pat is None and stroke_pat is None and not (st.blend or st.smask or st.group) and st.clip_rule == "nonzero":
            return _PLAIN
        info = PaintInfo(blend=st.blend, soft_mask=st.smask, in_group=st.group, clip_rule=st.clip_rule)
        if fill_pat is not None:
            info.fill_pattern, info.gradient, info.gradient_problem = self._pattern(fill_pat, base)
        if stroke_pat is not None:
            info.stroke_pattern = self._pattern(stroke_pat, base)[0]
        return info

    def _pattern(self, pat, base: tuple[float, ...]) -> tuple[str, Optional[RawGradient], str]:
        if pat is _UNSET:
            return _UNSET, None, ""
        key = (pat.objgen, base) if pat.objgen != (0, 0) else None
        if key is not None and key in self.pattern_cache:
            return self.pattern_cache[key]
        try:
            ptype = int(pat.get("/PatternType", 0))
        except (TypeError, ValueError):
            ptype = 0
        if ptype == 2:
            if pat.get("/ExtGState") is not None:
                out = ("shading", None, "漸層另外帶了繪圖狀態")
            else:
                matrix = affine_mul(_matrix(pat.get("/Matrix")), base)  # 花紋矩陣 × 所在內容串流的起始矩陣
                gradient, problem = read_gradient(pat.get("/Shading"), matrix)
                out = ("shading", gradient, problem)
        else:
            out = ("tiling", None, "")
        if key is not None:
            self.pattern_cache[key] = out
        return out

    # ---------- 內容串流 ----------
    def _stream(self, source, res, st: _ScanState, base: tuple[float, ...], level: int) -> None:
        import pikepdf

        saved_path = (self.pts, self.cur, self.start, self.clip_pending)
        self.pts, self.cur, self.start, self.clip_pending = [], (0.0, 0.0), (0.0, 0.0), None
        stack: list[_ScanState] = []

        def num(a, i: int) -> float:  # 從後面數第 i 個運算元；缺的當成 0（與 PDFium 相同）
            try:
                return float(a[-1 - i])
            except (IndexError, TypeError, ValueError):
                return 0.0

        for ins in pikepdf.parse_content_stream(source):
            op = str(ins.operator)
            a = ins.operands
            if op == "m":
                if len(a) == 2:
                    self._point(num(a, 1), num(a, 0), _PT_MOVE)
            elif op == "l":
                if len(a) == 2:
                    self._point(num(a, 1), num(a, 0), _PT_LINE)
            elif op == "c":
                self._point(num(a, 5), num(a, 4), _PT_BEZIER)
                self._point(num(a, 3), num(a, 2), _PT_BEZIER)
                self._point(num(a, 1), num(a, 0), _PT_BEZIER)
            elif op == "v":
                self._point(self.cur[0], self.cur[1], _PT_BEZIER)
                self._point(num(a, 3), num(a, 2), _PT_BEZIER)
                self._point(num(a, 1), num(a, 0), _PT_BEZIER)
            elif op == "y":
                self._point(num(a, 3), num(a, 2), _PT_BEZIER)
                self._point(num(a, 1), num(a, 0), _PT_BEZIER)
                self._point(num(a, 1), num(a, 0), _PT_BEZIER)
            elif op == "h":
                self._close()
            elif op == "re":
                x, y, w, h = _f32(num(a, 3)), _f32(num(a, 2)), _f32(num(a, 1)), _f32(num(a, 0))
                self._point(x, y, _PT_MOVE)
                self._point(x + w, y, _PT_LINE)
                self._point(x + w, y + h, _PT_LINE)
                self._point(x, y + h, _PT_LINE)
                self._point_close((_f32(x), _f32(y)))
            elif op in ("f", "F", "f*"):
                self._paint(st, base, True, False)
            elif op == "S":
                self._paint(st, base, False, True)
            elif op == "s":
                self._close()
                self._paint(st, base, False, True)
            elif op in ("B", "B*"):
                self._paint(st, base, True, True)
            elif op == "b":
                self._close()
                self._paint(st, base, True, True)
            elif op == "b*":
                self._point_close(self.start)
                self._paint(st, base, True, True)
            elif op == "n":
                self._paint(st, base, False, False)
            elif op == "W":
                self.clip_pending = "nonzero"
            elif op == "W*":
                self.clip_pending = "evenodd"
            elif op == "q":
                stack.append(st.copy())
            elif op == "Q":
                if stack:
                    st = stack.pop()
            elif op == "cm":
                st.ctm = affine_mul(tuple(num(a, i) for i in (5, 4, 3, 2, 1, 0)), st.ctm)
            elif op == "gs":
                state = self._find(res, "/ExtGState", a[0]) if a else None
                if isinstance(state, pikepdf.Dictionary):
                    self._ext_gstate(state, st)
            elif op in ("cs", "CS"):
                pat = self._colorspace(res, a[0] if a else None, st.fill_pat if op == "cs" else st.stroke_pat)
                if op == "cs":
                    st.fill_pat = pat
                else:
                    st.stroke_pat = pat
            elif op in ("scn", "SCN"):
                if a and isinstance(a[-1], pikepdf.Name):
                    pat = self._find(res, "/Pattern", a[-1])
                    if _is_dict(pat):
                        if op == "scn":
                            st.fill_pat = pat
                        else:
                            st.stroke_pat = pat
            elif op == "g":
                st.fill_pat = None
            elif op == "G":
                st.stroke_pat = None
            elif op == "rg":
                if len(a) == 3:
                    st.fill_pat = None
            elif op == "RG":
                if len(a) == 3:
                    st.stroke_pat = None
            elif op == "k":
                if len(a) == 4:
                    st.fill_pat = None
            elif op == "K":
                if len(a) == 4:
                    st.stroke_pat = None
            elif op == "J":
                st.cap = int(num(a, 0))
            elif op == "sh":
                shading = self._find(res, "/Shading", a[0]) if a else None
                if _is_dict(shading):
                    gradient, problem = read_gradient(shading, st.ctm)
                    self.records.append(("shading", 0, PaintInfo(
                        fill_pattern="shading", gradient=gradient, gradient_problem=problem,
                        blend=st.blend, soft_mask=st.smask, in_group=st.group, clip_rule=st.clip_rule,
                    )))
            elif op == "Do":
                xobj = self._find(res, "/XObject", a[0]) if a else None
                if isinstance(xobj, pikepdf.Stream) and xobj.get("/Subtype") == "/Form":
                    self._form(xobj, res, st, level)
        self.pts, self.cur, self.start, self.clip_pending = saved_path

    def _ext_gstate(self, state, st: _ScanState) -> None:
        import pikepdf

        bm = state.get("/BM")
        if bm is not None:
            if isinstance(bm, pikepdf.Array):
                bm = bm[0] if len(bm) else "/Normal"
            st.blend = str(bm) not in ("/Normal", "/Compatible")
        if "/SMask" in state:
            st.smask = isinstance(state.get("/SMask"), pikepdf.Dictionary)
        for key in ("ca", "CA"):
            value = state.get("/" + key)
            if value is not None:
                try:
                    setattr(st, key, float(value))
                except (TypeError, ValueError):
                    pass
        if state.get("/LC") is not None:
            try:
                st.cap = int(state.get("/LC"))
            except (TypeError, ValueError):
                pass

    def _colorspace(self, res, name, current):
        """cs／CS：換色彩空間。回傳新的花紋狀態（None 表示一般顏色；找不到色彩空間時維持原狀）。"""
        import pikepdf

        if name is None:
            return current
        text = str(name)
        if text == "/Pattern":
            return _UNSET
        if text in ("/DeviceGray", "/DeviceRGB", "/DeviceCMYK"):
            return None
        value = self._find(res, "/ColorSpace", name)
        if value is None:
            return current
        if value == "/Pattern" or (isinstance(value, pikepdf.Array) and len(value) and value[0] == "/Pattern"):
            return _UNSET
        return None

    def _form(self, form, res, st: _ScanState, level: int) -> None:
        self.records.append(("form", 0, None))  # 表單物件本身（PageReader._walk 先列出表單，再列出裡面的物件）
        if level >= MAX_FORM_DEPTH - 1:
            return  # 再往下的內容 _walk 不會列出來
        ident = form.objgen
        if ident != (0, 0) and ident in self.active:
            return
        inner = st.copy()
        inner.ctm = affine_mul(_matrix(form.get("/Matrix")), st.ctm)
        group = form.get("/Group")
        if _is_dict(group) and group.get("/S") == "/Transparency":
            # 透明群組：裡面的物件先各自畫好，再整組套上外面的不透明度、混合模式、遮罩
            harmful = st.blend or st.smask or abs(st.ca - 1.0) > 1e-6 or abs(st.CA - 1.0) > 1e-6 or bool(group.get("/K", False))
            inner.group = st.group or harmful
            inner.blend, inner.smask, inner.ca, inner.CA = False, False, 1.0, 1.0
        inner_res = form.get("/Resources")
        if not _is_dict(inner_res):
            inner_res = res if _is_dict(res) else self.page_res
        if ident != (0, 0):
            self.active.add(ident)
        try:
            self._stream(form, inner_res, inner, inner.ctm, level + 1)
        finally:
            self.active.discard(ident)


def _matrix(value) -> tuple[float, ...]:
    try:
        m = tuple(float(v) for v in value)
        return m if len(m) == 6 else IDENTITY
    except (TypeError, ValueError):
        return IDENTITY


# ---------- 漸層 ----------


def _color_reader(cs):
    """色彩空間 →（分量數、把分量轉成 RGB 的函式）。只接受不必做色彩管理就能精確換算的：
    DeviceRGB、DeviceGray，以及描述檔是 sRGB 的 ICCBased。其他回傳 None。"""
    import pikepdf

    def rgb(v):
        return tuple(int(round(255 * min(max(float(x), 0.0), 1.0))) for x in v[:3])

    def gray(v):
        g = int(round(255 * min(max(float(v[0]), 0.0), 1.0)))
        return g, g, g

    if cs == "/DeviceRGB":
        return 3, rgb
    if cs == "/DeviceGray":
        return 1, gray
    if isinstance(cs, pikepdf.Array) and len(cs) == 2 and cs[0] == "/ICCBased" and isinstance(cs[1], pikepdf.Stream):
        try:
            n = int(cs[1].get("/N", 0))
            profile = cs[1].read_bytes()
        except Exception:
            return None
        if b"sRGB" in profile[:4096] and n == 3:
            return 3, rgb
    return None


def _function_stops(fn, n_out: int) -> Optional[list[tuple[float, list[float]]]]:
    """漸層的顏色函式 → 幾個（位置 0 到 1、顏色分量）。只接受「線性內插」（類型 2、指數 1）與它們的接合（類型 3）；
    其他（取樣函式、PostScript 函式、指數不是 1）顏色不是直線變化，回傳 None。"""
    import pikepdf

    def floats(value, default):
        try:
            return [float(v) for v in value] if value is not None else list(default)
        except (TypeError, ValueError):
            return list(default)

    def linear(f) -> Optional[tuple[list[float], list[float]]]:
        if not _is_dict(f) or int(f.get("/FunctionType", -1)) != 2 or abs(float(f.get("/N", 0)) - 1.0) > 1e-6:
            return None
        if floats(f.get("/Domain"), [0, 1]) != [0.0, 1.0]:
            return None
        c0, c1 = floats(f.get("/C0"), [0.0]), floats(f.get("/C1"), [1.0])
        return (c0, c1) if len(c0) == len(c1) else None

    if isinstance(fn, pikepdf.Array):  # 每個顏色分量各一個函式
        parts = [linear(f) for f in fn]
        if len(parts) != n_out or any(p is None or len(p[0]) != 1 for p in parts):
            return None
        return [(0.0, [p[0][0] for p in parts]), (1.0, [p[1][0] for p in parts])]
    if not _is_dict(fn):
        return None
    kind = int(fn.get("/FunctionType", -1))
    if kind == 2:
        ends = linear(fn)
        if ends is None or len(ends[0]) != n_out:
            return None
        return [(0.0, ends[0]), (1.0, ends[1])]
    if kind != 3:
        return None
    subs = fn.get("/Functions")
    d0, d1 = floats(fn.get("/Domain"), [0, 1])[:2]
    bounds = floats(fn.get("/Bounds"), [])
    encode = floats(fn.get("/Encode"), [])
    if subs is None or len(bounds) != len(subs) - 1 or len(encode) != 2 * len(subs) or d1 <= d0:
        return None
    edges = [d0] + bounds + [d1]
    stops: list[tuple[float, list[float]]] = []
    for i, f in enumerate(subs):
        ends = linear(f)
        if ends is None or len(ends[0]) != n_out or edges[i + 1] < edges[i]:
            return None
        for edge, e in ((edges[i], encode[2 * i]), (edges[i + 1], encode[2 * i + 1])):
            e = min(max(e, 0.0), 1.0)
            stops.append(((edge - d0) / (d1 - d0), [a + e * (b - a) for a, b in zip(*ends)]))
    return stops


def read_gradient(shading, matrix: tuple[float, ...]) -> tuple[Optional[RawGradient], str]:
    """PDF 的漸層字典 → RawGradient（座標用 matrix 換到頁面空間）。回傳（漸層、讀不出來的原因）。
    讀得出來的條件：軸向或放射（同心圓）漸層、顏色直線變化、色彩空間可以精確換算。"""
    if not _is_dict(shading):
        return None, "找不到漸層的定義"
    try:
        kind = int(shading.get("/ShadingType", 0))
        if kind not in (2, 3):
            return None, "不是線性或放射漸層（網格或函式漸層）"
        if shading.get("/BBox") is not None:
            return None, "漸層有自己的範圍框"
        reader = _color_reader(shading.get("/ColorSpace"))
        if reader is None:
            return None, "漸層的色彩空間無法精確換算成 RGB"
        n_out, to_rgb = reader
        raw_stops = _function_stops(shading.get("/Function"), n_out)
        if raw_stops is None:
            return None, "漸層的顏色不是直線變化"
        coords = [float(v) for v in shading.get("/Coords")]
        domain = [float(v) for v in shading.get("/Domain")] if shading.get("/Domain") is not None else [0.0, 1.0]
        if domain != [0.0, 1.0]:
            return None, "漸層的定義域不是 0 到 1"
        ext = shading.get("/Extend")
        extend = (bool(ext[0]), bool(ext[1])) if ext is not None and len(ext) == 2 else (False, False)
    except Exception:
        return None, "漸層的定義讀不懂"
    stops = [(min(max(t, 0.0), 1.0), to_rgb(c)) for t, c in raw_stops]
    a, b, c, d = matrix[0], matrix[1], matrix[2], matrix[3]
    det = a * d - b * c
    if abs(det) < 1e-12:
        return None, "漸層的座標轉換退化"
    if kind == 2:
        if len(coords) != 4:
            return None, "漸層的定義讀不懂"
        x0, y0, x1, y1 = coords
        dx, dy = x1 - x0, y1 - y0
        n2 = dx * dx + dy * dy
        if n2 < 1e-18:
            return None, "漸層的起點與終點重疊"
        # 線性漸層在仿射轉換後仍是線性漸層，但軸要重算：等色線轉過去之後不一定與「轉過去的軸」垂直。
        # 位置 t 是座標的一次函數，梯度 g = A⁻¹·(d/|d|²)；新軸 = 起點 + g/|g|²。
        ux, uy = dx / n2, dy / n2
        gx, gy = (d * ux - b * uy) / det, (-c * ux + a * uy) / det
        g2 = gx * gx + gy * gy
        px, py = affine_apply(matrix, x0, y0)
        return RawGradient(kind="linear", coords=(px, py, px + gx / g2, py + gy / g2), stops=stops, extend=extend), ""
    if len(coords) != 6:
        return None, "漸層的定義讀不懂"
    x0, y0, r0, x1, y1, r1 = coords
    if math.hypot(x1 - x0, y1 - y0) > 1e-6 * max(abs(r0), abs(r1), 1.0):
        return None, "放射漸層的兩個圓不同心"
    sx, sy = math.hypot(a, b), math.hypot(c, d)
    if abs(sx - sy) > 1e-4 * max(sx, sy) or abs(a * c + b * d) > 1e-4 * sx * sy:
        return None, "放射漸層被非等比縮放（變成橢圓）"
    if r0 > r1:  # 由外往內：反過來寫成由內往外
        r0, r1 = r1, r0
        stops = [(1.0 - t, col) for t, col in reversed(stops)]
        extend = (extend[1], extend[0])
    if r1 <= 0:
        return None, "放射漸層的半徑是零"
    if r0 > 0:  # 內圓半徑不是零：把位置換算成「離圓心多遠 ÷ 外圓半徑」
        if not extend[0]:
            return None, "放射漸層的內圓裡面不畫"
        stops = [((r0 + t * (r1 - r0)) / r1, col) for t, col in stops]
    cx, cy = affine_apply(matrix, x0, y0)
    return RawGradient(kind="radial", coords=(cx, cy, r1 * sx), stops=stops, extend=extend), ""
