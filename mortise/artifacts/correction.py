"""修正單 Correction：回頭修改的唯一形式。"""

from __future__ import annotations

from enum import Enum
from typing import Any, Optional

from .common import Model


class CorrectionSource(str, Enum):
    VERIFY = "verify"  # ⑦驗證
    AI = "ai"
    USER = "user"  # 使用者：信心值 1.0，之後任何重跑都不得覆蓋


class Correction(Model):
    id: str
    target_stage: int  # 1 到 7：送給哪個階段，從該階段重跑到底
    target_step: str = ""  # 階段內的步驟名稱（可留空）
    page_index: Optional[int] = None  # 文件級修正留空
    element_id: Optional[str] = None
    field: str  # 要改的欄位，例如 "size"、"text"、"font_family"
    new_value: Any = None
    instruction: str = ""  # 給階段的指示（例如「把這兩段合併」）
    source: CorrectionSource
    round: int = 0  # 第幾輪
