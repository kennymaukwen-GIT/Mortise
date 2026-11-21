"""② 頁面：收來源文件，交出頁面規格 PageSpec（四角、長寬比、比例尺、工作影像）。

步驟：
  s2.box        原生 PDF：選頁框（裁切框與媒體框的交集）、套用旋轉
  s2.corners    照片：四邊擬合找頁面四角（找不到就以整張圖為頁面，使用者可以手動拉四角）；截圖與掃描以整張圖為頁面
  s2.rectify    透視攤平，建立頁面座標系（頁寬為 1）
  s2.enhance    去陰影、白平衡，產生工作影像（量顏色時用未增強的影像 color_image）
  s2.size       尺寸判斷：使用者輸入永遠優先，其次檔案內建，都沒有才依長寬比推論候選規格
  s2.working    產生工作影像，計算有效解析度
"""

from __future__ import annotations

import io
from pathlib import Path
from typing import Optional

from PIL import Image, ImageOps

from ..artifacts import Correction, InputType, PageSpec, PhysicalSize, Point, Provenance, SourceDoc, by_user, exact, measured
from ..config import Config
from ..pipeline.store import Store
from ..tools import pdfium as pdf
from ..tools.units import to_pt

NAME = "s2"
VERSION = "2"


def _png(im: Image.Image) -> bytes:
    buf = io.BytesIO()
    im.save(buf, "PNG")
    return buf.getvalue()


def _user_size(doc: SourceDoc, corrections: list[Correction]) -> Optional[PhysicalSize]:
    """使用者指定的尺寸：修正單優先於上傳時填的。"""
    for c in reversed(corrections):
        if c.field == "size" and c.new_value:
            return PhysicalSize(**c.new_value)
    return doc.intent.known_size


def pdf_matrix(box: tuple[float, float, float, float], rotation: int) -> tuple[list[float], float, float]:
    """PDF 使用者空間（pt，y 向上）→ 頁面相對座標（頁寬為 1，y 向下）。回傳（矩陣、顯示寬 pt、顯示高 pt）。"""
    l, b, r, t = box
    w, h = r - l, t - b
    rot = rotation % 360
    if rot == 0:
        dw, dh = w, h
        m = [1 / dw, 0, -l / dw, 0, -1 / dw, t / dw, 0, 0, 1]
    elif rot == 90:
        dw, dh = h, w
        m = [0, 1 / dw, -b / dw, 1 / dw, 0, -l / dw, 0, 0, 1]
    elif rot == 180:
        dw, dh = w, h
        m = [-1 / dw, 0, r / dw, 0, 1 / dw, -b / dw, 0, 0, 1]
    else:  # 270
        dw, dh = h, w
        m = [0, -1 / dw, t / dw, -1 / dw, 0, r / dw, 0, 0, 1]
    return m, dw, dh


def run(doc: SourceDoc, page_index: int, cfg: Config, corrections: list[Correction], store: Store) -> PageSpec:
    c = cfg.section("page")
    ref = doc.pages[page_index]
    user = _user_size(doc, corrections)
    notes: list[str] = []
    w_step = f"{NAME}.size@{VERSION}"

    if doc.media_type == "application/pdf":
        pdoc = pdf.open_document(str(store.blob_path(doc.source)))
        reader = pdf.PageReader(pdoc, page_index)
        try:
            boxes = reader.boxes()
            media = boxes["media"]
            crop = boxes.get("crop", media)
            box = (max(crop[0], media[0]), max(crop[1], media[1]), min(crop[2], media[2]), min(crop[3], media[3]))
            if box[2] - box[0] <= 0 or box[3] - box[1] <= 0:
                box = media
            box_used = "crop" if "crop" in boxes and tuple(crop) != tuple(media) else "media"
            rotation = reader.rotation()
            matrix, dw, dh = pdf_matrix(box, rotation)
            dpi = c["working_dpi"]
            if dw * dh * (dpi / 72) ** 2 > c["max_working_pixels"]:
                dpi = 72 * (c["max_working_pixels"] / (dw * dh)) ** 0.5
                notes.append(f"頁面很大，工作影像解析度降為 {dpi:.0f} dpi。")
            image = reader.render(dpi)
        finally:
            reader.close()
            pdoc.close()
        l, b, r, t = box
        corners = {0: [(l, t), (r, t), (r, b), (l, b)], 90: [(l, b), (l, t), (r, t), (r, b)], 180: [(r, b), (l, b), (l, t), (r, t)], 270: [(r, t), (r, b), (l, b), (l, t)]}[rotation % 360]
        if ref.input_type.value == InputType.SCANNED_PDF:
            # 掃描 PDF 的頁面尺寸只是掃描器設定，只當強參考
            file_width = measured(dw, 0.7, Provenance.INFERRED, w_step)
        else:
            file_width = exact(dw, w_step)
        aspect = dh / dw
    else:
        image, color, matrix, corners, aspect, quad_method, quad_conf, geo_notes = _image_geometry(doc, ref, c, corrections, store)
        notes += geo_notes
        rotation, box_used = 0, None
        pw = image.size[0]
        size_candidates = _size_candidates(aspect, ref, image.size, cfg, c)
        top = size_candidates[0] if size_candidates else None
        if top and top["confidence"] >= c.get("size_auto_accept", 0.6):
            file_width = measured(top["width_pt"], top["confidence"], Provenance.INFERRED, w_step)
            notes.append(f"尺寸推論為「{top['name']}」（{top['reason']}）。如果不對，請輸入實際尺寸。")
        elif ref.dpi_hint and quad_method in ("full_image", ""):
            file_width = measured(pw * 72 / ref.dpi_hint, 0.4, Provenance.INFERRED, w_step)
            notes.append(f"尺寸依檔案標記的 {ref.dpi_hint:.0f} dpi 推算，可能不準，建議輸入實際尺寸。")
        else:
            file_width = measured(pw * 72 / c["assumed_image_dpi"], 0.2, Provenance.INFERRED, w_step)
            if size_candidates:
                names = "、".join(f"{x['name']}（{x['confidence']:.0%}）" for x in size_candidates[:3])
                notes.append(f"單張影像量不出實際大小。依長寬比最可能是：{names}。請選一個或輸入實際尺寸。")
            else:
                notes.append("單張影像量不出實際大小，長寬比也不符合常見規格，請輸入實際尺寸。")

    if user is not None:
        uw, uh = to_pt(user.width, user.unit), to_pt(user.height, user.unit)
        if abs((uh / uw) / aspect - 1) > 0.02:
            notes.append(f"輸入的尺寸長寬比（{uh / uw:.3f}）與頁面（{aspect:.3f}）不符，已以寬度為準。")
        width = by_user(uw, w_step)
    else:
        width = file_width

    blob = store.put_blob(_png(image), "image/png", "png")
    extra = {}
    if doc.media_type != "application/pdf":
        extra = {
            "color_image": store.put_blob(_png(color), "image/png", "png") if color is not image else None,
            "size_candidates": size_candidates, "quad_method": quad_method, "quad_confidence": quad_conf,
        }
    else:
        extra = {"quad_method": "pdf_box", "quad_confidence": 1.0}
    return PageSpec(
        page_index=page_index,
        source_corners=[Point(x=x, y=y) for x, y in corners],
        aspect_ratio=aspect, source_to_page=matrix, rotation_applied=rotation, box_used=box_used,
        working_image=blob, working_image_size=image.size,
        page_width_pt=width, effective_resolution=image.size[0] / width.value, notes=notes, **extra,
    )


# ---------- 影像輸入：s2.corners、s2.rectify、s2.enhance ----------


def _user_corners(corrections: list[Correction]):
    for c in reversed(corrections):
        if c.field == "corners" and c.new_value:
            return [tuple(map(float, pt)) for pt in c.new_value]
    return None


def _image_geometry(doc: SourceDoc, ref, c: dict, corrections: list[Correction], store: Store):
    """找頁面四角、透視攤平、增強。照片才找四角；截圖與一般影像以整張圖為頁面。
    回傳（工作影像、未增強的影像、來源到頁面的矩陣、四角、長寬比、四角的來源、信心值、備註）。"""
    import numpy as np

    raw = ImageOps.exif_transpose(Image.open(store.blob_path(doc.source))).convert("RGB")
    pw, ph = raw.size
    notes: list[str] = []
    full = ([1 / pw, 0, 0, 0, 1 / pw, 0, 0, 0, 1], [(0, 0), (pw, 0), (pw, ph), (0, ph)], ph / pw)
    user = _user_corners(corrections)
    kind = ref.input_type.value
    try:
        from ..recognition import page_geometry as pg
        from ..recognition.types import PageQuad
    except Exception:  # 模組還沒有或壞掉：以整張圖為頁面
        return raw, raw, *full, "full_image", 0.5, ["頁面幾何模組無法載入，以整張圖為頁面。"]
    arr = np.asarray(raw)
    try:
        if user is not None:
            corners = np.array(user, dtype=np.float64)
            quad = PageQuad(corners=corners, confidence=1.0, method="user", aspect_ratio=_aspect_from_corners(corners))
        elif kind == InputType.PHOTO:
            quad = pg.find_page_quad(arr)
        else:
            quad = None
        if quad is None or quad.method == "full_image":
            if quad is not None:
                notes.append("照片中找不到清楚的頁面邊緣，以整張圖為頁面。可以手動拉出四個角。")
            image_arr = arr
            matrix, corners_l, aspect = full
            method, conf = ("full_image", quad.confidence) if quad is not None else ("full_image", 1.0)
        else:
            flat, H = pg.rectify(arr, quad)
            image_arr = flat
            fw = flat.shape[1]
            Hn = np.diag([1 / fw, 1 / fw, 1.0]) @ np.asarray(H, dtype=np.float64)
            matrix = [float(v) for v in (Hn / Hn[2, 2]).ravel()]
            corners_l = [tuple(map(float, pt)) for pt in quad.corners]
            aspect = flat.shape[0] / flat.shape[1]
            method, conf = quad.method, quad.confidence
            notes += list(getattr(quad, "notes", []))
            notes.append(f"已找出頁面四角並透視攤平（{method}，信心值 {conf:.2f}）。")
        color = Image.fromarray(np.ascontiguousarray(image_arr))
        if kind in (InputType.PHOTO, InputType.SCANNED_PDF) and c.get("enhance_photos", True):
            work = Image.fromarray(np.ascontiguousarray(pg.enhance(image_arr)))
        else:
            work = color
        return work, color, matrix, corners_l, aspect, method, conf, notes
    except Exception as e:  # 幾何處理失敗不中斷：退回整張圖
        return raw, raw, *full, "full_image", 0.3, [f"頁面幾何處理失敗（{e}），以整張圖為頁面。"]


def _aspect_from_corners(corners) -> float:
    import math as _m

    top = _m.dist(corners[0], corners[1])
    bottom = _m.dist(corners[3], corners[2])
    left = _m.dist(corners[0], corners[3])
    right = _m.dist(corners[1], corners[2])
    return ((left + right) / 2) / max((top + bottom) / 2, 1e-6)


def _size_candidates(aspect: float, ref, pixel_size, cfg: Config, c: dict) -> list[dict]:
    try:
        from ..recognition import page_size
    except Exception:
        return []
    sizes = {k: tuple(v) for k, v in cfg.section("page_sizes").items()}
    hints = {"pixel_size": tuple(pixel_size), "dpi": ref.dpi_hint, "input_type": ref.input_type.value.value}
    try:
        cands = page_size.infer_size(aspect, sizes, hints)
    except Exception:
        return []
    return [{"name": x.name, "width_pt": x.width_pt, "height_pt": x.height_pt, "confidence": round(x.confidence, 3), "reason": x.reason} for x in cands[:5]]


def geometry_key(spec: PageSpec) -> str:
    """③只依賴頁面幾何與工作影像，不依賴比例尺：改尺寸不必重跑辨識。"""
    return "|".join([spec.working_image.sha256, repr(spec.source_to_page), repr(spec.aspect_ratio), repr(spec.rotation_applied)])
