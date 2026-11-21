"""尺寸判斷（page_size.py）的測試：規格表每種尺寸的直橫向、非標準比例、尺度線索、三種結果的判定。"""

from __future__ import annotations

import math
import tomllib
from pathlib import Path

import pytest

from mortise.recognition.page_size import infer_size, judge

CFG = Path(__file__).resolve().parents[2] / "mortise" / "config" / "default.toml"
SIZES = {k: tuple(v) for k, v in tomllib.loads(CFG.read_text(encoding="utf-8"))["page_sizes"].items()}
ISO_P = {"A3 直", "A4 直", "A5 直"}
ISO_L = {"A3 橫", "A4 橫", "A5 橫"}


def _all_sizes():
    """規格表的每個尺寸，再加上旋轉後的另一方向（例如 16:9 簡報的直式、A5 橫）。"""
    cands = infer_size(1.0, SIZES)
    return [(c.name, c.width_pt, c.height_pt) for c in cands]


def test_rotated_variants_added():
    names = {n for n, _, _ in _all_sizes()}
    assert {"A5 橫", "16:9 簡報 直", "4:3 簡報 直", "16:10 簡報 直"} <= names
    assert len(names) == len(SIZES) + 4


@pytest.mark.parametrize("name,w,h", _all_sizes())
def test_every_size_ranks_its_family_first(name, w, h):
    """長寬比精確、沒有尺度線索：正確尺寸排在第一家族；單一成員家族應判 clear，多成員家族應判 ambiguous 且成員信心接近。"""
    cands = infer_size(h / w, SIZES)
    j = judge(cands)
    top_family = cands[0].family
    mates = [c for c in cands if c.family == top_family]
    assert name in {c.name for c in mates}
    assert j["family_verdict"] == "clear"
    if len(mates) == 1:
        assert cands[0].name == name and j["verdict"] == "clear"
    else:
        assert j["verdict"] == "ambiguous"
        confs = [c.confidence for c in mates]
        # 沒有尺度線索：同家族信心值接近（A5 的比例 1.4189 與 √2 差 0.33%，所以略低一點）
        assert max(confs) - min(confs) < 0.05
        assert not j["scale_observable"]


@pytest.mark.parametrize("name,w,h", _all_sizes())
def test_every_size_with_photo_noise(name, w, h):
    """照片等級的長寬比誤差（0.8%，且量測值偏 +0.5%）：正確尺寸仍在第一家族。"""
    a = h / w * 1.005
    cands = infer_size(a, SIZES, {"aspect_error": 0.008 * a})
    assert name in {c.name for c in cands if c.family == cands[0].family}


@pytest.mark.parametrize("name,w,h", _all_sizes())
def test_every_size_with_dpi_is_clear(name, w, h):
    """掃描檔：有可信的 dpi 與頁面像素尺寸 → 同家族也能分出大小，結果 clear。"""
    dpi = 300.0
    px = (w / 72 * dpi, h / 72 * dpi)
    cands = infer_size(h / w, SIZES, {"page_px": px, "dpi": dpi})
    j = judge(cands)
    assert cands[0].name == name
    assert j["verdict"] == "clear" and j["scale_observable"]


def test_text_size_cue_is_weak_but_directional():
    """A4 文件、150 dpi 的影像但沒有 dpi 標記；內文 10.5 pt → 約 21.9 px。字級線索讓 A4 排第一但不至於確定。"""
    wpx = 595.276 / 72 * 150
    tp = [10.5 / 72 * 150 * f for f in (0.95, 1.0, 1.0, 1.02, 1.05, 1.0, 0.98)]
    cands = infer_size(841.89 / 595.276, SIZES, {"page_px": (wpx, wpx * 1.4142), "text_px": tp})
    j = judge(cands)
    assert cands[0].name == "A4 直"
    iso = {c.name: c.confidence for c in cands if c.name in ISO_P}
    assert iso["A4 直"] > 1.5 * iso["A3 直"] and iso["A4 直"] > 1.5 * iso["A5 直"]
    assert j["verdict"] == "ambiguous"  # 弱線索：不宣稱確定
    assert j["family_verdict"] == "clear"


def test_slide_text_prior():
    """簡報：內文 20 pt。16:9 家族只有一個成員，字級線索不影響家族判定。"""
    wpx = 1920
    tp = [20 / 960 * 1920] * 5
    cands = infer_size(1080 / 1920, SIZES, {"page_px": (1920, 1080), "text_px": tp})
    assert cands[0].name == "16:9 簡報" and judge(cands)["verdict"] == "clear"


@pytest.mark.parametrize("a", [1.0, 2.0, 0.45, 1.55, 1.18, 0.66])
def test_nonstandard(a):
    cands = infer_size(a, SIZES, {"aspect_error": 0.005 * a})
    j = judge(cands)
    assert j["verdict"] == "nonstandard", (a, [(c.name, round(c.confidence, 3)) for c in cands[:3]], j)
    assert j["p_nonstandard"] > 0.5


def test_close_pair_is_ambiguous():
    """4:3 橫（0.750）與 Letter 橫（0.773）中間的量測值：前兩名接近，應請使用者選。"""
    a = math.sqrt(0.75 * 612 / 792)
    cands = infer_size(a, SIZES)
    j = judge(cands)
    assert j["verdict"] == "ambiguous"
    top2 = {cands[0].name, cands[1].name}
    assert top2 == {"4:3 簡報", "Letter 橫"}
    assert abs(cands[0].confidence - cands[1].confidence) < 0.05


def test_big_aspect_error_merges_families():
    """長寬比誤差大（5%，例如透視解不穩定）：A4 直 與 Letter 直 都留在前段班，不可宣稱 clear。"""
    a = 1.38
    cands = infer_size(a, SIZES, {"aspect_error": 0.05 * a})
    j = judge(cands)
    assert j["verdict"] == "ambiguous" and j["family_verdict"] == "ambiguous"
    names = [c.name for c in cands[:5]]
    assert "Letter 直" in names and "A4 直" in names


def test_ranking_accuracy_summary():
    """統計：所有尺寸 × 直橫，加上長寬比誤差 ±1%（σ=1.2%）時，正確尺寸在第一家族的比例。"""
    hits = total = 0
    for name, w, h in _all_sizes():
        for f in (0.99, 1.0, 1.01):
            cands = infer_size(h / w * f, SIZES)
            total += 1
            hits += name in {c.name for c in cands if c.family == cands[0].family}
    assert hits / total >= 0.95, (hits, total)


def test_reason_is_plain_language():
    cands = infer_size(1.4142, SIZES)
    assert "無法區分" in cands[0].reason and "長寬比" in cands[0].reason
