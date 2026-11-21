"""③ 辨識：收頁面規格，交出元素清單 ElementList。只記看得到的事實，不吸附、不定案。

兩條產線，交出同一種元素清單：
  擷取產線（原生 PDF）：直接讀檔案
    s3.pdf_text    讀逐字資料，合併成文字行與樣式段
    s3.pdf_images  讀內嵌圖片
    s3.pdf_shapes  讀向量路徑：認出矩形、圓角矩形、橢圓、直線；其他較簡單的保留成自訂路徑；太複雜的留在底圖
    s3.tables      由框線找表格區（區域與格線位置；列欄結構在④）
    s3.background  隱藏已擷取的物件後重繪，得到底圖（不是生成）
  影像產線（掃描、截圖、照片）：
    s3.image_stub  尚未實作文字與區域辨識：整頁當成一張圖片交出，並在備註中說明
"""

from __future__ import annotations

import io
import math

import numpy as np
from PIL import Image

from ..artifacts import (
    Background, Color, Correction, ElementList, FontCandidate, ImageElement, ImageKind, InputType, PageSpec, Point, TableRegion,
    ProductionLine, Provenance, Quad, ShapeElement, ShapeGeometry, SourceDoc, TextLine, TextRun, exact, make_element_id, measured,
)
from ..config import Config
from ..pipeline.store import Store
from ..tools import pdfium as pdf
from ..tools.fonts import clean_font_name, is_cjk, split_style
from ..tools.geometry import to_point

NAME = "s3"
VERSION = "2"

SEG_LINETO, SEG_BEZIERTO, SEG_MOVETO = 0, 1, 2


def _png(im: Image.Image) -> bytes:
    buf = io.BytesIO()
    im.save(buf, "PNG")
    return buf.getvalue()


def run(doc: SourceDoc, spec: PageSpec, cfg: Config, corrections: list[Correction], store: Store) -> ElementList:
    ref = doc.pages[spec.page_index]
    kind = ref.input_type.value
    if doc.media_type == "application/pdf" and kind in (InputType.NATIVE_PDF, InputType.MIXED_PDF):
        elements = _extract_line(doc, spec, cfg, store)
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
        notes=["影像產線（文字偵測、文字辨識、區域分類、背景修補）尚未實作：這一頁整頁以圖片輸出，文字不可編輯。"],
    )


# ====================================================================== 擷取產線（原生 PDF）


def _extract_line(doc: SourceDoc, spec: PageSpec, cfg: Config, store: Store) -> ElementList:
    c = cfg.section("recognize")
    m = spec.source_to_page
    src_w = math.hypot(spec.source_corners[1].x - spec.source_corners[0].x, spec.source_corners[1].y - spec.source_corners[0].y)
    notes: list[str] = []
    pdoc = pdf.open_document(str(store.blob_path(doc.source)))
    reader = pdf.PageReader(pdoc, spec.page_index)
    try:
        texts = read_text_lines(reader, spec.page_index, m, src_w, c)
        images, image_z = _images(reader, spec, m, c, store, notes)
        shapes, shape_z, page_color, _ = _shapes(reader, spec, m, src_w, c, notes)
        _mark_shadows(images, shapes)
        # 底色物件不隱藏：底圖要帶著底色，否則隱藏後會變成白底（深色投影片就會整頁變白）
        background = _background(reader, spec, c, store, image_z | shape_z, page_color)
        tables, absorbed = _tables(shapes, [t for t in texts if not t.hidden], spec, c)
        shapes = [sh for sh in shapes if sh.id not in absorbed]
    finally:
        reader.close()
        pdoc.close()
    if any(t.hidden for t in texts):
        notes.append("這一頁有不可見文字（多半是 OCR 文字層），已標記為隱藏，不會輸出。")
    return ElementList(
        page_index=spec.page_index, production_line=ProductionLine.EXTRACT,
        texts=texts, images=images, shapes=shapes, table_regions=tables, background=background, notes=notes,
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
            radii.append((abs(end[0] - prev[0]) + abs(end[1] - prev[1])) / 2)
        prev = end
    rad = sum(radii) / 4
    if rad <= tol or max(radii) - min(radii) > max(0.15 * rad, tol) or rad > min(r - l, t - b) / 2 + tol:
        return None
    return (l, b, r, t), rad


def _shapes(reader: pdf.PageReader, spec: PageSpec, m: list[float], src_w: float, c: dict, notes: list[str]) -> tuple[list[ShapeElement], set[int], Color | None, set[int]]:
    """向量路徑 → 形狀。矩形、圓角矩形、橢圓、直線認成基本幾何；其他較簡單的路徑保留成自訂路徑；
    太複雜的（例如上千條路徑組成的插畫）留在底圖。回傳（形狀、已取出的物件、頁面底色、底色物件）。"""
    out: list[ShapeElement] = []
    taken: set[int] = set()
    page_color: Color | None = None
    page_color_z: set[int] = set()
    page_area = spec.aspect_ratio
    paths = reader.paths()
    tol = 0.02
    max_paths = c.get("max_vector_paths", 300)
    max_segs = c.get("max_path_segments", 400)
    complex_paths = [p for p in paths if (p.fill or p.stroke) and _rect_of(p, tol) is None and len(p.points) > 2]
    allow_free = len(complex_paths) <= max_paths
    leftover = 0

    def rel_quad(l: float, b: float, r: float, t: float) -> Quad:
        a, bq = to_point(m, l, t), to_point(m, r, b)
        return Quad.from_rect(min(a.x, bq.x), min(a.y, bq.y), max(a.x, bq.x), max(a.y, bq.y))

    for p in paths:
        fill, stroke = _color(p.fill), _color(p.stroke)
        if fill is None and stroke is None:
            continue
        sw = (p.stroke_width / src_w) if stroke else None
        rect = _rect_of(p, tol=tol)
        segs = _segments(p)
        key_col = f"{fill.hex if fill else ''}:{stroke.hex if stroke else ''}"
        if rect is not None:
            q = rel_quad(*rect)
            full = (q.width * q.height) / page_area >= c["full_page_coverage"]
            if full and fill is not None and fill.a >= 0.999 and not out and stroke is None:
                page_color = fill  # 最底層的整頁色塊：當成頁面底色，不輸出成形狀
                page_color_z.add(p.z)
                continue
            out.append(ShapeElement(id=make_element_id(spec.page_index, "shp", q, f"rect:{key_col}"), quad=q, z=p.z, geometry=ShapeGeometry.RECT, fill=fill, stroke=stroke, stroke_width=sw))
            taken.add(p.z)
            continue
        if len(p.points) == 2 and stroke is not None and all(pt[0] != SEG_BEZIERTO for pt in p.points):
            a, bq = to_point(m, p.points[0][1], p.points[0][2]), to_point(m, p.points[1][1], p.points[1][2])
            q = Quad(tl=a, tr=bq, br=bq, bl=a)
            out.append(ShapeElement(id=make_element_id(spec.page_index, "shp", q, f"line:{stroke.hex}"), quad=q, z=p.z, geometry=ShapeGeometry.LINE, stroke=stroke, stroke_width=sw))
            taken.add(p.z)
            continue
        ell = _ellipse_of(segs, tol)
        if ell is not None:
            q = rel_quad(*ell)
            out.append(ShapeElement(id=make_element_id(spec.page_index, "shp", q, f"ellipse:{key_col}"), quad=q, z=p.z, geometry=ShapeGeometry.ELLIPSE, fill=fill, stroke=stroke, stroke_width=sw))
            taken.add(p.z)
            continue
        rr = _rounded_rect_of(segs, tol)
        if rr is not None:
            q = rel_quad(*rr[0])
            out.append(ShapeElement(id=make_element_id(spec.page_index, "shp", q, f"rrect:{key_col}"), quad=q, z=p.z, geometry=ShapeGeometry.ROUNDED_RECT, fill=fill, stroke=stroke, stroke_width=sw, corner_radius=rr[1] / src_w))
            taken.add(p.z)
            continue
        if allow_free and len(segs) <= max_segs:
            rel: list[list[float]] = []
            for sg in segs:
                if sg[0] == 3:
                    rel.append([3])
                    continue
                coords = []
                for x, y in zip(sg[1::2], sg[2::2]):
                    pt = to_point(m, x, y)
                    coords += [pt.x, pt.y]
                rel.append([sg[0]] + coords)
            xs = [v for sg in rel if sg[0] != 3 for v in sg[1::2]]
            ys = [v for sg in rel if sg[0] != 3 for v in sg[2::2]]
            if not xs or (max(xs) - min(xs)) + (max(ys) - min(ys)) <= 1e-6:
                continue
            q = Quad.from_rect(min(xs), min(ys), max(xs), max(ys))
            out.append(ShapeElement(id=make_element_id(spec.page_index, "shp", q, f"path:{key_col}:{len(rel)}:{round(xs[0], 5)}"), quad=q, z=p.z, geometry=ShapeGeometry.PATH, fill=fill, stroke=stroke, stroke_width=sw, path=rel))
            taken.add(p.z)
            continue
        leftover += 1
    if leftover:
        notes.append(f"有 {leftover} 個向量圖形太複雜，沒有轉成可編輯形狀，已留在底圖中。")
    return out, taken, page_color, page_color_z


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
