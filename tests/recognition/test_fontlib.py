"""fontlib 的測試：列舉、量度、HarfBuzz 排字、FreeType 畫字、衍生量度。"""

from __future__ import annotations

import sys
import time
from pathlib import Path

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from mortise.recognition.fontlib import DEFAULT_PARAMS, FontFace, FontLibrary, normalize_style, stroke_width_soft  # noqa: E402


@pytest.fixture(scope="module")
def lib():
    return FontLibrary.default()


def test_enumerate_and_find(lib):
    assert len(lib) >= 50
    fams = set(lib.families())
    for fam in ("Liberation Sans", "Liberation Serif", "Carlito", "Caladea", "DejaVu Sans", "Inter", "JetBrains Mono", "WenQuanYi Zen Hei", "IPAGothic"):
        assert fam in fams, fam
    assert "Noto Color Emoji" not in fams  # 彩色點陣字型排除
    f = lib.find("liberation sans", "regular")
    assert f is not None and f.family == "Liberation Sans" and f.style == "Regular"
    assert lib.find("DejaVu Sans", "Book") is not None  # Book → Regular
    assert lib.find("Liberation Sans", "Bold").is_bold
    assert lib.find("Liberation Serif", "Italic").is_italic
    assert lib.find("No Such Font") is None
    assert FontLibrary.default() is lib  # 快取


def test_coverage_filter(lib):
    cjk = lib.faces("中文字型")
    names = {f.family for f in cjk}
    assert "WenQuanYi Zen Hei" in names and "Liberation Sans" not in names
    assert len(lib.faces("Hello")) > len(cjk)
    wq = lib.find("WenQuanYi Zen Hei")
    assert ord("中") in wq.coverage and wq.covers("中 文")


def test_metrics_known_values(lib):
    ls = lib.find("Liberation Sans")
    assert ls.units_per_em == 2048
    assert abs(ls.ascender - 1854 / 2048) < 1e-6 and abs(ls.descender + 434 / 2048) < 1e-6
    assert abs(ls.x_height - 1082 / 2048) < 1e-6 and abs(ls.cap_height - 1409 / 2048) < 1e-6
    carlito = lib.find("Carlito")
    assert abs(carlito.x_height - 0.478) < 0.01  # research/fonts.md 3.10：978 ÷ 2048
    assert lib.find("Liberation Mono").is_monospace and not ls.is_monospace
    assert lib.find("Liberation Serif", "Italic").italic_angle > 5


def test_harfbuzz_layout_with_kerning(lib):
    ls = lib.find("Liberation Sans")
    # 「AV」有字距調整：排出來的寬度小於兩個字前進寬度之和
    assert ls.advance("AV") < ls.advance("A") + ls.advance("V") - 0.02
    st = ls.advances("Hello")
    assert len(st) == 5 and st[0] == 0.0 and all(b > a for a, b in zip(st, st[1:]))
    wq = lib.find("WenQuanYi Zen Hei")
    assert np.allclose(wq.advances("中文字型"), [0, 1, 2, 3])  # 全形字 1 em
    lay = ls.layout("Hxg")
    x_box, g_box = lay.ink[1], lay.ink[2]
    assert abs(x_box[2] - ls.x_height) < 0.02  # x 的上緣 ≈ x 字高
    assert g_box[3] < -0.1  # g 有下伸部
    ib = ls.ink_bounds("Hxg")
    assert ib[0] >= 0 and ib[1] <= ls.advance("Hxg") + 0.05


def test_render_fractional_size_and_origin(lib):
    ls = lib.find("Liberation Sans")
    for size in (20.0, 20.5, 33.3):
        rt = ls.render_layout("H", size)
        m = rt.mask
        assert m.dtype == np.float32 and 0.0 <= m.min() and m.max() <= 1.0
        rows = np.flatnonzero(m.max(axis=1) >= 0.5)
        top, bottom = rows[0], rows[-1] + 1
        # 「H」的墨跡高 = 大寫字高 × 字級，底緣在基線上（容差 1 px）
        assert abs((bottom - top) - ls.cap_height * size) <= 1.0
        assert abs(bottom - rt.baseline_y) <= 1.0
    # 分數字級不被捨入：20 px 與 20.5 px 畫出來的墨跡面積不同
    a, b = ls.render("Hello", 20.0).sum(), ls.render("Hello", 20.5).sum()
    assert b > a * 1.02
    # 假斜體與水平縮放
    w0 = ls.render("H", 40).shape[1]
    assert ls.render_layout("H", 40, x_scale=1.5).mask.shape[1] > w0
    sl = ls.render_layout("lll", 40, slant_deg=12).mask
    assert sl.shape[1] > ls.render("lll", 40).shape[1]


def test_stroke_ratio_and_soft_width(lib):
    reg, bold = lib.find("Liberation Sans"), lib.find("Liberation Sans", "Bold")
    assert bold.stem_ratio() > reg.stem_ratio() * 1.4
    # soft 估計對模糊不敏感
    import cv2

    m = np.zeros((60, 60), np.float32)
    m[10:50, 20:26] = 1.0  # 6 px 寬的直線
    w_sharp = stroke_width_soft(m)
    w_blur = stroke_width_soft(cv2.GaussianBlur(m, (0, 0), 1.5))
    assert abs(w_sharp - 6.0) < 0.8 and abs(w_blur - w_sharp) < 0.6


def test_equivalents_and_style_normalization(lib):
    assert normalize_style("Book") == "Regular"
    assert normalize_style("Bold Oblique") == "Bold Italic"
    wq = lib.find("WenQuanYi Zen Hei")
    eq = {f.family for f in lib.equivalents(wq)}
    assert "WenQuanYi Zen Hei Sharp" in eq  # 同一家族的點陣提示版本，字寬相同


def test_extra_font_dir_and_params(tmp_path, lib):
    import shutil

    src = lib.find("Carlito").path
    shutil.copy(src, tmp_path / "MyCopy.ttf")
    lib2 = FontLibrary.default(params={"use_fc_list": False, "fallback_dirs": []}, font_dirs=[str(tmp_path)])
    assert len(lib2) == 1 and lib2.faces()[0].family == "Carlito"
    assert "features" in DEFAULT_PARAMS and DEFAULT_PARAMS["features"]["kern"] is True
    f2 = FontFace(family="Carlito", style="Regular", path=src, params={**DEFAULT_PARAMS, "features": {"kern": False}})
    assert f2.advance("AV") >= lib.find("Carlito").advance("AV")


def test_speed(lib):
    t = time.time()
    faces = lib.faces("The quick brown fox")
    for f in faces:
        f.advance("The quick brown fox")
    assert time.time() - t < 5.0
