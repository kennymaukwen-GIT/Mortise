"""synth.degrade 的可重現性與矩陣正確性；各指標用「標準答案＋已知擾動」驗證數值。"""

from pathlib import Path

import cv2
import numpy as np
import pytest

from mortise.artifacts import Color, FontCandidate, Paragraph, PageLayout, Provenance, Quad, TextBox, measured
from mortise.artifacts.common import Align, Role
from mortise.config import Config
from mortise.eval import metrics as M
from mortise.eval.synth import apply_h, degrade
from mortise.eval.truth import render_truth
from mortise.stages import s4_structure

FIX = Path(__file__).resolve().parents[1] / "fixtures"


# ---------------------------------------------------------------- synth


def _dots_image():
    img = np.full((900, 1600, 3), 255, np.uint8)
    pts = np.array([[50, 50], [1550, 50], [1550, 850], [50, 850], [800, 450], [400, 260], [1200, 640]], float)
    for x, y in pts:
        cv2.circle(img, (int(x), int(y)), 7, (0, 0, 0), -1, lineType=cv2.LINE_AA)
    return img, pts + 0.5  # cv2 畫在像素中心座標；換成像素角落約定要加 0.5


def _centroid_error(out, matrix, pts):
    g = out.astype(float).mean(axis=2)
    errs = []
    for px, py in apply_h(matrix, pts):
        r = 18
        x0, y0 = int(px) - r, int(py) - r
        win = g[y0:y0 + 2 * r, x0:x0 + 2 * r]
        th = (win.min() + win.max()) / 2
        ys, xs = np.nonzero(win < th)
        w = th - win[win < th]
        cx, cy = (xs * w).sum() / w.sum() + x0 + 0.5, (ys * w).sum() / w.sum() + y0 + 0.5
        errs.append(np.hypot(cx - px, cy - py))
    return max(errs)


@pytest.mark.parametrize("kind,tol", [("screenshot", 0.2), ("scan", 0.2), ("photo", 1.0)])
def test_degrade_reproducible_and_matrix_maps_known_points(kind, tol):
    img, pts = _dots_image()
    a, ma, ia = degrade(img, kind, seed=11)
    b, mb, ib = degrade(img, kind, seed=11)
    c, _, _ = degrade(img, kind, seed=12)
    assert np.array_equal(a, b) and np.array_equal(ma, mb) and ia == ib
    assert a.shape != c.shape or not np.array_equal(a, c)
    assert _centroid_error(a, ma, pts) < tol  # 已知點經矩陣對回去，與干擾後影像中實際的位置相差不到 tol 像素
    assert ia["kind"] == kind and ia["output_size"] == [a.shape[1], a.shape[0]]


def test_screenshot_dpi_range_and_strength_zero():
    img, _ = _dots_image()
    for seed in range(8):
        _, m, info = degrade(img, "screenshot", seed)
        assert 96 <= info["dpi"] <= 144 and abs(m[0, 0] - info["dpi"] / 150) < 0.01
    out, m, info = degrade(img, "scan", 3, strength=0.0)
    assert info["angle_deg"] == 0.0 and info["noise_sigma"] == 0.0


# ---------------------------------------------------------------- metrics：幾何與顏色


def test_iou_and_delta_e_reference_values():
    a = Quad.from_rect(0, 0, 1, 1)
    assert M.quad_iou(a, Quad.from_rect(0.5, 0, 1.5, 1)) == pytest.approx(1 / 3)
    rot = np.array([[0.5, -0.2071], [1.2071, 0.5], [0.5, 1.2071], [-0.2071, 0.5]])  # 同中心轉 45 度、邊長 1
    assert M.quad_iou(rot, rot) == pytest.approx(1.0)
    line = Quad.from_rect(0, 0.5, 1, 0.5)  # 面積為 0 的直線，撐開後可以配對
    assert M.quad_iou(line, line, min_thick=0.01) == pytest.approx(1.0)
    assert M.quad_iou(line, Quad.from_rect(0, 0.6, 1, 0.6), min_thick=0.01) == 0.0
    assert M.delta_e76((0, 0, 0), (255, 255, 255)) == pytest.approx(100.0, abs=0.01)
    assert M.delta_e76((255, 0, 0), (0, 0, 0)) == pytest.approx(np.linalg.norm([53.24, 80.09, 67.20]), abs=0.05)
    assert M.normalize_family("ABCDEF+LiberationSans-BoldItalic") == M.normalize_family("Liberation Sans") == "liberationsans"


# ---------------------------------------------------------------- metrics：標準答案＋已知擾動


@pytest.fixture(scope="module")
def truth():
    image, el, spec = render_truth(FIX / "slide_basic.pdf", 0, 100)
    layout = s4_structure.run_page(el, spec, Config.load(), [])
    return el, spec, layout


def _runs(el, fn):
    return el.model_copy(update={"texts": [t.model_copy(update={"runs": [fn(t, k, r) for k, r in enumerate(t.runs)]}) for t in el.texts]})


def test_identity_is_perfect(truth):
    el, spec, layout = truth
    pw = spec.page_width_pt.value
    m = M.evaluate_page(el, el, pw, layout, layout).finalize()
    for k in ("text_recall", "text_precision", "orientation_accuracy", "font_top1", "font_top3", "bold_accuracy", "italic_accuracy", "image_recall", "shape_recall",
              "shape_kind_accuracy", "paragraph_pair_f1", "reading_order"):
        assert m[k] == pytest.approx(1.0), k
    for k in ("cer", "cer_zh", "cer_en", "page_cer", "position_error_pt", "size_error_pt", "color_delta_e"):
        assert m[k] == pytest.approx(0.0, abs=1e-9), k
    assert m["cer_zh"] == 0 and m["box_count_error"] == 0


def test_shift_scale_and_attribute_perturbations(truth):
    el, spec, layout = truth
    pw = spec.page_width_pt.value
    # 位置：整頁平移 (0.6 pt, 0.8 pt) → 四角平均誤差 1.0 pt；水平基線的偏移 0.8 pt
    h = np.array([[1, 0, 0.6 / pw], [0, 1, 0.8 / pw], [0, 0, 1.0]])
    m = M.evaluate_page(M.transform_elements(el, h), el, pw).finalize()
    assert m["position_error_pt"] == pytest.approx(1.0, abs=1e-6)
    assert m["baseline_error_pt"] == pytest.approx(0.8, abs=1e-6)
    # 方向：把第一行的四角順序轉一格（等於文字方向轉了 90°）→ 位置誤差不變、方向正確率少一行
    t0 = el.texts[0]
    pts = t0.quad.points
    rot = t0.model_copy(update={"quad": Quad(tl=pts[3], tr=pts[0], br=pts[1], bl=pts[2])})
    m = M.evaluate_page(el.model_copy(update={"texts": [rot] + list(el.texts[1:])}), el, pw).finalize()
    assert m["position_error_pt"] == pytest.approx(0.0, abs=1e-9)
    assert m["orientation_accuracy"] == pytest.approx((len(el.texts) - 1) / len(el.texts))
    # 字級放大 10%
    big = _runs(el, lambda t, k, r: r.model_copy(update={"font_size": r.font_size.model_copy(update={"value": r.font_size.value * 1.1})}))
    m = M.evaluate_page(big, el, pw).finalize()
    assert m["size_error_pct"] == pytest.approx(10.0, abs=1e-6)
    # 每行第一段改成粗體反相、顏色改成紅色、字型候選第一名換成錯的（正確的落到第二名）
    n_runs = sum(len(t.runs) for t in el.texts)

    def mess(t, k, r):
        if k:
            return r
        fonts = [FontCandidate(name="Comic Sans MS")] + list(r.font_candidates)
        return r.model_copy(update={"bold": r.bold.model_copy(update={"value": not r.bold.value}),
                                    "color": r.color.model_copy(update={"value": Color(r=255, g=0, b=0)}), "font_candidates": fonts})

    bad = _runs(el, mess)
    m = M.evaluate_page(bad, el, pw).finalize()
    n_lines = len(el.texts)
    assert m["bold_accuracy"] == pytest.approx((n_runs - n_lines) / n_runs)
    assert m["font_top1"] == pytest.approx((n_runs - n_lines) / n_runs)
    assert m["font_top3"] == pytest.approx(1.0)
    expect_de = sum(M.delta_e76((255, 0, 0), (t.runs[0].color.value.r, t.runs[0].color.value.g, t.runs[0].color.value.b)) for t in el.texts) / n_runs
    assert m["color_delta_e"] == pytest.approx(expect_de, rel=1e-6)


def test_character_errors_and_missing_lines(truth):
    el, spec, _ = truth
    pw = spec.page_width_pt.value
    texts = list(el.texts)
    zh_total = sum(M.char_class(c) == "zh" for t in texts for c in t.text if not c.isspace())
    en_total = sum(M.char_class(c) == "en" for t in texts for c in t.text if not c.isspace())
    # 第 0 行：換掉一個中文字與一個英文字母
    t0 = texts[0]
    r0 = t0.runs
    new_runs = []
    done = {"zh": False, "en": False}
    for r in r0:
        s = list(r.text)
        for i, ch in enumerate(s):
            cls = M.char_class(ch)
            if not ch.isspace() and not done[cls]:
                s[i] = "錯" if cls == "zh" else ("x" if ch != "x" else "y")
                done[cls] = True
        new_runs.append(r.model_copy(update={"text": "".join(s)}))
    assert all(done.values())
    texts[0] = t0.model_copy(update={"runs": new_runs})
    pred = el.model_copy(update={"texts": texts})
    m = M.evaluate_page(pred, el, pw).finalize()
    assert m["cer_zh"] == pytest.approx(1 / zh_total) and m["cer_en"] == pytest.approx(1 / en_total)
    assert m["cer"] == pytest.approx(2 / (zh_total + en_total))
    # 漏抓兩行、多抓一行（放在頁面外，不會配到任何標準答案）
    dropped = texts[2:]
    extra = texts[3].model_copy(update={"id": "fake", "quad": Quad.from_rect(0.0, 5.0, 0.2, 5.05)})
    pred = el.model_copy(update={"texts": dropped + [extra]})
    m = M.evaluate_page(pred, el, pw).finalize()
    n = len(el.texts)
    assert m["text_recall"] == pytest.approx((n - 2) / n) and m["text_precision"] == pytest.approx((n - 2) / (n - 1))
    chars = lambda t: sum(1 for c in t.text if not c.isspace())  # noqa: E731
    lost = chars(texts[0]) + chars(texts[1]) + chars(extra)
    assert m["page_cer"] == pytest.approx(lost / (zh_total + en_total))  # 漏抓的行整行算刪除、多抓的行整行算插入


def _para(pid, lines):
    q = Quad.from_rect(0, 0, 1, 0.1)
    return Paragraph(id=pid, line_ids=lines, quad=q, alignment=measured(Align.LEFT, 1.0, Provenance.INFERRED, "t"),
                     role=measured(Role.BODY, 1.0, Provenance.INFERRED, "t"))


def test_layout_pairwise_f1_boxes_and_reading_order():
    pairs = [("a", "A"), ("b", "B"), ("c", "C"), ("d", "D")]
    truth_groups = {"a": "p1", "b": "p1", "c": "p2", "d": "p2"}
    merged = PageLayout(page_index=0, paragraphs=[_para("P", ["A", "B", "C", "D"])],
                        text_boxes=[TextBox(id="X", quad=Quad.from_rect(0, 0, 1, 1), z=0, paragraph_ids=["P"])], reading_order=["X"])
    # 標準答案兩段、預測合成一段：同段的配對 TP=2（ab、cd），FP=4 → F1 = 4 / (4 + 4) = 0.5
    m = M.compare_layout(pairs, truth_groups, merged, ["a", "b", "c", "d"], ["A", "B", "C", "D"], truth_box_count=2, truth_paragraph_count=2).finalize()
    assert m["paragraph_pair_f1"] == pytest.approx(0.5)
    assert m["boxes_per_paragraph"] == pytest.approx(0.5) and m["box_count_error"] == pytest.approx(0.5)
    assert m["reading_order"] == pytest.approx(1.0)
    # 四行各自一個框（過碎）、閱讀順序完全顛倒
    frag = PageLayout(page_index=0, paragraphs=[_para(f"P{x}", [x]) for x in "ABCD"],
                      text_boxes=[TextBox(id=f"X{x}", quad=Quad.from_rect(0, 0, 1, 1), z=0, paragraph_ids=[f"P{x}"]) for x in "ABCD"],
                      reading_order=[f"X{x}" for x in "DCBA"])
    m = M.compare_layout(pairs, truth_groups, frag, ["a", "b", "c", "d"], M.line_order(frag, None) if False else ["D", "C", "B", "A"],
                         truth_box_count=2, truth_paragraph_count=2).finalize()
    assert m["paragraph_pair_f1"] == pytest.approx(0.0) and m["boxes_per_paragraph"] == pytest.approx(2.0)
    assert m["reading_order"] == pytest.approx(0.0) and m["box_count_error"] == pytest.approx(1.0)


def test_coordinates_round_trip_through_degrade_matrix(truth):
    """runner 的座標對回方式：標準答案 → 干擾影像 → 預測頁面相對座標 → 對回。誤差應近乎 0。"""
    el, spec, layout = truth
    pw = spec.page_width_pt.value
    w0 = float(spec.working_image_size[0])
    image = np.full((spec.working_image_size[1], spec.working_image_size[0], 3), 255, np.uint8)
    _, mat, info = degrade(image, "photo", seed=5)
    s_pred = np.diag([1.0 / info["output_size"][0], 1.0 / info["output_size"][0], 1.0])  # 截圖類輸入：像素 ÷ 影像寬
    to_pred = s_pred @ mat @ np.diag([w0, w0, 1.0])
    pred_frame = M.transform_elements(el, to_pred)  # 假裝影像產線完美辨識（在它自己的座標系）
    back = M.transform_elements(pred_frame, np.linalg.inv(to_pred))
    m = M.evaluate_page(back, el, pw, layout, layout).finalize()
    assert m["text_recall"] == 1.0 and m["position_error_pt"] < 1e-6
    assert m["size_error_pct"] < 0.05  # 字級用行中心的局部縮放倍數換算；透視下來回有萬分之一級的近似誤差
    # 沒對回就直接比，應該幾乎配不到
    raw = M.evaluate_page(pred_frame, el, pw).finalize()
    assert raw["text_recall"] < 0.5


def test_composite_and_confidence_distribution():
    good = {"text_f1": 1.0, "page_cer": 0.0, "e2e_score": 1.0, "rasterized_page": 0.0}
    bad = {"text_f1": 0.0, "page_cer": 1.0, "e2e_score": 1.0, "rasterized_page": 1.0}
    assert M.composite(good) == pytest.approx(1.0)
    assert M.composite(bad) == pytest.approx(1.0 / 5.0)  # 只有 e2e（權重 1）是滿分；權重總和 2+1+1+1
    assert M.goodness("position_error_pt", 2.5) == pytest.approx(0.5)
    d = M.distribution([0.1, 0.5, 0.95, 1.0])
    assert d["n"] == 4 and sum(d["hist"]) == 4 and d["min"] == 0.1
