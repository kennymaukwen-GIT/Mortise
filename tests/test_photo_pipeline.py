"""照片路線接上找紙與攤平之後的①②：合成照片（不放任何真實照片），只跑①②，不跑辨識。

對應 stages/s2_page.py 的照片路線：find_page_quad 的 status、其他候選、各角誤差、彎曲外框寫進 PageSpec；
rectify 開攤平（dewarp_photos）；紙被畫面切掉時預選「三條邊＋畫面邊」；使用者拉的四角永遠優先；
要不要請使用者確認（quad_needs_review）與白話理由。UVDoc 權重一律不下載（MORTISE_OFFLINE=1、dewarp_uvdoc="off"）。
"""

from __future__ import annotations

import copy
import math
from pathlib import Path

import cv2
import numpy as np
import pytest
from PIL import Image

from mortise.artifacts import Correction, InputType, Intent
from mortise.config import Config
from mortise.eval import synth as S
from mortise.eval.truth import render_truth
from mortise.pipeline.store import Store
from mortise.stages import s1_input, s2_page

FIX = Path(__file__).resolve().parent / "fixtures"
_PAGES: dict = {}


@pytest.fixture(autouse=True)
def _offline(monkeypatch):
    monkeypatch.setenv("MORTISE_OFFLINE", "1")


def _cfg(**page) -> Config:
    cfg = Config.load()
    data = copy.deepcopy(cfg.data)
    data["page"].update({"dewarp_uvdoc": "off", **page})
    return Config(data)


def _page(name: str, dpi: float) -> np.ndarray:
    key = (name, dpi)
    if key not in _PAGES:
        _PAGES[key] = np.ascontiguousarray(render_truth(FIX / f"{name}.pdf", 0, dpi)[0])
    return _PAGES[key]


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


FLAT_GT = np.array([[160, 120], [930, 150], [960, 1180], [130, 1150]], dtype=np.float64)
CUT_GT = np.array([[180, 160], [820, 140], [860, 1300], [150, 1320]], dtype=np.float64)  # 下緣超出畫面（畫面高 1100）


def flat_photo() -> np.ndarray:
    return _finish(_warp_onto(_desk(1100, 1300, 3), _page("doc_a4", 100), FLAT_GT), 3)


def cut_off_photo() -> np.ndarray:
    return _finish(_warp_onto(_desk(1000, 1100, 2), _page("doc_a4", 60), CUT_GT), 2)


def _run(tmp_path: Path, img: np.ndarray, name: str, corrections=(), cfg: Config | None = None, fmt: str = "JPEG"):
    cfg = cfg or _cfg()
    path = tmp_path / name
    Image.fromarray(img).save(path, fmt, **({"quality": 90} if fmt == "JPEG" else {}))
    store = Store(tmp_path / (Path(name).stem + "-job"))
    doc = s1_input.run(path, name, Intent(), cfg, store)
    spec = s2_page.run(doc, 0, cfg, list(corrections), store)
    return doc, spec, store


def _corners(spec) -> np.ndarray:
    return np.array([[p.x, p.y] for p in spec.source_corners], dtype=np.float64)


def _user(value) -> Correction:
    return Correction(id="u-test", target_stage=2, page_index=0, field="corners", new_value=value, source="user")


def test_flat_photo_ok_and_not_asked(tmp_path):
    doc, spec, _ = _run(tmp_path, flat_photo(), "flat.jpg")
    t = doc.pages[0].input_type
    assert t.value == InputType.PHOTO and t.confidence >= 0.7  # 以前 PageQuad 多了欄位時曾退回「照片 0.5」
    assert spec.quad_method == "edge_fit" and spec.quad_status == "ok", (spec.quad_status, spec.notes)
    assert spec.quad_needs_review is False and spec.quad_review_reason == ""
    assert np.abs(_corners(spec) - FLAT_GT).max() < 3.0
    assert len(spec.quad_corner_error_px) == 4 and max(spec.quad_corner_error_px) < 3.0
    assert spec.source_size == (1100, 1300)
    assert spec.dewarp_map is None and spec.dewarp_source == ""  # 平的紙只做透視
    assert spec.dewarp_confidence is not None  # 有試過攤平（判定是平的）


def test_s1_classify_still_reads_page_quad():
    """①的照片／截圖分類用同一個 find_page_quad（PageQuad 多了 status 等欄位也不能失敗）。"""
    cfg = _cfg()
    feats = s1_input.image_features(Image.fromarray(flat_photo()), cfg.section("input"))
    kind, conf, why = s1_input.classify_image(feats, False, cfg.section("input"))
    assert feats.quad_method == "edge_fit" and feats.quad_conf >= 0.5, feats.summary()
    assert kind == InputType.PHOTO and conf >= 0.9, why


def test_cut_off_preselects_three_sides_and_asks(tmp_path):
    _, spec, _ = _run(tmp_path, cut_off_photo(), "cut.jpg")
    assert spec.quad_status == "cut_off", spec.notes
    assert spec.quad_method == "frame_edges"
    assert spec.quad_needs_review is True and "超出畫面" in spec.quad_review_reason
    assert spec.quad_preselect == 0 and spec.quad_alternative_labels[0] == "三條邊＋畫面邊"
    alt0 = np.array([[p.x, p.y] for p in spec.quad_alternatives[0]])
    assert np.abs(_corners(spec) - alt0).max() < 1e-6  # 目前的四角就是預選的那一組
    top = alt0[np.argsort(alt0[:, 1])[:2]]
    top = top[np.argsort(top[:, 0])]
    assert np.abs(top - CUT_GT[:2]).max() < 3.0  # 看得到的兩個上角要準
    assert len(spec.quad_alternative_scores) == len(spec.quad_alternatives) == len(spec.quad_alternative_labels)
    assert spec.working_image_size[0] < 1000  # 不是整張圖


def test_user_corners_always_win(tmp_path):
    _, spec, _ = _run(tmp_path, cut_off_photo(), "cut.jpg", [_user(CUT_GT.tolist())])
    assert spec.quad_method == "user" and spec.quad_confidence == 1.0 and spec.quad_status == "user"
    assert spec.quad_needs_review is False and spec.quad_review_reason == ""
    assert np.abs(_corners(spec) - CUT_GT).max() < 1e-6  # 角落在畫面外也照用
    assert spec.dewarp_map is None  # 使用者的四角不再攤平
    assert any("你拉的位置" in n for n in spec.notes)
    w, h = spec.working_image_size
    assert abs(h / w - spec.aspect_ratio) < 0.01


def test_user_chooses_whole_image(tmp_path):
    W, H = 1000, 1100
    _, spec, _ = _run(tmp_path, cut_off_photo(), "cut.jpg", [_user([[0, 0], [W, 0], [W, H], [0, H]])])
    assert spec.quad_method == "user" and spec.quad_needs_review is False
    assert spec.working_image_size == (W, H) or abs(spec.working_image_size[0] - W) <= 1
    assert abs(spec.aspect_ratio - H / W) < 0.01


@pytest.mark.parametrize("bad", ["亂寫", [[0, 0], [10, 0]], [[0, 0], [900, 900], [900, 0], [0, 900]], [[1, 1], [2, 1], [2, 2], [1, 2]]])
def test_bad_user_corners_are_ignored(tmp_path, bad):
    """格式不對、自我交叉（順序亂了會自動排好）、或小到不可能是頁面的四角修正單：略過並寫進備註，不讓整份工作失敗。"""
    _, spec, _ = _run(tmp_path, cut_off_photo(), "cut.jpg", [_user(bad)])
    if bad == [[0, 0], [900, 900], [900, 0], [0, 900]]:
        assert spec.quad_method == "user"  # 只是順序亂：排成左上、右上、右下、左下後照用
        assert np.abs(_corners(spec) - np.array([[0, 0], [900, 0], [900, 900], [0, 900]])).max() < 1e-6
    else:
        assert spec.quad_method != "user"
        assert any("四角修正單" in n for n in spec.notes), spec.notes


def test_curved_photo_is_dewarped_with_map(tmp_path):
    page = _page("cv_two_column", 150)
    photo, _, _ = S.degrade_curved(page, "crease", 37)
    _, spec, store = _run(tmp_path, photo, "crease.png", fmt="PNG")
    assert spec.quad_status == "curved_edge"
    assert spec.dewarp_map is not None and spec.dewarp_source in ("measured", "uvdoc_prior"), spec.notes
    assert spec.dewarp_residual_px is not None and spec.dewarp_residual_px < 3.0
    z = np.load(store.blob_path(spec.dewarp_map))
    w, h = spec.working_image_size
    assert z["map_x"].shape == (h, w) and z["map_y"].shape == (h, w)
    assert len(spec.source_outline) >= 8  # 攤平時量到的外框
    assert spec.quad_needs_review is True  # 紙邊是彎的：請使用者確認
    # 對應圖的四個角落要落在原照片的紙角附近（與 source_corners 一致）
    c = _corners(spec)
    got = np.array([[z["map_x"][0, 0], z["map_y"][0, 0]], [z["map_x"][0, -1], z["map_y"][0, -1]],
                    [z["map_x"][-1, -1], z["map_y"][-1, -1]], [z["map_x"][-1, 0], z["map_y"][-1, 0]]])
    assert np.abs(got - c).max() < 0.02 * math.hypot(*photo.shape[:2])


def test_dewarp_can_be_turned_off(tmp_path):
    page = _page("cv_two_column", 150)
    photo, _, _ = S.degrade_curved(page, "crease", 37)
    _, spec, _ = _run(tmp_path, photo, "crease.png", cfg=_cfg(dewarp_photos=False), fmt="PNG")
    assert spec.dewarp_map is None and spec.dewarp_confidence is None
    assert spec.quad_status == "curved_edge" and spec.quad_needs_review is True


def test_screenshot_is_not_photo(tmp_path):
    img = _page("slide_basic", 72)
    doc, spec, _ = _run(tmp_path, img, "shot.png", fmt="PNG")
    assert doc.pages[0].input_type.value == InputType.SCREENSHOT
    assert spec.quad_status == "not_photo" and spec.quad_needs_review is False
    assert spec.quad_method == "full_image" and spec.working_image_size == (img.shape[1], img.shape[0])


def test_review_threshold_comes_from_config(tmp_path):
    """quad_review_confidence 由設定檔決定：門檻拉到 0.99，信心 0.98 的四角也要確認。"""
    _, spec, _ = _run(tmp_path, flat_photo(), "flat.jpg", cfg=_cfg(quad_review_confidence=0.99))
    assert spec.quad_confidence < 0.99
    assert spec.quad_needs_review is True and "信心值" in spec.quad_review_reason


def test_pdf_page_spec_defaults_unchanged(tmp_path):
    cfg = _cfg()
    store = Store(tmp_path / "pdf-job")
    doc = s1_input.run(FIX / "slide_basic.pdf", "slide_basic.pdf", Intent(), cfg, store)
    spec = s2_page.run(doc, 0, cfg, [], store)
    assert spec.quad_method == "pdf_box" and spec.quad_needs_review is False and spec.source_size is None


def test_first_use_does_not_wait_for_uvdoc_download(tmp_path, monkeypatch):
    """第一次使用、快取裡沒有 UVDoc：②不等下載（背景下載、這張先用量測式），備註寫明。這裡把下載換成卡住的假函數，不連網。"""
    import threading
    import time

    from mortise.recognition import dewarp as D

    monkeypatch.delenv("MORTISE_OFFLINE", raising=False)
    monkeypatch.setenv("MORTISE_CACHE_DIR", str(tmp_path / "cache"))
    gate = threading.Event()

    def stuck(params=None):
        gate.wait(60)
        return None, ["測試：沒有下載"]

    monkeypatch.setattr(D, "ensure_uvdoc_model", stuck)
    photo, _, _ = S.degrade_curved(_page("cv_two_column", 150), "crease", 37)
    try:
        t0 = time.time()
        _, spec, _ = _run(tmp_path, photo, "crease.png", cfg=_cfg(dewarp_uvdoc="auto"), fmt="PNG")
        assert time.time() - t0 < 30
        assert any("背景下載" in n for n in spec.notes), spec.notes
        assert spec.dewarp_map is not None  # 量測式攤平照做
    finally:
        gate.set()
