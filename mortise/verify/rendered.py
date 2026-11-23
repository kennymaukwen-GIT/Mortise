"""「畫成 PDF 的輸出檔」逐頁比對的共用程式（docx_target、ai_pdf_target 用）。

輸入是一份 PDF：Word 檔經 LibreOffice 轉出來的 PDF，或輸出檔本身就是 PDF（給 Illustrator 的 PDF）。每一頁做三件事：
  1. 外觀：PDFium 畫成影像，與工作影像比相似度（s7_verify.similarity；有指定輸出尺寸時只比原始內容那一塊）。
     畫好的影像連同讀回來的每個字的位置經 on_render 交給⑦，做逐行文字對照（verify/lines.py）。
  2. 文字：用③的擷取函式（s3_recognize.read_text_lines）把文字讀回來，與成品文件逐框比對。
       依位置比（by_position=True，版面固定的格式）：讀回來的每一行分給它所在的文字框或表格儲存格
         （與 PowerPoint 的目標驗證是同一支 s7_verify._assign），框裡的字（不計空白）要與成品文件一致。
       不依位置比（by_position=False，可重排的 Word）：元素的位置本來就不保留，只檢查每個文字框、每個儲存格的字
         有沒有出現在整份文件讀回來的文字裡（不計空白；九成以上的八字片段找得到就算有）。
     text_match_ratio = 文字一致（或找得到）的框與儲存格 ÷ 全部的框與儲存格。
  3. 頁數：渲染結果少了某一頁就開 page_missing（這一頁的分數為 0）；多出來的頁寫在 notes。
     可重排的 Word 重新分頁後頁數不同時，頁與頁對不起來：不比相似度、也不做逐行對照，只比文字，notes 會寫明。

只讀不寫：不改輸出檔，也不改任何上游產物。
"""

from __future__ import annotations

import re
from typing import Callable, Optional

from PIL import Image

from ..artifacts import ElementList, FormatPageReport, Issue, Layout, OutputFormat, PageSpec, Severity, StyledDoc, TextLine
from ..config import Config
from ..pipeline.store import Store
from ..stages import s2_page, s3_recognize
from ..tools import pdfium as pdf

_SHINGLE = 8  # 不依位置比時，片段的長度（字）
_SHINGLE_FOUND = 0.9  # 片段找得到的比例至少這麼多，就算這個框的字有出現


def norm(text: str) -> str:
    return re.sub(r"\s+", "", text)


def expected_texts(page) -> dict[tuple, str]:
    """成品文件這一頁每個文字框、每個有文字的儲存格的字。鍵是（元素編號, 儲存格的（列, 欄）或 None）。"""
    out: dict[tuple, str] = {}
    for tb in page.text_boxes:
        out[(tb.id, None)] = "".join(r.text for sp in tb.paragraphs for r in sp.runs)
    for t in page.tables:
        for cell in t.cells:
            out[(t.id, (cell.row, cell.col))] = "".join(r.text for sp in cell.paragraphs for r in sp.runs)
    return {k: v for k, v in out.items() if norm(v)}


def _where(key: tuple) -> str:
    return key[0] if key[1] is None else f"{key[0]} 第 {key[1][0] + 1} 列第 {key[1][1] + 1} 欄"


def _clip(text: str, n: int = 24) -> str:
    text = text.strip()
    return text if len(text) <= n else text[:n] + "…"


def _present(want: str, haystack: str, shingles: set[str]) -> bool:
    """want（已去空白）有沒有出現在 haystack 裡：整串找得到，或九成以上的八字片段找得到（換行、清單編號造成的小差異不算）。"""
    if not want or want in haystack:
        return True
    if len(want) <= _SHINGLE:
        return False
    parts = [want[i:i + _SHINGLE] for i in range(len(want) - _SHINGLE + 1)]
    return sum(1 for s in parts if s in shingles) / len(parts) >= _SHINGLE_FOUND


def read_page(reader: pdf.PageReader, page_index: int, cfg: Optional[Config]) -> tuple[list[TextLine], float]:
    """用③的擷取函式讀回一頁看得見的文字行。回傳（文字行（頁寬為 1 的相對座標）、頁寬 pt）。"""
    boxes = reader.boxes()
    matrix, dw, _ = s2_page.pdf_matrix(boxes.get("crop", boxes["media"]), reader.rotation())
    rc = cfg.section("recognize") if cfg is not None else Config.load().section("recognize")
    return [t for t in s3_recognize.read_text_lines(reader, page_index, matrix, dw, rc) if not t.hidden], dw


def verify_pdf(path: str, fmt: OutputFormat, specs: list[PageSpec], elements: list[ElementList], layout: Layout, styled: StyledDoc,
               cfg: Config, store: Store, on_render: Optional[Callable] = None, by_position: bool = True,
               label: str = "輸出檔", similarity_pass: float = 0.95) -> tuple[list[FormatPageReport], list[str]]:
    """逐頁比對一份 PDF（見模組開頭）。回傳（各頁報告、notes）。
    on_render(頁碼, 裁成與工作影像同一範圍的渲染結果, glyphs=讀回來的每個字) 每畫好一頁呼叫一次；
    glyphs 是 verify/lines.py 的 glyphs_from_lines 的結果（輸出頁面 pt）。"""
    from ..stages.s7_verify import _assign, content_region, similarity, verify_dpi  # 延後匯入：s7_verify 會回頭匯入本套件
    from . import lines as line_check

    vc = cfg.section("verify") if cfg is not None else {}
    notes: list[str] = []
    reports: list[FormatPageReport] = []
    pdoc = pdf.open_document(path)
    try:
        n = len(pdoc)
        same_pages = n == len(styled.pages)
        if n > len(styled.pages):
            notes.append(f"{label}渲染後有 {n} 頁，成品文件只有 {len(styled.pages)} 頁（多出的頁多半是文字放不下、被擠到下一頁）。")
        if not by_position and not same_pages:
            notes.append(f"{label}重新分頁後是 {n} 頁、成品文件是 {len(styled.pages)} 頁，頁與頁對不起來：只比文字有沒有出現，不比相似度、也不做逐行文字對照。")
        read: dict[int, tuple[list[TextLine], float]] = {}
        all_text = ""
        if not by_position:  # 整份文件讀回來的字（不計空白）
            for i in range(n):
                reader = pdf.PageReader(pdoc, i)
                try:
                    read[i] = read_page(reader, i, cfg)
                finally:
                    reader.close()
            all_text = norm("".join(t.text for i in range(n) for t in read[i][0]))
        shingles = {all_text[i:i + _SHINGLE] for i in range(max(0, len(all_text) - _SHINGLE + 1))}
        for sp in styled.pages:
            i = sp.page_index
            spec = next((s for s in specs if s.page_index == i), None)
            issues: list[Issue] = []
            expected = expected_texts(sp)
            if by_position and i >= n:
                reports.append(FormatPageReport(page_index=i, issues=[Issue(severity=Severity.ERROR, code="page_missing", message=f"{label}渲染後少了這一頁。")]))
                continue
            sim: Optional[float] = None
            got: list[TextLine] = []
            width = sp.width_pt
            if i < n and (by_position or same_pages):
                reader = pdf.PageReader(pdoc, i)
                try:
                    image = reader.render(verify_dpi(float(vc.get("render_dpi", 100)), sp, spec))
                    got, width = read[i] if i in read else read_page(reader, i, cfg)
                finally:
                    reader.close()
                if spec is not None:
                    working = Image.open(store.blob_path(spec.working_image))  # 白紙類頁面比去光照後的影像；其他頁面工作影像就是原圖
                    region = content_region(image, sp)
                    sim = similarity(working, region)
                    if on_render is not None:
                        on_render(i, region, glyphs=line_check.glyphs_from_lines(got, width))
                    if sim < similarity_pass:
                        issues.append(Issue(severity=Severity.WARNING, code="low_similarity", message=f"{label}渲染後與原圖的相似度 {sim:.3f}，低於合格線 {similarity_pass}。"))
            good = 0
            if by_position:
                assigned = _assign(got, sp, width)
                for key, want in expected.items():
                    slot = key[0] if key[1] is None else f"{key[0]}#{key[1][0]},{key[1][1]}"
                    lines = sorted(assigned.get(slot, []), key=lambda t: (round(t.baseline[0].y, 4), t.baseline[0].x))
                    text = "".join(t.text for t in lines)
                    if norm(text) == norm(want):
                        good += 1
                    elif not norm(text):
                        issues.append(Issue(severity=Severity.ERROR, code="text_missing", element_id=key[0], message=f"{_where(key)} 的文字在{label}的渲染結果裡找不到（預期「{_clip(want)}」）。"))
                    else:
                        issues.append(Issue(severity=Severity.ERROR, code="text_mismatch", element_id=key[0],
                                            message=f"{_where(key)} 的文字在{label}的渲染結果裡與成品文件不一致：預期「{_clip(want)}」，讀到「{_clip(text)}」。"))
            else:
                for key, want in expected.items():
                    if _present(norm(want), all_text, shingles):
                        good += 1
                    else:
                        issues.append(Issue(severity=Severity.ERROR, code="text_missing", element_id=key[0], message=f"{_where(key)} 的文字在{label}的渲染結果裡找不到（預期「{_clip(want)}」）。"))
            ratio = good / len(expected) if expected else None
            reports.append(FormatPageReport(page_index=i, similarity=sim, text_match_ratio=ratio, issues=issues))
    finally:
        pdoc.close()
    return reports, notes
