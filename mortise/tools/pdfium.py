"""PDF 讀取層：把 PDFium 的原始介面包成本產品需要的資料。

授權：PDFium 為 BSD-3-Clause 加 Apache-2.0（見 docs/architecture/research/pdf.md）。
PDFium 不是執行緒安全，所有呼叫都經過同一把鎖。
座標一律是 PDF 使用者空間（pt，原點左下，y 向上）；轉成頁面相對座標是②③的工作。
"""

from __future__ import annotations

import ctypes
import math
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


@dataclass
class RawPath:
    z: int
    points: list[tuple[int, float, float, bool]]  # （線段種類、x、y、是否閉合）
    fill: Optional[tuple[int, int, int, int]]
    stroke: Optional[tuple[int, int, int, int]]
    stroke_width: float
    nested: bool
    _obj: object = field(repr=False, default=None)

    @property
    def bounds(self) -> tuple[float, float, float, float]:
        xs = [p[1] for p in self.points]
        ys = [p[2] for p in self.points]
        return min(xs), min(ys), max(xs), max(ys)


def _rgba(fn, handle, *extra) -> Optional[tuple[int, int, int, int]]:
    r, g, b, a = (ctypes.c_uint() for _ in range(4))
    ok = fn(handle, *extra, ctypes.byref(r), ctypes.byref(g), ctypes.byref(b), ctypes.byref(a))
    return (r.value, g.value, b.value, a.value) if ok else None


def _key(ptr) -> int:
    return ctypes.cast(ptr, ctypes.c_void_p).value or 0


def open_document(path: str, password: Optional[str] = None) -> pdfium.PdfDocument:
    try:
        with LOCK:
            return pdfium.PdfDocument(path, password=password)
    except pdfium.PdfiumError as e:  # 密碼錯誤、檔案損壞
        raise PdfOpenError(str(e)) from e


class PageReader:
    """讀一頁 PDF。用完請呼叫 close()。"""

    def __init__(self, doc: pdfium.PdfDocument, page_index: int):
        self.doc = doc
        with LOCK:
            self.page = doc[page_index]
            self._textpage = None
            self._objects: Optional[list] = None

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
                if raw.FPDFText_GetMatrix(tp, i, ctypes.byref(m)):
                    vscale = math.hypot(m.c, m.d) or 1.0
                    hscale = (math.hypot(m.a, m.b) or 1.0) / vscale
                size = raw_size * vscale
                out.append(
                    RawChar(
                        index=i, text=text, origin=(ox.value, oy.value),
                        loose_box=(rect.left, rect.bottom, rect.right, rect.top),
                        font_base=base, font_family=family, font_weight=weight, font_italic_angle=italic_angle,
                        size=size, fill=_rgba(raw.FPDFText_GetFillColor, tp, i), stroke=_rgba(raw.FPDFText_GetStrokeColor, tp, i),
                        render_mode=mode, angle=float(raw.FPDFText_GetCharAngle(tp, i)), generated=generated,
                        map_error=bool(raw.FPDFText_HasUnicodeMapError(tp, i)), obj_key=_key(obj) if obj else 0,
                        ascent=(ascent * vscale) or size * 0.9, descent=(descent * vscale) or -size * 0.2, hscale=hscale,
                    )
                )
            return out

    # ---------- 物件 ----------
    def _walk(self) -> list:
        """依繪製順序列出所有物件（含表單內的），附上累積的變換矩陣。"""
        if self._objects is None:
            objs = []
            for z, obj in enumerate(self.page.get_objects(max_depth=8)):
                ctm = IDENTITY
                container = obj.container
                while container is not None:
                    ctm = affine_mul(ctm, tuple(container.get_matrix().get()))
                    container = container.container
                objs.append((z, obj, ctm))
            self._objects = objs
        return self._objects

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

    def paths(self) -> list[RawPath]:
        with LOCK:
            out = []
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
                out.append(
                    RawPath(
                        z=z, points=pts,
                        fill=_rgba(raw.FPDFPageObj_GetFillColor, obj.raw) if fill_mode.value else None,
                        stroke=_rgba(raw.FPDFPageObj_GetStrokeColor, obj.raw) if has_stroke.value else None,
                        stroke_width=width.value * scale, nested=obj.level > 0, _obj=obj,
                    )
                )
            return out

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


def document_metadata(doc: pdfium.PdfDocument) -> dict[str, str]:
    with LOCK:
        try:
            meta = doc.get_metadata_dict(skip_empty=True)
        except TypeError:
            meta = {k: v for k, v in doc.get_metadata_dict().items() if v}
        return {str(k): str(v) for k, v in meta.items()}
