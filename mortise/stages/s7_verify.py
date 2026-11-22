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

整體分數（overall_score）是各頁分數的平均。每一頁的分數依下面的順序取第一個算得出來的：
  1. 有 PowerPoint 的目標驗證：0.5 × 目標渲染的相似度 ＋ 0.5 × 文字框與表格中「文字一致、斷行一致、位置在容許差內」的比例。
  2. 沒有 PowerPoint 的目標驗證、但有其他格式的回讀驗證（例如可編輯 PDF、SVG）：
     每個格式在這一頁的分數 = 0.5 × 回讀渲染的相似度 ＋ 0.5 × 文字一致率（兩者缺一個就只用另一個），
     這一頁的分數是各格式分數的平均。
  3. 都沒有：7A 中性渲染的相似度。
"""

from __future__ import annotations

import math
import re
import sys
import tempfile
from pathlib import Path

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
from . import s2_page, s3_recognize

NAME = "s7"
VERSION = "4"

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


def verify_dpi(dpi: float, page, spec: PageSpec | None) -> float:
    """比對用的渲染解析度。有 placement 時依縮放比例調整，讓原始內容那一塊的像素數與不縮放時相同
    （縮小到小紙張時不會因為像素太少而失真，放大到大海報時也不必畫出巨大的影像）；調整幅度限制在 4 倍以內。"""
    pl = getattr(page, "placement", None)
    if pl is None or spec is None or pl.w <= 0:
        return dpi
    s = pl.w / max(spec.page_width_pt.value, 1e-6)
    return dpi * min(4.0, max(0.25, 1.0 / s))


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
    c = cfg.section("verify")
    notes: list[str] = []
    pages = [_field_report(spec, el, layout, styled) for spec, el in zip(specs, elements)]
    neutral_engine = None
    if c.get("neutral", True):
        try:
            pages, neutral_engine = _neutral_verify(specs, elements, layout, styled, pages, cfg, store)
        except Exception as e:  # 中性渲染失敗不影響其他驗證
            notes.append(f"7A 中性驗證沒有執行：{type(e).__name__}: {e}")
    target_engine = None
    target_render = None
    proposals: list[Correction] = []

    pptx = next((o for o in outputs if o.format == OutputFormat.PPTX), None)
    if pptx is not None and libreoffice.available():
        try:
            rendered = libreoffice.to_pdf(store.blob_path(pptx.file), c["libreoffice_timeout_s"])
            target_render = store.put_blob(rendered, "application/pdf", "pdf")
            target_engine = f"{libreoffice.version()}（替身引擎，不是 PowerPoint；斷行與字距可能與 PowerPoint 不同）"
            pages, proposals = _target_verify(rendered, specs, elements, layout, styled, pages, cfg, store, round_no)
        except libreoffice.RenderError as e:
            notes.append(f"7B 目標驗證沒有執行：{e}")
    elif pptx is not None:
        notes.append("7B 目標驗證沒有執行：這台機器沒有安裝 LibreOffice。")

    # 其他格式的目標驗證（mortise/verify/ 下各格式一個模組）
    from .. import verify as targets

    format_reports = []
    for o in outputs:
        fn = targets.verifier(o.format)
        if fn is None:
            continue
        try:
            format_reports.append(fn(o, specs, elements, layout, styled, cfg, store))
        except Exception as e:  # 單一格式的驗證失敗不影響其他驗證
            notes.append(f"{o.format.value} 的目標驗證沒有執行：{type(e).__name__}: {e}")
            continue
        # 驗證模組可以另外提供 propose：回傳要送給⑥該格式輸出器的修正單（欄位名稱以「格式.」開頭）。
        # 與 PowerPoint 的修正單一樣只影響那個格式的輸出，不改成品文件；要不要保留由執行器看整體分數有沒有進步。
        propose = getattr(sys.modules.get(fn.__module__), "propose", None)
        if propose is not None:
            try:
                proposals.extend(propose(o, specs, elements, layout, styled, cfg, store, round_no))
            except Exception as e:
                notes.append(f"{o.format.value} 的修正單沒有開出來：{type(e).__name__}: {e}")

    counts: dict[str, int] = {}
    for p in pages:
        for e in p.elements:
            for status in e.fields.values():
                counts[status.value] = counts.get(status.value, 0) + 1
    scores = []
    for p in pages:
        if p.target_similarity is None:
            readback = _readback_score(format_reports, p.page_index)
            if readback is not None:
                scores.append(readback)
            elif p.neutral_similarity is not None:
                scores.append(p.neutral_similarity)
            continue
        boxes = [e for e in p.elements if e.kind in ("text_box", "table")]
        good = [e for e in boxes if e.text_match and e.linebreak_match and (e.position_error_pt or 0.0) <= c["position_tolerance_pt"]]
        text_score = len(good) / len(boxes) if boxes else 1.0
        scores.append(0.5 * p.target_similarity + 0.5 * text_score)
    for o in outputs:
        for d in o.degradations:
            notes.append(f"{o.format.value}：{d.concept} → {d.action}")
    notes.extend(styled.notes)
    return FidelityReport(
        neutral_engine=neutral_engine, target_engine=target_engine, pages=pages, overall_score=(sum(scores) / len(scores)) if scores else None,
        field_counts=counts, corrections_proposed=proposals, round=round_no, notes=notes, target_render=target_render,
        format_reports=format_reports,
    )


def _readback_score(format_reports, page_index: int):
    """沒有 PowerPoint 目標驗證時，這一頁的分數取自各格式的回讀驗證：
    每個格式 0.5 × 相似度 ＋ 0.5 × 文字一致率（缺一個就只用另一個），再對各格式取平均。沒有任何格式驗到這一頁就回傳 None。"""
    per_format = []
    for fr in format_reports:
        for fp in fr.pages:
            if fp.page_index != page_index:
                continue
            parts = [v for v in (fp.similarity, fp.text_match_ratio) if v is not None]
            if parts:
                per_format.append(sum(parts) / len(parts))
    return (sum(per_format) / len(per_format)) if per_format else None


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
    for n in el.notes + spec.notes:
        issues.append(Issue(severity=Severity.INFO, code="note", message=n))
    return PageReport(page_index=page, elements=reports, issues=issues)


# ---------- s7.neutral（7A）----------


def _neutral_verify(specs, elements, layout, styled, pages, cfg: Config, store: Store):
    """7A 中性驗證：用自家確定性的渲染（給 Illustrator 的 PDF 輸出器，ReportLab 加開放字型）把成品文件畫出來，
    與工作影像比對。它不依賴任何辦公軟體，所以檢查的是③④⑤的結果本身：位置、字級、字型寬度、斷行。
    不能檢查目標軟體的重新斷行（那是 7B 的工作）。"""
    from . import s6_export
    from ..artifacts import OutputFormat

    c = cfg.section("verify")
    out = s6_export.run(styled, OutputFormat.AI_PDF, "neutral", cfg, store)
    engine = "Mortise 中性渲染（ReportLab，開放授權字型）"
    new_pages = []
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
            finally:
                reader.close()
            working = Image.open(store.blob_path(spec.working_image))  # 白紙類頁面比去光照後的影像；其他頁面工作影像就是原圖
            sim = similarity(working, content_region(image, sp))  # 有輸出尺寸時只比原始內容那一塊
            issues = list(report.issues)
            if sim < c.get("neutral_similarity_pass", 0.95):
                issues.append(Issue(severity=Severity.WARNING, code="neutral_low_similarity", message=f"中性渲染與原圖的相似度 {sim:.3f} 偏低：辨識、結構或設定可能有誤。"))
            new_pages.append(report.model_copy(update={"neutral_similarity": sim, "issues": issues}))
    finally:
        pdoc.close()
    return new_pages, engine


# ---------- s7.target、s7.compare、s7.propose ----------


def _target_verify(rendered_pdf: bytes, specs, elements, layout, styled, pages, cfg: Config, store: Store, round_no: int):
    c = cfg.section("verify")
    rc = cfg.section("recognize")
    proposals: list[Correction] = []
    out_pages: list[PageReport] = []
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
                sim = similarity(working, content_region(image, sp))  # 有輸出尺寸時只比原始內容那一塊
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
                        issues.append(Issue(severity=Severity.WARNING, code="linebreak_mismatch", element_id=e.element_id, message=f"斷行不一致：原圖 {len(expect)} 行，輸出檔渲染為 {len(got)} 行。"))
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
                out_pages.append(report.model_copy(update={"target_similarity": sim, "elements": new_elements, "issues": issues}))
        finally:
            pdoc.close()
    return out_pages, proposals


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
