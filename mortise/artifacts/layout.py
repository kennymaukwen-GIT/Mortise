"""版面 Layout（④結構 交出）。理解出的結構：段落、文字框、閱讀順序、角色、圖層、組裝編號。"""

from __future__ import annotations

from enum import Enum
from typing import Optional

from .common import SCHEMA_VERSION, Align, Measured, Model, Quad, Role


class AssemblyType(str, Enum):
    """空間組裝法：有類型的組裝編號。只有類型與編號都相符的空間才能組在一起。"""

    CONTAIN = "contain"  # 包含（A 在 B 裡面）
    TEXT_FLOW = "text_flow"  # 文字流（同一段文字接續）
    CAPTION_PAIR = "caption_pair"  # 圖說配對
    GRID = "grid"  # 網格（同列或同欄）
    SAME_STYLE = "same_style"  # 同樣式


class AssemblyLink(Model):
    type: AssemblyType
    key: str  # 組裝編號
    members: list[str]  # 元素編號（有順序）
    confidence: float = 1.0
    writer: str = ""


class Paragraph(Model):
    id: str
    line_ids: list[str]  # 依閱讀順序
    quad: Quad
    alignment: Measured[Align]
    role: Measured[Role]
    line_pitch: Optional[float] = None  # 基線到基線的距離（相對單位）；只有一行就留空
    first_line_indent: float = 0.0  # 相對單位
    list_marker: Optional[str] = None  # 項目符號或編號的文字；不是清單就留空
    list_level: int = 0
    joiners: list[str] = []  # 行與行接起來時要補的字元（"" 或 " "），長度為行數減 1


class TextBox(Model):
    id: str
    quad: Quad
    z: int
    paragraph_ids: list[str]
    space_between: list[float] = []  # 相鄰段落之間多出來的距離（相對單位），長度為段落數減 1


class TableCell(Model):
    row: int
    col: int
    row_span: int = 1
    col_span: int = 1
    quad: Quad
    paragraph_ids: list[str] = []  # 儲存格內的段落（依閱讀順序）
    header: bool = False


class TableStructure(Model):
    """④理解出的表格：列、欄、合併儲存格、表頭、儲存格內文字的歸屬。"""

    id: str
    region_id: str  # 來自③的表格區編號
    quad: Quad
    z: int
    row_lines: list[float]  # 列的邊界 y（列數 + 1 條）
    col_lines: list[float]  # 欄的邊界 x（欄數 + 1 條）
    cells: list[TableCell]
    header_rows: int = 0
    ruled: bool = True
    confidence: float = 1.0


class PageLayout(Model):
    page_index: int
    paragraphs: list[Paragraph] = []
    text_boxes: list[TextBox] = []
    reading_order: list[str] = []  # 文字框、圖片、形狀的編號
    z_order: list[str] = []  # 由下到上
    links: list[AssemblyLink] = []
    guides_x: list[float] = []  # 對齊線
    guides_y: list[float] = []
    notes: list[str] = []
    tables: list[TableStructure] = []


class MasterCandidate(Model):
    """跨頁重複出現在相同位置的元素：母片候選。"""

    key: str
    element_ids: list[str]  # 每頁對應的元素編號
    page_indexes: list[int]


class Layout(Model):
    schema_version: str = SCHEMA_VERSION
    pages: list[PageLayout]
    master_candidates: list[MasterCandidate] = []
