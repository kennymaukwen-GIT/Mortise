"""mortise/tools/fontfind.py：沒有 fontconfig 的電腦（macOS、Windows）也要找得到字型。

用 MORTISE_NO_FONTCONFIG=1 模擬沒有 fontconfig，MORTISE_FONT_DIRS 指到只放幾個字型檔的目錄，
模擬「使用者電腦上的字型和伺服器不同」。"""

import shutil
from pathlib import Path

import pytest

from mortise.tools import fontfind

LIB = Path("/usr/share/fonts/truetype/liberation")


@pytest.fixture
def mac_like(tmp_path, monkeypatch):
    if not (LIB / "LiberationSans-Regular.ttf").exists():
        pytest.skip("這台機器沒有 Liberation 字型")
    fonts = tmp_path / "fonts"
    fonts.mkdir()
    for name in ("LiberationSans-Regular.ttf", "LiberationSans-Bold.ttf", "LiberationSans-Italic.ttf", "LiberationSerif-Regular.ttf", "LiberationMono-Regular.ttf"):
        shutil.copy(LIB / name, fonts / name)
    monkeypatch.setenv("MORTISE_NO_FONTCONFIG", "1")
    monkeypatch.setenv("MORTISE_FONT_DIRS", str(fonts))
    monkeypatch.setenv("MORTISE_CACHE_DIR", str(tmp_path / "cache"))
    monkeypatch.setattr(fontfind, "system_font_dirs", lambda: [str(fonts)])
    fontfind.reset_cache()
    yield fonts
    fontfind.reset_cache()


def test_exact_family_and_style(mac_like):
    m = fontfind.match("Liberation Sans")
    assert m.exact and Path(m.path).name == "LiberationSans-Regular.ttf"
    b = fontfind.match("Liberation Sans", bold=True)
    assert b.exact and Path(b.path).name == "LiberationSans-Bold.ttf" and b.weight >= 180
    i = fontfind.match("Liberation Sans", italic=True)
    assert i.exact and i.slant > 0
    assert fontfind.find_file("Liberation Sans") is not None


def test_metric_compatible_and_generic_fallbacks(mac_like):
    arial = fontfind.match("Arial", bold=True)
    assert not arial.exact and arial.families[0] == "Liberation Sans" and arial.weight >= 180
    assert fontfind.match("Times New Roman").families[0] == "Liberation Serif"
    assert fontfind.match("Some Unknown Serif").families[0] == "Liberation Serif"
    assert fontfind.match("Some Unknown Mono").families[0] == "Liberation Mono"
    assert fontfind.match("sans-serif").families[0] == "Liberation Sans"
    assert fontfind.find_file("Arial") is None  # 只找字族本身時不退回


def test_scan_is_cached(mac_like, tmp_path):
    fontfind.list_fonts()
    cache = list((tmp_path / "cache").glob("fontscan-*.json"))
    assert cache, "掃描結果要寫進快取"
    fontfind.reset_cache()
    assert len(fontfind.list_fonts()) == 5


def test_pipeline_scores_without_fontconfig_or_libreoffice(mac_like, tmp_path, monkeypatch):
    """使用者的 Mac 上沒有 fontconfig 也沒有 LibreOffice：⑦仍要打得出分數（以前會因「找不到任何可內嵌的字型」而沒有分數）。"""
    from mortise.artifacts import Intent, OutputFormat
    from mortise.pipeline import Pipeline
    from mortise.tools import libreoffice

    monkeypatch.setattr(libreoffice, "available", lambda: False)
    fixture = Path(__file__).resolve().parents[1] / "fixtures" / "slide_basic.pdf"
    pipe = Pipeline(tmp_path / "job")
    pipe.start(fixture, fixture.name, Intent(outputs=[OutputFormat.DOCX_FIXED]))
    report = pipe.run()
    assert report.overall_score is not None and report.overall_score > 0.9
    assert not any("找不到任何可內嵌的字型" in n for n in report.notes)
