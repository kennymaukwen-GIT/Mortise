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
    x_error: float = 0.0  # x0、x1 的誤差範圍（像素，約一個標準差）


@dataclass
class OcrLine:
    quad: np.ndarray  # 形狀 (4, 2)：左上、右上、右下、左下（以文字自身方向為準，可旋轉）
    text: str
    score: float  # 整行的信心值
    chars: list[OcrChar] = field(default_factory=list)  # 與 text 的字元一一對應（空白也算一個字）
    vertical: bool = False  # 直排
    quad_error: float = 0.0  # 四角位置的誤差範圍（像素，約一個標準差）
    angle_error_deg: float = 0.0  # 行角度的誤差範圍（度）
    notes: list[str] = field(default_factory=list)  # 量測旗標，例如「低解析度」「旋轉 90 度的橫排文字」


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
    # ---- 以下為 text_attributes.py 追加的欄位（皆有預設值）
    slant_deg: Optional[Estimate] = None  # 傾斜角（度，向右傾為正）原始量測
    stroke_width_px: Optional[Estimate] = None  # 筆畫粗細（像素）原始量測
    x_scale: Optional[Estimate] = None  # 水平縮放倍率原始量測（偵測到水平縮放時才有）
    size_evidence: dict[str, Estimate] = field(default_factory=dict)  # 各項字級證據（像素）：height、width、pitch 等


@dataclass
class LineAttributes:
    quad: np.ndarray  # 精修後的四角 (4, 2)：上下緣取字型的上緣與下緣，左右取墨跡範圍
    baseline: np.ndarray  # 形狀 (2, 2)：基線的起點與終點
    runs: list[RunAttributes]
    char_x: list[float]  # 每個非空白字的起點，沿基線方向、以基線起點為 0（像素）
    mask: np.ndarray  # 筆畫遮罩（bool），在「攤平的行」座標系中：x 沿基線、y 垂直基線
    mask_to_image: np.ndarray  # 3×3 矩陣：攤平座標 → 工作影像座標
    notes: list[str] = field(default_factory=list)
    # ---- 以下為 text_attributes.py 追加的欄位（皆有預設值）
    baseline_error_px: float = 0.0  # 基線位置的誤差（像素，約一個標準差）
    polarity: str = ""  # "dark_on_light"（深字淺底）或 "light_on_dark"（淺字深底）
    blur_px: float = 0.0  # 估計的模糊程度（高斯 σ，工作影像像素）


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
    # ---- 以下為 regions.py 追加（皆有預設值，不影響既有呼叫） ----
    confidence: float = 0.0  # 幾何類型判定的信心值（0 到 1）：依殘差與「最佳對次佳模型」的差距
    candidates: list[tuple[str, float]] = field(default_factory=list)  # 各幾何模型的平均擬合誤差（像素），由小到大；供⑤改判
    estimates: dict[str, Estimate] = field(default_factory=dict)  # 原始量測值與誤差：width_px、height_px、angle_deg、corner_radius_px、stroke_width_px、fill_mad 等


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
    # ---- 以下為 regions.py 追加（皆有預設值，不影響既有呼叫） ----
    kind_candidates: list[tuple[str, float]] = field(default_factory=list)  # 類型候選與分數，由高到低（不定案，交⑤）
    features: dict[str, float] = field(default_factory=dict)  # 分類用的特徵原始值（色彩數、平坦比、高頻比…）
    outline: Optional[ShapeFit] = None  # 照片與插畫的外形擬合（rect、rounded_rect、ellipse）；擬合不好則為 None
    occluded_mask: Optional[np.ndarray] = None  # bool，形狀同 mask：被文字或其他區域蓋住、由補全推得的部分


# ---------------------------------------------------------------- 背景（background.py）


@dataclass
class BackgroundResult:
    image: np.ndarray  # RGB，與工作影像同尺寸：擦掉文字、圖片、形狀之後的底圖
    model: str  # "flat"（純色）、"gradient"（漸層擬合）、"inpaint"（修補）、"mixed"
    flat_color: Optional[RGB]  # 純色底才有
    generated_boxes: list[tuple[int, int, int, int]]  # 哪些區域是生成（修補）出來的；純色與漸層擬合不算生成
    is_blank: bool  # 底圖是否只是單一顏色（是的話輸出時不需要底圖）
    notes: list[str] = field(default_factory=list)
    confidence: float = 1.0  # 整體品質估計（0 到 1）：擬合殘差、修補面積、遮罩寬度、周圍複雜度
    generated_mask: Optional[np.ndarray] = None  # bool，與工作影像同尺寸：逐像素標示「修補生成」的像素
    generated_fraction: float = 0.0  # 生成像素佔全頁的比例（還原度報告用）
    fit_residual: Optional[float] = None  # 未遮住的背景像素對所選模型的殘差（ΔE，第 90 百分位）
    gradient: Optional[dict] = None  # 漸層參數：{"degree", "coeffs"（3×k，RGB 各一列）, "basis"}；model="gradient" 才有
    regions: list[dict] = field(default_factory=list)  # 分區處理的逐區紀錄：{"bbox", "method", "residual", "confidence"}


# ---------------------------------------------------------------- 頁面幾何（page_geometry.py、page_size.py）


@dataclass
class PageQuad:
    corners: np.ndarray  # (4, 2)：頁面四角在原圖中的位置：左上、右上、右下、左下
    confidence: float
    method: str  # "edge_fit"、"contour"、"full_image"（找不到邊，以整張圖為頁面）
    aspect_ratio: float  # 攤平後的高 ÷ 寬（已由透視關係估計）
    notes: list[str] = field(default_factory=list)
    corner_error_px: Optional[np.ndarray] = None  # (4,)：每個角的位置誤差（像素，約一個標準差）
    aspect: Optional[Estimate] = None  # 長寬比（高 ÷ 寬）的量測值、誤差範圍、信心值
    aspect_method: str = ""  # "perspective"（由四角反推焦距）、"known_focal"（用已知焦距）、"side_ratio"（近乎正面，退回邊長比）、"focal_prior"（單向透視，用焦距先驗範圍）、"image"（整張圖）
    focal_px: Optional[float] = None  # 由四角反推的焦距（像素）；解不穩定時留空
    edge_support: Optional[list[float]] = None  # 上、右、下、左四邊有邊緣支持的長度比例（0 到 1）


@dataclass
class SizeCandidate:
    name: str  # 例如 "A4 直"
    width_pt: float
    height_pt: float
    confidence: float
    reason: str  # 白話說明依據
    aspect_score: float = 1.0  # 只看長寬比的吻合度（0 到 1；以長寬比誤差範圍為尺度）
    family: str = ""  # 規格家族，例如 "ISO A 直"（長寬比相同的尺寸歸同一家族）
    scale_evidence: bool = False  # 是否有絕對尺度線索（dpi、字級）參與排序


# ---------------------------------------------------------------- 描圖向量化（vectorize.py）


@dataclass
class TracedGradient:
    """漸層填色（像素座標）。色標之間在 sRGB 數值上線性內插，與 SVG、PDF 的預設行為一致。"""

    kind: str  # "linear"、"radial"
    coords: list[float]  # linear：[x0, y0, x1, y1]；radial：[cx, cy, r]
    stops: list[tuple[float, RGB]]  # （位置 0 到 1、顏色），依位置由小到大


@dataclass
class TracedPrimitive:
    """這條路徑其實是哪一種基本圖形（像素座標）。欄位意義與 artifacts.VectorPrimitive 相同。"""

    kind: str  # "rect"、"rounded_rect"、"ellipse"、"circle"、"line"、"polygon"
    cx: float
    cy: float
    w: float  # 沿自身方向的寬（line 為長度）
    h: float  # 沿自身方向的高（line 為 0）
    angle_deg: float = 0.0  # 順時針為正
    corner_radius: float = 0.0  # rounded_rect 才有
    points: list[list[float]] = field(default_factory=list)  # polygon 的頂點


@dataclass
class TracedPath:
    """描圖得到的一條路徑（像素座標；像素 i 佔 [i, i+1)，中心在 i+0.5）。"""

    path: list[list[float]]  # 與 ShapeElement.path 同一種編碼：[種類, x, y, ...]；0 起筆、1 直線、2 三次貝茲（後接 3 個點）、3 閉合
    fill: Optional[RGB] = None
    fill_rule: str = "nonzero"  # "nonzero"、"evenodd"
    gradient: Optional[TracedGradient] = None  # 有漸層時 fill 是漸層的平均色
    stroke: Optional[RGB] = None
    stroke_width_px: Optional[float] = None
    stroke_cap: str = "butt"
    stroke_join: str = "miter"
    primitive: Optional[TracedPrimitive] = None
    fit_error_px: Optional[float] = None  # 路徑與次像素邊界的平均距離（只算看得到的邊界）
    completed: bool = False  # 有一部分被上層遮住、由補全推得（不是量到的）
    notes: list[str] = field(default_factory=list)


@dataclass
class VectorResult:
    """vectorize() 的結果。paths 由下到上；所有數值都是演算法量出來的。"""

    paths: list[TracedPath]
    size: tuple[int, int]  # （寬、高），像素
    background: Optional[RGB]  # 被當成底色（透明）的顏色；沒有就是 None
    render_error: float  # 重新畫出來與原圖的平均色差（CIE76 ΔE），只算遮罩內
    error_p95: float  # 色差的第 95 百分位
    iou: float  # 重畫的覆蓋範圍與原圖前景的交集 ÷ 聯集
    node_count: int  # 所有路徑的節點數（線段與曲線段的數目）
    accepted: bool  # 是否達到可以當向量圖輸出的品質
    confidence: float  # 0 到 1
    notes: list[str] = field(default_factory=list)
    stats: dict = field(default_factory=dict)  # 過程中的量測：雜訊、區域數、各步驟耗時、驗證輪數等
