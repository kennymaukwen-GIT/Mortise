"""影像辨識各模組之間交接的型別。座標一律是工作影像的像素（浮點數）。"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

import numpy as np

RGB = tuple[int, int, int]


@dataclass
class Estimate:
    """量測值：值、誤差範圍（與值同單位，約一個標準差）、信心值（0 到 1）。"""

    value: float
    error: float
    confidence: float


# ---------------------------------------------------------------- 文字辨識（ocr.py）


@dataclass
class CharCandidate:
    text: str
    prob: float


@dataclass
class OcrChar:
    text: str
    score: float  # 0 到 1
    x0: float  # 這個字沿文字行方向的起點（像素，以行四角的左上角為 0）
    x1: float  # 終點
    candidates: list[CharCandidate] = field(default_factory=list)  # 含第一名，機率由高到低


@dataclass
class OcrLine:
    quad: np.ndarray  # 形狀 (4, 2)：左上、右上、右下、左下（以文字自身方向為準，可旋轉）
    text: str
    score: float  # 整行的信心值
    chars: list[OcrChar] = field(default_factory=list)  # 與 text 的字元一一對應（空白也算一個字）
    vertical: bool = False  # 直排


# ---------------------------------------------------------------- 文字屬性（text_attributes.py）


@dataclass
class FontMatch:
    family: str  # 字族名，例如 "Liberation Sans"
    style: str  # "Regular"、"Bold"、"Italic"、"Bold Italic"
    score: float  # 0 到 1；候選清單依分數由高到低
    path: Optional[str] = None  # 字型檔路徑


@dataclass
class RunAttributes:
    """一行之內，顏色、字級、字型都相同的一段字。"""

    start: int  # 涵蓋行內第幾個字（含）
    end: int  # 到第幾個字（不含）
    color: RGB  # 筆畫核心的顏色（遮罩內縮後取中位數，避開邊緣反鋸齒混色）
    color_confidence: float
    background: RGB  # 鄰近背景色
    font_size_px: Estimate  # 字級（em 大小）的原始量測值，單位像素；不吸附
    fonts: list[FontMatch]  # 字型候選清單
    bold: tuple[bool, float]  # （是否粗體、信心值）
    italic: tuple[bool, float]
    underline: bool = False
    strikethrough: bool = False
    char_spacing_px: Optional[Estimate] = None  # 額外字距；量不出來就留空
    notes: list[str] = field(default_factory=list)  # 例如「有外框特效，字級量測不可靠」


@dataclass
class LineAttributes:
    quad: np.ndarray  # 精修後的四角 (4, 2)：上下緣取字型的上緣與下緣，左右取墨跡範圍
    baseline: np.ndarray  # 形狀 (2, 2)：基線的起點與終點
    runs: list[RunAttributes]
    char_x: list[float]  # 每個非空白字的起點，沿基線方向、以基線起點為 0（像素）
    mask: np.ndarray  # 筆畫遮罩（bool），在「攤平的行」座標系中：x 沿基線、y 垂直基線
    mask_to_image: np.ndarray  # 3×3 矩陣：攤平座標 → 工作影像座標
    notes: list[str] = field(default_factory=list)


# ---------------------------------------------------------------- 區域（regions.py）


@dataclass
class ShapeFit:
    geometry: str  # "rect"、"rounded_rect"、"ellipse"、"line"、"polygon"
    quad: np.ndarray  # (4, 2) 外框四角（可旋轉）；line 為兩端點重複成四角
    fill: Optional[RGB] = None
    stroke: Optional[RGB] = None
    stroke_width_px: Optional[float] = None
    corner_radius_px: Optional[float] = None  # rounded_rect 才有
    polygon: Optional[np.ndarray] = None  # (N, 2)，polygon 才有
    fit_error_px: float = 0.0  # 擬合後輪廓與實際輪廓的平均距離


@dataclass
class TableGrid:
    h_lines: list[float]  # 水平格線的 y（像素）
    v_lines: list[float]  # 垂直格線的 x（像素）
    has_ruling: bool  # 是否有畫出來的框線（False 表示由文字對齊推出的無框線表格）


@dataclass
class Region:
    """文字以外的一塊區域。遮罩可以互相重疊（不是互斥的方框）。"""

    kind: str  # "photo"、"illustration"、"shape"、"table"
    bbox: tuple[int, int, int, int]  # x0, y0, x1, y1（像素，右下不含）
    mask: np.ndarray  # bool，形狀與 bbox 相同（高 = y1 - y0，寬 = x1 - x0）
    confidence: float
    z: int = 0  # 圖層初判：數字大的在上面
    shape: Optional[ShapeFit] = None  # kind == "shape" 才有
    table: Optional[TableGrid] = None  # kind == "table" 才有
    notes: list[str] = field(default_factory=list)


# ---------------------------------------------------------------- 背景（background.py）


@dataclass
class BackgroundResult:
    image: np.ndarray  # RGB，與工作影像同尺寸：擦掉文字、圖片、形狀之後的底圖
    model: str  # "flat"（純色）、"gradient"（漸層擬合）、"inpaint"（修補）、"mixed"
    flat_color: Optional[RGB]  # 純色底才有
    generated_boxes: list[tuple[int, int, int, int]]  # 哪些區域是生成（修補）出來的；純色與漸層擬合不算生成
    is_blank: bool  # 底圖是否只是單一顏色（是的話輸出時不需要底圖）
    notes: list[str] = field(default_factory=list)


# ---------------------------------------------------------------- 頁面幾何（page_geometry.py、page_size.py）


@dataclass
class PageQuad:
    corners: np.ndarray  # (4, 2)：頁面四角在原圖中的位置：左上、右上、右下、左下
    confidence: float
    method: str  # "edge_fit"、"contour"、"full_image"（找不到邊，以整張圖為頁面）
    aspect_ratio: float  # 攤平後的高 ÷ 寬（已由透視關係估計）
    notes: list[str] = field(default_factory=list)


@dataclass
class SizeCandidate:
    name: str  # 例如 "A4 直"
    width_pt: float
    height_pt: float
    confidence: float
    reason: str  # 白話說明依據
