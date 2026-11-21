"""還原度報告 FidelityReport（⑦驗證 交出）。"""

from __future__ import annotations

from enum import Enum
from typing import Optional

from .common import SCHEMA_VERSION, BlobRef, Model, OutputFormat
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


class FormatPageReport(Model):
    """某個輸出格式、某一頁的目標驗證結果。"""

    page_index: int
    similarity: Optional[float] = None  # 把輸出檔重新畫出來，與工作影像的相似度（0 到 1）
    text_match_ratio: Optional[float] = None  # 從輸出檔讀回來的文字，與成品文件一致的比例（0 到 1）
    position_error_pt: Optional[float] = None  # 讀回來的文字位置與成品文件的最大差（pt）
    issues: list[Issue] = []


class FormatReport(Model):
    """某個輸出格式的目標驗證結果（7B）：把輸出檔重新打開、畫出來、讀回來，確認它真的是我們要的。"""

    format: OutputFormat
    engine: str  # 用什麼把輸出檔畫出來或讀回來（不是使用者手上的目標軟體時要寫明）
    ok: bool = True
    pages: list[FormatPageReport] = []
    notes: list[str] = []
    render: Optional[BlobRef] = None  # 重新畫出來的結果（前端疊圖預覽用），沒有就留空


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
    format_reports: list[FormatReport] = []  # PowerPoint 以外各輸出格式的目標驗證結果
