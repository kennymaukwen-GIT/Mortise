"""④結構的單元測試：閱讀順序與數字格式。"""

import pytest

from mortise.stages.s4_structure import xy_cut
from mortise.stages.s5_settings import infer_number


def test_xy_cut_two_columns():
    items = [("title", (0.05, 0.02, 0.95, 0.06)), ("R1", (0.55, 0.10, 0.95, 0.30)), ("L1", (0.05, 0.10, 0.45, 0.20)),
             ("L2", (0.05, 0.22, 0.45, 0.40)), ("R2", (0.55, 0.32, 0.95, 0.40)), ("foot", (0.05, 0.50, 0.95, 0.52))]
    assert xy_cut(items) == ["title", "L1", "L2", "R1", "R2", "foot"]


def test_xy_cut_overlapping_falls_back_to_rows():
    items = [("b", (0.1, 0.5, 0.6, 0.6)), ("a", (0.0, 0.0, 1.0, 1.0))]
    assert xy_cut(items) == ["a", "b"]


@pytest.mark.parametrize("text,value,fmt", [
    ("1,250", 1250.0, "#,##0"), ("61%", 0.61, "0%"), ("$25", 25.0, "$0"), ("12.50", 12.5, "0.00"), ("NT$1,200", 1200.0, '"NT$"#,##0'),
])
def test_number_formats(text, value, fmt):
    v, f = infer_number(text)
    assert v == pytest.approx(value) and f == fmt


@pytest.mark.parametrize("text", ["1,2,3", "abc", "2027年", ""])
def test_not_numbers(text):
    assert infer_number(text) is None


def test_formula_candidates_and_confirmation():
    from mortise.artifacts import Box, Provenance, StyledCell, StyledDoc, StyledPage, StyledTable, TargetMode, measured
    from mortise.pipeline.runner import user_correction
    from mortise.stages.s5_settings import _apply_corrections, _formula_candidates

    cells = _formula_candidates([
        StyledCell(row=0, col=0, header=True), StyledCell(row=0, col=1, header=True),
        StyledCell(row=1, col=1, value=1250, number_format="#,##0"), StyledCell(row=2, col=1, value=1480, number_format="#,##0"),
        StyledCell(row=3, col=1, value=2730, number_format="#,##0"), StyledCell(row=3, col=2, value=2731, number_format="#,##0"),
    ])
    total = next(c for c in cells if (c.row, c.col) == (3, 1))
    assert total.formula_candidate == "=SUM(B2:B3)" and total.formula is None  # 只是候選，沒確認不寫公式
    assert next(c for c in cells if (c.row, c.col) == (3, 2)).formula_candidate is None  # 加總不符
    table = StyledTable(id="p001-table-x", box=Box(x=0, y=0, w=100, h=100), z=0, col_widths_pt=[30, 30, 40], row_heights_pt=[20] * 4, cells=cells)
    doc = StyledDoc(target_mode=measured(TargetMode.CROSS_SOURCE, 0.5, Provenance.INFERRED, "t"), source_app=measured("unknown", 0.0, Provenance.INFERRED, "t"),
                    pages=[StyledPage(page_index=0, width_pt=600, height_pt=800, tables=[table])])
    fixed = _apply_corrections(doc, [user_correction(5, "formula", "accept", "p001-table-x#3,1")], {})
    assert next(c for c in fixed.pages[0].tables[0].cells if (c.row, c.col) == (3, 1)).formula == "=SUM(B2:B3)"


def test_small_heading_near_bottom_with_content_below_is_not_footer():
    """頁面最下方、字比內文小一點的小標題（例如履歷的 EDUCATION），底下還有內文就不是頁尾；真正的頁尾仍是頁尾。"""
    from mortise.artifacts import Align, Paragraph, Point, Provenance, Quad, Role, TextLine, TextRun, measured
    from mortise.stages.s4_structure import _roles

    def line(i, text, y, size, x0=0.1, x1=0.6):
        q = Quad.from_rect(x0, y, x1, y + size)
        run = TextRun(text=text, quad=q, font_size=measured(size, 0.9, Provenance.MEASURED, "t"),
                      bold=measured(False, 0.9, Provenance.MEASURED, "t"), italic=measured(False, 0.9, Provenance.MEASURED, "t"),
                      color=measured(__import__("mortise.artifacts", fromlist=["Color"]).Color(r=0, g=0, b=0), 0.9, Provenance.MEASURED, "t"))
        return TextLine(id=f"l{i}", quad=q, z=i, baseline=(Point(x=x0, y=y + size), Point(x=x1, y=y + size)), runs=[run])

    lines = [line(0, "Body text " * 6, 0.30, 0.020), line(1, "More body " * 6, 0.60, 0.020),
             line(2, "EDUCATION", 1.25, 0.0185), line(3, "BA (Hons) Urban Design and Planning", 1.29, 0.020),
             line(4, "Company footer", 1.385, 0.012, 0.3, 0.7)]
    paras = [Paragraph(id=f"p{i}", line_ids=[ln.id], quad=ln.quad, alignment=measured(Align.LEFT, 0.9, Provenance.MEASURED, "t"),
                       role=measured(Role.UNKNOWN, 0.1, Provenance.INFERRED, "t")) for i, ln in enumerate(lines)]

    class Spec:
        aspect_ratio = 1.414

    roles = {p.id: p.role.value for p in _roles(paras, {ln.id: ln for ln in lines}, Spec(), {"page_number_band": 0.12, "title_size_ratio": 1.4})}
    assert roles["p2"] != Role.FOOTER, roles
    assert roles["p4"] == Role.FOOTER, roles
