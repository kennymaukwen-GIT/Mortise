"""照片頁面偵測的「實景型態」測試：全部用合成照片重現真實照片的難處（不放任何真實照片）。

對應的真實照片（創辦人上傳，不進版本庫）：印刷課表放在米色布料上、紙的上緣捲起、右邊露出另一張紙、光線不均。
以前的錯誤（見 docs/architecture/research/page-detection.md）：
  - 這類照片候選來源不夠，回傳整張圖、信心 0.15；
  - synth.py 的海報照片把與背景同色的紅色標題帶切掉，信心卻是 0.97；
  - 有陰影、紙貼近畫面邊的照片挑到亂框。
合成來源：mortise.eval.synth.degrade（一般翻拍）與 degrade_curved（彎曲、同色背景、第二張紙、硬陰影）。
判準一律是「四角最大誤差 < 影像對角線 1%」；信心值 ≥ 0.6（status 為 ok）時必須達到，否則 status 必須不是 ok。
"""

from __future__ import annotations

import math
from pathlib import Path

import cv2
import numpy as np
import pytest

from mortise.eval import synth as S
from mortise.eval.truth import render_truth
from mortise.recognition.page_geometry import find_page_quad

FIX = Path(__file__).resolve().parents[1] / "fixtures"
_PAGES: dict = {}


def page_image(name: str, dpi: float = 150.0) -> np.ndarray:
    key = (name, dpi)
    if key not in _PAGES:
        im, _, _ = render_truth(FIX / f"{name}.pdf", 0, dpi)
        _PAGES[key] = np.ascontiguousarray(im)
    return _PAGES[key]


def corner_err(q: np.ndarray, gt: np.ndarray) -> np.ndarray:
    """四角誤差（容許起點不同的排列）。"""
    q = np.asarray(q, dtype=np.float64)
    best = None
    for s in range(4):
        e = np.linalg.norm(np.roll(q, -s, axis=0) - gt, axis=1)
        if best is None or e.max() < best.max():
            best = e
    return best


def synth_photo(name: str, seed: int, strength: float):
    img = page_image(name)
    out, M, _ = S.degrade(img, "photo", seed=seed, strength=strength)
    h, w = img.shape[:2]
    gt = S.apply_h(M, np.array([[0, 0], [w, 0], [w, h], [0, h]], dtype=np.float64))
    return out, gt


def table_page(w: int = 900, h: int = 1270) -> np.ndarray:
    """像課表的頁面：標題、外框與格線、格子裡的字、頁尾。"""
    img = np.full((h, w, 3), 250, np.uint8)
    cv2.putText(img, "Module Study Plan 6003", (70, 110), cv2.FONT_HERSHEY_SIMPLEX, 1.2, (30, 30, 30), 2, cv2.LINE_AA)
    cv2.putText(img, "Thursday mornings from 24th Sept", (70, 150), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (40, 40, 40), 1, cv2.LINE_AA)
    x0, y0, x1, y1 = 70, 190, w - 70, h - 190
    cv2.rectangle(img, (x0, y0), (x1, y1), (20, 20, 20), 2)
    cols = [x0, x0 + 110, x0 + 380, x0 + 600, x1]
    for x in cols[1:-1]:
        cv2.line(img, (x, y0), (x, y1), (20, 20, 20), 1)
    rows = np.linspace(y0, y1, 13).astype(int)
    for y in rows[1:-1]:
        cv2.line(img, (x0, y), (x1, y), (20, 20, 20), 1)
    rng = np.random.default_rng(0)
    words = ["Planning", "Policy", "Session", "Thames", "Valley", "workshop", "Strategic", "Oct", "Nov", "group"]
    for r in range(12):
        for c in range(4):
            for line in range(2):
                txt = " ".join(rng.choice(words, 2))
                cv2.putText(img, txt, (cols[c] + 8, rows[r] + 28 + 26 * line), cv2.FONT_HERSHEY_SIMPLEX, 0.55, (30, 30, 30), 1, cv2.LINE_AA)
    cv2.putText(img, "Module Guide 2026-27", (w // 2 - 130, h - 110), cv2.FONT_HERSHEY_SIMPLEX, 0.8, (40, 40, 40), 1, cv2.LINE_AA)
    return img


def check_honest(q, gt, img_shape, where: str) -> float:
    """信心與狀態要誠實：信心 ≥ 0.6 或 status 為 ok 時，四角誤差必須 < 對角線 1%。回傳最大誤差佔對角線的比例。"""
    diag = math.hypot(img_shape[1], img_shape[0])
    rel = float(corner_err(q.corners, gt).max()) / diag
    if q.confidence >= 0.6 or q.status == "ok":
        assert rel < 0.01, (where, q.method, q.status, q.confidence, rel, q.notes)
    return rel


# ------------------------------------------------------------------ 以前錯的 synth.py 照片


@pytest.mark.parametrize(
    "name,seed,strength",
    [
        ("poster", 5, 2.0),  # 紅色標題帶與背景同色：以前切掉標題帶、信心 0.97
        ("doc_a4", 2, 2.0),  # 大片陰影、紙貼近畫面右緣：以前挑到亂框
        ("doc_a4", 4, 2.0),
        ("cv_two_column", 2, 2.0),
    ],
)
def test_former_failures_now_correct(name, seed, strength):
    photo, gt = synth_photo(name, seed, strength)
    q = find_page_quad(photo)
    assert q.method == "edge_fit", q.notes
    diag = math.hypot(*photo.shape[:2])
    e = corner_err(q.corners, gt)
    # 實測：0.4、0.5、0.9、0.6 px（對角線約 2200 px）
    assert e.max() < 0.01 * diag, (e, q.notes)
    assert q.status in ("ok", "cut_off"), q.status  # 紙貼近畫面邊的會標 cut_off（角在畫面外或貼邊），請使用者確認


def test_red_header_band_kept_with_confidence():
    """海報的紅色標題帶與背景顏色接近：整張海報（含標題帶）要框到，且有足夠信心（不必問使用者）。"""
    photo, gt = synth_photo("poster", 5, 2.0)
    q = find_page_quad(photo)
    assert q.status == "ok" and q.confidence >= 0.8, (q.status, q.confidence)  # 實測 0.97
    assert corner_err(q.corners, gt).max() < 2.0


def test_confidence_is_honest_on_synth_photos():
    """一批 synth.degrade 翻拍照片：信心 ≥ 0.6 的四角都要在對角線 1% 內；大部分要找對。"""
    rels = []
    for name in ("doc_a4", "poster", "cv_two_column"):
        for seed in (0, 1):
            for strength in (1.0, 2.0):
                photo, gt = synth_photo(name, seed, strength)
                q = find_page_quad(photo)
                rels.append(check_honest(q, gt, photo.shape, (name, seed, strength)))
    # 實測 12/12 在 1% 內
    assert sum(r < 0.01 for r in rels) >= 11, rels


# ------------------------------------------------------------------ 創辦人照片的型態：米色布、第二張紙、上緣捲起


def _founder_like(kind: str, seed: int, second: bool = True, background: str = "paper_like"):
    if not hasattr(S, "degrade_curved"):
        pytest.skip("synth.degrade_curved 尚未提供")
    page = table_page()
    prm = {"curved_out_long": 1100, "curved_background": background, "curved_second_sheet": second}
    out, _, info = S.degrade_curved(page, kind, seed=seed, params=prm)
    return out, np.array(info["page_corners"], dtype=np.float64)


@pytest.mark.parametrize("seed", [0, 1, 2])
def test_founder_like_flat_sheet_on_paper_colored_cloth(seed):
    """紙平放、背景是與紙同色的布、旁邊有第二張紙：要找到主紙，信心與狀態誠實。"""
    photo, gt = _founder_like("flat", seed)
    q = find_page_quad(photo)
    assert q.method == "edge_fit", q.notes
    rel = check_honest(q, gt, photo.shape, ("flat", seed))
    assert rel < 0.01, (rel, q.status, q.notes)  # 實測三張都在 0.5% 內


@pytest.mark.parametrize("seed", [0, 1, 2, 3])
def test_founder_like_curled_top_is_honest(seed):
    """上緣捲起：直線四角框不到捲起的部分。不要求四角準，但不可錯得很有信心；有找到紙（不是整張圖）。"""
    photo, gt = _founder_like("curl_top", seed)
    q = find_page_quad(photo)
    assert q.method != "full_image", q.notes
    check_honest(q, gt, photo.shape, ("curl_top", seed))
    if q.status == "curved_edge":
        assert q.boundary is not None and q.boundary.shape[1] == 2
        assert max(q.edge_bow_px) > 0


# ------------------------------------------------------------------ 多張紙、紙被切掉


def _warp_onto(canvas: np.ndarray, page: np.ndarray, dst: np.ndarray) -> np.ndarray:
    h, w = page.shape[:2]
    src = np.array([[0, 0], [w, 0], [w, h], [0, h]], dtype=np.float32)
    M = cv2.getPerspectiveTransform(src - 0.5, dst.astype(np.float32) - 0.5)
    H, W = canvas.shape[:2]
    warped = cv2.warpPerspective(page, M, (W, H), flags=cv2.INTER_AREA)
    mask = cv2.warpPerspective(np.ones((h, w), np.float32), M, (W, H), flags=cv2.INTER_LINEAR)[..., None]
    return (canvas * (1 - mask) + warped * mask).astype(np.float32)


def _desk(W: int, H: int, seed: int) -> np.ndarray:
    rng = np.random.default_rng(seed)
    n = cv2.resize(rng.standard_normal((H // 40, W // 40)).astype(np.float32), (W, H), interpolation=cv2.INTER_CUBIC)
    return np.array([70, 62, 58], np.float32)[None, None] + (n * 4)[..., None]


def _finish(img: np.ndarray, seed: int) -> np.ndarray:
    rng = np.random.default_rng(seed)
    img = cv2.GaussianBlur(img, (0, 0), 0.8) + rng.normal(0, 2.5, img.shape).astype(np.float32)
    return np.clip(img, 0, 255).astype(np.uint8)


def test_two_similar_sheets_report_multiple_pages():
    """兩張差不多的紙並排：分數接近，status 要是 multiple_pages，另一張要在其他候選裡。"""
    page = cv2.resize(page_image("doc_a4", 60), None, fx=0.9, fy=0.9, interpolation=cv2.INTER_AREA)
    W, H = 1400, 1000
    img = _desk(W, H, 1)
    a = np.array([[90, 140], [640, 120], [660, 880], [80, 900]], dtype=np.float64)
    b = np.array([[760, 130], [1310, 150], [1320, 905], [745, 890]], dtype=np.float64)
    img = _warp_onto(img, page, a)
    img = _warp_onto(img, page, b)
    photo = _finish(img, 1)
    q = find_page_quad(photo)
    assert q.method == "edge_fit"
    assert q.status == "multiple_pages", (q.status, q.confidence, q.notes)
    assert q.confidence < 0.6
    found = [q.corners] + list(q.alternatives)
    for gt in (a, b):
        assert min(corner_err(f, gt).max() for f in found) < 3.0, [np.round(f) for f in found]


def test_page_cut_off_by_frame_offers_three_side_candidate():
    """紙的下緣超出畫面：status 是 cut_off；主答案仍是整張圖（請使用者拉四角），其他候選第一個是「三條邊＋畫面邊」。"""
    page = page_image("doc_a4", 60)
    W, H = 1000, 1100
    img = _desk(W, H, 2)
    a = np.array([[180, 160], [820, 140], [860, 1300], [150, 1320]], dtype=np.float64)
    img = _warp_onto(img, page, a)
    photo = _finish(img, 2)
    q = find_page_quad(photo)
    assert q.status == "cut_off", (q.status, q.notes)
    assert q.confidence < 0.6
    cands = ([q.corners] if q.method != "full_image" else []) + list(q.alternatives)
    assert cands, q.notes
    c = np.asarray(cands[0])
    top = c[np.argsort(c[:, 1])[:2]]
    top = top[np.argsort(top[:, 0])]
    assert np.abs(top - a[:2]).max() < 3.0, (top, a[:2])  # 看得到的兩個上角要準
    assert c[:, 1].max() >= H - 4.0  # 下緣貼著畫面邊
