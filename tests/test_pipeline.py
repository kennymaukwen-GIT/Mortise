"""端到端測試：原生 PDF → PPTX，並檢查修正單與快取的行為。"""

from pathlib import Path

import pytest

from mortise.artifacts import ElementList, FidelityReport, Intent, PageSpec, PhysicalSize, StyledDoc
from mortise.pipeline import Pipeline
from mortise.pipeline.runner import user_correction
from mortise.tools import libreoffice

FIXTURE = Path(__file__).parent / "fixtures" / "slide_basic.pdf"


@pytest.fixture(scope="module")
def job(tmp_path_factory):
    events = []
    pipe = Pipeline(tmp_path_factory.mktemp("job"), on_event=events.append)
    pipe.start(FIXTURE, FIXTURE.name, Intent())
    report = pipe.run()
    return pipe, report, events


def test_recognizes_text_fonts_and_sizes(job):
    pipe, _, _ = job
    el = pipe.store.load("s3/page-001", ElementList)
    assert [t.text for t in el.texts][0] == "Mortise 季度報告 Quarterly Review"
    assert len(el.texts) == 8 and len(el.images) == 2 and len(el.shapes) == 1
    spec = pipe.store.load("s2/page-001", PageSpec)
    sizes = sorted({round(r.font_size.value * spec.page_width_pt.value, 1) for t in el.texts for r in t.runs})
    assert sizes == [12.0, 18.0, 40.0]
    title = el.texts[0]
    assert all(r.bold.value for r in title.runs)  # 中文字型的假粗體（描邊）也要認得
    assert {r.font_candidates[0].name for r in title.runs} == {"Liberation Sans", "WenQuanYi Zen Hei"}


def test_lines_become_paragraphs_not_fragments(job):
    pipe, _, _ = job
    styled = pipe.store.load("s5/styled_doc", StyledDoc)
    page = styled.pages[0]
    assert (page.width_pt, page.height_pt) == (960.0, 540.0)  # 959.98 吸附到 16:9 簡報
    assert len(page.text_boxes) == 3  # 8 行文字 → 3 個文字框，不是 8 個零碎的框
    body = max(page.text_boxes, key=lambda b: len(b.paragraphs))
    assert len(body.paragraphs) == 2
    text = "".join(r.text for r in body.paragraphs[0].runs)
    assert "檔案急救市場。This paragraph mixes Chinese and English text" in text


@pytest.mark.skipif(not libreoffice.available(), reason="需要 LibreOffice")
def test_verify_loop_fixes_offsets(job):
    pipe, report, _ = job
    assert report.overall_score > 0.97
    boxes = [e for e in report.pages[0].elements if e.kind == "text_box"]
    assert all(e.text_match and e.linebreak_match and e.position_error_pt < 0.5 for e in boxes)
    assert pipe.state["rounds"][0]["score"] < pipe.state["rounds"][-1]["score"]


def test_size_correction_does_not_rerun_recognition(job):
    pipe, _, events = job
    events.clear()
    pipe.add_corrections([user_correction(2, "size", PhysicalSize(width=338.67, height=190.5, unit="mm").model_dump(), n=1)])
    pipe.run()
    assert not any(e["stage"] == 3 and e["status"] == "start" for e in events)  # 改尺寸不必重跑辨識
    assert any(e["stage"] == 5 and e["status"] == "start" for e in events)
    spec = pipe.store.load("s2/page-001", PageSpec)
    assert spec.page_width_pt.locked and spec.page_width_pt.source.value == "user"


def test_user_font_correction_survives_rerun(job):
    pipe, _, _ = job
    styled = pipe.store.load("s5/styled_doc", StyledDoc)
    box = styled.pages[0].text_boxes[0]
    pipe.add_corrections([user_correction(5, "font_family", "Carlito", element_id=box.id, n=2)])
    pipe.run()
    styled = pipe.store.load("s5/styled_doc", StyledDoc)
    fam = styled.pages[0].text_boxes[0].paragraphs[0].runs[0].font.family
    assert fam.value == "Carlito" and fam.locked and fam.confidence == 1.0
    assert isinstance(pipe.store.load("s7/fidelity_report", FidelityReport), FidelityReport)


def test_export_keeps_both_word_files(tmp_path):
    """Word 的版面固定與可重排都叫「檔名.docx」：一起匯出時不可互相覆蓋（實際發生過，只剩一份）。"""
    pipe = Pipeline(tmp_path / "job")
    from mortise.artifacts import OutputFormat

    pipe.start(FIXTURE, "slide_basic.pdf", Intent(outputs=[OutputFormat.DOCX_FIXED, OutputFormat.DOCX_FLOW]))
    pipe.run()
    written = pipe.export_to(tmp_path / "out")
    docx = sorted(p.name for p in written if p.suffix == ".docx")
    assert len(docx) == 2 and len(set(docx)) == 2, docx
