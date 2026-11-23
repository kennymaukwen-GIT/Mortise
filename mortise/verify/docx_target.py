"""Word 檔（版面固定 docx_fixed、可重排 docx_flow）的目標驗證（7B）：把輸出的 .docx 真的畫出來，確認裡面是成品文件要的東西。

為什麼要有這一項
  以前 Word 檔沒有自己的驗證，分數全部來自 7A 中性渲染（把成品文件用自家輸出器畫出來）。那只檢查得到③④⑤，
  Word 輸出器自己的錯誤（文字框放錯、字被擠出框外、字型被換掉、整份檔案是空的）完全看不到：把 Word 檔換成
  空白文件，分數照樣不變。

做法
  用 LibreOffice（無介面模式）把 .docx 轉成 PDF，再交給 verify/rendered.py 逐頁比對：相似度、讀回文字與成品文件比對，
  並把每一頁的渲染結果交給⑦做逐行文字對照（LibreOffice 會自己重新排字，所以⑦以「替身引擎」的寬鬆標準看水平位置）。
    版面固定：讀回的每一行依位置分給文字框或表格儲存格，框裡的字要與成品文件一致。
    可重排：元素位置本來就不保留，只檢查每個框的字有沒有出現；頁數與成品文件不同時不比相似度。
  LibreOffice 不是 Word：斷行、字距、文字框的處理可能與 Word 不同，engine 欄與報告都會寫明。
  這台機器沒有 LibreOffice 時不驗證（丟出 Unverified），⑦改用中性渲染當代用值，並在報告裡寫明「Word 檔本身沒有驗證」。

同一份 .docx（內容雜湊相同）在同一個程序裡只轉一次：修正迴圈每一輪都會重跑⑦，Word 檔沒有變就沿用上一次的 PDF。
"""

from __future__ import annotations

import tempfile
from collections import OrderedDict
from typing import Callable, Optional

from ..artifacts import ElementList, FormatReport, Layout, OutputFile, OutputFormat, PageSpec, Severity, StyledDoc
from ..config import Config
from ..pipeline.store import Store
from ..tools import libreoffice
from . import Unverified, rendered

_LABEL = {OutputFormat.DOCX_FIXED: "Word 檔（版面固定）", OutputFormat.DOCX_FLOW: "Word 檔（可重排）"}
_CACHE: "OrderedDict[tuple[str, str], bytes]" = OrderedDict()
_CACHE_MAX = 8


def to_pdf(output: OutputFile, store: Store, timeout_s: int) -> bytes:
    """用 LibreOffice 把輸出檔轉成 PDF；同一份檔案（內容雜湊）只轉一次。"""
    key = (output.file.sha256, output.format.value)
    if key in _CACHE:
        _CACHE.move_to_end(key)
        return _CACHE[key]
    data = libreoffice.to_pdf(store.blob_path(output.file), timeout_s)
    _CACHE[key] = data
    while len(_CACHE) > _CACHE_MAX:
        _CACHE.popitem(last=False)
    return data


def verify(output: OutputFile, specs: list[PageSpec], elements: list[ElementList], layout: Layout, styled: StyledDoc, cfg: Config, store: Store,
           on_render: Optional[Callable] = None) -> FormatReport:
    if not libreoffice.available():
        raise Unverified("這台機器沒有安裝 LibreOffice，Word 檔本身沒有渲染驗證。")
    vc = cfg.section("verify")
    label = _LABEL.get(output.format, "Word 檔")
    try:
        data = to_pdf(output, store, int(vc.get("libreoffice_timeout_s", 120)))
    except libreoffice.RenderError as e:
        raise Unverified(f"LibreOffice 沒有把 Word 檔轉出來（{e}），Word 檔本身沒有渲染驗證。") from e
    render = store.put_blob(data, "application/pdf", "pdf")
    with tempfile.NamedTemporaryFile(suffix=".pdf") as tmp:
        tmp.write(data)
        tmp.flush()
        pages, notes = rendered.verify_pdf(tmp.name, output.format, specs, elements, layout, styled, cfg, store, on_render,
                                           by_position=output.format != OutputFormat.DOCX_FLOW, label=label,
                                           similarity_pass=float(vc.get("similarity_pass", 0.97)))
    notes.insert(0, "Word 檔用 LibreOffice 轉成 PDF 再比對；LibreOffice 不是 Word，斷行、字距與文字框的處理可能與 Word 不同。")
    if not output.selfcheck_ok:
        notes.append("輸出器的自檢沒有通過：" + "；".join(output.selfcheck_notes[:5]))
    ok = output.selfcheck_ok and all(i.severity != Severity.ERROR for p in pages for i in p.issues) and all(
        p.text_match_ratio is None or p.text_match_ratio >= 1.0 for p in pages)
    return FormatReport(format=output.format, engine=f"{libreoffice.version()}（替身引擎，不是 Word）", ok=ok, pages=pages, notes=notes, render=render)
