"""共同型別：座標、四角、帶信心值的欄位、元素編號、列舉。

座標約定：以頁寬為 1 的等向相對單位。原點在頁面左上角，x 向右、y 向下。
x 範圍 0 到 1，y 範圍 0 到「高 ÷ 寬」。以完整浮點精度儲存，不四捨五入。
實際尺寸只存在 PageSpec 的比例尺；改尺寸不必重跑辨識。
"""

from __future__ import annotations

import hashlib
import math
from enum import Enum
from typing import Generic, Optional, TypeVar

from pydantic import BaseModel, ConfigDict

SCHEMA_VERSION = "0.1"


class Model(BaseModel):
    """所有產物的基底：交出後不可改（frozen），不接受未定義的欄位。"""

    model_config = ConfigDict(frozen=True, extra="forbid")


# ---------- 列舉 ----------


class Provenance(str, Enum):
    """來源類型：這個值是怎麼來的。"""

    EXACT = "exact"  # 精確擷取（直接從檔案讀出）
    MEASURED = "measured"  # 量測（由演算法從影像或幾何量出）
    SNAPPED = "snapped"  # 吸附（量測值吸到整齊的設定值）
    INFERRED = "inferred"  # 推論（規則或先驗，沒有直接證據）
    AI = "ai"  # 大型 AI 的判斷
    USER = "user"  # 使用者輸入或修正
    GENERATED = "generated"  # 生成（例如背景修補）


class InputType(str, Enum):
    NATIVE_PDF = "native_pdf"
    SCANNED_PDF = "scanned_pdf"
    MIXED_PDF = "mixed_pdf"
    SCREENSHOT = "screenshot"
    PHOTO = "photo"


class OutputFormat(str, Enum):
    PPTX = "pptx"
    GOOGLE_SLIDES = "google_slides"
    DOCX_FIXED = "docx_fixed"
    DOCX_FLOW = "docx_flow"
    XLSX = "xlsx"
    AI_PDF = "ai_pdf"
    IDML = "idml"
    TEMPLATE = "template"


class UsageMode(str, Enum):
    RESCUE = "rescue"  # 檔案急救
    TEMPLATE = "template"  # 靈感模板


class WritingDirection(str, Enum):
    HORIZONTAL = "horizontal"
    VERTICAL = "vertical"  # 直排：欄位先預留，尚未實作


class Align(str, Enum):
    LEFT = "left"
    CENTER = "center"
    RIGHT = "right"
    JUSTIFY = "justify"


class Role(str, Enum):
    TITLE = "title"
    SUBTITLE = "subtitle"
    BODY = "body"
    CAPTION = "caption"
    HEADER = "header"
    FOOTER = "footer"
    PAGE_NUMBER = "page_number"
    FOOTNOTE = "footnote"
    UNKNOWN = "unknown"


# ---------- 帶信心值的欄位 ----------

T = TypeVar("T")


class Measured(Model, Generic[T]):
    """帶信心值的欄位：值、信心值、來源類型、寫入者。"""

    value: T
    confidence: float  # 0 到 1
    source: Provenance
    writer: str  # 步驟名稱與版本，例如 "s3.pdf_text@1"
    error: Optional[float] = None  # 誤差範圍（與 value 同單位），不知道就留空
    locked: bool = False  # 使用者修正：之後任何重跑都不得覆蓋


def exact(value: T, writer: str) -> Measured[T]:
    return Measured[type(value)](value=value, confidence=1.0, source=Provenance.EXACT, writer=writer)  # type: ignore[index]


def by_user(value: T, writer: str = "user") -> Measured[T]:
    return Measured[type(value)](  # type: ignore[index]
        value=value, confidence=1.0, source=Provenance.USER, writer=writer, locked=True
    )


# ---------- 幾何 ----------


class Point(Model):
    x: float
    y: float


class Quad(Model):
    """四角座標（可表示旋轉）。順序：左上、右上、右下、左下（以元素自身方向為準）。"""

    tl: Point
    tr: Point
    br: Point
    bl: Point

    @classmethod
    def from_rect(cls, x0: float, y0: float, x1: float, y1: float) -> "Quad":
        return cls(tl=Point(x=x0, y=y0), tr=Point(x=x1, y=y0), br=Point(x=x1, y=y1), bl=Point(x=x0, y=y1))

    @property
    def points(self) -> list[Point]:
        return [self.tl, self.tr, self.br, self.bl]

    @property
    def bbox(self) -> tuple[float, float, float, float]:
        xs = [p.x for p in self.points]
        ys = [p.y for p in self.points]
        return min(xs), min(ys), max(xs), max(ys)

    @property
    def width(self) -> float:
        """沿元素自身方向的寬（上邊長）。"""
        return math.hypot(self.tr.x - self.tl.x, self.tr.y - self.tl.y)

    @property
    def height(self) -> float:
        """沿元素自身方向的高（左邊長）。"""
        return math.hypot(self.bl.x - self.tl.x, self.bl.y - self.tl.y)

    @property
    def angle_deg(self) -> float:
        """上邊相對於水平的角度（順時針為正，因為 y 向下）。"""
        return math.degrees(math.atan2(self.tr.y - self.tl.y, self.tr.x - self.tl.x))

    @property
    def center(self) -> Point:
        return Point(x=sum(p.x for p in self.points) / 4, y=sum(p.y for p in self.points) / 4)

    def is_axis_aligned(self, tol_deg: float = 0.1) -> bool:
        return abs(self.angle_deg) <= tol_deg


def oriented_union(quads: list[Quad]) -> Quad:
    """多個同方向四角的外接矩形，保留方向（旋轉的文字框不能用軸對齊的外接矩形）。
    方向不一致或接近水平時，退回軸對齊的外接矩形。"""
    angles = [q.angle_deg for q in quads]
    if not quads or max(angles) - min(angles) > 0.5 or abs(angles[0]) < 0.05:
        return union_quad(quads)
    a = math.radians(sum(angles) / len(angles))
    ux, uy = math.cos(a), math.sin(a)  # 沿文字方向
    nx, ny = -uy, ux  # 垂直文字方向（y 向下的座標系中，指向文字的下方）
    pts = [p for q in quads for p in q.points]
    us = [p.x * ux + p.y * uy for p in pts]
    ns = [p.x * nx + p.y * ny for p in pts]
    u0, u1, n0, n1 = min(us), max(us), min(ns), max(ns)

    def pt(u: float, n: float) -> Point:
        return Point(x=u * ux + n * nx, y=u * uy + n * ny)

    return Quad(tl=pt(u0, n0), tr=pt(u1, n0), br=pt(u1, n1), bl=pt(u0, n1))


def union_quad(quads: list[Quad]) -> Quad:
    """多個四角的外接矩形（軸對齊）。"""
    boxes = [q.bbox for q in quads]
    return Quad.from_rect(min(b[0] for b in boxes), min(b[1] for b in boxes), max(b[2] for b in boxes), max(b[3] for b in boxes))


class Color(Model):
    r: int
    g: int
    b: int
    a: float = 1.0  # 不透明度 0 到 1

    @property
    def hex(self) -> str:
        return f"{self.r:02X}{self.g:02X}{self.b:02X}"


class BlobRef(Model):
    """指向儲存區中一個二進位檔（影像、原始檔、輸出檔）。以內容雜湊為名。"""

    sha256: str
    media_type: str
    size: int
    ext: str  # 副檔名，不含點


class PhysicalSize(Model):
    width: float
    height: float
    unit: str = "pt"  # pt、mm、cm、in、px


# ---------- 元素編號 ----------


def make_element_id(page_index: int, kind: str, quad: Quad, content: str = "") -> str:
    """穩定編號：由頁碼、種類、位置（取到萬分之一頁寬）與內容導出。

    上游重跑後，同一元素會得到同一編號，修正單才指得到它。
    """
    b = quad.bbox
    key = f"{kind}|{round(b[0], 4)}|{round(b[1], 4)}|{round(b[2], 4)}|{round(b[3], 4)}|{content}"
    digest = hashlib.sha1(key.encode("utf-8")).hexdigest()[:8]
    return f"p{page_index + 1:03d}-{kind}-{digest}"


def measured(value: T, confidence: float, source: Provenance, writer: str, error: Optional[float] = None) -> Measured[T]:
    return Measured[type(value)](value=value, confidence=confidence, source=source, writer=writer, error=error)  # type: ignore[index]
