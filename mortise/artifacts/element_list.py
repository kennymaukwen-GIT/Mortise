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
    stroke_ratio: Optional[float] = None  # 筆畫粗細 ÷ 字級（影像產線才有）：⑤用它與整頁比較來判斷粗體，不受模糊影響


class TextLine(Model):
    id: str
    quad: Quad
    z: int  # 圖層順序：數字大的在上面
    baseline: tuple[Point, Point]  # 基線的起點與終點
    runs: list[TextRun]
    direction: WritingDirection = WritingDirection.HORIZONTAL
    hidden: bool = False  # 不可見文字（多半是 OCR 文字層）
    text_confidence: Optional[float] = None  # 文字內容的信心值（影像產線才有；擷取產線的文字是精確的）
    char_candidates: list[list[tuple[str, float]]] = []  # 每個字的候選字與機率（含第一名），供大型 AI 或使用者在候選中挑選
    ai_chars: list[int] = []  # 哪些字（在 text 中的位置）是大型 AI 在候選中挑的
    notes: list[str] = []

    @property
    def text(self) -> str:
        return "".join(r.text for r in self.runs)

    @property
    def font_size(self) -> float:
        """行的代表字級（相對單位）：各段字級依字數與信心值加權的中位數（量測值有雜訊時比「最多字的段」穩）。"""
        items = sorted((r.font_size.value, max(len(r.text.strip()), 1) * max(r.font_size.confidence, 0.05)) for r in self.runs)
        if not items:
            return 0.0
        total = sum(w for _, w in items)
        acc = 0.0
        for v, w in items:
            acc += w
            if acc >= total / 2:
                return v
        return items[-1][0]

    @property
    def font_size_rel_error(self) -> float:
        """代表字級的相對誤差（精確擷取為 0）。"""
        errs = [(r.font_size.error or 0.0) / r.font_size.value for r in self.runs if r.font_size.value > 0]
        return min(errs) if errs else 0.0


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


class GradientStop(Model):
    offset: float  # 0 到 1
    color: Color


class Gradient(Model):
    """漸層填色。座標與所屬路徑用同一個座標系、同一種單位。"""

    kind: str  # "linear"、"radial"
    coords: list[float]  # linear：[x0, y0, x1, y1]；radial：[cx, cy, r]
    stops: list[GradientStop]


class VectorPrimitive(Model):
    """這條路徑其實是哪一種基本圖形。有填的話，輸出 SVG 時寫成 <rect>、<circle>、<ellipse>、<line>、<polygon>，比路徑好編輯。
    座標與所屬路徑用同一個座標系、同一種單位；路徑本身（path）仍要完整保留，兩者描述同一個形狀。"""

    kind: str  # "rect"、"rounded_rect"、"ellipse"、"circle"、"line"、"polygon"
    cx: float  # 中心
    cy: float
    w: float  # 沿自身方向的寬（line 為長度）
    h: float  # 沿自身方向的高（line 為 0）
    angle_deg: float = 0.0  # 順時針為正
    corner_radius: float = 0.0  # rounded_rect 才有
    points: list[list[float]] = []  # polygon 的頂點 [[x, y], ...]


class VectorPath(Model):
    """向量圖裡的一條路徑。"""

    path: list[list[float]]  # 與 ShapeElement.path 同一種編碼：每段 [種類, x, y, ...]；種類 0 起筆、1 直線、2 三次貝茲（後接 3 個點共 6 個數）、3 閉合。頁面相對座標
    fill: Optional[Color] = None
    fill_rule: str = "nonzero"  # "nonzero"、"evenodd"
    gradient: Optional[Gradient] = None  # 有漸層時 fill 填漸層的平均色（給不支援漸層的輸出器用）
    stroke: Optional[Color] = None
    stroke_width: Optional[float] = None  # 相對單位
    stroke_cap: str = "butt"  # butt、round、square
    stroke_join: str = "miter"  # miter、round、bevel
    primitive: Optional[VectorPrimitive] = None
    fit_error: Optional[float] = None  # 描圖才有：路徑與原始像素邊界的平均距離（相對單位）；精確擷取的留空


class VectorGraphic(Model):
    """一組屬於同一個圖形的向量路徑（標誌、圖示、插畫、圖表）。

    來源有兩種（寫在 origin）：
      "pdf_paths"：原生 PDF 裡本來就是向量，直接讀出來（來源類型 exact）。
      "traced"：已經被壓成像素（扁平化），由描圖重建（來源類型 measured）。
    每個向量圖都有一張對應的點陣圖，放在同一份 ElementList 的 images 裡（image_id 指向它，quad 與 z 與它相同）：
      ④結構只看那張圖片（閱讀順序、圖層），不必認識向量圖；
      不支援向量的輸出器照常輸出那張圖片；支援的輸出器改畫向量、跳過那張圖片。
    """

    id: str
    image_id: str  # 對應的 ImageElement 編號
    quad: Quad  # 與對應的圖片相同
    z: int
    paths: list[VectorPath]  # 由下到上
    origin: Measured[str]  # 值為 "pdf_paths" 或 "traced"
    kind: str = "unknown"  # "logo"、"icon"、"chart"、"illustration"、"unknown"（只是標籤，不影響輸出）
    render_error: Optional[float] = None  # 描圖才有：把向量重新畫出來與原始像素比較的平均色差（ΔE）；精確擷取的留空
    notes: list[str] = []


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
    vectors: list[VectorGraphic] = []  # 向量圖（每個都對應 images 裡的一張圖片）
