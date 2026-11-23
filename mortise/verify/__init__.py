"""各輸出格式的目標驗證（7B）。

每種格式一個模組（mortise/verify/ 下），登記在 TARGETS，介面相同：

    def verify(output: OutputFile, specs: list[PageSpec], elements: list[ElementList], layout: Layout,
               styled: StyledDoc, cfg: Config, store: Store) -> FormatReport

做法：把輸出檔重新打開，畫成影像與工作影像比相似度，並把文字讀回來與成品文件比對。
只讀不寫：不修改任何上游產物；要回頭修改只能在 FormatReport 的 issues 裡提出，由⑦決定是否開修正單。

驗證模組可以另外接受 on_render 參數（可省略）：每畫好一頁就呼叫 on_render(頁碼, 渲染結果, glyphs=讀回來的字)，
渲染結果要先用 s7_verify.content_region 裁成與工作影像同一個範圍；glyphs 是讀回來的每個字在輸出頁面上的位置
（verify/lines.py 的 Glyph；讀不到文字層時省略）。⑦用它做「逐行文字對照」（lines.py：③讀到的每一行字在成品上
找不找得到、有沒有錯位或疊在一起），比對的問題由⑦併進這個格式的 FormatReport，並計入整體分數。
PowerPoint 的目標驗證（要靠 LibreOffice 當替身引擎）是較早寫的，仍在 stages/s7_verify.py 裡。
這台機器驗證不了某個格式時（例如 Word 需要 LibreOffice），驗證模組丟出例外（docx_target.Unverified），
⑦改用中性渲染的分數當這個格式的代用值，並在報告裡寫明。沒有登記驗證模組的格式（例如 Excel、InDesign）也一樣。
"""

from __future__ import annotations

import importlib
from typing import Callable, Optional

from ..artifacts import OutputFormat

class Unverified(Exception):
    """這個格式在這台機器上驗證不了（例如 Word 需要 LibreOffice）。⑦改用中性渲染的分數當代用值，並把訊息寫進報告。"""


# 格式 → 驗證模組（mortise.verify 底下的模組名稱）
TARGETS: dict[OutputFormat, str] = {
    OutputFormat.PDF: "pdf_target",
    OutputFormat.SVG: "svg_target",
    OutputFormat.DOCX_FIXED: "docx_target",  # LibreOffice 轉成 PDF 再比（替身引擎，不是 Word）
    OutputFormat.DOCX_FLOW: "docx_target",
    OutputFormat.AI_PDF: "ai_pdf_target",
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
