"""mortise/recognition/ocr.py 的測試與精準度量測。

兩組資料：
1. 原生 PDF fixture（render_truth 畫成影像，擷取產線的結果當標準答案）：150 dpi、96 dpi，
   另加 150 dpi 的高斯模糊與 JPEG 壓縮干擾。標準答案的行框是字型上緣到下緣、逐字位置是
   「筆位」（不是墨跡），所以四角與逐字誤差含有字形留白造成的系統差，分開列出。
2. 自己合成的頁面（PIL 畫字，答案是精確的墨跡框與每個字的墨跡起點）：量真正的幾何精度，
   並測旋轉 90／270 度、些微歪斜、深色底、小字、圓形誤判、雜訊、低解析度、光照不均、大圖切塊。

直接執行本檔（python tests/recognition/test_ocr.py）會印出完整數字表。
"""

from __future__ import annotations

import io
import json
import math
import sys
import time
from functools import lru_cache
from pathlib import Path

import cv2
import numpy as np
import pytest
from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from mortise.eval.truth import render_truth  # noqa: E402
from mortise.recognition.ocr import DEFAULT_PARAMS, OcrEngine  # noqa: E402
from mortise.recognition.types import OcrLine  # noqa: E402

FIX = ROOT / "tests" / "fixtures"
PAGES = [("slide_basic", 0), ("slide_dark", 0), ("doc_a4", 0), ("doc_a4", 1), ("poster", 0)]
SANS = "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf"
SANS_B = "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf"
WQY = "/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc"


def _font_path(path: str, pattern: str) -> str:
    if Path(path).exists():
        return path
    import subprocess

    out = subprocess.run(["fc-match", "-f", "%{file}", pattern], capture_output=True, text=True).stdout.strip()
    return out


SANS = _font_path(SANS, "Liberation Sans")
SANS_B = _font_path(SANS_B, "Liberation Sans:bold")
WQY = _font_path(WQY, "WenQuanYi Zen Hei")

# ============================================================================ 評估工具


def _is_wide(ch: str) -> bool:
    import unicodedata

    return unicodedata.east_asian_width(ch) in ("F", "W")


def lev_ops(a: str, b: str):
    """Levenshtein 對齊：回傳 (距離, 操作清單)；操作為 ('eq'|'sub'|'del'|'ins', i, j)。a 是預測，b 是答案。"""
    n, m = len(a), len(b)
    D = np.zeros((n + 1, m + 1), np.int32)
    D[:, 0] = np.arange(n + 1)
    D[0, :] = np.arange(m + 1)
    for i in range(1, n + 1):
        for j in range(1, m + 1):
            D[i, j] = min(D[i - 1, j] + 1, D[i, j - 1] + 1, D[i - 1, j - 1] + (a[i - 1] != b[j - 1]))
    ops = []
    i, j = n, m
    while i > 0 or j > 0:
        if i > 0 and j > 0 and D[i, j] == D[i - 1, j - 1] + (a[i - 1] != b[j - 1]):
            ops.append(("eq" if a[i - 1] == b[j - 1] else "sub", i - 1, j - 1))
            i, j = i - 1, j - 1
        elif i > 0 and D[i, j] == D[i - 1, j] + 1:
            ops.append(("ins", i - 1, None))  # 預測多出來的字
            i -= 1
        else:
            ops.append(("del", None, j - 1))  # 答案有、預測沒讀到
            j -= 1
    return int(D[n, m]), ops[::-1]


def quad_iou(a: np.ndarray, b: np.ndarray) -> float:
    a32, b32 = a.astype(np.float32), b.astype(np.float32)
    aa, ab = abs(cv2.contourArea(a32)), abs(cv2.contourArea(b32))
    if aa <= 0 or ab <= 0:
        return 0.0
    inter, _ = cv2.intersectConvexConvex(a32, b32)
    return float(inter) / (aa + ab - float(inter) + 1e-9)


def match(pred_quads: list[np.ndarray], true_quads: list[np.ndarray], thr: float = 0.5) -> list[tuple[int, int, float]]:
    pairs = []
    for i, pq in enumerate(pred_quads):
        for j, tq in enumerate(true_quads):
            iou = quad_iou(pq, tq)
            if iou >= thr:
                pairs.append((iou, i, j))
    pairs.sort(reverse=True)
    used_p, used_t, out = set(), set(), []
    for iou, i, j in pairs:
        if i in used_p or j in used_t:
            continue
        used_p.add(i)
        used_t.add(j)
        out.append((i, j, iou))
    return out


class Acc:
    """累積各項指標。"""

    def __init__(self):
        self.n_true = self.n_pred = self.n_match = 0
        self.err = {"cjk": 0, "lat": 0}
        self.cnt = {"cjk": 0, "lat": 0}
        self.space_err = self.space_cnt = 0
        self.char_err_raw: list[float] = []
        self.char_err_off: list[float] = []
        self.corner_err: list[float] = []
        self.lr_err: list[float] = []
        self.times: list[float] = []
        self.cpu: list[float] = []

    def add_page(self, lines: list[OcrLine], truths: list[dict], seconds: float, cpu: float = 0.0):
        self.times.append(seconds)
        self.cpu.append(cpu)
        self.n_true += len(truths)
        self.n_pred += len(lines)
        m = match([l.quad for l in lines], [t["quad"] for t in truths])
        self.n_match += len(m)
        for i, j, _ in m:
            pl, tl = lines[i], truths[j]
            pt = " ".join(pl.text.split())
            tt = tl["text"]
            pns, tns = pt.replace(" ", ""), tt.replace(" ", "")
            for ch in tns:
                self.cnt["cjk" if _is_wide(ch) else "lat"] += 1
            d_ns, ops = lev_ops(pns, tns)
            for op, a, b in ops:
                if op in ("sub", "del"):
                    self.err["cjk" if _is_wide(tns[b]) else "lat"] += 1
                elif op == "ins":
                    self.err["cjk" if _is_wide(pns[a]) else "lat"] += 1
            d_sp, _ = lev_ops(pt, tt)
            self.space_err += max(d_sp - d_ns, 0)
            self.space_cnt += tt.count(" ")
            # 四角
            d = np.linalg.norm(pl.quad - tl["quad"], axis=1)
            self.corner_err.append(float(d.mean()))
            u = tl["quad"][1] - tl["quad"][0]
            u = u / (np.linalg.norm(u) + 1e-9)
            self.lr_err.append(0.5 * (abs(float((pl.quad[0] - tl["quad"][0]) @ u)) + abs(float((pl.quad[1] - tl["quad"][1]) @ u))))
            # 逐字起點
            pchars = [c for c in pl.chars if c.text != " "]
            if len(pchars) != len(pns):
                continue
            _, ops = lev_ops(pns, tl["chars"])
            pairs = [(pchars[a].x0, tl["xs"][b]) for op, a, b in ops if op == "eq" and b < len(tl["xs"]) and not math.isnan(tl["xs"][b])]
            if not pairs:
                continue
            diffs = np.array([px - tx for px, tx in pairs])
            self.char_err_raw.extend(np.abs(diffs).tolist())
            self.char_err_off.extend(np.abs(diffs - np.median(diffs)).tolist())

    def summary(self) -> dict:
        f = lambda v: round(float(np.mean(v)), 2) if len(v) else None  # noqa: E731
        return {
            "line_recall": round(self.n_match / max(self.n_true, 1), 3),
            "line_precision": round(self.n_match / max(self.n_pred, 1), 3),
            "cer_cjk": round(self.err["cjk"] / max(self.cnt["cjk"], 1), 4),
            "cer_latin": round(self.err["lat"] / max(self.cnt["lat"], 1), 4),
            "n_cjk": self.cnt["cjk"],
            "n_latin": self.cnt["lat"],
            "space_err_rate": round(self.space_err / max(self.space_cnt, 1), 3),
            "char_start_err_px": f(self.char_err_raw),
            "char_start_err_offset_removed_px": f(self.char_err_off),
            "corner_err_px": f(self.corner_err),
            "left_right_err_px": f(self.lr_err),
            "sec_per_page": round(float(np.mean(self.times)), 2) if self.times else None,
            "cpu_sec_per_page": round(float(np.mean(self.cpu)), 2) if self.cpu else None,
            "n_true": self.n_true,
            "n_pred": self.n_pred,
        }


def truth_lines(truth, W: int) -> list[dict]:
    out = []
    for tl in truth.texts:
        q = tl.quad
        quad = np.array([[q.tl.x, q.tl.y], [q.tr.x, q.tr.y], [q.br.x, q.br.y], [q.bl.x, q.bl.y]], float) * W
        chars, xs = [], []
        for r in tl.runs:
            nonsp = [c for c in r.text if not c.isspace()]
            for ch, x in zip(nonsp, r.char_x):
                chars.append(ch)
                xs.append(x * W)
        out.append({"text": " ".join(tl.text.split()), "quad": quad, "chars": "".join(chars), "xs": xs})
    return out


# ============================================================================ 干擾


def blur(img: np.ndarray, sigma: float = 1.2) -> np.ndarray:
    return cv2.GaussianBlur(img, (0, 0), sigma)


def jpeg(img: np.ndarray, quality: int = 30) -> np.ndarray:
    buf = io.BytesIO()
    Image.fromarray(img).save(buf, format="JPEG", quality=quality)
    return np.asarray(Image.open(buf).convert("RGB"))


def noise(img: np.ndarray, sigma: float = 12.0, seed: int = 0) -> np.ndarray:
    rng = np.random.default_rng(seed)
    return np.clip(img.astype(np.float32) + rng.normal(0, sigma, img.shape), 0, 255).astype(np.uint8)


def uneven_light(img: np.ndarray) -> np.ndarray:
    H, W = img.shape[:2]
    gy, gx = np.mgrid[0:H, 0:W]
    g = 0.55 + 0.45 * (gx / W) * (0.6 + 0.4 * gy / H)
    return np.clip(img.astype(np.float32) * g[..., None], 0, 255).astype(np.uint8)


# ============================================================================ 合成頁面


def _draw_line(canvas: Image.Image, text: str, font_path: str, size: int, xy, color, rotate: int = 0, index: int = 0):
    """在 canvas 上畫一行字，回傳答案：墨跡四角（文字方向）、每個非空白字的墨跡起點（相對行左緣）。"""
    font = ImageFont.truetype(font_path, size, index=index, layout_engine=ImageFont.Layout.BASIC)
    asc, desc = font.getmetrics()
    tw = int(math.ceil(font.getlength(text))) + size
    th = asc + desc + size // 2
    ox, oy = size // 2, size // 4
    mask = Image.new("L", (tw, th), 0)
    ImageDraw.Draw(mask).text((ox, oy), text, font=font, fill=255)
    m = np.asarray(mask) > 127
    ys, xs = np.nonzero(m)
    L, R, T, B = xs.min(), xs.max() + 1, ys.min(), ys.max() + 1
    # 每個字的墨跡起點：逐次多畫一個字，新增墨跡的最左像素（與整行同一套點陣化，不受 getbbox 取整影響）
    starts = []
    prev = np.zeros_like(m)
    for i, ch in enumerate(text):
        sub = Image.new("L", (tw, th), 0)
        ImageDraw.Draw(sub).text((ox, oy), text[: i + 1], font=font, fill=255)
        cur = np.asarray(sub) > 127
        if not ch.isspace():
            cols = np.nonzero((cur & ~prev).any(axis=0))[0]
            starts.append(float(cols.min() - L) if cols.size else float("nan"))
        prev = cur
    corners = np.array([[L, T], [R, T], [R, B], [L, B]], float)
    layer = Image.new("RGBA", (tw, th), (0, 0, 0, 0))
    layer.putalpha(mask)
    solid = Image.new("RGBA", (tw, th), tuple(color) + (255,))
    solid.putalpha(mask)
    if rotate == 270:  # 逆時針轉 90 度：由下往上讀
        solid = solid.transpose(Image.Transpose.ROTATE_90)
        corners = np.stack([corners[:, 1], tw - corners[:, 0]], axis=1)
    elif rotate == 90:  # 順時針轉 90 度：由上往下讀
        solid = solid.transpose(Image.Transpose.ROTATE_270)
        corners = np.stack([th - corners[:, 1], corners[:, 0]], axis=1)
    canvas.paste(solid, xy, solid)
    corners = corners + np.array(xy, float)
    return {"text": text, "quad": corners, "chars": text.replace(" ", ""), "xs": starts}


@lru_cache(maxsize=None)
def synth_page() -> tuple[np.ndarray, tuple]:
    """1600 × 1100 的合成頁面。回傳（影像、答案清單）。"""
    img = Image.new("RGB", (1600, 1100), (250, 250, 248))
    d = ImageDraw.Draw(img)
    truths = []
    truths.append(_draw_line(img, "The quick brown fox jumps over the lazy dog", SANS, 40, (80, 60), (20, 20, 20)))
    truths.append(_draw_line(img, "排版一致的可編輯檔案從照片開始", WQY, 40, (80, 140), (30, 30, 30)))
    truths.append(_draw_line(img, "Mortise 支援 PPTX 與 Word 輸出", WQY, 36, (80, 220), (40, 40, 120)))
    truths.append(_draw_line(img, "Revenue grew 18% in 2026", SANS_B, 52, (80, 290), (180, 40, 40)))
    truths.append(_draw_line(img, "Small print: version 2.3, page 14 of 20", SANS, 15, (80, 390), (60, 60, 60)))
    d.rectangle([60, 450, 900, 560], fill=(28, 36, 64))
    truths.append(_draw_line(img, "Light text on a dark panel 2027", SANS, 44, (90, 470), (235, 235, 235)))
    truths.append(_draw_line(img, "深色底淺色字測試", WQY, 40, (90, 600), (250, 250, 250)))
    d.rectangle([60, 590, 520, 660], fill=(40, 90, 60))
    # 上一行被色塊蓋掉了：重畫在色塊上
    truths[-1] = _draw_line(img, "深色底淺色字測試", WQY, 40, (90, 600), (250, 250, 250))
    truths.append(_draw_line(img, "Organised by the arts council", SANS, 34, (1450, 80), (30, 30, 30), rotate=270))
    truths.append(_draw_line(img, "旋轉文字由上往下讀", WQY, 34, (1320, 80), (30, 30, 30), rotate=90))
    # 圖片裡的圓形（不該被讀成字）
    d.rectangle([700, 700, 1200, 1050], fill=(40, 90, 140))
    d.ellipse([850, 760, 1050, 960], fill=(238, 200, 60))
    truths.append(_draw_line(img, "Footnote 3: data as of 2026-11-01", SANS, 22, (80, 1000), (80, 80, 80)))
    return np.asarray(img).copy(), tuple(truths)


def skew_page(img: np.ndarray, truths, angle: float):
    H, W = img.shape[:2]
    M = cv2.getRotationMatrix2D((W / 2 - 0.5, H / 2 - 0.5), angle, 1.0)
    out = cv2.warpAffine(img, M, (W, H), flags=cv2.INTER_LINEAR, borderValue=(250, 250, 248))
    new = []
    for t in truths:
        q = t["quad"] - 0.5
        q = q @ M[:, :2].T + M[:, 2] + 0.5
        new.append({**t, "quad": q})
    return out, new


def scale_page(img: np.ndarray, truths, s: float):
    H, W = img.shape[:2]
    out = cv2.resize(img, (int(round(W * s)), int(round(H * s))), interpolation=cv2.INTER_AREA)
    return out, [{**t, "quad": t["quad"] * s, "xs": [x * s for x in t["xs"]]} for t in truths]


# ============================================================================ 量測（快取）


@lru_cache(maxsize=None)
def engine() -> OcrEngine:
    return OcrEngine()


def _timed_read(img):
    """回傳（文字行、牆鐘秒數、本行程 CPU 秒數（各執行緒加總，不受其他程式搶 CPU 影響））。"""
    t, c = time.perf_counter(), time.process_time()
    lines = engine().read(img)
    return lines, time.perf_counter() - t, time.process_time() - c


@lru_cache(maxsize=None)
def fixture_results() -> dict:
    # 干擾組只跑三頁（不含最大的 A3 海報與只有兩行的附錄頁），以控制測試時間
    sub = [("slide_basic", 0), ("slide_dark", 0), ("doc_a4", 0)]
    conds = {"clean_150dpi": (150, None, PAGES), "clean_96dpi": (96, None, PAGES), "blur_s1.2_150dpi": (150, blur, sub), "jpeg_q30_150dpi": (150, jpeg, sub)}
    out = {}
    for name, (dpi, fn, pages) in conds.items():
        acc = Acc()
        per_page = {}
        for pdf, page in pages:
            img, truth, _ = render_truth(FIX / f"{pdf}.pdf", page, dpi)
            if fn is not None:
                img = fn(img)
            lines, sec, cpu = _timed_read(img)
            tl = truth_lines(truth, img.shape[1])
            acc.add_page(lines, tl, sec, cpu)
            per_page[f"{pdf}#{page}"] = [l.text for l in lines]
        out[name] = {"summary": acc.summary(), "pages": per_page}
    return out


@lru_cache(maxsize=None)
def synth_results() -> dict:
    base, truths = synth_page()
    truths = list(truths)
    conds = {
        "clean": (base, truths),
        "skew_2.5deg": skew_page(base, truths, 2.5),
        "noise_s12": (noise(base), truths),
        "lowres_x0.5": scale_page(base, truths, 0.5),
        "uneven_light": (uneven_light(base), truths),
    }
    out = {}
    for name, (img, tr) in conds.items():
        acc = Acc()
        lines, sec, cpu = _timed_read(img)
        acc.add_page(lines, tr, sec, cpu)
        out[name] = {"summary": acc.summary(), "lines": lines, "truths": tr}
    return out


# ============================================================================ 測試：基本行為


def test_default_params_complete():
    eng = engine()
    for k in DEFAULT_PARAMS:
        assert k in eng.params


def test_empty_and_tiny_images():
    eng = engine()
    assert eng.read(np.full((200, 300, 3), 255, np.uint8)) == []
    assert eng.read(np.full((8, 8, 3), 0, np.uint8)) == []
    gray = np.full((100, 100), 255, np.uint8)
    assert eng.read(gray) == []


def test_line_structure_and_candidates():
    lines = synth_results()["clean"]["lines"]
    assert lines
    for l in lines:
        assert l.quad.shape == (4, 2)
        assert len(l.chars) == len(l.text)
        assert all(c.text == t for c, t in zip(l.chars, l.text))
        assert l.vertical is False
        assert 0.0 <= l.score <= 1.0
        assert l.quad_error > 0 and l.angle_error_deg > 0
        prev = -1e9
        for c in l.chars:
            assert c.x1 >= c.x0 - 1e-6
            assert c.x0 >= prev - 1.0  # 字界單調
            prev = c.x0
            assert c.x_error > 0
            if c.text != " ":
                assert c.candidates and c.candidates[0].text == c.text
                probs = [cc.prob for cc in c.candidates]
                assert probs == sorted(probs, reverse=True)
                assert len(c.candidates) <= DEFAULT_PARAMS["cand_topk"]
    # 至少有些字有第二候選（候選清單不是只有第一名）
    assert sum(len(c.candidates) > 1 for l in lines for c in l.chars) > 10


def test_params_override():
    eng = OcrEngine({"cand_topk": 2, "em_ratio": {"cjk": 0.95}})
    assert eng.params["cand_topk"] == 2
    assert eng.params["em_ratio"]["cjk"] == 0.95 and eng.params["em_ratio"]["tall"] == DEFAULT_PARAMS["em_ratio"]["tall"]
    img, _ = synth_page()
    lines = eng.read(img[:200, :900])
    assert all(len(c.candidates) <= 2 for l in lines for c in l.chars)


# ============================================================================ 測試：fixture 精準度


def test_fixture_clean_150():
    s = fixture_results()["clean_150dpi"]["summary"]
    assert s["line_recall"] >= 0.95  # 實測 0.957（兩個漏的是短行 IoU 定義問題，見模組文件）
    assert s["line_precision"] >= 0.95  # 實測 0.957
    assert s["cer_cjk"] <= 0.014  # 實測 0.010
    assert s["cer_latin"] <= 0.011  # 實測 0.0073
    assert s["space_err_rate"] <= 0.07  # 實測 0.044
    assert s["char_start_err_offset_removed_px"] <= 1.3  # 實測 1.07
    assert s["left_right_err_px"] <= 5.0  # 實測 4.47（含首字左側留白，答案是筆位）


def test_fixture_clean_96():
    s = fixture_results()["clean_96dpi"]["summary"]
    assert s["line_recall"] >= 0.95  # 實測 0.957
    assert s["line_precision"] >= 0.95  # 實測 0.957
    assert s["cer_cjk"] <= 0.03  # 實測 0.023
    assert s["cer_latin"] <= 0.011  # 實測 0.0073
    assert s["char_start_err_offset_removed_px"] <= 0.9  # 實測 0.71


def test_fixture_blur_and_jpeg():
    for cond in ("blur_s1.2_150dpi", "jpeg_q30_150dpi"):
        s = fixture_results()[cond]["summary"]
        assert s["line_recall"] >= 0.9, cond  # 實測 0.917 兩組
        assert s["line_precision"] >= 0.9, cond  # 實測 0.943、0.917
        assert s["cer_cjk"] <= 0.025, cond  # 實測 0.019、0.016
        assert s["cer_latin"] <= 0.015, cond  # 實測 0.011 兩組
        assert s["char_start_err_offset_removed_px"] <= 1.5, cond  # 實測 1.26、1.05


def test_fixture_speed():
    """以 CPU 秒數（各執行緒加總）判斷，牆鐘時間受機器負載影響太大。"""
    for cond, r in fixture_results().items():
        assert r["summary"]["cpu_sec_per_page"] <= 8.0, cond


def test_slide_basic_circle_not_read():
    """slide_basic 圖片裡的圓形不能被讀成字（rapidocr 原樣會讀成 'O'，分數 0.80）。"""
    texts = fixture_results()["clean_150dpi"]["pages"]["slide_basic#0"]
    assert not any(t.strip() in ("O", "o", "0", "〇") for t in texts)


# ============================================================================ 測試：合成頁面（精確墨跡答案）


def test_synth_clean_geometry():
    s = synth_results()["clean"]["summary"]
    assert s["line_recall"] >= 0.99
    assert s["line_precision"] >= 0.99
    assert s["cer_cjk"] <= 0.02
    assert s["cer_latin"] <= 0.02
    assert s["corner_err_px"] <= 0.6  # 實測 0.41
    assert s["char_start_err_px"] <= 0.35  # 實測 0.19


def test_synth_rotated_90_270():
    r = synth_results()["clean"]
    for want in ("Organised by the arts council", "旋轉文字由上往下讀"):
        t = next(t for t in r["truths"] if t["text"] == want)
        l = max(r["lines"], key=lambda l: quad_iou(l.quad, t["quad"]))
        assert quad_iou(l.quad, t["quad"]) > 0.7
        assert l.text.replace(" ", "") == want.replace(" ", "")
        # 四角順序依文字方向：左上角要對到文字起點的上緣
        assert np.linalg.norm(l.quad[0] - t["quad"][0]) < 4.0
        assert any("旋轉" in n for n in l.notes)


def test_synth_circle_dropped():
    r = synth_results()["clean"]
    circle = np.array([[850, 760], [1051, 760], [1051, 961], [850, 961]], float)
    assert not any(quad_iou(l.quad, circle) > 0.3 for l in r["lines"])


def test_synth_skewed_page():
    s = synth_results()["skew_2.5deg"]["summary"]
    assert s["line_recall"] >= 0.99
    assert s["corner_err_px"] <= 0.8  # 實測 0.59
    assert s["char_start_err_px"] <= 0.6  # 實測 0.40
    assert s["cer_latin"] <= 0.01  # 實測 0


def test_synth_degraded():
    res = synth_results()
    for cond in ("noise_s12", "uneven_light", "lowres_x0.5"):
        s = res[cond]["summary"]
        assert s["line_recall"] >= 0.99, cond  # 實測 1.0
        assert s["corner_err_px"] <= 0.6, cond  # 實測 0.38、0.38、0.31
        assert s["char_start_err_px"] <= 0.75, cond  # 實測 0.31、0.19、0.56


def test_low_resolution_flagged_and_less_confident():
    res = synth_results()
    small_clean = next(l for l in res["clean"]["lines"] if "Small print" in l.text)
    small_low = [l for l in res["lowres_x0.5"]["lines"] if "print" in l.text or "version" in l.text]
    assert small_low, "低解析度的小字整行不見了"
    assert any("低解析度" in n for n in small_low[0].notes)
    assert small_low[0].score < small_clean.score


def test_large_image_tiling():
    """長邊超過切塊門檻的大圖：切塊偵測加整頁縮小偵測，跨切塊的行、小字與大標題都要找到。
    為了控制測試時間，把切塊門檻調到 1600 像素，用 2000 × 1400 的影像測同一套邏輯。"""
    big = np.full((1200, 1800, 3), 250, np.uint8)
    pil = Image.fromarray(big)
    t1 = _draw_line(pil, "Large Display Title", SANS_B, 140, (120, 60), (20, 20, 20))
    # 這一行橫跨兩個切塊的重疊區（橫向切塊起點 0 與 776）
    t2 = _draw_line(pil, "tiny footnote text across the tile border 42", SANS, 18, (700, 640), (40, 40, 40))
    t3 = _draw_line(pil, "中文小字也要讀得到", WQY, 20, (1400, 1120), (40, 40, 40))
    img = np.asarray(pil)
    c = time.process_time()
    lines = engine().read(img, params={"det_tile_threshold": 1500, "det_tile_size": 1024})
    sec = time.process_time() - c
    for tr in (t1, t2, t3):
        best = max(lines, key=lambda l: quad_iou(l.quad, tr["quad"]))
        assert quad_iou(best.quad, tr["quad"]) > 0.6, tr["text"]
        assert best.text.replace(" ", "") == tr["text"].replace(" ", ""), (best.text, tr["text"])
    assert sec < 20.0  # CPU 秒數


def test_zz_report():
    """設了環境變數 MORTISE_OCR_METRICS（檔案路徑）時，把所有數字寫成 JSON（供回報用）。"""
    import os

    path = os.environ.get("MORTISE_OCR_METRICS")
    if not path:
        return
    data = {"fixtures": {k: v["summary"] for k, v in fixture_results().items()}, "synthetic": {k: v["summary"] for k, v in synth_results().items()}}
    data["fixture_texts_clean_150"] = fixture_results()["clean_150dpi"]["pages"]
    Path(path).write_text(json.dumps(data, ensure_ascii=False, indent=1))


# ============================================================================ 直接執行：印出數字


if __name__ == "__main__":
    fr = fixture_results()
    print("== fixtures")
    for k, v in fr.items():
        print(k, json.dumps(v["summary"], ensure_ascii=False))
    for k, v in fr["clean_150dpi"]["pages"].items():
        print("  ", k, v)
    sr = synth_results()
    print("== synthetic")
    for k, v in sr.items():
        print(k, json.dumps(v["summary"], ensure_ascii=False))
        if "-v" in sys.argv:
            for l in v["lines"]:
                print("    ", repr(l.text), round(l.score, 3), l.notes)


def test_rule_line_above_text_keeps_spaces():
    """文字上方有一條橫線（海報常見的分隔線）時，橫線不可被當成 i 的點那樣併進這一行：
    併進來會填滿字間空隙，「12 – 14 JULY 2027」就會讀成「12–14JULY2027」（實際發生過的錯誤）。"""
    canvas = Image.new("RGB", (1500, 260), (245, 235, 215))
    draw = ImageDraw.Draw(canvas)
    draw.rectangle([40, 40, 1460, 50], fill=(40, 45, 70))  # 橫線：離字上緣約 30 像素
    font = ImageFont.truetype(SANS_B, 150)
    draw.text((40, 80), "12 – 14 JULY 2027", font=font, fill=(40, 45, 70))
    lines = engine().read(np.asarray(canvas))
    texts = [ln.text for ln in lines]
    assert "12 – 14 JULY 2027" in texts, texts
