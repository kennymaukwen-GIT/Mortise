"""⑦ 驗證：收輸出檔與工作影像，交出還原度報告 FidelityReport 與修正單。

放在輸出之後，分兩道：
  7A 中性驗證（s7.neutral）：用自家確定性的渲染把成品文件畫出來，檢查③④⑤對不對。尚未實作。
  7B 目標驗證（s7.target）：用目標軟體或替身引擎渲染輸出檔，檢查轉檔後有沒有跑版。
     目前的替身引擎是 LibreOffice（不是使用者手上的 PowerPoint，報告中會註明）。
其他步驟：
  s7.compare   逐區比對：位置誤差、文字是否一致、斷行是否一致、整頁相似度
  s7.fields    每個元素每個欄位標示 精確擷取／量測吸附／AI 判斷／生成／使用者／無法還原
  s7.propose   開修正單（目前只有：斷行不一致 → 調整文字框寬度，送給⑤）
"""

from __future__ import annotations

import math
import re
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
VERSION = "2"

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


def run(
    doc: SourceDoc, specs: list[PageSpec], elements: list[ElementList], layout: Layout, styled: StyledDoc,
    outputs: list[OutputFile], cfg: Config, store: Store, round_no: int = 0,
) -> FidelityReport:
    c = cfg.section("verify")
    notes: list[str] = ["7A 中性驗證尚未實作；目前的分數全部來自 7B 目標驗證。"]
    pages = [_field_report(spec, el, layout, styled) for spec, el in zip(specs, elements)]
    target_engine = None
    proposals: list[Correction] = []

    pptx = next((o for o in outputs if o.format == OutputFormat.PPTX), None)
    if pptx is not None and libreoffice.available():
        try:
            rendered = libreoffice.to_pdf(store.blob_path(pptx.file), c["libreoffice_timeout_s"])
            target_engine = f"{libreoffice.version()}（替身引擎，不是 PowerPoint；斷行與字距可能與 PowerPoint 不同）"
            pages, proposals = _target_verify(rendered, specs, elements, layout, styled, pages, cfg, store, round_no)
        except libreoffice.RenderError as e:
            notes.append(f"7B 目標驗證沒有執行：{e}")
    elif pptx is not None:
        notes.append("7B 目標驗證沒有執行：這台機器沒有安裝 LibreOffice。")

    counts: dict[str, int] = {}
    for p in pages:
        for e in p.elements:
            for status in e.fields.values():
                counts[status.value] = counts.get(status.value, 0) + 1
    scores = []
    for p in pages:
        if p.target_similarity is None:
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
        target_engine=target_engine, pages=pages, overall_score=(sum(scores) / len(scores)) if scores else None,
        field_counts=counts, corrections_proposed=proposals, round=round_no, notes=notes,
    )


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
        fields = {
            "text": FieldStatus.EXACT if el.production_line.value == "extract" else FieldStatus.MEASURED,
            "position": FieldStatus.EXACT if el.production_line.value == "extract" else FieldStatus.MEASURED,
            "font": _STATUS[min((r.font.family for r in runs), key=lambda m: m.confidence).source] if runs else FieldStatus.UNRECOVERABLE,
            "font_size": _STATUS[min((r.size_pt for r in runs), key=lambda m: m.confidence).source] if runs else FieldStatus.UNRECOVERABLE,
            "color": _STATUS[min((r.color for r in src_runs), key=lambda m: m.confidence).source] if src_runs else FieldStatus.UNRECOVERABLE,
            "paragraphs": FieldStatus.MEASURED,  # 段落是④從行距與行寬推出來的，檔案裡沒有
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
    if el.background.generated_regions:
        issues.append(Issue(severity=Severity.WARNING, code="background_generated", message=f"底圖有 {len(el.background.generated_regions)} 個區域是生成（修補）出來的。"))
    for n in el.notes + spec.notes:
        issues.append(Issue(severity=Severity.INFO, code="note", message=n))
    return PageReport(page_index=page, elements=reports, issues=issues)


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
                reader = pdf.PageReader(pdoc, spec.page_index)
                try:
                    boxes = reader.boxes()
                    matrix, dw, dh = s2_page.pdf_matrix(boxes.get("crop", boxes["media"]), reader.rotation())
                    image = reader.render(c["render_dpi"])
                    r_lines = [t for t in s3_recognize.read_text_lines(reader, spec.page_index, matrix, dw, rc) if not t.hidden]
                finally:
                    reader.close()
                working = Image.open(store.blob_path(spec.working_image))
                sim = similarity(working, image)
                sp = styled.pages[spec.page_index]
                w = sp.width_pt
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
                    text_ok = _norm("".join(t.text for t in expect)) == _norm("".join(t.text for t in got))
                    breaks_ok = text_ok and len(expect) == len(got) and all(_norm(a.text) == _norm(b.text) for a, b in zip(expect, got))
                    pos = None
                    dx = dy = 0.0
                    if got:
                        dx = (expect[0].baseline[0].x - got[0].baseline[0].x) * w
                        dy = (expect[0].baseline[0].y - got[0].baseline[0].y) * w
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
                                id=f"v{round_no + 1}-{e.element_id}", target_stage=5, target_step="s5.box", page_index=spec.page_index, element_id=e.element_id,
                                field="width_adjust_pt", new_value=round(delta, 2), instruction="斷行與原圖不一致，調整文字框寬度後重試。",
                                source=CorrectionSource.VERIFY, round=round_no + 1,
                            ))
                    elif pos is not None and pos > c["position_tolerance_pt"]:
                        issues.append(Issue(severity=Severity.WARNING, code="position_offset", element_id=e.element_id, message=f"位置與原圖相差 {pos:.1f} pt。"))
                    if breaks_ok and pos is not None and pos > c["position_fix_threshold_pt"] and round_no < c["max_rounds"]:
                        # 文字與斷行都對，只是整個框偏了（各引擎放第一行的位置不同）：把框移回去
                        proposals.append(Correction(
                            id=f"v{round_no + 1}-{e.element_id}-offset", target_stage=5, target_step="s5.box", page_index=spec.page_index, element_id=e.element_id,
                            field="offset_pt", new_value=[round(dx, 3), round(dy, 3)], instruction="渲染後第一行基線與原圖有偏移，平移文字框補正。",
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
