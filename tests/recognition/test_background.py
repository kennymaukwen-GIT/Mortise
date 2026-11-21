"""背景還原（background.py）的測試：在已知底圖上畫文字與形狀，遮住後還原，量與原底圖的平均絕對誤差與 model 判斷。

合成方式
    底圖：純色、線性漸層、放射漸層、紙紋（斑駁＋纖維＋顆粒）、照片般的紋理、左半純色右半照片（mixed）。
    前景：cv2 畫的多行文字、矩形、圓形、一塊「圖片」；遮罩是前景像素再膨脹 2 像素（模擬上游「已略為膨脹」）。
    干擾：整張加高斯雜訊 σ=3 與 JPEG 85（標準答案仍是乾淨底圖）。
"""

from __future__ import annotations

import functools
from pathlib import Path

import cv2
import numpy as np
import pytest

from mortise.eval.truth import render_truth
from mortise.recognition.background import reconstruct_background

FIX = Path(__file__).resolve().parents[1] / "fixtures"
H, W = 600, 800


def _lowfreq(rng, h, w, scale, amp):
    n = rng.standard_normal((max(2, h // scale), max(2, w // scale))).astype(np.float32)
    n = cv2.resize(n, (w, h), interpolation=cv2.INTER_CUBIC)
    return n / (n.std() + 1e-6) * amp


def make_bg(kind: str, rng: np.random.Generator, h: int = H, w: int = W) -> np.ndarray:
    ys, xs = np.mgrid[0:h, 0:w].astype(np.float32)
    if kind == "flat":
        img = np.zeros((h, w, 3), np.float32) + np.array([236, 228, 212], np.float32)
    elif kind == "linear":
        t = (xs * 0.8 + ys * 0.6) / (w * 0.8 + h * 0.6)
        img = np.array([30, 60, 140], np.float32) * (1 - t[..., None]) + np.array([200, 120, 60], np.float32) * t[..., None]
    elif kind == "radial":
        r = np.hypot(xs - w * 0.4, ys - h * 0.45) / np.hypot(w, h) * 1.6
        t = np.clip(r, 0, 1)[..., None]
        img = np.array([250, 245, 225], np.float32) * (1 - t) + np.array([170, 150, 120], np.float32) * t
    elif kind == "paper":
        img = np.zeros((h, w, 3), np.float32) + np.array([236, 230, 214], np.float32)
        img += (_lowfreq(rng, h, w, 6, 7.0) + _lowfreq(rng, h, w, 20, 5.0))[..., None]
        fib = np.zeros((h, w), np.float32)
        for _ in range(180):
            x, y = int(rng.integers(0, w)), int(rng.integers(0, h))
            ang = rng.uniform(0, np.pi)
            L = rng.uniform(10, 40)
            cv2.line(fib, (x, y), (int(x + L * np.cos(ang)), int(y + L * np.sin(ang))), 1.0, 1, cv2.LINE_AA)
        img -= (fib * 18)[..., None]
        img += rng.standard_normal((h, w, 3)).astype(np.float32) * 1.5
    elif kind == "photo":
        img = np.zeros((h, w, 3), np.float32)
        for c, base in enumerate((90, 120, 80)):
            img[..., c] = base + _lowfreq(rng, h, w, 80, 30) + _lowfreq(rng, h, w, 12, 18) + _lowfreq(rng, h, w, 4, 10)
        for _ in range(25):
            col = rng.integers(20, 235, 3).tolist()
            cv2.ellipse(img, (int(rng.integers(0, w)), int(rng.integers(0, h))), (int(rng.integers(10, 80)), int(rng.integers(10, 80))), float(rng.uniform(0, 180)), 0, 360, col, -1, cv2.LINE_AA)
        img = cv2.GaussianBlur(img, (0, 0), 1.2)
    elif kind == "mixed":
        img = make_bg("photo", rng, h, w)
        img[:, : int(w * 0.55)] = (52, 70, 110)
        return img
    else:
        raise ValueError(kind)
    return np.clip(img, 0, 255).astype(np.uint8)


WORDS = ["Mortise", "背景", "Quarterly", "Report", "2026", "revenue", "slide", "edit", "layout", "text"]


def draw_foreground(bg: np.ndarray, rng: np.random.Generator, n_lines: int = 9, shapes: bool = True) -> tuple[np.ndarray, np.ndarray]:
    img = bg.copy()
    m = np.zeros(bg.shape[:2], np.uint8)
    h, w = bg.shape[:2]
    for i in range(n_lines):
        s = " ".join(rng.choice([wd for wd in WORDS if wd.isascii()], 3))
        scale = float(rng.uniform(0.6, 1.6))
        th = 1 if scale < 1.0 else 2
        org = (int(rng.integers(10, w // 2)), int(40 + i * (h - 60) / n_lines))
        col = tuple(int(v) for v in rng.integers(0, 80, 3)) if i % 2 else (250, 250, 250)
        cv2.putText(img, s, org, cv2.FONT_HERSHEY_SIMPLEX, scale, col, th, cv2.LINE_AA)
        cv2.putText(m, s, org, cv2.FONT_HERSHEY_SIMPLEX, scale, 255, th, cv2.LINE_AA)
    if shapes:
        cv2.rectangle(img, (int(w * 0.62), int(h * 0.1)), (int(w * 0.9), int(h * 0.3)), (200, 40, 40), -1)
        cv2.rectangle(m, (int(w * 0.62), int(h * 0.1)), (int(w * 0.9), int(h * 0.3)), 255, -1)
        cv2.circle(img, (int(w * 0.25), int(h * 0.78)), 45, (30, 140, 60), -1, cv2.LINE_AA)
        cv2.circle(m, (int(w * 0.25), int(h * 0.78)), 45, 255, -1, cv2.LINE_AA)
        x0, y0, x1, y1 = int(w * 0.66), int(h * 0.6), int(w * 0.86), int(h * 0.85)  # 一塊「圖片」
        img[y0:y1, x0:x1] = rng.integers(0, 255, (y1 - y0, x1 - x0, 3)).astype(np.uint8)
        m[y0:y1, x0:x1] = 255
    mask = cv2.dilate((m > 0).astype(np.uint8), cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (5, 5))) > 0
    return img, mask


def disturb(img: np.ndarray, rng) -> np.ndarray:
    out = np.clip(img.astype(np.float32) + rng.standard_normal(img.shape).astype(np.float32) * 3.0, 0, 255).astype(np.uint8)
    ok, buf = cv2.imencode(".jpg", out[..., ::-1], [cv2.IMWRITE_JPEG_QUALITY, 85])
    return cv2.imdecode(buf, cv2.IMREAD_COLOR)[..., ::-1].copy()


EXPECTED = {"flat": "flat", "linear": "gradient", "radial": "gradient", "paper": "inpaint", "photo": "inpaint", "mixed": "mixed"}


@functools.lru_cache(maxsize=None)
def run_case(kind: str, seed: int, noisy: bool) -> dict:
    rng = np.random.default_rng(seed)
    bg = make_bg(kind, rng)
    img, mask = draw_foreground(bg, rng)
    if noisy:
        img = disturb(img, rng)
    res = reconstruct_background(img, mask)
    diff = np.abs(res.image.astype(np.float64) - bg.astype(np.float64))
    return {
        "kind": kind,
        "noisy": noisy,
        "model": res.model,
        "expected": EXPECTED[kind],
        "mae_masked": float(diff[mask].mean()),
        "mae_all": float(diff.mean()),
        "is_blank": res.is_blank,
        "conf": res.confidence,
        "gen_frac": res.generated_fraction,
        "gen_boxes": len(res.generated_boxes),
        "res": res,
        "mask": mask,
    }


def all_cases(noisy: bool) -> list[dict]:
    return [run_case(k, s, noisy) for k in EXPECTED for s in (1, 2)]


# ------------------------------------------------------------------ 測試


def test_flat_background():
    for noisy in (False, True):
        r = run_case("flat", 1, noisy)
        assert r["model"] == "flat" and r["is_blank"]
        assert r["res"].flat_color is not None and r["gen_boxes"] == 0 and r["gen_frac"] == 0.0
        assert r["mae_masked"] < (0.6 if not noisy else 1.0), r  # 實測：乾淨 0.33、干擾 0.33（中位色取整）


def test_gradient_backgrounds():
    for kind in ("linear", "radial"):
        for noisy in (False, True):
            r = run_case(kind, 1, noisy)
            assert r["model"] == "gradient", (kind, noisy, r["res"].notes)
            assert not r["is_blank"] and r["gen_boxes"] == 0
            assert r["res"].gradient is not None
            lim = {"linear": 0.8, "radial": 2.0}[kind]
            assert r["mae_masked"] < lim, (kind, noisy, r["mae_masked"])


def test_textured_backgrounds_are_inpainted_and_reported():
    for kind in ("paper", "photo"):
        r = run_case(kind, 1, False)
        assert r["model"] in ("inpaint", "mixed"), r["res"].notes
        assert r["gen_boxes"] > 0 and r["gen_frac"] > 0.03
        assert not r["is_blank"]
        assert r["conf"] < 0.6  # 紋理上的修補品質有限，要照實降信心
        g = r["res"].generated_mask
        assert g is not None and g.sum() > 0 and not (g & ~r["mask"]).any()  # 只有遮罩內的像素可能是生成的
    # 品質上限：紙紋 MAE 實測約 7.4、照片約 27
    assert run_case("paper", 1, False)["mae_masked"] < 9.5
    assert run_case("photo", 1, False)["mae_masked"] < 32


def test_mixed_background():
    r = run_case("mixed", 1, False)
    assert r["model"] == "mixed", r["res"].notes
    methods = {reg["method"] for reg in r["res"].regions}
    assert "local_flat" in methods and "inpaint" in methods
    # 左半純色區的遮罩應幾乎完美還原
    left = np.zeros_like(r["mask"])
    left[:, : int(W * 0.5)] = True
    sel = r["mask"] & left
    diff = np.abs(r["res"].image.astype(float) - make_bg("mixed", np.random.default_rng(1)).astype(float))
    assert diff[sel].mean() < 1.0


def test_model_accuracy():
    """model 判斷正確率：乾淨與干擾各 12 例（6 種底 × 2 個亂數種子）。"""
    for noisy, need in ((False, 1.0), (True, 0.9)):
        cases = all_cases(noisy)
        acc = np.mean([c["model"] == c["expected"] for c in cases])
        assert acc >= need, (noisy, [(c["kind"], c["model"]) for c in cases])


def test_flat_fixture_slide():
    """fixture：slide_basic 畫成影像，以 truth 的文字框、圖片框、形狀框為遮罩 → 純白底，is_blank。"""
    img, truth, _ = render_truth(FIX / "slide_basic.pdf", 0, 72)
    h, w = img.shape[:2]
    m = np.zeros((h, w), np.uint8)
    for el in list(truth.texts) + list(truth.images) + [s for s in truth.shapes if not s.is_page_background]:
        q = el.quad
        pts = np.array([[pt.x * w, pt.y * w] for pt in (q.tl, q.tr, q.br, q.bl)], np.int32)
        cv2.fillPoly(m, [pts], 255)
    mask = cv2.dilate(m, np.ones((7, 7), np.uint8)) > 0
    res = reconstruct_background(img, mask)
    assert res.model == "flat" and res.is_blank
    assert max(abs(c - 255) for c in res.flat_color) <= 1


def test_empty_and_full_masks():
    rng = np.random.default_rng(0)
    bg = make_bg("photo", rng)
    r = reconstruct_background(bg, np.zeros(bg.shape[:2], bool))
    assert r.model == "inpaint" and r.generated_boxes == [] and np.array_equal(r.image, bg)
    r = reconstruct_background(bg, np.ones(bg.shape[:2], bool))
    assert r.confidence < 0.1 and r.generated_fraction == 1.0


def test_params_override():
    rng = np.random.default_rng(1)
    bg = make_bg("paper", rng)
    img, mask = draw_foreground(bg, rng)
    # 放寬容許色差 → 紙紋被當成純色紙（檔案急救的「紙張當空白頁」）
    r = reconstruct_background(img, mask, {"flat_tol_de": 12.0})
    assert r.model == "flat" and r.is_blank


def report() -> None:  # pragma: no cover - 供手動量測用
    for noisy in (False, True):
        for c in all_cases(noisy):
            print({k: (round(v, 3) if isinstance(v, float) else v) for k, v in c.items() if k not in ("res", "mask")})


if __name__ == "__main__":  # pragma: no cover
    report()
