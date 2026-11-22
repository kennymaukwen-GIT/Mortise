"""可編輯 PDF 的端到端測試：五個 fixture 以 outputs=[pdf] 跑整條流水線，用回讀驗證（verify/pdf_target.py）檢查
輸出檔讀回來的文字、位置、相似度與結構標記；以及⑦整體分數與元素報告的行為。"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pikepdf
import pypdfium2 as pdfium
import pytest

from mortise.artifacts import ElementList, FidelityReport, Intent, OutputFile, OutputFormat, PageSpec, StyledDoc
from mortise.config import Config
from mortise.pipeline import Pipeline
from mortise.tools import pdfium as mp
from mortise.verify import pdf_target

FIXTURES = Path(__file__).parent / "fixtures"
NAMES = ["slide_basic", "slide_dark", "deck", "doc_a4", "poster"]


@pytest.fixture(scope="module", params=NAMES)
def job(request, tmp_path_factory):
    pipe = Pipeline(tmp_path_factory.mktemp(f"pdf-{request.param}"))
    src = FIXTURES / f"{request.param}.pdf"
    pipe.start(src, src.name, Intent(outputs=[OutputFormat.PDF]))
    report = pipe.run()
    return request.param, pipe, report


def _pdf_report(report: FidelityReport):
    return next(f for f in report.format_reports if f.format == OutputFormat.PDF)


def test_native_fixture_reads_back_exactly(job):
    name, pipe, report = job
    out = pipe.store.load("s6/pdf", OutputFile)
    assert out.selfcheck_ok, out.selfcheck_notes
    fr = _pdf_report(report)
    assert "PDFium" in fr.engine and "不是 Acrobat" in fr.engine
    styled = pipe.store.load("s5/styled_doc", StyledDoc)
    assert len(fr.pages) == len(styled.pages)
    for pg in fr.pages:
        errors = [i for i in pg.issues if i.severity.value == "error"]
        assert not errors, [(i.code, i.message) for i in errors]
        assert pg.text_match_ratio == 1.0  # 讀回來的文字與成品文件逐行完全一致
        assert pg.position_error_pt is not None and pg.position_error_pt <= 0.5  # 每個字都在排版算定的位置上
        assert pg.similarity is not None and pg.similarity > 0.98
    assert fr.ok


def test_overall_score_uses_readback_when_no_powerpoint(job):
    _, _, report = job
    fr = _pdf_report(report)
    want = sum(0.5 * pg.similarity + 0.5 * pg.text_match_ratio for pg in fr.pages) / len(fr.pages)
    assert report.target_engine is None  # 沒有要求 PowerPoint，就沒有 PowerPoint 的目標驗證
    assert report.overall_score == pytest.approx(want, abs=1e-9)


def _source_hits(pipe, page_index: int):
    """輸出檔讀回來的文字行，與③讀到的來源文字行比對。"""
    out = pipe.store.load("s6/pdf", OutputFile)
    el = pipe.store.load(f"s3/page-{page_index + 1:03d}", ElementList)
    pdoc = mp.open_document(str(pipe.store.blob_path(out.file)))
    reader = mp.PageReader(pdoc, page_index)
    try:
        got, width = pdf_target.read_lines(reader, page_index, pipe.cfg)
    finally:
        reader.close()
        pdoc.close()
    return pdf_target.compare_source(el.texts, got, width), el


def test_native_fixture_matches_source_lines_within_half_a_point(job):
    """原生輸入：輸出檔讀回來的每一行都在來源的位置上（行首與逐字都在 0.5 pt 內）。
    光靠成品文件的段落屬性做不到（來源軟體在中英交界加寬間距、左右對齊的分配方式、行距取整），
    靠的是⑦開給輸出器的逐行對位修正單。"""
    name, pipe, report = job
    styled = pipe.store.load("s5/styled_doc", StyledDoc)
    for sp in styled.pages:
        hits, el = _source_hits(pipe, sp.page_index)
        assert el.production_line.value == "extract"
        assert len(hits) == len([t for t in el.texts if not t.hidden and t.text.strip()])
        assert all(h.found for h in hits), [h.want.text for h in hits if not h.found]  # 來源的每一行文字都讀得回來
        assert max((h.start_error for h in hits), default=0.0) <= 0.5
        assert max((h.char_error for h in hits), default=0.0) <= 0.5
        assert max((h.size_error for h in hits), default=0.0) <= 0.05
    fr = _pdf_report(report)
    codes = [i.code for pg in fr.pages for i in pg.issues]
    assert "source_position_offset" not in codes and "source_text_missing" not in codes


def test_line_fit_goes_through_corrections_and_is_kept_only_if_score_improves(job):
    name, pipe, report = job
    rounds = pipe.state["rounds"]
    fits = [c for c in pipe.state["verify_corrections"] if c["field"] == "pdf.line_fit"]
    out = pipe.store.load("s6/pdf", OutputFile)
    applied = pdf_target.applied_fit(str(pipe.store.blob_path(out.file)))
    assert rounds[0]["proposed"] >= 1  # 五個 fixture 都有需要對位的行（中英交界的間距、置中、行距取整）
    assert len(rounds) == 2 and rounds[1]["kept"] and rounds[1]["score"] > rounds[0]["score"]  # 對位後相似度變高，這一輪才保留
    assert rounds[1]["proposed"] == 0  # 對位一次就到位，不會一直開單
    assert fits and all(c["target_stage"] == 6 and c["source"] == "verify" and c["element_id"] for c in fits)
    assert applied == {c["element_id"]: c["new_value"] for c in fits}  # 輸出檔記的就是修正單的內容
    assert not report.corrections_proposed
    styled = pipe.store.load("s5/styled_doc", StyledDoc)
    elements = [pipe.store.load(f"s3/page-{sp.page_index + 1:03d}", ElementList) for sp in styled.pages]
    assert pdf_target.line_fit(styled, elements, pipe.cfg) == applied  # 同一份成品文件每次算出來的對位都一樣


def test_output_is_tagged_and_fonts_are_embedded(job):
    name, pipe, report = job
    out = pipe.store.load("s6/pdf", OutputFile)
    assert out.filename == f"{name}_editable.pdf" and out.fonts_used
    styled = pipe.store.load("s5/styled_doc", StyledDoc)
    with pikepdf.open(pipe.store.blob_path(out.file)) as pdf:
        from mortise.exporters import pdf_editable

        assert pdf_editable.struct_problems(pdf) == []
        assert str(pdf.Root.Lang) in ("zh-Hant", "en")
        for page in pdf.pages:
            for f in dict(page.obj.Resources.get("/Font", {})).values():
                assert f.Subtype == "/Type0" and "/ToUnicode" in f
                desc = f.DescendantFonts[0].FontDescriptor
                assert "/FontFile2" in desc or "/FontFile3" in desc
    fr = _pdf_report(report)
    assert any("Tagged PDF" in n for n in fr.notes)
    assert not any(i.code in ("struct_missing", "struct_mismatch", "reading_order", "image_count", "image_missing") for pg in fr.pages for i in pg.issues)
    if styled.masters:
        assert any(d.concept == "母片" for d in out.degradations)


def test_report_notes_explain_the_numbers(job):
    _, _, report = job
    fr = _pdf_report(report)
    assert any("文字一致率 =" in n for n in fr.notes) and any("逐行對位" in n for n in fr.notes)
    assert fr.render is not None  # 前端疊圖用的就是輸出檔本身
    info = [i for pg in fr.pages for i in pg.issues if i.code == "source_compare"]
    assert info and all("來源是原生 PDF" in i.message for i in info)


# ---------------------------------------------------------------- 向量圖（原生 PDF 裡的向量）


def test_vector_fixture_reports_vectors_and_draws_them_as_paths(tmp_path):
    src = FIXTURES / "vector_logo.pdf"
    if not src.exists():
        pytest.skip("沒有 vector_logo.pdf")
    pipe = Pipeline(tmp_path / "job")
    pipe.start(src, src.name, Intent(outputs=[OutputFormat.PDF]))
    report = pipe.run()
    styled = pipe.store.load("s5/styled_doc", StyledDoc)
    vectors = [v for sp in styled.pages for v in sp.vectors]
    if not vectors:
        pytest.skip("③沒有從這個 fixture 交出向量圖")
    reports = {e.element_id: e for pg in report.pages for e in pg.elements if e.kind == "vector"}
    assert set(reports) == {v.id for v in vectors}  # 元素報告裡有每一個向量圖
    for v in vectors:
        r = reports[v.id]
        want = "exact" if v.origin == "pdf_paths" else "measured"
        assert r.fields["paths"].value == want and r.fields["position"].value == want and r.confidence <= v.confidence
    assert report.field_counts.get("exact", 0) > 0
    fr = _pdf_report(report)
    assert fr.ok and not [i for pg in fr.pages for i in pg.issues if i.severity.value == "error"]
    out = pipe.store.load("s6/pdf", OutputFile)
    pdoc = mp.open_document(str(pipe.store.blob_path(out.file)))
    reader = mp.PageReader(pdoc, 0)
    try:
        objects = pdf_target.read_objects(reader)
        tree = pdf_target.read_struct_tree(reader)
    finally:
        reader.close()
        pdoc.close()
    figures = {n.id: n for top in tree for n in top.walk() if n.type == "Figure"}
    page = styled.pages[0]
    for v in page.vectors:
        assert v.id in figures  # 每個向量圖是一個 Figure，裡面是路徑（或漸層）物件，不是影像
        kinds = {o.kind for o in objects if o.mcid in figures[v.id].mcids}
        assert kinds and kinds <= {"path", "shading"}
    assert sum(1 for o in objects if o.kind == "image") == len([im for im in page.images if not im.vector_id]) + (1 if page.background_image else 0)


# ---------------------------------------------------------------- 影像產線


def test_screenshot_becomes_pdf_with_readable_text(tmp_path):
    """把 fixture 畫成影像再跑（走影像產線）：也要產出文字讀得回來的 PDF。影像產線不做逐行對位。"""
    png = tmp_path / "slide_basic.png"
    pdfium.PdfDocument(str(FIXTURES / "slide_basic.pdf"))[0].render(scale=2.0).to_pil().convert("RGB").save(png)
    pipe = Pipeline(tmp_path / "job")
    pipe.start(png, png.name, Intent(outputs=[OutputFormat.PDF]))
    report = pipe.run()
    el = pipe.store.load("s3/page-001", ElementList)
    assert el.production_line.value == "image"
    out = pipe.store.load("s6/pdf", OutputFile)
    assert out.selfcheck_ok, out.selfcheck_notes
    fr = _pdf_report(report)
    pg = fr.pages[0]
    assert pg.text_match_ratio == 1.0 and pg.position_error_pt <= 0.5 and pg.similarity > 0.95
    assert not [i for i in pg.issues if i.severity.value == "error"]
    assert not [c for c in pipe.state["verify_corrections"] if c["field"] == "pdf.line_fit"]
    assert pdf_target.applied_fit(str(pipe.store.blob_path(out.file))) == {}
    info = next(i for i in pg.issues if i.code == "source_compare")
    assert "影像量測值" in info.message
    d = pdfium.PdfDocument(str(pipe.store.blob_path(out.file)))
    text = re.sub(r"\s+", "", d[0].get_textpage().get_text_range())
    assert "Mortise季度報告QuarterlyReview" in text and "檔案急救市場" in text  # 文字是真的文字，取得出來
    styled = pipe.store.load("s5/styled_doc", StyledDoc)
    want = re.sub(r"\s+", "", "".join(r.text for tb in styled.pages[0].text_boxes for p in tb.paragraphs for r in p.runs))
    assert sorted(text) == sorted(want)  # 成品文件的每個字都在輸出檔裡（順序依圖層，不一定相同）


# ---------------------------------------------------------------- 逐行對位的算法（不經流水線）


def _placed(text: str, width: float = 10.0, size: float = 20.0, seg_starts=(0,)):
    from mortise.exporters.ai_pdf import PlacedLine

    chars = [(ch, i * width, size) for i, ch in enumerate(text)]
    return PlacedLine(owner="tb", paragraph="p", cell=None, text=text, x=100.0, y=50.0, angle_deg=0.0, size=size, chars=chars,
                      families=["F"], line_no=0, widths=[width] * len(text), seg_starts=list(seg_starts))


def _fit(ln, positions, across=0.0, size=None):
    """positions：來源裡每個看得見的字沿基線的位置（以這一行排版的起點為原點）。"""
    hit = pdf_target._Hit(want=None, found=True, found_at=[(x, across, size or ln.size) for x in positions])
    return pdf_target._fit_line(ln, hit, pdf_target._params(Config.load()))


def test_fit_line_leaves_close_lines_alone():
    ln = _placed("ABCDEFGH")
    assert _fit(ln, [i * 10.0 + 0.1 for i in range(8)]) is None  # 逐字都在容許差（0.25 pt）內：不對位


def test_fit_line_shifts_start_and_inserts_one_gap():
    ln = _placed("ABCD EFGH")  # 第 4 個字是空白
    src = [3.0 + i * 10.0 for i in range(4)] + [3.0 + 50.0 + 6.0 + i * 10.0 for i in range(4)]  # 整行右移 3；空白之後多空 6
    assert _fit(ln, src, across=-1.2) == [3.0, -1.2, [0.0], [[5, 6.0]]]


def test_fit_line_uses_per_segment_char_spacing():
    ln = _placed("中文字中文字ABCDEF", seg_starts=(0, 6))  # 前六個字一段、後六個字一段
    src = [i * 10.4 for i in range(6)] + [6 * 10.4 + i * 10.0 for i in range(6)]  # 來源只撐開中文那一段（每個字多 0.4）
    dx, dy, tcs, gaps = _fit(ln, src)
    assert (dx, dy) == (0.0, 0.0) and tcs == [0.4, 0.0] and gaps == []


def test_fit_line_falls_back_to_uniform_spacing_when_widths_differ_everywhere():
    ln = _placed("ABCDEFGHIJ")
    steps = [0.0, 10.6, 9.5, 10.9, 9.4, 10.8, 9.6, 10.7, 9.3, 10.8]  # 來源字型的字寬處處不同
    src = [sum(steps[: i + 1]) for i in range(10)]
    dx, dy, tcs, gaps = _fit(ln, src)
    assert gaps == [] and tcs == [pytest.approx((src[-1] - 90.0) / 9, abs=1e-4)]  # 不逐字位移，只讓行寬與來源相同


def test_fit_line_respects_user_changes():
    ln = _placed("ABCDEFGH")
    assert _fit(ln, [i * 12.0 for i in range(8)]) is None  # 行寬差 20%：多半是使用者改過字型或字級，不照來源對位
    assert _fit(ln, [5.0 + i * 10.0 for i in range(8)], size=22.0) is None  # 字級與來源不同：不對位


def test_fit_keys_cover_table_cells(tmp_path):
    from mortise.artifacts import Align, Box, Color, FontChoice, Provenance, StyledCell, StyledPage, StyledParagraph, StyledRun, StyledTable, TargetMode, measured
    from mortise.exporters import pdf_editable
    from mortise.pipeline.store import Store

    def cell(r, c, text):
        font = FontChoice(family=measured("Liberation Sans", 0.9, Provenance.INFERRED, "t"), verification_family="Liberation Sans", is_substituted=False)
        run = StyledRun(text=text, font=font, size_pt=measured(11.0, 0.9, Provenance.MEASURED, "t"), color=Color(r=0, g=0, b=0))
        return StyledCell(row=r, col=c, paragraphs=[StyledParagraph(id=f"c{r}{c}", runs=[run], align=Align.LEFT)])

    table = StyledTable(id="tbl", box=Box(x=40, y=40, w=200, h=50), z=1, col_widths_pt=[100.0, 100.0], row_heights_pt=[25.0, 25.0],
                        cells=[cell(0, 0, "Item"), cell(0, 1, "Amount"), cell(1, 0, "Plan"), cell(1, 1, "1,200")])
    doc = StyledDoc(target_mode=measured(TargetMode.CROSS_SOURCE, 0.5, Provenance.INFERRED, "t"), source_app=measured("unknown", 0.0, Provenance.INFERRED, "t"),
                    pages=[StyledPage(page_index=0, width_pt=300, height_pt=150, tables=[table])])
    cfg = Config.load()
    fit = {"tbl": {"1,1/c11/0": [4.0, 0.5, [0.0], []]}}  # 表格儲存格的鍵：「列,欄/段落/行」
    base = {(ln.cell, ln.text): ln for ln in pdf_editable.placed_lines(doc, cfg)[0]}
    moved = {(ln.cell, ln.text): ln for ln in pdf_editable.placed_lines(doc, cfg, fit)[0]}
    for key, ln in base.items():
        dx, dy = moved[key].x - ln.x, moved[key].y - ln.y
        assert (dx, dy) == (pytest.approx(4.0), pytest.approx(0.5)) if key[0] == (1, 1) else (dx, dy) == (0.0, 0.0)
    store = Store(tmp_path)
    out = pdf_editable.export(doc, "t", cfg, store, adjust={"tbl": {"line_fit": fit["tbl"]}})
    report = pdf_target.verify(out, [], [], None, doc, cfg, store)
    assert report.ok and report.pages[0].text_match_ratio == 1.0 and report.pages[0].position_error_pt < 0.01
