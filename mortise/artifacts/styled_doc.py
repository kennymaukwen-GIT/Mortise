"""成品文件 StyledDoc（⑤設定 交出）。與輸出格式無關的最終設定，一律用實體單位（pt）。"""

from __future__ import annotations

from enum import Enum
from typing import Optional

from .common import SCHEMA_VERSION, Align, BlobRef, Color, Measured, Model, Role


class TargetMode(str, Enum):
    SAME_SOURCE = "same_source"  # 同源同格式：求原值
    CROSS_SOURCE = "cross_source"  # 異源或照片：求最簡潔且渲染一致的設定


class FontChoice(Model):
    """字型定案。「推論的字型名稱」與「驗證用的替代字型」是兩個欄位（風險 R2）。"""

    family: Measured[str]  # 推論的字型名稱：寫進輸出檔
    verification_family: str  # 伺服器上實際拿來渲染驗證的字型
    is_substituted: bool  # 驗證字型是否不同於推論字型
    east_asian: bool = False  # 這段字是否為中日韓文字


class StyledRun(Model):
    text: str
    font: FontChoice
    size_pt: Measured[float]
    bold: bool = False
    italic: bool = False
    color: Color


class LineSpacingMode(str, Enum):
    MULTIPLE = "multiple"  # 倍數（1.0、1.15、1.5…）
    EXACT = "exact"  # 固定值（pt）


class LineSpacing(Model):
    mode: LineSpacingMode
    value: float


class StyledParagraph(Model):
    id: str
    runs: list[StyledRun]
    align: Align = Align.LEFT
    line_spacing: Optional[Measured[LineSpacing]] = None  # 只有一行、量不出來就留空
    space_before_pt: float = 0.0
    space_after_pt: float = 0.0
    first_line_indent_pt: float = 0.0
    role: Role = Role.UNKNOWN
    style_id: Optional[str] = None
    source_line_count: int = 1  # 原圖中這段佔幾行（⑦比對斷行用）
    text_locked: bool = False  # 使用者改過這段的文字（⑦改以使用者的文字為準）


class Box(Model):
    """元素在頁面上的位置（pt，原點左上）。rotation 為順時針角度。"""

    x: float
    y: float
    w: float
    h: float
    rotation: float = 0.0


class StyledTextBox(Model):
    id: str
    box: Box
    z: int
    paragraphs: list[StyledParagraph]
    wrap: bool = True  # 是否自動換行（單行文字關掉，避免目標軟體字寬略大時被折行）


class StyledImage(Model):
    id: str
    box: Box
    z: int
    image: BlobRef


class StyledShape(Model):
    id: str
    box: Box
    z: int
    geometry: str  # rect、rounded_rect、line、ellipse、path
    fill: Optional[Color] = None
    stroke: Optional[Color] = None
    stroke_width_pt: Optional[float] = None
    corner_radius_pt: Optional[float] = None  # rounded_rect 才有
    path: list[list[float]] = []  # path 才有：每段 [種類, x, y, ...]，座標相對於 box 左上角（pt）；種類 0 起筆、1 直線、2 三次貝茲（後接 3 個點）、3 閉合


class Border(Model):
    color: Color
    width_pt: float


class StyledCell(Model):
    row: int
    col: int
    row_span: int = 1
    col_span: int = 1
    paragraphs: list[StyledParagraph] = []
    fill: Optional[Color] = None
    borders: list[Optional[Border]] = [None, None, None, None]  # 上、右、下、左
    v_align: str = "top"  # top、middle、bottom
    margins_pt: list[float] = [0.0, 0.0, 0.0, 0.0]  # 上、右、下、左：文字與儲存格邊緣的距離
    header: bool = False
    value: Optional[float] = None  # 數字儲存格的數值（⑤推論；給 Excel 用）
    number_format: Optional[str] = None  # Excel 數字格式，例如 "#,##0"、"0%"
    formula: Optional[str] = None  # 只有使用者確認過的公式才會填


class StyledTable(Model):
    id: str
    box: Box
    z: int
    col_widths_pt: list[float]
    row_heights_pt: list[float]
    cells: list[StyledCell]
    header_rows: int = 0


class StyledPage(Model):
    page_index: int
    width_pt: float
    height_pt: float
    background_color: Optional[Color] = None
    background_image: Optional[BlobRef] = None
    text_boxes: list[StyledTextBox] = []
    images: list[StyledImage] = []
    shapes: list[StyledShape] = []
    reading_order: list[str] = []
    tables: list[StyledTable] = []


class StyledMaster(Model):
    """母片：每一頁相同位置都出現的元素（標誌、頁尾文字），放到母片上只要改一次。"""

    id: str
    text_boxes: list[StyledTextBox] = []
    images: list[StyledImage] = []
    shapes: list[StyledShape] = []
    member_ids: list[str] = []  # 各頁中被母片取代的元素編號（輸出時這些不再逐頁畫）
    page_indexes: list[int] = []


class Style(Model):
    """樣式表的一個樣式：一群段落共用的設定。"""

    id: str
    name: str
    font_family: str
    size_pt: float
    bold: bool
    italic: bool
    color: Color
    role: Role = Role.UNKNOWN
    usage_count: int = 0


class StyledDoc(Model):
    schema_version: str = SCHEMA_VERSION
    target_mode: Measured[TargetMode]
    source_app: Measured[str]  # 來源軟體判斷，例如 "powerpoint"、"libreoffice_impress"、"unknown"
    pages: list[StyledPage]
    styles: list[Style] = []
    theme_colors: list[Color] = []
    notes: list[str] = []
    masters: list[StyledMaster] = []
