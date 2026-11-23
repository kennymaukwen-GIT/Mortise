"""⑦ 驗證：收輸出檔與工作影像，交出還原度報告 FidelityReport 與修正單。

放在輸出之後，分兩道：
  7A 中性驗證（s7.neutral）：用自家確定性的渲染（ai_pdf 輸出器）把成品文件畫出來，與原圖比相似度，檢查③④⑤對不對。
  7B 目標驗證（s7.target）：用目標軟體或替身引擎渲染輸出檔，檢查轉檔後有沒有跑版。
     目前的替身引擎是 LibreOffice（不是使用者手上的 PowerPoint，報告中會註明）。
     PowerPoint 以外的格式各有自己的回讀驗證（mortise/verify/ 下各一個模組），結果收在 format_reports。
其他步驟：
  s7.compare   逐區比對：位置誤差、文字是否一致、斷行是否一致、整頁相似度
  s7.fields    每個元素每個欄位標示 精確擷取／量測吸附／AI 判斷／生成／使用者／無法還原
  s7.propose   開修正單：斷行不一致 → 調整文字框寬度；位置偏移 → 平移。這是目標軟體的渲染差異，送給⑥該格式的輸出器，不改成品文件

有指定輸出尺寸時（⑤把頁面等比例縮放並置中，StyledPage.placement 記原始內容在輸出頁面上的位置）：
  - 與原圖（工作影像）比相似度時，只比 placement 那一塊（content_region），留白不算；渲染解析度依縮放比例調整（verify_dpi），
    讓那一塊的像素數與不縮放時相同。
  - 「成品文件對輸出檔」的比對（讀回的文字與成品文件排版的位置）不受影響：兩邊都是輸出頁面的座標。
  - 「輸出檔對來源」的比對（與③來源文字行的位置差）先用 placement 把來源座標換到輸出頁面（source_frame）。

整體分數（overall_score）
  每個輸出格式各算一個分數（各頁的平均），整體分數 = 最差的格式 ＋ format_mean_weight（0.1）×（各格式的平均 − 最差）。
  報告 notes 的第一行列出各格式的分數；用代用值的格式會寫明原因。

  各格式每一頁的分數 = 畫面分數 × 逐行分數。
    PowerPoint（LibreOffice 當替身引擎渲染）：畫面分數 = 0.5 × 渲染的相似度 ＋ 0.5 × 文字框與表格中「文字一致、斷行一致、
      位置在容許差內」的比例。渲染結果少了這一頁是 0。
    有自己驗證模組的格式（mortise/verify/：可編輯 PDF、SVG、Word（LibreOffice 轉成 PDF）、給 Illustrator 的 PDF）：
      畫面分數 = 0.5 × 回讀渲染的相似度 ＋ 0.5 × 文字一致率（缺一個就只用另一個）。輸出檔裡沒有這一頁是 0。
    沒有驗證模組、或這台機器驗證不了的格式（Excel、InDesign 等；沒有 LibreOffice 時的 Word 與 PowerPoint）：
      用 7A 中性渲染的分數（相似度 × 中性渲染的逐行分數）當「代用值」，報告寫明「這個格式的檔案本身沒有驗證」。
    沒有任何輸出格式：中性渲染的分數。
  逐行分數（mortise/verify/lines.py）：拿同一份渲染結果，逐行檢查③讀到的每一行字在成品上找不找得到、有沒有錯位、
    有沒有和別的行疊在一起、換行是否相同、字的顏色是否相同、有沒有多出原圖沒有的字。輸出檔讀得回文字時（PDF、SVG、
    LibreOffice 的渲染、中性渲染）也比對「原位置上寫的是什麼字」。沒有任何可判斷的行（例如整頁都是圖）時，逐行分數不算，只看畫面分數。

  為什麼這樣組合
    - 畫面相似度是把整頁縮到約 480 像素寬再比，看得出整頁的樣子（底色、圖片、大區塊的位置），卻幾乎看不出少了幾行小字、
      或一行字錯開幾 pt：A4 履歷左欄十幾行字不見、右欄幾行疊在一起，相似度仍有 0.95。逐行分數補的就是這一塊。
    - 兩者相乘而不是相加：任何一邊有問題都要把分數往下拉，不能被另一邊的高分抵銷；兩邊都完美時（例如原生 PDF
      輸出成可編輯 PDF）仍是 1.0 附近。
    - 多個格式取最差、而不是平均：使用者拿到的每一個檔案都要能用。以前 PowerPoint 的分數會蓋掉其他格式（可編輯 PDF 換成
      空白檔，整體分數照樣不變），沒有 PowerPoint 時又取平均（兩個格式一個壞掉，分數還有一半）。平均只佔一成，
      讓「其他格式都好」仍略有反映，但一個格式壞掉一定看得出來。
    - Word 檔一定要實際渲染（LibreOffice 轉成 PDF）：以前 Word 的分數全來自中性渲染，Word 輸出器自己的錯誤（文字框放錯、
      字型被換掉、整份檔案是空的）完全看不到。LibreOffice 不是 Word，報告會寫明。
    - 逐行分數 = 1 − 各行嚴重程度的平均 − min(10%, 2% × 內容問題的嚴重程度總和)。
        每一行算一份：5 行的投影片少一行扣 20%，80 行的 A4 少一行扣 1.25%。
        但長文件少一行也必須看得出來，所以「找不到、疊在一起」這類內容問題每個再多扣 2%（最多多扣 10%：
        問題很多時第一項已經反映嚴重程度，不必再加重）。
        嚴重程度：找不到 1（內容不見了，最嚴重）；疊在一起、字在檔案裡卻沒有畫在原位置 0.6（字還在，但讀不清楚或找不到）；
        換行與原圖不同 最多 0.5；一部分找不到 = 不見的比例；位置偏移 0.15 到 0.35 × 偏掉的部分佔這一行的比例
        （字在、讀得懂，只是位置不對）；字的顏色不同 0.2；成品上多出原圖沒有的一行字 0.2。
      例：80 行的履歷有 20 行找不到、3 行疊在一起 → 逐行分數約 1 − (20 + 1.8) ÷ 80 − 10% ≈ 0.63，
          畫面相似度 0.95 的整體分數就是 0.60，而不是 0.95。
    - LibreOffice（代替 PowerPoint、Word）與中性渲染是自己重新排字的替身引擎，字距本來就和目標軟體不同，
      水平偏移放寬到 0.35 個字高才算；可編輯 PDF 與 SVG 的每一行位置寫死在檔案裡，不放寬。驗證用的這台電腦沒有字型
      畫得出來的字，在不內嵌字型的格式裡不判斷（摘要會註明有幾行沒有驗證到），可編輯 PDF 照常判斷。
    - 每一行的問題都開成一條問題（代碼見 verify/lines.py 的 CODES），element_id 指到那一行所屬的文字框或表格，
      網頁的 Overlay 依它畫紅框。PowerPoint 與（當代用值的）中性渲染的問題放在各頁的 issues；其他格式的放在該格式的 format_reports。
    - 照片、掃描這類影像輸入：多數文字行一致偏斜（原圖歪了、沒有被轉正）時，在頁面的 issues 寫一條 page_skewed 提醒。
"""

from __future__ import annotations

import inspect
import math
import re
import sys
import tempfile
from typing import Optional

import numpy as np
from PIL import Image, ImageFilter

from ..artifacts import (
    Correction, CorrectionSource, ElementList, ElementReport, FidelityReport, FieldStatus, Issue, Layout, OutputFile, OutputFormat,
    PageReport, PageSpec, Provenance, Severity, SourceDoc, StyledDoc, TextLine,
)
from ..config import Config
from ..pipeline.store import Store
from ..tools import libreoffice
from ..tools import pdfium as pdf
from ..verify import lines as line_check
from . import s2_page, s3_recognize

NAME = "s7"
VERSION = "6"

# 逐行文字對照的問題說明前面加的渲染來源
_WHERE = {OutputFormat.PDF: "可編輯 PDF", OutputFormat.SVG: "SVG", OutputFormat.PPTX: "PowerPoint 經 LibreOffice 渲染",
          OutputFormat.DOCX_FIXED: "Word（版面固定）經 LibreOffice 渲染", OutputFormat.DOCX_FLOW: "Word（可重排）經 LibreOffice 渲染",
          OutputFormat.AI_PDF: "給 Illustrator 的 PDF"}
_NEUTRAL_WHERE = "成品自行渲染"
# 會把字型內嵌進檔案的格式：驗證時畫不出來的字，使用者那裡也畫不出來（照常判斷）；其他格式只寫字型名稱
_EMBEDS_FONTS = {OutputFormat.PDF, OutputFormat.AI_PDF}
# 渲染結果是替身引擎自己重新排字的格式（LibreOffice 代替 Word）：水平位置放寬（PowerPoint 與中性渲染另外指定）
_RELAYOUT = {OutputFormat.DOCX_FIXED, OutputFormat.DOCX_FLOW}
# 報告裡各格式的名稱
_LABEL = {OutputFormat.PPTX: "PowerPoint", OutputFormat.PDF: "可編輯 PDF", OutputFormat.SVG: "SVG", OutputFormat.DOCX_FIXED: "Word（版面固定）",
          OutputFormat.DOCX_FLOW: "Word（可重排）", OutputFormat.XLSX: "Excel", OutputFormat.AI_PDF: "給 Illustrator 的 PDF",
          OutputFormat.IDML: "InDesign（IDML）", OutputFormat.GOOGLE_SLIDES: "Google 簡報", OutputFormat.TEMPLATE: "模板",
          OutputFormat.SVG_ASSETS: "向量圖 SVG"}

_STATUS = {
    Provenance.EXACT: FieldStatus.EXACT, Provenance.MEASURED: FieldStatus.MEASURED, Provenance.SNAPPED: FieldStatus.MEASURED,
    Provenance.INFERRED: FieldStatus.MEASURED, Provenance.AI: FieldStatus.AI, Provenance.USER: FieldStatus.USER, Provenance.GENERATED: FieldStatus.GENERATED,
}


def _norm(text: str) -> str:
    return re.sub(r"\s+", "", text)


def similarity(a: Image.Image, b: Image.Image, width: int = 480) -> float:
    """整頁相似度（0 到 1）：縮到同樣大小、略為模糊後比較，對反鋸齒的細微差異不敏感。"""
    h = max(1, round(width * a.size[1] / a.size[0]))
    x = np.asarray(a.convert("L").resize((width, h), Image.LANCZOS).filter(ImageFilter.GaussianBlur(1.0)), dtype=np.float32)
    y = np.asarray(b.convert("L").resize((width, h), Image.LANCZOS).filter(ImageFilter.GaussianBlur(1.0)), dtype=np.float32)
    return float(1.0 - np.abs(x - y).mean() / 255.0)


# ---------- 輸出尺寸（placement）----------


def content_region(image: Image.Image, page) -> Image.Image:
    """有指定輸出尺寸時，輸出頁面上只有 placement 範圍是原始內容（其餘是留白）：把整頁的渲染結果裁到那個範圍，
    才能與原圖（工作影像）比相似度。用浮點座標裁切（不取整到像素），沒有 placement 就原樣回傳。"""
    pl = getattr(page, "placement", None)
    if pl is None or page.width_pt <= 0:
        return image
    k = image.size[0] / page.width_pt
    size = (max(1, round(pl.w * k)), max(1, round(pl.h * k)))
    return image.transform(size, Image.EXTENT, (pl.x * k, pl.y * k, (pl.x + pl.w) * k, (pl.y + pl.h) * k), Image.BILINEAR)


MAX_RENDER_PIXELS = 40_000_000  # 比對用渲染的整頁像素上限（與②工作影像的上限 max_working_pixels 預設值相同）


def verify_dpi(dpi: float, page, spec: PageSpec | None) -> float:
    """比對用的渲染解析度。有 placement 時依縮放比例調整，讓原始內容那一塊的像素數與不縮放時相同
    （縮小到小紙張時不會因為像素太少而失真，放大到大海報時也不必畫出巨大的影像）；調整幅度限制在 4 倍以內。
    不論有沒有 placement，整頁的像素數都不超過 MAX_RENDER_PIXELS：大型看板、海報的實際尺寸（或多打一個 0）
    不會讓渲染要求幾 GB 的記憶體而讓整個服務崩潰；工作影像本身也有同樣的上限，畫得更大並不會比得更準。"""
    pl = getattr(page, "placement", None)
    if pl is not None and spec is not None and pl.w > 0:
        s = pl.w / max(spec.page_width_pt.value, 1e-6)
        dpi = dpi * min(4.0, max(0.25, 1.0 / s))
    w, h = float(getattr(page, "width_pt", 0) or 0), float(getattr(page, "height_pt", 0) or 0)
    if w > 0 and h > 0 and (w * dpi / 72.0) * (h * dpi / 72.0) > MAX_RENDER_PIXELS:
        dpi = 72.0 * (MAX_RENDER_PIXELS / (w * h)) ** 0.5
    return dpi


def source_frame(page) -> tuple[float, float, float]:
    """來源座標（頁寬為 1 的相對單位）換到輸出頁面 pt 的（比例, x 位移, y 位移）：x' = ox + k·x、y' = oy + k·y。
    沒有 placement 時就是乘上頁寬。「輸出檔對來源」的比對（例如來源文字行的位置）一律用它換算。"""
    pl = getattr(page, "placement", None)
    if pl is None:
        return page.width_pt, 0.0, 0.0
    return pl.w, pl.x, pl.y


def run(
    doc: SourceDoc, specs: list[PageSpec], elements: list[ElementList], layout: Layout, styled: StyledDoc,
    outputs: list[OutputFile], cfg: Config, store: Store, round_no: int = 0,
) -> FidelityReport:
    from .. import verify as targets  # 其他格式的目標驗證（mortise/verify/ 下各格式一個模組）
    from . import s6_export

    c = cfg.section("verify")
    notes: list[str] = []
    lines_on = bool(line_check.params(cfg)["line_check"])
    checker = _LineChecker(specs, elements, layout, styled, cfg, store, notes) if lines_on else None
    pptx = next((o for o in outputs if o.format == OutputFormat.PPTX), None)
    pages = [_field_report(spec, el, layout, styled) for spec, el in zip(specs, elements)]
    # 7A 的第一半：先把中性渲染的 PDF 輸出出來。它的缺字紀錄（所有候選字型都沒有的字）告訴其他格式的逐行比對：
    # 驗證用的這台電腦畫不出哪些字。真正畫出來比對（第二半）放在最後，那時才知道需不需要拿它來評分。
    neutral_out = None
    if c.get("neutral", True):
        try:
            neutral_out = s6_export.run(styled, OutputFormat.AI_PDF, "neutral", cfg, store)
            if checker is not None:
                checker.unrenderable = frozenset(chr(int(h, 16)) for d in neutral_out.degradations if d.concept == "缺字"
                                                 for h in re.findall(r"U\+([0-9A-F]{4,6})", d.action))
        except Exception as e:  # 中性渲染失敗不影響其他驗證
            notes.append(f"7A 中性驗證沒有執行：{type(e).__name__}: {e}")
    target_engine = None
    target_render = None
    proposals: list[Correction] = []
    target_lines: dict[int, line_check.PageLines] = {}
    unverified: dict[OutputFormat, str] = {}  # 這台機器驗證不了的格式 → 原因

    if pptx is not None and libreoffice.available():
        try:
            rendered = libreoffice.to_pdf(store.blob_path(pptx.file), c["libreoffice_timeout_s"])
            target_render = store.put_blob(rendered, "application/pdf", "pdf")
            target_engine = f"{libreoffice.version()}（替身引擎，不是 PowerPoint；斷行與字距可能與 PowerPoint 不同）"
            pages, proposals, target_lines = _target_verify(rendered, specs, elements, layout, styled, pages, cfg, store, round_no, checker)
        except libreoffice.RenderError as e:
            notes.append(f"7B 目標驗證沒有執行：{e}")
            unverified[OutputFormat.PPTX] = f"LibreOffice 沒有把 PowerPoint 檔轉出來（{e}）"
    elif pptx is not None:
        notes.append("7B 目標驗證沒有執行：這台機器沒有安裝 LibreOffice。")
        unverified[OutputFormat.PPTX] = "這台機器沒有安裝 LibreOffice"

    # 其他格式的目標驗證
    format_reports = []
    format_lines: dict[tuple[OutputFormat, int], line_check.PageLines] = {}
    for o in outputs:
        fn = targets.verifier(o.format)
        if fn is None:
            continue
        kwargs = {}
        if checker is not None and "on_render" in inspect.signature(fn).parameters:
            # 驗證模組每畫好一頁就交給逐行文字對照（只保留比對結果，不必把每一頁的影像都留在記憶體裡）
            def on_render(page_index: int, image: Image.Image, glyphs=None, _fmt: OutputFormat = o.format) -> None:
                found = checker.check(page_index, image, _WHERE.get(_fmt, _fmt.value), relayout=_fmt in _RELAYOUT,
                                      embeds_fonts=_fmt in _EMBEDS_FONTS, glyphs=glyphs)
                if found is not None:
                    format_lines[(_fmt, page_index)] = found

            kwargs["on_render"] = on_render
        try:
            fr = fn(o, specs, elements, layout, styled, cfg, store, **kwargs)
        except targets.Unverified as e:
            notes.append(f"{_LABEL.get(o.format, o.format.value)} 的目標驗證沒有執行：{e}")
            unverified[o.format] = str(e).rstrip("。")
            continue
        except Exception as e:  # 單一格式的驗證失敗不影響其他驗證
            notes.append(f"{o.format.value} 的目標驗證沒有執行：{type(e).__name__}: {e}")
            unverified[o.format] = f"驗證時發生錯誤（{type(e).__name__}）"
            continue
        format_reports.append(_with_lines(fr, format_lines))
        # 驗證模組可以另外提供 propose：回傳要送給⑥該格式輸出器的修正單（欄位名稱以「格式.」開頭）。
        # 與 PowerPoint 的修正單一樣只影響那個格式的輸出，不改成品文件；要不要保留由執行器看整體分數有沒有進步。
        propose = getattr(sys.modules.get(fn.__module__), "propose", None)
        if propose is not None:
            try:
                proposals.extend(propose(o, specs, elements, layout, styled, cfg, store, round_no))
            except Exception as e:
                notes.append(f"{o.format.value} 的修正單沒有開出來：{type(e).__name__}: {e}")

    # 哪些格式沒有自己的驗證：分數用中性渲染代替（要逐行比對中性渲染，問題也列進各頁）
    formats = list(dict.fromkeys(o.format for o in outputs))
    verified = {fr.format for fr in format_reports} | ({OutputFormat.PPTX} if target_engine is not None else set())
    proxies = [f for f in formats if f not in verified]
    neutral_scores = not formats or bool(proxies)
    neutral_engine = None
    neutral_lines: dict[int, line_check.PageLines] = {}
    if neutral_out is not None:
        try:
            pages, neutral_engine, neutral_lines = _neutral_verify(neutral_out, specs, styled, pages, cfg, store, checker if neutral_scores else None)
        except Exception as e:  # 中性渲染失敗不影響其他驗證
            notes.append(f"7A 中性驗證沒有執行：{type(e).__name__}: {e}")

    counts: dict[str, int] = {}
    for p in pages:
        for e in p.elements:
            for status in e.fields.values():
                counts[status.value] = counts.get(status.value, 0) + 1
    if neutral_scores:  # 中性渲染就是（某些格式的）評分依據：它的逐行問題列進各頁
        pages = [p.model_copy(update={"issues": p.issues + neutral_lines[p.page_index].issues}) if p.page_index in neutral_lines else p for p in pages]
    neutral_page = {p.page_index: (p.neutral_similarity * _line_factor(neutral_lines.get(p.page_index)) if p.neutral_similarity is not None else None)
                    for p in pages}
    per_format: dict[OutputFormat, dict[int, Optional[float]]] = {}
    for fmt in formats:
        if fmt == OutputFormat.PPTX and fmt in verified:
            per_format[fmt] = {p.page_index: _pptx_page_score(p, target_lines.get(p.page_index), c) for p in pages}
        elif fmt in verified:
            fr = next(r for r in format_reports if r.format == fmt)
            per_format[fmt] = {p.page_index: _readback_page_score(fr, p.page_index, format_lines.get((fmt, p.page_index)), neutral_page.get(p.page_index))
                               for p in pages}
        else:
            per_format[fmt] = dict(neutral_page)
    overall, summary = _combine(per_format, neutral_page, proxies, unverified, float(c.get("format_mean_weight", 0.1)))
    head = [summary] if summary else []
    for p in pages:
        if p.page_index in target_lines:
            head.append(f"第 {p.page_index + 1} 頁（{_WHERE[OutputFormat.PPTX]}）{target_lines[p.page_index].summary()}")
        if neutral_scores and p.page_index in neutral_lines:
            head.append(f"第 {p.page_index + 1} 頁（{_NEUTRAL_WHERE}）{neutral_lines[p.page_index].summary()}")
        for fr in format_reports:
            found = format_lines.get((fr.format, p.page_index))
            if found is not None:
                head.append(f"第 {p.page_index + 1} 頁（{_WHERE.get(fr.format, fr.format.value)}）{found.summary()}")
    notes = head + notes
    for o in outputs:
        for d in o.degradations:
            notes.append(f"{o.format.value}：{d.concept} → {d.action}")
    notes.extend(styled.notes)
    return FidelityReport(
        neutral_engine=neutral_engine, target_engine=target_engine, pages=pages, overall_score=overall,
        field_counts=counts, corrections_proposed=proposals, round=round_no, notes=notes, target_render=target_render,
        format_reports=format_reports,
    )


def _line_factor(found) -> float:
    """逐行分數；沒有做逐行比對、或這一頁沒有可判斷的行時是 1（只看畫面分數）。"""
    return found.score if found is not None and found.score is not None else 1.0


def _pptx_page_score(p: PageReport, found, c) -> float:
    """PowerPoint（LibreOffice 渲染）這一頁的分數：(0.5 × 相似度 ＋ 0.5 × 文字框一致率) × 逐行分數。渲染結果少了這一頁是 0。"""
    if p.target_similarity is None:
        return 0.0
    boxes = [e for e in p.elements if e.kind in ("text_box", "table")]
    good = [e for e in boxes if e.text_match and e.linebreak_match and (e.position_error_pt or 0.0) <= c["position_tolerance_pt"]]
    text_score = len(good) / len(boxes) if boxes else 1.0
    return (0.5 * p.target_similarity + 0.5 * text_score) * _line_factor(found)


def _readback_page_score(fr, page_index: int, found, fallback: Optional[float]) -> Optional[float]:
    """某個格式（回讀驗證）這一頁的分數：(0.5 × 相似度 ＋ 0.5 × 文字一致率，缺一個就只用另一個) × 逐行分數。
    輸出檔裡沒有這一頁（有 error 等級的問題、兩個數字都沒有）是 0；兩個數字都量不到（例如渲染器載入失敗）時用 fallback（中性渲染）。"""
    fp = next((x for x in fr.pages if x.page_index == page_index), None)
    if fp is None:
        return 0.0
    parts = [v for v in (fp.similarity, fp.text_match_ratio) if v is not None]
    if parts:
        return sum(parts) / len(parts) * _line_factor(found)
    if any(i.severity == Severity.ERROR for i in fp.issues):
        return 0.0
    return fallback


def _combine(per_format: dict, neutral_page: dict, proxies: list, unverified: dict, mean_weight: float) -> tuple[Optional[float], str]:
    """各格式的分數（各頁平均）→ 整體分數與一句說明。見模組開頭「整體分數」。"""
    doc_scores: dict = {}
    for fmt, by_page in per_format.items():
        vals = [v for v in by_page.values() if v is not None]
        if vals:
            doc_scores[fmt] = sum(vals) / len(vals)
    if not per_format:  # 沒有輸出檔：只看成品文件本身（中性渲染）
        vals = [v for v in neutral_page.values() if v is not None]
        return ((sum(vals) / len(vals)) if vals else None), ""
    parts = []
    for fmt in per_format:
        label = _LABEL.get(fmt, fmt.value)
        if fmt not in doc_scores:
            parts.append(f"{label} 沒有分數（{unverified.get(fmt, '沒有可用的驗證')}，中性渲染也沒有結果）")
        elif fmt in proxies:
            why = unverified.get(fmt, "這個格式還沒有自己的驗證")
            if "本身沒有" not in why:
                why += f"，{label}檔本身沒有驗證"
            parts.append(f"{label} {doc_scores[fmt]:.3f}（代用值：{why}；分數來自成品文件的中性渲染）")
        else:
            parts.append(f"{label} {doc_scores[fmt]:.3f}")
    if not doc_scores:
        return None, "各格式的分數：" + "；".join(parts) + "。"
    worst, mean = min(doc_scores.values()), sum(doc_scores.values()) / len(doc_scores)
    overall = worst + mean_weight * (mean - worst)
    text = "各格式的分數：" + "；".join(parts) + "。"
    if len(doc_scores) > 1:
        bad = min(doc_scores, key=doc_scores.get)
        text += (f"整體分數以最差的格式為準：最差（{_LABEL.get(bad, bad.value)} {worst:.3f}）＋ {mean_weight:g} ×（平均 {mean:.3f} − 最差），"
                 f"一個格式壞掉不會被其他格式的高分蓋過。")
    return overall, text


# ---------- s7.lines（逐行文字對照）----------


class _LineChecker:
    """把某一份渲染結果（已裁成與工作影像同一個範圍）交給 verify/lines.py 逐行比對。比對失敗只記在 notes，不影響其他驗證。"""

    def __init__(self, specs, elements, layout, styled, cfg: Config, store: Store, notes: list[str]):
        self.specs = {s.page_index: s for s in specs}
        self.elements = {e.page_index: e for e in elements}
        self.layout = {pl.page_index: pl for pl in layout.pages}
        self.styled = {sp.page_index: sp for sp in styled.pages}
        self.p = line_check.params(cfg)
        self.store = store
        self.notes = notes

    unrenderable: frozenset[str] = frozenset()  # 驗證用的這台電腦沒有字型畫得出來的字（由中性渲染的缺字紀錄得知）

    def check(self, page_index: int, rendered: Image.Image, where: str, relayout: bool = False, embeds_fonts: bool = False, glyphs=None):
        """relayout：渲染結果是替身引擎自己重新排字的（LibreOffice 代替 PowerPoint 或 Word、中性渲染）。
        embeds_fonts：輸出檔內嵌字型（可編輯 PDF）；沒有內嵌時，驗證用的電腦畫不出來的字所在的行不判斷。
        glyphs：從輸出檔讀回來的每個字的位置（輸出頁面 pt，lines.Glyph）；讀不到文字層時是 None，只靠影像判斷。"""
        spec, el, sp = self.specs.get(page_index), self.elements.get(page_index), self.styled.get(page_index)
        if spec is None or el is None or sp is None:
            return None
        try:
            working = Image.open(self.store.blob_path(spec.working_image))
            return line_check.check_page(working, rendered, el, self.layout.get(page_index), sp, source_frame(sp), self.p, where,
                                         relayout=relayout, unrenderable=frozenset() if embeds_fonts else self.unrenderable, glyphs=glyphs)
        except Exception as e:  # 逐行比對失敗：這一頁只看畫面分數
            self.notes.append(f"第 {page_index + 1} 頁（{where}）的逐行文字對照沒有執行：{type(e).__name__}: {e}")
            return None


def _with_lines(fr, format_lines):
    """把某個格式的逐行比對問題併進它的 FormatReport（各頁的 issues）；有找不到或疊在一起的行，這個格式就不算通過。"""
    pages = []
    bad = False
    for fp in fr.pages:
        found = format_lines.get((fr.format, fp.page_index))
        if found is None:
            pages.append(fp)
            continue
        bad = bad or any(i.severity == Severity.ERROR for i in found.issues)
        pages.append(fp.model_copy(update={"issues": fp.issues + found.issues}))
    notes = list(fr.notes) + [f"第 {pg + 1} 頁{found.summary()}" for (fmt, pg), found in sorted(format_lines.items(), key=lambda kv: kv[0][1]) if fmt == fr.format]
    return fr.model_copy(update={"pages": pages, "notes": notes, "ok": fr.ok and not bad})


def _read_back(reader, page_index: int, cfg: Config):
    """從一頁 PDF 讀回看得見的文字行（③的擷取函式）。回傳（文字行、頁寬 pt）。"""
    from ..verify import rendered

    return rendered.read_page(reader, page_index, cfg)


# ---------- s7.fields ----------


def _field_report(spec: PageSpec, el: ElementList, layout: Layout, styled: StyledDoc) -> PageReport:
    page = spec.page_index
    lines = {t.id: t for t in el.texts}
    pl = layout.pages[page]
    pars = {p.id: p for p in pl.paragraphs}
    sp = styled.pages[page]
    reports: list[ElementReport] = []
    issues: list[Issue] = []
    for tb in sp.text_boxes:
        src_runs = [r for pid in next(b for b in pl.text_boxes if b.id == tb.id).paragraph_ids for lid in pars[pid].line_ids for r in lines[lid].runs]
        runs = [r for p in tb.paragraphs for r in p.runs]
        box_lines = [lines[lid] for pid in next(b for b in pl.text_boxes if b.id == tb.id).paragraph_ids for lid in pars[pid].line_ids]
        text_status = FieldStatus.EXACT if el.production_line.value == "extract" else FieldStatus.MEASURED
        if any(ln.ai_chars for ln in box_lines):
            text_status = FieldStatus.AI
        if any(p.text_locked for p in tb.paragraphs):
            text_status = FieldStatus.USER
        fields = {
            "text": text_status,
            "position": FieldStatus.EXACT if el.production_line.value == "extract" else FieldStatus.MEASURED,
            "font": _STATUS[min((r.font.family for r in runs), key=lambda m: m.confidence).source] if runs else FieldStatus.UNRECOVERABLE,
            "font_size": _STATUS[min((r.size_pt for r in runs), key=lambda m: m.confidence).source] if runs else FieldStatus.UNRECOVERABLE,
            "color": _STATUS[min((r.color for r in src_runs), key=lambda m: m.confidence).source] if src_runs else FieldStatus.UNRECOVERABLE,
            "paragraphs": FieldStatus.AI if any(pars[p.id].role.source == Provenance.AI for p in tb.paragraphs if p.id in pars) else FieldStatus.MEASURED,  # 段落是④從行距與行寬推出來的，檔案裡沒有
            "line_spacing": FieldStatus.MEASURED,
        }
        substituted = any(r.font.is_substituted for r in runs)
        conf = min([r.font.family.confidence for r in runs] + [r.size_pt.confidence for r in runs] + [pars[pid].alignment.confidence for pid in pars if pid in {p.id for p in tb.paragraphs}] or [1.0])
        reports.append(ElementReport(element_id=tb.id, kind="text_box", fields=fields, font_substituted=substituted, confidence=conf))
        if substituted:
            names = sorted({f"{r.font.family.value} → {r.font.verification_family}" for r in runs if r.font.is_substituted})
            issues.append(Issue(severity=Severity.INFO, code="font_substituted", element_id=tb.id, message="驗證時用替代字型：" + "、".join(names) + "。輸出檔仍寫原字型名稱，你的電腦若沒有該字型，開啟時會被換掉。"))
    for t in sp.tables:
        lt = next(x for x in pl.tables if x.id == t.id)
        tconf = lt.confidence
        reports.append(ElementReport(element_id=t.id, kind="table", fields={
            "text": FieldStatus.EXACT if el.production_line.value == "extract" else FieldStatus.MEASURED,
            "structure": FieldStatus.MEASURED,  # 列欄與合併儲存格是④從格線推出來的
            "borders": FieldStatus.EXACT if el.production_line.value == "extract" else FieldStatus.MEASURED,
            "number_format": FieldStatus.MEASURED,
        }, confidence=tconf))
    for im in sp.images:
        reports.append(ElementReport(element_id=im.id, kind="image", fields={"pixels": FieldStatus.EXACT if el.production_line.value == "extract" else FieldStatus.MEASURED, "position": FieldStatus.EXACT if el.production_line.value == "extract" else FieldStatus.MEASURED}))
    for s in sp.shapes:
        reports.append(ElementReport(element_id=s.id, kind="shape", fields={"geometry": FieldStatus.EXACT, "fill": FieldStatus.EXACT, "position": FieldStatus.EXACT}))
    src_vectors = {v.id: v for v in el.vectors}
    for v in sp.vectors:
        # 向量圖的來源：原生 PDF 裡本來就是向量（精確擷取），或由像素描圖重建（量測）。以③記的來源類型為準；
        # ③沒有這個向量圖時（例如使用者修正後新增）退回成品文件記的 origin。
        src = src_vectors.get(v.id)
        status = _STATUS[src.origin.source] if src is not None else (FieldStatus.EXACT if v.origin == "pdf_paths" else FieldStatus.MEASURED)
        fields = {"paths": status, "fill": status, "position": status}
        if any(vp.stroke is not None for vp in v.paths):
            fields["stroke"] = status
        if any(vp.gradient is not None for vp in v.paths):
            fields["gradient"] = status
        reports.append(ElementReport(element_id=v.id, kind="vector", fields=fields, confidence=min(v.confidence, src.origin.confidence) if src is not None else v.confidence))
        if status != FieldStatus.EXACT:
            detail = f"，重畫後與原圖的平均色差 ΔE {v.render_error:.1f}" if v.render_error is not None else ""
            issues.append(Issue(severity=Severity.INFO, code="vector_traced", element_id=v.id, message=f"這個向量圖是由像素描圖重建的（{len(v.paths)} 條路徑{detail}），不是原檔裡的向量資料。"))
    if el.background.generated_regions:
        issues.append(Issue(severity=Severity.WARNING, code="background_generated", message=f"底圖有 {len(el.background.generated_regions)} 個區域是生成（修補）出來的。"))
    skew = page_skew(el)
    if skew is not None:
        issues.append(Issue(severity=Severity.WARNING, code="page_skewed", message=(
            f"原圖的文字行一致歪了約 {abs(skew):.1f}°（{'順' if skew > 0 else '逆'}時針），頁面沒有被轉正：成品的文字行是水平的，"
            "與原圖比對時每行兩端會有偏移。如果原圖是掃描或照片，可以在修正畫面手動拉出頁面四角，讓系統轉正後重新辨識。")))
    for n in el.notes + spec.notes:
        issues.append(Issue(severity=Severity.INFO, code="note", message=n))
    return PageReport(page_index=page, elements=reports, issues=issues)


PAGE_SKEW_MIN_DEG = 0.3  # 多數文字行一致偏斜超過這麼多度，才提醒頁面沒有被轉正
PAGE_SKEW_AGREE = 0.6  # 至少這個比例的行偏斜角度相同（±0.25°）：避免把斜放的標題、圖片裡的字當成整頁歪了


def page_skew(el: ElementList) -> Optional[float]:
    """影像輸入的頁面歪了幾度（③量到的文字行基線，中位數；y 向下，正值是順時針）。不是影像輸入、可判斷的行太少、
    或各行角度不一致時回傳 None。只看橫排、至少 8 個字的行。"""
    if el.production_line.value == "extract":
        return None
    angles = []
    for t in el.texts:
        if t.hidden or t.direction.value != "horizontal" or len(t.text.strip()) < 8:
            continue
        (a, b) = t.baseline
        if math.hypot(b.x - a.x, b.y - a.y) <= 1e-6:
            continue
        angles.append(math.degrees(math.atan2(b.y - a.y, b.x - a.x)))
    if len(angles) < 3:
        return None
    m = float(np.median(angles))
    agree = sum(1 for v in angles if abs(v - m) <= 0.25) / len(angles)
    return m if abs(m) >= PAGE_SKEW_MIN_DEG and agree >= PAGE_SKEW_AGREE else None


# ---------- s7.neutral（7A）----------


def _neutral_verify(out: OutputFile, specs, styled, pages, cfg: Config, store: Store, checker=None):
    """7A 中性驗證：用自家確定性的渲染（給 Illustrator 的 PDF 輸出器，ReportLab 加開放字型）把成品文件畫出來，
    與工作影像比對。它不依賴任何辦公軟體，所以檢查的是③④⑤的結果本身：位置、字級、字型寬度、斷行。
    不能檢查目標軟體的重新斷行（那是 7B 的工作）。out 是 run 先輸出好的中性渲染 PDF。
    有 checker 時也做逐行文字對照，結果另外回傳（run 只在有格式要拿它當代用值時才傳 checker，並把它的問題列進報告）。"""
    c = cfg.section("verify")
    engine = "Mortise 中性渲染（ReportLab，開放授權字型）"
    new_pages = []
    found_lines: dict[int, line_check.PageLines] = {}
    pdoc = pdf.open_document(str(store.blob_path(out.file)))
    try:
        for spec, report in zip(specs, pages):
            if spec.page_index >= len(pdoc):
                new_pages.append(report)
                continue
            sp = styled.pages[spec.page_index]
            reader = pdf.PageReader(pdoc, spec.page_index)
            try:
                image = reader.render(verify_dpi(c["render_dpi"], sp, spec))
                glyphs = line_check.glyphs_from_lines(*_read_back(reader, spec.page_index, cfg)) if checker is not None else None
            finally:
                reader.close()
            working = Image.open(store.blob_path(spec.working_image))  # 白紙類頁面比去光照後的影像；其他頁面工作影像就是原圖
            region = content_region(image, sp)
            sim = similarity(working, region)  # 有輸出尺寸時只比原始內容那一塊
            if checker is not None:
                found = checker.check(spec.page_index, region, _NEUTRAL_WHERE, relayout=True, glyphs=glyphs)
                if found is not None:
                    found_lines[spec.page_index] = found
            issues = list(report.issues)
            if sim < c.get("neutral_similarity_pass", 0.95):
                issues.append(Issue(severity=Severity.WARNING, code="neutral_low_similarity", message=f"中性渲染與原圖的相似度 {sim:.3f} 偏低：辨識、結構或設定可能有誤。"))
            new_pages.append(report.model_copy(update={"neutral_similarity": sim, "issues": issues}))
    finally:
        pdoc.close()
    return new_pages, engine, found_lines


# ---------- s7.target、s7.compare、s7.propose ----------


def _target_verify(rendered_pdf: bytes, specs, elements, layout, styled, pages, cfg: Config, store: Store, round_no: int, checker=None):
    c = cfg.section("verify")
    rc = cfg.section("recognize")
    proposals: list[Correction] = []
    out_pages: list[PageReport] = []
    found_lines: dict[int, line_check.PageLines] = {}
    with tempfile.NamedTemporaryFile(suffix=".pdf") as tmp:
        tmp.write(rendered_pdf)
        tmp.flush()
        pdoc = pdf.open_document(tmp.name)
        try:
            for spec, el, report in zip(specs, elements, pages):
                if spec.page_index >= len(pdoc):
                    out_pages.append(report.model_copy(update={"issues": report.issues + [Issue(severity=Severity.ERROR, code="page_missing", message="輸出檔渲染後少了這一頁。")]}))
                    continue
                sp = styled.pages[spec.page_index]
                reader = pdf.PageReader(pdoc, spec.page_index)
                try:
                    boxes = reader.boxes()
                    matrix, dw, dh = s2_page.pdf_matrix(boxes.get("crop", boxes["media"]), reader.rotation())
                    image = reader.render(verify_dpi(c["render_dpi"], sp, spec))
                    r_lines = [t for t in s3_recognize.read_text_lines(reader, spec.page_index, matrix, dw, rc) if not t.hidden]
                finally:
                    reader.close()
                working = Image.open(store.blob_path(spec.working_image))  # 白紙類頁面比去光照後的影像；其他頁面工作影像就是原圖
                region = content_region(image, sp)
                sim = similarity(working, region)  # 有輸出尺寸時只比原始內容那一塊
                found = (checker.check(spec.page_index, region, _WHERE[OutputFormat.PPTX], relayout=True, glyphs=line_check.glyphs_from_lines(r_lines, dw))
                         if checker is not None else None)
                if found is not None:
                    found_lines[spec.page_index] = found
                w = sp.width_pt
                k, ox, oy = source_frame(sp)  # 來源文字行（原始頁面的相對座標）→ 輸出頁面 pt
                pl = layout.pages[spec.page_index]
                src_lines = {t.id: t for t in el.texts}
                pars = {p.id: p for p in pl.paragraphs}
                assigned = _assign(r_lines, sp, w)
                new_elements = []
                issues = list(report.issues)
                for e in report.elements:
                    if e.kind == "table":
                        lt = next(x for x in pl.tables if x.id == e.element_id)
                        wrong = []
                        for cell in lt.cells:
                            expect_c = "".join(src_lines[lid].text for pid in cell.paragraph_ids for lid in pars[pid].line_ids)
                            got_c = "".join(t.text for t in sorted(assigned.get(f"{e.element_id}#{cell.row},{cell.col}", []), key=lambda t: (round(t.baseline[0].y, 4), t.baseline[0].x)))
                            if _norm(expect_c) != _norm(got_c):
                                wrong.append(f"第 {cell.row + 1} 列第 {cell.col + 1} 欄")
                        new_elements.append(e.model_copy(update={"text_match": not wrong, "linebreak_match": not wrong}))
                        if wrong:
                            issues.append(Issue(severity=Severity.ERROR, code="table_cell_mismatch", element_id=e.element_id, message=f"表格有 {len(wrong)} 格的文字與原圖不一致：" + "、".join(wrong[:6]) + ("…" if len(wrong) > 6 else "")))
                        continue
                    if e.kind != "text_box":
                        new_elements.append(e)
                        continue
                    lbox = next(b for b in pl.text_boxes if b.id == e.element_id)
                    expect: list[TextLine] = [src_lines[lid] for pid in lbox.paragraph_ids for lid in pars[pid].line_ids]
                    got = sorted(assigned.get(e.element_id, []), key=lambda t: (round(t.baseline[0].y, 4), t.baseline[0].x))
                    stb = next(t for t in sp.text_boxes if t.id == e.element_id)
                    if any(p.text_locked for p in stb.paragraphs):
                        # 使用者改過文字：以成品文件的文字為準，斷行只比行數
                        want = _norm("".join(r.text for p in stb.paragraphs for r in p.runs))
                        text_ok = want == _norm("".join(t.text for t in got))
                        breaks_ok = text_ok and len(got) == sum(p.source_line_count for p in stb.paragraphs)
                    else:
                        text_ok = _norm("".join(t.text for t in expect)) == _norm("".join(t.text for t in got))
                        breaks_ok = text_ok and len(expect) == len(got) and all(_norm(a.text) == _norm(b.text) for a, b in zip(expect, got))
                    pos = None
                    dx = dy = 0.0
                    if got:
                        dx = (ox + expect[0].baseline[0].x * k) - got[0].baseline[0].x * w
                        dy = (oy + expect[0].baseline[0].y * k) - got[0].baseline[0].y * w
                        pos = math.hypot(dx, dy)
                    new_elements.append(e.model_copy(update={"text_match": text_ok, "linebreak_match": breaks_ok, "position_error_pt": pos}))
                    if not got:
                        issues.append(Issue(severity=Severity.ERROR, code="text_missing", element_id=e.element_id, message="這個文字框在輸出檔的渲染結果中找不到。"))
                    elif not text_ok:
                        issues.append(Issue(severity=Severity.ERROR, code="text_mismatch", element_id=e.element_id, message="輸出檔渲染出來的文字與原圖不一致。"))
                    elif not breaks_ok:
                        if len(expect) != len(got):
                            why = f"原圖 {len(expect)} 行，輸出檔渲染為 {len(got)} 行"
                        else:
                            k_bad = next((n for n, (a, b) in enumerate(zip(expect, got)) if _norm(a.text) != _norm(b.text)), 0)
                            why = f"行數相同（{len(got)} 行），但從第 {k_bad + 1} 行起每行的字不同（在不同的地方換行）"
                        issues.append(Issue(severity=Severity.WARNING, code="linebreak_mismatch", element_id=e.element_id, message=f"斷行不一致：{why}。"))
                        if round_no < c["max_rounds"]:
                            size = max((r.size_pt.value for p in next(t for t in sp.text_boxes if t.id == e.element_id).paragraphs for r in p.runs), default=12.0)
                            delta = (0.35 if len(got) > len(expect) else -0.35) * size * (round_no + 1)
                            proposals.append(Correction(
                                id=f"v{round_no + 1}-{e.element_id}", target_stage=6, target_step="s6.pptx", page_index=spec.page_index, element_id=e.element_id,
                                field="pptx.width_adjust_pt", new_value=round(delta, 2), instruction="斷行與原圖不一致，調整文字框寬度後重試。",
                                source=CorrectionSource.VERIFY, round=round_no + 1,
                            ))
                    elif pos is not None and pos > c["position_tolerance_pt"]:
                        issues.append(Issue(severity=Severity.WARNING, code="position_offset", element_id=e.element_id, message=f"位置與原圖相差 {pos:.1f} pt。"))
                    if breaks_ok and pos is not None and pos > c["position_fix_threshold_pt"] and round_no < c["max_rounds"]:
                        # 文字與斷行都對，只是整個框偏了（各引擎放第一行的位置不同）：把框移回去
                        proposals.append(Correction(
                            id=f"v{round_no + 1}-{e.element_id}-offset", target_stage=6, target_step="s6.pptx", page_index=spec.page_index, element_id=e.element_id,
                            field="pptx.offset_pt", new_value=[round(dx, 3), round(dy, 3)], instruction="渲染後第一行基線與原圖有偏移，平移文字框補正。",
                            source=CorrectionSource.VERIFY, round=round_no + 1,
                        ))
                if sim < c["similarity_pass"]:
                    issues.append(Issue(severity=Severity.WARNING, code="low_similarity", message=f"整頁相似度 {sim:.3f}，低於合格線 {c['similarity_pass']}。"))
                if found is not None:
                    issues.extend(found.issues)
                out_pages.append(report.model_copy(update={"target_similarity": sim, "elements": new_elements, "issues": issues}))
        finally:
            pdoc.close()
    return out_pages, proposals, found_lines


def _assign(lines: list[TextLine], page, w: float) -> dict[str, list[TextLine]]:
    """把渲染結果的每一行，分給它所在的文字框或表格儲存格（旋轉的框先轉回框自己的座標再判斷）。
    表格儲存格的鍵是「表格編號#列,欄」。"""
    frames = []  # （鍵、中心 x、中心 y、寬、高、角度）
    for tb in page.text_boxes:
        b = tb.box
        frames.append((tb.id, b.x + b.w / 2, b.y + b.h / 2, b.w, b.h, math.radians(b.rotation)))
    for t in page.tables:
        xs = [t.box.x]
        for cw in t.col_widths_pt:
            xs.append(xs[-1] + cw)
        ys = [t.box.y]
        for rh in t.row_heights_pt:
            ys.append(ys[-1] + rh)
        for cell in t.cells:
            x0, x1 = xs[cell.col], xs[cell.col + cell.col_span]
            y0, y1 = ys[cell.row], ys[cell.row + cell.row_span]
            frames.append((f"{t.id}#{cell.row},{cell.col}", (x0 + x1) / 2, (y0 + y1) / 2, x1 - x0, y1 - y0, 0.0))
    out: dict[str, list[TextLine]] = {}
    for line in lines:
        px, py = line.quad.center.x * w, line.quad.center.y * w
        size = line.font_size * w
        best, best_d = None, None
        for key, cx, cy, fw, fh, ang in frames:
            dx, dy = px - cx, py - cy
            lx = dx * math.cos(ang) + dy * math.sin(ang)  # 轉回框自己的座標
            ly = -dx * math.sin(ang) + dy * math.cos(ang)
            d = math.hypot(max(0.0, abs(lx) - fw / 2), max(0.0, abs(ly) - fh / 2))
            if best_d is None or d < best_d:
                best, best_d = key, d
        if best is not None and best_d <= max(2.0 * size, 0.01 * w):
            out.setdefault(best, []).append(line)
    return out
