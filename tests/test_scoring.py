"""還原度分數要抓得到「內容不見」與「文字錯位」（公式在 stages/s7_verify.py 開頭，逐行比對在 verify/lines.py）。

做法：poster.pdf（原生 PDF，7 個文字框、8 行字）跑一次流水線，拿它的成品文件故意做壞——
刪掉一個文字框、把一個框移開 10 pt、把一個框疊到另一個框上——重新輸出成可編輯 PDF，再跑⑦驗證。
  好例子（不改）：分數接近 1，而且不比舊算法（只看畫面相似度與讀回的文字）低；沒有找不到或疊字的問題。
  壞例子：分數明顯下降，問題（line_missing、line_offset、line_overlap）要標在對的文字框上，網頁的 Overlay 會畫紅框。
只用 tests/fixtures 裡的合成檔案。
"""

from pathlib import Path

import pytest

from mortise.artifacts import ElementList, Intent, Layout, OutputFormat, PageSpec, Severity, SourceDoc, StyledDoc
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
    assert any("逐行文字對照：判斷 8 行" in n for n in good.notes)  # 每一行都有判斷，沒有被略過


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
    assert overlap[0].severity == Severity.ERROR and "疊" in overlap[0].message
    assert [i.element_id for i in issues if i.code == "line_missing"] == [over.id]  # 被搬走的框在原位置找不到
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
