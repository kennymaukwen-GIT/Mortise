"""③ 辨識：收頁面規格，交出元素清單 ElementList。只記看得到的事實，不吸附、不定案。

兩條產線，交出同一種元素清單：
  擷取產線（原生 PDF）：直接讀檔案
    s3.pdf_text    讀逐字資料，合併成文字行與樣式段
    s3.pdf_images  讀內嵌圖片
    s3.pdf_shapes  讀向量路徑，認出矩形與直線；其餘留在底圖
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
    Background, Color, Correction, ElementList, FontCandidate, ImageElement, ImageKind, InputType, PageSpec, Point,
    ProductionLine, Provenance, Quad, ShapeElement, ShapeGeometry, SourceDoc, TextLine, TextRun, exact, make_element_id, measured,
)
from ..config import Config
from ..pipeline.store import Store
from ..tools import pdfium as pdf
from ..tools.fonts import clean_font_name, is_cjk, split_style
from ..tools.geometry import to_point

NAME = "s3"
VERSION = "1"

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
        shapes, shape_z, page_color = _shapes(reader, spec, m, src_w, c, notes)
        _mark_shadows(images, shapes)
        background = _background(reader, spec, c, store, image_z | shape_z, page_color)
    finally:
        reader.close()
        pdoc.close()
    if any(t.hidden for t in texts):
        notes.append("這一頁有不可見文字（多半是 OCR 文字層），已標記為隱藏，不會輸出。")
    return ElementList(
        page_index=spec.page_index, production_line=ProductionLine.EXTRACT,
        texts=texts, images=images, shapes=shapes, background=background, notes=notes,
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


def _shapes(reader: pdf.PageReader, spec: PageSpec, m: list[float], src_w: float, c: dict, notes: list[str]) -> tuple[list[ShapeElement], set[int], Color | None]:
    out: list[ShapeElement] = []
    taken: set[int] = set()
    page_color: Color | None = None
    page_area = spec.aspect_ratio
    paths = reader.paths()
    leftover = 0
    for p in paths:
        fill, stroke = _color(p.fill), _color(p.stroke)
        if fill is None and stroke is None:
            continue
        rect = _rect_of(p, tol=0.02)
        if rect is not None:
            l, b, r, t = rect
            a, bq = to_point(m, l, t), to_point(m, r, b)
            q = Quad.from_rect(min(a.x, bq.x), min(a.y, bq.y), max(a.x, bq.x), max(a.y, bq.y))
            full = (q.width * q.height) / page_area >= c["full_page_coverage"]
            if full and fill is not None and fill.a >= 0.999 and not out and stroke is None:
                page_color = fill  # 最底層的整頁色塊：當成頁面底色，不輸出成形狀
                taken.add(p.z)
                continue
            out.append(
                ShapeElement(
                    id=make_element_id(spec.page_index, "shp", q, f"rect:{fill.hex if fill else ''}:{stroke.hex if stroke else ''}"), quad=q, z=p.z,
                    geometry=ShapeGeometry.RECT, fill=fill, stroke=stroke, stroke_width=(p.stroke_width / src_w) if stroke else None,
                )
            )
            taken.add(p.z)
        elif len(p.points) == 2 and stroke is not None and all(pt[0] != SEG_BEZIERTO for pt in p.points):
            a, bq = to_point(m, p.points[0][1], p.points[0][2]), to_point(m, p.points[1][1], p.points[1][2])
            q = Quad(tl=a, tr=bq, br=bq, bl=a)
            out.append(
                ShapeElement(
                    id=make_element_id(spec.page_index, "shp", q, f"line:{stroke.hex}"), quad=q, z=p.z,
                    geometry=ShapeGeometry.LINE, stroke=stroke, stroke_width=p.stroke_width / src_w,
                )
            )
            taken.add(p.z)
        else:
            leftover += 1
    if leftover:
        notes.append(f"有 {leftover} 個較複雜的向量圖形尚未轉成可編輯形狀，已留在底圖中。")
    return out, taken, page_color


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
