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
