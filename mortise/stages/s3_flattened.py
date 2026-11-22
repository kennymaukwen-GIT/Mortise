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
from dataclasses import dataclass, field
from typing import Optional

import numpy as np

from ..artifacts import (
    Color, FontCandidate, PageSpec, Point, Provenance, Quad, TextLine, TextRun, exact, make_element_id, measured,
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
