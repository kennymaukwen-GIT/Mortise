"""原生 PDF 擷取產線的細節：表格、形狀、旋轉文字、左右對齊段落、深色底。"""

from pathlib import Path

import pytest

from mortise.artifacts import ElementList, FidelityReport, Intent, Layout, OutputFile, StyledDoc
from mortise.pipeline import Pipeline
from mortise.tools import libreoffice

FIX = Path(__file__).parent / "fixtures"


def _run(tmp_path_factory, name):
    pipe = Pipeline(tmp_path_factory.mktemp(name))
    pipe.start(FIX / f"{name}.pdf", f"{name}.pdf", Intent())
    report = pipe.run()
    return pipe, report


@pytest.fixture(scope="module")
def doc_a4(tmp_path_factory):
    return _run(tmp_path_factory, "doc_a4")


@pytest.fixture(scope="module")
def poster(tmp_path_factory):
    return _run(tmp_path_factory, "poster")


@pytest.fixture(scope="module")
def slide_dark(tmp_path_factory):
    return _run(tmp_path_factory, "slide_dark")


def test_table_structure_and_numbers(doc_a4):
    pipe, _ = doc_a4
    styled = pipe.store.load("s5/styled_doc", StyledDoc)
    (table,) = styled.pages[0].tables
    assert (len(table.row_heights_pt), len(table.col_widths_pt)) == (4, 3)
    assert table.header_rows == 1
    cells = {(c.row, c.col): c for c in table.cells}
    text = lambda c: "".join(r.text for p in c.paragraphs for r in p.runs)
    assert text(cells[(0, 0)]) == "項目 Item" and text(cells[(3, 2)]) == "64%"
    assert (cells[(1, 1)].value, cells[(1, 1)].number_format) == (1250.0, "#,##0")
    assert (cells[(3, 1)].value, cells[(3, 1)].number_format) == (pytest.approx(0.61), "0%")
    assert all(b is not None for b in cells[(1, 1)].borders)
    # 表格裡的字不能混進內文的文字框
    body = "".join(r.text for tb in styled.pages[0].text_boxes for p in tb.paragraphs for r in p.runs)
    assert "1,250" not in body and "毛利率" not in body


def test_justified_lines_are_whole(doc_a4):
    pipe, _ = doc_a4
    el = pipe.store.load("s3/page-001", ElementList)
    first = next(t for t in el.texts if t.text.startswith("本報告"))
    assert first.text.endswith("主要來自企業客")  # 左右對齊被拉寬的詞間距不可把一行切碎
    assert len(el.texts) == 22


def test_shapes_rounded_rect_and_rotation(poster):
    pipe, _ = poster
    el = pipe.store.load("s3/page-001", ElementList)
    rr = [s for s in el.shapes if s.geometry.value == "rounded_rect"]
    assert len(rr) == 1 and rr[0].corner_radius * 841.89 == pytest.approx(18.0, abs=0.2)
    rotated = next(t for t in el.texts if t.text.startswith("Organised"))
    assert rotated.text == "Organised by the city arts council"
    styled = pipe.store.load("s5/styled_doc", StyledDoc)
    box = next(tb for tb in styled.pages[0].text_boxes if "Organised" in tb.paragraphs[0].runs[0].text)
    assert box.box.rotation == pytest.approx(-90.0, abs=0.1)
    size = next(r.size_pt.value for tb in styled.pages[0].text_boxes for p in tb.paragraphs for r in p.runs if "Music" in r.text)
    assert size == pytest.approx(37.2, abs=0.01)  # 不整齊的字級不可被硬吸附成 37 或 37.5


def test_dark_slide_background_and_ellipse(slide_dark):
    pipe, report = slide_dark
    el = pipe.store.load("s3/page-001", ElementList)
    assert el.background.color.hex == "141E30" and el.background.image is None
    assert [s.geometry.value for s in el.shapes].count("ellipse") == 1
    if libreoffice.available():
        assert report.pages[0].target_similarity > 0.98


@pytest.mark.skipif(not libreoffice.available(), reason="需要 LibreOffice")
def test_all_fixtures_verify_cleanly(doc_a4, poster, slide_dark):
    for pipe, report in (doc_a4, poster, slide_dark):
        assert report.overall_score > 0.97
        for page in report.pages:
            assert all(e.text_match for e in page.elements if e.kind in ("text_box", "table"))


@pytest.fixture(scope="module")
def deck(tmp_path_factory):
    return _run(tmp_path_factory, "deck")


def test_repeated_elements_go_to_master(deck):
    from pptx import Presentation

    pipe, report = deck
    styled = pipe.store.load("s5/styled_doc", StyledDoc)
    assert styled.masters and len(styled.masters[0].member_ids) >= 9  # 標誌、色帶、頁尾文字 × 3 頁
    out = pipe.store.load("s6/pptx", OutputFile)
    prs = Presentation(str(pipe.store.blob_path(out.file)))
    layout_names = [sh.name for sh in prs.slide_layouts[6].shapes]
    assert any(n.endswith(tuple("0123456789abcdef")) and "-box-" in n for n in layout_names)  # 頁尾文字在母片上
    for slide in prs.slides:
        texts = ["".join(r.text for p in sh.text_frame.paragraphs for r in p.runs) for sh in slide.shapes if sh.has_text_frame]
        assert not any("Internal" in t for t in texts)  # 頁尾不再逐頁重複
        assert len(texts) == 3  # 標題、內文、頁碼（頁碼每頁不同，留在頁面上）
    if libreoffice.available():
        assert report.overall_score > 0.97
