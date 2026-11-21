"""text_attributes.measure_line 的精準度測試。

合成測試（主力）：用字型庫的字型把已知文字畫成影像（Pillow + RAQM 排字，與被測模組的 HarfBuzz/FreeType
是不同的渲染路徑），答案已知。兩種畫法：
- scan：以 4 倍解析度畫再以面積平均縮小（近似印刷後掃描，沒有 hinting）。
- screen：直接以目標字級畫（FreeType hinting，近似螢幕截圖）。
干擾：高斯模糊、高斯雜訊、JPEG 壓縮、光照不均（隨機組合）。乾淨與有干擾分開統計。

fixture 測試：render_truth 取得原生 PDF 的影像與標準答案，把 truth 的文字行（文字與四角）轉成 OcrLine
餵進 measure_line（不受 OCR 錯誤影響）。

直接執行本檔（python tests/recognition/test_text_attributes.py）會印出完整報表。
"""

from __future__ import annotations

import io
import math
import sys
import time
from pathlib import Path

import cv2
import numpy as np
import pytest
from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from mortise.recognition.fontlib import FontLibrary  # noqa: E402
from mortise.recognition.text_attributes import DEFAULT_PARAMS, measure_line  # noqa: E402
from mortise.recognition.types import OcrLine  # noqa: E402

LIB = FontLibrary.default()

LATIN_TEXTS = [
    "Quarterly revenue grew",
    "The quick brown fox jumps",
    "Design review notes",
    "Typography and layout",
    "Hello world 2027",
    "Meeting agenda for Monday",
    "Project timeline overview",
    "Budget summary table",
    "Customer feedback highlights",
    "Shipping by July",
]
CJK_TEXTS = ["產品路線圖與時程規劃", "第一季完成影像辨識產線", "字型與字級聯合求解", "會議紀錄與待辦事項", "年度預算摘要報告書"]
MIXED_TEXTS = ["第二季 Word 與 Excel 輸出", "產品路線圖 Product Roadmap", "第三季：desktop beta"]

LATIN_FACES = [
    ("Liberation Sans", "Regular"), ("Liberation Sans", "Bold"), ("Liberation Sans", "Italic"),
    ("Liberation Serif", "Regular"), ("Liberation Serif", "Bold"), ("Liberation Serif", "Italic"),
    ("Carlito", "Regular"), ("Carlito", "Bold"), ("Caladea", "Regular"), ("Caladea", "Italic"),
    ("DejaVu Sans", "Regular"), ("DejaVu Serif", "Bold"), ("Inter", "Regular"), ("Inter", "Bold"),
    ("JetBrains Mono", "Regular"), ("Liberation Mono", "Regular"),
]
SIZES = [10.0, 12.5, 15.0, 19.0, 24.0, 31.0, 40.0, 52.0, 64.0, 72.0]
COLORS = [((25, 25, 30), (250, 250, 246)), ((255, 255, 255), (30, 52, 90)), ((200, 40, 40), (245, 245, 240)), ((240, 220, 120), (40, 40, 40))]


# ====================================================================== 合成資料


def synth_line(family, style, text, size, fg, bg, mode="scan", fake_bold=False, shear_deg=0.0, rng=None):
    """畫一行字，回傳 (影像, OcrLine, 答案 dict)。"""
    face = LIB.find(family, style)
    assert face is not None, (family, style)
    ss = 4 if mode == "scan" else 1
    font = ImageFont.truetype(face.path, size=size * ss, index=face.index, layout_engine=ImageFont.Layout.RAQM)
    length = font.getlength(text) / ss
    pad = int(0.9 * size) + 6
    extra = int(math.tan(math.radians(abs(shear_deg))) * size) + 2
    W = int(length + 2 * pad + extra)
    H = int(2.6 * size + 12)
    yb = int(round(1.75 * size)) + 4
    im = Image.new("RGB", (W * ss, H * ss), bg)
    d = ImageDraw.Draw(im)
    stroke = int(round(0.028 * size * ss)) if fake_bold else 0
    d.text((pad * ss, yb * ss), text, font=font, fill=fg, anchor="ls", stroke_width=stroke, stroke_fill=fg)
    a = np.asarray(im)
    if shear_deg:
        t = math.tan(math.radians(shear_deg))
        M = np.float32([[1, -t, t * yb * ss], [0, 1, 0]])  # 基線以上往右推：x' = x + t·(yb − y)
        a = cv2.warpAffine(a, M, (W * ss, H * ss), flags=cv2.INTER_LINEAR, borderValue=tuple(int(c) for c in bg))
    if ss > 1:
        a = cv2.resize(a, (W, H), interpolation=cv2.INTER_AREA)
    diff = np.abs(a.astype(int) - np.array(bg)).sum(axis=2)
    ys, xs = np.nonzero(diff > 60)
    l, r, t_, b = xs.min(), xs.max() + 1, ys.min(), ys.max() + 1
    rng = rng or np.random.default_rng(0)
    hgt = b - t_
    jit = lambda k: rng.uniform(0.04, 0.16) * hgt * k  # noqa: E731
    quad = np.array([[l - jit(0.5), t_ - jit(1)], [r + jit(0.5), t_ - jit(1)], [r + jit(0.5), b + jit(1)], [l - jit(0.5), b + jit(1)]], float)
    truth = {"size": size, "family": family, "style": style, "baseline": float(yb), "color": fg,
             "bold": "Bold" in style or fake_bold, "italic": "Italic" in style or abs(shear_deg) > 0, "text": text, "mode": mode}
    return np.ascontiguousarray(a), OcrLine(quad=quad, text=text, score=1.0), truth


def disturb(img, rng):
    """隨機組合：模糊、雜訊、JPEG、光照不均。回傳 (影像, 行中央的光照倍率)。"""
    a = img.astype(np.float32)
    gain = 1.0
    if rng.random() < 0.5:
        h, w = a.shape[:2]
        gx = np.linspace(rng.uniform(0.7, 0.85), 1.0, w)[None, :, None]
        gy = np.linspace(1.0, rng.uniform(0.85, 1.0), h)[:, None, None]
        a = a * gx * gy
        gain = float(gx[0, w // 2, 0] * gy[h // 2, 0, 0])
    a = cv2.GaussianBlur(a, (0, 0), rng.uniform(0.5, 1.1))
    a = a + rng.normal(0, rng.uniform(3, 8), a.shape)
    a = np.clip(a, 0, 255).astype(np.uint8)
    buf = io.BytesIO()
    Image.fromarray(a).save(buf, format="JPEG", quality=int(rng.integers(55, 85)))
    return np.asarray(Image.open(buf).convert("RGB")), gain


def build_cases(n_latin=36, n_cjk=10, n_mixed=4, seed=1):
    rng = np.random.default_rng(seed)
    cases = []
    for k in range(n_latin):
        fam, sty = LATIN_FACES[k % len(LATIN_FACES)]
        size = float(SIZES[k % len(SIZES)] * rng.uniform(0.97, 1.03))
        fg, bg = COLORS[k % len(COLORS)]
        mode = "screen" if k % 6 == 5 else "scan"
        cases.append(("latin", synth_line(fam, sty, LATIN_TEXTS[k % len(LATIN_TEXTS)], size, fg, bg, mode, rng=rng)))
    for k in range(n_cjk):
        fam = ["WenQuanYi Zen Hei", "IPAGothic"][k % 2]
        text = CJK_TEXTS[k % len(CJK_TEXTS)]
        if not LIB.find(fam).covers(text):
            fam = "WenQuanYi Zen Hei"
        size = float([12.0, 16.0, 22.0, 30.0, 44.0, 60.0][k % 6] * rng.uniform(0.97, 1.03))
        fg, bg = COLORS[k % len(COLORS)]
        cases.append(("cjk", synth_line(fam, "Regular", text, size, fg, bg, "scan", fake_bold=(k % 4 == 3), shear_deg=(12.0 if k % 5 == 4 else 0.0), rng=rng)))
    for k in range(n_mixed):
        size = float([18.0, 26.0, 36.0, 48.0][k % 4])
        fg, bg = COLORS[k % len(COLORS)]
        cases.append(("mixed", synth_line("WenQuanYi Zen Hei", "Regular", MIXED_TEXTS[k % len(MIXED_TEXTS)], size, fg, bg, "scan", rng=rng)))
    return cases


def evaluate(cases, disturbed=False, seed=7, params=None):
    rng = np.random.default_rng(seed)
    rows = []
    for kind, (img, line, tr) in cases:
        im, gain = disturb(img, rng) if disturbed else (img, 1.0)
        t0 = time.time()
        la = measure_line(im, line, LIB, params)
        dt = time.time() - t0
        run = max(la.runs, key=lambda r: r.end - r.start)
        fams = []
        for m in run.fonts:
            if m.family not in fams:
                fams.append(m.family)
        by = float(np.mean(la.baseline[:, 1]))
        rows.append({
            "kind": kind, "mode": tr["mode"], "size": tr["size"], "meas": run.font_size_px.value, "err": run.font_size_px.error,
            "conf": run.font_size_px.confidence, "top1": bool(fams and fams[0] == tr["family"]), "top3": tr["family"] in fams[:3],
            "bold_ok": run.bold[0] == tr["bold"], "italic_ok": run.italic[0] == tr["italic"],
            "color_err": float(np.mean(np.abs(np.array(run.color, float) - np.array(tr["color"], float) * gain))),
            "base_err": abs(by - tr["baseline"]), "time": dt, "family": tr["family"], "style": tr["style"], "got": fams[:3],
            "n_runs": len(la.runs), "text": tr["text"],
        })
    return rows


def summarize(rows, label=""):
    out = {}
    ae = np.array([abs(r["meas"] - r["size"]) for r in rows])
    pe = np.array([abs(r["meas"] - r["size"]) / r["size"] * 100 for r in rows])
    out["size_mae_px"] = float(ae.mean())
    out["size_mape"] = float(pe.mean())
    for lo, hi in ((10, 16), (16, 28), (28, 48), (48, 80)):
        sel = [i for i, r in enumerate(rows) if lo <= r["size"] < hi]
        if sel:
            out[f"mae_px_{lo}-{hi}"] = float(ae[sel].mean())
            out[f"mape_{lo}-{hi}"] = float(pe[sel].mean())
    lat = [r for r in rows if r["kind"] == "latin"]
    out["top1"] = float(np.mean([r["top1"] for r in rows]))
    out["top3"] = float(np.mean([r["top3"] for r in rows]))
    out["top1_latin"] = float(np.mean([r["top1"] for r in lat])) if lat else float("nan")
    out["top3_latin"] = float(np.mean([r["top3"] for r in lat])) if lat else float("nan")
    out["bold_acc"] = float(np.mean([r["bold_ok"] for r in rows]))
    out["italic_acc"] = float(np.mean([r["italic_ok"] for r in rows]))
    out["color_mae"] = float(np.mean([r["color_err"] for r in rows]))
    out["baseline_mae_px"] = float(np.mean([r["base_err"] for r in rows]))
    # 誤差是否誠實：實際誤差落在回報誤差 2 倍內的比例
    out["within_2sigma"] = float(np.mean([abs(r["meas"] - r["size"]) <= 2 * r["err"] + 1e-9 for r in rows]))
    for k in ("cjk", "mixed"):
        sub = [r for r in rows if r["kind"] == k]
        if sub:
            out[f"mape_{k}"] = float(np.mean([abs(r["meas"] - r["size"]) / r["size"] * 100 for r in sub]))
    out["sec_per_line"] = float(np.mean([r["time"] for r in rows]))
    return out


@pytest.fixture(scope="module")
def synth_results():
    cases = build_cases()
    clean = evaluate(cases, disturbed=False)
    dist = evaluate(cases, disturbed=True)
    return summarize(clean), summarize(dist), clean, dist


# ====================================================================== 合成測試（門檻設在實測值附近）


def test_synthetic_clean(synth_results):
    s, _, rows, _ = synth_results
    print("\n[合成 乾淨]", {k: round(v, 3) for k, v in s.items()})
    assert s["size_mape"] < THRESH["clean_mape"]
    assert s["top1_latin"] >= THRESH["clean_top1_latin"]
    assert s["top3"] >= THRESH["clean_top3"]
    assert s["bold_acc"] >= THRESH["clean_bold"]
    assert s["italic_acc"] >= THRESH["clean_italic"]
    assert s["color_mae"] < THRESH["clean_color"]
    assert s["baseline_mae_px"] < THRESH["clean_baseline"]
    assert s["within_2sigma"] >= THRESH["clean_honest"]


def test_synthetic_disturbed(synth_results):
    _, s, _, rows = synth_results
    print("\n[合成 干擾]", {k: round(v, 3) for k, v in s.items()})
    assert s["size_mape"] < THRESH["dist_mape"]
    assert s["top3"] >= THRESH["dist_top3"]
    assert s["bold_acc"] >= THRESH["dist_bold"]
    assert s["italic_acc"] >= THRESH["dist_italic"]
    assert s["color_mae"] < THRESH["dist_color"]
    assert s["baseline_mae_px"] < THRESH["dist_baseline"]


THRESH = {
    "clean_mape": 3.0, "clean_top1_latin": 0.4, "clean_top3": 0.6, "clean_bold": 0.8, "clean_italic": 0.85,
    "clean_color": 8.0, "clean_baseline": 0.6, "clean_honest": 0.6,
    "dist_mape": 4.0, "dist_top3": 0.5, "dist_bold": 0.7, "dist_italic": 0.8, "dist_color": 12.0, "dist_baseline": 0.8,
}


# ====================================================================== 例外情況（自己合成，答案已知）


def test_cjk_pitch_crosscheck_and_tracking():
    """中文間距法與高度法互相驗證；調字距時間距字級 ≠ 高度字級，要標「調字距嫌疑」並記字距。"""
    img, line, tr = synth_line("WenQuanYi Zen Hei", "Regular", "年度預算摘要報告書", 30.0, (20, 20, 20), (250, 250, 250))
    la = measure_line(img, line, LIB)
    r = la.runs[0]
    assert "pitch" in r.size_evidence and "height" in r.size_evidence
    assert abs(r.size_evidence["pitch"].value - 30.0) < 0.5
    assert r.size_evidence["pitch"].error < r.size_evidence["height"].error
    assert not any("調字距" in n for n in r.notes)
    # 每個字之間加 6 px 字距（逐字畫）
    face = LIB.find("WenQuanYi Zen Hei")
    font = ImageFont.truetype(face.path, size=120, index=face.index)
    text = "年度預算摘要報告書"
    W, H = int(len(text) * (30 + 6) + 60), 90
    im = Image.new("RGB", (W * 4, H * 4), (250, 250, 250))
    d = ImageDraw.Draw(im)
    for k, ch in enumerate(text):
        d.text(((30 + k * 36) * 4, 60 * 4), ch, font=font, fill=(20, 20, 20), anchor="ls")
    a = cv2.resize(np.asarray(im), (W, H), interpolation=cv2.INTER_AREA)
    line = OcrLine(quad=np.array([[26, 28], [30 + 9 * 36 - 2, 28], [30 + 9 * 36 - 2, 70], [26, 70]], float), text=text, score=1.0)
    la = measure_line(a, line, LIB)
    r = la.runs[0]
    assert any("調字距" in n for n in r.notes), r.notes
    assert abs(r.font_size_px.value - 30.0) < 1.5
    assert r.char_spacing_px is not None and abs(r.char_spacing_px.value - 6.0) < 1.5


def test_justified_word_spacing():
    """左右對齊：詞間空白被拉寬，字級只用詞內寬度。"""
    face = LIB.find("Liberation Serif")
    font = ImageFont.truetype(face.path, size=28 * 4, index=face.index, layout_engine=ImageFont.Layout.RAQM)
    words = ["Justified", "text", "has", "wide", "gaps"]
    im = Image.new("RGB", (700 * 4, 80 * 4), (255, 255, 255))
    d = ImageDraw.Draw(im)
    x = 20 * 4
    for w in words:
        d.text((x, 55 * 4), w, font=font, fill=(0, 0, 0), anchor="ls")
        x += font.getlength(w) + 30 * 4  # 空白約 3.5 倍
    a = cv2.resize(np.asarray(im), (700, 80), interpolation=cv2.INTER_AREA)
    diff = (a.sum(axis=2) < 600)
    ys, xs = np.nonzero(diff)
    q = np.array([[xs.min() - 3, ys.min() - 4], [xs.max() + 3, ys.min() - 4], [xs.max() + 3, ys.max() + 4], [xs.min() - 3, ys.max() + 4]], float)
    la = measure_line(a, OcrLine(quad=q, text=" ".join(words), score=1.0), LIB)
    r = la.runs[0]
    assert any("左右對齊" in n for n in r.notes), r.notes
    assert abs(r.font_size_px.value - 28.0) / 28.0 < 0.03


def test_short_line_low_confidence():
    img, line, _ = synth_line("Liberation Sans", "Regular", "Hi", 24.0, (0, 0, 0), (255, 255, 255))
    la = measure_line(img, line, LIB)
    r = la.runs[0]
    assert any("字數不足" in n for n in r.notes)
    long_img, long_line, _ = synth_line("Liberation Sans", "Regular", "Hello quick brown fox", 24.0, (0, 0, 0), (255, 255, 255))
    rl = measure_line(long_img, long_line, LIB).runs[0]
    assert r.font_size_px.confidence < rl.font_size_px.confidence
    assert r.font_size_px.error > rl.font_size_px.error


def test_underline_and_strike_removed_from_mask():
    face = LIB.find("Liberation Sans")
    font = ImageFont.truetype(face.path, size=32 * 4, index=face.index, layout_engine=ImageFont.Layout.RAQM)
    out = {}
    for kind in ("under", "strike"):
        im = Image.new("RGB", (520 * 4, 90 * 4), (255, 255, 255))
        d = ImageDraw.Draw(im)
        d.text((20 * 4, 60 * 4), "Underlined words here", font=font, fill=(10, 10, 10), anchor="ls")
        w = font.getlength("Underlined words here")
        y = (60 + 4) * 4 if kind == "under" else (60 - 8) * 4
        d.rectangle([20 * 4, y, 20 * 4 + w, y + 2 * 4], fill=(10, 10, 10))
        a = cv2.resize(np.asarray(im), (520, 90), interpolation=cv2.INTER_AREA)
        ys, xs = np.nonzero(a.sum(axis=2) < 600)
        q = np.array([[xs.min() - 3, ys.min() - 4], [xs.max() + 3, ys.min() - 4], [xs.max() + 3, ys.max() + 4], [xs.min() - 3, ys.max() + 4]], float)
        la = measure_line(a, OcrLine(quad=q, text="Underlined words here", score=1.0), LIB)
        out[kind] = la.runs[0]
        assert abs(np.mean(la.baseline[:, 1]) - 60.0) < 1.0
    assert out["under"].underline and not out["under"].strikethrough
    assert out["strike"].strikethrough and not out["strike"].underline
    for r in out.values():
        assert abs(r.font_size_px.value - 32.0) / 32.0 < 0.04


def test_polarity_and_colors():
    img, line, tr = synth_line("Carlito", "Bold", "Light text on dark", 30.0, (250, 230, 120), (20, 30, 60))
    la = measure_line(img, line, LIB)
    assert la.polarity == "light_on_dark"
    r = la.runs[0]
    assert np.abs(np.array(r.color) - np.array([250, 230, 120])).max() <= 12
    assert np.abs(np.array(r.background) - np.array([20, 30, 60])).max() <= 8


def test_outline_effect_flagged():
    face = LIB.find("Liberation Sans", "Bold")
    font = ImageFont.truetype(face.path, size=40 * 4, index=face.index, layout_engine=ImageFont.Layout.RAQM)
    im = Image.new("RGB", (560 * 4, 100 * 4), (60, 120, 200))
    d = ImageDraw.Draw(im)
    d.text((20 * 4, 70 * 4), "Outlined title", font=font, fill=(255, 255, 255), anchor="ls", stroke_width=12, stroke_fill=(200, 30, 30))
    a = cv2.resize(np.asarray(im), (560, 100), interpolation=cv2.INTER_AREA)
    ys, xs = np.nonzero(np.abs(a.astype(int) - [60, 120, 200]).sum(axis=2) > 60)
    q = np.array([[xs.min() - 3, ys.min() - 4], [xs.max() + 3, ys.min() - 4], [xs.max() + 3, ys.max() + 4], [xs.min() - 3, ys.max() + 4]], float)
    la = measure_line(a, OcrLine(quad=q, text="Outlined title", score=1.0), LIB)
    allnotes = la.notes + [n for r in la.runs for n in r.notes]
    assert any("不可靠" in n for n in allnotes), allnotes


def test_color_change_splits_runs():
    face = LIB.find("Liberation Sans")
    font = ImageFont.truetype(face.path, size=30 * 4, index=face.index, layout_engine=ImageFont.Layout.RAQM)
    im = Image.new("RGB", (640 * 4, 90 * 4), (255, 255, 255))
    d = ImageDraw.Draw(im)
    a_txt, b_txt = "Black words then ", "red words"
    d.text((20 * 4, 60 * 4), a_txt, font=font, fill=(10, 10, 10), anchor="ls")
    d.text((20 * 4 + font.getlength(a_txt), 60 * 4), b_txt, font=font, fill=(220, 20, 20), anchor="ls")
    a = cv2.resize(np.asarray(im), (640, 90), interpolation=cv2.INTER_AREA)
    ys, xs = np.nonzero(a.sum(axis=2) < 700)
    q = np.array([[xs.min() - 3, ys.min() - 4], [xs.max() + 3, ys.min() - 4], [xs.max() + 3, ys.max() + 4], [xs.min() - 3, ys.max() + 4]], float)
    la = measure_line(a, OcrLine(quad=q, text=a_txt + b_txt, score=1.0), LIB)
    assert len(la.runs) == 2, [(r.start, r.end, r.color) for r in la.runs]
    assert la.runs[1].color[0] > 180 and la.runs[1].color[1] < 70
    assert la.runs[1].start == len(a_txt)


def test_unknown_font_low_confidence():
    """字型庫裡沒有相近的字型：只給一個字型庫（Liberation Mono）去量比例字型的字。"""
    from mortise.recognition.fontlib import FontLibrary as FL

    mono_only = FL([LIB.find("Liberation Mono")])
    img, line, _ = synth_line("Liberation Serif", "Italic", "Elegant serif italic", 30.0, (0, 0, 0), (255, 255, 255))
    la = measure_line(img, line, mono_only)
    r = la.runs[0]
    assert any("無吻合字型" in n for n in r.notes), r.notes
    assert r.font_size_px.confidence < 0.4
    assert r.font_size_px.error >= 0.08 * r.font_size_px.value


def test_rotated_line():
    img, line, tr = synth_line("Liberation Sans", "Regular", "Rotated label text", 26.0, (0, 0, 0), (255, 255, 255))
    rot = np.ascontiguousarray(np.rot90(img))  # 逆時針 90 度：文字由下往上
    h, w = img.shape[:2]
    q = line.quad
    # 原座標 (x, y) → 旋轉後 (y, w − x)
    q2 = np.column_stack([q[:, 1], w - q[:, 0]])
    la = measure_line(rot, OcrLine(quad=q2, text=line.text, score=1.0), LIB)
    r = la.runs[0]
    assert abs(r.font_size_px.value - 26.0) / 26.0 < 0.03
    assert abs(np.mean(la.baseline[:, 0]) - tr["baseline"]) < 1.0


def test_params_override():
    img, line, _ = synth_line("Liberation Sans", "Regular", "Override test", 20.0, (0, 0, 0), (255, 255, 255))
    la = measure_line(img, line, LIB, {"max_candidates": 2})
    assert len(la.runs[0].fonts) <= 2
    assert set(DEFAULT_PARAMS) >= {"topk_redraw", "tracking_tol", "pitch_cv_max", "min_match_score"}


# ====================================================================== fixture 測試（原生 PDF 自我監督）

FIXTURES = ROOT / "tests" / "fixtures"


def fixture_rows(dpi=150.0):
    from mortise.eval.truth import render_truth

    rows = []
    for name in ("slide_basic.pdf", "slide_dark.pdf", "doc_a4.pdf", "poster.pdf"):
        img, truth, spec = render_truth(FIXTURES / name, 0, dpi)
        W = img.shape[1]
        pw = spec.page_width_pt.value
        for tl in truth.texts:
            if tl.hidden or not tl.text.strip():
                continue
            q = np.array([[p.x * W, p.y * W] for p in tl.quad.points])
            la = measure_line(img, OcrLine(quad=q, text=tl.text, score=1.0), LIB)
            # 以字數最多的段比較
            tr_run = max(tl.runs, key=lambda r: len(r.text.strip()))
            my = max(la.runs, key=lambda r: r.end - r.start)
            fams = []
            for m in my.fonts:
                if m.family not in fams:
                    fams.append(m.family)
            truth_pt = tl.font_size * pw
            meas_pt = my.font_size_px.value / W * pw
            tc = tr_run.color.value
            rows.append({
                "file": name, "text": tl.text, "truth_pt": truth_pt, "meas_pt": meas_pt, "err_pt": my.font_size_px.error / W * pw,
                "font": tr_run.font_candidates[0].name if tr_run.font_candidates else "", "got": fams[:3],
                "top1": bool(fams) and fams[0] == (tr_run.font_candidates[0].name if tr_run.font_candidates else ""),
                "top3": (tr_run.font_candidates[0].name if tr_run.font_candidates else "") in fams[:3],
                "color_err": float(np.mean(np.abs(np.array(my.color, float) - [tc.r, tc.g, tc.b]))),
                "rotated": abs(q[1, 1] - q[0, 1]) > abs(q[1, 0] - q[0, 0]),
            })
    return rows


@pytest.fixture(scope="module")
def fx_rows():
    return fixture_rows()


def test_fixtures(fx_rows):
    rows = fx_rows
    up = [r for r in rows if not r["rotated"]]
    ae = np.array([abs(r["meas_pt"] - r["truth_pt"]) for r in up])
    pe = ae / np.array([r["truth_pt"] for r in up]) * 100
    top1 = np.mean([r["top1"] for r in up])
    top3 = np.mean([r["top3"] for r in up])
    cerr = np.mean([r["color_err"] for r in up])
    poster = [r for r in rows if r["file"] == "poster.pdf" and abs(r["truth_pt"] - 37.2) < 0.05]
    print(f"\n[fixture] 行數={len(up)} 字級 MAE={ae.mean():.3f} pt ({pe.mean():.2f} %)  字型 top1={top1:.2f} top3={top3:.2f}  顏色 MAE={cerr:.2f}")
    for r in poster:
        print(f"[fixture] poster 37.2 pt → 量到 {r['meas_pt']:.3f} ± {r['err_pt']:.3f} pt（{r['text']}）")
    for r in rows:
        if r["rotated"]:
            print(f"[fixture] 旋轉行 {r['text']!r}: truth {r['truth_pt']:.2f} pt → {r['meas_pt']:.2f} pt")
    assert pe.mean() < FX["mape"]
    assert top3 >= FX["top3"]
    assert cerr < FX["color"]
    assert poster and abs(poster[0]["meas_pt"] - 37.2) < FX["poster_abs_pt"]


FX = {"mape": 3.0, "top3": 0.6, "color": 10.0, "poster_abs_pt": 0.5}


if __name__ == "__main__":
    cases = build_cases()
    for dist in (False, True):
        rows = evaluate(cases, disturbed=dist)
        print("干擾" if dist else "乾淨", {k: round(v, 3) for k, v in summarize(rows).items()})
        for r in rows:
            flag = "" if r["top1"] else "  <-- " + str(r["got"])
            print(f"   {r['kind']:5s} {r['mode']:6s} {r['family']:17s} {r['style']:8s} {r['size']:5.1f} → {r['meas']:6.2f}±{r['err']:.2f} c={r['conf']:.2f} bold={r['bold_ok']} it={r['italic_ok']} col={r['color_err']:.1f} base={r['base_err']:.2f} runs={r['n_runs']} t={r['time']:.2f}{flag}")
    if "--fixtures" in sys.argv:
        for r in fixture_rows():
            print(f"   {r['file']:16s} {r['truth_pt']:6.2f} → {r['meas_pt']:6.2f}±{r['err_pt']:.2f} pt {r['font']:18s} got={r['got']} col={r['color_err']:.1f} {r['text'][:30]!r}")
