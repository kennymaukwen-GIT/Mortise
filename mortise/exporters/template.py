"""模板輸出器：只輸出版面的「空間骨架」，給靈感模板市場用。

使用者拍下好看的版面，帶回去換成自己的內容。做法分兩段：
  1. 內容替換：在記憶體裡從成品文件導出一份「佔位版」（不存檔、不回寫成品文件）。
  2. 交給 PPTX 寫出器（mortise.exporters.pptx.export）寫檔，檔名 basename + "_template.pptx"。
他人的圖片與整頁底圖一律不輸出（著作權風險 R8）；目前一律當成非自有內容。

降階對照表（成品文件的概念 → 模板 PPTX 的物件 → 是否有損）：
  文字內容             → 同長度的佔位文字：中文用「範例文字」重複、英文用 Lorem ipsum 截到相近長度
                          （中文字算 2 個西文字寬）；保留段落數、run 數、字型、字級、粗斜體、顏色、對齊、行距 → 內容捨棄（本格式的本質）
  文字框位置、大小、旋轉 → 保留                                                     → 無損
  圖片                 → 同位置、同尺寸、同旋轉的灰色佔位框，含兩條對角線與「圖片」字樣 → 內容捨棄
  整頁底圖             → 不輸出；保留底色（沒有底色時取底圖的平均色當純色背景）     → 內容捨棄
  頁面底色             → 保留                                                       → 無損
  簡單形狀（矩形、圓角矩形、橢圓、直線、節點少的路徑）→ 保留                         → 無損
  複雜路徑（段數超過門檻，多半是插圖或標誌）→ 灰色佔位框                            → 內容捨棄
  表格                 → 保留列欄、欄寬列高、合併、底色、框線、對齊；文字清空，數值、數字格式、公式清除 → 內容捨棄
  圖層順序 z           → 重新編號（保持原順序），佔位框的對角線與字樣疊在框上        → 無損
  閱讀順序             → 保留（佔位框沿用原圖片的元素編號）                         → 無損
  其他                 → 同 PPTX 輸出器的降階對照                                   → 同 PPTX
"""

from __future__ import annotations

import io
import math
import re
from typing import Any, Optional

from PIL import Image

from ..artifacts import (
    Align,
    Box,
    Color,
    Degradation,
    FontChoice,
    OutputFile,
    OutputFormat,
    Provenance,
    StyledCell,
    StyledDoc,
    StyledPage,
    StyledParagraph,
    StyledRun,
    StyledShape,
    StyledTable,
    StyledTextBox,
    measured,
)
from ..config import Config
from ..pipeline.store import Store
from . import pptx as pptx_exporter
from .google_slides import audit_pptx

WRITER = "s6.template@1"

LOREM = (
    "Lorem ipsum dolor sit amet, consectetur adipiscing elit, sed do eiusmod tempor incididunt ut labore et dolore "
    "magna aliqua. Ut enim ad minim veniam, quis nostrud exercitation ullamco laboris nisi ut aliquip ex ea commodo "
    "consequat. Duis aute irure dolor in reprehenderit in voluptate velit esse cillum dolore eu fugiat nulla pariatur. "
    "Excepteur sint occaecat cupidatat non proident, sunt in culpa qui officia deserunt mollit anim id est laborum. "
)

DEFAULT_PARAMS: dict[str, Any] = {
    "zh_placeholder": "範例文字",  # 中文佔位文字（重複到原文長度）
    "lorem": LOREM,  # 英文佔位文字（截到相近長度）
    "lorem_word_snap": 2,  # 英文佔位文字與原文長度差在此字元數內時，截在字詞邊界
    "placeholder_fill": [217, 217, 217],  # 佔位框底色
    "placeholder_stroke": [166, 166, 166],  # 佔位框外框與對角線
    "placeholder_stroke_width_pt": 1.0,
    "placeholder_label": "圖片",
    "label_color": [118, 118, 118],
    "label_font": "Microsoft JhengHei",  # 文件裡沒有中文字型時，「圖片」字樣用的字型
    "label_max_size_pt": 28.0,
    "label_min_size_pt": 6.0,
    "min_box_for_marks_pt": 16.0,  # 佔位框短邊小於此值時不畫對角線與字樣
    "complex_path_segments": 12,  # 路徑段數超過此值視為圖稿，換成佔位框（初始值，待評測調整）
    "background_from_image_mean": True,  # 沒有底色時，用底圖平均色當純色背景
}

_WIDE = re.compile(r"[ᄀ-ᅟ⺀-鿿豈-﫿　-〿가-힯＀-｠￠-￦]")


def params(cfg: Optional[Config]) -> dict[str, Any]:
    out = dict(DEFAULT_PARAMS)
    if cfg is not None:
        out.update(cfg.section("export").get("template", {}) or {})
    return out


# ---------------------------------------------------------------- 佔位文字


class Filler:
    """產生同長度的佔位文字。每段從頭開始，同一段內的 run 接續上一個 run 的位置。"""

    def __init__(self, p: dict[str, Any]):
        self.zh = p["zh_placeholder"]
        self.lorem = p["lorem"]
        self.snap = int(p["lorem_word_snap"])
        self.reset()

    def reset(self) -> None:
        self.zh_off = 0
        self.en_off = 0

    def _zh(self, n: int) -> str:
        out = "".join(self.zh[(self.zh_off + i) % len(self.zh)] for i in range(n))
        self.zh_off += n
        return out

    def _en(self, n: int) -> str:
        """從 Lorem ipsum 取約 n 個字元：字詞邊界在 snap 個字元以內就截在邊界，否則硬截；不以空白開頭或結尾。"""
        if n <= 0:
            return ""
        src = self.lorem
        while src[self.en_off % len(src)] == " ":  # 不以空白開頭
            self.en_off += 1
        window = "".join(src[(self.en_off + i) % len(src)] for i in range(n + self.snap + 1))
        cut = n
        if window[n - 1] != " " and window[n] != " ":  # 截在字中間：找最近的字詞邊界
            ends = [i for i in range(1, len(window)) if window[i] == " " and window[i - 1] != " "]
            near = [e for e in ends if abs(e - n) <= self.snap]
            if near:
                cut = min(near, key=lambda e: (abs(e - n), e))
        out = window[:cut].rstrip()
        self.en_off += cut
        return out or window[:n]

    def line(self, text: str, east_asian: bool) -> str:
        core = text.strip()
        if not core:
            return text
        lead = text[: len(text) - len(text.lstrip())]
        trail = text[len(text.rstrip()) :]
        wide = len(_WIDE.findall(core))
        narrow = len(core) - wide
        if wide > narrow or (east_asian and wide > 0 and wide * 2 >= narrow):
            body = self._zh(max(1, wide + math.ceil(narrow / 2)))
        else:
            body = self._en(narrow + 2 * wide)
        return lead + body + trail

    def text(self, text: str, east_asian: bool) -> str:
        return "\n".join(self.line(part, east_asian) for part in text.split("\n"))


def placeholder_paragraphs(paragraphs: list[StyledParagraph], filler: Filler) -> list[StyledParagraph]:
    out = []
    for para in paragraphs:
        filler.reset()
        runs = [r.model_copy(update={"text": filler.text(r.text, r.font.east_asian)}) for r in para.runs]
        out.append(para.model_copy(update={"runs": runs}))
    return out


def allowed_placeholder_chars(p: dict[str, Any]) -> set[str]:
    """佔位版裡允許出現的字元（自檢用：確認沒有原文外流）。"""
    return set(p["zh_placeholder"]) | set(p["lorem"]) | set(p["placeholder_label"]) | set(" \t\n\r　\v")


# ---------------------------------------------------------------- 佔位框


def _rgb(v: list[int]) -> Color:
    return Color(r=int(v[0]), g=int(v[1]), b=int(v[2]))


def _corners(b: Box) -> list[tuple[float, float]]:
    """box 旋轉後的四角：左上、右上、右下、左下（順時針角度，繞中心）。"""
    cx, cy = b.x + b.w / 2, b.y + b.h / 2
    a = math.radians(b.rotation)
    ca, sa = math.cos(a), math.sin(a)
    out = []
    for dx, dy in ((-b.w / 2, -b.h / 2), (b.w / 2, -b.h / 2), (b.w / 2, b.h / 2), (-b.w / 2, b.h / 2)):
        out.append((cx + dx * ca - dy * sa, cy + dx * sa + dy * ca))
    return out


def _line_between(eid: str, p0: tuple[float, float], p1: tuple[float, float], z: int, color: Color, width: float) -> StyledShape:
    """兩點之間的直線。依⑤的約定：rotation 0 表示左上到右下，1 表示左下到右上。"""
    x0, x1 = sorted((p0[0], p1[0]))
    y0, y1 = sorted((p0[1], p1[1]))
    up = (p1[0] - p0[0]) * (p1[1] - p0[1]) < 0
    return StyledShape(id=eid, box=Box(x=x0, y=y0, w=x1 - x0, h=y1 - y0, rotation=1.0 if up else 0.0), z=z, geometry="line", stroke=color, stroke_width_pt=width)


def _label_font(doc: StyledDoc, p: dict[str, Any]) -> str:
    for page in doc.pages:
        for tb in page.text_boxes:
            for para in tb.paragraphs:
                for r in para.runs:
                    if r.font.east_asian:
                        return r.font.family.value
    return p["label_font"]


def placeholder_frame(eid: str, box: Box, z: int, p: dict[str, Any], label_font: str) -> tuple[list[StyledShape], list[StyledTextBox]]:
    """灰色佔位框：底框（沿用原元素編號）+ 兩條對角線 + 置中的「圖片」字樣。z、z+1、z+2 三層。"""
    stroke = _rgb(p["placeholder_stroke"])
    sw = float(p["placeholder_stroke_width_pt"])
    shapes = [StyledShape(id=eid, box=box, z=z, geometry="rect", fill=_rgb(p["placeholder_fill"]), stroke=stroke, stroke_width_pt=sw)]
    texts: list[StyledTextBox] = []
    if min(box.w, box.h) < float(p["min_box_for_marks_pt"]):
        return shapes, texts
    tl, tr, br, bl = _corners(box)
    shapes.append(_line_between(f"{eid}-diag1", tl, br, z + 1, stroke, sw))
    shapes.append(_line_between(f"{eid}-diag2", bl, tr, z + 1, stroke, sw))
    label = p["placeholder_label"]
    size = max(float(p["label_min_size_pt"]), min(float(p["label_max_size_pt"]), box.h * 0.2, box.w / (len(label) + 2)))
    size = round(size, 1)
    h = size * 1.4
    cy = box.y + box.h / 2
    fam = measured(label_font, 1.0, Provenance.INFERRED, WRITER)
    run = StyledRun(text=label, font=FontChoice(family=fam, verification_family=label_font, is_substituted=False, east_asian=True), size_pt=measured(size, 1.0, Provenance.INFERRED, WRITER), color=_rgb(p["label_color"]))
    para = StyledParagraph(id=f"{eid}-label-p1", runs=[run], align=Align.CENTER)
    texts.append(StyledTextBox(id=f"{eid}-label", box=Box(x=box.x, y=cy - h / 2, w=box.w, h=h, rotation=box.rotation), z=z + 2, paragraphs=[para], wrap=False))
    return shapes, texts


# ---------------------------------------------------------------- 佔位版


def _empty_table(t: StyledTable) -> StyledTable:
    """保留列欄與格式，文字清空（段落數、run 數與字級保留，列高才不會被空段落的預設字級撐開）。"""
    cells: list[StyledCell] = []
    for c in t.cells:
        paras = [para.model_copy(update={"runs": [r.model_copy(update={"text": ""}) for r in para.runs]}) for para in c.paragraphs]
        cells.append(c.model_copy(update={"paragraphs": paras, "value": None, "number_format": None, "formula": None}))
    return t.model_copy(update={"cells": cells})


def _mean_color(path) -> Color:
    with Image.open(path) as im:
        r, g, b = im.convert("RGB").resize((1, 1), Image.BOX).getpixel((0, 0))
    return Color(r=r, g=g, b=b)


def _placeholder_page(page: StyledPage, store: Store, p: dict[str, Any], label_font: str, degradations: list[Degradation]) -> StyledPage:
    filler = Filler(p)
    # 依 PPTX 寫出器的疊放規則（z 排序、同 z 時文字框 → 圖片 → 形狀）重新編號，每個元素留 4 層空間
    kinds = {"text": 0, "image": 1, "shape": 2, "table": 3}
    items = [(t.z, kinds["text"], i, "text", t) for i, t in enumerate(page.text_boxes)]
    items += [(m.z, kinds["image"], i, "image", m) for i, m in enumerate(page.images)]
    items += [(s.z, kinds["shape"], i, "shape", s) for i, s in enumerate(page.shapes)]
    items += [(t.z, kinds["table"], i, "table", t) for i, t in enumerate(page.tables)]
    text_boxes: list[StyledTextBox] = []
    shapes: list[StyledShape] = []
    tables: list[StyledTable] = []
    for rank, (_, _, _, kind, el) in enumerate(sorted(items, key=lambda x: (x[0], x[1], x[2]))):
        z = rank * 4
        if kind == "text":
            text_boxes.append(el.model_copy(update={"z": z, "paragraphs": placeholder_paragraphs(el.paragraphs, filler)}))
        elif kind == "image":
            ss, tt = placeholder_frame(el.id, el.box, z, p, label_font)
            shapes += ss
            text_boxes += tt
            degradations.append(Degradation(element_id=el.id, concept="圖片", action="模板不輸出他人的圖片（著作權），換成同尺寸的灰色佔位框", lossy=True))
        elif kind == "shape":
            if el.geometry == "path" and len(el.path) > int(p["complex_path_segments"]):
                ss, tt = placeholder_frame(el.id, el.box, z, p, label_font)
                shapes += ss
                text_boxes += tt
                degradations.append(Degradation(element_id=el.id, concept=f"複雜路徑（{len(el.path)} 段，視為插圖或標誌）", action="模板不輸出他人的圖稿，換成同尺寸的灰色佔位框", lossy=True))
            else:
                shapes.append(el.model_copy(update={"z": z}))
        else:
            tables.append(_empty_table(el).model_copy(update={"z": z}))
            degradations.append(Degradation(element_id=el.id, concept="表格內容", action="保留列欄結構與格式，儲存格文字、數值與公式清空", lossy=True))
    for tb in page.text_boxes:
        degradations.append(Degradation(element_id=tb.id, concept="文字內容", action="換成同長度的佔位文字（保留段落數、字型、字級、顏色、對齊）", lossy=True))
    bg_color = page.background_color
    if page.background_image is not None:
        if bg_color is None and p["background_from_image_mean"]:
            bg_color = _mean_color(store.blob_path(page.background_image))
            action = f"模板不輸出他人的底圖（著作權），改用底圖平均色 #{bg_color.hex} 的純色背景"
        else:
            action = "模板不輸出他人的底圖（著作權），保留頁面底色"
        degradations.append(Degradation(element_id=f"background-{page.page_index + 1}", concept="整頁底圖", action=action, lossy=True))
    return page.model_copy(update={"text_boxes": text_boxes, "images": [], "shapes": shapes, "tables": tables, "background_image": None, "background_color": bg_color})


def placeholder_doc(doc: StyledDoc, store: Store, p: dict[str, Any]) -> tuple[StyledDoc, list[Degradation]]:
    """成品文件的佔位版（只在記憶體裡）。"""
    degradations: list[Degradation] = []
    label_font = _label_font(doc, p)
    pages = [_placeholder_page(pg, store, p, label_font, degradations) for pg in doc.pages]
    return doc.model_copy(update={"pages": pages}), degradations


# ---------------------------------------------------------------- 自檢


def _leak_check(data: bytes, p: dict[str, Any]) -> list[str]:
    """確認輸出檔裡沒有圖片、沒有佔位字元以外的文字（原作內容不外流）。"""
    from pptx import Presentation
    from pptx.enum.shapes import MSO_SHAPE_TYPE

    prs = Presentation(io.BytesIO(data))
    allowed = allowed_placeholder_chars(p)
    problems: list[str] = []
    for i, slide in enumerate(prs.slides, start=1):
        for sh in slide.shapes:
            if sh.shape_type == MSO_SHAPE_TYPE.PICTURE:
                problems.append(f"自檢：第 {i} 頁仍有圖片 {sh.name}。")
            texts = []
            if sh.has_text_frame:
                texts.append(sh.text_frame.text)
            if getattr(sh, "has_table", False) and sh.has_table:
                texts += [c.text for row in sh.table.rows for c in row.cells]
            bad = {ch for t in texts for ch in t if ch not in allowed}
            if bad:
                problems.append(f"自檢：第 {i} 頁 {sh.name} 含佔位文字以外的字元：{''.join(sorted(bad))[:20]}")
        bg = slide._element.find(".//{http://schemas.openxmlformats.org/presentationml/2006/main}bg")
        if bg is not None and bg.find(".//{http://schemas.openxmlformats.org/drawingml/2006/main}blip") is not None:
            problems.append(f"自檢：第 {i} 頁背景含圖片。")
    return problems


# ---------------------------------------------------------------- 入口


def export(doc: StyledDoc, basename: str, cfg: Config, store: Store) -> OutputFile:
    p = params(cfg)
    copy, degradations = placeholder_doc(doc, store, p)
    out = pptx_exporter.export(copy, f"{basename}_template", cfg, store)
    data = store.read_blob(out.file)
    ok, notes, audit_degr = audit_pptx(data, copy)
    leaks = _leak_check(data, p)
    if leaks:
        ok = False
        notes += leaks
    notes = list(out.selfcheck_notes) + notes
    notes.append("模板：文字已換成佔位文字，圖片與整頁底圖已換成佔位框或拿掉；替換成自己的內容即可使用。")
    return OutputFile(
        format=OutputFormat.TEMPLATE,
        file=out.file,
        filename=f"{basename}_template.pptx",
        degradations=degradations + list(out.degradations) + audit_degr,
        fonts_used=out.fonts_used,
        selfcheck_ok=ok and out.selfcheck_ok,
        selfcheck_notes=notes,
    )
