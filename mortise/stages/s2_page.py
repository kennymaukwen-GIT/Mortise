"""② 頁面：收來源文件，交出頁面規格 PageSpec（四角、長寬比、比例尺、工作影像）。

步驟：
  s2.box        原生 PDF：選頁框（裁切框與媒體框的交集）、套用旋轉
  s2.corners    建立頁面座標系（頁寬為 1）；影像輸入目前以整張圖為頁面（尚未做四邊擬合與透視校正）
  s2.size       尺寸判斷：使用者輸入永遠優先，其次檔案內建，都沒有才推論
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
VERSION = "1"


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
        image = ImageOps.exif_transpose(Image.open(store.blob_path(doc.source))).convert("RGB")
        pw, ph = image.size
        matrix = [1 / pw, 0, 0, 0, 1 / pw, 0, 0, 0, 1]
        corners = [(0, 0), (pw, 0), (pw, ph), (0, ph)]
        aspect = ph / pw
        rotation, box_used = 0, None
        if ref.dpi_hint:
            file_width = measured(pw * 72 / ref.dpi_hint, 0.4, Provenance.INFERRED, w_step)
            notes.append(f"尺寸依檔案標記的 {ref.dpi_hint:.0f} dpi 推算，可能不準，建議輸入實際尺寸。")
        else:
            file_width = measured(pw * 72 / c["assumed_image_dpi"], 0.2, Provenance.INFERRED, w_step)
            notes.append("影像沒有尺寸資訊，暫以 96 dpi 換算。單張影像量不出實際大小，請輸入實際尺寸。")
        notes.append("影像輸入目前以整張圖為頁面，尚未做四邊擬合與透視校正。")

    if user is not None:
        uw, uh = to_pt(user.width, user.unit), to_pt(user.height, user.unit)
        if abs((uh / uw) / aspect - 1) > 0.02:
            notes.append(f"輸入的尺寸長寬比（{uh / uw:.3f}）與頁面（{aspect:.3f}）不符，已以寬度為準。")
        width = by_user(uw, w_step)
    else:
        width = file_width

    blob = store.put_blob(_png(image), "image/png", "png")
    return PageSpec(
        page_index=page_index,
        source_corners=[Point(x=x, y=y) for x, y in corners],
        aspect_ratio=aspect, source_to_page=matrix, rotation_applied=rotation, box_used=box_used,
        working_image=blob, working_image_size=image.size,
        page_width_pt=width, effective_resolution=image.size[0] / width.value, notes=notes,
    )


def geometry_key(spec: PageSpec) -> str:
    """③只依賴頁面幾何與工作影像，不依賴比例尺：改尺寸不必重跑辨識。"""
    return "|".join([spec.working_image.sha256, repr(spec.source_to_page), repr(spec.aspect_ratio), repr(spec.rotation_applied)])
