"""還原度分數要抓得到「內容不見」與「文字錯位」（公式在 stages/s7_verify.py 開頭，逐行比對在 verify/lines.py）。

做法：poster.pdf（原生 PDF，7 個文字框、8 行字）跑一次流水線，拿它的成品文件故意做壞——
刪掉一個文字框、把一個框移開 10 pt、把一個框疊到另一個框上——重新輸出成可編輯 PDF，再跑⑦驗證。
  好例子（不改）：分數接近 1，而且不比舊算法（只看畫面相似度與讀回的文字）低；沒有找不到或疊字的問題。
  壞例子：分數明顯下降，問題（line_missing、line_offset、line_overlap）要標在對的文字框上，網頁的 Overlay 會畫紅框。
審查後補上的例子（同一份成品文件做壞）：
  框變窄、換行與原圖不同（line_rewrapped、line_extra）；行距壓到字高一半、行疊在一起（要說疊在一起，不是「找不到」）；
  字的顏色改成紅色（line_color）；頁碼這種一個字的行被刪掉（slide_basic.pdf）；縮小渲染解析度時判斷的行數不變；
  多個輸出格式時一個格式壞掉（整體分數取最差的格式）；Word 檔本身要渲染驗證（換成空白檔分數要掉）；
  SVG 沒有畫頁面底色（透明）要被抓到。
只用 tests/fixtures 裡的合成檔案。
"""

import io
from pathlib import Path

import pytest

from mortise.artifacts import (
    Color, ElementList, Intent, Layout, LineSpacing, LineSpacingMode, OutputFormat, PageSpec, Severity, SourceDoc, StyledDoc,
)
from mortise.config import Config
from mortise.pipeline import Pipeline
from mortise.stages import s6_export, s7_verify
from mortise.verify import lines as line_check

FIX = Path(__file__).parent / "fixtures"

MUSIC = "Music, food and design market"  # 單行的文字框（約 37 pt）
RIVERSIDE = "Riverside Park, gates open at 10:00"  # 兩行文字框的第一行（22 pt）
TICKETS = "Tickets at summerfest.example"  # 單行的文字框（18 pt）


@pytest.fixture(scope="module")
def poster(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("scoring")
    pipe = Pipeline(tmp / "job")
    pipe.start(FIX / "poster.pdf", "poster.pdf", Intent(outputs=[OutputFormat.PDF]))
    pipe.run()
    store = pipe.store
    doc = store.load("s1/source_doc", SourceDoc)
    specs = [store.load(f"s2/page-{r.page_index + 1:03d}", PageSpec) for r in doc.pages]
    elements = [store.load(f"s3/page-{r.page_index + 1:03d}", ElementList) for r in doc.pages]
    return {"store": store, "cfg": Config.load(), "doc": doc, "specs": specs, "elements": elements,
            "layout": store.load("s4/layout", Layout), "styled": store.load("s5/styled_doc", StyledDoc)}


def _verify(P, styled):
    """成品文件 → 可編輯 PDF（不套用任何修正單）→ ⑦驗證。"""
    out = s6_export.run(styled, OutputFormat.PDF, "poster", P["cfg"], P["store"])
    return s7_verify.run(P["doc"], P["specs"], P["elements"], P["layout"], styled, [out], P["cfg"], P["store"])


def _box(styled, text):
    page = styled.pages[0]
    return next(tb for tb in page.text_boxes if "".join(r.text for p in tb.paragraphs for r in p.runs).startswith(text))


def _with_boxes(styled, boxes):
    """產物是不可變的：換掉第 1 頁的文字框，做出一份新的成品文件。"""
    page = styled.pages[0].model_copy(update={"text_boxes": boxes})
    return styled.model_copy(update={"pages": [page] + list(styled.pages[1:])})


def _deleted(styled, text):
    victim = _box(styled, text)
    return _with_boxes(styled, [tb for tb in styled.pages[0].text_boxes if tb.id != victim.id]), victim


def _moved(styled, text, x=None, y=None, dx=0.0):
    tb = _box(styled, text)
    box = tb.box.model_copy(update={"x": (tb.box.x if x is None else x) + dx, "y": tb.box.y if y is None else y})
    return _with_boxes(styled, [t.model_copy(update={"box": box}) if t.id == tb.id else t for t in styled.pages[0].text_boxes]), tb


def _line_issues(report):
    return [i for fr in report.format_reports for pg in fr.pages for i in pg.issues if i.code in line_check.CODES]


def _old_score(report):
    """舊算法：可編輯 PDF 的回讀驗證只看 0.5 × 相似度 ＋ 0.5 × 文字一致率。"""
    fp = report.format_reports[0].pages[0]
    return 0.5 * fp.similarity + 0.5 * fp.text_match_ratio


@pytest.fixture(scope="module")
def good(poster):
    return _verify(poster, poster["styled"])


def test_good_example_keeps_its_score(good):
    issues = _line_issues(good)
    assert not [i for i in issues if i.severity == Severity.ERROR], [i.message for i in issues]
    assert good.overall_score > 0.97
    assert good.overall_score >= _old_score(good) - 0.02  # 逐行比對沒有把好例子拉下來
    assert any("逐行文字對照（影像＋文字層）：判斷 8 行" in n for n in good.notes)  # 每一行都有判斷（也用了讀回的文字層），沒有被略過


def test_deleted_text_box_is_found_missing(poster, good):
    styled, victim = _deleted(poster["styled"], MUSIC)
    report = _verify(poster, styled)
    missing = [i for i in _line_issues(report) if i.code == "line_missing"]
    assert [i.element_id for i in missing] == [victim.id]
    assert missing[0].severity == Severity.ERROR and "找不到" in missing[0].message and "Music" in missing[0].message
    # 8 行少 1 行：逐行分數扣 1/8 再加 2%，整體分數至少掉 0.1；舊算法只掉一點點
    assert report.overall_score < good.overall_score - 0.1
    assert _old_score(good) - _old_score(report) < 0.1


def test_box_moved_10pt_is_found_offset(poster, good):
    styled, moved = _moved(poster["styled"], TICKETS, dx=10.0)
    report = _verify(poster, styled)
    issues = _line_issues(report)
    offset = [i for i in issues if i.code == "line_offset"]
    assert [i.element_id for i in offset] == [moved.id]
    assert offset[0].severity == Severity.WARNING
    distance = float(offset[0].message.split("相差 ")[1].split(" pt")[0])
    assert 8.0 <= distance <= 12.0  # 量到的偏移就是移開的距離
    assert not [i for i in issues if i.code == "line_missing"]  # 移開不是不見
    assert report.overall_score < good.overall_score - 0.02


def test_overlapping_boxes_are_flagged(poster, good):
    under = _box(poster["styled"], RIVERSIDE)
    styled, over = _moved(poster["styled"], TICKETS, x=under.box.x, y=under.box.y + 3.0)  # 「Tickets…」整個框搬到「Riverside Park…」上面
    report = _verify(poster, styled)
    issues = _line_issues(report)
    overlap = [i for i in issues if i.code == "line_overlap"]
    assert overlap and {i.element_id for i in overlap} == {under.id}
    assert len(overlap) == 1  # 同一行同一種問題只開一條
    assert overlap[0].severity == Severity.ERROR and "疊" in overlap[0].message and "Tickets" in overlap[0].message
    # 被搬走的框：原位置上沒有它，字被畫到「Riverside…」那一行上（文字層讀得到）
    gone = [i for i in issues if i.code in ("line_missing", "line_misplaced")]
    assert [i.element_id for i in gone] == [over.id]
    assert gone[0].severity == Severity.ERROR and "Riverside" in gone[0].message and "疊" in gone[0].message
    assert report.overall_score < good.overall_score - 0.15


def test_flagged_issues_reach_the_overlay(poster):
    """網頁的 Overlay 依 _flagged 畫紅框：可編輯 PDF 的逐行問題要收進去，別的格式的不收。"""
    pytest.importorskip("fastapi")
    from mortise.api.app import _flagged

    styled, victim = _deleted(poster["styled"], MUSIC)
    styled, moved = _moved(styled, TICKETS, dx=10.0)
    report = _verify(poster, styled)
    flagged = _flagged(report, 0, OutputFormat.PDF)
    assert any("找不到" in m for m in flagged[victim.id])
    assert any("相差" in m for m in flagged[moved.id])
    assert _flagged(report, 0) == flagged  # 不指定格式：收所有格式的
    assert victim.id not in _flagged(report, 0, OutputFormat.SVG)


def test_line_score_formula():
    """逐行分數 = 1 − 各行嚴重程度的平均 − min(10%, 2% × 內容問題的嚴重程度總和)。"""
    assert line_check.line_score(0.0, 0.0, 8) == 1.0
    assert line_check.line_score(0.0, 0.0, 0) is None  # 沒有可判斷的行：不給分數
    assert line_check.line_score(1.0, 1.0, 8) == pytest.approx(1 - 1 / 8 - 0.02)  # 8 行少 1 行
    assert line_check.line_score(1.0, 1.0, 80) == pytest.approx(1 - 1 / 80 - 0.02)  # 長文件少一行也至少扣 3% 多
    assert line_check.line_score(21.8, 21.8, 80) == pytest.approx(1 - 21.8 / 80 - 0.10)  # 20 行不見、3 行疊字
    assert line_check.line_score(0.3, 0.0, 3) == pytest.approx(0.9)  # 只有位置偏移：沒有額外扣分


# ---------------------------------------------------------------- 審查後補上的例子


def _restyled(styled, text, **para_update):
    """把某個框每個段落的屬性換掉（例如行距）。"""
    tb = _box(styled, text)
    paras = [pp.model_copy(update=para_update) for pp in tb.paragraphs]
    return _with_boxes(styled, [t.model_copy(update={"paragraphs": paras}) if t.id == tb.id else t for t in styled.pages[0].text_boxes]), tb


def test_rewrapped_box_is_flagged(poster, good):
    """框變窄 → 重新換行：原位置上影像看起來仍「有字」（長度相近的詞形狀幾乎一樣），但讀回的文字層看得出那裡是別的字。"""
    tb = _box(poster["styled"], RIVERSIDE)
    box = tb.box.model_copy(update={"w": tb.box.w * 0.55})
    styled = _with_boxes(poster["styled"], [t.model_copy(update={"box": box}) if t.id == tb.id else t for t in poster["styled"].pages[0].text_boxes])
    report = _verify(poster, styled)
    issues = _line_issues(report)
    rewrapped = [i for i in issues if i.code == "line_rewrapped"]
    assert rewrapped and {i.element_id for i in rewrapped} == {tb.id}
    assert "換行" in rewrapped[0].message and "原位置上讀到的是" in rewrapped[0].message
    extra = [i for i in issues if i.code == "line_extra"]
    assert extra and {i.element_id for i in extra} == {tb.id}  # 被擠出框外的字，紅框畫在原本的框上
    assert report.overall_score < good.overall_score - 0.1


def test_tight_line_spacing_is_overlap_not_missing(poster, good):
    """行距壓到字高的 0.45 倍：兩行疊在一起。字都還在，要說「疊在一起」，不能說「找不到」。"""
    size = max(r.size_pt.value for pp in _box(poster["styled"], RIVERSIDE).paragraphs for r in pp.runs)
    ls = _box(poster["styled"], RIVERSIDE).paragraphs[0].line_spacing
    tight = LineSpacing(mode=LineSpacingMode.EXACT, value=0.45 * size)
    styled, tb = _restyled(poster["styled"], RIVERSIDE, line_spacing=ls.model_copy(update={"value": tight}) if ls is not None else None)
    if ls is None:
        pytest.skip("這個框沒有行距設定")
    report = _verify(poster, styled)
    issues = [i for i in _line_issues(report) if i.element_id == tb.id]
    assert any(i.code == "line_overlap" and "疊" in i.message for i in issues), [i.message for i in issues]
    assert not [i for i in issues if i.code == "line_missing"]
    assert report.overall_score < good.overall_score - 0.1


def test_text_color_change_is_flagged(poster, good):
    """字的顏色改成紅色：整頁相似度看灰階、幾乎不變；逐行比對比較「字的顏色 − 底色」的方向，要開 line_color。"""
    tb = _box(poster["styled"], RIVERSIDE)
    red = Color(r=230, g=20, b=20)
    paras = [pp.model_copy(update={"runs": [r.model_copy(update={"color": red}) for r in pp.runs]}) for pp in tb.paragraphs]
    styled = _with_boxes(poster["styled"], [t.model_copy(update={"paragraphs": paras}) if t.id == tb.id else t for t in poster["styled"].pages[0].text_boxes])
    report = _verify(poster, styled)
    color = [i for i in _line_issues(report) if i.code == "line_color"]
    assert color and {i.element_id for i in color} == {tb.id} and len(color) == 2  # 兩行都標出來
    assert color[0].severity == Severity.WARNING and "顏色" in color[0].message
    assert good.overall_score - 0.08 < report.overall_score < good.overall_score - 0.02  # 字在、讀得懂：扣分比「找不到」輕


@pytest.fixture(scope="module")
def slide(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("scoring-slide")
    pipe = Pipeline(tmp / "job")
    pipe.start(FIX / "slide_basic.pdf", "slide_basic.pdf", Intent(outputs=[OutputFormat.PDF]))
    pipe.run()
    store = pipe.store
    doc = store.load("s1/source_doc", SourceDoc)
    return {"store": store, "cfg": Config.load(), "doc": doc,
            "specs": [store.load(f"s2/page-{r.page_index + 1:03d}", PageSpec) for r in doc.pages],
            "elements": [store.load(f"s3/page-{r.page_index + 1:03d}", ElementList) for r in doc.pages],
            "layout": store.load("s4/layout", Layout), "styled": store.load("s5/styled_doc", StyledDoc)}


def test_deleted_page_number_is_found_missing(slide):
    """頁碼「1」只有一個字、墨跡很少：以前一律略過，刪掉也不扣分。現在短行也判斷「完全找不到」。"""
    page = slide["styled"].pages[0]
    number = next(t for t in page.text_boxes if "".join(r.text for pp in t.paragraphs for r in pp.runs).strip() == "1")
    styled = _with_boxes(slide["styled"], [t for t in page.text_boxes if t.id != number.id])
    good = _verify(slide, slide["styled"])
    report = _verify(slide, styled)
    missing = [i for i in _line_issues(report) if i.code == "line_missing"]
    assert [i.element_id for i in missing] == [number.id]
    assert "「1」" in missing[0].message
    assert report.overall_score < good.overall_score - 0.05


def test_line_check_does_not_skip_short_lines_at_low_resolution(poster):
    """墨跡量與解析度的平方成正比，門檻也要跟著字高的平方走：渲染解析度減半，判斷的行數不變。"""
    from PIL import Image

    from mortise.tools import pdfium as pdf

    out = s6_export.run(poster["styled"], OutputFormat.PDF, "poster", poster["cfg"], poster["store"])
    spec, el, sp = poster["specs"][0], poster["elements"][0], poster["styled"].pages[0]
    working = Image.open(poster["store"].blob_path(spec.working_image))
    checked = []
    d = pdf.open_document(str(poster["store"].blob_path(out.file)))
    try:
        for dpi in (100.0, 50.0):
            r = pdf.PageReader(d, 0)
            try:
                image = r.render(dpi)
            finally:
                r.close()
            res = line_check.check_page(working, image, el, poster["layout"].pages[0], sp, s7_verify.source_frame(sp), line_check.params(poster["cfg"]))
            checked.append(res.checked)
            assert not [x for x in res.lines if x.skip_reason == "原圖上這一行幾乎沒有墨跡"]
    finally:
        d.close()
    assert checked[0] == checked[1] == 8


def _blank(P, out, fmt):
    """把輸出檔換成同格式、什麼都沒畫的空白檔。"""
    page = P["styled"].pages[0]
    if fmt == OutputFormat.SVG:
        data = (f'<svg xmlns="http://www.w3.org/2000/svg" width="{page.width_pt}pt" height="{page.height_pt}pt" '
                f'viewBox="0 0 {page.width_pt} {page.height_pt}"></svg>').encode()
        ref = P["store"].put_blob(data, "image/svg+xml", "svg")
    elif fmt in (OutputFormat.DOCX_FIXED, OutputFormat.DOCX_FLOW):
        import docx

        buf = io.BytesIO()
        docx.Document().save(buf)
        ref = P["store"].put_blob(buf.getvalue(), "application/vnd.openxmlformats-officedocument.wordprocessingml.document", "docx")
    else:
        raise ValueError(fmt)
    return out.model_copy(update={"file": ref, "degradations": []})


def test_one_broken_format_pulls_the_overall_score_down(poster, good):
    """多個輸出格式：整體分數取最差的格式（加一成平均）。以前取平均，兩個格式一個是空白檔，分數還有一半。"""
    pdf_out = s6_export.run(poster["styled"], OutputFormat.PDF, "poster", poster["cfg"], poster["store"])
    svg_out = _blank(poster, s6_export.run(poster["styled"], OutputFormat.SVG, "poster", poster["cfg"], poster["store"]), OutputFormat.SVG)
    report = s7_verify.run(poster["doc"], poster["specs"], poster["elements"], poster["layout"], poster["styled"], [pdf_out, svg_out],
                           poster["cfg"], poster["store"])
    assert report.overall_score < 0.15
    assert "SVG 0.0" in report.notes[0] and "最差" in report.notes[0]
    assert {fr.format: fr.ok for fr in report.format_reports} == {OutputFormat.PDF: True, OutputFormat.SVG: False}


def test_word_file_itself_is_verified(poster, monkeypatch):
    """Word 檔要真的渲染（LibreOffice 轉 PDF）：換成空白檔，分數要掉到接近 0。沒有 LibreOffice 時改用中性渲染當代用值，報告寫明。"""
    from mortise.tools import libreoffice

    if not libreoffice.available():
        pytest.skip("這台機器沒有 LibreOffice")
    out = s6_export.run(poster["styled"], OutputFormat.DOCX_FIXED, "poster", poster["cfg"], poster["store"])
    args = (poster["doc"], poster["specs"], poster["elements"], poster["layout"], poster["styled"])
    real = s7_verify.run(*args, [out], poster["cfg"], poster["store"])
    blank = s7_verify.run(*args, [_blank(poster, out, OutputFormat.DOCX_FIXED)], poster["cfg"], poster["store"])
    assert [fr.format for fr in real.format_reports] == [OutputFormat.DOCX_FIXED] and "LibreOffice" in real.format_reports[0].engine
    assert blank.overall_score < 0.1 < real.overall_score
    assert not blank.format_reports[0].ok
    monkeypatch.setattr(libreoffice, "available", lambda: False)
    proxy = s7_verify.run(*args, [_blank(poster, out, OutputFormat.DOCX_FIXED)], poster["cfg"], poster["store"])
    assert not proxy.format_reports and proxy.overall_score > 0.9  # 驗證不了：分數來自成品文件本身
    assert "代用值" in proxy.notes[0] and "Word" in proxy.notes[0] and "沒有驗證" in proxy.notes[0]


def test_transparent_svg_page_is_flagged(poster):
    """SVG 沒有畫頁面底色（透明）：驗證時一律鋪白底會看不出來。設定是 auto 卻透明 → svg_transparent（error），分數取黑底下的相似度。"""
    page = poster["styled"].pages[0].model_copy(update={"background_color": None})  # 頁面底色是白色（None）時才會透明
    styled = poster["styled"].model_copy(update={"pages": [page]})
    none_cfg = Config.load()
    none_cfg.data["export"]["svg_page_background"] = "none"
    args = (poster["doc"], poster["specs"], poster["elements"], poster["layout"], styled)
    opaque = s6_export.run(styled, OutputFormat.SVG, "poster", poster["cfg"], poster["store"])
    clear = s6_export.run(styled, OutputFormat.SVG, "poster", none_cfg, poster["store"])  # 輸出器「忘了」畫底色
    ok = s7_verify.run(*args, [opaque], poster["cfg"], poster["store"])
    bad = s7_verify.run(*args, [clear], poster["cfg"], poster["store"])
    codes = lambda r: [i for fp in r.format_reports[0].pages for i in fp.issues if i.code == "svg_transparent"]  # noqa: E731
    assert not codes(ok)
    flagged = codes(bad)
    assert flagged and flagged[0].severity == Severity.ERROR and "深色模式" in flagged[0].message
    assert bad.overall_score < ok.overall_score - 0.1  # 海報上半是色塊與圖片，透明的只有其餘的底
    chosen = s7_verify.run(*args, [clear], none_cfg, poster["store"])  # 使用者自己選了透明：只寫說明，不扣分
    assert [i.severity for i in codes(chosen)] == [Severity.INFO]
    assert chosen.overall_score == pytest.approx(ok.overall_score, abs=0.01)
