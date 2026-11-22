"""輸出尺寸（⑤ s5.output_size）與⑦依 placement 的驗證。

1. 縮放函式逐欄位檢查：把每種元素都放進一頁，縮放後走遍成品文件的每一個數字欄位，
   每個欄位都要有明確的規則（照比例、平移加比例、字級四捨五入到 0.01 pt、或不動）；沒有規則的欄位直接讓測試失敗，
   以後成品文件加了新的長度欄位，這裡會提醒要一起縮放。
2. 整條流水線：同一個 fixture 不指定輸出尺寸、指定 A3、指定長寬比差很多的尺寸各跑一次，驗證分數不應明顯下降。
"""

from __future__ import annotations

import math
from pathlib import Path

import pytest
from pydantic import BaseModel

from mortise.artifacts import (
    Align, BlobRef, Border, Box, Color, FontChoice, Gradient, GradientStop, Intent, LineSpacing, LineSpacingMode, OutputFormat, PhysicalSize, Provenance,
    Role, Style, StyledCell, StyledDoc, StyledImage, StyledMaster, StyledPage, StyledParagraph, StyledRun, StyledShape, StyledTable, StyledTextBox,
    StyledVector, StyledVectorPath, TargetMode, VectorPrimitive, measured,
)
from mortise.stages import s5_settings

FIX = Path(__file__).parent / "fixtures"

S = 1.5  # 200 × 100 pt → 300 × 300 pt：比例 1.5，上下各留 75 pt
OX, OY = 0.0, 75.0
TW, TH = 300.0, 300.0
SLACK = 1.5  # settings.box_width_slack_pt：會換行的文字框多留的固定餘裕（pt）


def _blob(tag: str) -> BlobRef:
    return BlobRef(sha256=tag * 64, media_type="image/png", size=123, ext="png")


def _run(text: str, size: float, error=None) -> StyledRun:
    fc = FontChoice(family=measured("Inter", 0.9, Provenance.MEASURED, "t"), verification_family="Inter", is_substituted=False)
    return StyledRun(text=text, font=fc, size_pt=measured(size, 0.8, Provenance.MEASURED, "t", error=error), bold=True, color=Color(r=10, g=20, b=30, a=0.5))


def _para(pid: str, mode: LineSpacingMode, value: float, error=None) -> StyledParagraph:
    return StyledParagraph(
        id=pid, runs=[_run("Hello ", 13.37, error=0.21), _run("世界", 9.1)], line_spacing=measured(LineSpacing(mode=mode, value=value), 0.7, Provenance.MEASURED, "t", error=error),
        space_before_pt=3.3, space_after_pt=4.4, first_line_indent_pt=5.5, role=Role.BODY, source_line_count=2, style_id="S1",
    )


def _page() -> StyledPage:
    tb = StyledTextBox(id="p001-box-a", box=Box(x=10, y=20, w=50, h=30, rotation=15.0), z=3,
                       paragraphs=[_para("p001-par-a", LineSpacingMode.EXACT, 16.2, error=0.4), _para("p001-par-b", LineSpacingMode.MULTIPLE, 1.15, error=0.01)],
                       first_baseline_pt=11.111)
    tb_center = StyledTextBox(id="p001-box-b", box=Box(x=30, y=40, w=20, h=10), z=3, wrap=False,
                              paragraphs=[_para("p001-par-d", LineSpacingMode.EXACT, 12.0).model_copy(update={"align": Align.CENTER})])
    tb_right = StyledTextBox(id="p001-box-c", box=Box(x=120, y=40, w=60, h=10), z=3,
                             paragraphs=[_para("p001-par-e", LineSpacingMode.EXACT, 12.0).model_copy(update={"align": Align.RIGHT})])
    im = StyledImage(id="p001-image-a", box=Box(x=100, y=10, w=40, h=20), z=1, image=_blob("a"), vector_id="p001-vec-a")
    shapes = [
        StyledShape(id="p001-shape-rect", box=Box(x=5, y=6, w=7, h=8), z=2, geometry="rect", fill=Color(r=1, g=2, b=3), stroke=Color(r=4, g=5, b=6), stroke_width_pt=0.75),
        StyledShape(id="p001-shape-round", box=Box(x=15, y=16, w=17, h=18, rotation=30.0), z=2, geometry="rounded_rect", corner_radius_pt=2.5),
        StyledShape(id="p001-shape-line", box=Box(x=20, y=30, w=40, h=10, rotation=1.0), z=2, geometry="line", stroke=Color(r=0, g=0, b=0), stroke_width_pt=1.25),
        StyledShape(id="p001-shape-path", box=Box(x=50, y=50, w=20, h=20), z=2, geometry="path", fill=Color(r=9, g=9, b=9),
                    path=[[0, 0.0, 0.0], [1, 20.0, 0.0], [2, 20.0, 5.0, 15.0, 20.0, 0.0, 20.0], [3]]),
    ]
    vec = StyledVector(id="p001-vec-a", image_id="p001-image-a", box=Box(x=100, y=10, w=40, h=20), z=1, origin="pdf_paths", confidence=0.9, kind="logo", render_error=1.7, paths=[
        StyledVectorPath(path=[[0, 1.0, 2.0], [1, 30.0, 2.0], [2, 30.0, 10.0, 20.0, 18.0, 1.0, 18.0], [3]], fill=Color(r=200, g=0, b=0),
                         gradient=Gradient(kind="linear", coords=[0.0, 0.0, 40.0, 20.0], stops=[GradientStop(offset=0.0, color=Color(r=0, g=0, b=0)), GradientStop(offset=0.6, color=Color(r=255, g=255, b=255))]),
                         stroke=Color(r=0, g=0, b=0), stroke_width_pt=0.6, primitive=VectorPrimitive(kind="polygon", cx=15.0, cy=10.0, w=29.0, h=16.0, angle_deg=12.0, corner_radius=1.5, points=[[1.0, 2.0], [30.0, 2.0], [1.0, 18.0]])),
        StyledVectorPath(path=[[0, 5.0, 5.0], [1, 9.0, 9.0]], gradient=Gradient(kind="radial", coords=[20.0, 10.0, 8.0], stops=[GradientStop(offset=0.25, color=Color(r=1, g=1, b=1))]),
                         primitive=VectorPrimitive(kind="rounded_rect", cx=7.0, cy=7.0, w=4.0, h=4.0, corner_radius=0.5)),
    ])
    cells = [
        StyledCell(row=0, col=0, paragraphs=[_para("p001-par-c", LineSpacingMode.EXACT, 14.0)], fill=Color(r=240, g=240, b=240),
                   borders=[Border(color=Color(r=0, g=0, b=0), width_pt=0.5), None, Border(color=Color(r=0, g=0, b=0), width_pt=1.0), None],
                   margins_pt=[1.0, 2.0, 3.0, 4.0], header=True),
        StyledCell(row=0, col=1, row_span=2, paragraphs=[], value=1250.0, number_format="#,##0", formula_candidate="=SUM(B1:B2)"),
        StyledCell(row=1, col=0, value=0.61, number_format="0%"),
    ]
    table = StyledTable(id="p001-table-a", box=Box(x=10, y=60, w=90, h=30), z=4, col_widths_pt=[40.0, 50.0], row_heights_pt=[12.0, 18.0], cells=cells, header_rows=1)
    return StyledPage(page_index=0, width_pt=200.0, height_pt=100.0, background_color=Color(r=20, g=30, b=40), background_image=_blob("b"),
                      text_boxes=[tb, tb_center, tb_right], images=[im], shapes=shapes, reading_order=["p001-box-a", "p001-table-a"], tables=[table], vectors=[vec])


def _doc() -> StyledDoc:
    page = _page()
    master = StyledMaster(id="master-1", text_boxes=[page.text_boxes[0]], images=[page.images[0]], shapes=[page.shapes[0]], member_ids=["p001-box-a"], page_indexes=[0])
    styles = [Style(id="S1", name="內文", font_family="Inter", size_pt=13.37, bold=True, italic=False, color=Color(r=10, g=20, b=30), role=Role.BODY, usage_count=6)]
    return StyledDoc(target_mode=measured(TargetMode.CROSS_SOURCE, 0.5, Provenance.INFERRED, "t"), source_app=measured("unknown", 0.0, Provenance.INFERRED, "t"),
                     pages=[page], styles=styles, theme_colors=[Color(r=1, g=2, b=3)], notes=["原本的說明"], masters=[master])


# ---------------------------------------------------------------- 逐欄位的規則

# （類別名稱, 欄位）→ 規則。x、y：平移加比例；len：照比例；font：照比例後四捨五入到 0.01 pt；metric：照比例後四捨五入到 0.01；
# baseline：照比例後四捨五入到 0.001；keep：不動；path：路徑（每段第一個數是種類代碼，不動）；list：每個數照比例；points：每個點的每個數照比例。
RULES: dict[tuple[str, str], str] = {
    ("Box", "x"): "x", ("Box", "y"): "y", ("Box", "w"): "len", ("Box", "h"): "len", ("Box", "rotation"): "keep",
    ("Color", "r"): "keep", ("Color", "g"): "keep", ("Color", "b"): "keep", ("Color", "a"): "keep",
    ("BlobRef", "size"): "keep",
    ("StyledPage", "page_index"): "keep", ("StyledPage", "width_pt"): "target_w", ("StyledPage", "height_pt"): "target_h",
    ("StyledTextBox", "z"): "keep", ("StyledTextBox", "first_baseline_pt"): "baseline",
    ("StyledParagraph", "space_before_pt"): "metric", ("StyledParagraph", "space_after_pt"): "metric", ("StyledParagraph", "first_line_indent_pt"): "metric",
    ("StyledParagraph", "source_line_count"): "keep",
    ("StyledRun", "size_pt.value"): "font", ("StyledRun", "size_pt.confidence"): "keep", ("StyledRun", "size_pt.error"): "len",
    ("FontChoice", "family.confidence"): "keep",
    ("StyledParagraph", "line_spacing.confidence"): "keep",
    ("StyledImage", "z"): "keep",
    ("StyledShape", "z"): "keep", ("StyledShape", "stroke_width_pt"): "len", ("StyledShape", "corner_radius_pt"): "len", ("StyledShape", "path"): "path",
    ("StyledVector", "z"): "keep", ("StyledVector", "confidence"): "keep", ("StyledVector", "render_error"): "keep",
    ("StyledVectorPath", "path"): "path", ("StyledVectorPath", "stroke_width_pt"): "len",
    ("Gradient", "coords"): "list", ("GradientStop", "offset"): "keep",
    ("VectorPrimitive", "cx"): "len", ("VectorPrimitive", "cy"): "len", ("VectorPrimitive", "w"): "len", ("VectorPrimitive", "h"): "len",
    ("VectorPrimitive", "angle_deg"): "keep", ("VectorPrimitive", "corner_radius"): "len", ("VectorPrimitive", "points"): "points",
    ("StyledTable", "z"): "keep", ("StyledTable", "header_rows"): "keep", ("StyledTable", "col_widths_pt"): "list", ("StyledTable", "row_heights_pt"): "list",
    ("StyledCell", "row"): "keep", ("StyledCell", "col"): "keep", ("StyledCell", "row_span"): "keep", ("StyledCell", "col_span"): "keep",
    ("StyledCell", "value"): "keep", ("StyledCell", "margins_pt"): "metric_list",
    ("Border", "width_pt"): "len",
    ("Style", "size_pt"): "font", ("Style", "usage_count"): "keep",
    ("StyledMaster", "page_indexes"): "keep",
}


def _expect(rule: str, v):
    if rule == "x":
        return OX + v * S
    if rule == "y":
        return OY + v * S
    if rule == "len":
        return None if v is None else v * S
    if rule == "font":
        return round(v * S, 2)
    if rule == "metric":
        return round(v * S, 2)
    if rule == "baseline":
        return None if v is None else round(v * S, 3)
    if rule == "keep":
        return v
    if rule == "target_w":
        return TW
    if rule == "target_h":
        return TH
    if rule == "path":
        return [[sg[0]] + [x * S for x in sg[1:]] for sg in v]
    if rule == "list":
        return [x * S for x in v]
    if rule == "metric_list":
        return [round(x * S, 2) for x in v]
    if rule == "points":
        return [[x * S for x in pt] for pt in v]
    raise AssertionError(rule)


def _numeric(v) -> bool:
    if isinstance(v, bool):
        return False
    if isinstance(v, (int, float)):
        return True
    return isinstance(v, list) and bool(v) and all(_numeric(x) for x in v)


def _close(a, b) -> bool:
    if isinstance(a, list):
        return isinstance(b, list) and len(a) == len(b) and all(_close(x, y) for x, y in zip(a, b))
    return math.isclose(a, b, rel_tol=1e-9, abs_tol=1e-9)


ANY = {("StyledParagraph", "style_id")}  # 樣式表依縮放後的字級重建，編號可能改變（另外檢查對得上）


def _text_box_rule(tb, va: Box, vb: Box) -> None:
    """文字框的框：會換行的框只縮放「文字本身的寬」（扣掉固定餘裕 SLACK），字級進位時再放大同樣的比例，餘裕照原值加回；
    依第一段的對齊方式錨定（靠左：左緣、靠右：右緣、置中：中線，都落在照比例的位置）。不換行的框照一般的框縮放。"""
    assert vb.rotation == va.rotation and math.isclose(vb.y, OY + va.y * S) and math.isclose(vb.h, va.h * S)
    if not tb.wrap:
        assert math.isclose(vb.x, OX + va.x * S) and math.isclose(vb.w, va.w * S)
        return
    sizes = [r.size_pt.value for p in tb.paragraphs for r in p.runs]
    grow = max([1.0] + [round(v * S, 2) / (v * S) for v in sizes])
    assert grow > 1.0  # 測試資料的字級進位後比照比例的值大
    natural = (va.w - SLACK) * S * grow
    assert math.isclose(vb.w, natural + SLACK)
    align = tb.paragraphs[0].align.value
    if align == "left":
        assert math.isclose(vb.x, OX + va.x * S)  # 左緣照比例
    elif align == "right":
        assert math.isclose(vb.x + vb.w, OX + (va.x + va.w) * S)  # 右緣照比例
    else:
        assert math.isclose(vb.x + vb.w / 2, OX + (va.x + va.w / 2) * S)


SPECIAL = {("StyledTextBox", "box"): _text_box_rule}


def _compare(a, b, where: str, owner: str = "", prefix: str = "", checked: list | None = None) -> None:
    """平行走過縮放前（a）與縮放後（b）的同一個物件，逐一檢查每個欄位。
    Measured、LineSpacing 這類包裝的欄位算在外層元素名下（例如 StyledRun.size_pt.value）。"""
    assert type(a) is type(b), f"{where}：型別不同（{type(a).__name__} ≠ {type(b).__name__}）"
    name = type(a).__name__
    wrapper = name.startswith("Measured") or name == "LineSpacing"
    own, pre = (owner, prefix) if wrapper else (name, "")
    for field in type(a).model_fields:
        va, vb = getattr(a, field), getattr(b, field)
        key = (own, pre + field)
        at = f"{where}.{field}"
        if key in SPECIAL:
            SPECIAL[key](a, va, vb)
            if checked is not None:
                checked.append(f"{key[0]}.{key[1]}")
        elif isinstance(va, BaseModel):
            _compare(va, vb, at, own, pre + field + ".", checked)
        elif isinstance(va, list) and any(isinstance(x, BaseModel) for x in va) and all(isinstance(x, BaseModel) or x is None for x in va):
            assert len(va) == len(vb), f"{at}：數量不同 {len(va)} ≠ {len(vb)}"
            for i, (x, y) in enumerate(zip(va, vb)):
                if x is None:
                    assert y is None, f"{at}[{i}]：不該改變"
                else:
                    _compare(x, y, f"{at}[{i}]", own, "", checked)
        elif _numeric(va) or (va is None and key in RULES):
            rule = RULES.get(key)
            if key == ("StyledParagraph", "line_spacing.value.value"):
                rule = "metric" if a.mode == LineSpacingMode.EXACT else "keep"  # 固定值（pt）照比例；倍數不動
            elif key == ("StyledParagraph", "line_spacing.error"):
                rule = "len" if a.value.mode == LineSpacingMode.EXACT else "keep"
            assert rule is not None, f"{at}（{key[0]}.{key[1]}）沒有縮放規則：新加的欄位要決定要不要跟著縮放"
            want = _expect(rule, va)
            if want is None or vb is None:
                assert want is None and vb is None, f"{at}：{vb} ≠ {want}"
            else:
                assert _close(vb, want), f"{at}：{vb} ≠ {want}（規則 {rule}）"
            if checked is not None:
                checked.append(f"{key[0]}.{key[1]}")
        elif key not in ANY:
            assert va == vb, f"{at}：不該改變（{va!r} → {vb!r}）"


def test_scale_every_field():
    doc = _doc()
    out = s5_settings.fit_to_size(doc, TW, TH, SLACK)
    src, page = doc.pages[0], out.pages[0]
    assert page.placement == Box(x=OX, y=OY, w=200 * S, h=100 * S)
    # 底圖改成一張放在原始內容範圍、最底層的圖片；底色照舊（各輸出器畫滿整頁，留白處也是底色）
    assert page.background_image is None and page.background_color == src.background_color
    bg = page.images[0]
    assert bg.id == "p001-background" and bg.image == src.background_image and bg.box == page.placement
    assert bg.z < min(x.z for x in [*page.text_boxes, *page.images[1:], *page.shapes, *page.tables, *page.vectors])
    checked: list[str] = []
    stripped = page.model_copy(update={"images": page.images[1:], "placement": None, "background_image": src.background_image})
    _compare(src, stripped, "page", checked=checked)
    for a, b in zip(doc.masters, out.masters):
        _compare(a, b, "master", checked=checked)
    # 樣式表依縮放後的字級重建，段落的樣式編號對得上
    sizes = {st.size_pt for st in out.styles}
    assert round(13.37 * S, 2) in sizes and round(9.1 * S, 2) in sizes
    by_id = {st.id: st for st in out.styles}
    for p in page.text_boxes[0].paragraphs:
        longest = max(p.runs, key=lambda r: len(r.text))
        assert by_id[p.style_id].size_pt == longest.size_pt.value
    # 每一種會縮放的欄位都真的被檢查過（避免走訪漏掉整個分支而測試照樣通過）
    for key in ("Box.x", "Box.y", "StyledTextBox.box", "StyledRun.size_pt.value", "StyledRun.size_pt.error", "StyledParagraph.space_after_pt", "StyledTextBox.first_baseline_pt",
                "StyledShape.path", "StyledShape.corner_radius_pt", "StyledShape.stroke_width_pt", "StyledVectorPath.path", "Gradient.coords",
                "VectorPrimitive.points", "VectorPrimitive.corner_radius", "StyledTable.col_widths_pt", "StyledCell.margins_pt", "Border.width_pt"):
        assert key in checked, key
    # 固定行距照比例、倍數行距不動
    ls = [p.line_spacing for p in page.text_boxes[0].paragraphs]
    assert ls[0].value.value == round(16.2 * S, 2) and math.isclose(ls[0].error, 0.4 * S)
    assert ls[1].value.value == 1.15 and ls[1].error == 0.01
    # 白話說明：比例、原始尺寸、輸出尺寸、留白
    note = next(n for n in out.notes if n.startswith("輸出尺寸"))
    assert "150.0%" in note and "70.6 × 35.3 mm" in note and "105.8 × 105.8 mm" in note and "上下各留白 26.5 mm" in note
    assert out.notes[0] == "原本的說明"


def test_same_size_is_not_scaled():
    doc = _doc()
    out = s5_settings.fit_to_size(doc, 200.0, 100.0)
    assert out.pages[0].placement is None and out.pages[0].text_boxes == doc.pages[0].text_boxes
    assert out.pages[0].background_image == doc.pages[0].background_image


def test_landscape_into_portrait_centers_horizontally():
    sc = s5_settings.page_scale(960.0, 540.0, 595.276, 841.89)
    assert math.isclose(sc.s, 595.276 / 960.0) and sc.ox == 0.0 and math.isclose(sc.oy, (841.89 - 540.0 * sc.s) / 2)
    sc = s5_settings.page_scale(595.276, 841.89, 960.0, 540.0)
    assert math.isclose(sc.s, 540.0 / 841.89) and sc.oy == 0.0 and sc.ox > 0


def test_masters_dropped_when_page_scales_differ():
    doc = _doc()
    p2 = doc.pages[0].model_copy(update={"page_index": 1, "width_pt": 100.0, "height_pt": 100.0})
    doc = doc.model_copy(update={"pages": [doc.pages[0], p2], "masters": [doc.masters[0].model_copy(update={"page_indexes": [0, 1]})]})
    out = s5_settings.fit_to_size(doc, TW, TH)
    assert out.masters == [] and any("母片" in n for n in out.notes)
    assert [round(p.placement.w, 6) for p in out.pages] == [300.0, 300.0]


# ---------------------------------------------------------------- 整條流水線


def _pipeline(source: Path, tmp: Path, size=None):
    from mortise.artifacts import FidelityReport
    from mortise.pipeline import Pipeline

    pipe = Pipeline(tmp)
    pipe.start(source, source.name, Intent(outputs=[OutputFormat.PDF, OutputFormat.SVG], target_size=size))
    report: FidelityReport = pipe.run()
    return pipe, report


def _summary(report) -> dict:
    """各格式各頁的相似度與文字一致率，以及會影響分數的問題代碼。"""
    out = {"overall": report.overall_score, "neutral": [p.neutral_similarity for p in report.pages]}
    for fr in report.format_reports:
        out[fr.format.value] = [(fp.similarity, fp.text_match_ratio, sorted(i.code for i in fp.issues if i.severity.value != "info")) for fp in fr.pages]
    return out


SIZES = {
    "none": None,
    "A3": PhysicalSize(width=297.0, height=420.0, unit="mm"),  # 同為直式、比例不同：上下留白
    "Slide 16:9": PhysicalSize(width=338.67, height=190.5, unit="mm"),  # 直式文件套到橫式簡報：左右大量留白
}


@pytest.fixture(scope="module")
def sized_runs(tmp_path_factory):
    src = FIX / "doc_a4.pdf"  # 兩頁的直式文件（Letter）
    return {name: _pipeline(src, tmp_path_factory.mktemp(name.replace(" ", "_").replace(":", "")), size) for name, size in SIZES.items()}


def test_pipeline_scores_hold_with_output_size(sized_runs):
    """實測（2026-11-22，本機）：overall 無 0.99896、A3 0.99894、Slide 16:9 0.99889；
    各頁各格式的相似度相差都在 0.001 以內，文字一致率都是 1.0。"""
    base = _summary(sized_runs["none"][1])
    for name in ("A3", "Slide 16:9"):
        pipe, report = sized_runs[name]
        got = _summary(report)
        print(name, got)
        assert got["overall"] >= base["overall"] - 0.003, (name, got["overall"], base["overall"])
        for a, b in zip(base["neutral"], got["neutral"]):
            assert b >= a - 0.005, (name, "neutral", a, b)
        for fmt in ("pdf", "svg"):
            for (sim0, text0, codes0), (sim1, text1, codes1) in zip(base[fmt], got[fmt]):
                assert text1 == text0 == 1.0, (name, fmt)
                assert sim1 >= sim0 - 0.005, (name, fmt, sim0, sim1)
                assert set(codes1) <= set(codes0), (name, fmt, codes1)  # 不因縮放多出問題（例如與來源的位置差、斷行改變）
        styled = pipe.store.load("s5/styled_doc", StyledDoc)
        w, h = (297.0, 420.0) if name == "A3" else (338.67, 190.5)
        for page in styled.pages:
            assert math.isclose(page.width_pt, w * 72 / 25.4) and math.isclose(page.height_pt, h * 72 / 25.4)
            pl = page.placement
            assert pl is not None and math.isclose(pl.x * 2 + pl.w, page.width_pt) and math.isclose(pl.y * 2 + pl.h, page.height_pt)
        assert any(n.startswith("輸出尺寸") for n in styled.notes)


def test_output_files_have_target_size(sized_runs):
    from mortise.artifacts import OutputFile
    from mortise.tools import pdfium as pdf

    pipe, _ = sized_runs["Slide 16:9"]
    out = pipe.store.load("s6/pdf", OutputFile)
    doc = pdf.open_document(str(pipe.store.blob_path(out.file)))
    try:
        reader = pdf.PageReader(doc, 0)
        try:
            l, b, r, t = reader.boxes()["media"]
        finally:
            reader.close()
    finally:
        doc.close()
    assert math.isclose(r - l, 338.67 * 72 / 25.4, abs_tol=0.01) and math.isclose(t - b, 190.5 * 72 / 25.4, abs_tol=0.01)


def _gradient_png(path: Path) -> Path:
    """漸層底的合成截圖：漸層不是任何元素，會留在底圖裡（用來測「底圖只放在 placement 範圍」）。"""
    import numpy as np
    from PIL import Image, ImageDraw, ImageFont

    W, H = 1600, 900
    x = np.linspace(0, 1, W)[None, :, None]
    y = np.linspace(0, 1, H)[:, None, None]
    arr = np.concatenate([200 - 120 * x + 0 * y, 220 - 60 * y + 0 * x, 255 - 100 * x * y], axis=2).clip(0, 255).astype("uint8")
    im = Image.fromarray(arr)
    d = ImageDraw.Draw(im)
    from mortise.tools import fontfind  # 找字型一律經過 fontfind（沒有 fontconfig 的 Mac 也找得到）

    m = fontfind.match("DejaVu Sans", bold=True)  # 沒有 DejaVu Sans 時退回同類的字型
    assert m is not None, "這台電腦找不到任何字型"
    font, index = m.path, m.index
    d.text((100, 120), "Gradient Poster 2027", font=ImageFont.truetype(font, 72, index=index), fill=(20, 20, 40))
    d.text((100, 300), "Background is a smooth gradient", font=ImageFont.truetype(font, 36, index=index), fill=(20, 20, 40))
    d.ellipse((1100, 450, 1450, 800), fill=(240, 120, 40))
    im.save(path)
    return path


def test_background_image_only_inside_placement(tmp_path):
    """實測（2026-11-22）：overall 無 0.99630、A3 0.99616。"""
    png = _gradient_png(tmp_path / "gradient.png")
    _, base = _pipeline(png, tmp_path / "none")
    pipe, report = _pipeline(png, tmp_path / "a3", SIZES["A3"])
    styled = pipe.store.load("s5/styled_doc", StyledDoc)
    page = styled.pages[0]
    bg = next((im for im in page.images if im.id.endswith("-background")), None)
    assert bg is not None and page.background_image is None and bg.box == page.placement
    assert report.overall_score >= base.overall_score - 0.003, (report.overall_score, base.overall_score)
    for fr in report.format_reports:
        assert all(i.code not in ("image_count", "struct_missing", "struct_mismatch") for fp in fr.pages for i in fp.issues), fr.format
