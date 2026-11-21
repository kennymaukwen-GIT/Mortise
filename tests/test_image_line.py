"""影像產線端到端：把原生 PDF 畫成截圖（PNG），走影像辨識，與原生 PDF 的結果比對。"""

from pathlib import Path

import pypdfium2 as pdfium
import pytest

from mortise.artifacts import ElementList, FidelityReport, Intent, PageSpec, StyledDoc
from mortise.pipeline import Pipeline
from mortise.tools import libreoffice

FIX = Path(__file__).parent / "fixtures"


def _screenshot(tmp_path, name, dpi=144):
    out = tmp_path / f"{name}.png"
    pdfium.PdfDocument(str(FIX / f"{name}.pdf"))[0].render(scale=dpi / 72).to_pil().convert("RGB").save(out)
    return out


@pytest.fixture(scope="module")
def dark(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("img")
    png = _screenshot(tmp, "slide_dark")
    pipe = Pipeline(tmp / "job")
    pipe.start(png, png.name, Intent())
    report = pipe.run()
    return pipe, report


def test_screenshot_size_and_text(dark):
    pipe, report = dark
    spec = pipe.store.load("s2/page-001", PageSpec)
    assert spec.page_width_pt.value == pytest.approx(960.0, abs=1.0)  # 由長寬比推論出 16:9 簡報
    styled = pipe.store.load("s5/styled_doc", StyledDoc)
    texts = ["".join(r.text for p in tb.paragraphs for r in p.runs) for tb in styled.pages[0].text_boxes]
    assert any("Roadmap 2027" in t for t in texts)
    bullets = next(tb for tb in styled.pages[0].text_boxes if len(tb.paragraphs) == 4)  # 四個項目在同一個文字框
    assert all("".join(r.text for r in p.runs).startswith("• ") for p in bullets.paragraphs)  # 項目符號沒有丟
    sizes = {r.size_pt.value for p in bullets.paragraphs for r in p.runs}
    assert sizes == {20.0}  # 量測的 19.8 到 20.3 pt 同段定案並吸附成 20 pt
    title = styled.pages[0].text_boxes[0].paragraphs[0].runs[0]
    assert title.size_pt.value == 44.0 and title.bold and title.color.hex == "FFFFFF"


def test_screenshot_shapes_and_background(dark):
    pipe, _ = dark
    el = pipe.store.load("s3/page-001", ElementList)
    geoms = sorted(s.geometry.value for s in el.shapes)
    assert geoms.count("rect") >= 3 and "ellipse" in geoms and "line" in geoms
    assert el.background.color.hex == "141E30" and el.background.image is None
    assert not any(t.text.strip() == "D" for t in el.texts)  # 圖形不可被讀成大寫字母


@pytest.mark.skipif(not libreoffice.available(), reason="需要 LibreOffice")
def test_screenshot_verifies(dark):
    _, report = dark
    assert report.overall_score > 0.95
    assert report.pages[0].neutral_similarity > 0.95 and report.pages[0].target_similarity > 0.95
