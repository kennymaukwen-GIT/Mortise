"""還原度報告 FidelityReport（⑦驗證 交出）。"""

from __future__ import annotations

from enum import Enum
from typing import Optional

from .common import SCHEMA_VERSION, BlobRef, Model
from .correction import Correction


class FieldStatus(str, Enum):
    EXACT = "exact"  # 精確擷取
    MEASURED = "measured"  # 量測吸附
    AI = "ai"  # AI 判斷
    GENERATED = "generated"  # 生成
    USER = "user"  # 使用者指定
    UNRECOVERABLE = "unrecoverable"  # 無法還原


class Severity(str, Enum):
    INFO = "info"
    WARNING = "warning"
    ERROR = "error"


class Issue(Model):
    severity: Severity
    code: str  # 例如 "linebreak_mismatch"、"font_substituted"、"text_mismatch"
    message: str
    element_id: Optional[str] = None


class ElementReport(Model):
    element_id: str
    kind: str  # text_box、image、shape
    fields: dict[str, FieldStatus] = {}
    position_error_pt: Optional[float] = None  # 與原圖的位置差
    size_error_pt: Optional[float] = None
    text_match: Optional[bool] = None
    linebreak_match: Optional[bool] = None
    font_substituted: bool = False
    confidence: float = 1.0


class PageReport(Model):
    page_index: int
    neutral_similarity: Optional[float] = None  # 7A 中性驗證：0 到 1
    target_similarity: Optional[float] = None  # 7B 目標驗證：0 到 1
    elements: list[ElementReport] = []
    issues: list[Issue] = []


class FidelityReport(Model):
    schema_version: str = SCHEMA_VERSION
    neutral_engine: Optional[str] = None  # 7A 用的渲染方式
    target_engine: Optional[str] = None  # 7B 用的引擎（不是使用者手上的目標軟體時要寫明）
    pages: list[PageReport]
    overall_score: Optional[float] = None
    field_counts: dict[str, int] = {}  # 各種欄位狀態的數量
    corrections_proposed: list[Correction] = []
    round: int = 0
    notes: list[str] = []
    target_render: Optional[BlobRef] = None  # 7B 目標驗證渲染出來的 PDF（前端疊圖預覽用）
