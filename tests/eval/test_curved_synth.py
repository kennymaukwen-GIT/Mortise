"""synth.degrade_curved（彎曲的紙、逐像素真值）與攤平指標（dewarp_geometry、pdf_text_cer）。"""

import cv2
import numpy as np
import pytest

from mortise.eval import metrics as M
from mortise.eval import synth as S
from mortise.recognition.page_geometry import homography_from_points

SMALL = {"curved_out_long": 700, "curved_grid_px": 8.0}


def _dots_page():
    img = np.full((700, 500, 3), 250, np.uint8)
    pts = []
    for y in range(70, 700, 140):
        for x in range(60, 500, 120):
            cv2.circle(img, (x, y), 9, (0, 0, 0), -1, lineType=cv2.LINE_AA)
            pts.append((x + 0.5, y + 0.5))  # cv2 畫在像素中心；換成角落約定加 0.5
    return img, np.array(pts)


@pytest.mark.parametrize("kind", S.CURVED_KINDS)
def test_degrade_curved_reproducible_and_map_points_land_on_dots(kind):
    page, pts = _dots_page()
    prm = dict(SMALL, curved_glare=False, curved_hard_shadow=False, curved_second_sheet=False)
    a, ma, ia = S.degrade_curved(page, kind, seed=5, params=prm)
    b, mb, ib = S.degrade_curved(page, kind, seed=5, params=prm)
    assert np.array_equal(a, b) and np.array_equal(ma, mb, equal_nan=True) and ia == ib
    assert ma.shape[:2] == a.shape[:2] and ia["output_size"] == [a.shape[1], a.shape[0]]
    gray = a.astype(np.float32).mean(axis=2)
    ok = ~np.isnan(ma[..., 0])
    assert 0.2 < ok.mean() < 0.9
    seen = 0
    for u0, v0 in pts:
        d = np.hypot(ma[..., 0] - u0, ma[..., 1] - v0)
        inner = ok & (d < 4.0)
        ring = ok & (d > 16.0) & (d < 22.0)
        if inner.sum() < 3 or ring.sum() < 10:  # 這個點被捲起的紙遮住或太斜
            continue
        seen += 1
        assert gray[inner].mean() < 0.5 * gray[ring].mean() and gray[ring].mean() > 90, (kind, u0, v0)  # 捲起處背光，整體較暗
    assert seen >= 0.6 * len(pts)


def test_degrade_curved_scene_options_and_reproducible_info():
    page, _ = _dots_page()
    _, pm, info = S.degrade_curved(page, "curl_top", seed=2, params=dict(SMALL, curved_background="paper_like", curved_second_sheet=True,
                                                                         curved_hard_shadow=True, curved_glare=True))
    assert info["background"] == "paper_like" and info["second_sheet"] and info["second_sheet_poly"] is not None
    assert info["hard_shadow"] and info["glare"] is not None
    assert len(info["page_outline"]) == 4 and all(len(e) == 65 for e in info["page_outline"])
    with pytest.raises(ValueError):
        S.degrade_curved(page, "origami", seed=1)


def test_dewarp_geometry_perfect_and_wrong_maps():
    page, _ = _dots_page()
    _, pm, info = S.degrade_curved(page, "flat", seed=3, params=dict(SMALL, curved_out_long=900))
    H, W = page.shape[:2]
    c = np.array(info["page_corners"])
    Hm = homography_from_points(np.array([[0, 0], [W, 0], [W, H], [0, H]], float), c)  # 頁面 → 照片（平的紙就是單應矩陣）
    ys, xs = np.mgrid[0:H, 0:W].astype(np.float64) + 0.5
    P = np.stack([xs, ys, np.ones_like(xs)], -1) @ Hm.T
    mx, my = (P[..., 0] / P[..., 2]).astype(np.float32), (P[..., 1] / P[..., 2]).astype(np.float32)
    g = M.dewarp_geometry(mx, my, pm, (W, H))
    assert g["dewarp_pos_p95_px"] < 1.0 and g["page_coverage"] > 0.97 and g["background_frac"] < 0.02
    # 加上一個正弦波位移：p95 應明顯變大
    bad = M.dewarp_geometry(mx + 6 * np.sin(ys / 60.0).astype(np.float32), my, pm, (W, H))
    assert bad["dewarp_pos_p95_px"] > 4.0
    # 只交出頁面上半：覆蓋率約一半
    half = M.dewarp_geometry(mx[: H // 2], my[: H // 2], pm, (W, H))
    assert 0.4 < half["page_coverage"] < 0.6


def test_pdf_text_cer_assignment():
    truth = [{"text": "Module Study Plan", "bbox": [100, 100, 400, 130]}, {"text": "第二行 文字", "bbox": [100, 200, 300, 230]}]
    perfect = [("Module Study", (200, 115)), ("Plan", (380, 116)), ("第二行文字", (200, 215))]
    assert M.pdf_text_cer(perfect, truth)["pdf_text_cer"] == 0.0
    one = M.pdf_text_cer([("Module Studv Plan", (250, 115)), ("第二行文字", (200, 215))], truth)
    assert one["errors"] == 1 and one["chars"] == len("ModuleStudyPlan") + len("第二行文字")
    stray = M.pdf_text_cer(perfect + [("noise", (900, 900)), ("x", None)], truth)
    assert stray["unassigned"] == 2 and stray["errors"] == 6


def test_table_page_has_text_layer_and_grid():
    img, lines = S.table_page(seed=0)
    assert img.shape[:2] == (1754, 1241)
    texts = " ".join(t["text"] for t in lines)
    assert "Module Study Plan 6003" in texts and "Session 1" in texts and len(lines) > 40
    gray = img.mean(axis=2)
    dark_rows = (gray < 128).mean(axis=1) > 0.6  # 表格橫線：整列大半是黑的
    assert dark_rows.sum() >= 14
