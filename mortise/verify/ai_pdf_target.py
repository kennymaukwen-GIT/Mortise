"""給 Illustrator 的 PDF（ai_pdf）的目標驗證（7B）：把輸出檔本身用 PDFium 畫出來、讀回文字，與原圖和成品文件比對。

7A 中性渲染用的也是同一個輸出器，但那是另外畫的一份（不含⑥收到的修正單）；這裡比的是真正交給使用者的那個檔案。
做法與 Word 的驗證共用 verify/rendered.py（依位置比對文字）。PDFium 不是 Illustrator，engine 欄會寫明。
"""

from __future__ import annotations

from typing import Callable, Optional

from ..artifacts import ElementList, FormatReport, Layout, OutputFile, PageSpec, Severity, StyledDoc
from ..config import Config
from ..pipeline.store import Store
from . import rendered

ENGINE = "PDFium 回讀（自家工具，不是 Illustrator；只能證明檔案裡寫了什麼）"


def verify(output: OutputFile, specs: list[PageSpec], elements: list[ElementList], layout: Layout, styled: StyledDoc, cfg: Config, store: Store,
           on_render: Optional[Callable] = None) -> FormatReport:
    vc = cfg.section("verify")
    pages, notes = rendered.verify_pdf(str(store.blob_path(output.file)), output.format, specs, elements, layout, styled, cfg, store, on_render,
                                       by_position=True, label="給 Illustrator 的 PDF", similarity_pass=float(vc.get("similarity_pass", 0.97)))
    if not output.selfcheck_ok:
        notes.append("輸出器的自檢沒有通過：" + "；".join(output.selfcheck_notes[:5]))
    ok = output.selfcheck_ok and all(i.severity != Severity.ERROR for p in pages for i in p.issues)
    return FormatReport(format=output.format, engine=ENGINE, ok=ok, pages=pages, notes=notes, render=output.file)
