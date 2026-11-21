"""編號標記圖：在頁面影像上把元素框起來並標上編號，供大型 AI 只用編號回答。

- 影像先縮到長邊不超過 max_long_edge（Claude 的高解析度上限是 2576 px，超過會被縮圖或拒收）。
- 框線用醒目的顏色，編號放在框的左上角外側並加白底，避免遮住元素本身的內容。
- 回傳 PNG 位元組與「編號 → 元素編號」的對照表。
"""

from __future__ import annotations

import io
from typing import Optional

from PIL import Image, ImageDraw, ImageFont

from ..artifacts import Quad

_COLORS = [(230, 25, 75), (0, 130, 200), (60, 180, 75), (245, 130, 48), (145, 30, 180), (0, 128, 128)]


def _font(size: int):
    for name in ("DejaVuSans-Bold.ttf", "LiberationSans-Bold.ttf"):
        try:
            return ImageFont.truetype(name, size)
        except OSError:
            continue
    return ImageFont.load_default()


def render_marks(image: Image.Image, items: list[tuple[str, Quad]], page_width_rel: float = 1.0, max_long_edge: int = 2576) -> tuple[bytes, dict[int, str]]:
    """items：（元素編號、四角，頁面相對座標）。回傳（PNG、編號從 1 起 → 元素編號）。"""
    im = image.convert("RGB")
    scale = min(1.0, max_long_edge / max(im.size))
    if scale < 1.0:
        im = im.resize((max(1, round(im.size[0] * scale)), max(1, round(im.size[1] * scale))), Image.LANCZOS)
    W = im.size[0] / page_width_rel
    draw = ImageDraw.Draw(im)
    font = _font(max(12, min(28, im.size[0] // 60)))
    mapping: dict[int, str] = {}
    for n, (eid, q) in enumerate(items, start=1):
        mapping[n] = eid
        color = _COLORS[(n - 1) % len(_COLORS)]
        pts = [(p.x * W, p.y * W) for p in q.points]
        draw.line(pts + [pts[0]], fill=color, width=max(2, im.size[0] // 600))
        label = str(n)
        x0 = min(p[0] for p in pts)
        y0 = min(p[1] for p in pts)
        tw, th = draw.textbbox((0, 0), label, font=font)[2:]
        bx, by = max(0, x0 - 2), max(0, y0 - th - 6)
        draw.rectangle([bx, by, bx + tw + 6, by + th + 4], fill=(255, 255, 255), outline=color)
        draw.text((bx + 3, by + 1), label, fill=color, font=font)
    buf = io.BytesIO()
    im.save(buf, "PNG", optimize=True)
    return buf.getvalue(), mapping


def crop_png(image: Image.Image, q: Quad, pad_rel: float = 0.01, max_long_edge: int = 1600, min_height: int = 48) -> bytes:
    """裁出一個元素附近的影像（文字行的候選字仲裁用）。"""
    W = image.size[0]
    l, t, r, b = q.bbox
    box = (max(0, int((l - pad_rel) * W)), max(0, int((t - pad_rel) * W)), min(image.size[0], int((r + pad_rel) * W) + 1), min(image.size[1], int((b + pad_rel) * W) + 1))
    im = image.convert("RGB").crop(box)
    if im.size[1] < min_height:
        f = min_height / max(1, im.size[1])
        im = im.resize((max(1, round(im.size[0] * f)), min_height), Image.LANCZOS)
    s = min(1.0, max_long_edge / max(im.size))
    if s < 1.0:
        im = im.resize((max(1, round(im.size[0] * s)), max(1, round(im.size[1] * s))), Image.LANCZOS)
    buf = io.BytesIO()
    im.save(buf, "PNG", optimize=True)
    return buf.getvalue()


def stack_crops(crops: list[tuple[str, Image.Image]], max_long_edge: int = 2576, row_height: int = 64) -> bytes:
    """把幾行的裁切影像由上而下疊成一張圖，每行左邊加上標籤（L1、L2…）。"""
    rows = []
    font = _font(max(14, row_height // 3))
    for label, im in crops:
        im = im.convert("RGB")
        f = row_height / max(1, im.size[1])
        rows.append((label, im.resize((max(1, round(im.size[0] * f)), row_height), Image.LANCZOS)))
    label_w = row_height
    width = label_w + max(r[1].size[0] for r in rows)
    out = Image.new("RGB", (width, (row_height + 8) * len(rows)), (255, 255, 255))
    draw = ImageDraw.Draw(out)
    for i, (label, im) in enumerate(rows):
        y = i * (row_height + 8) + 4
        draw.text((4, y + row_height // 3), label, fill=(230, 25, 75), font=font)
        out.paste(im, (label_w, y))
        draw.line([(0, y + row_height + 3), (width, y + row_height + 3)], fill=(200, 200, 200))
    s = min(1.0, max_long_edge / max(out.size))
    if s < 1.0:
        out = out.resize((max(1, round(out.size[0] * s)), max(1, round(out.size[1] * s))), Image.LANCZOS)
    buf = io.BytesIO()
    out.save(buf, "PNG", optimize=True)
    return buf.getvalue()
