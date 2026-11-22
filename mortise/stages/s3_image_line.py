"""③ 辨識的影像產線（掃描、截圖、照片）：把 mortise/recognition 的各模組串起來，交出元素清單。

順序是「剝洋蔥」：先處理最可靠的文字，再處理剩下的區域，最後補回底圖。
  s3.ocr          文字偵測與文字辨識（PP-OCRv6，保留每個字的候選字與機率）
  s3.text_attr    文字屬性：筆畫遮罩、顏色、字級與字型一起解（檢索加重繪比對）、粗斜體、基線
  s3.regions      扣掉文字後的區域：照片、插畫、形狀（擬合成基本幾何；照片上後來疊上去的平塗圖形也拆出來）、表格格線；遮罩可以重疊
  s3.crop         裁出照片與插畫（被文字、被疊在上面的形狀蓋住的部分先補再裁；補的部分屬於生成，記在 notes）
  s3.vectorize    插畫（標誌、圖示、圖表）描成向量路徑；重畫驗證過關才交出向量圖，否則只留圖片。照片不描
  s3.background   擦掉所有元素後補回底圖；純色與漸層用擬合，紋理用修補（修補屬於生成，會記錄位置）
這裡只負責轉換座標與產物格式；量測與感知的細節在各模組。所有數值都是原始量測值，不吸附、不定案。
"""

from __future__ import annotations

import io
import threading
from typing import Optional

import numpy as np
from PIL import Image

from ..artifacts import (
    Background, Color, ElementList, FontCandidate, ImageElement, ImageKind, PageSpec, Point, ProductionLine, Provenance, Quad,
    ShapeElement, ShapeGeometry, TableRegion, TextLine, TextRun, VectorGraphic, make_element_id, measured,
)
from ..config import Config
from ..pipeline.store import Store

NAME = "s3"
STEP = "s3.image@1"
VECTOR_STEP = "s3.vectorize@1"

_ENGINE = None
_FONTS = None
_LOCK = threading.Lock()


def _engine(params: Optional[dict]):
    global _ENGINE
    with _LOCK:
        if _ENGINE is None:
            from ..recognition.ocr import OcrEngine

            _ENGINE = OcrEngine(params)
        return _ENGINE


def _fonts():
    global _FONTS
    with _LOCK:
        if _FONTS is None:
            from ..recognition.fontlib import FontLibrary

            _FONTS = FontLibrary.default()
        return _FONTS


def _png(arr: np.ndarray) -> bytes:
    buf = io.BytesIO()
    Image.fromarray(np.ascontiguousarray(arr)).save(buf, "PNG")
    return buf.getvalue()


def _quad(pts, W: float) -> Quad:
    p = [Point(x=float(x) / W, y=float(y) / W) for x, y in np.asarray(pts, dtype=np.float64)[:4]]
    return Quad(tl=p[0], tr=p[1], br=p[2], bl=p[3])


def _rgb(c) -> Color:
    return Color(r=int(c[0]), g=int(c[1]), b=int(c[2]))


def run(spec: PageSpec, cfg: Config, store: Store, ai=None) -> ElementList:
    import cv2

    from ..recognition import background as bgmod
    from ..recognition import regions as regmod
    from ..recognition import text_attributes as ta

    c = cfg.section("recognize")
    work = np.asarray(Image.open(store.blob_path(spec.working_image)).convert("RGB"))
    # 量顏色、擦字、補底圖都用工作影像：只有白紙類頁面會被增強（白平衡、去光照），這時增強後的顏色反而比原圖準；
    # 其他頁面工作影像就是原圖。未增強的原圖（spec.color_image）留給前端預覽。
    color = work
    H, W = work.shape[:2]
    notes: list[str] = []
    page = spec.page_index

    # ---------- s3.ocr ----------
    ocr_lines = _drop_implausible(_engine(c.get("ocr_params")).read(work), c, notes)
    bullets = _find_bullets(work, ocr_lines, c)
    if bullets:
        notes.append(f"有 {len(bullets)} 行左側的小圓點、方塊或短線認成項目符號。")
    ai_chars: dict[int, list[int]] = {}
    if ai is not None and ai.feature("ocr_arbitration"):
        ai_chars = _arbitrate(ai, Image.fromarray(work), ocr_lines, spec, c)
        if ai_chars:
            notes.append(f"有 {sum(len(v) for v in ai_chars.values())} 個不確定的字由大型 AI 在候選字中挑選。")
    # 繁體文件：辨識模型讀成的簡體字改回繁體（字形幾乎相同的「内／內」「参／參」）
    from ..recognition import script_prior

    trad_fixed: dict[int, list[int]] = {}
    if c.get("traditional_prior", True) and script_prior.available() and script_prior.page_is_traditional([ol.text for ol in ocr_lines]):
        for i, ol in enumerate(ocr_lines):
            if len(ol.chars) != len(ol.text):
                continue
            new_text, changed = script_prior.to_traditional(ol.text, [[cd.text for cd in ch.candidates] for ch in ol.chars])
            if changed:
                for k in changed:
                    ol.chars[k].text = new_text[k]
                ol.text = new_text
                trad_fixed[i] = changed
        if trad_fixed:
            notes.append(f"有 {sum(len(v) for v in trad_fixed.values())} 個字依繁體文件的先驗由簡體改回繁體（例如「内」改「內」），請確認。")
    fonts = _fonts()

    # ---------- s3.text_attr ----------
    texts: list[TextLine] = []
    text_mask = np.zeros((H, W), dtype=bool)
    dilate = int(c.get("text_mask_dilate_px", 2))
    low_text = 0
    for i, ol in enumerate(ocr_lines):
        if not ol.text.strip():
            continue
        attrs = None
        try:
            attrs = ta.measure_line(color, ol, fonts)
        except Exception as e:  # 單行量測失敗不中斷整頁
            notes.append(f"有一行文字的屬性量不出來（{type(e).__name__}），改用粗估值。")
        line = _text_line(page, i, ol, attrs, W)
        if i in ai_chars:
            line = line.model_copy(update={"ai_chars": ai_chars[i]})
        if i in bullets:
            line = _prepend_bullet(line, *bullets[i], W)
        texts.append(line)
        if (ol.score or 0) < c.get("ocr_low_score", 0.8):
            low_text += 1
        # 筆畫遮罩貼回整張圖（擦字、找區域都要用）
        if attrs is not None and attrs.mask is not None and attrs.mask.size:
            warped = cv2.warpPerspective(attrs.mask.astype(np.uint8) * 255, np.asarray(attrs.mask_to_image, dtype=np.float64), (W, H), flags=cv2.INTER_NEAREST)
            text_mask |= warped > 127
        else:
            poly = np.round(np.asarray(ol.quad, dtype=np.float64)).astype(np.int32)
            m = np.zeros((H, W), dtype=np.uint8)
            cv2.fillPoly(m, [poly], 255)
            text_mask |= m > 0
    if dilate > 0 and text_mask.any():
        k = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (2 * dilate + 1, 2 * dilate + 1))
        text_mask = cv2.dilate(text_mask.astype(np.uint8), k) > 0
    if low_text:
        notes.append(f"有 {low_text} 行文字的辨識信心值偏低，請確認內容。")

    # ---------- s3.regions ----------
    # 設定檔 [recognize] 裡以 region_ 開頭的值蓋過 regions.py 的預設（去掉前綴後同名）
    region_params = {k[7:]: v for k, v in c.items() if k.startswith("region_")}
    regions = regmod.find_regions(work, text_mask, params=region_params or None, text_quads=[np.asarray(ol.quad) for ol in ocr_lines])
    region_mask = np.zeros((H, W), dtype=bool)
    for rg in regions:
        x0, y0, x1, y1 = rg.bbox
        region_mask[y0:y1, x0:x1] |= rg.mask.astype(bool)

    # ---------- s3.crop：被文字蓋住的照片先擦字再裁 ----------
    photos = [rg for rg in regions if rg.kind in ("photo", "illustration")]
    text_free = None
    if photos and any(text_mask[rg.bbox[1]:rg.bbox[3], rg.bbox[0]:rg.bbox[2]].any() for rg in photos):
        text_free = bgmod.reconstruct_background(color, text_mask).image
    # 被上層的形狀或插畫（例如從照片裡拆出來的平塗圖形）蓋住的地方也先補掉，否則移動形狀會露出殘影。
    # 補出來的像素屬於生成，記在 notes（圖片元素本身沒有生成範圍的欄位）。
    # 補好的影像只給這張照片自己裁圖用：疊在上面的插畫要從沒補過的影像裁，不然裁到的是補出來的照片。
    covered_px: dict[int, tuple[int, int]] = {}
    filled_src: dict[int, np.ndarray] = {}
    for rg in photos:
        cover = np.zeros((H, W), dtype=bool)
        for up in regions:
            if up is rg or up.z <= rg.z or up.kind not in ("shape", "illustration"):
                continue
            ux0, uy0, ux1, uy1 = up.bbox
            if ux1 <= rg.bbox[0] or ux0 >= rg.bbox[2] or uy1 <= rg.bbox[1] or uy0 >= rg.bbox[3]:
                continue
            cover[uy0:uy1, ux0:ux1] |= up.mask.astype(bool)
        if not cover.any():
            continue
        base = text_free if text_free is not None else color
        filled, gen_mask = regmod.fill_covered(base, rg, cover, region_params or None)
        n_gen = int(gen_mask.sum())
        if n_gen:
            filled_src[id(rg)] = filled
            covered_px[id(rg)] = (n_gen, int(rg.mask.sum()))

    images: list[ImageElement] = []
    shapes: list[ShapeElement] = []
    tables: list[TableRegion] = []
    vectors: list[VectorGraphic] = []
    vector_params = {k: v for k, v in c.items() if k.startswith("vector_")}
    for rg in regions:
        x0, y0, x1, y1 = rg.bbox
        q = Quad.from_rect(x0 / W, y0 / W, x1 / W, y1 / W)
        if rg.kind in ("photo", "illustration"):
            rgba = regmod.crop_region(color, rg, inpainted=filled_src.get(id(rg), text_free))
            blob = store.put_blob(_png(rgba), "image/png", "png")
            kind = ImageKind.PHOTO if rg.kind == "photo" else ImageKind.ILLUSTRATION
            images.append(ImageElement(
                id=make_element_id(page, "img", q, blob.sha256[:12]), quad=q, z=10 + rg.z, image=blob,
                pixel_size=(rgba.shape[1], rgba.shape[0]), kind=measured(kind, rg.confidence, Provenance.MEASURED, STEP),
            ))
            if id(rg) in covered_px:
                n_gen, n_all = covered_px[id(rg)]
                what = "照片" if rg.kind == "photo" else "插畫"
                notes.append(f"{what}（{images[-1].id}）有 {n_gen} 個像素（約 {n_gen / max(n_all, 1):.0%}）原本被疊在上面的圖形蓋住，"
                             "已補成周圍的內容（平滑內插加上照片本身的顆粒）；這部分是生成的，不是原圖。")
            # ---------- s3.vectorize：插畫描成向量（照片不描）；對應的圖片照樣留在 images 裡
            if rg.kind == "illustration" and c.get("vector_enabled", True):
                trace_in = rgba
                if rg.features.get("overlay") == 1.0:  # 從照片上拆出來的圖形：透明處底下是照片，先換成單色再描
                    trace_in = np.dstack([regmod.isolate_overlay(rgba[..., :3], rgba[..., 3] >= 128), rgba[..., 3]])
                vg = _trace_vector(page, images[-1], trace_in, (x0, y0), W, vector_params, notes)
                if vg is not None:
                    vectors.append(vg)
        elif rg.kind == "shape" and rg.shape is not None:
            shapes.append(_shape(page, rg, W))
        elif rg.kind == "table" and rg.table is not None:
            tables.append(_table(page, rg, W))

    # ---------- s3.background ----------
    remove = text_mask | region_mask
    bg = bgmod.reconstruct_background(color, remove)
    gen = [Quad.from_rect(a / W, b / W, cc / W, d / W) for a, b, cc, d in bg.generated_boxes]
    flat = _rgb(bg.flat_color) if bg.flat_color is not None else _rgb(np.median(bg.image.reshape(-1, 3), axis=0))
    if bg.is_blank:
        background = Background(color=flat, method=bg.model)
    else:
        background = Background(image=store.put_blob(_png(bg.image), "image/png", "png"), color=flat, generated_regions=gen, method=bg.model)
    if gen:
        notes.append(f"底圖有 {len(gen)} 個區域是修補（生成）出來的，原本被文字或圖片蓋住。")
    notes += list(bg.notes)

    if not texts and not images and not shapes:
        notes.append("這一頁沒有找到文字或可分離的元素，整頁以底圖輸出。")
    return ElementList(page_index=page, production_line=ProductionLine.IMAGE, texts=texts, images=images, shapes=shapes, table_regions=tables, background=background, notes=notes,
                       vectors=vectors)


def _trace_vector(page: int, img: ImageElement, rgba: np.ndarray, origin_px: tuple[int, int], W: float, params: dict, notes: list[str]) -> Optional[VectorGraphic]:
    """把一張插畫描成向量圖。輸入是裁好的 RGBA（透明度當遮罩）；重畫驗證過關才回傳，否則只在 notes 說明原因。
    向量圖與圖片互指（image_id），四角與圖層順序相同；路徑換成頁面相對座標。所有數值都是量測值。"""
    from ..recognition import vectorize as vz

    h, w = rgba.shape[:2]
    if min(h, w) < int(params.get("vector_min_size_px", 12)):
        return None
    try:
        res = vz.vectorize(rgba[..., :3], rgba[..., 3] >= 128, params)
    except Exception as e:  # 描圖失敗不中斷整頁：這張圖照常以圖片輸出
        notes.append(f"有一張插畫描成向量時出錯（{type(e).__name__}），以圖片輸出。")
        return None
    if not res.accepted or not res.paths:
        # 說明取真正的原因（「放細門檻重描了幾輪」只是過程）
        why = next((n for n in res.notes if "未通過" in n or "不適合" in n), res.notes[0] if res.notes else "重畫驗證未通過。")
        notes.append(f"有一張插畫沒有描成向量，以圖片輸出：{why}")
        return None
    paths = vz.to_vector_paths(res, (origin_px[0] / W, origin_px[1] / W), 1.0 / W)
    n_stroke = sum(1 for p in res.paths if p.stroke is not None)
    kind = "chart" if n_stroke >= 3 and n_stroke >= 0.5 * len(res.paths) else "illustration"
    info = [f"描圖：{len(paths)} 條路徑、{res.node_count} 個節點，重畫色差 ΔE {res.render_error:.2f}。"]
    return VectorGraphic(
        id=make_element_id(page, "vec", img.quad, img.id), image_id=img.id, quad=img.quad, z=img.z, paths=paths,
        origin=measured("traced", float(res.confidence), Provenance.MEASURED, VECTOR_STEP), kind=kind,
        render_error=float(res.render_error), notes=info + [n for n in res.notes if n],
    )


def _text_line(page: int, i: int, ol, attrs, W: float) -> TextLine:
    text = ol.text
    if attrs is None:
        q = _quad(ol.quad, W)
        size = q.height * 0.8
        run = TextRun(
            text=text, quad=q, font_size=measured(size, 0.2, Provenance.MEASURED, STEP, error=size * 0.25),
            font_candidates=[], bold=measured(False, 0.3, Provenance.MEASURED, STEP), italic=measured(False, 0.3, Provenance.MEASURED, STEP),
            color=measured(Color(r=0, g=0, b=0), 0.2, Provenance.MEASURED, STEP),
        )
        return TextLine(
            id=make_element_id(page, "t", q, text), quad=q, z=10000 + i, baseline=(q.bl, q.br), runs=[run],
            text_confidence=ol.score, char_candidates=[[(cd.text, cd.prob) for cd in ch.candidates] for ch in ol.chars],
            notes=["文字屬性量測失敗，字級與顏色為粗估值。"],
        )
    q = _quad(attrs.quad, W)
    b0, b1 = np.asarray(attrs.baseline, dtype=np.float64)
    # 非空白字的序號 → 在 text 中的位置，用來把每個字的起點分給各段
    nonspace = [k for k, ch in enumerate(text) if ch.strip()]
    xs = list(attrs.char_x)
    runs: list[TextRun] = []
    # 各段的文字範圍要連起來蓋住整行：量測交出的段落不含段與段之間的空白（例如「12 – 14」與「JULY 2027」之間），
    # 只取 text[start:end] 會把那個空白丟掉（實際發生過：「12 – 14 JULY 2027」變成「12 – 14JULY 2027」）。
    # 所以每段從自己的起點延伸到下一段的起點，第一段從行首開始、最後一段到行尾。
    ordered = sorted((r for r in attrs.runs if r.end > r.start), key=lambda r: r.start)
    spans = []
    for n, r in enumerate(ordered):
        a = 0 if n == 0 else r.start
        b = ordered[n + 1].start if n + 1 < len(ordered) else len(text)
        spans.append((r, a, b))
    for r, a, b in spans:
        seg = text[a:b]
        if not seg:
            continue
        idx = [n for n, k in enumerate(nonspace) if a <= k < b]
        char_x = [xs[n] / W for n in idx if n < len(xs)]
        # 這一段的四角：沿基線取這段字的範圍
        if idx and len(xs) > max(idx):
            s0 = xs[idx[0]]
            s1 = xs[idx[-1] + 1] if idx[-1] + 1 < len(xs) else None
        else:
            s0, s1 = None, None
        rq = _sub_quad(attrs.quad, b0, b1, s0, s1, W) if s0 is not None else q
        est = r.font_size_px
        runs.append(TextRun(
            text=seg, quad=rq,
            font_size=measured(float(est.value) / W, float(est.confidence), Provenance.MEASURED, STEP, error=float(est.error) / W),
            font_candidates=[FontCandidate(name=fm.family, raw_name=f"{fm.family} {fm.style}".strip(), score=float(fm.score)) for fm in r.fonts],
            bold=measured(bool(r.bold[0]), float(r.bold[1]), Provenance.MEASURED, STEP),
            italic=measured(bool(r.italic[0]), float(r.italic[1]), Provenance.MEASURED, STEP),
            color=measured(_rgb(r.color), float(r.color_confidence), Provenance.MEASURED, STEP),
            char_spacing=(float(r.char_spacing_px.value) / W) if r.char_spacing_px is not None else None,
            char_x=char_x,
            stroke_ratio=(float(r.stroke_width_px.value) / float(est.value)) if getattr(r, "stroke_width_px", None) is not None and est.value > 0 else None,
        ))
    if not runs:
        return _text_line(page, i, ol, None, W)
    return TextLine(
        id=make_element_id(page, "t", q, text), quad=q, z=10000 + i,
        baseline=(Point(x=float(b0[0]) / W, y=float(b0[1]) / W), Point(x=float(b1[0]) / W, y=float(b1[1]) / W)),
        runs=runs, text_confidence=ol.score,
        char_candidates=[[(cd.text, float(cd.prob)) for cd in ch.candidates] for ch in ol.chars],
        notes=list(attrs.notes) + [n for r in attrs.runs for n in r.notes],
    )


def _sub_quad(quad, b0, b1, s0: float, s1: Optional[float], W: float) -> Quad:
    """整行的四角中，沿基線從 s0 到 s1（像素，以基線起點為 0）的那一段。"""
    pts = np.asarray(quad, dtype=np.float64)
    u = b1 - b0
    length = float(np.hypot(*u)) or 1.0
    u = u / length
    s1 = length if s1 is None else s1
    tl, tr, br, bl = pts

    def along(a, b, s):
        return a + (b - a) * max(0.0, min(1.0, (s + float(np.dot(b0 - a, u))) / (float(np.dot(b - a, u)) or 1.0)))

    p = [along(tl, tr, s0), along(tl, tr, s1), along(bl, br, s1), along(bl, br, s0)]
    return _quad(p, W)


_GEOM = {"rect": ShapeGeometry.RECT, "rounded_rect": ShapeGeometry.ROUNDED_RECT, "ellipse": ShapeGeometry.ELLIPSE, "line": ShapeGeometry.LINE, "polygon": ShapeGeometry.PATH}


def _shape(page: int, rg, W: float) -> ShapeElement:
    f = rg.shape
    geom = _GEOM.get(f.geometry, ShapeGeometry.PATH)
    pts = np.asarray(f.quad, dtype=np.float64)
    if geom == ShapeGeometry.LINE:
        a, b = pts[0], pts[1] if not np.allclose(pts[0], pts[1]) else pts[2]
        q = Quad(tl=Point(x=a[0] / W, y=a[1] / W), tr=Point(x=b[0] / W, y=b[1] / W), br=Point(x=b[0] / W, y=b[1] / W), bl=Point(x=a[0] / W, y=a[1] / W))
    else:
        q = _quad(pts, W)
    path: list[list[float]] = []
    if geom == ShapeGeometry.PATH and f.polygon is not None and len(f.polygon) >= 3:
        poly = np.asarray(f.polygon, dtype=np.float64) / W
        path = [[0, float(poly[0][0]), float(poly[0][1])]] + [[1, float(x), float(y)] for x, y in poly[1:]] + [[3]]
        xs, ys = poly[:, 0], poly[:, 1]
        q = Quad.from_rect(float(xs.min()), float(ys.min()), float(xs.max()), float(ys.max()))
    elif geom == ShapeGeometry.PATH:
        geom = ShapeGeometry.RECT
    return ShapeElement(
        id=make_element_id(page, "shp", q, f"{f.geometry}:{f.fill}:{f.stroke}"), quad=q, z=10 + rg.z, geometry=geom,
        fill=_rgb(f.fill) if f.fill is not None else None, stroke=_rgb(f.stroke) if f.stroke is not None else None,
        stroke_width=(f.stroke_width_px / W) if f.stroke_width_px else None,
        corner_radius=(f.corner_radius_px / W) if f.corner_radius_px else None, path=path, fit_error=f.fit_error_px / W,
    )


def _table(page: int, rg, W: float) -> TableRegion:
    g = rg.table
    xs = sorted(float(x) / W for x in g.v_lines)
    ys = sorted(float(y) / W for y in g.h_lines)
    q = Quad.from_rect(xs[0], ys[0], xs[-1], ys[-1]) if xs and ys else Quad.from_rect(rg.bbox[0] / W, rg.bbox[1] / W, rg.bbox[2] / W, rg.bbox[3] / W)
    segments = [(xs[0], y, xs[-1], y) for y in ys] + [(x, ys[0], x, ys[-1]) for x in xs] if g.has_ruling else []
    return TableRegion(id=make_element_id(page, "tbl", q, f"{len(xs)}x{len(ys)}"), quad=q, h_lines=ys, v_lines=xs, ruled=g.has_ruling, segments=segments, confidence=rg.confidence, z=10 + rg.z)


def _arbitrate(ai, page_image: Image.Image, ocr_lines, spec: PageSpec, c: dict) -> dict[int, list[int]]:
    """信心值低、而且有其他候選的字：把這幾行的影像疊成一張圖，請 AI 在候選中挑。AI 不得改寫原文。
    直接修改 ocr_lines 中的字，回傳（行序號 → 被 AI 挑過的字的位置）。"""
    from ..ai.marks import stack_crops
    from ..ai.tasks import choose_candidates_task

    thr = float(c.get("ai_char_prob", 0.6))
    margin = float(c.get("ai_char_margin", 0.25))
    max_lines = int(c.get("ai_max_lines", 12))
    picks = []
    for i, ol in enumerate(ocr_lines):
        slots = []
        for idx, ch in enumerate(ol.chars):
            cands = [cd for cd in ch.candidates if cd.text.strip()]
            if len(cands) >= 2 and cands[0].prob < thr and cands[0].prob - cands[1].prob < margin:
                slots.append({"index": idx, "candidates": [cd.text for cd in cands[:5]]})
        if slots and len(ol.chars) == len(ol.text):
            picks.append((min(ol.chars[sl["index"]].candidates[0].prob for sl in slots), i, slots))
    if not picks:
        return {}
    picks.sort()
    picks = picks[:max_lines]
    W = page_image.size[0]
    crops, lines, k = [], [], 1
    for n, (_, i, slots) in enumerate(picks, start=1):
        q = _quad(ocr_lines[i].quad, W)
        l, t, r, b = q.bbox
        pad = 0.004
        crops.append((f"L{n}", page_image.crop((max(0, int((l - pad) * W)), max(0, int((t - pad) * W)), min(W, int((r + pad) * W) + 1), min(page_image.size[1], int((b + pad) * W) + 1)))))
        for sl in slots:
            sl["k"] = k
            k += 1
        lines.append({"label": f"L{n}", "text": ocr_lines[i].text, "slots": slots, "line": i})
    ans = ai.ask(choose_candidates_task(stack_crops(crops), lines, spec.page_index))
    if ans is None:
        return {}
    choice = {c_["k"]: int(c_["choice"]) for c_ in ans["choices"]}
    out: dict[int, list[int]] = {}
    for ln in lines:
        ol = ocr_lines[ln["line"]]
        text = list(ol.text)
        for sl in ln["slots"]:
            pick = sl["candidates"][choice[sl["k"]]]
            if pick != text[sl["index"]]:
                text[sl["index"]] = pick
                ol.chars[sl["index"]].text = pick
            out.setdefault(ln["line"], []).append(sl["index"])
        ol.text = "".join(text)
    return out


def _drop_implausible(lines, c: dict, notes: list[str]):
    """去掉不像文字的偵測：只有一兩個字、高度遠大於頁面上其他文字、而且信心不夠高（例如照片裡的圓被讀成「D」）。"""
    import numpy as np

    if len(lines) < 3:
        return lines
    heights = []
    for ol in lines:
        q = np.asarray(ol.quad, dtype=np.float64)
        heights.append(float(np.hypot(*(q[3] - q[0]))))
    median = float(np.median(heights))
    keep, dropped = [], 0
    for ol, h in zip(lines, heights):
        short = len(ol.text.strip()) <= int(c.get("implausible_max_chars", 2))
        if short and h > float(c.get("implausible_height_ratio", 4.0)) * median and (ol.score or 0.0) < float(c.get("implausible_min_score", 0.97)):
            dropped += 1
            continue
        keep.append(ol)
    if dropped:
        notes.append(f"有 {dropped} 個偵測結果不像文字（字很少、尺寸遠大於其他文字、信心不高），已當成圖形處理。")
    return keep


def _find_bullets(work: np.ndarray, lines, c: dict) -> dict:
    """項目符號：文字辨識常漏掉行首的小圓點。在每行左側兩個字高的範圍內找一個獨立的小色塊；
    接近圓形填滿 → 「•」，方形 → 「▪」，扁長 → 「–」。回傳（行序號 → （符號、符號左緣到行首的距離、符號寬，像素））。
    文字行本身不改（字級與字型照原行量），符號在組成文字行時接在最前面。"""
    import cv2

    if not c.get("bullet_detection", True):
        return {}
    H, W = work.shape[:2]
    gray = cv2.cvtColor(work, cv2.COLOR_RGB2GRAY).astype(np.float32)
    out = {}
    for i, ol in enumerate(lines):
        if not ol.text.strip() or ol.text.strip()[0] in "•‧·●○■□▪◆◇▶➤-–—*":
            continue
        q = np.asarray(ol.quad, dtype=np.float64)
        u = q[1] - q[0]
        length = float(np.hypot(*u))
        if length <= 0 or abs(np.degrees(np.arctan2(u[1], u[0]))) > 2.0:
            continue  # 只處理橫排、接近水平的行
        h = float(np.hypot(*(q[3] - q[0])))
        left = min(q[0][0], q[3][0])
        x0 = int(max(0, np.floor(left - 2.2 * h)))
        x1 = int(max(0, np.floor(left - 0.15 * h)))
        y0 = int(max(0, np.floor(min(q[0][1], q[1][1]))))
        y1 = int(min(H, np.ceil(max(q[2][1], q[3][1]))))
        if x1 - x0 < 3 or y1 - y0 < 3:
            continue
        patch = gray[y0:y1, x0:x1]
        bg = float(np.median(np.concatenate([patch[:, :2].ravel(), patch[:, -2:].ravel(), patch[:2, :].ravel()])))
        mask = (np.abs(patch - bg) > 40.0).astype(np.uint8)
        n, _, stats, cents = cv2.connectedComponentsWithStats(mask, 8)
        blobs = [(stats[k], cents[k]) for k in range(1, n) if stats[k][cv2.CC_STAT_AREA] >= 4]
        if len(blobs) != 1:
            continue
        st, cen = blobs[0]
        bw, bh, area = st[cv2.CC_STAT_WIDTH], st[cv2.CC_STAT_HEIGHT], st[cv2.CC_STAT_AREA]
        if not (0.12 * h <= max(bw, bh) <= 0.7 * h):
            continue
        cy = y0 + cen[1]
        if not (q[0][1] + 0.2 * h <= cy <= q[3][1] - 0.1 * h):
            continue  # 項目符號在行的中段，不在上緣或下緣
        fill = area / max(1, bw * bh)
        aspect = bw / max(1, bh)
        if aspect > 2.2 and bh <= 0.25 * h:
            glyph = "–"
        elif fill > 0.9 and 0.8 <= aspect <= 1.25:
            glyph = "▪"
        elif fill > 0.55 and 0.7 <= aspect <= 1.4:
            glyph = "•"
        else:
            continue
        dx = float(left - (x0 + st[cv2.CC_STAT_LEFT]))
        if dx > 0:
            out[i] = (glyph, dx, float(bw))
    return out


def _prepend_bullet(line: TextLine, glyph: str, dx_px: float, bw_px: float, W: float) -> TextLine:
    """把項目符號接到文字行最前面：四角與基線往左延伸，第一段的樣式沿用（符號的字級與顏色跟著這一行）。"""
    import math

    dx = dx_px / W
    ang = math.radians(line.quad.angle_deg)
    ux, uy = math.cos(ang), math.sin(ang)

    def shift(pt):
        return Point(x=pt.x - ux * dx, y=pt.y - uy * dx)

    q = Quad(tl=shift(line.quad.tl), tr=line.quad.tr, br=line.quad.br, bl=shift(line.quad.bl))
    first = line.runs[0]
    rq = Quad(tl=q.tl, tr=Point(x=q.tl.x + ux * bw_px / W, y=q.tl.y + uy * bw_px / W), br=Point(x=q.bl.x + ux * bw_px / W, y=q.bl.y + uy * bw_px / W), bl=q.bl)
    bullet = first.model_copy(update={"text": glyph + " ", "quad": rq, "char_x": [0.0], "font_candidates": first.font_candidates})
    runs = [bullet] + [r.model_copy(update={"char_x": [x + dx for x in r.char_x]}) for r in line.runs]
    b0 = line.baseline[0]
    return line.model_copy(update={"quad": q, "runs": runs, "baseline": (Point(x=b0.x - ux * dx, y=b0.y - uy * dx), line.baseline[1]),
                                   "char_candidates": [[(glyph, 0.8)], [(" ", 0.8)]] + list(line.char_candidates),
                                   "notes": list(line.notes) + ["行首的項目符號由影像偵測補上"]})
