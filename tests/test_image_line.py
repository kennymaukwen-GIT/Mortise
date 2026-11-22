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


def test_spaces_between_styled_runs_are_kept():
    """量測把一行切成好幾段時，段與段之間的空白不屬於任何一段；組回文字行時不可丟掉。
    實際發生過：海報日期「12 – 14 JULY 2027」被切成「12 – 14」與「JULY 2027」，輸出變成「12 – 14JULY 2027」。"""
    from types import SimpleNamespace as NS

    import numpy as np

    from mortise.recognition.types import Estimate
    from mortise.stages.s3_image_line import _text_line

    text = "12 – 14 JULY 2027"

    def run(start, end):
        return NS(start=start, end=end, font_size_px=Estimate(38.0, 1.0, 0.9), fonts=[], bold=(True, 0.9), italic=(False, 0.9),
                  color=(43, 45, 66), color_confidence=0.9, char_spacing_px=None, notes=[], stroke_width_px=None)

    n_ink = sum(1 for ch in text if ch.strip())
    attrs = NS(quad=[[0, 0], [700, 0], [700, 50], [0, 50]], baseline=[[0, 40], [700, 40]],
               char_x=list(np.linspace(0, 650, n_ink)), runs=[run(0, 7), run(8, 17)], notes=[])
    ol = NS(text=text, quad=attrs.quad, score=0.99, chars=[])
    line = _text_line(0, 0, ol, attrs, 1000.0)
    assert "".join(r.text for r in line.runs) == text
    assert [r.text for r in line.runs] == ["12 – 14 ", "JULY 2027"]


def test_screenshot_shapes_over_photo_are_split(dark):
    """slide_dark 的照片（make_fixtures._photo）上畫了平塗的黃橢圓與深藍矩形：要拆成疊在圖片上的形狀，
    圖片被蓋住的地方補掉，補出來的像素照實記成生成。"""
    pipe, _ = dark
    el = pipe.store.load("s3/page-001", ElementList)
    photo = next(i for i in el.images if i.kind.value == "photo")
    px0, py0, px1, py1 = photo.quad.bbox

    def inside(s):
        x0, y0, x1, y1 = s.quad.bbox
        return px0 - 1e-4 <= x0 and py0 - 1e-4 <= y0 and x1 <= px1 + 1e-4 and y1 <= py1 + 1e-4

    over = [s for s in el.shapes if inside(s)]
    assert sorted(s.geometry.value for s in over) == ["ellipse", "rect"]
    assert all(s.z > photo.z for s in over)
    want = {"ellipse": (235, 200, 70), "rect": (40, 60, 90)}
    assert all(max(abs(a - b) for a, b in zip((s.fill.r, s.fill.g, s.fill.b), want[s.geometry.value])) <= 3 for s in over)
    assert any(photo.id in n and "生成" in n for n in el.notes)
