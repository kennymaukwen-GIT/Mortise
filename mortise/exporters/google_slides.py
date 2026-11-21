"""Google 簡報輸出器：產生一份「匯入 Google 簡報後失真最少」的 PPTX。

Google 簡報沒有可直接寫的檔案格式，使用者把 PPTX 上傳到雲端硬碟後以 Google 簡報開啟，
匯入時 Google 會再轉一次（風險 R9）。本輸出器不改 PowerPoint 版的檔案，而是從成品文件
複製一份「Google 方言」的副本，再交給 PPTX 寫出器（mortise.exporters.pptx.export）：

  1. 字型換成 Google 字型庫裡有的字型（Google 簡報不能用內嵌字型，也不能上傳字型；
     不在字型庫的字型會被替換，常見換成 Arial，造成文字重排）。
  2. 段數過多的自訂路徑點陣化成圖片（Google 簡報對複雜路徑支援差；門檻放參數）。
  3. 圖片總量超過檔案大小預算（轉換上限 100 MB）時降解析度。

降階對照表（成品文件的概念 → Google 簡報用 PPTX 的物件 → 是否有損）：
  字型 Calibri／Cambria／Arial／Times New Roman／Courier New
                       → Carlito／Caladea／Arimo／Tinos／Cousine（字寬相容的開放字型）→ 字寬相同，無損
  字型 其他西文        → Google 字型庫已有的照用；沒有對應的換成 Arimo               → 有損（字寬不同，可能重排）
  字型 中文黑體類      → Noto Sans TC（拉丁與東亞欄位都寫同一個字型）               → 有損
  字型 中文明體、宋體類 → Noto Serif TC                                              → 有損
  字型 中文楷體類      → LXGW WenKai TC                                              → 有損
  東亞文字但字型是西文 → 依襯線與否換成 Noto Sans TC／Noto Serif TC（涵蓋中英文）  → 有損
  自訂路徑（段數 ≤ 門檻）→ 自訂幾何（同 PPTX）                                       → 匯入後的結果未經實測
  自訂路徑（段數 > 門檻）→ 點陣圖片（同位置、同旋轉，四周留半個線寬）               → 有損（不再是可編輯向量）
  圖片（超過檔案預算） → 降解析度的圖片                                              → 有損
  其他（文字框、段落、圖片、形狀、表格、底圖）→ 同 PPTX 輸出器的降階對照            → 同 PPTX
  各頁尺寸不同         → 以第 1 頁為準（同 PPTX）                                    → 有損
"""

from __future__ import annotations

import io
import math
import re
from typing import Any, Optional

from lxml import etree
from PIL import Image, ImageChops, ImageDraw

from ..artifacts import (
    Box,
    Degradation,
    FontChoice,
    OutputFile,
    OutputFormat,
    Provenance,
    StyledDoc,
    StyledImage,
    StyledPage,
    StyledParagraph,
    StyledShape,
    StyledTable,
    StyledTextBox,
    measured,
)
from ..config import Config
from ..pipeline.store import Store
from . import pptx as pptx_exporter

WRITER = "s6.google_slides@1"

DEFAULT_PARAMS: dict[str, Any] = {
    # 西文字型對照（小寫比對）→ Google 字型庫的字型
    "font_map": {
        "calibri": "Carlito",
        "cambria": "Caladea",
        "arial": "Arimo",
        "helvetica": "Arimo",
        "helvetica neue": "Arimo",
        "liberation sans": "Arimo",
        "times new roman": "Tinos",
        "times": "Tinos",
        "liberation serif": "Tinos",
        "courier new": "Cousine",
        "courier": "Cousine",
        "liberation mono": "Cousine",
        "georgia": "Gelasio",
    },
    # 字寬相容的配對：換過去不會重排（降級紀錄標為無損）
    "metric_twins": {
        "calibri": "Carlito",
        "carlito": "Carlito",
        "cambria": "Caladea",
        "arial": "Arimo",
        "liberation sans": "Arimo",
        "helvetica": "Arimo",
        "times new roman": "Tinos",
        "liberation serif": "Tinos",
        "courier new": "Cousine",
        "liberation mono": "Cousine",
        "georgia": "Gelasio",
    },
    "default_western": "Arimo",  # 其他沒有對應的西文
    "cjk_sans": "Noto Sans TC",  # 中文黑體類
    "cjk_serif": "Noto Serif TC",  # 中文明體、宋體類
    "cjk_kai": "LXGW WenKai TC",  # 中文楷體類
    # Google 字型庫裡已有、照用的字型（小寫比對；清單待實測擴充）
    "google_fonts": [
        "arimo", "tinos", "cousine", "carlito", "caladea", "gelasio", "roboto", "roboto slab", "roboto mono",
        "open sans", "lato", "montserrat", "oswald", "raleway", "poppins", "inter", "nunito", "merriweather",
        "playfair display", "pt sans", "pt serif", "source sans 3", "source serif 4", "ubuntu", "work sans",
        "noto sans", "noto serif", "noto sans tc", "noto serif tc", "noto sans sc", "noto serif sc",
        "noto sans jp", "noto serif jp", "noto sans kr", "lxgw wenkai tc", "comfortaa", "lora", "rubik",
    ],
    "cjk_google_fonts": ["noto sans tc", "noto serif tc", "noto sans sc", "noto serif sc", "noto sans jp", "noto serif jp", "noto sans kr", "lxgw wenkai tc"],
    # 判斷「這是中日韓字型」的名稱片段（小寫）；名稱含中日韓字元也算
    "cjk_font_markers": [
        "jhenghei", "yahei", "mingliu", "pmingliu", "simsun", "nsimsun", "simhei", "kaiti", "dfkai", "biaukai",
        "pingfang", "heiti", "songti", "stheiti", "stsong", "stkaiti", "fangsong", "noto sans cjk", "noto serif cjk",
        "source han", "wenquanyi", "wqy", "mincho", "ms gothic", "meiryo", "yu gothic", "malgun", "hiragino",
        "lantinghei", "dengxian", "microsoft jhenghei", "microsoft yahei",
    ],
    "cjk_kai_markers": ["楷", "kai"],
    "cjk_serif_markers": ["明", "宋", "ming", "song", "sung", "simsun", "mincho", "batang", "serif", "fangsong", "仿宋"],
    # 西文襯線字型的名稱片段：東亞文字配西文字型時，用來選 Noto Serif TC 或 Noto Sans TC
    "western_serif_markers": ["times", "cambria", "caladea", "tinos", "georgia", "garamond", "serif", "book antiqua", "palatino", "minion"],
    # 自訂路徑：段數超過此值就點陣化（初始值，待實際匯入 Google 簡報測試後調整）
    "max_path_segments": 24,
    "raster_dpi": 200.0,  # 點陣化的解析度
    "raster_supersample": 4,  # 反鋸齒的超取樣倍數
    "bezier_steps": 16,  # 三次貝茲曲線攤平的段數
    "file_size_budget_mb": 90.0,  # 檔案大小預算；Google 的轉換上限是 100 MB
    "file_size_limit_mb": 100.0,
}

_CJK_CHAR = re.compile(r"[⺀-鿿豈-﫿　-〿가-힯＀-￯]")


def params(cfg: Optional[Config]) -> dict[str, Any]:
    out = dict(DEFAULT_PARAMS)
    if cfg is not None:
        out.update(cfg.section("export").get("google_slides", {}) or {})
    return out


# ---------------------------------------------------------------- 字型對照


def _has_marker(name: str, markers: list[str]) -> bool:
    low = name.lower()
    return any(m.lower() in low for m in markers)


def is_cjk_font(family: str, p: dict[str, Any]) -> bool:
    return bool(_CJK_CHAR.search(family)) or _has_marker(family, p["cjk_font_markers"]) or family.lower() in p["cjk_google_fonts"]


def map_font(family: str, east_asian: bool, p: dict[str, Any]) -> str:
    """推論的字型 → Google 字型庫的字型。"""
    low = family.strip().lower()
    cjk_font = is_cjk_font(family, p)
    if east_asian or cjk_font:
        if low in p["cjk_google_fonts"]:
            return family
        if cjk_font:
            if _has_marker(family, p["cjk_kai_markers"]):
                return p["cjk_kai"]
            if _has_marker(family, p["cjk_serif_markers"]):
                return p["cjk_serif"]
            return p["cjk_sans"]
        # 東亞文字配西文字型：換成涵蓋中英文的字型，拉丁與東亞欄位寫同一個
        return p["cjk_serif"] if _has_marker(family, p["western_serif_markers"]) else p["cjk_sans"]
    if low in p["google_fonts"]:
        return family
    return p["font_map"].get(low, p["default_western"])


def _map_paragraphs(paragraphs: list[StyledParagraph], p: dict[str, Any], pairs: set[tuple[str, str]]) -> list[StyledParagraph]:
    out = []
    for para in paragraphs:
        runs = []
        for run in para.runs:
            src = run.font.family.value
            dst = map_font(src, run.font.east_asian, p)
            if dst != src:
                pairs.add((src, dst))
                fam = measured(dst, run.font.family.confidence, Provenance.INFERRED, WRITER)
                # 驗證用的替代字型沿用原本的（伺服器上不一定有 Google 字型）
                ver = run.font.verification_family
                font = FontChoice(family=fam, verification_family=ver, is_substituted=ver != dst, east_asian=run.font.east_asian)
                run = run.model_copy(update={"font": font})
            runs.append(run)
        out.append(para.model_copy(update={"runs": runs}))
    return out


def _font_degradations(element_id: str, pairs: set[tuple[str, str]], p: dict[str, Any]) -> list[Degradation]:
    out = []
    for src, dst in sorted(pairs):
        twin = p["metric_twins"].get(src.lower()) == dst
        note = "字寬相容，不會重排" if twin else "字寬不同，文字可能重排"
        out.append(Degradation(element_id=element_id, concept=f"字型 {src}", action=f"換成 Google 字型庫的 {dst}（Google 簡報不能用內嵌或自訂字型；{note}）", lossy=not twin))
    return out


# ---------------------------------------------------------------- 路徑點陣化


def flatten_path(path: list[list[float]], steps: int = 16) -> list[tuple[list[tuple[float, float]], bool]]:
    """把路徑攤平成多段折線：[(點列, 是否閉合)]，座標相對於 box 左上角（pt）。"""
    subpaths: list[tuple[list[tuple[float, float]], bool]] = []
    cur: list[tuple[float, float]] = []
    start: Optional[tuple[float, float]] = None

    def flush(closed: bool) -> None:
        nonlocal cur
        if len(cur) >= 2:
            subpaths.append((cur, closed))
        cur = []

    for seg in path:
        if not seg:
            continue
        kind = int(seg[0])
        if kind == 0:
            flush(False)
            start = (seg[1], seg[2])
            cur = [start]
        elif kind == 1:
            if not cur:
                cur = [start or (0.0, 0.0)]
            cur.append((seg[1], seg[2]))
        elif kind == 2:
            if not cur:
                cur = [start or (0.0, 0.0)]
            x0, y0 = cur[-1]
            x1, y1, x2, y2, x3, y3 = seg[1:7]
            for i in range(1, steps + 1):
                t = i / steps
                u = 1 - t
                cur.append((u**3 * x0 + 3 * u * u * t * x1 + 3 * u * t * t * x2 + t**3 * x3, u**3 * y0 + 3 * u * u * t * y1 + 3 * u * t * t * y2 + t**3 * y3))
        elif kind == 3:
            first = cur[0] if cur else start
            flush(True)
            if first is not None:
                start = first
    flush(False)
    return subpaths


def rasterize_path(shape: StyledShape, p: dict[str, Any]) -> tuple[bytes, Box]:
    """把自訂路徑畫成透明底的 PNG。回傳 (PNG, 圖片的位置)；四周多留半個線寬，旋轉不變（中心不變）。"""
    scale = float(p["raster_dpi"]) / 72.0
    ss = int(p["raster_supersample"])
    sw = (shape.stroke_width_pt or 1.0) if shape.stroke is not None else 0.0
    pad = sw / 2 + 1.0 / scale
    b = shape.box
    w_px = max(1, int(math.ceil((b.w + 2 * pad) * scale)))
    h_px = max(1, int(math.ceil((b.h + 2 * pad) * scale)))
    big = (w_px * ss, h_px * ss)
    k = scale * ss

    def to_px(pt: tuple[float, float]) -> tuple[float, float]:
        return ((pt[0] + pad) * k, (pt[1] + pad) * k)

    subpaths = flatten_path(shape.path, int(p["bezier_steps"]))
    canvas = Image.new("RGBA", big, (0, 0, 0, 0))
    if shape.fill is not None:
        fill_mask = Image.new("1", big, 0)
        for pts, _closed in subpaths:  # 奇偶規則：每個子路徑各畫一次再互斥或，內圈成為洞
            if len(pts) >= 3:
                one = Image.new("1", big, 0)
                ImageDraw.Draw(one).polygon([to_px(q) for q in pts], fill=1)
                fill_mask = ImageChops.logical_xor(fill_mask, one)
        alpha = fill_mask.convert("L").point(lambda v: int(v * shape.fill.a))
        layer = Image.new("RGBA", big, (shape.fill.r, shape.fill.g, shape.fill.b, 255))
        layer.putalpha(alpha)
        canvas = Image.alpha_composite(canvas, layer)
    if shape.stroke is not None and sw > 0:
        stroke_mask = Image.new("L", big, 0)
        d = ImageDraw.Draw(stroke_mask)
        width = max(1, int(round(sw * k)))
        for pts, closed in subpaths:
            line = [to_px(q) for q in pts] + ([to_px(pts[0])] if closed else [])
            d.line(line, fill=255, width=width, joint="curve")
        alpha = stroke_mask.point(lambda v: int(v * shape.stroke.a))
        layer = Image.new("RGBA", big, (shape.stroke.r, shape.stroke.g, shape.stroke.b, 255))
        layer.putalpha(alpha)
        canvas = Image.alpha_composite(canvas, layer)
    img = canvas.resize((w_px, h_px), Image.LANCZOS)
    buf = io.BytesIO()
    img.save(buf, "PNG", optimize=True)
    box = Box(x=b.x - pad, y=b.y - pad, w=b.w + 2 * pad, h=b.h + 2 * pad, rotation=b.rotation)
    return buf.getvalue(), box


# ---------------------------------------------------------------- 檔案大小


def _shrink_images(doc: StyledDoc, store: Store, p: dict[str, Any], degradations: list[Degradation]) -> StyledDoc:
    """圖片總量超過預算時，等比例降解析度（預算的九成留給圖片）。"""
    budget = float(p["file_size_budget_mb"]) * 1024 * 1024 * 0.9
    refs = [im.image for pg in doc.pages for im in pg.images] + [pg.background_image for pg in doc.pages if pg.background_image is not None]
    total = sum(r.size for r in {r.sha256: r for r in refs}.values())
    if total <= budget or total == 0:
        return doc
    factor = math.sqrt(budget / total) * 0.95
    cache: dict[str, Any] = {}

    def shrink(ref):
        if ref.sha256 in cache:
            return cache[ref.sha256]
        with Image.open(store.blob_path(ref)) as im:
            im.load()
            size = (max(1, int(im.width * factor)), max(1, int(im.height * factor)))
            small = im.resize(size, Image.LANCZOS)
            buf = io.BytesIO()
            if small.mode in ("RGBA", "LA", "P"):
                small.save(buf, "PNG", optimize=True)
                new = store.put_blob(buf.getvalue(), "image/png", "png")
            else:
                small.convert("RGB").save(buf, "JPEG", quality=85)
                new = store.put_blob(buf.getvalue(), "image/jpeg", "jpg")
        cache[ref.sha256] = new
        return new

    pages = []
    for pg in doc.pages:
        images = [im.model_copy(update={"image": shrink(im.image)}) for im in pg.images]
        bg = shrink(pg.background_image) if pg.background_image is not None else None
        for im in pg.images:
            degradations.append(Degradation(element_id=im.id, concept="圖片解析度", action=f"超過檔案大小預算 {p['file_size_budget_mb']} MB，長寬縮為 {factor:.0%}", lossy=True))
        pages.append(pg.model_copy(update={"images": images, "background_image": bg}))
    return doc.model_copy(update={"pages": pages})


# ---------------------------------------------------------------- 副本


def _convert_page(page: StyledPage, store: Store, p: dict[str, Any], degradations: list[Degradation]) -> StyledPage:
    text_boxes: list[StyledTextBox] = []
    for tb in page.text_boxes:
        pairs: set[tuple[str, str]] = set()
        text_boxes.append(tb.model_copy(update={"paragraphs": _map_paragraphs(tb.paragraphs, p, pairs)}))
        degradations.extend(_font_degradations(tb.id, pairs, p))
    tables: list[StyledTable] = []
    for t in page.tables:
        pairs = set()
        cells = [c.model_copy(update={"paragraphs": _map_paragraphs(c.paragraphs, p, pairs)}) for c in t.cells]
        tables.append(t.model_copy(update={"cells": cells}))
        degradations.extend(_font_degradations(t.id, pairs, p))
    shapes: list[StyledShape] = []
    images: list[StyledImage] = list(page.images)
    for s in page.shapes:
        if s.geometry == "path" and len(s.path) > int(p["max_path_segments"]):
            png, box = rasterize_path(s, p)
            images.append(StyledImage(id=s.id, box=box, z=s.z, image=store.put_blob(png, "image/png", "png")))
            degradations.append(Degradation(element_id=s.id, concept=f"複雜自訂路徑（{len(s.path)} 段）", action=f"超過 {p['max_path_segments']} 段，點陣化成 {p['raster_dpi']:.0f} dpi 的圖片（Google 簡報對複雜路徑支援差）", lossy=True))
        else:
            shapes.append(s)
    return page.model_copy(update={"text_boxes": text_boxes, "tables": tables, "shapes": shapes, "images": images})


def google_dialect(doc: StyledDoc, store: Store, p: dict[str, Any]) -> tuple[StyledDoc, list[Degradation]]:
    """成品文件的 Google 方言副本（只在記憶體裡，不存檔、不回寫成品文件）。"""
    degradations: list[Degradation] = []
    pages = [_convert_page(pg, store, p, degradations) for pg in doc.pages]
    copy = doc.model_copy(update={"pages": pages})
    copy = _shrink_images(copy, store, p, degradations)
    return copy, degradations


# ---------------------------------------------------------------- 自檢（與模板輸出器共用）


def audit_pptx(data: bytes, doc: StyledDoc) -> tuple[bool, list[str], list[Degradation]]:
    """重新讀入 PPTX 寫出器的結果，對照成品文件：頁數、每個元素是否寫出、表格列欄數、
    圓角矩形與自訂路徑的幾何是否保留。寫出器沒做到的，補記降級紀錄，不默默丟掉。"""
    from pptx import Presentation

    notes: list[str] = []
    degradations: list[Degradation] = []
    try:
        prs = Presentation(io.BytesIO(data))
    except Exception as e:
        return False, [f"自檢：輸出檔無法重新開啟（{e}）。"], degradations
    ok = True
    if len(prs.slides) != len(doc.pages):
        ok = False
        notes.append(f"自檢：投影片數 {len(prs.slides)} 與成品文件頁數 {len(doc.pages)} 不符。")
    for page, slide in zip(doc.pages, prs.slides):
        by_name = {sh.name: sh for sh in slide.shapes}
        for t in page.tables:
            sh = by_name.get(t.id)
            if sh is None or not getattr(sh, "has_table", False):
                degradations.append(Degradation(element_id=t.id, concept="表格", action="PPTX 寫出器沒有寫出這張表格（表格內容未進入輸出檔）", lossy=True))
                notes.append(f"自檢：表格 {t.id} 沒有寫進輸出檔。")
            elif len(sh.table.rows) != len(t.row_heights_pt) or len(sh.table.columns) != len(t.col_widths_pt):
                notes.append(f"自檢：表格 {t.id} 的列欄數與成品文件不符。")
                ok = False
        for s in page.shapes:
            sh = by_name.get(s.id)
            if sh is None:
                degradations.append(Degradation(element_id=s.id, concept=f"形狀（{s.geometry}）", action="PPTX 寫出器沒有寫出這個形狀", lossy=True))
                continue
            xml = etree.tostring(sh._element)
            if s.geometry == "rounded_rect" and b'prst="roundRect"' not in xml:
                degradations.append(Degradation(element_id=s.id, concept="圓角矩形", action="PPTX 寫出器以直角矩形輸出", lossy=True))
            if s.geometry == "path" and b"custGeom" not in xml:
                degradations.append(Degradation(element_id=s.id, concept="自訂路徑", action="PPTX 寫出器以外框矩形近似", lossy=True))
        for el in list(page.text_boxes) + list(page.images):
            if el.id not in by_name:
                degradations.append(Degradation(element_id=el.id, concept="元素", action="PPTX 寫出器沒有寫出這個元素", lossy=True))
    return ok, notes, degradations


def pptx_fonts(data: bytes) -> set[str]:
    """輸出檔中所有 run 的拉丁、東亞、複雜文字字型名稱。"""
    from pptx import Presentation

    prs = Presentation(io.BytesIO(data))
    names: set[str] = set()
    for slide in prs.slides:
        for el in slide._element.iter():
            if isinstance(el.tag, str) and el.tag.rsplit("}", 1)[-1] in ("latin", "ea", "cs") and el.get("typeface"):
                names.add(el.get("typeface"))
    return names


# ---------------------------------------------------------------- 入口


def export(doc: StyledDoc, basename: str, cfg: Config, store: Store) -> OutputFile:
    p = params(cfg)
    copy, degradations = google_dialect(doc, store, p)
    out = pptx_exporter.export(copy, f"{basename}_google_slides", cfg, store)
    data = store.read_blob(out.file)
    ok, notes, audit_degr = audit_pptx(data, copy)
    ok = ok and out.selfcheck_ok
    notes = list(out.selfcheck_notes) + notes
    allowed = {n.lower() for n in p["google_fonts"]} | {v.lower() for v in p["font_map"].values()} | {p[k].lower() for k in ("default_western", "cjk_sans", "cjk_serif", "cjk_kai")}
    stray = sorted(f for f in pptx_fonts(data) if f.lower() not in allowed and not f.startswith("+"))
    if stray:
        notes.append("自檢：輸出檔仍含 Google 字型庫清單以外的字型：" + "、".join(stray))
    size_mb = len(data) / 1024 / 1024
    if size_mb > float(p["file_size_limit_mb"]):
        ok = False
        notes.append(f"自檢：檔案 {size_mb:.1f} MB 超過 Google 簡報的轉換上限 {p['file_size_limit_mb']} MB。")
    notes.append("請在 Google 雲端硬碟中以 Google 簡報開啟（上傳後在檔案上按右鍵 → 選擇開啟工具 → Google 簡報）。")
    notes.append("本檔未經 Google 簡報驗證：匯入時 Google 會再轉換一次，版面可能與 PowerPoint 版略有不同。")
    return OutputFile(
        format=OutputFormat.GOOGLE_SLIDES,
        file=out.file,
        filename=f"{basename}_google_slides.pptx",
        degradations=degradations + list(out.degradations) + audit_degr,
        fonts_used=out.fonts_used,
        selfcheck_ok=ok,
        selfcheck_notes=notes,
    )
