"""③ 辨識：收頁面規格，交出元素清單 ElementList。只記看得到的事實，不吸附、不定案。

兩條產線，交出同一種元素清單：
  擷取產線（原生 PDF）：直接讀檔案
    s3.pdf_text    讀逐字資料，合併成文字行與樣式段
    s3.pdf_images  讀內嵌圖片
    s3.pdf_paths   讀所有向量路徑與漸層物件，判斷每一個讀不讀得出來（花紋、被裁切、混合模式等讀不出來的留在底圖）
    s3.pdf_vectors 把屬於同一個圖形的路徑分群，每群交出一個向量圖（VectorGraphic）與一張對應的透明底圖片；
                   疑似文字轉外框的路徑單獨成群（kind 為 outlined_text）
    s3.pdf_shapes  沒有成群的路徑：認出矩形、圓角矩形、橢圓、直線；其他簡單的保留成自訂路徑
    s3.tables      由框線找表格區（區域與格線位置；列欄結構在④）
    s3.background  隱藏已擷取的物件後重繪，得到底圖（不是生成）
  影像產線（掃描、截圖、照片）：見 s3_image_line.py（文字辨識、文字屬性、區域、裁圖、底圖修補）。
    失敗時退回 s3.image_stub：整頁當成一張圖片交出，並在備註中說明原因。
"""

from __future__ import annotations

import io
import math
from collections import Counter
from dataclasses import dataclass, field
from typing import Optional

import numpy as np
from PIL import Image

from ..artifacts import (
    Background, Color, Correction, ElementList, FontCandidate, Gradient, GradientStop, ImageElement, ImageKind, InputType, PageSpec, Point,
    TableRegion, ProductionLine, Provenance, Quad, ShapeElement, ShapeGeometry, SourceDoc, TextLine, TextRun, VectorGraphic, VectorPath,
    VectorPrimitive, exact, make_element_id, measured,
)
from ..config import Config
from ..pipeline.store import Store
from ..tools import pdfium as pdf
from ..tools.fonts import clean_font_name, is_cjk, split_style
from ..tools.geometry import to_point
from . import s3_flattened

NAME = "s3"
VERSION = "3"

SEG_LINETO, SEG_BEZIERTO, SEG_MOVETO = 0, 1, 2


def _png(im: Image.Image) -> bytes:
    buf = io.BytesIO()
    im.save(buf, "PNG")
    return buf.getvalue()


def run(doc: SourceDoc, spec: PageSpec, cfg: Config, corrections: list[Correction], store: Store, ai=None) -> ElementList:
    ref = doc.pages[spec.page_index]
    kind = ref.input_type.value
    if doc.media_type == "application/pdf" and kind in (InputType.NATIVE_PDF, InputType.MIXED_PDF):
        elements = _extract_line(doc, spec, cfg, store)
    elif cfg.section("recognize").get("image_line", True):
        try:
            from . import s3_image_line

            elements = s3_image_line.run(spec, cfg, store, ai=ai)
        except Exception as e:  # 影像產線失敗：整頁以圖片輸出，並照實說明
            elements = _image_line(spec)
            elements = elements.model_copy(update={"notes": elements.notes + [f"影像辨識失敗（{type(e).__name__}: {e}），這一頁以整張圖片輸出。"]})
    else:
        elements = _image_line(spec)
    return _apply_corrections(elements, corrections)


# ====================================================================== 影像產線（尚未實作）


def _image_line(spec: PageSpec) -> ElementList:
    page = Quad.from_rect(0.0, 0.0, 1.0, spec.aspect_ratio)
    image = ImageElement(
        id=make_element_id(spec.page_index, "img", page, spec.working_image.sha256[:12]), quad=page, z=0,
        image=spec.working_image, pixel_size=spec.working_image_size,
        kind=measured(ImageKind.FULL_PAGE, 1.0, Provenance.INFERRED, f"{NAME}.image_stub@{VERSION}"),
    )
    return ElementList(
        page_index=spec.page_index, production_line=ProductionLine.IMAGE, images=[image],
        notes=["這一頁整頁以圖片輸出，文字不可編輯。"],
    )


# ====================================================================== 擷取產線（原生 PDF）


def _extract_line(doc: SourceDoc, spec: PageSpec, cfg: Config, store: Store) -> ElementList:
    c = cfg.section("recognize")
    m = spec.source_to_page
    src_w = math.hypot(spec.source_corners[1].x - spec.source_corners[0].x, spec.source_corners[1].y - spec.source_corners[0].y)
    notes: list[str] = []
    flags = doc.pages[spec.page_index].fake_native
    source_path = str(store.blob_path(doc.source))
    pdoc = pdf.open_document(source_path)
    reader = pdf.PageReader(pdoc, spec.page_index, source_path=source_path)
    try:
        texts = read_text_lines(reader, spec.page_index, m, src_w, c)
        images, image_z = _images(reader, spec, m, c, store, notes)
        # 向量路徑：先全部讀出來並判斷讀不讀得出來，再分群成向量圖，剩下的照舊轉成形狀
        paths = _read_paths(reader, spec, m, src_w, c)
        page_color, _ = _page_color(paths, spec, c)
        _mark_covered(paths)
        groups = _group_paths(paths, reader, m, c, bool(flags and flags.outlined_text_suspected))
        # 文字轉成的外框：辨識過關的變回文字行（那些路徑不再畫成向量圖，但仍從底圖隱藏），其餘照舊是向量圖
        outlined_texts, groups, outlined_z = s3_flattened.outlined_to_text(groups, reader, spec, src_w, c, store, notes)
        texts = texts + outlined_texts
        vectors, vector_images, vector_z = _vector_graphics(groups, reader, spec, src_w, c, store, notes)
        shapes, shape_z = _shapes(paths, spec, m, src_w, notes)
        _mark_shadows(images, shapes)
        # 底色物件不隱藏：底圖要帶著底色，否則隱藏後會變成白底（深色投影片就會整頁變白）
        background = _background(reader, spec, c, store, image_z | shape_z | vector_z | outlined_z, page_color)
        tables, absorbed = _tables(shapes, [t for t in texts if not t.hidden], spec, c)
        shapes = [sh for sh in shapes if sh.id not in absorbed]
    finally:
        reader.close()
        pdoc.close()
    if any(t.hidden for t in texts):
        notes.append("這一頁有不可見文字（多半是 OCR 文字層），已標記為隱藏，不會輸出。")
    return ElementList(
        page_index=spec.page_index, production_line=ProductionLine.EXTRACT,
        texts=texts, images=images + vector_images, shapes=shapes, table_regions=tables, background=background, notes=notes, vectors=vectors,
    )


# ---------- s3.pdf_text ----------


def _style_key(ch: pdf.RawChar) -> tuple:
    return (ch.font_base, round(ch.size, 3), ch.fill, ch.render_mode in pdf.RENDER_MODES_STROKED, ch.render_mode == pdf.RENDER_MODE_INVISIBLE)


def read_text_lines(reader: pdf.PageReader, page_index: int, m: list[float], src_w: float, c: dict) -> list[TextLine]:
    """從一頁 PDF 讀出文字行（⑦驗證也用它來讀渲染結果）。"""
    chars = reader.chars()
    z_of = reader.text_object_z()
    step = f"{NAME}.pdf_text@{VERSION}"

    # 1. 切成行：遇到換行、方向改變、基線跳動、間距過大就斷開
    lines: list[list[pdf.RawChar]] = []
    cur: list[pdf.RawChar] = []

    def flush() -> None:
        nonlocal cur
        while cur and not cur[-1].text.strip():
            cur.pop()
        if cur:
            lines.append(cur)
        cur = []

    for ch in chars:
        if ch.text in ("\r", "\n", ""):
            flush()
            continue
        if not ch.text.strip():
            if cur:  # 行首的空白不要
                cur.append(ch)
            continue
        if cur:
            prev = next(p for p in reversed(cur) if p.text.strip())
            ux, uy = math.cos(prev.angle), math.sin(prev.angle)
            dx, dy = ch.origin[0] - prev.origin[0], ch.origin[1] - prev.origin[1]
            along, across = dx * ux + dy * uy, -dx * uy + dy * ux
            if abs(uy) < 0.02:
                prev_adv = prev.loose_box[2] - prev.loose_box[0]
            elif abs(ux) < 0.02:
                prev_adv = prev.loose_box[3] - prev.loose_box[1]
            else:
                prev_adv = prev.size * 0.5
            size = max(prev.size, ch.size)
            if (
                abs(ch.angle - prev.angle) > math.radians(1.0)
                or abs(across) > c["baseline_tolerance"] * size
                or along - prev_adv > c["line_gap_factor"] * size
                or along < -0.5 * size
            ):
                flush()
        cur.append(ch)
    flush()
    lines = _rejoin(lines, reader, c)

    out: list[TextLine] = []
    for line in lines:
        # PDFium 會在字距較大處自行補空白；緊鄰中日韓文字的多半是誤補（全形標點的留白），拿掉
        kept: list[pdf.RawChar] = []
        for i, ch in enumerate(line):
            if ch.generated and not ch.text.strip():
                before = next((p.text for p in reversed(line[:i]) if p.text.strip()), "")
                after = next((p.text for p in line[i + 1:] if p.text.strip()), "")
                if (before and is_cjk(before[-1])) or (after and is_cjk(after[0])):
                    continue
            kept.append(ch)
        line = kept
        first = next(ch for ch in line if ch.text.strip())
        ang = first.angle
        ux, uy = math.cos(ang), math.sin(ang)  # 沿基線
        nx, ny = -uy, ux  # 垂直基線（向上）
        ox, oy = first.origin

        def extent(chs: list[pdf.RawChar]) -> tuple[float, float, float, float, list[float]]:
            starts, ends = [], []
            for ch in chs:
                if not ch.text.strip():
                    continue
                s0 = (ch.origin[0] - ox) * ux + (ch.origin[1] - oy) * uy
                l, b, r, t = ch.loose_box
                s1 = max((px - ox) * ux + (py - oy) * uy for px, py in ((l, b), (r, b), (r, t), (l, t)))
                starts.append(s0)
                ends.append(max(s1, s0))
            asc = max(ch.ascent for ch in chs)
            desc = min(ch.descent for ch in chs)
            return min(starts), max(ends), asc, desc, starts

        def quad(s0: float, s1: float, asc: float, desc: float) -> Quad:
            def pt(s: float, h: float) -> Point:
                return to_point(m, ox + ux * s + nx * h, oy + uy * s + ny * h)

            return Quad(tl=pt(s0, asc), tr=pt(s1, asc), br=pt(s1, desc), bl=pt(s0, desc))

        # 2. 行內依樣式切段
        groups: list[list[pdf.RawChar]] = []
        for ch in line:
            if ch.text.strip() and groups and _style_key(ch) != _style_key(next(p for p in groups[-1] if p.text.strip())):
                groups.append([ch])
            elif groups:
                groups[-1].append(ch)
            else:
                groups.append([ch])
        runs: list[TextRun] = []
        for g in groups:
            head = next(ch for ch in g if ch.text.strip())
            s0, s1, asc, desc, starts = extent(g)
            family, name_bold, name_italic = split_style(head.font_base)
            if head.font_family:
                family = head.font_family
            fake_bold = head.render_mode in pdf.RENDER_MODES_STROKED and head.stroke is not None and head.fill is not None and head.stroke[:3] == head.fill[:3]
            is_bold = name_bold or head.font_weight >= 600 or fake_bold
            bold = exact(True, step) if (name_bold or head.font_weight >= 600) else (measured(True, 0.8, Provenance.INFERRED, step) if fake_bold else exact(False, step))
            italic = exact(bool(name_italic or head.font_italic_angle != 0), step)
            fill = head.fill or (0, 0, 0, 255)
            color = Color(r=fill[0], g=fill[1], b=fill[2], a=fill[3] / 255)
            runs.append(
                TextRun(
                    text="".join(ch.text for ch in g), quad=quad(s0, s1, asc, desc),
                    font_size=exact(head.size / src_w, step),
                    font_candidates=[FontCandidate(name=family or "unknown", raw_name=clean_font_name(head.font_base), score=1.0)],
                    bold=bold if is_bold else exact(False, step), italic=italic,
                    color=exact(color, step) if head.fill else measured(color, 0.5, Provenance.INFERRED, step),
                    char_x=[s / src_w for s in starts],
                )
            )
        s0, s1, asc, desc, _ = extent(line)
        q = quad(s0, s1, asc, desc)
        hidden = first.render_mode == pdf.RENDER_MODE_INVISIBLE or (first.fill is not None and first.fill[3] == 0)
        text = "".join(r.text for r in runs)
        out.append(
            TextLine(
                id=make_element_id(page_index, "t", q, text), quad=q, z=z_of.get(first.obj_key, 0),
                baseline=(to_point(m, ox + ux * s0, oy + uy * s0), to_point(m, ox + ux * s1, oy + uy * s1)),
                runs=runs, hidden=hidden,
            )
        )
    return out


def _rejoin(lines: list[list[pdf.RawChar]], reader: pdf.PageReader, c: dict) -> list[list[pdf.RawChar]]:
    """把同一條基線上被切開的片段接回去。任何方向的文字都適用（在各自的文字座標系中判斷）。

    兩種情況：
    1. 間距很小（不到 line_gap_factor 個字寬）：片段是被 PDF 的物件邊界或 PDFium 補的換行切開的，直接接。
    2. 間距較大（左右對齊的段落會把詞間距拉寬）：只有在「不是欄間空白」時才接。欄間空白（或無框線表格的欄距）
       會在上下相鄰的行出現在同一個位置；對齊造成的寬間距每行位置都不同，上下行在這裡有字。
    都不跨越垂直的框線（表格的格子）。
    """
    rules = []  # 垂直框線（PDF 座標）：x、y0、y1
    for p in reader.paths():
        l, b, r, t = p.bounds
        if (r - l) <= 2.0 and (t - b) >= 4.0 and (p.stroke or p.fill):
            rules.append(((l + r) / 2, b, t))
    info = []
    for chs in lines:
        vis = [ch for ch in chs if ch.text.strip()]
        ang = sorted(ch.angle for ch in vis)[len(vis) // 2]
        ux, uy = math.cos(ang), math.sin(ang)
        spans = []
        for ch in vis:
            l, b, r, t = ch.loose_box
            proj = [x * ux + y * uy for x, y in ((l, b), (r, b), (r, t), (l, t))]
            s0 = ch.origin[0] * ux + ch.origin[1] * uy
            spans.append((min(s0, max(proj)), max(proj)))
        size = sorted(ch.size for ch in vis)[len(vis) // 2]
        cross = -vis[0].origin[0] * uy + vis[0].origin[1] * ux
        info.append({"chs": chs, "ang": ang, "cross": cross, "size": size, "x0": min(a for a, _ in spans), "x1": max(b for _, b in spans), "spans": spans,
                     "horizontal": abs(uy) < 0.02, "y": vis[0].origin[1]})
    small_gap = c["line_gap_factor"]
    max_gap = c.get("join_max_gap_factor", 4.0)
    merged = True
    while merged:
        merged = False
        order = sorted(range(len(info)), key=lambda i: (round(math.degrees(info[i]["ang"])), round(info[i]["cross"], 1), info[i]["x0"]))
        for ai, bi in zip(order, order[1:]):
            A, B = info[ai], info[bi]
            if abs(A["ang"] - B["ang"]) > math.radians(1.0):
                continue
            size = max(A["size"], B["size"])
            if abs(A["cross"] - B["cross"]) > c["baseline_tolerance"] * size or abs(A["size"] - B["size"]) > 0.05 * size:
                continue
            gap = B["x0"] - A["x1"]
            if gap < -0.3 * size or gap > max_gap * size:
                continue
            if A["horizontal"] and any(A["x1"] - 0.5 <= x <= B["x0"] + 0.5 and y0 - size <= A["y"] <= y1 + size for x, y0, y1 in rules):
                continue
            if gap > small_gap * size:
                lo, hi = A["x1"] + 0.25 * gap, B["x0"] - 0.25 * gap
                neighbours = [n for k, n in enumerate(info) if k not in (ai, bi) and abs(n["ang"] - A["ang"]) <= math.radians(1.0)
                              and 0.3 * size < abs(n["cross"] - A["cross"]) <= 2.6 * size and n["x0"] < B["x1"] and n["x1"] > A["x0"]]
                if not neighbours:
                    continue
                if not any(any(a < hi and b2 > lo for a, b2 in n["spans"]) for n in neighbours):
                    continue  # 上下行在這裡也都是空白：是欄間空白，不接
            last = next(ch for ch in reversed(A["chs"]) if ch.text.strip())
            first = next(ch for ch in B["chs"] if ch.text.strip())
            joiner = []
            if gap > 0.15 * size and not (is_cjk(last.text) or is_cjk(first.text)):
                joiner = [pdf.RawChar(**{**last.__dict__, "text": " ", "generated": True})]
            A["chs"] = A["chs"] + joiner + B["chs"]
            A["x1"] = max(A["x1"], B["x1"])
            A["spans"] = A["spans"] + B["spans"]
            info.pop(bi)
            merged = True
            break
    return [n["chs"] for n in info]


# ---------- s3.pdf_images ----------


def _images(reader: pdf.PageReader, spec: PageSpec, m: list[float], c: dict, store: Store, notes: list[str]) -> tuple[list[ImageElement], set[int]]:
    step = f"{NAME}.pdf_images@{VERSION}"
    out: list[ImageElement] = []
    taken: set[int] = set()
    page_area = spec.aspect_ratio
    for im in reader.images():
        if min(im.pixel_size) < c["min_image_pixels"]:
            continue
        pts = [to_point(m, x, y) for x, y in im.corners]
        xs, ys = [p.x for p in pts], [p.y for p in pts]
        q = Quad.from_rect(min(xs), min(ys), max(xs), max(ys))  # 旋轉已畫進像素，所以取外接矩形
        if q.width <= 0 or q.height <= 0:
            continue
        try:
            bitmap = reader.image_bitmap(im)
        except Exception:  # 個別圖片讀不出來：留在底圖，不中斷整頁
            notes.append("有一張圖片無法單獨取出，已留在底圖中。")
            continue
        blob = store.put_blob(_png(bitmap), "image/png", "png")
        coverage = (q.width * q.height) / page_area
        kind = ImageKind.FULL_PAGE if coverage >= c["full_page_coverage"] else ImageKind.UNKNOWN
        out.append(
            ImageElement(
                id=make_element_id(spec.page_index, "img", q, blob.sha256[:12]), quad=q, z=im.z, image=blob,
                pixel_size=bitmap.size, kind=measured(kind, 0.9 if kind == ImageKind.FULL_PAGE else 0.5, Provenance.INFERRED, step),
            )
        )
        taken.add(im.z)
    return out, taken


# ---------- s3.pdf_shapes ----------


def _rect_of(path: pdf.RawPath, tol: float) -> tuple[float, float, float, float] | None:
    """路徑若是軸對齊的矩形，回傳（左、下、右、上）。"""
    if any(p[0] == SEG_BEZIERTO for p in path.points) or sum(1 for p in path.points if p[0] == SEG_MOVETO) != 1:
        return None
    pts: list[tuple[float, float]] = []
    for p in path.points:  # 去掉連續重複的點
        if not pts or abs(pts[-1][0] - p[1]) > tol or abs(pts[-1][1] - p[2]) > tol:
            pts.append((p[1], p[2]))
    if len(pts) >= 2 and abs(pts[0][0] - pts[-1][0]) <= tol and abs(pts[0][1] - pts[-1][1]) <= tol:
        pts = pts[:-1]
    if len(pts) > 32:
        return None  # 矩形不會有這麼多點；幾萬點的長路徑不必往下檢查（下面的迴圈對點數是平方時間）
    changed = True
    while changed and len(pts) > 4:  # 去掉落在邊上的中間點（有些軟體從邊的中點起筆）
        changed = False
        for i in range(len(pts)):
            a, b, c = pts[i - 1], pts[i], pts[(i + 1) % len(pts)]
            if (abs(a[0] - b[0]) <= tol and abs(b[0] - c[0]) <= tol) or (abs(a[1] - b[1]) <= tol and abs(b[1] - c[1]) <= tol):
                pts.pop(i)
                changed = True
                break
    if len(pts) != 4:
        return None
    l, b, r, t = path.bounds
    for x, y in pts:
        if min(abs(x - l), abs(x - r)) > tol or min(abs(y - b), abs(y - t)) > tol:
            return None
    for (x0, y0), (x1, y1) in zip(pts, pts[1:] + pts[:1]):
        if abs(x0 - x1) > tol and abs(y0 - y1) > tol:
            return None
    return (l, b, r, t) if (r - l) > tol and (t - b) > tol else None


def _color(rgba) -> Color | None:
    return Color(r=rgba[0], g=rgba[1], b=rgba[2], a=rgba[3] / 255) if rgba and rgba[3] > 0 else None


def _segments(path: pdf.RawPath) -> list[list]:
    """PDFium 的路徑點 → 線段清單：[種類, x, y, ...]（PDF 座標）。種類 0 起筆、1 直線、2 三次貝茲、3 閉合。"""
    out: list[list] = []
    pts = path.points
    i = 0
    while i < len(pts):
        kind, x, y, close = pts[i]
        if kind == SEG_MOVETO:
            out.append([0, x, y])
            i += 1
        elif kind == SEG_LINETO:
            out.append([1, x, y])
            i += 1
        else:  # 三次貝茲：連續三個點
            trio = pts[i:i + 3]
            if len(trio) < 3:
                break
            out.append([2, trio[0][1], trio[0][2], trio[1][1], trio[1][2], trio[2][1], trio[2][2]])
            close = trio[-1][3]
            i += 3
        if close:
            out.append([3])
    return out


def _ellipse_of(segs: list[list], tol: float) -> tuple[float, float, float, float] | None:
    """由貝茲曲線組成（可夾雜極短的直線）、所有端點與曲線中點都落在外接矩形內切橢圓上：是橢圓。
    不同軟體切的段數不同（PowerPoint 4 段、LibreOffice 12 段加短直線），所以不限段數。回傳（左、下、右、上）。"""
    curves = [sg for sg in segs if sg[0] == 2]
    if len(curves) < 4 or sum(1 for sg in segs if sg[0] == 0) != 1:
        return None
    xs = [v for sg in segs if sg[0] in (0, 1, 2) for v in sg[1::2]]
    ys = [v for sg in segs if sg[0] in (0, 1, 2) for v in sg[2::2]]
    l, r, b, t = min(xs), max(xs), min(ys), max(ys)
    rx, ry = (r - l) / 2, (t - b) / 2
    if rx < tol or ry < tol:
        return None
    cx, cy = (l + r) / 2, (t + b) / 2

    def off(x: float, y: float) -> float:
        return abs(((x - cx) / rx) ** 2 + ((y - cy) / ry) ** 2 - 1)

    prev = None
    for sg in segs:
        if sg[0] == 3:
            continue
        end = (sg[-2], sg[-1])
        if off(*end) > 0.03:
            return None
        if sg[0] == 1 and prev is not None and abs(end[0] - prev[0]) + abs(end[1] - prev[1]) > 0.08 * min(rx, ry):
            return None  # 長直線：不是橢圓
        if sg[0] == 2 and prev is not None:
            mx = 0.125 * prev[0] + 0.375 * sg[1] + 0.375 * sg[3] + 0.125 * sg[5]
            my = 0.125 * prev[1] + 0.375 * sg[2] + 0.375 * sg[4] + 0.125 * sg[6]
            if off(mx, my) > 0.03:
                return None
        prev = end
    return l, b, r, t


def _rounded_rect_of(segs: list[list], tol: float) -> tuple[tuple[float, float, float, float], float] | None:
    """四條邊加四個圓角（貝茲）的軸對齊圓角矩形。回傳（（左、下、右、上）、圓角半徑）。"""
    body = [sg for sg in segs if sg[0] in (1, 2)]
    curves = [sg for sg in body if sg[0] == 2]
    if len(curves) != 4 or not (4 <= len(body) <= 9) or sum(1 for sg in segs if sg[0] == 0) != 1:
        return None
    xs = [v for sg in segs if sg[0] in (0, 1, 2) for v in sg[1::2]]
    ys = [v for sg in segs if sg[0] in (0, 1, 2) for v in sg[2::2]]
    l, r, b, t = min(xs), max(xs), min(ys), max(ys)
    radii = []
    prev = (segs[0][1], segs[0][2])
    for sg in body:
        end = (sg[-2], sg[-1])
        if sg[0] == 1:
            if abs(prev[0] - end[0]) > tol and abs(prev[1] - end[1]) > tol:
                return None  # 斜線：不是圓角矩形
        else:
            dx, dy = abs(end[0] - prev[0]), abs(end[1] - prev[1])
            rad = (dx + dy) / 2
            slack = max(0.15 * rad, tol)
            if abs(dx - dy) > slack:
                return None  # 圓角的橫向與縱向半徑不同（橢圓角）：形狀表示不了
            # 圓角的兩端必須一個在上下邊、一個在左右邊；圓心是「上下邊那一端的 x、左右邊那一端的 y」
            on_h = [min(abs(q[1] - b), abs(q[1] - t)) <= tol for q in (prev, end)]
            on_v = [min(abs(q[0] - l), abs(q[0] - r)) <= tol for q in (prev, end)]
            if on_h[0] and on_v[1]:
                cx, cy = prev[0], end[1]
            elif on_v[0] and on_h[1]:
                cx, cy = end[0], prev[1]
            else:
                return None  # 曲線的端點不在外框的邊上：是不規則的圓弧形（或被旋轉過），不是圓角矩形
            mx = 0.125 * prev[0] + 0.375 * sg[1] + 0.375 * sg[3] + 0.125 * end[0]
            my = 0.125 * prev[1] + 0.375 * sg[2] + 0.375 * sg[4] + 0.125 * end[1]
            if abs(math.hypot(mx - cx, my - cy) - rad) > slack:
                return None  # 曲線的中點不在圓弧上
            radii.append(rad)
        prev = end
    rad = sum(radii) / 4
    if rad <= tol or max(radii) - min(radii) > max(0.15 * rad, tol) or rad > min(r - l, t - b) / 2 + tol:
        return None
    return (l, b, r, t), rad


# ---------- s3.pdf_paths ----------

# 留在底圖的原因：代碼 → 給使用者看的說法
_REASONS = {
    "gradient": "讀不出參數的漸層",
    "pattern": "花紋填色",
    "clipped": "被裁切的圖形",
    "blend": "混合模式",
    "soft_mask": "透明遮罩",
    "group": "整組套用的透明效果",
    "unknown": "無法確認填色方式",
    "covered": "被上面留在底圖的物件蓋住",
    "page_gradient": "整頁的漸層底",
}
_BOGUS_FILLS = {(255, 255, 255), (191, 191, 191)}  # 填色其實是花紋或漸層時，PDFium 回報的假顏色
_PRIMITIVES = ("rect", "line", "ellipse", "rrect")


@dataclass
class _Path:
    """一條路徑（或一個漸層物件）在分群與轉換時用到的量測。③內部使用，不是產物。"""

    z: int  # 繪製順序
    raw: pdf.RawPath
    segs: list[list]  # 線段（PDF 座標）：[種類, x, y, ...]；種類 0 起筆、1 直線、2 三次貝茲、3 閉合
    rel: list[list[float]]  # 同樣的線段，頁面相對座標（與 VectorPath.path 同一種編碼）
    box: tuple[float, float, float, float]  # 幾何外框（頁面相對座標：左、上、右、下）
    vbox: tuple[float, float, float, float]  # 看得到的外框，含筆畫寬度（頁面相對座標）
    kind: str  # "rect"、"line"、"ellipse"、"rrect"、"free"
    geom: tuple = ()  # 基本形狀的參數（PDF 座標）：rect 與 ellipse 是（左、下、右、上）；rrect 是（（左、下、右、上）、圓角半徑）
    fill: Optional[Color] = None
    stroke: Optional[Color] = None
    fill_rule: str = "nonzero"
    gradient: Optional[pdf.RawGradient] = None
    reason: Optional[str] = None  # 留在底圖的原因（_REASONS 的代碼）；None 表示讀得出來
    detail: str = ""  # 原因的補充說明（例如漸層為什麼讀不出來）
    role: str = ""  # 去向：""（還沒決定，最後會變成形狀）、"page_color"（頁面底色）、"vector"（向量圖的一部分）
    glyph: bool = False  # 疑似文字轉外框的一部分


def _rel_box(m: list[float], l: float, b: float, r: float, t: float) -> tuple[float, float, float, float]:
    """PDF 座標的外框 → 頁面相對座標的外框（左、上、右、下）；頁面有旋轉時取轉過去之後的外接矩形。"""
    pts = [to_point(m, x, y) for x, y in ((l, b), (r, b), (r, t), (l, t))]
    return min(q.x for q in pts), min(q.y for q in pts), max(q.x for q in pts), max(q.y for q in pts)


def _unreadable(p: pdf.RawPath, fill: Color | None, stroke: Color | None, status: str) -> tuple[str | None, str]:
    """這條路徑有沒有「幾何讀得到、但畫出來的樣子重現不了」的狀況。回傳（原因代碼、補充說明）。"""
    info = p.paint
    if info is None:
        # 沒有掃描結果：頁面沒有花紋之類的內容時不必擔心；掃描失敗時，只有回報成假顏色或帶透明效果的才可疑
        if status == pdf.PAINT_FAILED and (p.transparent or any(col is not None and (col.r, col.g, col.b) in _BOGUS_FILLS for col in (fill, stroke))):
            return "unknown", ""
        return None, ""
    if info.in_group:
        return "group", ""
    if info.soft_mask:
        return "soft_mask", ""
    if info.blend:
        return "blend", ""
    if stroke is not None and info.stroke_pattern:
        return ("gradient", "漸層用在筆畫上") if info.stroke_pattern == "shading" else ("pattern", "")
    if fill is not None and info.fill_pattern:
        if info.fill_pattern != "shading":
            return "pattern", ""
        if info.gradient is None:
            return "gradient", info.gradient_problem
    return None, ""


def _bbox_of(points: list) -> tuple[float, float, float, float]:
    xs, ys = [q[1] for q in points], [q[2] for q in points]
    return min(xs), min(ys), max(xs), max(ys)


def _clip_region(clips: list[list]) -> list | None:
    """好幾條裁切路徑的交集，如果可以用「一條路徑」精確表示就回傳它的線段：
    全部是矩形 → 交集矩形；只有一條不是矩形、其餘的矩形都把它整個包住 → 就是那一條。其他情況回傳 None。"""
    rects, others = [], []
    for pts in clips:
        rect = pdf.segments_rect(pts)
        (rects if rect is not None else others).append(rect if rect is not None else pts)
    if len(others) > 1 or (not rects and not others):
        return None
    if others:
        l, b, r, t = _bbox_of(others[0])
        if all(rc[0] <= l + 1e-3 and rc[1] <= b + 1e-3 and rc[2] >= r - 1e-3 and rc[3] >= t - 1e-3 for rc in rects):
            return others[0]
        return None
    l, b, r, t = max(rc[0] for rc in rects), max(rc[1] for rc in rects), min(rc[2] for rc in rects), min(rc[3] for rc in rects)
    if r - l <= 0 or t - b <= 0:
        return None
    M, L = SEG_MOVETO, SEG_LINETO
    return [(M, l, b, False), (L, r, b, False), (L, r, t, False), (L, l, t, True)]


def _gradient_covers(g: pdf.RawGradient, points: list) -> bool:
    """漸層沒有往兩端延伸時，形狀必須整個落在漸層的範圍裡，否則範圍外其實不畫（輸出格式的漸層一律會延伸）。"""
    if g.kind == "linear":
        x0, y0, x1, y1 = g.coords
        n2 = (x1 - x0) ** 2 + (y1 - y0) ** 2
        ts = [((q[1] - x0) * (x1 - x0) + (q[2] - y0) * (y1 - y0)) / n2 for q in points]
        return (g.extend[0] or min(ts) >= -1e-3) and (g.extend[1] or max(ts) <= 1 + 1e-3)
    cx, cy, r = g.coords
    return g.extend[1] or max(math.hypot(q[1] - cx, q[2] - cy) for q in points) <= r * (1 + 1e-3)


def _gradient_color(g: pdf.RawGradient, alpha: float) -> Color:
    """漸層的平均色（各段依長度加權）：給不支援漸層的輸出器當替代填色。"""
    stops = sorted(g.stops, key=lambda st: st[0])
    acc, total = [0.0, 0.0, 0.0], 0.0
    for (t0, c0), (t1, c1) in zip(stops, stops[1:]):
        w = max(t1 - t0, 0.0)
        total += w
        for i in range(3):
            acc[i] += w * (c0[i] + c1[i]) / 2
    rgb = [int(round(v / total)) for v in acc] if total > 0 else list(stops[0][1])
    return Color(r=rgb[0], g=rgb[1], b=rgb[2], a=alpha)


def _classify(p: pdf.RawPath, segs: list[list], stroke: Color | None, tol: float) -> tuple[str, tuple]:
    """這條路徑是哪一種基本形狀（判斷的順序與條件沿用原本的形狀辨識）。"""
    rect = _rect_of(p, tol=tol)
    if rect is not None:
        return "rect", rect
    if len(p.points) == 2 and stroke is not None and all(pt[0] != SEG_BEZIERTO for pt in p.points):
        return "line", ()
    ell = _ellipse_of(segs, tol)
    if ell is not None:
        return "ellipse", ell
    rr = _rounded_rect_of(segs, tol)
    if rr is not None:
        return "rrect", rr
    return "free", ()


def _path_item(p: pdf.RawPath, m: list[float], fill: Color | None, stroke: Color | None, fill_rule: str, gradient, reason: str | None, detail: str, visual: bool) -> _Path | None:
    tol = 0.02
    segs = _segments(p)
    rel: list[list[float]] = []
    for sg in segs:
        if sg[0] == 3:
            rel.append([3])
            continue
        coords: list[float] = []
        for x, y in zip(sg[1::2], sg[2::2]):
            pt = to_point(m, x, y)
            coords += [pt.x, pt.y]
        rel.append([sg[0]] + coords)
    xs = [v for sg in rel if sg[0] != 3 for v in sg[1::2]]
    ys = [v for sg in rel if sg[0] != 3 for v in sg[2::2]]
    if not xs:
        return None
    box = (min(xs), min(ys), max(xs), max(ys))
    vbox = box
    if visual and p.visual_bounds is not None:
        vl, vt, vr, vb = _rel_box(m, *p.visual_bounds)
        vbox = (min(vl, box[0]), min(vt, box[1]), max(vr, box[2]), max(vb, box[3]))
    kind, geom = _classify(p, segs, stroke, tol)
    return _Path(z=p.z, raw=p, segs=segs, rel=rel, box=box, vbox=vbox, kind=kind, geom=geom, fill=fill, stroke=stroke,
                 fill_rule=fill_rule, gradient=gradient, reason=reason, detail=detail)


def _read_paths(reader: pdf.PageReader, spec: PageSpec, m: list[float], src_w: float, c: dict) -> list[_Path]:
    """讀出頁面上所有看得到的路徑與漸層物件（依繪製順序），並判斷每一個讀不讀得出來。
    讀不出來的不是丟掉：它們留在底圖，reason 記下原因；被它們蓋住的可讀路徑也一起留下（見 _mark_covered）。"""
    status = reader.paint_status
    out: list[_Path] = []
    for p in reader.paths():
        fill, stroke = _color(p.fill), _color(p.stroke)
        if fill is None and stroke is None:
            continue
        reason, detail = _unreadable(p, fill, stroke, status)
        info = p.paint
        gradient = None
        fill_rule = p.fill_rule
        visual = True
        if reason is None and fill is not None and info is not None and info.fill_pattern == "shading":
            gradient = info.gradient
        if reason is None and p.clip:
            # 整塊矩形色塊被裁切：看得到的就是「裁切區」填上這個顏色，可以精確寫成一條路徑
            region = _clip_region(p.clip) if stroke is None else None
            rect = _rect_of(p, tol=0.02)
            if region is not None and rect is not None:
                cl, cb, cr, ct = _bbox_of(region)
                inner = pdf.segments_rect(region)
                if inner is not None:  # 矩形裁矩形：交集還是矩形
                    region = _clip_region([region, [(SEG_MOVETO, rect[0], rect[1], False), (SEG_LINETO, rect[2], rect[1], False), (SEG_LINETO, rect[2], rect[3], False), (SEG_LINETO, rect[0], rect[3], True)]])
                elif not (rect[0] <= cl + 1e-3 and rect[1] <= cb + 1e-3 and rect[2] >= cr - 1e-3 and rect[3] >= ct - 1e-3):
                    region = None
                elif (info is None or info.clip_rule == "mixed") and pdf.convex_polygon(region) is None:
                    region = None  # 不知道這條裁切路徑用哪一種填色規則，而它不是單純的凸多邊形（規則會影響結果）：不猜
            else:
                region = None
            if region is None:
                reason = "clipped"
            else:
                fill_rule = info.clip_rule if info is not None and info.clip_rule != "mixed" and pdf.segments_rect(region) is None else "nonzero"
                p = pdf.RawPath(z=p.z, points=list(region), fill=p.fill, stroke=None, stroke_width=0.0, nested=p.nested, _obj=p._obj, fill_rule=fill_rule, paint=p.paint, forms=p.forms)
                visual = False
        if reason is None and gradient is not None and not _gradient_covers(gradient, p.points):
            reason, detail = "gradient", "漸層沒有延伸到整個形狀"
        if gradient is not None and fill is not None:
            fill = _gradient_color(gradient, fill.a)
        item = _path_item(p, m, fill, stroke, fill_rule, gradient if reason is None else None, reason, detail, visual)
        if item is not None:
            out.append(item)
    for sh in reader.shadings():  # 直接畫的漸層：範圍是當時的裁切區
        info = sh.paint
        region = _clip_region(sh.clip)
        reason, detail = None, ""
        if info is None or info.gradient is None:
            reason, detail = "gradient", (info.gradient_problem if info is not None else "")
        elif info.in_group or info.soft_mask or info.blend:
            reason = "group" if info.in_group else ("soft_mask" if info.soft_mask else "blend")
        elif region is None:
            reason, detail = ("gradient", "漸層的範圍由好幾條裁切路徑交出來") if sh.clip else ("page_gradient", "")
        elif info.clip_rule == "mixed" and pdf.convex_polygon(region) is None:
            reason, detail = "gradient", "漸層的範圍由裁切路徑決定，但無法確認它的填色規則"
        elif not _gradient_covers(info.gradient, region):
            reason, detail = "gradient", "漸層沒有延伸到整個形狀"
        if region is None:
            l, b, r, t = sh.bounds
            region = [(SEG_MOVETO, l, b, False), (SEG_LINETO, r, b, False), (SEG_LINETO, r, t, False), (SEG_LINETO, l, t, True)]
        gradient = info.gradient if reason is None else None
        fill = _gradient_color(gradient, 1.0) if gradient is not None else Color(r=128, g=128, b=128)
        rule = info.clip_rule if info is not None and info.clip_rule != "mixed" and pdf.segments_rect(region) is None else "nonzero"
        fake = pdf.RawPath(z=sh.z, points=list(region), fill=(fill.r, fill.g, fill.b, 255), stroke=None, stroke_width=0.0, nested=sh.nested, _obj=sh._obj, fill_rule=rule, paint=info, forms=sh.forms)
        item = _path_item(fake, m, fill, None, rule, gradient, reason, detail, False)
        if item is not None:
            out.append(item)
    out.sort(key=lambda it: it.z)
    return out


def _page_color(paths: list[_Path], spec: PageSpec, c: dict) -> tuple[Color | None, set[int]]:
    """最底層的整頁色塊當成頁面底色（不輸出成形狀，也不從底圖隱藏）。最底層的整頁漸層留在底圖。"""
    color: Color | None = None
    taken: set[int] = set()
    page_area = spec.aspect_ratio
    drawn = False
    for it in paths:
        if it.reason is not None:
            continue
        full = it.kind == "rect" and (it.box[2] - it.box[0]) * (it.box[3] - it.box[1]) / page_area >= c["full_page_coverage"]
        if full and not drawn and it.stroke is None and it.fill is not None:
            if it.gradient is not None:
                it.reason = "page_gradient"
                continue
            if it.fill.a >= 0.999:
                color = it.fill
                it.role = "page_color"
                taken.add(it.z)
                continue
        drawn = True
    return color, taken


def _mark_covered(paths: list[_Path]) -> None:
    """底圖永遠畫在最下面。留在底圖的物件原本若蓋在某條可讀路徑上面，把那條路徑取出來重畫就會反過來蓋住它，
    畫面就錯了。所以這種路徑（繪製順序在前、外框與它相交）也留在底圖；連鎖下去直到沒有為止。"""
    n = len(paths)
    if not any(it.reason is not None for it in paths):
        return
    kept = np.zeros((n, 4))
    count = 0
    eps = 1e-5
    for it in sorted(paths, key=lambda q: q.z, reverse=True):
        if it.role == "page_color":
            continue
        if it.reason is None and count:
            k = kept[:count]
            if bool(np.any((k[:, 0] < it.vbox[2] - eps) & (k[:, 2] > it.vbox[0] + eps) & (k[:, 1] < it.vbox[3] - eps) & (k[:, 3] > it.vbox[1] + eps))):
                it.reason = "covered"
        if it.reason is not None:
            kept[count] = it.vbox
            count += 1


def _leftover_note(paths: list[_Path]) -> str | None:
    left = Counter(it.reason for it in paths if it.reason is not None)
    if not left:
        return None
    parts = []
    for code, n in left.most_common():
        details = sorted({it.detail for it in paths if it.reason == code and it.detail})
        parts.append(f"{_REASONS[code]} {n} 個" + (f"（{'；'.join(details)}）" if details else ""))
    return f"有 {sum(left.values())} 個向量物件留在底圖中，不能個別編輯：{'、'.join(parts)}。"


# ---------- s3.pdf_vectors：分群 ----------


@dataclass
class GlyphRow:
    """一行疑似文字轉外框的字形。座標是頁面相對座標（y 向下）。"""

    boxes: list[tuple[float, float, float, float]]  # 每個字形的外框（左、上、右、下），由左到右
    owners: list[int]  # 每個字形來自第幾條路徑（傳入清單中的位置）
    baseline: float  # 基線的 y
    height: float  # 主要字形高度的中位數
    pieces: list[int] = field(default_factory=list)  # 併成字形之前的每一塊各來自第幾條路徑（判斷「這條路徑是不是大多落在行裡」用）


@dataclass
class _Group:
    """一個向量圖：屬於同一個圖形的路徑。"""

    members: list[_Path]  # 依繪製順序
    kind: str = "unknown"  # "unknown"、"outlined_text"
    notes: list[str] = field(default_factory=list)


def _contour_boxes(path: list[list[float]]) -> list[tuple[float, float, float, float]]:
    """一條路徑裡每一圈（子路徑）的外框。"""
    out: list[list[float]] = []
    for sg in path:
        if sg[0] == 3:
            continue
        if sg[0] == 0 or not out:
            out.append([math.inf, math.inf, -math.inf, -math.inf])
        b = out[-1]
        for x, y in zip(sg[1::2], sg[2::2]):
            b[0], b[1], b[2], b[3] = min(b[0], x), min(b[1], y), max(b[2], x), max(b[3], y)
    return [tuple(b) for b in out if b[2] >= b[0]]


def _merge_overlapping(boxes: list[tuple[float, float, float, float]], owners: list[int] | None = None) -> tuple[list[tuple[float, float, float, float]], list[int]]:
    """同一行裡，水平位置大幅重疊（重疊超過較窄那個的一半）的外框併成一個字形：i 的點、冒號的兩點、重音符號、上下結構。
    只是邊緣稍微交疊的不併（AV 這類字距緊的相鄰字母）。回傳（由左到右的外框、每個外框的主人：面積最大那一塊的）。"""
    order = sorted(range(len(boxes)), key=lambda k: boxes[k][0])
    merged: list[list[float]] = []
    owner: list[int] = []
    biggest: list[float] = []
    for k in order:
        b = boxes[k]
        area = (b[2] - b[0]) * (b[3] - b[1])
        if merged:
            last = merged[-1]
            overlap = min(last[2], b[2]) - max(last[0], b[0])
            if overlap >= 0.5 * min(last[2] - last[0], b[2] - b[0]) and overlap > 0:
                last[0], last[1], last[2], last[3] = min(last[0], b[0]), min(last[1], b[1]), max(last[2], b[2]), max(last[3], b[3])
                if area > biggest[-1]:
                    biggest[-1] = area
                    owner[-1] = owners[k] if owners is not None else k
                continue
        merged.append(list(b))
        owner.append(owners[k] if owners is not None else k)
        biggest.append(area)
    return [tuple(b) for b in merged], owner


def _glyph_blobs(path: list[list[float]]) -> list[tuple[float, float, float, float]]:
    """把一條路徑拆成「字形」的外框。一條路徑可能只畫一個字，也可能畫了整個詞、整行、甚至好幾行：
      1. 去掉包在別圈裡面的圈（字的孔）；
      2. 依上下位置分成幾帶（好幾行字畫成一條路徑時，每行一帶），比最高的一帶矮很多的是零件（i 的點、重音符號），併到最近的一帶；
      3. 每一帶裡，水平位置大幅重疊的圈併成一個字形。"""
    boxes = _contour_boxes(path)
    if len(boxes) <= 1:
        return boxes
    outer = [b for i, b in enumerate(boxes) if not any(j != i and o[0] <= b[0] and o[1] <= b[1] and o[2] >= b[2] and o[3] >= b[3] and (o != b or j < i) for j, o in enumerate(boxes))]
    bands: list[list] = []  # 每一帶：[上緣, 下緣, 這一帶的外框]
    for b in sorted(outer, key=lambda q: q[1]):
        if bands and b[1] <= bands[-1][1]:
            bands[-1][1] = max(bands[-1][1], b[3])
            bands[-1][2].append(b)
        else:
            bands.append([b[1], b[3], [b]])
    while len(bands) > 1:
        tallest = max(band[1] - band[0] for band in bands)
        small = next((i for i, band in enumerate(bands) if band[1] - band[0] < 0.5 * tallest), None)
        if small is None:
            break
        above = bands[small][0] - bands[small - 1][1] if small > 0 else math.inf
        below = bands[small + 1][0] - bands[small][1] if small + 1 < len(bands) else math.inf
        target = small - 1 if above <= below else small + 1
        lo, hi = min(small, target), max(small, target)
        bands[lo] = [bands[lo][0], bands[hi][1], bands[lo][2] + bands[hi][2]]
        del bands[hi]
    out: list[tuple[float, float, float, float]] = []
    for _, _, members in bands:
        out += _merge_overlapping(members)[0]
    return out


def glyph_rows(paths: list[list[list[float]]], c: dict, suspected: bool = False, free: list[bool] | None = None) -> list[GlyphRow]:
    """在一批填色路徑裡找「沿基線排成一行的小型字形」（疑似文字轉外框）。
    paths 是每條路徑的線段（頁面相對座標，與 VectorPath.path 同一種編碼），所以也可以直接拿向量圖的路徑來用；
    free 標出哪些是自由路徑（不是矩形、圓）：一行裡至少一半的字形要來自自由路徑，免得一排小方塊被當成字。
    suspected 是①的旗標（整頁沒有文字但路徑很多）：這時門檻放寬。只找水平的行；直排與旋轉的文字找不到。"""
    min_glyphs = int(c.get("pdf_outlined_min_glyphs_suspected", 3) if suspected else c.get("pdf_outlined_min_glyphs", 6))
    aligned_share = 0.45 if suspected else float(c.get("pdf_outlined_aligned_share", 0.6))
    max_gap = float(c.get("pdf_outlined_max_gap", 1.2))
    max_height = float(c.get("pdf_outlined_max_height", 0.08))
    blobs: list[tuple[float, float, float, float]] = []
    owner: list[int] = []
    for i, path in enumerate(paths):
        for b in _glyph_blobs(path):
            if b[2] - b[0] > 0 and b[3] - b[1] > 0 and b[3] - b[1] <= 2.5 * max_height:
                blobs.append(b)
                owner.append(i)
    n = len(blobs)
    if n < min_glyphs:
        return []
    order = sorted(range(n), key=lambda k: blobs[k][0])
    arr = np.array([blobs[k] for k in order])
    heights = arr[:, 3] - arr[:, 1]
    parent = list(range(n))

    def find(i: int) -> int:
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    # 左右相鄰、高度帶相容的字形連成一行：小的那個要落在大的那個上下各放寬三成的範圍裡
    tallest_after = np.maximum.accumulate(heights[::-1])[::-1]  # 排在後面的字形裡最高的
    for i in range(n - 1):
        hi = int(np.searchsorted(arr[:, 0], arr[i, 2] + max_gap * max(heights[i], float(tallest_after[i + 1])), side="right"))
        if hi <= i + 1:
            continue
        js = np.arange(i + 1, hi)
        hb = np.maximum(heights[i], heights[js])
        big_top = np.where(heights[js] > heights[i], arr[js, 1], arr[i, 1])
        big_bottom = np.where(heights[js] > heights[i], arr[js, 3], arr[i, 3])
        small_top = np.where(heights[js] > heights[i], arr[i, 1], arr[js, 1])
        small_bottom = np.where(heights[js] > heights[i], arr[i, 3], arr[js, 3])
        ok = (arr[js, 0] - arr[i, 2] <= max_gap * hb) & (small_top >= big_top - 0.3 * hb) & (small_bottom <= big_bottom + 0.3 * hb)
        for j in js[ok]:
            a, b = find(i), find(int(j))
            if a != b:
                parent[a] = b
    comps: dict[int, list[int]] = {}
    for i in range(n):
        comps.setdefault(find(i), []).append(i)
    rows: list[GlyphRow] = []
    for members in comps.values():
        if len(members) < min_glyphs:
            continue
        idx = np.array(members)
        h = heights[idx]
        main = idx[h >= 0.45 * np.percentile(h, 90)]  # 主要字形（不含標點、i 的點）
        if len(main) < max(3, 0.5 * len(idx)):
            continue
        hm = float(np.median(heights[main]))
        if hm > max_height:
            continue
        widths = arr[main, 2] - arr[main, 0]
        if float(np.mean(widths <= 2.5 * hm)) < 0.8:
            continue  # 字形不會比字高寬很多；一排長條不是字
        xs = arr[idx][np.argsort(arr[idx, 0])]
        overlap = float(np.clip(xs[:-1, 2] - xs[1:, 0], 0, None).sum())
        if overlap > 0.2 * float((xs[:, 2] - xs[:, 0]).sum()):
            continue  # 字是一個接一個排的，不會大量左右重疊
        if len(np.unique(np.round(widths / (0.05 * hm)))) < (2 if len(main) < 6 else 3):
            continue  # 字母有寬有窄；一排一模一樣的圖示（箭頭、星星、樹）不是字
        bottoms, centers = arr[main, 3], (arr[main, 1] + arr[main, 3]) / 2
        base = float(np.median(bottoms))
        share = max(float(np.mean(np.abs(bottoms - base) <= 0.15 * hm)), float(np.mean(np.abs(centers - np.median(centers)) <= 0.15 * hm)))
        if share < aligned_share:
            continue  # 沒有沿著同一條基線（或同一條中線）排
        if free is not None and sum(1 for k in main if free[owner[order[int(k)]]]) < 0.5 * len(main):
            continue
        pieces = [owner[order[k]] for k in members]
        boxes, owners = _merge_overlapping([tuple(float(v) for v in arr[k]) for k in members], pieces)  # 不同路徑的零件（分開畫的點）也併回字形
        rows.append(GlyphRow(boxes=boxes, owners=owners, baseline=base, height=hm, pieces=pieces))
    return rows


def _glyph_blocks(cands: list[_Path], c: dict, suspected: bool) -> list[tuple[list[_Path], int, int]]:
    """疑似文字轉外框的路徑 → 文字區塊。回傳每個區塊的（路徑、行數、字形數）。"""
    max_side = 10.0  # 基本形狀要當成字形（I、l、句點），長邊不能超過短邊這麼多倍：表格線與底線不算
    pool = []
    for it in cands:
        if it.fill is None or it.stroke is not None or it.gradient is not None or it.kind not in ("free", "rect", "ellipse"):
            continue
        w, h = it.box[2] - it.box[0], it.box[3] - it.box[1]
        if it.kind != "free" and max(w, h) > max_side * max(min(w, h), 1e-12):
            continue
        pool.append(it)
    if not pool:
        return []
    rows = glyph_rows([it.rel for it in pool], c, suspected, free=[it.kind == "free" for it in pool])
    if not rows:
        return []
    # 一條路徑的字形要大多落在合格的行裡，才算文字
    total = Counter()
    for i, it in enumerate(pool):
        total[i] = len(_glyph_blobs(it.rel))
    in_rows = Counter(o for row in rows for o in row.pieces)
    texty = {i for i in in_rows if in_rows[i] >= 0.8 * max(total[i], 1)}
    rows = [row for row in rows if sum(1 for o in row.owners if o in texty) >= 0.8 * len(row.owners)]
    if not rows:
        return []
    # 行 → 區塊：上下相鄰（基線距離不超過幾倍字高）、字高相近、左右有重疊的行是同一塊；同一條基線上隔開的片段也是
    pitch = float(c.get("pdf_outlined_line_pitch", 3.2))
    parent = list(range(len(rows)))

    def find(i: int) -> int:
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    ext = [(min(b[0] for b in r.boxes), max(b[2] for b in r.boxes)) for r in rows]
    for i, a in enumerate(rows):
        for j in range(i + 1, len(rows)):
            b = rows[j]
            hm = max(a.height, b.height)
            if hm > 1.5 * min(a.height, b.height):
                continue
            gap_x = max(ext[i][0], ext[j][0]) - min(ext[i][1], ext[j][1])  # 負的表示左右有重疊
            dy = abs(a.baseline - b.baseline)
            if (dy <= pitch * hm and gap_x <= 0) or (dy <= 0.25 * hm and gap_x <= 4 * hm):
                parent[find(i)] = find(j)
    first_row: dict[int, int] = {}
    for i, row in enumerate(rows):  # 同一條路徑橫跨好幾行（整段畫成一條路徑）
        for o in row.owners:
            if o in first_row:
                parent[find(i)] = find(first_row[o])
            else:
                first_row[o] = i
    blocks: dict[int, list[int]] = {}
    for i in range(len(rows)):
        blocks.setdefault(find(i), []).append(i)
    out = []
    for members in blocks.values():
        owners = sorted({o for i in members for o in rows[i].owners if o in texty})
        glyphs = sum(len(rows[i].owners) for i in members)
        if owners:
            out.append(([pool[o] for o in owners], len(members), glyphs))
    return out


def _components(boxes: np.ndarray, gap_abs: float, gap_ratio: float) -> list[list[int]]:
    """外框相交、或相距不超過 max(gap_abs, gap_ratio × 兩者中較小者的長邊) 的連在一起。回傳各群的成員位置。"""
    n = len(boxes)
    if n <= 1:
        return [[i] for i in range(n)]
    order = np.argsort(boxes[:, 0], kind="stable")
    b = boxes[order]
    size = np.maximum(b[:, 2] - b[:, 0], b[:, 3] - b[:, 1])
    parent = list(range(n))

    def find(i: int) -> int:
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    for i in range(n - 1):
        hi = int(np.searchsorted(b[:, 0], b[i, 2] + max(gap_abs, gap_ratio * size[i]), side="right"))
        if hi <= i + 1:
            continue
        js = np.arange(i + 1, hi)
        g = np.maximum(gap_abs, gap_ratio * np.minimum(size[i], size[js]))
        ok = (b[js, 0] <= b[i, 2] + g) & (b[js, 1] <= b[i, 3] + g) & (b[i, 1] <= b[js, 3] + g)
        for j in js[ok]:
            x, y = find(i), find(int(j))
            if x != y:
                parent[x] = y
    comps: dict[int, list[int]] = {}
    for i in range(n):
        comps.setdefault(find(i), []).append(int(order[i]))
    return list(comps.values())


class _Drawn:
    """頁面上所有「會被取出來、各自疊在底圖上」的物件（文字、圖片、可讀的路徑），依繪製順序排好。
    用來檢查一群路徑合成一個向量圖之後，前後順序還對不對。"""

    def __init__(self, paths: list[_Path], others: list[tuple[int, tuple[float, float, float, float]]]):
        items = [(it.z, it.vbox, it) for it in paths if it.reason is None and it.role != "page_color"] + [(z, box, None) for z, box in others]
        items.sort(key=lambda q: q[0])
        self.z = np.array([q[0] for q in items], dtype=np.int64)
        self.box = np.array([q[1] for q in items], dtype=float).reshape(-1, 4)
        self.path: list[_Path | None] = [q[2] for q in items]


def _settle_order(clusters: list[list[_Path]], drawn: _Drawn, taken: set[int], c: dict, recluster: bool) -> list[list[_Path]]:
    """一群路徑合成一個向量圖後，整群只佔一個繪製順序（最底下那條的）。如果有別的物件原本夾在這群中間、
    而且被群裡「畫在它後面」的路徑蓋到，合起來之後它就會跑到整群上面，畫面就錯了。處理方式：
      夾在中間的是比整群小的矩形、圓、直線 → 算進這一群（它本來就是圖的一部分）；
      其他（文字、圖片、大色塊、別群的路徑）→ 從那裡把群切開，各自重新分群。"""
    gap_abs = float(c.get("pdf_vector_join_gap", 0.002))
    gap_ratio = float(c.get("pdf_vector_join_gap_ratio", 0.1))
    max_area = float(c.get("pdf_vector_absorb_max_area", 0.6))
    eps = 1e-5
    done: list[list[_Path]] = []
    work = [list(cl) for cl in clusters]
    while work:
        members = sorted(work.pop(), key=lambda it: it.z)
        while True:
            zs = np.array([it.z for it in members], dtype=np.int64)
            boxes = np.array([it.vbox for it in members], dtype=float)
            lo, hi = int(np.searchsorted(drawn.z, zs[0], side="right")), int(np.searchsorted(drawn.z, zs[-1], side="left"))
            l, t, r, b = boxes[:, 0].min(), boxes[:, 1].min(), boxes[:, 2].max(), boxes[:, 3].max()
            conflicts = []
            if hi > lo:
                # 夾在中間的物件可能有幾萬個：先整批篩掉「不是這一群的、外框碰不到整群外框的」，剩下的才逐一確認
                between = drawn.box[lo:hi]
                near = (between[:, 0] < r - eps) & (between[:, 2] > l + eps) & (between[:, 1] < b - eps) & (between[:, 3] > t + eps)
                near &= ~np.isin(drawn.z[lo:hi], zs)
                for k in np.nonzero(near)[0] + lo:
                    fb = drawn.box[k]
                    later = boxes[int(np.searchsorted(zs, drawn.z[k], side="right")):]  # 這一群裡畫在它後面的路徑
                    if bool(np.any((later[:, 0] < fb[2] - eps) & (later[:, 2] > fb[0] + eps) & (later[:, 1] < fb[3] - eps) & (later[:, 3] > fb[1] + eps))):
                        conflicts.append(int(k))
            if not conflicts:
                done.append(members)
                break
            area = max((r - l) * (b - t), 1e-12)
            absorbed = []
            for k in conflicts:
                it = drawn.path[k]
                if it is None or it.kind not in _PRIMITIVES or it.glyph or it.z in taken or it.gradient is not None:
                    continue
                v = it.vbox
                if v[0] >= l - gap_abs and v[1] >= t - gap_abs and v[2] <= r + gap_abs and v[3] <= b + gap_abs and (v[2] - v[0]) * (v[3] - v[1]) <= max_area * area:
                    absorbed.append(it)
            if absorbed:
                members = sorted(members + absorbed, key=lambda it: it.z)
                taken.update(it.z for it in absorbed)
                continue
            cuts = sorted(int(drawn.z[k]) for k in conflicts)
            parts: list[list[_Path]] = [[] for _ in range(len(cuts) + 1)]
            for it in members:
                parts[int(np.searchsorted(cuts, it.z))].append(it)
            for part in parts:
                if not part:
                    continue
                if recluster and len(part) > 1:
                    for comp in _components(np.array([it.box for it in part], dtype=float), gap_abs, gap_ratio):
                        work.append([part[i] for i in comp])
                else:
                    work.append(part)
            break
    return done


def _needs_vector(it: _Path, c: dict) -> bool:
    """形狀（ShapeElement）表示不了、只能放進向量圖的路徑：漸層填色，或線段太多的自由路徑。"""
    return it.gradient is not None or (it.kind == "free" and len(it.segs) > c.get("max_path_segments", 400))


def _group_paths(paths: list[_Path], reader: pdf.PageReader, m: list[float], c: dict, suspected: bool) -> list[_Group]:
    """把屬於同一個圖形的路徑分群。規則（門檻都在設定檔）：
      1. 只有「自由路徑」（不是矩形、圓角矩形、橢圓、直線）會帶頭成群；外框相交或很靠近的連成一群。
         矩形、直線這些基本形狀不會因為靠近就被吸進來，所以表格格線、文字的底線與框、色塊都維持是形狀。
      2. 疑似文字轉外框的路徑先挑出來，自己成群（kind 為 outlined_text），不和插畫混在一起。
      3. 合成一群後前後順序不能錯（見 _settle_order）：中間被文字或圖片隔開的群會被切開。
      4. 一群至少要有 pdf_vector_min_paths 條路徑、pdf_vector_min_segments 段線段才算向量圖；全部只有直線的群
         （箭頭、三角形、折線這類辦公軟體常見的圖形）要更多條（pdf_vector_min_straight_paths）才算。
         不夠的照舊各自變成形狀。形狀表示不了的路徑（漸層、線段太多）單獨一條也成一個向量圖。
      5. 零散的自由路徑如果多到超過 max_vector_paths，不各自變成形狀，依繪製順序合成向量圖。
      6. 分出來的向量圖超過 pdf_vector_max_groups 個時（地圖、工程圖），不再細分，依繪製順序合併成幾層。"""
    cands = [it for it in paths if it.reason is None and it.role == ""]
    if not cands:
        return []
    others = [(z, _rel_box(m, *box)) for z, box in reader.object_bounds(("text", "image"))]
    drawn = _Drawn(paths, others)
    taken: set[int] = set()
    groups: list[_Group] = []

    # 2. 文字轉外框
    for block, n_rows, n_glyphs in _glyph_blocks(cands, c, suspected):
        for it in block:
            it.glyph = True
        taken.update(it.z for it in block)
        for part in _settle_order([block], drawn, taken, c, recluster=False):
            groups.append(_Group(members=part, kind="outlined_text", notes=[
                f"疑似文字轉成的外框（約 {n_rows} 行、{n_glyphs} 個字形）：目前是向量圖，可以改形狀與顏色，還不能當文字編輯。"
            ]))

    # 1、3、4. 一般圖形
    gap_abs = float(c.get("pdf_vector_join_gap", 0.002))
    gap_ratio = float(c.get("pdf_vector_join_gap_ratio", 0.1))
    min_paths = int(c.get("pdf_vector_min_paths", 3))
    min_segments = int(c.get("pdf_vector_min_segments", 24))
    min_straight = int(c.get("pdf_vector_min_straight_paths", 8))
    free = [it for it in cands if it.kind == "free" and not it.glyph]
    clusters = [[free[i] for i in comp] for comp in _components(np.array([it.box for it in free], dtype=float).reshape(-1, 4), gap_abs, gap_ratio)] if free else []
    taken.update(it.z for it in free)
    in_graphic: set[int] = set(it.z for g in groups for it in g.members)
    for members in _settle_order(clusters, drawn, taken, c, recluster=True):
        enough = len(members) >= min_paths and sum(len(it.segs) for it in members) >= min_segments
        if enough and len(members) < min_straight and not any(sg[0] == 2 for it in members for sg in it.segs):
            enough = False  # 只有直線、條數又少：是幾個簡單的圖形排在一起，不是插畫
        if enough or any(_needs_vector(it, c) for it in members):
            groups.append(_Group(members=members))
            in_graphic.update(it.z for it in members)
    for it in cands:  # 形狀表示不了的基本形狀（漸層填色的矩形、圓）：單獨成一個向量圖
        if it.z not in in_graphic and it.kind != "free" and _needs_vector(it, c):
            groups.append(_Group(members=[it]))
            in_graphic.add(it.z)

    # 6. 向量圖太多：整頁是一大片插畫（地圖、工程圖、圖示牆），不再細分。所有自由路徑（含零散的）依繪製順序合併，
    #    只在「被文字、圖片、大色塊隔開」的地方切開；切出來只剩一條的照舊當形狀
    max_groups = int(c.get("pdf_vector_max_groups", 60))
    plain = [g for g in groups if g.kind != "outlined_text"]
    if len(plain) > max_groups:
        taken = {it.z for g in groups if g.kind == "outlined_text" for it in g.members} | {it.z for it in free}  # 先前被各群吸進去的矩形、圓重新來過
        merged = [part for part in _settle_order([free], drawn, taken, c, recluster=False) if len(part) >= 2 or _needs_vector(part[0], c)]
        note = f"這一頁的圖形很多（原本分成 {len(plain)} 群），不再細分，依繪製順序合成 {len(merged)} 個向量圖。"
        groups = [g for g in groups if g.kind == "outlined_text"] + [_Group(members=part, notes=[note]) for part in merged]
        in_graphic = set(it.z for g in groups for it in g.members)
        for it in cands:  # 沒有被併進去的漸層矩形、圓：仍然各自成一個向量圖
            if it.z not in in_graphic and it.kind != "free" and _needs_vector(it, c):
                groups.append(_Group(members=[it]))
                in_graphic.add(it.z)

    # 5. 零散的自由路徑太多
    loose = [it for it in free if it.z not in in_graphic]
    if len(loose) > c.get("max_vector_paths", 300):
        taken = in_graphic | {it.z for it in free}
        for part in _settle_order([loose], drawn, taken, c, recluster=False):
            if len(part) >= 2:
                groups.append(_Group(members=part, notes=[f"這 {len(part)} 條零散的路徑數量太多，不適合各自變成形狀，依繪製順序合成一個向量圖。"]))
                in_graphic.update(it.z for it in part)
    for g in groups:
        g.members.sort(key=lambda it: it.z)
        for it in g.members:
            it.role = "vector"
    groups.sort(key=lambda g: g.members[0].z)
    return groups


# ---------- s3.pdf_vectors：轉成向量圖 ----------


def _cubic_split(p0, p1, p2, p3, t: float):
    """三次貝茲在 t 切成兩段（de Casteljau，精確）。"""
    def mix(a, b):
        return (a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t)

    a, b, cc = mix(p0, p1), mix(p1, p2), mix(p2, p3)
    d, e = mix(a, b), mix(b, cc)
    f = mix(d, e)
    return (p0, a, d, f), (f, e, cc, p3)


_GAUSS = ((-0.9061798459386640, 0.2369268850561891), (-0.5384693101056831, 0.4786286704993665), (0.0, 0.5688888888888889),
          (0.5384693101056831, 0.4786286704993665), (0.9061798459386640, 0.2369268850561891))


def _cubic_length(p0, p1, p2, p3, t1: float = 1.0, depth: int = 4) -> float:
    """三次貝茲從 0 到 t1 的弧長（分段高斯積分）。"""
    n = 1 << depth
    total = 0.0
    for k in range(n):
        a, b = t1 * k / n, t1 * (k + 1) / n
        half, mid = (b - a) / 2, (a + b) / 2
        for x, w in _GAUSS:
            t = mid + half * x
            mt = 1 - t
            dx = 3 * (mt * mt * (p1[0] - p0[0]) + 2 * mt * t * (p2[0] - p1[0]) + t * t * (p3[0] - p2[0]))
            dy = 3 * (mt * mt * (p1[1] - p0[1]) + 2 * mt * t * (p2[1] - p1[1]) + t * t * (p3[1] - p2[1]))
            total += w * half * math.hypot(dx, dy)
    return total


def _dash_path(path: list[list[float]], dash: list[float], phase: float) -> list[list[float]]:
    """把虛線展開成一段一段的實線（資料格式沒有虛線欄位）。每一段都是原路徑的一部分：直線精確，
    曲線用 de Casteljau 切開（切點的位置由弧長數值求出）。每一圈（子路徑）各自從虛線樣式的起點算起。"""
    pattern = [abs(d) for d in dash]
    if len(pattern) % 2:
        pattern = pattern * 2
    if not pattern or sum(pattern) <= 1e-12:
        return path
    out: list[list[float]] = []
    pieces: list[tuple] = []  # 目前這一圈的線段：("L", p0, p1) 或 ("C", p0, p1, p2, p3)
    start = cur = None

    def flush() -> None:
        nonlocal pieces
        idx, on, left = 0, True, pattern[0]
        skip = phase % sum(pattern)
        while skip > 0:  # 相位：先走掉樣式的前面一段
            step = min(skip, left)
            skip -= step
            left -= step
            if left <= 1e-12:
                idx = (idx + 1) % len(pattern)
                on, left = not on, pattern[idx]
        pen_down = False
        for piece in pieces:
            length = math.hypot(piece[2][0] - piece[1][0], piece[2][1] - piece[1][1]) if piece[0] == "L" else _cubic_length(*piece[1:])
            pos = 0.0
            rest = piece
            while length - pos > 1e-12:
                step = min(left, length - pos)
                if rest[0] == "L":
                    t = step / max(length - pos, 1e-18)
                    a, b = rest[1], rest[2]
                    mid = (a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t) if t < 1 else b
                    head, rest = ("L", a, mid), ("L", mid, b)
                else:
                    if step >= length - pos - 1e-12:
                        head, rest = rest, ("C", rest[4], rest[4], rest[4], rest[4])
                    else:
                        lo, hi = 0.0, 1.0
                        for _ in range(40):  # 二分法找「弧長剛好是 step」的參數
                            mid_t = (lo + hi) / 2
                            if _cubic_length(*rest[1:], t1=mid_t, depth=3) < step:
                                lo = mid_t
                            else:
                                hi = mid_t
                        first, second = _cubic_split(*rest[1:], (lo + hi) / 2)
                        head, rest = ("C", *first), ("C", *second)
                if on:
                    if not pen_down:
                        out.append([0, head[1][0], head[1][1]])
                        pen_down = True
                    if head[0] == "L":
                        out.append([1, head[2][0], head[2][1]])
                    else:
                        out.append([2, head[2][0], head[2][1], head[3][0], head[3][1], head[4][0], head[4][1]])
                pos += step
                left -= step
                if left <= 1e-12:
                    idx = (idx + 1) % len(pattern)
                    on, left = not on, pattern[idx]
                    pen_down = False
                    if on and left <= 1e-12 and length - pos > 1e-12:  # 長度為零的實線段（圓頭線帽會畫成圓點）
                        pt = rest[1]
                        out.append([0, pt[0], pt[1]])
                        out.append([1, pt[0], pt[1]])
        pieces = []

    for sg in path:
        if sg[0] == 0:
            flush()
            start = cur = (sg[1], sg[2])
        elif sg[0] == 1 and cur is not None:
            pieces.append(("L", cur, (sg[1], sg[2])))
            cur = (sg[1], sg[2])
        elif sg[0] == 2 and cur is not None:
            pieces.append(("C", cur, (sg[1], sg[2]), (sg[3], sg[4]), (sg[5], sg[6])))
            cur = (sg[5], sg[6])
        elif sg[0] == 3 and cur is not None and start is not None:
            if math.hypot(cur[0] - start[0], cur[1] - start[1]) > 1e-12:
                pieces.append(("L", cur, start))
            cur = start
    flush()
    return out


def _primitive(it: _Path, m: list[float], src_w: float) -> VectorPrimitive | None:
    """這條路徑其實是哪一種基本圖形（頁面相對座標）。給輸出器寫成好編輯的 <rect>、<circle> 等。"""
    if it.kind in ("rect", "ellipse", "rrect"):
        l, b, r, t = it.geom[0] if it.kind == "rrect" else it.geom
        x0, y0, x1, y1 = _rel_box(m, l, b, r, t)
        w, h = x1 - x0, y1 - y0
        if it.kind == "rect":
            return VectorPrimitive(kind="rect", cx=(x0 + x1) / 2, cy=(y0 + y1) / 2, w=w, h=h)
        if it.kind == "rrect":
            return VectorPrimitive(kind="rounded_rect", cx=(x0 + x1) / 2, cy=(y0 + y1) / 2, w=w, h=h, corner_radius=it.geom[1] / src_w)
        return VectorPrimitive(kind="circle" if abs(w - h) <= 1e-3 * max(w, h) else "ellipse", cx=(x0 + x1) / 2, cy=(y0 + y1) / 2, w=w, h=h)
    if it.kind == "line":
        (xa, ya), (xb, yb) = it.rel[0][1:3], it.rel[1][1:3]
        return VectorPrimitive(kind="line", cx=(xa + xb) / 2, cy=(ya + yb) / 2, w=math.hypot(xb - xa, yb - ya), h=0.0, angle_deg=math.degrees(math.atan2(yb - ya, xb - xa)))
    body = [sg for sg in it.rel if sg[0] != 3]
    if len(body) >= 3 and body[0][0] == 0 and all(sg[0] == 1 for sg in body[1:]) and it.rel[-1][0] == 3:
        pts = [[sg[1], sg[2]] for sg in body]
        if math.hypot(pts[0][0] - pts[-1][0], pts[0][1] - pts[-1][1]) <= 1e-9:
            pts = pts[:-1]
        if len(pts) >= 3:
            xs, ys = [q[0] for q in pts], [q[1] for q in pts]
            return VectorPrimitive(kind="polygon", cx=(min(xs) + max(xs)) / 2, cy=(min(ys) + max(ys)) / 2, w=max(xs) - min(xs), h=max(ys) - min(ys), points=pts)
    return None


def _vector_paths(it: _Path, m: list[float], src_w: float, hairline: float, remarks: Counter) -> list[VectorPath]:
    """一條路徑 → 向量圖裡的路徑（幾何、填色、填色規則、筆畫、線帽、接合、不透明度都照原樣）。"""
    raw_path = it.raw
    gradient = None
    if it.gradient is not None:
        g = it.gradient
        if g.kind == "linear":
            a, b = to_point(m, g.coords[0], g.coords[1]), to_point(m, g.coords[2], g.coords[3])
            coords = [a.x, a.y, b.x, b.y]
        else:
            center = to_point(m, g.coords[0], g.coords[1])
            coords = [center.x, center.y, g.coords[2] / src_w]
        gradient = Gradient(kind=g.kind, coords=coords, stops=[GradientStop(offset=t, color=Color(r=col[0], g=col[1], b=col[2], a=it.fill.a if it.fill else 1.0)) for t, col in g.stops])
        remarks["gradient"] += 1
    width = None
    if it.stroke is not None:
        width = raw_path.stroke_width / src_w
        if width <= 0:
            width = hairline  # 寬度 0 在 PDF 裡是「裝置上最細的線」
            remarks["hairline"] += 1
        if not raw_path.uniform_scale:
            remarks["nonuniform"] += 1
    prim = _primitive(it, m, src_w)
    if it.stroke is not None and raw_path.dash:
        remarks["dash"] += 1
        out = []
        if it.fill is not None:
            out.append(VectorPath(path=it.rel, fill=it.fill, fill_rule=it.fill_rule, gradient=gradient, primitive=prim))
        dashed = _dash_path(it.rel, [d / src_w for d in raw_path.dash], raw_path.dash_phase / src_w)
        out.append(VectorPath(path=dashed, stroke=it.stroke, stroke_width=width, stroke_cap=raw_path.stroke_cap, stroke_join=raw_path.stroke_join))
        return out
    return [VectorPath(
        path=it.rel, fill=it.fill, fill_rule=it.fill_rule if it.fill is not None else "nonzero", gradient=gradient, stroke=it.stroke, stroke_width=width,
        stroke_cap=raw_path.stroke_cap if it.stroke is not None else "butt", stroke_join=raw_path.stroke_join if it.stroke is not None else "miter", primitive=prim,
    )]


_REMARKS = {
    "gradient": "含 {n} 個漸層填色（不支援漸層的輸出格式會改用平均色）。",
    "dash": "有 {n} 條虛線已展開成一段一段的實線（資料格式沒有虛線欄位），畫面相同，但改線型時要整條重設。",
    "hairline": "有 {n} 條寬度為 0 的極細線，以一個像素的寬度輸出。",
    "nonuniform": "有 {n} 條筆畫被非等比縮放（原本各方向粗細不同），寬度取平均值。",
}


def _vector_graphics(groups: list[_Group], reader: pdf.PageReader, spec: PageSpec, src_w: float, c: dict, store: Store, notes: list[str]) -> tuple[list[VectorGraphic], list[ImageElement], set[int]]:
    """每一群 → 一個向量圖（精確擷取）加一張對應的圖片：只畫這一群物件、透明底。兩者的位置與圖層相同。
    回傳（向量圖、對應的圖片、已取出的物件）。"""
    step = f"{NAME}.pdf_vectors@{VERSION}"
    m = spec.source_to_page
    aspect = spec.aspect_ratio
    base_dpi = float(c.get("pdf_vector_raster_dpi", 300))
    max_pixels = float(c.get("pdf_vector_raster_max_pixels", 8_000_000))
    page_pixels = float(c.get("pdf_vector_raster_page_pixels", 24_000_000))
    min_dpi = min(float(c.get("pdf_vector_raster_min_dpi", 100)), base_dpi)
    hairline = 1.0 / max(spec.working_image_size[0], 1)
    base_w, base_h = reader.render_size(base_dpi)
    spans = []
    for g in groups:
        l = max(min(it.vbox[0] for it in g.members), 0.0)
        t = max(min(it.vbox[1] for it in g.members), 0.0)
        r = min(max(it.vbox[2] for it in g.members), 1.0)
        b = min(max(it.vbox[3] for it in g.members), aspect)
        if r <= l or b <= t:  # 整群在頁面外：看不到，不輸出
            for it in g.members:
                it.role = "offpage"
            continue
        area = (r - l) * base_w * (b - t) / aspect * base_h  # 用設定的解析度畫，會是幾個像素
        spans.append((g, (l, t, r, b), area, min(1.0, math.sqrt(max_pixels / area))))  # 太大的圖降低解析度，免得一張圖幾千萬像素
    # 整頁所有向量圖的像素總量也有上限（一頁有上百個、或好幾層整頁大小的向量圖時）：超過就一起降低解析度，但不低於下限
    total = sum(area * k * k for _, _, area, k in spans)
    shrink = math.sqrt(page_pixels / total) if total > page_pixels else 1.0
    jobs = []
    for g, (l, t, r, b), _, k in spans:
        dpi = min(base_dpi * k, max(base_dpi * k * shrink, min_dpi))
        w, h = reader.render_size(dpi)
        box = (max(math.floor(l * w) - 1, 0), max(math.floor(t / aspect * h) - 1, 0), min(math.ceil(r * w) + 1, w), min(math.ceil(b / aspect * h) + 1, h))
        jobs.append((g, dpi, box, (w, h)))
    blobs: dict[int, tuple] = {}

    def keep(index: int, bitmap: Image.Image) -> None:  # 畫好一張就存一張，不把上百張圖同時留在記憶體裡
        if np.asarray(bitmap.getchannel("A")).any():
            blobs[index] = (store.put_blob(_png(bitmap), "image/png", "png"), bitmap.size)

    if jobs:
        reader.render_only([({it.z for it in g.members}, dpi, box) for g, dpi, box, _ in jobs], each=keep)
    vectors: list[VectorGraphic] = []
    images: list[ImageElement] = []
    taken: set[int] = set()
    outlined = 0
    for index, (g, dpi, box, (w, h)) in enumerate(jobs):
        if index not in blobs:  # 畫出來是空的（例如完全透明）：不輸出
            for it in g.members:
                it.role = "offpage"
            continue
        blob, size = blobs[index]
        quad = Quad.from_rect(box[0] / w, box[1] / h * aspect, box[2] / w, box[3] / h * aspect)
        z = g.members[0].z
        image = ImageElement(id=make_element_id(spec.page_index, "img", quad, blob.sha256[:12]), quad=quad, z=z, image=blob, pixel_size=size, kind=exact(ImageKind.ILLUSTRATION, step))
        remarks: Counter = Counter()
        vpaths = [vp for it in g.members for vp in _vector_paths(it, m, src_w, hairline, remarks)]
        vectors.append(VectorGraphic(
            id=make_element_id(spec.page_index, "vec", quad, f"{len(vpaths)}:{blob.sha256[:12]}"), image_id=image.id, quad=quad, z=z, paths=vpaths,
            origin=exact("pdf_paths", step), kind=g.kind, notes=g.notes + [_REMARKS[k].format(n=n) for k, n in remarks.items()],
        ))
        images.append(image)
        taken.update(it.z for it in g.members)
        outlined += g.kind == "outlined_text"
    if outlined:
        notes.append(f"這一頁有 {outlined} 組疑似轉成外框的文字，先保留成向量圖（可以改形狀與顏色），還不能當文字編輯。")
    return vectors, images, taken


# ---------- s3.pdf_shapes ----------


def _shapes(paths: list[_Path], spec: PageSpec, m: list[float], src_w: float, notes: list[str]) -> tuple[list[ShapeElement], set[int]]:
    """沒有成群的可讀路徑 → 形狀。矩形、圓角矩形、橢圓、直線認成基本幾何；其他的保留成自訂路徑。
    回傳（形狀、已取出的物件）。留在底圖的物件在這裡彙整成一則備註。"""
    out: list[ShapeElement] = []
    taken: set[int] = set()

    def rel_quad(l: float, b: float, r: float, t: float) -> Quad:
        a, bq = to_point(m, l, t), to_point(m, r, b)
        return Quad.from_rect(min(a.x, bq.x), min(a.y, bq.y), max(a.x, bq.x), max(a.y, bq.y))

    for it in paths:
        if it.reason is not None or it.role != "":
            continue
        p, fill, stroke = it.raw, it.fill, it.stroke
        sw = (p.stroke_width / src_w) if stroke else None
        key_col = f"{fill.hex if fill else ''}:{stroke.hex if stroke else ''}"
        if it.kind == "rect":
            q = rel_quad(*it.geom)
            out.append(ShapeElement(id=make_element_id(spec.page_index, "shp", q, f"rect:{key_col}"), quad=q, z=p.z, geometry=ShapeGeometry.RECT, fill=fill, stroke=stroke, stroke_width=sw))
        elif it.kind == "line":
            a, bq = to_point(m, p.points[0][1], p.points[0][2]), to_point(m, p.points[1][1], p.points[1][2])
            q = Quad(tl=a, tr=bq, br=bq, bl=a)
            out.append(ShapeElement(id=make_element_id(spec.page_index, "shp", q, f"line:{stroke.hex}"), quad=q, z=p.z, geometry=ShapeGeometry.LINE, stroke=stroke, stroke_width=sw))
        elif it.kind == "ellipse":
            q = rel_quad(*it.geom)
            out.append(ShapeElement(id=make_element_id(spec.page_index, "shp", q, f"ellipse:{key_col}"), quad=q, z=p.z, geometry=ShapeGeometry.ELLIPSE, fill=fill, stroke=stroke, stroke_width=sw))
        elif it.kind == "rrect":
            q = rel_quad(*it.geom[0])
            out.append(ShapeElement(id=make_element_id(spec.page_index, "shp", q, f"rrect:{key_col}"), quad=q, z=p.z, geometry=ShapeGeometry.ROUNDED_RECT, fill=fill, stroke=stroke, stroke_width=sw, corner_radius=it.geom[1] / src_w))
        else:
            rel = it.rel
            xs = [v for sg in rel if sg[0] != 3 for v in sg[1::2]]
            ys = [v for sg in rel if sg[0] != 3 for v in sg[2::2]]
            if (max(xs) - min(xs)) + (max(ys) - min(ys)) <= 1e-6:
                continue
            q = Quad.from_rect(min(xs), min(ys), max(xs), max(ys))
            out.append(ShapeElement(id=make_element_id(spec.page_index, "shp", q, f"path:{key_col}:{len(rel)}:{round(xs[0], 5)}"), quad=q, z=p.z, geometry=ShapeGeometry.PATH, fill=fill, stroke=stroke, stroke_width=sw, path=rel))
        taken.add(p.z)
    note = _leftover_note(paths)
    if note:
        notes.append(note)
    return out, taken


# ---------- s3.tables ----------


def _tables(shapes: list[ShapeElement], texts: list[TextLine], spec: PageSpec, c: dict) -> tuple[list[TableRegion], set[str]]:
    """由框線找表格區：水平與垂直線段（直線或很細的矩形）連成格線。③只交區域與格線位置，列欄結構在④。
    回傳（表格區、被表格吸收的形狀編號：格線本身與儲存格底色）。"""
    thin = c.get("table_rule_max_width", 0.004)
    tol = c.get("table_join_tolerance", 0.004)
    hs, vs = [], []  # (x0, y0, x1, y1, 形狀)
    for sh in shapes:
        l, t, r, b = sh.quad.bbox
        if sh.geometry == ShapeGeometry.LINE:
            if abs(b - t) <= tol and r - l > tol:
                hs.append((l, (t + b) / 2, r, (t + b) / 2, sh))
            elif abs(r - l) <= tol and b - t > tol:
                vs.append(((l + r) / 2, t, (l + r) / 2, b, sh))
        elif sh.geometry == ShapeGeometry.RECT:
            if b - t <= thin and r - l > 4 * (b - t):
                hs.append((l, (t + b) / 2, r, (t + b) / 2, sh))
            elif r - l <= thin and b - t > 4 * (r - l):
                vs.append(((l + r) / 2, t, (l + r) / 2, b, sh))
            elif sh.stroke is not None and sh.fill is None:  # 每格一個描邊矩形的畫法
                hs += [(l, t, r, t, sh), (l, b, r, b, sh)]
                vs += [(l, t, l, b, sh), (r, t, r, b, sh)]
    if len(hs) < 2 or len(vs) < 2:
        return [], set()
    segs = hs + vs
    parent = list(range(len(segs)))

    def find(i: int) -> int:
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    for i, (hx0, hy, hx1, _, _) in enumerate(hs):
        for j, (vx, vy0, _, vy1, _) in enumerate(vs, start=len(hs)):
            if hx0 - tol <= vx <= hx1 + tol and vy0 - tol <= hy <= vy1 + tol:
                parent[find(i)] = find(j)
    groups: dict[int, list[int]] = {}
    for i in range(len(segs)):
        groups.setdefault(find(i), []).append(i)

    def cluster(values: list[float]) -> list[float]:
        out: list[list[float]] = []
        for v in sorted(values):
            if out and v - out[-1][-1] <= tol:
                out[-1].append(v)
            else:
                out.append([v])
        return [sum(g) / len(g) for g in out]

    regions: list[TableRegion] = []
    used: set[str] = set()
    for members in groups.values():
        H = [segs[i] for i in members if i < len(hs)]
        V = [segs[i] for i in members if i >= len(hs)]
        ys = cluster([s_[1] for s_ in H])
        xs = cluster([s_[0] for s_ in V])
        if len(ys) < 2 or len(xs) < 2 or (len(ys) - 1) * (len(xs) - 1) < 2:
            continue
        l, r, t, b = min(xs), max(xs), min(ys), max(ys)
        q = Quad.from_rect(l, t, r, b)
        inside = [tl for tl in texts if l <= tl.quad.center.x <= r and t <= tl.quad.center.y <= b]
        if not inside:
            continue  # 沒有字的格子多半是裝飾，不當表格
        first = (H + V)[0][4]
        col = first.stroke or first.fill
        width = first.stroke_width if first.stroke_width else min(sh.quad.bbox[3] - sh.quad.bbox[1] for *_, sh in H if sh.geometry == ShapeGeometry.RECT) if any(sh.geometry == ShapeGeometry.RECT for *_, sh in H) else None
        fills = []
        for sh in shapes:
            sl, st, sr, sb = sh.quad.bbox
            if sh.geometry == ShapeGeometry.RECT and sh.fill is not None and sh.stroke is None and sb - st > thin and sr - sl > thin and l - tol <= sl and sr <= r + tol and t - tol <= st and sb <= b + tol:
                fills.append((sh.quad, sh.fill))
                used.add(sh.id)
        rid = make_element_id(spec.page_index, "tbl", q, f"{len(xs)}x{len(ys)}")
        regions.append(TableRegion(
            id=rid, quad=q, h_lines=ys, v_lines=xs, ruled=True,
            segments=[(s_[0], s_[1], s_[2], s_[3]) for s_ in H + V], line_color=col, line_width=width, cell_fills=fills, confidence=0.9,
            z=min(s_[4].z for s_ in H + V),
        ))
        used.update(s_[4].id for s_ in H + V)
    return regions, used


def _mark_shadows(images: list[ImageElement], shapes: list[ShapeElement]) -> None:
    """軟體常把形狀的陰影壓成一張略大的點陣圖墊在形狀下面：標記出來。"""
    step = f"{NAME}.pdf_images@{VERSION}"
    for i, im in enumerate(images):
        il, it, ir, ib = im.quad.bbox
        for s in shapes:
            sl, st, sr, sb = s.quad.bbox
            margins = (sl - il, st - it, ir - sr, ib - sb)
            if s.z > im.z and all(-0.002 <= d <= 0.03 for d in margins) and (ir - il) * (ib - it) <= 1.6 * max((sr - sl) * (sb - st), 1e-9):
                images[i] = im.model_copy(update={"kind": measured(ImageKind.SHADOW, 0.7, Provenance.INFERRED, step)})
                break


# ---------- s3.background ----------


def _background(reader: pdf.PageReader, spec: PageSpec, c: dict, store: Store, hide_z: set[int], page_color: Color | None) -> Background:
    dpi = spec.working_image_size[0] / (math.hypot(spec.source_corners[1].x - spec.source_corners[0].x, spec.source_corners[1].y - spec.source_corners[0].y) / 72.0)
    rendered = reader.render(dpi, hide_text=True, hide_z=hide_z)
    base = (page_color.r, page_color.g, page_color.b) if page_color else (255, 255, 255)
    arr = np.asarray(rendered, dtype=np.int16)
    diff = np.abs(arr - np.array(base, dtype=np.int16)).max(axis=2)
    ratio = float((diff > c["background_blank_tolerance"]).mean())
    color = page_color or Color(r=255, g=255, b=255)
    if ratio < c["background_blank_ratio"]:
        return Background(color=color, method="pdf_hide_objects")
    blob = store.put_blob(_png(rendered), "image/png", "png")
    return Background(image=blob, color=color, method="pdf_hide_objects")


# ====================================================================== 修正單


def _apply_corrections(elements: ElementList, corrections: list[Correction]) -> ElementList:
    """③接受的修正：改某一行的文字內容（field="text"）。使用者修正之後不會被覆蓋。"""
    if not corrections:
        return elements
    by_id = {c.element_id: c for c in corrections if c.field == "text" and c.element_id}
    if not by_id:
        return elements
    texts = []
    for line in elements.texts:
        c = by_id.get(line.id)
        if c is None or not line.runs:
            texts.append(line)
            continue
        head = line.runs[0].model_copy(update={"text": str(c.new_value), "quad": line.quad, "char_x": []})
        texts.append(line.model_copy(update={"runs": [head]}))
    return elements.model_copy(update={"texts": texts})
