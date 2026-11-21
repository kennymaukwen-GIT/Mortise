"""評測指標：預測的產物與標準答案比一比，交出數字。

輸入一律是「頁面相對座標」（頁寬為 1，原點左上，y 向下）的產物；需要 pt 時乘上標準答案的頁寬 pt。
預測與標準答案若不在同一個座標系（例如合成照片），先用 transform_elements() 把預測對回標準答案的座標系。

做法與演算法：
1. 文字行配對：兩兩算「四角多邊形」的 IoU（Sutherland–Hodgman 凸多邊形裁切求交集面積），
   用匈牙利法（scipy.optimize.linear_sum_assignment）找 IoU 總和最大的一對一配對，
   IoU 低於門檻的配對不算。很細的框（直線、單行小字）先撐到最小厚度再算 IoU。
2. 字元錯誤率：Levenshtein 編輯距離＋回溯對齊；錯誤依「標準答案那個字」的文字類別歸到中文或英文
   （插入的字依插入的字歸類）。預設先做 NFKC 正規化並忽略空白。
   - 「已配對行」的錯誤率只看配對成功的行；「整頁」錯誤率把漏抓的行（整行刪除）與多抓的行（整行插入）也算進去。
3. 屬性（字級、字型、顏色、粗斜體）以「標準答案的樣式段」為單位比較：用第 2 步的逐字對齊，
   找出這個樣式段的字大多對到預測的哪一段，再比兩段的屬性。字型名稱先正規化
   （去子集前綴、去標點空白、去結尾的 Bold／Italic／MT 等樣式字、套別名表）。顏色差用 CIE76 ΔE（sRGB → Lab，D65）。
   位置誤差取四角四種循環對應中最小的平均距離（與方向無關）；方向另外以「上邊角度差 < 45°」算正確率，
   這樣旋轉 90° 的文字被讀成水平框時，會反映在方向正確率，而不是一個巨大的位置誤差。
4. 圖片與形狀：同樣以 IoU＋匈牙利法配對；形狀另外比幾何種類。預設忽略整頁底圖與整頁底色矩形。
5. 結構：段落分組的成對 F1（任兩條「已配對」的行：標準答案是否同段 vs 預測是否同段）；
   文字框數 ÷ 段落數（分母優先用標準答案的段落數，才看得出過碎）；閱讀順序的成對一致率（Kendall 式）。
6. 端到端：直接取還原度報告 FidelityReport 的整頁相似度、文字一致率、斷行一致率、位置誤差、總分。
7. 綜合分數：把可用的指標各自換成 0 到 1 的「好壞值」後加權平均（權重與尺度在 DEFAULT_PARAMS）；
   缺的指標不計入分母。這個分數用來排「最差的樣本」與當自動調參的目標。

彙總方式：同一個樣本（可能多頁）內用累加器做 micro 平均（例如召回率 = 全部配對數 ÷ 全部標準答案行數），
批次總表再對樣本做 macro 平均（見 runner.py）。

已知限制：
- 一行被預測拆成兩段（或兩行被合成一行）時，IoU 配對最多只認一段，另一段算多抓或漏抓；尚未做多對一配對。
- 「英文」錯誤率涵蓋所有非中日韓、非空白的字元（拉丁字母、數字、標點）。
- 端到端指標的總分沿用⑦的定義：整頁當成一張圖輸出時相似度近乎 1，所以另有「整頁點陣化比例」指標。

可單獨升級的點：多對一行配對；以字元框（而非行框）計算位置誤差；字型名稱別名表可擴充；
顏色差可換成 CIEDE2000。
"""

from __future__ import annotations

import math
import re
import unicodedata
from dataclasses import dataclass, field
from typing import Any, Iterable, Optional

import numpy as np
from scipy.optimize import linear_sum_assignment

from ..artifacts import ElementList, FidelityReport, ImageKind, Measured, PageLayout, Point, Quad, TextLine
from ..tools.fonts import is_cjk

DEFAULT_PARAMS: dict[str, Any] = {
    "text_iou_threshold": 0.5,  # 文字行配對的最低 IoU（0 到 1）
    "region_iou_threshold": 0.5,  # 圖片與形狀配對的最低 IoU（0 到 1）
    "min_box_thickness_pt": 2.0,  # 算 IoU 前，寬或高小於此值的框撐到此厚度（pt；直線形狀需要）
    "orientation_tolerance_deg": 45.0,  # 文字方向（四角的上邊角度）與標準答案相差小於此值才算方向正確（度）
    "cer_ignore_spaces": True,  # 字元錯誤率是否忽略空白（OCR 的空白很不穩定，且不影響可編輯性）
    "cer_nfkc": True,  # 比較文字前是否做 NFKC 正規化（全形半形標點視為相同）
    "ignore_full_page_images": True,  # 圖片指標是否忽略「整頁底圖」
    "ignore_page_background_shapes": True,  # 形狀指標是否忽略整頁底色矩形
    "font_aliases": {  # 字型別名（正規化後的名稱 → 標準名稱）
        "arialmt": "arial",
        "helveticaneue": "helvetica",
        "timesnewromanps": "timesnewroman",
        "文泉驛正黑": "wenquanyizenhei",
        "文泉驿正黑": "wenquanyizenhei",
    },
    "rasterized_area_ratio": 0.9,  # 單張圖佔頁面面積超過此比例且沒有文字 → 這頁算「整頁點陣化」
    # 綜合分數：權重（0 表示不計入）
    "composite_weights": {
        "text_f1": 2.0, "page_cer": 1.0, "font_top1": 0.5, "size_error_pct": 0.5, "position_error_pt": 0.5,
        "color_delta_e": 0.25, "bold_accuracy": 0.25, "image_recall": 0.5, "shape_recall": 0.25,
        "paragraph_pair_f1": 1.0, "reading_order": 0.5, "e2e_score": 1.0, "rasterized_page": 1.0,
    },
    # 綜合分數：非比例指標換成 0 到 1 的尺度（誤差達到此值時好壞值為 0）
    "composite_scales": {"position_error_pt": 5.0, "baseline_error_pt": 5.0, "size_error_pt": 4.0, "size_error_pct": 20.0,
                         "color_delta_e": 25.0, "e2e_position_error_pt": 5.0, "boxes_per_paragraph": 3.0, "box_count_error": 2.0},
}


def _p(params: dict | None) -> dict[str, Any]:
    p = dict(DEFAULT_PARAMS)
    if params:
        p.update(params)
    return p


# ====================================================================== 指標說明（報表用）


@dataclass(frozen=True)
class MetricInfo:
    label: str  # 白話中文名稱
    unit: str  # 單位："比例"（0 到 1）、"pt"、"%"、"ΔE"、"個"
    higher_is_better: bool
    group: str  # 文字、圖形、結構、端到端、綜合
    tolerance: float  # 與基準比較時容許的退步幅度（與值同單位）；超過就標成退步


METRICS: dict[str, MetricInfo] = {
    "composite": MetricInfo("綜合分數", "比例", True, "綜合", 0.01),
    "text_recall": MetricInfo("文字行召回率", "比例", True, "文字", 0.01),
    "text_precision": MetricInfo("文字行精確率", "比例", True, "文字", 0.01),
    "text_f1": MetricInfo("文字行 F1", "比例", True, "文字", 0.01),
    "cer": MetricInfo("字元錯誤率（已配對行，總體）", "比例", False, "文字", 0.005),
    "cer_zh": MetricInfo("字元錯誤率（中文）", "比例", False, "文字", 0.005),
    "cer_en": MetricInfo("字元錯誤率（英文與數字標點）", "比例", False, "文字", 0.005),
    "page_cer": MetricInfo("整頁字元錯誤率（含漏抓多抓）", "比例", False, "文字", 0.01),
    "position_error_pt": MetricInfo("文字行位置誤差（四角平均，與方向無關）", "pt", False, "文字", 0.25),
    "orientation_accuracy": MetricInfo("文字方向正確率", "比例", True, "文字", 0.01),
    "baseline_error_pt": MetricInfo("基線偏移", "pt", False, "文字", 0.25),
    "size_error_pt": MetricInfo("字級誤差", "pt", False, "文字", 0.1),
    "size_error_pct": MetricInfo("字級相對誤差", "%", False, "文字", 0.5),
    "font_top1": MetricInfo("字型命中率（第一名）", "比例", True, "文字", 0.02),
    "font_top3": MetricInfo("字型命中率（前三名）", "比例", True, "文字", 0.02),
    "color_delta_e": MetricInfo("文字顏色差（CIE76 ΔE）", "ΔE", False, "文字", 1.0),
    "bold_accuracy": MetricInfo("粗體正確率", "比例", True, "文字", 0.02),
    "italic_accuracy": MetricInfo("斜體正確率", "比例", True, "文字", 0.02),
    "image_recall": MetricInfo("圖片召回率", "比例", True, "圖形", 0.02),
    "image_precision": MetricInfo("圖片精確率", "比例", True, "圖形", 0.02),
    "shape_recall": MetricInfo("形狀召回率", "比例", True, "圖形", 0.02),
    "shape_precision": MetricInfo("形狀精確率", "比例", True, "圖形", 0.02),
    "shape_kind_accuracy": MetricInfo("形狀種類正確率", "比例", True, "圖形", 0.02),
    "paragraph_pair_f1": MetricInfo("段落分組成對 F1", "比例", True, "結構", 0.01),
    "boxes_per_paragraph": MetricInfo("文字框數 ÷ 段落數（越低越不碎）", "比例", False, "結構", 0.02),
    "box_count_error": MetricInfo("文字框數相對誤差（與標準答案比）", "比例", False, "結構", 0.02),
    "reading_order": MetricInfo("閱讀順序一致率", "比例", True, "結構", 0.01),
    "e2e_score": MetricInfo("還原度總分（⑦）", "比例", True, "端到端", 0.01),
    "e2e_similarity": MetricInfo("整頁相似度（⑦）", "比例", True, "端到端", 0.005),
    "e2e_text_match": MetricInfo("文字一致率（⑦）", "比例", True, "端到端", 0.01),
    "e2e_linebreak_match": MetricInfo("斷行一致率（⑦）", "比例", True, "端到端", 0.01),
    "e2e_position_error_pt": MetricInfo("輸出位置誤差（⑦）", "pt", False, "端到端", 0.1),
    "rasterized_page": MetricInfo("整頁點陣化比例（文字不可編輯）", "比例", False, "端到端", 0.01),
    "user_rating": MetricInfo("使用者整頁評分（換算成 0 到 1）", "比例", True, "回饋", 0.05),
}


def metric_meta() -> dict[str, dict[str, Any]]:
    """給報表與 JSON 用的指標說明。"""
    return {k: {"label": m.label, "unit": m.unit, "higher_is_better": m.higher_is_better, "group": m.group, "tolerance": m.tolerance} for k, m in METRICS.items()}


# ====================================================================== 幾何


def _poly(q: Quad) -> np.ndarray:
    return np.array([[p.x, p.y] for p in q.points], dtype=np.float64)


def _area(poly: np.ndarray) -> float:
    if len(poly) < 3:
        return 0.0
    x, y = poly[:, 0], poly[:, 1]
    return 0.5 * float(np.dot(x, np.roll(y, -1)) - np.dot(y, np.roll(x, -1)))


def _orient(poly: np.ndarray) -> np.ndarray:
    return poly[::-1].copy() if _area(poly) < 0 else poly


def _clip(subject: np.ndarray, clipper: np.ndarray) -> np.ndarray:
    """Sutherland–Hodgman：凸多邊形 subject 被凸多邊形 clipper 裁切（兩者同向）。"""
    out = subject
    n = len(clipper)
    for i in range(n):
        if len(out) == 0:
            break
        a, b = clipper[i], clipper[(i + 1) % n]
        edge = b - a

        def inside(p):
            return edge[0] * (p[1] - a[1]) - edge[1] * (p[0] - a[0]) >= -1e-15

        def cross_pt(p, q):
            d = q - p
            den = edge[0] * d[1] - edge[1] * d[0]
            if abs(den) < 1e-18:
                return p
            t = (edge[1] * (p[0] - a[0]) - edge[0] * (p[1] - a[1])) / den
            return p + t * d

        res = []
        for j in range(len(out)):
            cur, nxt = out[j], out[(j + 1) % len(out)]
            ci, ni = inside(cur), inside(nxt)
            if ci:
                res.append(cur)
                if not ni:
                    res.append(cross_pt(cur, nxt))
            elif ni:
                res.append(cross_pt(cur, nxt))
        out = np.array(res) if res else np.zeros((0, 2))
    return out


def thicken(poly: np.ndarray, min_thick: float) -> np.ndarray:
    """寬或高小於 min_thick 的四角框，沿自身方向撐開到 min_thick（中心不動）。"""
    tl, tr, br, bl = poly
    u = (tr - tl + br - bl) / 2.0
    v = (bl - tl + br - tr) / 2.0
    wl, hl = float(np.hypot(*u)), float(np.hypot(*v))
    if wl < 1e-12 and hl < 1e-12:
        u, v, wl, hl = np.array([1.0, 0.0]), np.array([0.0, 1.0]), 0.0, 0.0
    elif wl < 1e-12:
        u = np.array([v[1], -v[0]]) / hl * 1e-12
    elif hl < 1e-12:
        v = np.array([-u[1], u[0]]) / wl * 1e-12
    uh = u / max(np.hypot(*u), 1e-18)
    vh = v / max(np.hypot(*v), 1e-18)
    c = poly.mean(axis=0)
    w2, h2 = max(wl, min_thick) / 2.0, max(hl, min_thick) / 2.0
    if wl >= min_thick and hl >= min_thick:
        return poly
    return np.array([c - uh * w2 - vh * h2, c + uh * w2 - vh * h2, c + uh * w2 + vh * h2, c - uh * w2 + vh * h2])


def quad_iou(a: Quad | np.ndarray, b: Quad | np.ndarray, min_thick: float = 0.0) -> float:
    pa = _poly(a) if isinstance(a, Quad) else np.asarray(a, float)
    pb = _poly(b) if isinstance(b, Quad) else np.asarray(b, float)
    if min_thick > 0:
        pa, pb = thicken(pa, min_thick), thicken(pb, min_thick)
    pa, pb = _orient(pa), _orient(pb)
    aa, ab = abs(_area(pa)), abs(_area(pb))
    if aa <= 0 or ab <= 0:
        return 0.0
    # 外接框不相交就直接是 0（省時間）
    if pa[:, 0].max() < pb[:, 0].min() or pb[:, 0].max() < pa[:, 0].min() or pa[:, 1].max() < pb[:, 1].min() or pb[:, 1].max() < pa[:, 1].min():
        return 0.0
    inter = abs(_area(_clip(pa, pb)))
    return inter / (aa + ab - inter) if inter > 0 else 0.0


def match(truth_quads: list, pred_quads: list, threshold: float, min_thick: float = 0.0) -> list[tuple[int, int, float]]:
    """一對一配對（匈牙利法，最大化 IoU）。回傳 (標準答案索引, 預測索引, IoU)。"""
    if not truth_quads or not pred_quads:
        return []
    iou = np.zeros((len(truth_quads), len(pred_quads)))
    for i, t in enumerate(truth_quads):
        for j, p in enumerate(pred_quads):
            iou[i, j] = quad_iou(t, p, min_thick)
    rows, cols = linear_sum_assignment(-iou)
    return [(int(r), int(c), float(iou[r, c])) for r, c in zip(rows, cols) if iou[r, c] >= threshold]


# ====================================================================== 座標轉換


def _h_apply(h: np.ndarray, x: float, y: float) -> tuple[float, float]:
    v = h @ np.array([x, y, 1.0])
    return float(v[0] / v[2]), float(v[1] / v[2])


def local_scale(h: np.ndarray, x: float, y: float) -> float:
    """單應矩陣在 (x, y) 附近的局部等向縮放倍數（雅可比行列式開根號）。"""
    eps = 1e-4
    p0 = np.array(_h_apply(h, x, y))
    px = np.array(_h_apply(h, x + eps, y))
    py = np.array(_h_apply(h, x, y + eps))
    u, v = (px - p0) / eps, (py - p0) / eps
    return math.sqrt(abs(u[0] * v[1] - u[1] * v[0]))


def _tq(q: Quad, h: np.ndarray) -> Quad:
    pts = [_h_apply(h, p.x, p.y) for p in q.points]
    return Quad(tl=Point(x=pts[0][0], y=pts[0][1]), tr=Point(x=pts[1][0], y=pts[1][1]), br=Point(x=pts[2][0], y=pts[2][1]), bl=Point(x=pts[3][0], y=pts[3][1]))


def _scale_measured(m: Measured, s: float) -> Measured:
    return m.model_copy(update={"value": m.value * s, "error": None if m.error is None else m.error * s})


def transform_elements(el: ElementList, h: np.ndarray) -> ElementList:
    """把元素清單整份用 3×3 矩陣搬到另一個座標系（相對單位）。字級、字距、線寬依局部縮放倍數調整。"""
    h = np.asarray(h, dtype=np.float64)
    texts = []
    for t in el.texts:
        c = t.quad.center
        s = local_scale(h, c.x, c.y)
        runs = [r.model_copy(update={"quad": _tq(r.quad, h), "font_size": _scale_measured(r.font_size, s),
                                     "char_spacing": None if r.char_spacing is None else r.char_spacing * s,
                                     "char_x": [x * s for x in r.char_x]}) for r in t.runs]
        b0, b1 = (_h_apply(h, p.x, p.y) for p in t.baseline)
        texts.append(t.model_copy(update={"quad": _tq(t.quad, h), "runs": runs, "baseline": (Point(x=b0[0], y=b0[1]), Point(x=b1[0], y=b1[1]))}))
    images = [i.model_copy(update={"quad": _tq(i.quad, h)}) for i in el.images]
    shapes = []
    for sh in el.shapes:
        c = sh.quad.center
        s = local_scale(h, c.x, c.y)
        shapes.append(sh.model_copy(update={"quad": _tq(sh.quad, h), "stroke_width": None if sh.stroke_width is None else sh.stroke_width * s}))
    tables = [t.model_copy(update={"quad": _tq(t.quad, h), "h_lines": [], "v_lines": []}) for t in el.table_regions]
    return el.model_copy(update={"texts": texts, "images": images, "shapes": shapes, "table_regions": tables})


# ====================================================================== 文字工具


def char_class(ch: str) -> str:
    return "zh" if is_cjk(ch) else "en"


def _norm_chars(line: TextLine, p: dict) -> list[tuple[str, int, str]]:
    """把一行拆成（字、樣式段索引、文字類別）；依參數做 NFKC 與去空白。

    文字類別依「正規化之前」的字判斷：全形逗號 NFKC 後變成半形，但仍算中文的錯誤。"""
    out = []
    for ri, r in enumerate(line.runs):
        for ch in r.text:
            cls = char_class(ch)
            s = unicodedata.normalize("NFKC", ch) if p["cer_nfkc"] else ch
            for c in s:
                if p["cer_ignore_spaces"] and c.isspace():
                    continue
                out.append((c, ri, cls))
    return out


def align(a: str | list, b: str | list) -> list[tuple[Optional[int], Optional[int]]]:
    """Levenshtein 對齊：回傳 (a 的索引或 None, b 的索引或 None) 的序列。相同或替換時兩者皆有值。"""
    n, m = len(a), len(b)
    d = np.zeros((n + 1, m + 1), dtype=np.int32)
    d[:, 0] = np.arange(n + 1)
    d[0, :] = np.arange(m + 1)
    for i in range(1, n + 1):
        ai = a[i - 1]
        row, prev = d[i], d[i - 1]
        for j in range(1, m + 1):
            cost = 0 if ai == b[j - 1] else 1
            row[j] = min(prev[j] + 1, row[j - 1] + 1, prev[j - 1] + cost)
    i, j, path = n, m, []
    while i > 0 or j > 0:
        if i > 0 and j > 0 and d[i, j] == d[i - 1, j - 1] + (0 if a[i - 1] == b[j - 1] else 1):
            path.append((i - 1, j - 1))
            i, j = i - 1, j - 1
        elif i > 0 and d[i, j] == d[i - 1, j] + 1:
            path.append((i - 1, None))
            i -= 1
        else:
            path.append((None, j - 1))
            j -= 1
    return path[::-1]


_STYLE_SUFFIX = re.compile(r"(bolditalic|boldoblique|italic|oblique|bold|regular|medium|semibold|demibold|light|black|heavy|book|roman|psmt|mt|ps)$")


def normalize_family(name: str, aliases: dict[str, str] | None = None) -> str:
    """字族名稱正規化：去子集前綴（ABCDEF+）、轉小寫、去空白與標點、去結尾樣式字、套別名表。"""
    n = unicodedata.normalize("NFKC", name or "")
    n = re.sub(r"^[A-Z]{6}\+", "", n).lower()
    n = re.sub(r"[\s\-_,.]+", "", n)
    prev = None
    while prev != n and len(n) > 3:
        prev = n
        n2 = _STYLE_SUFFIX.sub("", n)
        n = n2 if len(n2) >= 3 else n
    return (aliases or {}).get(n, n)


def _srgb_to_lab(rgb: tuple[int, int, int]) -> np.ndarray:
    c = np.asarray(rgb, dtype=np.float64) / 255.0
    c = np.where(c <= 0.04045, c / 12.92, ((c + 0.055) / 1.055) ** 2.4)
    m = np.array([[0.4124564, 0.3575761, 0.1804375], [0.2126729, 0.7151522, 0.0721750], [0.0193339, 0.1191920, 0.9503041]])
    xyz = m @ c / np.array([0.95047, 1.0, 1.08883])
    f = np.where(xyz > (6 / 29) ** 3, np.cbrt(xyz), xyz / (3 * (6 / 29) ** 2) + 4 / 29)
    return np.array([116 * f[1] - 16, 500 * (f[0] - f[1]), 200 * (f[1] - f[2])])


def delta_e76(a: tuple[int, int, int], b: tuple[int, int, int]) -> float:
    return float(np.linalg.norm(_srgb_to_lab(a) - _srgb_to_lab(b)))


# ====================================================================== 累加器


def _safe_div(a: float, b: float) -> Optional[float]:
    return a / b if b else None


def _mean(xs: list[float]) -> Optional[float]:
    return float(np.mean(xs)) if xs else None


@dataclass
class Accumulator:
    """一個樣本（可多頁）的指標累加器：先累加原始數量，最後 finalize() 算出比例與平均（micro 平均）。"""

    counts: dict[str, float] = field(default_factory=dict)
    lists: dict[str, list[float]] = field(default_factory=dict)
    values: dict[str, list[float]] = field(default_factory=dict)  # 已經是頁級數值的指標（例如端到端），取平均

    def add(self, key: str, n: float = 1.0) -> None:
        self.counts[key] = self.counts.get(key, 0.0) + n

    def push(self, key: str, v: float) -> None:
        self.lists.setdefault(key, []).append(float(v))

    def value(self, key: str, v: Optional[float]) -> None:
        if v is not None:
            self.values.setdefault(key, []).append(float(v))

    def merge(self, other: "Accumulator") -> "Accumulator":
        for k, v in other.counts.items():
            self.add(k, v)
        for k, vs in other.lists.items():
            self.lists.setdefault(k, []).extend(vs)
        for k, vs in other.values.items():
            self.values.setdefault(k, []).extend(vs)
        return self

    def finalize(self, params: dict | None = None) -> dict[str, Optional[float]]:
        c, L = self.counts.get, self.lists.get
        out: dict[str, Optional[float]] = {}
        nt, npred, nm = c("text_truth", 0), c("text_pred", 0), c("text_matched", 0)
        if nt or npred:
            out["text_recall"] = _safe_div(nm, nt)  # 沒有標準答案行：召回率無定義
            out["text_precision"] = _safe_div(nm, npred)  # 沒有預測行：精確率無定義
            out["text_f1"] = 2 * nm / (nt + npred)  # 等同 2PR/(P+R)，且在一邊為 0 時正確給 0
        for k, num, den in (("cer", "err_all", "chars_all"), ("cer_zh", "err_zh", "chars_zh"), ("cer_en", "err_en", "chars_en")):
            out[k] = _safe_div(c(num, 0), c(den, 0)) if c(den, 0) else None
        if c("page_chars", 0):
            out["page_cer"] = c("page_err", 0) / c("page_chars", 0)
        for k in ("position_error_pt", "baseline_error_pt", "size_error_pt", "size_error_pct", "color_delta_e"):
            out[k] = _mean(L(k, []))
        for k in ("orientation_accuracy", "font_top1", "font_top3", "bold_accuracy", "italic_accuracy", "shape_kind_accuracy", "reading_order"):
            out[k] = _mean(L(k, []))
        for kind in ("image", "shape"):
            t, pr, m = c(f"{kind}_truth", 0), c(f"{kind}_pred", 0), c(f"{kind}_matched", 0)
            out[f"{kind}_recall"] = _safe_div(m, t)
            out[f"{kind}_precision"] = _safe_div(m, pr)
        tp, fp, fn = c("pair_tp", 0), c("pair_fp", 0), c("pair_fn", 0)
        if c("pair_pages", 0):
            out["paragraph_pair_f1"] = (2 * tp / (2 * tp + fp + fn)) if (tp + fp + fn) else 1.0
        if c("bpp_den", 0):
            out["boxes_per_paragraph"] = c("bpp_num", 0) / c("bpp_den", 0)
        if c("box_truth", 0):
            out["box_count_error"] = abs(c("box_pred", 0) - c("box_truth", 0)) / c("box_truth", 0)
        for k, vs in self.values.items():
            out[k] = _mean(vs)
        out = {k: v for k, v in out.items() if v is not None}
        out["composite"] = composite(out, params)
        return {k: v for k, v in out.items() if v is not None}


# ====================================================================== 逐頁比較


def _run_attrs(line: TextLine, ri: int, page_width_pt: float, aliases: dict) -> dict[str, Any]:
    r = line.runs[ri]
    return {
        "size_pt": r.font_size.value * page_width_pt,
        "fonts": [normalize_family(f.name, aliases) for f in r.font_candidates],
        "bold": bool(r.bold.value), "italic": bool(r.italic.value),
        "color": (r.color.value.r, r.color.value.g, r.color.value.b),
    }


def _perp_dist(pt: tuple[float, float], a: Point, b: Point) -> float:
    dx, dy = b.x - a.x, b.y - a.y
    n = math.hypot(dx, dy)
    if n < 1e-12:
        return math.hypot(pt[0] - a.x, pt[1] - a.y)
    return abs(dx * (pt[1] - a.y) - dy * (pt[0] - a.x)) / n


def compare_texts(pred: ElementList, truth: ElementList, page_width_pt: float, params: dict | None = None, acc: Accumulator | None = None,
                  use_baseline: bool = True) -> tuple[Accumulator, list[tuple[str, str]]]:
    """文字行：配對、字元錯誤率、位置、屬性。回傳（累加器、配對清單 [(標準答案行編號, 預測行編號)]）。

    use_baseline=False：標準答案沒有基線資訊（例如人工標註只畫了框），不計基線偏移。"""
    p = _p(params)
    acc = acc or Accumulator()
    tl = [t for t in truth.texts if not t.hidden]
    pl = [t for t in pred.texts if not t.hidden]
    thick = p["min_box_thickness_pt"] / page_width_pt
    pairs = match([t.quad for t in tl], [t.quad for t in pl], p["text_iou_threshold"], thick)
    acc.add("text_truth", len(tl))
    acc.add("text_pred", len(pl))
    acc.add("text_matched", len(pairs))
    matched_t, matched_p = {i for i, _, _ in pairs}, {j for _, j, _ in pairs}
    tchars = [_norm_chars(t, p) for t in tl]
    pchars = [_norm_chars(t, p) for t in pl]
    aliases = p["font_aliases"]
    page_err = 0
    for i, j, _ in pairs:
        tc, pc = tchars[i], pchars[j]
        path = align([c[0] for c in tc], [c[0] for c in pc])
        errs = 0
        run_votes: dict[int, list[int]] = {}
        for a, b in path:
            if a is not None:
                cls = tc[a][2]
                acc.add("chars_all")
                acc.add(f"chars_{cls}")
                wrong = b is None or tc[a][0] != pc[b][0]
                if wrong:
                    errs += 1
                    acc.add("err_all")
                    acc.add(f"err_{cls}")
                if b is not None:
                    run_votes.setdefault(tc[a][1], []).append(pc[b][1])
            else:  # 插入：依插入的字歸類
                cls = pc[b][2]
                errs += 1
                acc.add("err_all")
                acc.add(f"err_{cls}")
        page_err += errs
        # 位置
        t, q = tl[i], pl[j]
        # 位置：四角平均距離，取四種循環對應中最小的（與方向無關；方向另外算）
        tp, qp = t.quad.points, q.quad.points
        d = min(float(np.mean([math.hypot(tp[k].x - qp[(k + r) % 4].x, tp[k].y - qp[(k + r) % 4].y) for k in range(4)])) for r in range(4))
        acc.push("position_error_pt", d * page_width_pt)
        diff = (q.quad.angle_deg - t.quad.angle_deg + 180.0) % 360.0 - 180.0
        acc.push("orientation_accuracy", float(abs(diff) < p["orientation_tolerance_deg"]))
        mid = ((t.baseline[0].x + t.baseline[1].x) / 2, (t.baseline[0].y + t.baseline[1].y) / 2)
        if use_baseline:
            acc.push("baseline_error_pt", _perp_dist(mid, q.baseline[0], q.baseline[1]) * page_width_pt)
        # 屬性：以標準答案的樣式段為單位
        for tri, votes in run_votes.items():
            pri = max(set(votes), key=votes.count)
            ta = _run_attrs(t, tri, page_width_pt, aliases)
            pa = _run_attrs(q, pri, page_width_pt, aliases)
            acc.push("size_error_pt", abs(pa["size_pt"] - ta["size_pt"]))
            if ta["size_pt"] > 0:
                acc.push("size_error_pct", 100.0 * abs(pa["size_pt"] - ta["size_pt"]) / ta["size_pt"])
            if ta["fonts"]:
                acc.push("font_top1", float(bool(pa["fonts"]) and pa["fonts"][0] == ta["fonts"][0]))
                acc.push("font_top3", float(ta["fonts"][0] in pa["fonts"][:3]))
            acc.push("bold_accuracy", float(pa["bold"] == ta["bold"]))
            acc.push("italic_accuracy", float(pa["italic"] == ta["italic"]))
            acc.push("color_delta_e", delta_e76(pa["color"], ta["color"]))
    # 整頁字元錯誤率：漏抓的行整行算刪除、多抓的行整行算插入
    total_truth_chars = sum(len(c) for c in tchars)
    page_err += sum(len(tchars[i]) for i in range(len(tl)) if i not in matched_t)
    page_err += sum(len(pchars[j]) for j in range(len(pl)) if j not in matched_p)
    acc.add("page_chars", total_truth_chars)
    acc.add("page_err", page_err)
    return acc, [(tl[i].id, pl[j].id) for i, j, _ in pairs]


def compare_regions(pred: ElementList, truth: ElementList, page_width_pt: float, params: dict | None = None, acc: Accumulator | None = None) -> Accumulator:
    """圖片與形狀：IoU 配對的召回率與精確率、形狀種類正確率。"""
    p = _p(params)
    acc = acc or Accumulator()
    thick = p["min_box_thickness_pt"] / page_width_pt

    def keep_img(i):
        return not (p["ignore_full_page_images"] and i.kind.value == ImageKind.FULL_PAGE)

    ti, pi = [i for i in truth.images if keep_img(i)], [i for i in pred.images if keep_img(i)]
    pairs = match([i.quad for i in ti], [i.quad for i in pi], p["region_iou_threshold"], thick)
    acc.add("image_truth", len(ti))
    acc.add("image_pred", len(pi))
    acc.add("image_matched", len(pairs))
    ts = [s for s in truth.shapes if not (p["ignore_page_background_shapes"] and s.is_page_background)]
    ps = [s for s in pred.shapes if not (p["ignore_page_background_shapes"] and s.is_page_background)]
    pairs = match([s.quad for s in ts], [s.quad for s in ps], p["region_iou_threshold"], thick)
    acc.add("shape_truth", len(ts))
    acc.add("shape_pred", len(ps))
    acc.add("shape_matched", len(pairs))
    for i, j, _ in pairs:
        acc.push("shape_kind_accuracy", float(ts[i].geometry == ps[j].geometry))
    return acc


def line_order(layout: Optional[PageLayout], elements: ElementList) -> list[str]:
    """由版面推出文字行的閱讀順序（文字框 → 段落 → 行）；沒有版面就用元素清單的順序。"""
    ids = [t.id for t in elements.texts]
    if layout is None:
        return ids
    paras = {p.id: p for p in layout.paragraphs}
    boxes = {b.id: b for b in layout.text_boxes}
    order: list[str] = []
    seen: set[str] = set()
    for eid in layout.reading_order:
        for pid in (boxes[eid].paragraph_ids if eid in boxes else [eid] if eid in paras else []):
            for lid in paras[pid].line_ids if pid in paras else []:
                if lid not in seen:
                    order.append(lid)
                    seen.add(lid)
    for b in layout.text_boxes:  # 不在閱讀順序裡的框，照原順序補上
        for pid in b.paragraph_ids:
            for lid in paras[pid].line_ids if pid in paras else []:
                if lid not in seen:
                    order.append(lid)
                    seen.add(lid)
    order += [i for i in ids if i not in seen]
    return order


def paragraph_groups(layout: PageLayout) -> dict[str, str]:
    return {lid: p.id for p in layout.paragraphs for lid in p.line_ids}


def compare_layout(pairs: list[tuple[str, str]], truth_groups: Optional[dict[str, str]], pred_layout: Optional[PageLayout],
                   truth_order: Optional[list[str]], pred_order: Optional[list[str]], truth_box_count: Optional[int] = None,
                   truth_paragraph_count: Optional[int] = None, acc: Accumulator | None = None) -> Accumulator:
    """結構：段落分組成對 F1、文字框數 ÷ 段落數、閱讀順序一致率。只看已配對的行。"""
    acc = acc or Accumulator()
    pred_groups = paragraph_groups(pred_layout) if pred_layout is not None else {}
    if truth_groups is not None and pred_layout is not None and len(pairs) >= 2:
        acc.add("pair_pages")
        for a in range(len(pairs)):
            for b in range(a + 1, len(pairs)):
                (t1, p1), (t2, p2) = pairs[a], pairs[b]
                ts = truth_groups.get(t1) is not None and truth_groups.get(t1) == truth_groups.get(t2)
                ps = pred_groups.get(p1) is not None and pred_groups.get(p1) == pred_groups.get(p2)
                acc.add("pair_tp" if ts and ps else "pair_fp" if ps else "pair_fn" if ts else "pair_tn")
    if pred_layout is not None and pred_layout.text_boxes:
        den = truth_paragraph_count if truth_paragraph_count else len(pred_layout.paragraphs)
        if den:
            acc.add("bpp_num", len(pred_layout.text_boxes))
            acc.add("bpp_den", den)
    if truth_box_count:
        acc.add("box_truth", truth_box_count)
        acc.add("box_pred", len(pred_layout.text_boxes) if pred_layout is not None else 0)
    if truth_order is not None and pred_order is not None and len(pairs) >= 2:
        tpos = {lid: k for k, lid in enumerate(truth_order)}
        ppos = {lid: k for k, lid in enumerate(pred_order)}
        items = [(tpos[t], ppos[p]) for t, p in pairs if t in tpos and p in ppos]
        conc = tot = 0
        for a in range(len(items)):
            for b in range(a + 1, len(items)):
                dt, dp = items[a][0] - items[b][0], items[a][1] - items[b][1]
                if dt == 0:
                    continue
                tot += 1
                conc += (dt > 0) == (dp > 0) and dp != 0
        if tot:
            acc.push("reading_order", conc / tot)
    return acc


def is_rasterized(el: ElementList, aspect: float, params: dict | None = None) -> bool:
    """這一頁是否整頁以一張圖輸出、沒有任何可編輯文字。"""
    p = _p(params)
    if el.texts:
        return False
    page_area = aspect
    for im in el.images:
        if abs(_area(_poly(im.quad))) >= p["rasterized_area_ratio"] * page_area:
            return True
    return False


def compare_end_to_end(report: Optional[FidelityReport], acc: Accumulator | None = None) -> Accumulator:
    """端到端：取自還原度報告。"""
    acc = acc or Accumulator()
    if report is None:
        return acc
    acc.value("e2e_score", report.overall_score)
    sims = [p.target_similarity for p in report.pages if p.target_similarity is not None]
    acc.value("e2e_similarity", _mean(sims))
    boxes = [e for p in report.pages for e in p.elements if e.kind == "text_box"]
    tm = [e.text_match for e in boxes if e.text_match is not None]
    lm = [e.linebreak_match for e in boxes if e.linebreak_match is not None]
    pe = [e.position_error_pt for e in boxes if e.position_error_pt is not None]
    acc.value("e2e_text_match", _safe_div(sum(tm), len(tm)) if tm else None)
    acc.value("e2e_linebreak_match", _safe_div(sum(lm), len(lm)) if lm else None)
    acc.value("e2e_position_error_pt", _mean(pe))
    return acc


def evaluate_page(pred: ElementList, truth: ElementList, page_width_pt: float, pred_layout: Optional[PageLayout] = None,
                  truth_layout: Optional[PageLayout] = None, truth_groups: Optional[dict[str, str]] = None,
                  truth_order: Optional[list[str]] = None, params: dict | None = None, acc: Accumulator | None = None,
                  truth_box_count: Optional[int] = None, use_baseline: bool = True) -> Accumulator:
    """一頁的全部對照指標（文字、圖形、結構）。truth_groups／truth_order／truth_box_count 不給時由 truth_layout 推出。"""
    acc = acc or Accumulator()
    acc, pairs = compare_texts(pred, truth, page_width_pt, params, acc, use_baseline)
    compare_regions(pred, truth, page_width_pt, params, acc)
    if truth_groups is None and truth_layout is not None:
        truth_groups = paragraph_groups(truth_layout)
    if truth_order is None and truth_layout is not None:
        truth_order = line_order(truth_layout, truth)
    n_para = len(set(truth_groups.values())) if truth_groups else None
    n_box = truth_box_count if truth_box_count else (len(truth_layout.text_boxes) if truth_layout is not None and truth_layout.text_boxes else None)
    compare_layout(pairs, truth_groups, pred_layout, truth_order, line_order(pred_layout, pred) if pred_layout is not None else None, n_box, n_para, acc)
    return acc


# ====================================================================== 綜合分數


def goodness(name: str, v: float, params: dict | None = None) -> Optional[float]:
    """把一個指標換成 0 到 1 的好壞值（1 最好）。"""
    p = _p(params)
    info = METRICS.get(name)
    if info is None or v is None or (isinstance(v, float) and math.isnan(v)):
        return None
    if info.unit == "比例" and name not in p["composite_scales"]:
        g = v if info.higher_is_better else 1.0 - v
    else:
        s = p["composite_scales"].get(name)
        if not s:
            return None
        g = 1.0 - v / s if not info.higher_is_better else v / s
    return float(min(1.0, max(0.0, g)))


def composite(metrics: dict[str, float], params: dict | None = None) -> Optional[float]:
    p = _p(params)
    num = den = 0.0
    for k, w in p["composite_weights"].items():
        if w and k in metrics:
            g = goodness(k, metrics[k], p)
            if g is not None:
                num += w * g
                den += w
    return num / den if den else None


# ====================================================================== 信心值分佈


def collect_confidences(obj: Any, out: dict[str, list[float]] | None = None, name: str = "") -> dict[str, list[float]]:
    """走遍產物，收集每個帶信心值欄位（Measured）的信心值，依欄位名稱分組。"""
    out = {} if out is None else out
    if isinstance(obj, Measured):
        out.setdefault(name or "value", []).append(float(obj.confidence))
        return out
    if hasattr(obj, "__class__") and hasattr(obj.__class__, "model_fields"):
        for k in obj.__class__.model_fields:
            collect_confidences(getattr(obj, k), out, k)
    elif isinstance(obj, (list, tuple)):
        for x in obj:
            collect_confidences(x, out, name)
    elif isinstance(obj, dict):
        for k, x in obj.items():
            collect_confidences(x, out, name)
    return out


def distribution(values: Iterable[float], bins: int = 10) -> dict[str, Any]:
    v = np.asarray(list(values), dtype=float)
    if v.size == 0:
        return {"n": 0}
    hist, _ = np.histogram(np.clip(v, 0, 1), bins=bins, range=(0.0, 1.0))
    return {"n": int(v.size), "mean": float(v.mean()), "min": float(v.min()), "p10": float(np.percentile(v, 10)),
            "median": float(np.median(v)), "hist": hist.astype(int).tolist()}
