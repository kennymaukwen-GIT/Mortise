"""元素清單 ElementList（③辨識 交出）。只記「看得到的事實」，不吸附、不定案。"""

from __future__ import annotations

from enum import Enum
from typing import Optional

from .common import SCHEMA_VERSION, BlobRef, Color, Measured, Model, Point, Quad, WritingDirection


class ProductionLine(str, Enum):
    EXTRACT = "extract"  # 擷取產線：直接讀原生 PDF
    IMAGE = "image"  # 影像產線：掃描、截圖、照片


class FontCandidate(Model):
    name: str  # 清理後的字型名稱（去掉子集前綴）
    raw_name: str = ""  # 檔案中的原始名稱
    score: float = 1.0  # 0 到 1


class TextRun(Model):
    """一行之內，字型、字級、顏色都相同的一段字。"""

    text: str
    quad: Quad
    font_size: Measured[float]  # 字級 ÷ 頁寬（相對單位）；乘上比例尺才是 pt
    font_candidates: list[FontCandidate] = []
    bold: Measured[bool]
    italic: Measured[bool]
    color: Measured[Color]
    char_spacing: Optional[float] = None  # 額外字距（相對單位）；量不出來就留空
    char_x: list[float] = []  # 每個字起點的 x（相對單位，沿基線方向），供④⑤判斷對齊與字距


class TextLine(Model):
    id: str
    quad: Quad
    z: int  # 圖層順序：數字大的在上面
    baseline: tuple[Point, Point]  # 基線的起點與終點
    runs: list[TextRun]
    direction: WritingDirection = WritingDirection.HORIZONTAL
    hidden: bool = False  # 不可見文字（多半是 OCR 文字層）

    @property
    def text(self) -> str:
        return "".join(r.text for r in self.runs)

    @property
    def font_size(self) -> float:
        """行內佔最多字的字級（相對單位）。"""
        best: dict[float, int] = {}
        for r in self.runs:
            best[r.font_size.value] = best.get(r.font_size.value, 0) + len(r.text)
        return max(best, key=lambda k: best[k]) if best else 0.0


class ImageKind(str, Enum):
    PHOTO = "photo"
    ILLUSTRATION = "illustration"
    SHADOW = "shadow"  # 軟體把陰影或特效壓成的點陣圖
    FULL_PAGE = "full_page"  # 佔滿整頁（掃描頁或背景）
    UNKNOWN = "unknown"


class ImageElement(Model):
    id: str
    quad: Quad
    z: int
    image: BlobRef
    pixel_size: tuple[int, int]
    kind: Measured[ImageKind]


class ShapeGeometry(str, Enum):
    RECT = "rect"
    ROUNDED_RECT = "rounded_rect"
    LINE = "line"
    ELLIPSE = "ellipse"
    PATH = "path"


class ShapeElement(Model):
    id: str
    quad: Quad
    z: int
    geometry: ShapeGeometry
    fill: Optional[Color] = None
    stroke: Optional[Color] = None
    stroke_width: Optional[float] = None  # 相對單位
    is_page_background: bool = False  # 佔滿整頁的底色矩形
    corner_radius: Optional[float] = None  # 圓角半徑（相對單位），rounded_rect 才有
    path: list[list[float]] = []  # path 才有：每段 [種類, x, y, ...]（頁面相對座標）；種類 0 起筆、1 直線、2 三次貝茲（後接 3 個點）、3 閉合
    fit_error: Optional[float] = None  # 認成基本幾何時的擬合誤差（相對單位）


class TableRegion(Model):
    """表格區：③只找區域與格線，列欄結構在④。"""

    id: str
    quad: Quad
    h_lines: list[float] = []  # 水平格線的 y
    v_lines: list[float] = []  # 垂直格線的 x
    ruled: bool = True  # 有畫出來的框線；False 表示由文字對齊推出的無框線表格
    segments: list[tuple[float, float, float, float]] = []  # 實際的格線線段（x0, y0, x1, y1），④用來判斷合併儲存格
    line_color: Optional[Color] = None
    line_width: Optional[float] = None  # 相對單位
    cell_fills: list[tuple[Quad, Color]] = []  # 儲存格底色（色塊與其顏色）
    confidence: float = 1.0
    z: int = 0  # 圖層順序（格線中最底層的那一條）


class Background(Model):
    """背景：拿掉已辨識元素後剩下的畫面。"""

    image: Optional[BlobRef] = None  # 沒有剩餘內容就留空
    color: Optional[Color] = None  # 純色底
    generated_regions: list[Quad] = []  # 哪些區域是生成（修補）出來的
    method: str = ""  # 例如 "pdf_hide_objects"（原生 PDF 直接隱藏物件後重繪，不屬於生成）


class ElementList(Model):
    schema_version: str = SCHEMA_VERSION
    page_index: int
    production_line: ProductionLine
    texts: list[TextLine] = []
    images: list[ImageElement] = []
    shapes: list[ShapeElement] = []
    table_regions: list[TableRegion] = []
    background: Background = Background()
    notes: list[str] = []
