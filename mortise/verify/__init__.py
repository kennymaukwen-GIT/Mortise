"""各輸出格式的目標驗證（7B）。

每種格式一個模組（mortise/verify/ 下），登記在 TARGETS，介面相同：

    def verify(output: OutputFile, specs: list[PageSpec], elements: list[ElementList], layout: Layout,
               styled: StyledDoc, cfg: Config, store: Store) -> FormatReport

做法：把輸出檔重新打開，畫成影像與工作影像比相似度，並把文字讀回來與成品文件比對。
只讀不寫：不修改任何上游產物；要回頭修改只能在 FormatReport 的 issues 裡提出，由⑦決定是否開修正單。
PowerPoint 的目標驗證（要靠 LibreOffice 當替身引擎）是較早寫的，仍在 stages/s7_verify.py 裡。
"""

from __future__ import annotations

import importlib
from typing import Callable, Optional

from ..artifacts import OutputFormat

# 格式 → 驗證模組（mortise.verify 底下的模組名稱）
TARGETS: dict[OutputFormat, str] = {
    OutputFormat.PDF: "pdf_target",
    OutputFormat.SVG: "svg_target",
}


def verifier(fmt: OutputFormat) -> Optional[Callable]:
    name = TARGETS.get(fmt)
    if not name:
        return None
    try:
        module = importlib.import_module(f".{name}", __package__)
    except ModuleNotFoundError as e:
        if e.name and e.name.endswith(f"verify.{name}"):
            return None  # 這個格式的驗證還沒寫
        raise
    return getattr(module, "verify", None)
