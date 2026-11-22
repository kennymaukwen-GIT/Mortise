"""③ 辨識：原生 PDF 裡「被扁平化」的內容，變回可編輯的文字與圖形。由擷取產線（s3_recognize._extract_line）呼叫。

很多「不能編輯的 PDF」不是整頁掃描，而是原生 PDF 裡夾著被壓平的部分。這裡處理兩種：

  s3.outlined_text  文字轉成的外框（分群時 kind 為 outlined_text 的群）→ 文字行
    1. 只畫這一群外框（透明底），取不透明度做成「白底黑字」的影像去辨識文字：不受字的顏色影響。
    2. 每一行辨識結果對回外框：哪些外框圈屬於這一行、每個字形是哪一個字（依位置做單調對齊）。
    3. 位置、基線、字級直接由外框的精確幾何求出：字形的上緣與下緣 = 基線 − 字級 × 字型裡這個字的墨跡上緣與下緣，
       以最小平方法解出基線與字級，再沿基線解出起點與字距。字型候選先由重繪比對（text_attributes）選出，
       再依幾何殘差重新排序；字型只是推論。顏色直接取外框的填色（精確）。
    4. 辨識信心、字形對應、幾何殘差都過關才換成文字，並且不再輸出那些外框與它們對應的圖片；
       同一條路徑跨好幾行時，那幾行一起決定（同一塊區域不會同時輸出文字與外框）。
       沒過關的留在原本的外框向量圖（至少可以當圖形編輯），備註寫明原因。

  s3.embedded       內嵌圖片裡的文字與平面圖形（截圖、整頁一張圖再疊幾行字、匯出時被壓平的圖表）
    見 embedded_images()。

這裡的數值都是量測（由演算法從幾何或影像量出），不是精確擷取；來源類型照實標示。
"""

from __future__ import annotations

import math
from collections import Counter
from dataclasses import dataclass, field
from typing import Optional

import numpy as np

from ..artifacts import (
    Color, FontCandidate, ImageElement, ImageKind, PageSpec, Point, Provenance, Quad, ShapeElement, ShapeGeometry, TextLine, TextRun, exact,
    make_element_id, measured,
)
from ..pipeline.store import Store
from ..tools import pdfium as pdf

NAME = "s3"
OUTLINED_STEP = "s3.outlined_text@1"
EMBEDDED_STEP = "s3.embedded@1"


# ====================================================================== 共用：文字辨識引擎與字型庫


def _ocr_engine(c: dict):
    """文字辨識引擎。影像產線已經載入過就共用它的（模型只載入一次），否則自己載入一份。"""
    try:
        from . import s3_image_line

        get = getattr(s3_image_line, "_engine", None)
        if callable(get):
            return get(c.get("ocr_params"))
    except Exception:  # 影像產線的內部函式改名或載入失敗：改用自己的
        pass
    global _OWN_ENGINE
    if _OWN_ENGINE is None:
        from ..recognition.ocr import OcrEngine

        _OWN_ENGINE = OcrEngine(c.get("ocr_params"))
    return _OWN_ENGINE


def _font_library():
    try:
        from . import s3_image_line

        get = getattr(s3_image_line, "_fonts", None)
        if callable(get):
            return get()
    except Exception:
        pass
    from ..recognition.fontlib import FontLibrary

    return FontLibrary.default()


_OWN_ENGINE = None


# ====================================================================== s3.outlined_text：外框字 → 文字


def _cubic_extrema(p0: float, p1: float, p2: float, p3: float) -> list[float]:
    """三次貝茲在一個座標軸上的極值（0 < t < 1 之內的）。外框圈的精確外框要用它，控制點不一定在曲線上。"""
    a, b, cc = p1 - p0, p2 - p1, p3 - p2
    A, B, C = a - 2 * b + cc, 2 * (b - a), a
    ts: list[float] = []
    if abs(A) < 1e-14:
        if abs(B) > 1e-14:
            ts.append(-C / B)
    else:
        d = B * B - 4 * A * C
        if d >= 0:
            r = math.sqrt(d)
            ts += [(-B + r) / (2 * A), (-B - r) / (2 * A)]
    out = []
    for t in ts:
        if 0.0 < t < 1.0:
            mt = 1 - t
            out.append(mt * mt * mt * p0 + 3 * mt * mt * t * p1 + 3 * mt * t * t * p2 + t * t * t * p3)
    return out


def contour_boxes_exact(path: list[list[float]]) -> list[tuple[float, float, float, float]]:
    """一條路徑裡每一圈（子路徑）的精確外框（左、上、右、下），曲線取真正的極值，不是控制點的外框。"""
    out: list[list[float]] = []
    cur: Optional[tuple[float, float]] = None
    for sg in path:
        kind = sg[0]
        if kind == 3:
            continue
        if kind == 0 or not out:
            out.append([math.inf, math.inf, -math.inf, -math.inf])
        b = out[-1]
        if kind == 2 and cur is not None:
            xs = [sg[5]] + _cubic_extrema(cur[0], sg[1], sg[3], sg[5])
            ys = [sg[6]] + _cubic_extrema(cur[1], sg[2], sg[4], sg[6])
        else:
            xs, ys = [sg[-2]], [sg[-1]]
        b[0], b[1], b[2], b[3] = min(b[0], *xs), min(b[1], *ys), max(b[2], *xs), max(b[3], *ys)
        cur = (sg[-2], sg[-1])
    return [tuple(b) for b in out if b[2] >= b[0] and b[3] >= b[1]]


@dataclass
class _Contour:
    member: int  # 屬於群裡第幾條路徑
    box: tuple[float, float, float, float]  # 頁面相對座標（左、上、右、下）
    line: int = -1  # 屬於第幾行辨識結果；-1 表示不屬於任何一行


@dataclass
class _Piece:
    """一行裡的一個字形（水平位置大幅重疊的外框圈併在一起：字的孔、i 的點、冒號）。"""

    box: tuple[float, float, float, float]
    contours: list[int]
    char: int = -1  # 對應到行內第幾個字（在 text 中的位置）


@dataclass
class _LineFit:
    face: object
    s: float  # 字級（頁面相對單位）
    by: float  # 基線的 y
    t: float  # 每個字額外的字距
    err: float  # 幾何殘差（em）
    s_err: float  # 字級的誤差範圍
    score: float  # 綜合分數（重繪比對 × 幾何）
    pens: list[float] = field(default_factory=list)  # 每個字的筆位 x（與 text 一一對應）
    end: float = 0.0  # 最後一個字的筆位加上它的前進寬度


@dataclass
class _OLine:
    """一行辨識結果與它對回外框的情形。"""

    ocr: object  # OcrLine
    box: tuple[float, float, float, float]  # 頁面相對座標
    angle: float  # 度
    pieces: list[_Piece] = field(default_factory=list)
    fail: str = ""  # 不能轉成文字的原因（空字串表示可以）
    fit: Optional[_LineFit] = None
    members: set[int] = field(default_factory=set)


def _merge_pieces(contours: list[_Contour], idx: list[int]) -> list[_Piece]:
    """同一行的外框圈 → 字形：依左緣排序，水平位置重疊超過較窄者一半的併在一起。"""
    order = sorted(idx, key=lambda k: contours[k].box[0])
    pieces: list[_Piece] = []
    for k in order:
        b = contours[k].box
        if pieces:
            last = pieces[-1].box
            overlap = min(last[2], b[2]) - max(last[0], b[0])
            if overlap > 0 and overlap >= 0.5 * min(last[2] - last[0], b[2] - b[0]):
                pieces[-1].box = (min(last[0], b[0]), min(last[1], b[1]), max(last[2], b[2]), max(last[3], b[3]))
                pieces[-1].contours.append(k)
                continue
        pieces.append(_Piece(box=b, contours=[k]))
    return pieces


def _align(pieces: list[_Piece], spans: list[tuple[int, float, float]], h: float, max_cost: float) -> bool:
    """字形與辨識出的字做單調對齊：每個字至少一個字形、每個字形屬於一個字，總距離最小（動態規劃）。
    spans 是非空白字的（在 text 中的位置、左緣、右緣），頁面相對座標。成功時把結果寫進 piece.char。"""
    G, C = len(pieces), len(spans)
    if G < C or C == 0:
        return False
    centers = [(p.box[0] + p.box[2]) / 2 for p in pieces]

    def cost(g: int, k: int) -> float:
        _, a, b = spans[k]
        x = centers[g]
        return (a - x if x < a else (x - b if x > b else 0.0)) / max(h, 1e-12)

    INF = math.inf
    dp = np.full((G, C), INF)
    back = np.zeros((G, C), dtype=np.int8)  # 0：前一個字形屬於同一個字；1：屬於前一個字
    dp[0, 0] = cost(0, 0)
    for g in range(1, G):
        for k in range(max(0, C - (G - g)), min(C, g + 1)):
            same = dp[g - 1, k]
            prev = dp[g - 1, k - 1] if k > 0 else INF
            best, back[g, k] = (same, 0) if same <= prev else (prev, 1)
            if best < INF:
                dp[g, k] = best + cost(g, k)
    if not math.isfinite(dp[G - 1, C - 1]):
        return False
    k = C - 1
    assign = [0] * G
    for g in range(G - 1, -1, -1):
        assign[g] = k
        if g > 0 and back[g, k] == 1:
            k -= 1
    if max(cost(g, assign[g]) for g in range(G)) > max_cost:
        return False
    for g, p in enumerate(pieces):
        p.char = spans[assign[g]][0]
    return True


def _char_boxes(line: _OLine) -> dict[int, tuple[float, float, float, float]]:
    """每個字（在 text 中的位置）的外框：屬於它的字形的聯集。"""
    out: dict[int, list[float]] = {}
    for p in line.pieces:
        b = out.get(p.char)
        out[p.char] = list(p.box) if b is None else [min(b[0], p.box[0]), min(b[1], p.box[1]), max(b[2], p.box[2]), max(b[3], p.box[3])]
    return {k: tuple(v) for k, v in out.items()}


def _fit_face(face, text: str, boxes: dict[int, tuple[float, float, float, float]]) -> Optional[dict]:
    """用一個字型的墨跡外框擬合量到的字形外框，擬合不了回傳 None。模型（y 向下）：
      上緣 = 基線 − 字級 × 墨跡上緣；下緣 = 基線 − 字級 × 墨跡下緣（解出基線與字級，去掉離群的再解一次）；
      每個字都量一次「整行的起點」= 量到的左右緣 − 字級 × 字型排出來的左右墨跡位置（各字分別量，不受字距調整表不同的影響）；
      字距 = 這個起點對「第幾個字」的斜率；每個字的筆位 = 它量到的起點 + 字級 × 字型排出來的筆位。
    幾何殘差（em）合併上下緣的殘差與字寬的差：字型對了兩者都接近 0。"""
    try:
        layout = face.layout(text)
    except Exception:
        return None
    n = len(text)
    rows_v, rhs_v, rows_k = [], [], []
    for k, (l, t, r, b) in boxes.items():
        ink = layout.ink[k] if k < len(layout.ink) else None
        if ink is None:
            continue
        x0, x1, top, bot = ink
        rows_v += [[1.0, -top], [1.0, -bot]]
        rhs_v += [t, b]
        rows_k.append((k, l, r, x0, x1))
    if not rows_k or len(layout.starts) != n:
        return None
    A, y = np.array(rows_v), np.array(rhs_v)
    keep = np.ones(len(y), dtype=bool)
    sol = None
    for _ in range(2):  # 第二輪去掉離群的（例如字型裡這個字的造型不一樣）
        if keep.sum() < 2 or np.linalg.matrix_rank(A[keep]) < 2:
            return None
        sol, *_ = np.linalg.lstsq(A[keep], y[keep], rcond=None)
        if sol[1] <= 0:
            return None
        res = A @ sol - y
        med = float(np.median(np.abs(res[keep])))
        new = np.abs(res) <= max(4.0 * med, 0.03 * sol[1])
        if new.sum() == keep.sum() or new.sum() < 2:
            break
        keep = new
    by, s = float(sol[0]), float(sol[1])
    res_v = (A @ sol - y)[keep]
    # 沿基線：每個字量到的整行起點、字寬的差、字距
    meas: dict[int, float] = {}
    width_res = []
    for k, l, r, x0, x1 in rows_k:
        meas[k] = ((l - s * x0) + (r - s * x1)) / 2
        width_res.append((r - l) - s * (x1 - x0))
    ks = np.array(sorted(meas), dtype=float)
    d = np.array([meas[int(k)] for k in ks])
    if len(ks) >= 2:
        use = np.ones(len(ks), dtype=bool)
        for _ in range(2):
            coef = np.polyfit(ks[use], d[use], 1)
            res = d - np.polyval(coef, ks)
            med = float(np.median(np.abs(res[use])))
            new = np.abs(res) <= max(4.0 * med, 0.05 * s)
            if new.sum() == use.sum() or new.sum() < 2:
                break
            use = new
        t, ox = float(coef[0]), float(coef[1])
    else:
        t, ox = 0.0, float(d[0])
    pens = [meas.get(k, ox + t * k) + s * layout.starts[k] for k in range(n)]
    end = pens[-1] + s * (layout.advance - layout.starts[-1])
    err = math.sqrt((float(np.mean(res_v ** 2)) + float(np.mean(np.square(width_res)))) / 2) / s
    # 字級的誤差範圍：上下緣的殘差 ÷ 參與擬合的字形高度範圍
    spread = float(np.ptp(A[keep][:, 1])) if keep.sum() >= 2 else 1.0
    s_err = float(np.sqrt(np.mean(res_v ** 2))) / max(spread, 0.1) * math.sqrt(2.0 / max(keep.sum(), 1))
    return {"s": s, "by": by, "t": t, "err": err, "s_err": s_err, "pens": pens, "end": end}


def _quad_box(pts: np.ndarray, to_page) -> tuple[tuple[float, float, float, float], float]:
    q = [to_page(float(x), float(y)) for x, y in pts]
    xs, ys = [p[0] for p in q], [p[1] for p in q]
    u = np.asarray(pts[1], float) - np.asarray(pts[0], float)
    return (min(xs), min(ys), max(xs), max(ys)), math.degrees(math.atan2(u[1], u[0]))


def outlined_to_text(groups: list, reader: pdf.PageReader, spec: PageSpec, src_w: float, c: dict, store: Store, notes: list[str]) -> tuple[list[TextLine], list, set[int]]:
    """把疑似文字轉外框的群（kind 為 outlined_text）變回文字行。
    回傳（新的文字行、剩下的群、已轉成文字的路徑的繪製順序）。後者不再畫成向量圖，但仍要從底圖隱藏。
    groups 裡的成員是 s3_recognize._Path；剩下的群照舊交給 _vector_graphics。"""
    if not c.get("pdf_outlined_to_text", True):
        return [], groups, set()
    targets = [g for g in groups if g.kind == "outlined_text"]
    if not targets:
        return [], groups, set()
    try:
        engine = _ocr_engine(c)
        fonts = _font_library()
    except Exception as e:  # 辨識模型載入失敗：外框字維持是向量圖
        notes.append(f"外框字轉回文字需要的文字辨識無法使用（{type(e).__name__}），外框字維持是向量圖。")
        return [], groups, set()
    texts: list[TextLine] = []
    converted: set[int] = set()
    out_groups = []
    for g in groups:
        if g.kind != "outlined_text":
            out_groups.append(g)
            continue
        try:
            lines, done, remainder, reason = _convert_group(g, engine, fonts, reader, spec, src_w, c)
        except Exception as e:  # 單一群失敗不影響整頁：這一群維持是外框
            lines, done, remainder, reason = [], set(), g.members, f"處理時出錯（{type(e).__name__}）"
        texts += lines
        converted |= done
        if remainder:
            note = (f"其中 {len(lines)} 行已轉回文字，其餘的" if lines else "沒有轉回文字，") + f"保留成外框（可以當圖形編輯），原因：{reason}。"
            out_groups.append(type(g)(members=list(remainder), kind=g.kind, notes=g.notes + [note]))
    if texts:
        notes.append(f"這一頁有 {len(texts)} 行文字原本被轉成外框，已轉回可編輯的文字（文字內容由辨識而來，位置與字級由外框量出，字型為推論，請確認）。")
    return texts, out_groups, converted


def _convert_group(g, engine, fonts, reader: pdf.PageReader, spec: PageSpec, src_w: float, c: dict):
    """一群外框 → （文字行、已轉換路徑的繪製順序、剩下的路徑、沒轉的原因）。"""
    from .s3_recognize import glyph_rows

    members = g.members
    aspect = spec.aspect_ratio
    # 1. 每一圈外框的精確外框
    contours: list[_Contour] = []
    for i, it in enumerate(members):
        for b in contour_boxes_exact(it.rel):
            contours.append(_Contour(member=i, box=b))
    if not contours:
        return [], set(), members, "沒有外框"
    # 2. 解析度：讓主要字形高約 pdf_outlined_ocr_glyph_px 個像素
    rows = glyph_rows([it.rel for it in members], c, suspected=True)
    if rows:
        glyph_h = float(np.median([r.height for r in rows]))
    else:
        glyph_h = float(np.median([b.box[3] - b.box[1] for b in contours]))
    target = float(c.get("pdf_outlined_ocr_glyph_px", 48))
    dpi = target / max(glyph_h * src_w / 72.0, 1e-6)
    dpi = min(max(dpi, float(c.get("pdf_outlined_ocr_min_dpi", 72))), float(c.get("pdf_outlined_ocr_max_dpi", 600)))
    l = max(min(b.box[0] for b in contours), 0.0)
    t = max(min(b.box[1] for b in contours), 0.0)
    r = min(max(b.box[2] for b in contours), 1.0)
    bt = min(max(b.box[3] for b in contours), aspect)
    if r <= l or bt <= t:
        return [], set(), members, "外框在頁面外"
    max_px = float(c.get("pdf_outlined_ocr_max_pixels", 16_000_000))
    scale = dpi / 72.0 * src_w
    if (r - l) * (bt - t) * scale * scale > max_px:
        dpi *= math.sqrt(max_px / ((r - l) * (bt - t) * scale * scale))
    fw, fh = reader.render_size(dpi)
    box = (max(math.floor(l * fw) - 2, 0), max(math.floor(t / aspect * fh) - 2, 0), min(math.ceil(r * fw) + 2, fw), min(math.ceil(bt / aspect * fh) + 2, fh))
    rendered = reader.render_only([({it.z for it in members}, dpi, box)])[0]
    alpha = np.asarray(rendered.getchannel("A"))
    margin = int(round(0.6 * target))
    gray = np.pad(255 - alpha, margin, constant_values=255).astype(np.uint8)
    rgb = np.ascontiguousarray(np.stack([gray] * 3, axis=2))

    def to_page(u: float, v: float) -> tuple[float, float]:
        return (u - margin + box[0]) / fw, (v - margin + box[1]) / fh * aspect

    # 3. 辨識，並把每一圈外框分給一行
    ocr_lines = [ol for ol in engine.read(rgb) if ol.text.strip()]
    if not ocr_lines:
        return [], set(), members, "辨識不出文字"
    lines: list[_OLine] = []
    for ol in ocr_lines:
        bx, ang = _quad_box(np.asarray(ol.quad, float), to_page)
        lines.append(_OLine(ocr=ol, box=bx, angle=ang))
    for k, ct in enumerate(contours):
        cx, cy = (ct.box[0] + ct.box[2]) / 2, (ct.box[1] + ct.box[3]) / 2
        best, dist = -1, math.inf
        for i, ln in enumerate(lines):
            lx0, ly0, lx1, ly1 = ln.box
            m = 0.3 * (ly1 - ly0)
            if lx0 - m <= cx <= lx1 + m and ly0 - m <= cy <= ly1 + m:
                d = abs(cy - (ly0 + ly1) / 2)
                if d < dist:
                    best, dist = i, d
        ct.line = best
    thr = float(c.get("pdf_outlined_text_min_confidence", 0.9))
    max_cost = float(c.get("pdf_outlined_align_max_cost", 0.6))
    for i, ln in enumerate(lines):
        idx = [k for k, ct in enumerate(contours) if ct.line == i]
        ln.members = {contours[k].member for k in idx}
        if not idx:
            ln.fail = "辨識出的文字行底下沒有外框"
            continue
        if abs(ln.angle) > float(c.get("pdf_outlined_max_angle_deg", 1.5)):
            ln.fail = "文字是斜的或直排（目前只處理橫排）"
            continue
        ol = ln.ocr
        if (ol.score or 0.0) < thr:
            ln.fail = f"辨識信心 {ol.score:.2f} 低於門檻 {thr:.2f}"
            continue
        ln.pieces = _merge_pieces(contours, idx)
        q = np.asarray(ol.quad, float)
        u = q[1] - q[0]
        u = u / max(float(np.hypot(*u)), 1e-9)
        spans = []
        for k, ch in enumerate(ol.chars):
            if not ch.text.strip():
                continue
            a = to_page(*(q[0] + u * ch.x0))[0]
            b = to_page(*(q[0] + u * ch.x1))[0]
            spans.append((k, min(a, b), max(a, b)))
        if len(ol.chars) != len(ol.text) or not _align(ln.pieces, spans, ln.box[3] - ln.box[1], max_cost):
            ln.fail = "辨識出的字與外框的字形對不上"
    # 4. 字型候選：重繪比對最長的幾行，再依幾何殘差排序
    ok = [ln for ln in lines if not ln.fail]
    candidates = _font_candidates(ok, rgb, fonts, c) if ok else {}
    max_err = float(c.get("pdf_outlined_fit_max_error", 0.12))
    for ln in ok:
        ln.fit = _best_fit(ln, candidates, fonts)
        if ln.fit is None:
            ln.fail = "找不到能套上這些字形的字型"
        elif ln.fit.err > max_err:
            ln.fail = f"外框與字型的幾何差太多（{ln.fit.err:.3f} em）"
    # 5. 以「共用路徑」連起來的行一起決定；有外框沒分到任何一行的路徑，碰到的行都不轉
    n = len(lines)
    parent = list(range(n))

    def find(i: int) -> int:
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    owner_line: dict[int, int] = {}
    for i, ln in enumerate(lines):
        for mb in ln.members:
            if mb in owner_line:
                parent[find(i)] = find(owner_line[mb])
            else:
                owner_line[mb] = i
    stray = {ct.member for ct in contours if ct.line < 0}
    bad_unit: dict[int, str] = {}
    for i, ln in enumerate(lines):
        root = find(i)
        if ln.fail:
            bad_unit.setdefault(root, ln.fail)
        elif ln.members & stray:
            bad_unit.setdefault(root, "有外框不屬於任何辨識出的文字行")
    texts: list[TextLine] = []
    done_members: set[int] = set()
    reasons: list[str] = []
    for i, ln in enumerate(lines):
        root = find(i)
        if root in bad_unit:
            if bad_unit[root] not in reasons:
                reasons.append(bad_unit[root])
            continue
        texts.append(_text_line(ln, members, contours, spec.page_index))
        done_members |= ln.members
    if stray - done_members and not reasons:
        reasons.append("有外框不屬於任何辨識出的文字行")
    remainder = [it for i, it in enumerate(members) if i not in done_members]
    return texts, {members[i].z for i in done_members}, remainder, "；".join(reasons) or "辨識不出文字"


def _font_candidates(lines: list[_OLine], rgb: np.ndarray, fonts, c: dict) -> dict[str, tuple[object, float]]:
    """重繪比對（text_attributes）最長的幾行，得到字型候選（字型 →（字型物件、分數））。"""
    from ..recognition import text_attributes as ta

    probe = sorted(lines, key=lambda ln: -len(ln.ocr.text.strip()))[: int(c.get("pdf_outlined_font_probe_lines", 3))]
    out: dict[str, tuple[object, float]] = {}
    for ln in probe:
        try:
            attrs = ta.measure_line(rgb, ln.ocr, fonts)
        except Exception:
            continue
        for run in attrs.runs:
            for fm in run.fonts[:6]:
                face = fonts.find(fm.family, fm.style)
                if face is None:
                    continue
                prev = out.get(face.key)
                if prev is None or fm.score > prev[1]:
                    out[face.key] = (face, float(fm.score))
    return out


def _best_fit(ln: _OLine, candidates: dict[str, tuple[object, float]], fonts) -> Optional[_LineFit]:
    """在字型候選中找幾何最吻合的一個；候選都套不上時，改試字型庫裡所有涵蓋這些字的字型（只看幾何）。"""
    text = ln.ocr.text
    boxes = _char_boxes(ln)
    best: Optional[_LineFit] = None
    pools = [[(face, prior) for face, prior in candidates.values() if face.covers(text)]]
    pools.append([(face, 0.5) for face in fonts.faces(text)])
    for pool in pools:
        for face, prior in pool:
            fit = _fit_face(face, text, boxes)
            if fit is None:
                continue
            score = prior * math.exp(-fit["err"] / 0.02)
            if best is None or score > best.score:
                best = _LineFit(face=face, score=score, **fit)
        if best is not None and best.err <= 0.04:
            break
    return best


def _text_line(ln: _OLine, members: list, contours: list[_Contour], page_index: int) -> TextLine:
    """一行外框字 → 文字行。位置與字級由幾何擬合而來（量測），顏色取外框的填色（精確），字型是推論。"""
    ol, fit = ln.ocr, ln.fit
    text = ol.text
    face = fit.face
    pen = list(fit.pens)
    end = fit.end
    first = next(k for k, ch in enumerate(text) if ch.strip())
    last = max(k for k, ch in enumerate(text) if ch.strip())
    text = text[first:last + 1]
    pen = pen[first:last + 1]
    right = max(end, max(p.box[2] for p in ln.pieces))
    top, bottom = fit.by - fit.s * face.ascender, fit.by - fit.s * face.descender
    # 每個字的顏色：它的字形所屬路徑的填色；空白跟著前一個字
    color_of: dict[int, Color] = {}
    for p in ln.pieces:
        it = members[contours[p.contours[0]].member]
        if it.fill is not None and p.char - first not in color_of:
            color_of[p.char - first] = it.fill
    colors: list[Optional[Color]] = []
    for k in range(len(text)):
        colors.append(color_of.get(k, colors[-1] if colors else None))
    default = next((col for col in colors if col is not None), Color(r=0, g=0, b=0))
    colors = [col or default for col in colors]
    size_conf = float(max(0.3, min(0.98, 1.0 - fit.err * 10)))
    bold = measured(bool(face.is_bold), max(0.5, size_conf - 0.1), Provenance.INFERRED, OUTLINED_STEP)
    italic = measured(bool(face.is_italic), max(0.5, size_conf - 0.1), Provenance.INFERRED, OUTLINED_STEP)
    fonts = [FontCandidate(name=face.family, raw_name=f"{face.family} {face.style}".strip(), score=float(min(1.0, fit.score)))]
    runs: list[TextRun] = []
    k0 = 0
    for k in range(1, len(text) + 1):
        if k < len(text) and (colors[k] == colors[k0] or not text[k].strip()):
            continue
        seg = text[k0:k]
        x0 = pen[k0]
        x1 = pen[k] if k < len(text) else right
        char_x = [pen[j] - pen[0] for j in range(k0, k) if text[j].strip()]
        runs.append(TextRun(
            text=seg, quad=Quad.from_rect(x0, top, x1, bottom),
            font_size=measured(fit.s, size_conf, Provenance.MEASURED, OUTLINED_STEP, error=max(fit.s_err, fit.s * 0.002)),
            font_candidates=fonts, bold=bold, italic=italic, color=exact(colors[k0], OUTLINED_STEP),
            char_spacing=fit.t if abs(fit.t) > 0.01 * fit.s else None, char_x=char_x,
        ))
        k0 = k
    q = Quad.from_rect(pen[0], top, right, bottom)
    z = min(members[i].z for i in ln.members)
    return TextLine(
        id=make_element_id(page_index, "t", q, text), quad=q, z=z,
        baseline=(Point(x=pen[0], y=fit.by), Point(x=right, y=fit.by)), runs=runs,
        text_confidence=float(ol.score),
        char_candidates=[[(cd.text, float(cd.prob)) for cd in ch.candidates] for ch in ol.chars[first:last + 1]],
        notes=[f"原為外框字，已轉回文字，字型為推論（{face.family} {face.style}，與外框的幾何差 {fit.err:.3f} em）。"],
    )


# ====================================================================== s3.embedded：內嵌圖片裡的文字與平面圖形


@dataclass
class _Decomposed:
    """一張內嵌圖片拆開的結果（已換算成頁面座標；z 是在這張圖片內部的先後）。"""

    parent: object  # 原本的 ImageElement
    texts: list = field(default_factory=list)
    images: list = field(default_factory=list)
    shapes: list = field(default_factory=list)
    tables: list = field(default_factory=list)
    vectors: list = field(default_factory=list)
    cleaned: object = None  # 清掉文字與圖形後的圖片（ImageElement）、純色矩形（ShapeElement）或 None（不留）
    page_color: Optional[Color] = None  # 整頁的圖片拆完只剩純色：這個顏色改當頁面底色
    notes: list[str] = field(default_factory=list)
    dropped_texts: int = 0  # 與原生文字重複而拿掉的行數
    outcome: str = "cleaned"  # 原圖片的去向："cleaned"（換成清理後的版本）、"rect"（純色矩形）、"page_color"（改當頁面底色）、"dropped"（不留）


def _flatness(rgb: np.ndarray) -> tuple[float, int]:
    """平塗的程度：（與上下左右斜角的鄰點都幾乎同色的像素比例、佔 0.1% 以上面積的顏色數）。
    截圖、圖表、文件掃描大多是平塗色塊（比例高、顏色少）；照片到處都有細微變化（比例低、顏色多）。"""
    h, w = rgb.shape[:2]
    k = max(1, int(math.ceil(max(h, w) / 512)))
    a = rgb[::k, ::k].astype(np.int16)
    if min(a.shape[:2]) < 3:
        return 1.0, 1
    d = np.zeros(a.shape[:2], dtype=np.int16)
    for dy, dx in ((0, 1), (1, 0), (1, 1), (1, -1)):
        d = np.maximum(d, np.abs(a - np.roll(np.roll(a, dy, 0), dx, 1)).max(axis=2))
    flat = float((d[1:-1, 1:-1] <= 6).mean())
    q = (a[..., 0] // 16) * 256 + (a[..., 1] // 16) * 16 + a[..., 2] // 16
    counts = np.bincount(q.ravel(), minlength=4096)
    return flat, int((counts > 0.001 * q.size).sum())


def _scaled(m, k: float):
    """帶信心值的長度欄位乘上比例（值與誤差範圍一起）。"""
    return m.model_copy(update={"value": m.value * k, "error": (m.error * k) if m.error is not None else None})


class _Mapper:
    """圖片自己的相對座標（以工作影像寬為 1，y 也以寬為單位）→ 頁面相對座標，依圖片在頁面上的四角換算（仿射）：
    頁面上的點 = 原點 + x × EX + y × EY。圖片可能被非等比縮放、旋轉或翻轉：
    字級（由字高量出）乘 sy，沿基線的距離（每個字的起點、字距）乘 sx，線寬這類不分方向的長度乘兩者的幾何平均 k。"""

    def __init__(self, origin: tuple[float, float], EX: tuple[float, float], EY: tuple[float, float]):
        self.o, self.EX, self.EY = origin, EX, EY
        self.sx = math.hypot(*EX)
        self.sy = math.hypot(*EY)
        self.k = math.sqrt(self.sx * self.sy)
        self.angle = math.degrees(math.atan2(EX[1], EX[0]))  # 圖片的 x 軸在頁面上的角度（順時針為正）
        self.axis_aligned = abs(EX[1]) <= 1e-3 * self.sx and abs(EY[0]) <= 1e-3 * self.sy and EX[0] > 0 and EY[1] > 0

    @classmethod
    def from_box(cls, quad: Quad, size: tuple[int, int]) -> "_Mapper":
        """圖片沒有旋轉：工作影像整張對到頁面上的外框。"""
        x0, y0, x1, y1 = quad.bbox
        W, H = size
        return cls((x0, y0), (x1 - x0, 0.0), (0.0, (y1 - y0) * W / H))

    @property
    def x0(self) -> float:
        return self.o[0]

    @property
    def y0(self) -> float:
        return self.o[1]

    def xy(self, x: float, y: float) -> tuple[float, float]:
        return self.o[0] + x * self.EX[0] + y * self.EY[0], self.o[1] + x * self.EX[1] + y * self.EY[1]

    def pt(self, p: Point) -> Point:
        x, y = self.xy(p.x, p.y)
        return Point(x=x, y=y)

    def quad(self, q: Quad) -> Quad:
        return Quad(tl=self.pt(q.tl), tr=self.pt(q.tr), br=self.pt(q.br), bl=self.pt(q.bl))

    def path(self, path: list[list[float]]) -> list[list[float]]:
        out = []
        for sg in path:
            if sg[0] == 3:
                out.append([3])
                continue
            vals = [sg[0]]
            for i in range(1, len(sg), 2):
                vals += list(self.xy(sg[i], sg[i + 1]))
            out.append(vals)
        return out


def _map_vector(v, m: _Mapper, new_id: str, image_id: str, quad: Quad, z: int):
    paths = []
    for vp in v.paths:
        upd: dict = {"path": m.path(vp.path)}
        if vp.stroke_width is not None:
            upd["stroke_width"] = vp.stroke_width * m.k
        if vp.fit_error is not None:
            upd["fit_error"] = vp.fit_error * m.k
        if vp.gradient is not None:
            g = vp.gradient
            if g.kind == "linear":
                a, b = m.pt(Point(x=g.coords[0], y=g.coords[1])), m.pt(Point(x=g.coords[2], y=g.coords[3]))
                coords = [a.x, a.y, b.x, b.y]
            else:
                ctr = m.pt(Point(x=g.coords[0], y=g.coords[1]))
                coords = [ctr.x, ctr.y, g.coords[2] * m.k]
            upd["gradient"] = g.model_copy(update={"coords": coords})
        if vp.primitive is not None:
            pr = vp.primitive
            ctr = m.pt(Point(x=pr.cx, y=pr.cy))
            turned = abs(math.sin(math.radians(pr.angle_deg))) > 0.5  # 轉了接近 90 度：寬沿著圖片的垂直方向
            kw, kh = (m.sy, m.sx) if turned else (m.sx, m.sy)
            upd["primitive"] = pr.model_copy(update={
                "cx": ctr.x, "cy": ctr.y, "w": pr.w * kw, "h": pr.h * kh, "corner_radius": pr.corner_radius * m.k, "angle_deg": pr.angle_deg + m.angle,
                "points": [list(m.xy(x, y)) for x, y in pr.points],
            })
        paths.append(vp.model_copy(update=upd))
    return v.model_copy(update={"id": new_id, "image_id": image_id, "quad": quad, "z": z, "paths": paths,
                                "notes": list(v.notes) + ["由嵌入圖片描圖而來（量測，不是精確擷取）。"]})


def _overlap(a: tuple[float, float, float, float], b: tuple[float, float, float, float]) -> float:
    """交集面積 ÷ 較小者的面積。"""
    w = min(a[2], b[2]) - max(a[0], b[0])
    h = min(a[3], b[3]) - max(a[1], b[1])
    if w <= 0 or h <= 0:
        return 0.0
    small = min((a[2] - a[0]) * (a[3] - a[1]), (b[2] - b[0]) * (b[3] - b[1]))
    return w * h / max(small, 1e-12)


def _similar(a: str, b: str) -> float:
    from difflib import SequenceMatcher

    a, b = "".join(a.split()), "".join(b.split())
    if not a or not b:
        return 0.0
    return SequenceMatcher(None, a, b).ratio()


def embedded_images(elements, candidate_ids: set[str], spec: PageSpec, cfg, store: Store, ai=None, reader: Optional[pdf.PageReader] = None):
    """內嵌圖片裡的文字與平面圖形 → 拆成可以編輯的元素，併入這一頁的元素清單（回傳新的 ElementList）。

    對擷取產線讀出的每張圖片（candidate_ids；不含向量圖對應的圖片），先用便宜的量測篩掉照片（平塗比例），
    再以這張圖片為工作影像跑一次影像產線（文字辨識、文字屬性、區域、描圖、底圖），結果依圖片的四角換算回頁面座標：
      有夠可信的文字、或夠多的平塗圖形 → 採用：文字行、形狀、向量圖與它們的圖片、表格區都併進來，
        原圖片換成「清掉文字與圖形」之後的版本（拆完只剩純色時改成一個純色矩形，或與底下的頁面同色時就不留）；
      否則（照片、單一圖示）→ 原圖片照舊。
    和原生文字重疊而且內容相同的辨識結果不要（原生的才是精確的）。任何一張圖片失敗都不影響整頁：退回原圖片並記在備註。
    這些元素都是量測值（影像產線照實標示來源），不是精確擷取。"""
    c = cfg.section("recognize")
    if not c.get("pdf_embedded_images", True) or not candidate_ids:
        return elements
    notes: list[str] = []
    page_area = spec.aspect_ratio
    min_px = int(c.get("pdf_embedded_min_pixels", 48))
    min_area = float(c.get("pdf_embedded_min_area", 0.003))
    cands = []
    for im in elements.images:
        if im.id not in candidate_ids or im.kind.value == ImageKind.SHADOW:
            continue
        x0, y0, x1, y1 = im.quad.bbox
        if min(im.pixel_size) < min_px or (x1 - x0) * (y1 - y0) / page_area < min_area:
            continue
        cands.append(im)
    if not cands:
        return elements
    cands.sort(key=lambda im: -(im.quad.width * im.quad.height))
    limit = int(c.get("pdf_embedded_max_per_page", 8))
    if len(cands) > limit:
        notes.append(f"這一頁有 {len(cands)} 張圖片，只檢查最大的 {limit} 張裡有沒有可以拆開的文字與圖形（上限見設定 pdf_embedded_max_per_page）。")
        cands = cands[:limit]
    budget = float(c.get("pdf_embedded_page_pixels", 24_000_000))
    placements: dict[int, tuple] = {}  # 繪製順序 →（四角的 PDF 座標、原始像素大小）：旋轉、翻轉、非等比縮放的圖片要用
    if reader is not None:
        try:
            placements = {ri.z: (ri.corners, ri.pixel_size, reader, ri) for ri in reader.images()}
        except Exception:  # 讀不到四角：只處理沒有旋轉的圖片（以外框換算）
            placements = {}
    results: dict[str, _Decomposed] = {}
    for im in cands:
        try:
            res, used = _decompose(im, elements, spec, cfg, c, store, ai, budget, placements.get(im.z))
        except Exception as e:  # 單張圖片失敗：保留原圖片，不讓整頁失敗
            notes.append(f"有一張嵌入圖片（{im.id}）拆解時出錯（{type(e).__name__}: {e}），保留原圖片。")
            continue
        budget -= used
        if res is not None:
            results[im.id] = res
    if not results:
        return elements.model_copy(update={"notes": list(elements.notes) + notes}) if notes else elements
    return _assemble(elements, results, spec, notes)


def _placement(placement, spec: PageSpec):
    """圖片在頁面上的擺法：（左上角、原圖每個像素往右對到的頁面向量、往下對到的頁面向量、原圖像素大小）。沒有資料回傳 None。"""
    if placement is None:
        return None
    from ..tools.geometry import to_point

    corners, (wn, hn) = placement[:2]
    if wn <= 0 or hn <= 0:
        return None
    tl, tr, _, bl = [to_point(spec.source_to_page, x, y) for x, y in corners]
    ex = ((tr.x - tl.x) / wn, (tr.y - tl.y) / wn)
    ey = ((bl.x - tl.x) / hn, (bl.y - tl.y) / hn)
    return (tl.x, tl.y), ex, ey, (wn, hn)


def _decompose(im, elements, spec: PageSpec, cfg, c: dict, store: Store, ai, budget: float, placement=None):
    """拆一張圖片。回傳（結果或 None（不是含文字或平面圖形的圖）、用掉的工作影像像素數）。"""
    from PIL import Image

    from . import s3_image_line

    page = spec.page_index
    with Image.open(store.blob_path(im.image)) as raw_img:
        src = raw_img.convert("RGBA")
    rgba = np.asarray(src)
    alpha = rgba[..., 3]
    if not alpha.any():
        return None, 0
    under = elements.background.color or Color(r=255, g=255, b=255)
    base = np.array([under.r, under.g, under.b], dtype=np.float32)
    af = alpha[..., None].astype(np.float32) / 255.0
    rgb = (rgba[..., :3].astype(np.float32) * af + base * (1 - af) + 0.5).astype(np.uint8)
    opaque = int(alpha.min()) >= 250
    visible = rgba[..., :3][alpha > 0]
    if visible.size and int(np.ptp(visible, axis=0).max()) <= 2:
        return None, 0  # 只有一種顏色、靠透明度成形（陰影、光暈）：沒有可拆的內容
    flat, _ = _flatness(rgb)
    if flat < float(c.get("pdf_embedded_min_flat", 0.5)):
        return None, 0  # 照片：不拆
    # 工作影像：就是這張圖片自己的像素，依圖片的四角把它轉回原本的方向與長寬比（旋轉、翻轉、非等比縮放的圖片），
    # 只在解析度太低或太高時等比放大、縮小，讓它落在設定的範圍內
    import cv2

    x0, y0, x1, y1 = im.quad.bbox
    qw, qh = x1 - x0, y1 - y0
    h, w = rgb.shape[:2]
    pt_w = spec.page_width_pt.value
    place = _placement(placement, spec)
    general = False
    if place is not None:
        origin, ex, ey, (wn, hn) = place
        sx_n, sy_n = math.hypot(*ex), math.hypot(*ey)
        simple = abs(ex[1]) <= 2e-3 * sx_n and abs(ey[0]) <= 2e-3 * sy_n and ex[0] > 0 and ey[1] > 0 and abs(sx_n / sy_n - 1) <= 0.02
        general = not simple and sx_n > 0 and sy_n > 0
    if general:
        dpi = 1.0 / min(sx_n, sy_n) / (pt_w / 72.0)
        nat = (wn, hn)
    else:
        dpi = max(w / qw, h / qh) / (pt_w / 72.0)
        nat = (w, h)
    lo, hi = float(c.get("pdf_embedded_min_dpi", 150)), float(c.get("pdf_embedded_max_dpi", 300))
    target = min(max(dpi, min(lo, dpi * float(c.get("pdf_embedded_max_upscale", 3.0)))), hi)
    f = target / dpi
    cap = min(float(c.get("pdf_embedded_max_pixels", 8_000_000)), budget)
    if cap <= 0:
        return None, 0
    if nat[0] * nat[1] * f * f > cap:
        f = math.sqrt(cap / (nat[0] * nat[1]))
    Wp, Hp = max(int(round(nat[0] * f)), 1), max(int(round(nat[1] * f)), 1)
    if general:
        # 工作影像的像素（連續座標）→ 頁面 → 擷取出來那張圖片（已轉到頁面方向）的像素
        kx, ky = nat[0] / Wp, nat[1] / Hp
        exw, eyw = (ex[0] * kx, ex[1] * kx), (ey[0] * ky, ey[1] * ky)
        rx, ry = w / qw, h / qh
        A = np.array([[exw[0] * rx, eyw[0] * rx, (origin[0] - x0) * rx], [exw[1] * ry, eyw[1] * ry, (origin[1] - y0) * ry]])
        A[:, 2] += A[:, :2] @ np.array([0.5, 0.5]) - 0.5  # 像素中心的慣例（cv2 以整數座標為像素中心）
        m = _Mapper(origin, (exw[0] * Wp, exw[1] * Wp), (eyw[0] * Wp, eyw[1] * Wp))
        nat_rgba = cv2.warpAffine(np.ascontiguousarray(rgba), A, (Wp, Hp), flags=cv2.INTER_LINEAR | cv2.WARP_INVERSE_MAP, borderMode=cv2.BORDER_CONSTANT, borderValue=(0, 0, 0, 0))
        # 顏色盡量用影像原本的像素（不經過「轉到頁面方向再轉回來」兩次重新取樣）；透明度用轉回來的
        natural = placement[2].image_natural_bitmap(placement[3]) if len(placement) >= 4 else None
        if natural is not None and natural.size == (wn, hn):
            nat_rgb = np.asarray(natural.convert("RGB").resize((Wp, Hp), Image.LANCZOS) if (Wp, Hp) != (wn, hn) else natural.convert("RGB"))
            nat_rgba = np.dstack([nat_rgb, nat_rgba[..., 3]])
        naf = nat_rgba[..., 3:4].astype(np.float32) / 255.0
        work_arr = (nat_rgba[..., :3].astype(np.float32) * naf + base * (1 - naf) + 0.5).astype(np.uint8)
        work = Image.fromarray(work_arr)

        def to_page_grid(arr: np.ndarray, nearest: bool = False) -> np.ndarray:
            if nearest:
                return cv2.warpAffine(np.ascontiguousarray(arr), A, (w, h), flags=cv2.INTER_NEAREST, borderMode=cv2.BORDER_CONSTANT, borderValue=0)
            return cv2.warpAffine(np.ascontiguousarray(arr), A, (w, h), flags=cv2.INTER_LINEAR, borderMode=cv2.BORDER_REPLICATE)
    else:
        m = _Mapper.from_box(im.quad, (Wp, Hp))
        work = Image.fromarray(rgb)
        if (Wp, Hp) != (w, h):
            work = work.resize((Wp, Hp), Image.LANCZOS)
        work_arr = np.asarray(work)

        def to_page_grid(arr: np.ndarray, nearest: bool = False) -> np.ndarray:
            if arr.shape[:2] == (h, w):
                return arr
            return np.asarray(Image.fromarray(arr).resize((w, h), Image.NEAREST if nearest else Image.LANCZOS))
    from io import BytesIO

    buf = BytesIO()
    work.save(buf, "PNG")
    blob = store.put_blob(buf.getvalue(), "image/png", "png")
    sub_spec = PageSpec(
        page_index=page, source_corners=[Point(x=0, y=0), Point(x=Wp, y=0), Point(x=Wp, y=Hp), Point(x=0, y=Hp)],
        aspect_ratio=Hp / Wp, source_to_page=[1.0 / Wp, 0.0, 0.0, 0.0, 1.0 / Wp, 0.0, 0.0, 0.0, 1.0], working_image=blob, working_image_size=(Wp, Hp),
        page_width_pt=measured(qw * pt_w, 0.9, Provenance.MEASURED, EMBEDDED_STEP), effective_resolution=Wp / (qw * pt_w), quad_method="full_image",
        notes=[f"嵌入圖片 {im.id} 的工作影像"],
    )
    sub = s3_image_line.run(sub_spec, cfg, store, ai=ai)
    used = Wp * Hp
    # 採用與否：夠可信的文字（至少兩個字母、數字或漢字），或夠多的平塗圖形
    thr = float(c.get("pdf_embedded_min_text_confidence", 0.8))
    real = [t for t in sub.texts if (t.text_confidence or 0.0) >= thr and sum(ch.isalnum() for ch in t.text) >= 2]
    graphics = len(sub.shapes) + len(sub.table_regions) + len(sub.vectors) + sum(1 for i in sub.images if i.kind.value == ImageKind.ILLUSTRATION)
    if len(real) < int(c.get("pdf_embedded_min_text_lines", 1)) and graphics < int(c.get("pdf_embedded_min_graphics", 3)):
        return None, used
    res = _Decomposed(parent=im)
    native = [t for t in elements.texts if not t.hidden]
    for t in sub.texts:
        q = m.quad(t.quad)
        if any(_overlap(q.bbox, n.quad.bbox) > 0.5 and _similar(t.text, n.text) >= 0.5 for n in native):
            res.dropped_texts += 1
            continue
        runs = [r.model_copy(update={"quad": m.quad(r.quad), "font_size": _scaled(r.font_size, m.sy), "char_x": [x * m.sx for x in r.char_x],
                                     "char_spacing": (r.char_spacing * m.sx) if r.char_spacing is not None else None}) for r in t.runs]
        res.texts.append(t.model_copy(update={
            "id": make_element_id(page, "t", q, f"{t.text}|{im.id}"), "quad": q, "baseline": (m.pt(t.baseline[0]), m.pt(t.baseline[1])), "runs": runs,
            "notes": list(t.notes) + ["由嵌入圖片辨識而來（量測，不是精確擷取）。"],
        }))
    new_image_id: dict[str, tuple[str, Quad]] = {}
    for i in sub.images:
        q = m.quad(i.quad)
        nid = make_element_id(page, "img", q, f"{i.image.sha256[:12]}|{im.id}")
        new_image_id[i.id] = (nid, q)
        res.images.append(i.model_copy(update={"id": nid, "quad": q}))
    for v in sub.vectors:
        if v.image_id not in new_image_id:
            continue
        nid, q = new_image_id[v.image_id]
        res.vectors.append(_map_vector(v, m, make_element_id(page, "vec", q, f"{v.id}|{im.id}"), nid, q, v.z))
    for s in sub.shapes:
        q = m.quad(s.quad)
        res.shapes.append(s.model_copy(update={
            "id": make_element_id(page, "shp", q, f"{s.id}|{im.id}"), "quad": q, "path": m.path(s.path),
            "stroke_width": (s.stroke_width * m.k) if s.stroke_width is not None else None,
            "corner_radius": (s.corner_radius * m.k) if s.corner_radius is not None else None,
            "fit_error": (s.fit_error * m.k) if s.fit_error is not None else None,
        }))
    if sub.table_regions and not m.axis_aligned:
        res.notes.append(f"嵌入圖片（{im.id}）是旋轉或翻轉放置的，裡面找到的 {len(sub.table_regions)} 個表格區沒有轉換（表格格線只支援水平與垂直）。")
    for tb in (sub.table_regions if m.axis_aligned else []):
        q = m.quad(tb.quad)
        res.tables.append(tb.model_copy(update={
            "id": make_element_id(page, "tbl", q, f"{tb.id}|{im.id}"), "quad": q,
            "h_lines": [m.y0 + y * m.sy for y in tb.h_lines], "v_lines": [m.x0 + x * m.sx for x in tb.v_lines],
            "segments": [(m.x0 + a * m.sx, m.y0 + b * m.sy, m.x0 + cc * m.sx, m.y0 + dd * m.sy) for a, b, cc, dd in tb.segments],
            "line_width": (tb.line_width * m.k) if tb.line_width is not None else None,
            "cell_fills": [(m.quad(cq), col) for cq, col in tb.cell_fills],
        }))
    res.notes = [f"嵌入圖片（{im.id}）：{n}" for n in sub.notes] + res.notes
    ratio = m.sx / m.sy
    if abs(ratio - 1) > 0.02:
        res.notes.append(f"嵌入圖片（{im.id}）在頁面上被非等比縮放（寬高比例是原圖的 {ratio:.2f} 倍）：拆出來的文字用一般字形，字寬會和畫面上的不同。")
    # 清掉文字與圖形之後的圖片：影像產線的底圖；拆完只剩純色時，視情況改成頁面底色、純色矩形或不留
    bg = sub.background
    tol = int(c.get("background_blank_tolerance", 6))
    if bg.image is None:
        flat_color = bg.color or Color(r=255, g=255, b=255)
        col = (flat_color.r, flat_color.g, flat_color.b)
        if opaque:
            beneath = elements.background.image is not None or any(
                o.z < im.z and _overlap(o.quad.bbox, im.quad.bbox) > 0
                for o in list(elements.texts) + list(elements.shapes) + [i for i in elements.images if i.id != im.id] + list(elements.vectors))
            full = qw * qh / spec.aspect_ratio >= float(c.get("full_page_coverage", 0.98))
            same = max(abs(a - b) for a, b in zip(col, (under.r, under.g, under.b))) <= tol
            if full and not beneath:
                res.page_color, res.outcome = Color(r=col[0], g=col[1], b=col[2]), "page_color"
            elif same and not beneath:
                res.cleaned, res.outcome = None, "dropped"
            else:
                res.outcome = "rect"
                res.cleaned = ShapeElement(
                    id=make_element_id(page, "shp", im.quad, f"rect:{flat_color.hex}|{im.id}"), quad=im.quad, z=im.z, geometry=ShapeGeometry.RECT,
                    fill=Color(r=col[0], g=col[1], b=col[2]), is_page_background=full,
                )
            return res, used
        clean = np.empty_like(work_arr)
        clean[...] = col
    else:
        with Image.open(store.blob_path(bg.image)) as bimg:
            clean = np.asarray(bimg.convert("RGB"))
        if bg.generated_regions:
            res.notes.append(f"嵌入圖片（{im.id}）：清理後的圖片有 {len(bg.generated_regions)} 個區域是修補（生成）出來的，原本被文字或圖形蓋住。")
    # 只換掉被清掉的地方，其餘直接用原圖的像素（不經過重新取樣）；工作影像縮放過時，遮罩與底圖換回原圖的大小
    changed = np.abs(clean.astype(np.int16) - work_arr.astype(np.int16)).max(axis=2) > 2
    if changed.any():
        import cv2

        changed = cv2.dilate(changed.astype(np.uint8), np.ones((3, 3), np.uint8)) > 0
    if general or clean.shape[:2] != (h, w):
        changed = to_page_grid(changed.astype(np.uint8) * 255, nearest=True) > 0
        clean = to_page_grid(clean)
    out_rgb = rgba[..., :3].copy()
    out_a = alpha.copy()
    out_rgb[changed] = clean[changed]
    if not opaque:
        # 原圖有透明的地方：分析時墊的是頁面底色，清掉之後補出來的若就是那個底色，表示那裡原本是透明的
        empty = changed & (np.abs(clean.astype(np.int16) - base.astype(np.int16)).max(axis=2) <= tol)
        out_a[empty] = 0
        out_a[changed & ~empty] = 255
        if not out_a.any():
            res.cleaned, res.outcome = None, "dropped"
            return res, used
    out = np.dstack([out_rgb, out_a])
    buf = BytesIO()
    Image.fromarray(np.ascontiguousarray(out), "RGBA").save(buf, "PNG")
    cblob = store.put_blob(buf.getvalue(), "image/png", "png")
    res.cleaned = ImageElement(id=make_element_id(page, "img", im.quad, f"{cblob.sha256[:12]}|clean"), quad=im.quad, z=im.z, image=cblob, pixel_size=(w, h), kind=im.kind)
    return res, used


def _assemble(elements, results: dict[str, _Decomposed], spec: PageSpec, notes: list[str]):
    """把拆開的結果併回這一頁。z 只表示先後：整頁的 z 乘上 K，拆出來的元素插在原圖片的位置（原圖片的 z × K 之後）。"""
    K = max(len(r.texts) + len(r.images) + len(r.shapes) + len(r.tables) + 2 for r in results.values())

    def zz(z: int) -> int:
        return z * K

    texts = [t.model_copy(update={"z": zz(t.z)}) for t in elements.texts]
    images = []
    shapes = [s.model_copy(update={"z": zz(s.z)}) for s in elements.shapes]
    tables = [t.model_copy(update={"z": zz(t.z)}) for t in elements.table_regions]
    vectors = [v.model_copy(update={"z": zz(v.z)}) for v in elements.vectors]
    background = elements.background
    counts = {"t": 0, "s": 0, "v": 0, "i": 0, "tb": 0}
    for im in elements.images:
        res = results.get(im.id)
        if res is None:
            images.append(im.model_copy(update={"z": zz(im.z)}))
            continue
        base = zz(im.z)
        if res.cleaned is not None:
            cleaned = res.cleaned.model_copy(update={"z": base})
            (images if hasattr(cleaned, "image") else shapes).append(cleaned)
        if res.page_color is not None:
            background = background.model_copy(update={"color": res.page_color, "image": None})
        # 圖片內部的先後：依影像產線給的 z，同一層時圖片在下、形狀與表格次之、文字在最上面
        items = [(i.z, 0, "i", i) for i in res.images] + [(s.z, 1, "s", s) for s in res.shapes] + [(t.z, 2, "tb", t) for t in res.tables] + [(t.z, 3, "t", t) for t in res.texts]
        items.sort(key=lambda q: (q[0], q[1]))
        zmap: dict[str, int] = {}
        for rank, (_, _, kind, el) in enumerate(items, start=1):
            new = el.model_copy(update={"z": base + rank})
            zmap[el.id] = base + rank
            {"i": images, "s": shapes, "tb": tables, "t": texts}[kind].append(new)
            counts[kind] += 1
        for v in res.vectors:
            vectors.append(v.model_copy(update={"z": zmap.get(v.image_id, base + 1)}))
            counts["v"] += 1
        notes += res.notes
        if res.dropped_texts:
            notes.append(f"嵌入圖片（{im.id}）裡有 {res.dropped_texts} 行字和頁面上的原生文字重疊而且內容相同，以原生文字為準。")
    outcome = Counter(r.outcome for r in results.values())
    said = {"cleaned": "換成清掉這些內容之後的版本", "rect": "只剩純色，換成一個純色矩形", "page_color": "只剩純色，改當頁面底色", "dropped": "只剩與頁面相同的底色，不再保留"}
    parts = "、".join(f"{n} 張{said[k]}" for k, n in outcome.items())
    notes.insert(0, f"這一頁有 {len(results)} 張嵌入圖片含有文字或平面圖形，已拆成 {counts['t']} 行文字、{counts['s']} 個形狀、{counts['v']} 個向量圖、"
                    f"{counts['i']} 張圖片與 {counts['tb']} 個表格區（由影像辨識量測而來，不是精確擷取）。原圖片：{parts}。")
    # 編號不可重複：真的撞到時（極少見）在內容鍵後面加序號重新產生；圖片改了編號，對應的向量圖跟著改
    seen: set[str] = set()
    renamed: dict[str, str] = {}

    def unique(items: list) -> list:
        out = []
        for el in items:
            if el.id in seen:
                kind = el.id.split("-")[1]
                n = 2
                while True:
                    nid = make_element_id(spec.page_index, kind, el.quad, f"{el.id}#{n}")
                    if nid not in seen:
                        break
                    n += 1
                renamed[el.id] = nid
                el = el.model_copy(update={"id": nid})
            seen.add(el.id)
            out.append(el)
        return out

    texts, shapes, tables, images = unique(texts), unique(shapes), unique(tables), unique(images)
    vectors = unique([v.model_copy(update={"image_id": renamed[v.image_id]}) if v.image_id in renamed else v for v in vectors])
    return elements.model_copy(update={
        "texts": texts, "images": images, "shapes": shapes, "table_regions": tables, "vectors": vectors, "background": background,
        "notes": list(elements.notes) + notes,
    })
