"""Word 可重排模式輸出器（DOCX_FLOW）：寫出「正常的 Word 文件」，文字在主文字流裡，使用者最能自由編輯。

做法（見 docs/architecture/06-export.md 6.9）：
  - 主文字流照每頁的 reading_order 輸出成一般段落（不是文字方塊）；不在閱讀順序裡的元素依位置補在該頁最後。
  - 樣式表 StyledDoc.styles → Word 段落樣式；段落只引用樣式，與樣式不同的屬性才寫直接格式。
    標題角色（title、subtitle）依字級給大綱階層，對到內建「標題 1」「標題 2」…。
  - 頁首、頁尾、頁碼角色的段落移到 Word 的頁首或頁尾；頁碼寫成 PAGE 功能變數。
  - 頁面尺寸取第一頁；邊界由主文字流內容的最左、最右、最上、最下推算。
  - 表格輸出成正常表格（固定欄寬、列高「至少」、表頭列跨頁重複）；圖片輸出成行內圖片
    （依原比例，不超過版心或欄寬）。
  - 分欄：由文字框的水平分布偵測；偵測到時用連續分節符號切出分欄的節，欄與欄之間寫分欄符號。
  - 清單：段落開頭的編號或項目符號（1.、(1)、一、、（一）、a.、•…）連續出現時，改成 Word 的自動編號。
  - 每個來源頁從新頁開始（page_break="source_page"，預設）；"none" 則完全交給 Word 分頁。

降階對照表（成品文件的概念 → DOCX 的物件 → 是否有損）：
  | 成品文件的概念               | DOCX 的物件（可重排模式）                                   | 是否有損 |
  |------------------------------|------------------------------------------------------------|----------|
  | 閱讀順序 reading_order       | 文件本文的段落、表格、圖片序列                                | 順序無損；元素位置不保留（本模式的本質） |
  | 段落與樣式                   | 段落加 pStyle，差異寫直接格式                                 | 無損     |
  | 標題角色                     | 內建 heading N（標題 N）或基於它的樣式，帶 outlineLvl         | 無損     |
  | 文字框之間的垂直空隙         | 下一個區塊第一段的段前距離；從「排出來的」行上下緣算（_text_extent） | 近似（假履歷實測基線誤差 0.6 pt 以內） |
  | 並排的區塊（側欄、標題列、日期） | 看不見框線的表格（每塊一格，欄寬照原圖，側欄底色為儲存格底色）  | 無損；只有真的蓋在別的元素上的才用浮動物件 |
  | 文字框的左側位置             | 段落左縮排（相對於左邊界）                                    | 近似     |
  | 文字框旋轉                   | 不旋轉                                                        | 有損，記錄 |
  | 頁首、頁尾、頁碼             | 頁首頁尾部件；頁碼為 PAGE 功能變數；對齊依原位置推算           | 各頁內容不同時只保留第一種（有損，記錄） |
  | 頁面尺寸                     | 分節的 w:pgSz，取第一頁                                      | 其他頁尺寸不同時有損，記錄 |
  | 邊界                         | w:pgMar，由內容外框推算                                       | 近似     |
  | 分欄                         | 分節的 w:cols（欄寬、欄距）、連續分節、分欄符號               | 偵測不到的欄當成單欄（不記錄：成品文件沒有欄的資訊） |
  | 清單標記                     | 編號定義 w:abstractNum／w:num，段落 w:numPr                   | 對不上格式或只出現一次時保留為文字（記錄） |
  | 表格                         | 原生表格，列高「至少」、表頭列重複                            | 寬度超過版心時等比縮小（有損，記錄） |
  | 圖片                         | 行內圖片，依原比例，不超過版心或欄寬                          | 位置不保留；縮小時記錄；旋轉有損 |
  | 形狀                         | 不輸出                                                        | 有損，記錄 |
  | 頁面底色                     | w:background（全文件一種）                                    | 各頁不同時有損，記錄 |
  | 整頁底圖                     | 頁首內、文字後方、相對頁面的浮動圖片（每頁重複）              | 各頁不同時只保留第一頁的（有損，記錄） |
  | 來源的分頁位置               | 每個來源頁開頭分頁（或不分頁）                                | 頁數可能與來源不同 |
  | 註腳角色                     | 一般段落                                                      | 有損，記錄 |
  | 圖層順序 z                   | 主文字流沒有這個概念                                          | 有損，本模式的本質 |
"""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass, replace
from typing import Optional

from docx import Document
from docx.shared import Emu
from lxml import etree

from ..artifacts import Align, Box, OutputFile, OutputFormat, StyledDoc, StyledPage, StyledParagraph
from ..artifacts.common import Role
from ..config import Config
from ..pipeline.store import Store
from .docx_common import (
    MAX_PAGE_PT,
    URI_PIC,
    URI_WPS,
    Ctx,
    WordStyle,
    build_paragraph,
    build_styles,
    build_table,
    context_fonts,
    drawing_anchor,
    drawing_run,
    el,
    eighth_pt,
    emu,
    empty_paragraph,
    finish_package,
    hard_breaks,
    image_descriptor,
    make_ctx,
    picture_pic,
    q,
    rfonts,
    save_bytes,
    selfcheck,
    set_sect,
    setup_package,
    sub,
    table_texts,
    textbox_wsp,
    twip,
)
from .docx_fixed import MIME, params_from

DEFAULT_PARAMS = {
    "page_break": "source_page",  # "source_page"：每個來源頁從新頁開始；"none"：完全交給 Word 分頁
    "preserve_block_gaps": True,  # 文字框之間的垂直空隙寫成段前距離
    "auto_numbering": True,  # 連續的清單標記改成 Word 自動編號
    "detect_columns": True,  # 由文字框的水平分布偵測分欄
    "builtin_headings": True,  # 標題角色對到內建「標題 N」樣式
    "min_margin_pt": 18.0,  # 推算出的邊界下限（pt）
    "max_margin_ratio": 0.4,  # 推算出的邊界上限（頁寬或頁高的比例）
    "column_max_width_ratio": 0.6,  # 寬度小於版心此比例的文字框才可能是一欄
    "column_min_height_ratio": 0.2,  # 每一欄的高度至少佔頁高此比例
    "center_tolerance": 0.1,  # 頁首頁尾的框中心離版心中線在此比例內，視為置中
    "min_gap_pt": 0.5,  # 小於此值的空隙不寫段前距離
    "bottom_slack_pt": 12.0,  # 下邊界比內容外框少留這麼多（取這個值與頁高比例的較大者）：
    "bottom_slack_ratio": 0.03,  # 目標軟體的行高略大時誤差會累積，最後一段（連同標題的「與下段同頁」）才不會被擠到下一頁
    "float_side_images": True,  # 上緣在目前文字流底部之上的圖片改成浮動圖片（錨在最近的段落），不往下堆疊
    "float_offflow_boxes": True,  # 同理，這類文字框（側欄、壓在圖上的說明）改成浮動文字方塊
    "nowrap_slack_ratio": 0.15,  # 浮動文字方塊不換行時加寬的比例（同版面固定模式）
    # 並排的版面（側欄＋主欄、標題列的左右兩塊、標題右邊的日期）：寫成看不見框線的表格（見「並排的版面」一節）
    "side_by_side_tables": True,  # false 則用舊做法（錨在段落上的浮動文字方塊；在別的裝置上容易錯位）
    "max_table_depth": 2,  # 表格最多套幾層（第二層例如經歷標題與右邊的日期）
    "band_overlap_pt": 3.0,  # 上下兩個元素重疊不超過這麼多（pt）……
    "band_overlap_ratio": 0.25,  # ……也不超過較矮者高度的這個比例，仍當成上下分開（文字框的外框常常略有重疊）
    "band_overlap_rendered_pt": 1.0,  # 用排出來的行範圍（見 _text_extent）比較時的容許重疊（pt）：行範圍已含行距，重疊多半是真的交錯
    "tab_min_offset_ratio": 0.3,  # 單行小框的左緣在另一個框寬度的這個比例之後，才接到那一行的行尾（定位點）
    "overlay_min_ratio": 0.3,  # 與另一個元素的交集超過自身面積的這個比例，才算「蓋在上面」而用浮動物件
    "fit_page_gaps": True,  # 原圖內容貼到紙邊、放不進 Word 頁面的文字區時，等比壓縮元素之間與段落前後的空隙
    "min_gap_scale": 0.6,  # 空隙最多壓縮到原來的這個比例
    "rules_as_borders": True,  # 段落正下方的水平線改成段落下框線（例如履歷標題下的線）
    "rule_max_gap_pt": 10.0,  # 線與段落字的下緣距離不超過這麼多（pt）才算「段落正下方」
    "bottom_room": True,  # 每頁從新頁開始時，下邊界只留到頁尾為止（不貼著內容），讓目標軟體多出來的行高有地方放
}

HF_ROLES = {Role.HEADER, Role.FOOTER, Role.PAGE_NUMBER}


# ---------- 清單標記 ----------

_CJK_DIGITS = {c: i for i, c in enumerate("零一二三四五六七八九", start=0)}
_LIST_PATTERNS = [
    ("bullet", re.compile(r"^([•‧·●○■□▪◆◇▶➤])\s*")),  # 圓點類符號後面可以不接空白
    ("bullet", re.compile(r"^([\-–—*])\s+")),  # 破折號類一定要接空白，以免把「-5%」當成清單
    ("decimal", re.compile(r"^(\d{1,3})([.)、])\s*")),
    ("decimal_paren", re.compile(r"^([（(])(\d{1,3})([）)])\s*")),
    ("cjk", re.compile(r"^([一二三四五六七八九十]{1,3})、\s*")),
    ("cjk_paren", re.compile(r"^([（(])([一二三四五六七八九十]{1,3})([）)])\s*")),
    ("lower", re.compile(r"^([a-z])([.)])\s+")),
    ("upper", re.compile(r"^([A-Z])([.)])\s+")),
]
_NUMFMT = {"bullet": "bullet", "decimal": "decimal", "decimal_paren": "decimal", "cjk": "taiwaneseCountingThousand",
           "cjk_paren": "taiwaneseCountingThousand", "lower": "lowerLetter", "upper": "upperLetter"}


def _cjk_value(s: str) -> int:
    if s == "十":
        return 10
    if "十" in s:
        a, _, b = s.partition("十")
        return (_CJK_DIGITS.get(a, 1) if a else 1) * 10 + (_CJK_DIGITS.get(b, 0) if b else 0)
    return _CJK_DIGITS.get(s, 0)


@dataclass
class Marker:
    kind: str
    value: int
    lvl_text: str  # Word 的 lvlText，例如 "%1."、"（%1）"、"•"
    length: int  # 標記（含後面空白）佔幾個字元
    text: str


def detect_marker(text: str) -> Optional[Marker]:
    for kind, rx in _LIST_PATTERNS:
        m = rx.match(text)
        if not m or m.end() >= len(text.rstrip()):
            continue
        g = m.groups()
        if kind == "bullet":
            return Marker(kind, 0, g[0], m.end(), m.group(0))
        if kind == "decimal":
            return Marker(kind, int(g[0]), "%1" + g[1], m.end(), m.group(0))
        if kind == "decimal_paren":
            return Marker(kind, int(g[1]), g[0] + "%1" + g[2], m.end(), m.group(0))
        if kind == "cjk":
            return Marker(kind, _cjk_value(g[0]), "%1、", m.end(), m.group(0))
        if kind == "cjk_paren":
            return Marker(kind, _cjk_value(g[1]), g[0] + "%1" + g[2], m.end(), m.group(0))
        return Marker(kind, ord(g[0].lower()) - 96, "%1" + g[1], m.end(), m.group(0))
    return None


def _marker_width_pt(text: str, size_pt: float) -> float:
    """標記寬度的估計（em）：全形 1、數字與字母 0.56、標點與空白 0.28。"""
    w = 0.0
    for ch in text:
        o = ord(ch)
        if o >= 0x2E80 or 0xFF00 <= o <= 0xFFEF:
            w += 1.0
        elif ch.isalnum():
            w += 0.56
        elif ch in "•‧·●○■□▪◆◇▶➤*":
            w += 0.6
        else:
            w += 0.28
    return w * size_pt


# ---------- 區塊 ----------


@dataclass
class Block:
    kind: str  # para、image、table
    page: int
    box: Box
    elem_id: str
    item: object = None  # StyledParagraph、StyledImage、StyledTable
    box_paras: Optional[list] = None  # 同一文字框的全部段落（字型脈絡用）
    first_in_box: bool = False
    col: Optional[int] = None
    numpr: Optional[tuple[int, int]] = None
    strip: int = 0
    hanging_tw: int = 0
    wrap: bool = True


def _main_paras(tb) -> list[StyledParagraph]:
    return [p for p in tb.paragraphs if p.role not in HF_ROLES]


def _page_blocks(page: StyledPage, ctx: Ctx) -> list[Block]:
    """一頁的主文字流：照 reading_order；不在裡面的元素依位置補在最後。形狀不輸出。"""
    by_id = {t.id: ("text", t) for t in page.text_boxes}
    by_id.update({i.id: ("image", i) for i in page.images})
    by_id.update({t.id: ("table", t) for t in page.tables})
    shapes = {s.id for s in page.shapes}
    order = [i for i in page.reading_order if i in by_id]
    rest = sorted((i for i in by_id if i not in set(order)), key=lambda i: (by_id[i][1].box.y, by_id[i][1].box.x))
    for i in rest:
        if by_id[i][0] != "text" or _main_paras(by_id[i][1]):
            ctx.degrade(i, "閱讀順序", "不在閱讀順序中，依位置排在該頁最後", False)
    blocks: list[Block] = []
    for i in order + rest:
        if i in shapes:
            continue
        kind, item = by_id[i]
        if item.box.rotation and abs(item.box.rotation) > 0.05:
            ctx.degrade(i, "旋轉", "可重排模式的段落、表格與行內圖片不能旋轉，以不旋轉輸出", True)
        if kind == "text":
            paras = _main_paras(item)
            for k, sp in enumerate(paras):
                if sp.role == Role.FOOTNOTE:
                    ctx.degrade(sp.id, "註腳", "沒有本文中的參照位置，輸出成一般段落", True)
                blocks.append(Block("para", page.page_index, item.box, item.id, sp, item.paragraphs, k == 0, wrap=item.wrap))
        else:
            blocks.append(Block(kind, page.page_index, item.box, item.id, item))
    return blocks


def _assign_lists(blocks: list[Block], ctx: Ctx) -> list[tuple[Marker, list[Block]]]:
    """連續、同類型、編號遞增的清單段落成為一組；單獨一個有編號的段落保留為文字。"""
    groups: list[tuple[Marker, list[Block]]] = []
    cur: Optional[tuple[Marker, list[Block]]] = None
    last_val = 0
    for b in blocks:
        m = None
        if b.kind == "para":
            m = detect_marker("".join(r.text for r in b.item.runs))
        if m is not None and cur is not None and m.kind == cur[0].kind and m.lvl_text == cur[0].lvl_text and (m.kind == "bullet" or m.value == last_val + 1):
            cur[1].append(b)
            last_val = m.value
            continue
        if cur is not None:
            groups.append(cur)
            cur = None
        if m is not None:
            cur = (m, [b])
            last_val = m.value
    if cur is not None:
        groups.append(cur)
    out = []
    for m, members in groups:
        if m.kind != "bullet" and len(members) < 2:
            ctx.degrade(members[0].item.id, "清單編號", f"只有一項的編號「{m.text.strip()}」保留為文字，不啟用自動編號", False)
            continue
        out.append((m, members))
    return out


def _write_numbering(docx_doc, groups: list[tuple[Marker, list[Block]]], ctx: Ctx) -> None:
    """編號定義：每組一個 abstractNum 加一個 num（起始值照原件）。"""
    if not groups:
        return
    numbering = docx_doc.part.numbering_part.element
    abs_ids = [int(a.get(q("w:abstractNumId"))) for a in numbering.findall(q("w:abstractNum"))]
    num_ids = [int(n.get(q("w:numId"))) for n in numbering.findall(q("w:num"))]
    next_abs, next_num = max(abs_ids, default=-1) + 1, max(num_ids, default=0) + 1
    first_num = numbering.find(q("w:num"))
    for m, members in groups:
        sp0 = members[0].item
        size = sp0.runs[0].size_pt.value * ctx.scale if sp0.runs else 11.0
        marker_runs = [r for r in sp0.runs][:1]
        hanging = twip(max(_marker_width_pt(m.text, size), 0.5 * size))
        a = el("w:abstractNum", {"w:abstractNumId": next_abs})
        sub(a, "w:multiLevelType", {"w:val": "singleLevel"})
        lvl = sub(a, "w:lvl", {"w:ilvl": 0})
        sub(lvl, "w:start", {"w:val": max(1, m.value) if m.kind != "bullet" else 1})
        sub(lvl, "w:numFmt", {"w:val": _NUMFMT[m.kind]})
        sub(lvl, "w:lvlText", {"w:val": m.lvl_text})
        sub(lvl, "w:lvlJc", {"w:val": "left"})
        ppr = sub(lvl, "w:pPr")
        sub(ppr, "w:ind", {"w:left": hanging, "w:hanging": hanging})
        rpr = sub(lvl, "w:rPr")
        if marker_runs:
            latin, ea = context_fonts(members[0].box_paras, ctx)
            fam = ctx.font_name(marker_runs[0].font, members[0].elem_id, context_latin=latin)
            rfonts(rpr, fam, fam if marker_runs[0].font.east_asian else ea)
            ctx.fonts.update({fam, ea})
        if first_num is not None:
            first_num.addprevious(a)  # 綱要規定 abstractNum 全部排在 num 前面
        else:
            numbering.append(a)
        n = sub(numbering, "w:num", {"w:numId": next_num})
        sub(n, "w:abstractNumId", {"w:val": next_abs})
        for b in members:
            b.numpr, b.strip, b.hanging_tw = (next_num, 0), m.length if b is members[0] else (detect_marker("".join(r.text for r in b.item.runs)) or m).length, hanging
        next_abs += 1
        next_num += 1


# ---------- 分欄 ----------


def _detect_columns(page: StyledPage, blocks: list[Block], left: float, right_x: float, params: dict, s: float):
    """由文字框的水平分布偵測分欄。回傳（欄規格 [(欄寬, 欄距)]、元素編號 → 第幾欄）或 None。"""
    content_w = right_x - left
    seen = {}
    for b in blocks:
        seen.setdefault(b.elem_id, b)
    narrow = [b for b in seen.values() if b.box.w * s < params["column_max_width_ratio"] * content_w]
    wide = [b for b in seen.values() if b not in narrow and b.kind == "para"]
    clusters: list[list] = []
    for b in sorted(narrow, key=lambda b: b.box.x):
        x0, x1 = b.box.x * s, (b.box.x + b.box.w) * s
        if clusters and x0 < clusters[-1][1] - 1.0:
            clusters[-1][1] = max(clusters[-1][1], x1)
            clusters[-1][2].append(b)
        else:
            clusters.append([x0, x1, [b]])
    if not 2 <= len(clusters) <= 4:
        return None
    min_h = params["column_min_height_ratio"] * page.height_pt * s
    spans = []
    for x0, x1, members in clusters:
        y0 = min(m.box.y for m in members) * s
        y1 = max(m.box.y + m.box.h for m in members) * s
        if y1 - y0 < min_h:
            return None
        spans.append((y0, y1))
    for (a0, a1), (b0, b1) in zip(spans, spans[1:]):
        if min(a1, b1) - max(a0, b0) <= 0:
            return None
    band0, band1 = min(y for y, _ in spans), max(y for _, y in spans)
    for b in wide:  # 欄區內有橫跨的寬文字框，表示不是分欄
        if min(band1, (b.box.y + b.box.h) * s) - max(band0, b.box.y * s) > 1.0:
            return None
    widths = [c[1] - c[0] for c in clusters]
    gaps = [max(6.0, n[0] - c[1]) for c, n in zip(clusters, clusters[1:])]
    f = (content_w - sum(gaps)) / sum(widths)
    spec = [(w * f, g) for w, g in zip(widths, gaps + [0.0])]
    member = {m.elem_id: i for i, c in enumerate(clusters) for m in c[2]}
    return spec, member


# ---------- 本文寫入器 ----------


class BodyWriter:
    """依序把段落與表格放進本文，處理分頁、分欄符號與分節（sectPr 掛在每節最後一段）。"""

    def __init__(self, docx_doc, page_args: dict):
        self.final = docx_doc.element.body.find(q("w:sectPr"))
        self.page_args = page_args
        self.cols = None
        self.sect_type = None
        self.count = 0
        self.sections = 1
        self.last = None
        self.pending: Optional[str] = None  # page 或 column

    def pop_break(self) -> Optional[str]:
        k, self.pending = self.pending, None
        return k

    def add(self, element) -> None:
        if element.tag != q("w:p") and self.pending:  # 表格前面不能直接放分頁：加一個載體段落
            self.add(self._break_paragraph(self.pop_break()))
        self.final.addprevious(element)
        self.last = element
        self.count += 1

    def _break_paragraph(self, kind: str):
        p = empty_paragraph(1.0, page_break_before=kind == "page")
        if kind == "column":
            r = sub(p, "w:r")
            sub(r, "w:br", {"w:type": "column"})
        return p

    def new_section(self, cols, new_page: bool) -> None:
        if self.count == 0:  # 這一節還沒有內容：直接改它的設定
            self.cols = cols
            if new_page and self.sections > 1:
                self.sect_type = None
            return
        if self.last.tag != q("w:p"):
            self.add(empty_paragraph(1.0))
        sect = el("w:sectPr")
        if self.sections == 1:  # 頁首頁尾參照只掛在第一節，其餘各節沿用
            for ref in self.final:
                if ref.tag in (q("w:headerReference"), q("w:footerReference")):
                    sect.append(ref.__copy__())
        set_sect(sect, cols=self.cols, sect_type=self.sect_type, **self.page_args)
        self.last.find(q("w:pPr")).append(sect)
        self.sections += 1
        self.cols = cols
        self.sect_type = None if new_page else "continuous"
        self.count = 0
        if new_page:
            self.pending = None

    def finish(self) -> None:
        set_sect(self.final, cols=self.cols, sect_type=self.sect_type, **self.page_args)
        if self.sections > 1:
            for ref in list(self.final):
                if ref.tag in (q("w:headerReference"), q("w:footerReference")):
                    self.final.remove(ref)


# ---------- 頁首頁尾 ----------


def _inject_page_field(p) -> bool:
    """把段落中第一串數字換成 PAGE 功能變數（顯示值保留原數字）。"""
    for r in p.findall(q("w:r")):
        t = r.find(q("w:t"))
        if t is None or not re.search(r"\d+", t.text or ""):
            continue
        m = re.search(r"\d+", t.text)
        rpr = r.find(q("w:rPr"))
        new = []

        def mk(kind=None, text=None, instr=None):
            nr = el("w:r")
            if rpr is not None:
                nr.append(rpr.__copy__())
            if kind:
                sub(nr, "w:fldChar", {"w:fldCharType": kind})
            if instr:
                it = sub(nr, "w:instrText", text=instr)
                it.set(q("xml:space"), "preserve")
            if text:
                tt = sub(nr, "w:t", text=text)
                tt.set(q("xml:space"), "preserve")
            return nr

        if m.start():
            new.append(mk(text=t.text[:m.start()]))
        new += [mk("begin"), mk(instr=" PAGE "), mk("separate"), mk(text=m.group(0)), mk("end")]
        if m.end() < len(t.text):
            new.append(mk(text=t.text[m.end():]))
        for nr in new:
            r.addprevious(nr)
        p.remove(r)
        return True
    return False


def _hf_align(box: Box, s: float, left: float, right_x: float, tol: float) -> Optional[str]:
    content_w = right_x - left
    if box.w * s > 0.6 * content_w:
        return None
    c = (box.x + box.w / 2) * s
    mid = (left + right_x) / 2
    if abs(c - mid) < tol * content_w:
        return "center"
    return "right" if c > mid else "left"


def _write_header_footer(docx_doc, doc: StyledDoc, ctx: Ctx, styles: dict[str, WordStyle], s: float, left: float, right_x: float,
                         params: dict, expect: list[str]) -> tuple[float, float]:
    """頁首頁尾：取第一個有內容的頁；其他頁內容（數字除外）不同時記錄。回傳（頁首距離, 頁尾距離）。"""
    slots: dict[str, list] = {"header": [], "footer": []}
    signature: dict[str, str] = {}
    hdr_dist, ftr_dist = 36.0, 36.0
    for page in doc.pages:
        found: dict[str, list] = {"header": [], "footer": []}
        for tb in page.text_boxes:
            for sp in tb.paragraphs:
                if sp.role not in HF_ROLES:
                    continue
                top = sp.role == Role.HEADER or (sp.role != Role.FOOTER and (tb.box.y + tb.box.h / 2) < page.height_pt / 2)
                found["header" if top else "footer"].append((tb, sp))
        for slot, items in found.items():
            if not items:
                continue
            sig = "|".join(re.sub(r"\d+", "#", "".join(r.text for r in sp.runs)) for _, sp in items)
            if not slots[slot]:
                slots[slot] = sorted(items, key=lambda x: (x[0].box.y, x[0].box.x))
                signature[slot] = sig
            elif sig != signature[slot]:
                ctx.degrade(f"page-{page.page_index + 1}", f"{'頁首' if slot == 'header' else '頁尾'}內容", "各頁內容不同，Word 只保留第一種", True)
    sec = docx_doc.sections[0]
    for slot, items in slots.items():
        if not items:
            continue
        part = sec.header if slot == "header" else sec.footer
        part.is_linked_to_previous = False
        root = part._element
        for ch in list(root):
            root.remove(ch)
        for tb, sp in items:
            latin, ea = context_fonts(tb.paragraphs, ctx)
            align = _hf_align(tb.box, s, left, right_x, params["center_tolerance"])
            p = build_paragraph(sp, ctx, tb.id, latin, ea, styles.get(sp.style_id or ""), diff=True, align_override=align)
            if sp.role == Role.PAGE_NUMBER and not _inject_page_field(p):
                ctx.degrade(sp.id, "頁碼", "找不到數字，保留為一般文字", True)
            root.append(p)
            expect.append("".join(r.text for r in sp.runs))
        page_h = doc.pages[0].height_pt
        if slot == "header":
            hdr_dist = max(0.0, min(tb.box.y for tb, _ in items) * s)
        else:
            ftr_dist = max(0.0, (page_h - max(tb.box.y + tb.box.h for tb, _ in items)) * s)
    return hdr_dist, ftr_dist


def _beside_all(b: Block, blocks: list[Block]) -> bool:
    """浮動物件和本頁每個垂直範圍重疊的區塊都水平不重疊，才能用矩形文繞圖；
    否則（例如壓在行內圖片上）文繞圖會把那個區塊擠到浮動物件下方，版面變長。"""
    for o in blocks:
        if o.elem_id == b.elem_id:
            continue
        v = min(o.box.y + o.box.h, b.box.y + b.box.h) - max(o.box.y, b.box.y)
        if v > 1.0 and not (b.box.x >= o.box.x + o.box.w - 1.0 or b.box.x + b.box.w <= o.box.x + 1.0):
            return False
    return True


def _float_anchor(b: Block, anchors: list, flow_bottom: float):
    """元素的上緣在目前文字流底部之上（往下堆疊會把版面拉長、頁數變多）時，改成浮動物件：
    錨點優先找本頁最近一個與它水平不重疊的區塊（並排：矩形文繞圖），否則找最近一個上緣不低於它的區塊
    （重疊，例如壓在圖上的說明：不文繞圖）。回傳（錨點框, 錨點段落, 是否並排）；不需要浮動時回傳 None。"""
    if b.box.y >= flow_bottom - 1.0:
        return None
    for box, p in reversed(anchors):
        if b.box.x >= box.x + box.w - 1.0 or b.box.x + b.box.w <= box.x + 1.0:
            return box, p, True
    for box, p in reversed(anchors):
        if box.y <= b.box.y:
            return box, p, False
    return None


# ---------- 並排的版面：看不見框線的表格 ----------
#
# 為什麼不用浮動文字方塊：錨在段落上的浮動方塊，位置是「相對錨點段落」算的；錨點段落在別台裝置上因為字型不同
# 而上下移動、或檢視器（例如 iOS 的預覽）不照 Word 的規則放浮動物件時，方塊就錯位、互相重疊，主文字流也不會讓開。
# 表格是主文字流的一部分：格子裡的字變多，整列一起長高、後面的內容一起往下推，不會重疊。
#
# 切法（遞迴的 XY 切分）：
#   1. 先橫切：上下之間有完整空隙（容許 band_overlap_pt 以內的輕微重疊）的地方切開，得到由上而下的「帶」。
#   2. 每一帶再直切：左右之間有完整空隙的地方切開；切得開（兩欄以上）就是並排 → 一列表格，每欄一格，
#      格子裡的元素遞迴同樣處理（最多 max_table_depth 層，例如經歷標題與右邊的日期是第二層）。
#   3. 都切不開的一群元素是互相重疊的：
#      a. 單行的小文字框落在另一個文字框某一行的右半邊（例如「2025 – present」在「NORTHWIND — …」那一行的右邊）
#         → 接到那一段的行尾，用定位字元與定位點對齊（同一行、不會重疊）。
#      b. 真的蓋在別的元素上面（與它的交集超過自身面積的 overlay_min_ratio，例如壓在圖上的字）
#         → 浮動物件，錨在被蓋住的那個元素的第一段，不文繞圖。只有這種情況用浮動物件。
#      c. 其他（輕微交錯、但不是蓋在上面）→ 由上而下依序排。
# 欄寬照原圖：格與格的界線放在兩欄空隙的中點；某一欄背後有自己的填色矩形（側欄）時，界線改在矩形的邊緣，
# 矩形的顏色寫成儲存格底色，表格也延伸到矩形的範圍（可以超出頁面邊界，例如貼齊紙邊的側欄）。
# 列高寫「至少」（不是固定值，字變多時會長高）；允許跨頁拆列（不寫 cantSplit：一列比一頁還高時，Word 會把放不下的部分裁掉）。


@dataclass
class _Item:
    """版面切分的單位：一個文字框（主文字流的段落）、圖片或表格。座標是輸出座標（pt，已乘比例）。"""

    kind: str
    id: str
    x0: float
    y0: float
    x1: float
    y1: float
    obj: object
    blocks: list
    rank: int
    ty0: Optional[float] = None  # 排出來的第一行上緣（估計，見 _text_extent）；沒有時同 y0
    ty1: Optional[float] = None  # 排出來的最後一行下緣（估計）；沒有時同 y1

    @property
    def top(self) -> float:
        return self.y0 if self.ty0 is None else self.ty0

    @property
    def bottom(self) -> float:
        return self.y1 if self.ty1 is None else self.ty1

    @property
    def area(self) -> float:
        return max(0.0, self.x1 - self.x0) * max(0.0, self.y1 - self.y0)

    def single_line(self) -> bool:
        return self.kind == "text" and len(self.blocks) == 1 and (not self.obj.wrap or self.blocks[0].item.source_line_count <= 1)


@dataclass
class _Row:
    cols: list
    children: list
    bounds: list
    fills: list
    fill_ids: list
    top: float
    bottom: float


def _cut(items: list[_Item], axis: str, params: dict, glue: Optional[list[tuple[float, float]]] = None) -> list[list[_Item]]:
    """沿一個方向找完整的空隙切開。橫切時每個元素上下各縮一點（輕微重疊不算重疊）。
    glue：橫切時不可以切開的範圍（例如側欄的填色矩形：它的上下範圍裡切開，底色就斷成兩截）。"""
    spans = []
    for it in items:
        if axis == "y":  # 用排出來的上下緣（見 _text_extent）：框很貼字時，框看起來分開、排出來的行其實是並排的
            limit = params["band_overlap_rendered_pt"] if it.ty0 is not None else params["band_overlap_pt"]
            t = min(limit, params["band_overlap_ratio"] * (it.bottom - it.top))
            spans.append((it.top + t, it.bottom - t, it))
        else:
            spans.append((it.x0 + 0.5, it.x1 - 0.5, it))
    spans += [(a, b, None) for a, b in (glue or [])]
    spans.sort(key=lambda s: (s[0], s[2].rank if s[2] is not None else -1))
    groups: list[list[_Item]] = []
    end = float("-inf")
    for a, b, it in spans:
        if groups and a < end:
            end = max(end, b)
        else:
            groups.append([])
            end = b
        if it is not None:
            groups[-1].append(it)
    return [g for g in groups if g]


def _para_spans(it: _Item) -> list[tuple[float, float]]:
    """文字框裡每一段的上下緣估計（輸出座標）：行數 × 行高 + 段後距離，再等比對到框高。"""
    hs = []
    for b in it.blocks:
        sp = b.item
        size = max((r.size_pt.value for r in sp.runs), default=10.0)
        ls = sp.line_spacing.value if sp.line_spacing else None
        lh = ls.value if (ls is not None and ls.mode.value == "exact") else (ls.value if ls is not None else 1.0) * 1.2 * size
        hs.append((max(1, sp.source_line_count) * lh, sp.space_after_pt))
    total = sum(a + b for a, b in hs) or 1.0
    f = (it.y1 - it.y0) / total
    out, y = [], it.y0
    for a, b in hs:
        out.append((y, y + a * f))
        y += (a + b) * f
    return out


def _attach(group: list[_Item], params: dict) -> tuple[list[_Item], dict]:
    """互相重疊的一群：找出「接在別人身上」的元素（同一行的小框、或蓋在上面的元素）。
    回傳（底層元素, {被接的元素編號: [(接上來的元素, 方式 tab／float, 段落序號)]}）。"""
    attached: dict[str, list] = {}
    base = []
    for b in sorted(group, key=lambda i: (i.y0, i.x0)):
        best, best_area = None, 0.0
        for a in group:
            if a is b or a.area <= b.area:
                continue
            ix = min(a.x1, b.x1) - max(a.x0, b.x0)
            iy = min(a.y1, b.y1) - max(a.y0, b.y0)
            if ix > 0 and iy > 0 and ix * iy > best_area:
                best, best_area = a, ix * iy
        if best is None:
            base.append(b)
            continue
        cy = (b.y0 + b.y1) / 2
        if b.single_line() and best.kind == "text" and b.x0 >= best.x0 + params["tab_min_offset_ratio"] * (best.x1 - best.x0):
            spans = _para_spans(best)
            k = min(range(len(spans)), key=lambda i: 0.0 if spans[i][0] <= cy <= spans[i][1] else min(abs(cy - spans[i][0]), abs(cy - spans[i][1])))
            target = best.blocks[k].item
            last_line_top = spans[k][1] - (spans[k][1] - spans[k][0]) / max(1, target.source_line_count)
            if spans[k][0] - 2.0 <= cy <= spans[k][1] + 2.0 and cy >= last_line_top - 2.0:
                attached.setdefault(best.id, []).append((b, "tab", k))
                continue
        if best_area >= params["overlay_min_ratio"] * max(b.area, 1e-6):
            attached.setdefault(best.id, []).append((b, "float", 0))
            continue
        base.append(b)
    return base, attached


def _layout(items: list[_Item], depth: int, params: dict, attached: dict, shapes: Optional[list] = None) -> list:
    """遞迴的 XY 切分。節點：("item", 元素)、("row", _Row)、("stack", [元素])。attached 收集接在別人身上的元素。
    shapes 是可以當底色的填色矩形：裡面有一部分（不是全部）元素的矩形，橫切時不可以切開。"""
    out: list = []
    glue = []
    for x0, y0, x1, y1, _ in shapes or []:
        inside = [it for it in items if x0 - 2 <= (it.x0 + it.x1) / 2 <= x1 + 2 and y0 - 2 <= (it.y0 + it.y1) / 2 <= y1 + 2]
        if inside and len(inside) < len(items):
            glue.append((y0, y1))
    for band in _cut(items, "y", params, glue):
        if len(band) == 1:
            out.append(("item", band[0]))
            continue
        cols = _cut(band, "x", params)
        if len(cols) > 1 and depth < params["max_table_depth"]:
            out.append(("row", _Row(cols=[sorted(c, key=lambda i: (i.y0, i.rank)) for c in cols], children=[], bounds=[], fills=[],
                                    fill_ids=[], top=min(i.top for i in band), bottom=max(i.bottom for i in band))))
            continue
        base, att = _attach(band, params)
        if att and len(base) < len(band):
            attached.update(att)
            out.extend(_layout(base, depth, params, attached, shapes))
        else:
            out.append(("stack", sorted(band, key=lambda i: (i.y0, i.rank))))
    for kind, node in out:
        if kind == "row":
            node.children = [_layout(c, depth + 1, params, attached, shapes) for c in node.cols]
    return out


def _flush_right(it: _Item, tl: float, tr: float) -> bool:
    """單行、靠左、右緣貼齊文字區右緣、而且在右半邊的文字框（例如經歷標題右邊的日期）。"""
    return (it.kind == "text" and not it.obj.wrap and len(it.blocks) == 1 and it.blocks[0].item.align in (Align.LEFT, Align.JUSTIFY)
            and it.x1 >= tr - 2.0 and it.x0 > tl + 0.3 * (tr - tl))


def _flush_left(it: _Item, tl: float, tr: float) -> bool:
    """單行、被判成置中、左緣貼齊文字區左緣、右邊還有空間的文字框（例如經歷標題「BSc Food Science — …」）。
    單獨一行看不出是置中還是靠左；左緣貼齊欄的左緣，就是靠左。照置中輸出的話，字一寬就往兩邊長、
    在格子裡甚至折成兩行置中。"""
    return (it.kind == "text" and not it.obj.wrap and len(it.blocks) == 1 and it.blocks[0].item.align == Align.CENTER
            and it.blocks[0].item.source_line_count <= 1 and it.x0 <= tl + 1.5 and it.x1 < tr - 2.0)


def _fill_shapes(page: StyledPage, s: float) -> list:
    """可以當儲存格底色的形狀：有填色、不旋轉的矩形，而且不是整頁的底色。"""
    W, H = page.width_pt * s, page.height_pt * s
    out = []
    for sh in page.shapes:
        if sh.geometry not in ("rect", "rounded_rect") or sh.fill is None or sh.fill.a < 0.5 or abs(sh.box.rotation) > 0.05:
            continue
        x0, y0, x1, y1 = sh.box.x * s, sh.box.y * s, (sh.box.x + sh.box.w) * s, (sh.box.y + sh.box.h) * s
        if (x1 - x0) >= 0.95 * W and (y1 - y0) >= 0.95 * H:
            continue
        out.append((x0, y0, x1, y1, sh))
    return out


def _plan_row(row: _Row, cont: tuple[float, float], shapes: list, W: float) -> None:
    """一列的欄界線與底色。cont 是容器的文字區（左、右）。"""
    ext = [(min(i.x0 for i in c), max(i.x1 for i in c), min(i.y0 for i in c), max(i.y1 for i in c)) for c in row.cols]
    fills: list = []
    for k, (cx0, cx1, cy0, cy1) in enumerate(ext):
        lo = ext[k - 1][1] if k > 0 else float("-inf")
        hi = ext[k + 1][0] if k + 1 < len(ext) else float("inf")
        cand = [sh for sh in shapes if sh[0] <= cx0 + 2 and sh[2] >= cx1 - 2 and sh[1] <= cy0 + 2 and sh[3] >= cy1 - 2 and sh[0] >= lo - 1 and sh[2] <= hi + 1]
        fills.append(min(cand, key=lambda sh: (sh[2] - sh[0]) * (sh[3] - sh[1])) if cand else None)
    bounds = [min(cont[0], ext[0][0], fills[0][0] if fills[0] else float("inf"))]
    for k in range(1, len(ext)):
        gap0, gap1 = ext[k - 1][1], ext[k][0]
        if fills[k - 1] is not None and gap0 - 1 <= fills[k - 1][2] <= gap1 + 1:
            bounds.append(fills[k - 1][2])
        elif fills[k] is not None and gap0 - 1 <= fills[k][0] <= gap1 + 1:
            bounds.append(fills[k][0])
        else:
            bounds.append((gap0 + gap1) / 2)
    bounds.append(max(cont[1], ext[-1][1], fills[-1][2] if fills[-1] else float("-inf")))
    bounds[0], bounds[-1] = max(0.0, bounds[0]), min(W, bounds[-1])
    row.bounds = bounds
    row.fills = [f[4].fill if f else None for f in fills]
    row.fill_ids = [f[4].id if f else None for f in fills]
    ys = [f[1] for f in fills if f] + [row.top]
    row.top = min(ys)
    row.bottom = max([f[3] for f in fills if f] + [row.bottom])


def _inner_gaps(it: _Item) -> float:
    if it.kind != "text":
        return 0.0
    return sum(b.item.space_before_pt + (b.item.space_after_pt if k < len(it.blocks) - 1 else 0.0) for k, b in enumerate(it.blocks))


def _measure(nodes: list, start: float) -> tuple[float, float]:
    """一串節點由 start 往下排的（內容高度, 空隙高度）：空隙可以等比壓縮，內容不行。列取最高的那一格。"""
    content = gaps = 0.0
    y = start
    for kind, node in nodes:
        if kind == "row":
            best = (0.0, 0.0)
            top = max(node.top, y)  # 列的上緣比上一個內容的下緣還高時，只能從上一個內容的下緣開始（見 emit）
            for kids in node.children:
                c, g = _measure(kids, top)
                if c + g > sum(best):
                    best = (c, g)
            content += best[0]
            gaps += max(0.0, node.top - y) + best[1]
            y = max(node.bottom, top + sum(best))
            continue
        its = [node] if kind == "item" else node
        for it in its:
            g = _inner_gaps(it)
            gaps += max(0.0, it.top - y) + g
            content += max(0.0, it.bottom - it.top - g)
            y = max(y, it.bottom)
    return content, gaps


class _FlowLayout:
    """把版面樹寫成本文的段落與表格（看不見框線）。"""

    def __init__(self, docx_doc, ctx: Ctx, styles: dict, store: Store, params: dict, expect: list, s: float, page: StyledPage,
                 attached: dict, shapes: list, W: float, gap_scale: float):
        self.docx_doc, self.ctx, self.styles, self.store, self.params = docx_doc, ctx, styles, store, params
        self.expect, self.s, self.page, self.attached, self.shapes, self.W = expect, s, page, attached, shapes, W
        self.f = gap_scale
        self.used_fills: set[str] = set()
        self.below: dict[str, float] = {}  # 元素編號 → 它下面那條改成段落框線的線的下緣（之後的空隙從這裡算）
        self.rules = []  # 水平線（可以改成段落下框線）：（x0, x1, y, 線寬, 顏色, 形狀）
        for sh in page.shapes:
            if sh.geometry == "line" and sh.stroke is not None and abs(sh.box.h) * s < 1.0 and sh.box.w * s > 20.0 and sh.stroke.a >= 0.5:
                self.rules.append((sh.box.x * s, (sh.box.x + sh.box.w) * s, sh.box.y * s, (sh.stroke_width_pt or 0.75) * s, sh.stroke.hex, sh))

    def _rule_under(self, it: _Item, k: int, span: tuple[float, float], next_top: float, tl: float, tr: float):
        """段落正下方（離字的下緣不超過 rule_max_gap_pt、在下一段之前）、橫跨段落大部分寬度的水平線 → 段落下框線。
        回傳（框線寬八分之一點, 與字的距離 pt, 顏色, 線的下緣, 形狀編號）或 None。"""
        best = None
        for x0, x1, y, lw, color, sh in self.rules:
            if sh.id in self.used_fills:
                continue
            d = y - span[1]
            if not (-1.0 <= d <= self.params["rule_max_gap_pt"] and y < next_top):
                continue
            ov = min(x1, tr) - max(x0, max(tl, it.x0))
            if ov < 0.5 * (x1 - x0):
                continue
            if best is None or d < best[1]:
                best = (min(96, max(2, eighth_pt(lw))), max(0.0, d), color, y + lw, sh.id)
        return best

    # --- 單一元素 ---

    def _text(self, it: _Item, tl: float, tr: float, gap: float, in_cell: bool, brk: Optional[str]) -> list:
        latin, ea = context_fonts(it.blocks[0].box_paras, self.ctx)
        tails: dict[int, list] = {}
        floats = []
        for b, how, k in self.attached.get(it.id, []):
            if how == "tab":
                tails.setdefault(k, []).append(b)
            else:
                floats.append(b)
        aligns = {b.item.align for b in it.blocks}
        left = max(0.0, it.x0 - tl)
        right = 0.0
        align_override = None
        if in_cell:
            right = max(0.0, tr - it.x1)
            slack = self.params["nowrap_slack_ratio"] * (it.x1 - it.x0)
            if _flush_left(it, tl, tr):
                aligns, align_override = {Align.LEFT}, "left"  # 貼齊左緣的單行置中字：當成靠左，右邊留給較寬的字型
            elif _flush_right(it, tl, tr):
                aligns, align_override = {Align.RIGHT}, "right"  # 單行、貼齊右緣（例如右邊的日期）：靠右對齊，左邊留給較寬的字型
            if not it.obj.wrap:
                if aligns <= {Align.LEFT, Align.JUSTIFY}:
                    right = 0.0  # 單行靠左：右邊留給較寬的字型
                elif aligns == {Align.RIGHT}:
                    left = 0.0
                elif aligns == {Align.CENTER}:
                    d = min(left, right)
                    left, right = left - d, right - d
            elif aligns == {Align.RIGHT}:
                left = max(0.0, left - slack)  # 靠右的多行字：左邊多留一點，較寬的字型才不會把長字（例如電子郵件）從中間折斷
            elif aligns == {Align.CENTER}:
                d = min(left, right, slack / 2)
                left, right = left - d, right - d
        out = []
        spans = _para_spans(it)
        for k, b in enumerate(it.blocks):
            sp = b.item
            border = None
            if self.params["rules_as_borders"] and self.rules:
                nxt = spans[k + 1][0] if k + 1 < len(spans) else float("inf")
                rule = self._rule_under(it, k, spans[k], nxt, tl, tr)
                if rule is not None:
                    sz, d, color, bottom, sid = rule
                    border = (sz, int(round(min(31.0, d))), color)
                    self.used_fills.add(sid)
                    self.ctx.degrade(sid, "形狀 line", "段落正下方的水平線：改成段落的下框線（跟著段落移動）", False)
                    # 框線與上方的距離已經佔掉段後空間的一部分：段後距離扣掉它，下面的內容才不會多往下移
                    sp = sp.model_copy(update={"space_after_pt": max(0.0, sp.space_after_pt - d - sz / 8.0)})
                    if k == len(it.blocks) - 1:
                        self.below[it.id] = bottom
            if aligns <= {Align.RIGHT, Align.CENTER} and (hb := hard_breaks(sp, (it.x1 - it.x0) / self.s)) is not None:
                sp = hb
                self.ctx.degrade(it.id, "斷行", "靠右或置中的多行字：照原圖的斷行位置寫成強制換行（換了字型也不會改變斷行）", False)
            tail = []
            r_tw = twip(right) if right > 1.0 else 0
            for t in sorted(tails.get(k, []), key=lambda t: t.x0):
                tsp = t.blocks[0].item
                if tsp.align == Align.RIGHT or t.x1 >= it.x1 - 1.0:
                    tail.append(("right", twip(min(t.x1, tr) - tl), tsp, t.id))
                else:
                    tail.append(("left", twip(t.x0 - tl), tsp, t.id))
                r_tw = twip(max(0.0, tr - max(it.x1, t.x1))) if in_cell else 0
                self.expect.append("".join(r.text for r in tsp.runs))
                self.ctx.degrade(t.id, "文字框位置", "與另一個文字框的某一行同高（例如標題右邊的日期）：接在那一行的行尾，用定位點對齊", False)
            p = build_paragraph(sp, self.ctx, b.elem_id, latin, ea, self.styles.get(b.item.style_id or ""), diff=True,
                                extra_before_tw=twip(gap * self.f) if (k == 0 and gap > self.params["min_gap_pt"]) else 0,
                                page_break_before=(brk == "page" and k == 0), numpr=b.numpr, strip_chars=b.strip,
                                left_tw=twip(left) if left > 1.0 else 0, hanging_tw=b.hanging_tw, right_tw=r_tw,
                                gap_scale=self.f, tail=tail or None, align_override=align_override, border_bottom=border)
            self.expect.append("".join(r.text for r in b.item.runs)[b.strip:])
            out.append(p)
        for fl in floats:
            self._float(fl, it, out[0], tl)
        return out

    def _float(self, b: _Item, a: _Item, anchor_p, tl: float) -> None:
        """真的蓋在別的元素上面：浮動物件，錨在被蓋住的元素的第一段，不文繞圖。"""
        pid = self.ctx.new_id()
        w, h = b.x1 - b.x0, b.y1 - b.y0
        if b.kind == "text":
            latin, ea = context_fonts(b.blocks[0].box_paras, self.ctx)
            paras = [build_paragraph(x.item, self.ctx, x.elem_id, latin, ea, self.styles.get(x.item.style_id or ""), diff=True,
                                     numpr=x.numpr, strip_chars=x.strip, hanging_tw=x.hanging_tw) for x in b.blocks]
            self.expect.extend("".join(r.text for r in x.item.runs)[x.strip:] for x in b.blocks)
            if not b.obj.wrap:
                w += w * self.params["nowrap_slack_ratio"]
            child, uri, wps = textbox_wsp(paras, w, h, 0.0, b.obj.wrap, autofit=True), URI_WPS, True
        elif b.kind == "image":
            rid, _ = self.docx_doc.part.get_or_add_image(image_descriptor(self.store, b.obj.image))
            child, uri, wps = picture_pic(rid, w, h, 0.0, pid, b.id), URI_PIC, False
        else:
            return
        anc = drawing_anchor(child, uri, x=b.x0 - tl, y=b.y0 - a.y0, w=w, h=h, rot=0.0, rel_height=251659264 + pid, behind=False,
                             pr_id=pid, name=b.id, h_rel="column", v_rel="paragraph", wrap="none", layout_in_cell=True)
        anchor_p.append(drawing_run(anc, wps))
        self.ctx.degrade(b.id, "文字框位置" if b.kind == "text" else "圖片位置",
                         "蓋在另一個元素上面（交集超過自身面積的一定比例）：輸出成錨在被蓋住元素第一段的浮動物件，不文繞圖", False)

    def _image(self, it: _Item, tl: float, tr: float, gap: float, brk: Optional[str]) -> list:
        w, h = it.x1 - it.x0, it.y1 - it.y0
        avail = tr - tl
        if w > avail + 0.5:
            self.ctx.degrade(it.id, "圖片大小", f"超過可用寬度，依原比例縮小為 {avail / w:.0%}", True)
            w, h = avail, h * avail / w
        inline = self.docx_doc.part.new_pic_inline(image_descriptor(self.store, it.obj.image), Emu(emu(w)), Emu(emu(h)))
        p = el("w:p")
        ppr = sub(p, "w:pPr")
        if brk == "page":
            sub(ppr, "w:pageBreakBefore")
        sub(ppr, "w:spacing", {"w:before": twip(gap * self.f) if gap > self.params["min_gap_pt"] else 0, "w:after": 0, "w:line": 240, "w:lineRule": "auto"})
        off = it.x0 - tl
        if abs((it.x0 + it.x1) / 2 - (tl + tr) / 2) < self.params["center_tolerance"] * avail and off > 1.0:
            sub(ppr, "w:jc", {"w:val": "center"})
        elif off > 1.0 and off + w <= avail + 0.5:
            sub(ppr, "w:ind", {"w:left": twip(off)})
        # 圖片那一行的字級設成 1 pt：行高才不會比圖片多出一截字型的下緣（並排的格子裡多出來的高度會把列撐到下一頁）
        mrpr = sub(ppr, "w:rPr")
        sub(mrpr, "w:sz", {"w:val": 2})
        sub(mrpr, "w:szCs", {"w:val": 2})
        r = sub(p, "w:r")
        rpr = sub(r, "w:rPr")
        sub(rpr, "w:sz", {"w:val": 2})
        sub(rpr, "w:szCs", {"w:val": 2})
        sub(r, "w:drawing").append(inline)
        out = [p]
        for b, how, _ in self.attached.get(it.id, []):
            self._float(b, it, p, tl)
        return out

    def _table(self, it: _Item, tl: float, tr: float, gap: float) -> list:
        out = []
        if gap > self.params["min_gap_pt"]:
            out.append(empty_paragraph(max(1.0, gap * self.f)))
        indent = it.x0 - tl
        t = build_table(it.obj, self.ctx, self.styles, exact_rows=False, diff=True, width_limit_pt=tr - tl,
                        indent_pt=indent if (1.0 < indent and indent + sum(it.obj.col_widths_pt) * self.s <= tr - tl + 0.5) else 0.0)
        self.expect.extend(table_texts(it.obj))
        out.append(t)
        return out

    # --- 一串節點 ---

    def emit(self, nodes: list, tl: float, tr: float, start: float, in_cell: bool, brk: Optional[str] = None,
             limit_bottom: Optional[float] = None) -> list:
        """一串節點 → 段落與表格。tl、tr 是文字區的左右（輸出座標）；start 是上一個內容的下緣。
        limit_bottom：頁面文字區的下緣（只在最外層給，限制底色列的最小高度，不讓它擠出這一頁）。"""
        out: list = []
        y = start
        shrink = 0.0  # 到目前為止空隙被壓縮掉的高度（估計輸出位置用）
        for kind, node in nodes:
            if kind == "row":
                gap = node.top - y
                if out and out[-1].tag == q("w:tbl") or gap > self.params["min_gap_pt"]:
                    out.append(empty_paragraph(max(1.0, gap * self.f)))  # 兩個表格之間一定要有段落，不然會被併成一個
                    shrink += max(0.0, gap) * (1 - self.f)
                if brk == "page":
                    if out and out[0].tag == q("w:p"):
                        ppr = out[0].find(q("w:pPr"))
                        ppr.insert(0, el("w:pageBreakBefore"))
                    else:
                        out.insert(0, empty_paragraph(1.0, page_break_before=True))
                    brk = None
                row_top_out = node.top - shrink
                out.append(self._row(node, tl, tr, limit_bottom - row_top_out if limit_bottom is not None else None, start=max(node.top, y)))
                c, g = _measure([("row", node)], y)
                shrink += (1 - self.f) * g
                y = max(node.bottom, y + c + g)  # 列被上面的內容往下推時，下緣也跟著往下
                continue
            for it in ([node] if kind == "item" else node):
                gap = it.top - y
                if it.kind == "text":
                    els = self._text(it, tl, tr, gap, in_cell, brk)
                elif it.kind == "image":
                    els = self._image(it, tl, tr, gap, brk)
                else:
                    els = self._table(it, tl, tr, gap)
                    if out and out[-1].tag == q("w:tbl") and els[0].tag == q("w:tbl"):
                        out.append(empty_paragraph(1.0))
                    if brk == "page":
                        els.insert(0, empty_paragraph(1.0, page_break_before=True))
                brk = None
                shrink += (1 - self.f) * (max(0.0, gap) + _inner_gaps(it))
                out.extend(els)
                y = max(y, it.bottom, self.below.get(it.id, 0.0))
        return out

    def _row(self, row: _Row, tl: float, tr: float, max_h: Optional[float], start: Optional[float] = None) -> etree._Element:
        """一列並排的版面 → 一列的表格：每欄一格，沒有框線，欄寬照原圖。
        start：這一列實際開始的位置（列的上緣比上一個內容的下緣還高時，是上一個內容的下緣）。每一格的空隙從這裡算，
        只有真的和上面內容衝突的那一格被往下推，其他格維持原圖的位置（例如姓名右邊、比姓名下緣高一點的聯絡方式）。"""
        start = row.top if start is None else start
        _plan_row(row, (tl, tr), self.shapes, self.W)
        widths = [b - a for a, b in zip(row.bounds, row.bounds[1:])]
        t = el("w:tbl")
        tpr = sub(t, "w:tblPr")
        sub(tpr, "w:tblW", {"w:w": sum(twip(w) for w in widths), "w:type": "dxa"})
        sub(tpr, "w:jc", {"w:val": "left"})
        ind = row.bounds[0] - tl
        if abs(ind) > 0.05:
            sub(tpr, "w:tblInd", {"w:w": twip(ind), "w:type": "dxa"})
        bd = sub(tpr, "w:tblBorders")
        for side in ("top", "left", "bottom", "right", "insideH", "insideV"):
            sub(bd, f"w:{side}", {"w:val": "nil"})
        sub(tpr, "w:tblLayout", {"w:type": "fixed"})
        mar = sub(tpr, "w:tblCellMar")
        for side in ("top", "left", "bottom", "right"):
            sub(mar, f"w:{side}", {"w:w": 0, "w:type": "dxa"})
        sub(tpr, "w:tblLook", {"w:val": "0000", "w:firstRow": 0, "w:lastRow": 0, "w:firstColumn": 0, "w:lastColumn": 0, "w:noHBand": 1, "w:noVBand": 1})
        grid = sub(t, "w:tblGrid")
        for w in widths:
            sub(grid, "w:gridCol", {"w:w": twip(w)})
        tr_el = sub(t, "w:tr")
        trpr = sub(tr_el, "w:trPr")
        height = row.bottom - row.top
        if any(row.fills):
            if max_h is not None:
                # 留一段安全距離：目標軟體排出來的列上緣比估計的低一點時，最小高度仍放得進這一頁；放不進的話
                # LibreOffice 會把整列移到下一頁（實測），這一頁就只剩頁首
                height = min(height, max_h - max(self.params["bottom_slack_pt"], self.params["bottom_slack_ratio"] * self.page.height_pt * self.s))
            if height > 1.0:
                sub(trpr, "w:trHeight", {"w:val": twip(height), "w:hRule": "atLeast"})
        for k, (kids, cell_items) in enumerate(zip(row.children, row.cols)):
            x0, x1 = row.bounds[k], row.bounds[k + 1]
            cx0, cx1 = min(i.x0 for i in cell_items), max(i.x1 for i in cell_items)
            for rx0, rx1, ry, _, _, _ in self.rules:  # 格子裡的標題線比字寬時，文字區放寬到線的範圍（改成框線後才一樣長）
                if rx0 >= x0 - 1 and rx1 <= x1 + 1 and row.top - 1 <= ry <= row.bottom + 1:
                    cx0, cx1 = min(cx0, rx0), max(cx1, rx1)
            ml, mr = max(0.0, cx0 - x0), max(0.0, x1 - cx1)
            single = all(i.kind == "text" and not i.obj.wrap for i in cell_items)
            aligns = {b.item.align for i in cell_items if i.kind == "text" for b in i.blocks}
            if single and k > 0 and k == len(row.cols) - 1 and mr <= 3.0 and aligns <= {Align.LEFT, Align.JUSTIFY}:
                aligns = {Align.RIGHT}  # 最右邊一格、貼齊右緣的單行字（例如日期）：當成靠右
            if single and len(cell_items) == 1 and aligns == {Align.CENTER} and ml <= 1.0 and mr > 1.0:
                aligns = {Align.LEFT}  # 貼齊格子左緣的單行置中字：其實是靠左（見 _flush_left）
            if single and aligns <= {Align.LEFT, Align.JUSTIFY}:
                mr = min(mr, 1.0)  # 只有單行靠左的字：右邊的空隙也給字用（換成較寬的字型時不折行）
            elif single and aligns == {Align.RIGHT}:
                ml = min(ml, 1.0)
            elif aligns == {Align.RIGHT} and all(i.kind == "text" for i in cell_items):
                ml = max(0.0, ml - self.params["nowrap_slack_ratio"] * (cx1 - cx0))
            # 格子裡第一個元素與列上緣的距離寫成第一段的段前距離，不寫儲存格上邊距：
            # 實測 LibreOffice 24.2 把一列裡最大的儲存格上邊距套到同一列的每一格，其他格的字因此往下掉
            mt = 0.0
            tc = sub(tr_el, "w:tc")
            pr = sub(tc, "w:tcPr")
            sub(pr, "w:tcW", {"w:w": twip(x1 - x0), "w:type": "dxa"})
            borders = sub(pr, "w:tcBorders")
            for side in ("top", "left", "bottom", "right"):
                sub(borders, f"w:{side}", {"w:val": "nil"})
            if row.fills[k] is not None:
                sub(pr, "w:shd", {"w:val": "clear", "w:color": "auto", "w:fill": row.fills[k].hex})
                self.used_fills.add(row.fill_ids[k])
                self.ctx.degrade(row.fill_ids[k], "形狀 rect", "並排區塊背後的填色矩形：改成表格儲存格的底色", False)
            m = sub(pr, "w:tcMar")
            for side, v in (("top", mt), ("left", ml), ("bottom", 0.0), ("right", mr)):
                sub(m, f"w:{side}", {"w:w": twip(v), "w:type": "dxa"})
            sub(pr, "w:vAlign", {"w:val": "top"})
            els = self.emit(kids, x0 + ml, x1 - mr, start, in_cell=True)
            if not els or els[-1].tag != q("w:p"):
                els.append(empty_paragraph(1.0))  # 儲存格最後一定要是段落
            for e in els:
                tc.append(e)
        return t


def _text_extent(it: _Item, ctx: Ctx, s: float) -> tuple[float, float]:
    """文字框在目標軟體裡「排出來」的上下緣（估計，輸出座標）：空隙要從排出來的位置算，不是從框算。
    為什麼：框的上下緣是辨識量到的字形範圍，原生 PDF 的框常常貼著字（單行框高約 0.94 × 字級），
    但文字引擎每行佔「行高」（單倍行距約 1.15 × 字級），第一行的基線也不在框上緣 + 字級。
    直接用框算空隙，每個框會多出一兩點，一頁累積下來整份被拉長一成。
    做法：第一行基線照成品文件的 first_baseline_pt 放回原位；文字引擎把基線放在行上緣下方
    「固定行高 × 0.8」（固定行距；實測見 docx_fixed._baseline_shift）或「字型上緣量度 × 字級」（倍數行距），
    由此推回第一行的上緣；高度 = 各段行數 × 行高 + 段與段之間的距離。沒有 first_baseline_pt 時照框。"""
    from .docx_common import font_first_baseline_em, font_line_height_em

    tb = it.obj
    paras = [b.item for b in it.blocks]
    fb = getattr(tb, "first_baseline_pt", None)
    if fb is None or not paras or not all(sp.runs for sp in paras):
        return it.y0, it.y1
    latin, _ = context_fonts(it.blocks[0].box_paras, ctx)

    def metrics(sp: StyledParagraph) -> Optional[tuple[float, float]]:
        r = max(sp.runs, key=lambda r: r.size_pt.value)
        size = r.size_pt.value * s
        ls = sp.line_spacing.value if sp.line_spacing else None
        if ls is not None and ls.mode.value == "exact":
            return ls.value * s, 0.8 * ls.value * s
        fam = (r.font.verification_family or r.font.family.value) if r.font.east_asian else ctx.font_name(r.font, context_latin=latin, record=False)
        asc, lh = font_first_baseline_em(fam), font_line_height_em(fam)
        if asc is None or lh is None:
            return None
        return (ls.value if ls is not None else 1.0) * lh * size, asc * size

    ms = [metrics(sp) for sp in paras]
    if any(m is None for m in ms):
        return it.y0, it.y1
    top = it.y0 + fb * s - ms[0][1]
    h = 0.0
    for k, (sp, (lh, _)) in enumerate(zip(paras, ms)):
        h += max(1, sp.source_line_count) * lh
        if k:
            h += sp.space_before_pt * s
        if k < len(paras) - 1:
            h += sp.space_after_pt * s
    return top, top + h


def _box_top(tb, page_index: int, ctx: Ctx, s: float) -> float:
    """一個文字框排出來的第一行上緣（估計，輸出座標；推算上邊界用）。"""
    paras = _main_paras(tb)
    blocks = [Block("para", page_index, tb.box, tb.id, sp, tb.paragraphs, k == 0) for k, sp in enumerate(paras)]
    b = tb.box
    it = _Item("text", tb.id, b.x * s, b.y * s, (b.x + b.w) * s, (b.y + b.h) * s, tb, blocks, 0)
    return min(it.y0, _text_extent(it, ctx, s)[0])


def _page_items(page: StyledPage, blocks: list[Block], s: float, ctx: Optional[Ctx] = None) -> list[_Item]:
    items: dict[str, _Item] = {}
    order = {i: k for k, i in enumerate(page.reading_order)}
    for b in blocks:
        it = items.get(b.elem_id)
        if it is None:
            bx = b.box
            obj = next((t for t in page.text_boxes if t.id == b.elem_id), None) if b.kind == "para" else b.item
            it = _Item("text" if b.kind == "para" else b.kind, b.elem_id, bx.x * s, bx.y * s, (bx.x + bx.w) * s, (bx.y + bx.h) * s, obj, [],
                       order.get(b.elem_id, 10**6 + len(items)))
            items[b.elem_id] = it
        if b.kind == "para":
            it.blocks.append(b)
    if ctx is not None:
        for it in items.values():
            if it.kind == "text":
                it.ty0, it.ty1 = _text_extent(it, ctx, s)
    return list(items.values())


def _write_layout_page(writer: "BodyWriter", docx_doc, page: StyledPage, blocks: list[Block], ctx: Ctx, styles: dict, store: Store,
                       params: dict, expect: list, s: float, left: float, right_x: float, top: float, H: float, bottom: float) -> None:
    """不是報紙式分欄的頁：用版面樹寫成段落與看不見框線的表格。"""
    items = _page_items(page, blocks, s, ctx)
    if not items:
        return
    attached: dict = {}
    shapes = _fill_shapes(page, s)
    nodes = _layout(items, 0, params, attached, shapes)
    W = page.width_pt * s
    for kind, node in nodes:  # 先排好每一列，量高度時才知道底色列的上下緣
        if kind == "row":
            _plan_row(node, (left, right_x), shapes, W)
    f = 1.0
    if params["fit_page_gaps"]:
        c, g = _measure(nodes, top)
        avail = H - top - bottom - max(params["bottom_slack_pt"], params["bottom_slack_ratio"] * H)
        if c + g > avail and g > 0:
            f = max(params["min_gap_scale"], min(1.0, (avail - c) / g))
            ctx.degrade(f"page-{page.page_index + 1}", "垂直空隙",
                        f"原圖的內容貼到紙邊，放不進 Word 頁面的文字區：元素之間與段落前後的空隙等比壓縮為 {f:.0%}", True)
    lay = _FlowLayout(docx_doc, ctx, styles, store, params, expect, s, page, attached, shapes, W, f)
    brk = writer.pop_break() if writer.pending == "page" else None
    els = lay.emit(nodes, left, right_x, top, in_cell=False, brk=brk, limit_bottom=H - bottom - 2.0)
    for sh in page.shapes:
        if sh.id not in lay.used_fills:
            ctx.degrade(sh.id, f"形狀 {sh.geometry}", "可重排模式不輸出形狀", True)
    for e in els:
        writer.add(e)


# ---------- 主程式 ----------


def export(doc: StyledDoc, basename: str, cfg: Config, store: Store) -> OutputFile:
    params = params_from(cfg, DEFAULT_PARAMS, "docx_flow")
    ctx = make_ctx(doc, cfg)
    ctx._next_id = 10001  # 避開 python-docx 給行內圖片的編號
    first = doc.pages[0]
    s = min(1.0, MAX_PAGE_PT / max(first.width_pt, first.height_pt))
    ctx.scale = s
    if s < 1.0:
        ctx.degrade("page-1", "頁面尺寸", f"超過 Word 上限 22 英吋，整份等比縮小為 {s:.1%}", True)
    for page in doc.pages[1:]:
        if abs(page.width_pt - first.width_pt) > 0.5 or abs(page.height_pt - first.height_pt) > 0.5:
            ctx.degrade(f"page-{page.page_index + 1}", "各頁尺寸不同", "可重排模式只用第 1 頁的頁面尺寸", True)
    docx_doc = Document()
    sizes = Counter(round(r.size_pt.value * s, 1) for p in doc.pages for t in p.text_boxes for sp in t.paragraphs if sp.role == Role.BODY for r in sp.runs)
    setup_package(docx_doc, ctx, sizes.most_common(1)[0][0] if sizes else 11.0)
    styles = build_styles(docx_doc, doc, ctx, params["builtin_headings"])

    # 邊界：主文字流內容的外框（所有頁合併）
    W, H = first.width_pt * s, first.height_pt * s
    xs0, xs1, ys0, ys1 = [], [], [], []
    same = [p for p in doc.pages if abs(p.width_pt - first.width_pt) <= 0.5 and abs(p.height_pt - first.height_pt) <= 0.5]
    for page in same:  # 只用和第一頁同尺寸的頁推算邊界
        boxes = [t.box for t in page.text_boxes if _main_paras(t)] + [i.box for i in page.images] + [t.box for t in page.tables]
        for b in boxes:
            xs0.append(b.x * s)
            xs1.append((b.x + b.w) * s)
            ys0.append(b.y * s)
            ys1.append((b.y + b.h) * s)
        if params["side_by_side_tables"]:  # 第一行排出來的上緣可能比框高（見 _text_extent）：上邊界要留到那裡，第一行的基線才在原位
            ys0 += [_box_top(t, page.page_index, ctx, s) for t in page.text_boxes if _main_paras(t)]

    def clamp(v: float, dim: float) -> float:
        return min(max(v, params["min_margin_pt"]), params["max_margin_ratio"] * dim)

    left = clamp(min(xs0, default=72.0), W)
    right = clamp(W - max(xs1, default=W - 72.0), W)
    top = clamp(min(ys0, default=72.0), H)
    bottom = clamp(H - max(ys1, default=H - 72.0) - max(params["bottom_slack_pt"], params["bottom_slack_ratio"] * H), H)
    if params["side_by_side_tables"] and params["page_break"] == "source_page" and params["bottom_room"]:
        # 每個來源頁本來就從新頁開始：下邊界不必貼著內容，留到頁尾（或最小邊界）為止。
        # 並排的區塊是表格，整列的高度都在主文字流裡；目標軟體的行高略大時，多出來的高度才有地方放，不會把最後一列擠到下一頁
        ftops = [tb.box.y * s for p in same for tb in p.text_boxes if tb.paragraphs and all(sp.role in (Role.FOOTER, Role.PAGE_NUMBER) for sp in tb.paragraphs)
                 and tb.box.y + tb.box.h / 2 > p.height_pt / 2]
        bottom = max(params["min_margin_pt"], (H - min(ftops) + 2.0) if ftops else 0.0)
        bottom = min(bottom, params["max_margin_ratio"] * H)
    right_x = W - right
    expect: list[str] = []
    hdr, ftr = _write_header_footer(docx_doc, doc, ctx, styles, s, left, right_x, params, expect)
    page_args = dict(w_pt=W, h_pt=H, margins=(top, right, bottom, left), header_pt=min(hdr, top), footer_pt=min(ftr, bottom))

    # 頁面底色與整頁底圖
    colors = {p.background_color.hex if p.background_color else None for p in doc.pages}
    if len(colors) == 1 and first.background_color is not None and first.background_color.hex != "FFFFFF":
        bg = el("w:background", {"w:color": first.background_color.hex})
        docx_doc.element.insert(0, bg)
        st = docx_doc.settings.element
        dbs = el("w:displayBackgroundShape")
        zoom = st.find(q("w:zoom"))
        zoom.addnext(dbs) if zoom is not None else st.insert(0, dbs)  # 依綱要排在 w:zoom 之後
    elif len(colors) > 1:
        ctx.degrade("document", "頁面底色", "Word 的頁面色彩只能全文件一種；各頁不同，不輸出", True)
    bg_images = [p.background_image for p in doc.pages if p.background_image is not None]
    if bg_images:
        hdr_part = docx_doc.sections[0].header
        hdr_part.is_linked_to_previous = False
        rid, _ = hdr_part.part.get_or_add_image(image_descriptor(store, bg_images[0]))
        pid = ctx.new_id()
        pic = picture_pic(rid, W, H, 0.0, pid, "background")
        a = drawing_anchor(pic, URI_PIC, x=0, y=0, w=W, h=H, rot=0.0, rel_height=251658240, behind=True, pr_id=pid, name="background")
        hroot = hdr_part._element
        p0 = hroot.find(q("w:p"))
        if p0 is None:
            p0 = sub(hroot, "w:p")
        p0.append(drawing_run(a, False))
        ctx.degrade("background", "整頁底圖", "放在頁首、文字後方，每頁重複", False)
        if len({r.sha256 for r in bg_images}) > 1 or len(bg_images) < len(doc.pages):
            ctx.degrade("background", "各頁底圖不同", "只保留第一頁的底圖", True)

    # 本文
    writer = BodyWriter(docx_doc, page_args)
    page_blocks = [_page_blocks(page, ctx) for page in doc.pages]
    if params["auto_numbering"]:
        _write_numbering(docx_doc, _assign_lists([b for blocks in page_blocks for b in blocks], ctx), ctx)
    for page, blocks in zip(doc.pages, page_blocks):
        col_info = _detect_columns(page, blocks, left, right_x, params, s) if params["detect_columns"] else None
        if col_info:
            for b in blocks:
                b.col = col_info[1].get(b.elem_id)
            ctx.degrade(f"page-{page.page_index + 1}", "分欄", f"偵測為 {len(col_info[0])} 欄，欄寬依版心等比調整", False)
        if page.page_index > 0 and params["page_break"] == "source_page":
            writer.pending = "page"
        new_page_pending = page.page_index > 0 and params["page_break"] == "source_page"
        if not col_info and params["side_by_side_tables"]:
            if writer.cols is not None:
                writer.new_section(None, new_page=new_page_pending and writer.pending == "page")
            _write_layout_page(writer, docx_doc, page, blocks, ctx, styles, store, params, expect, s, left, right_x, top, H, bottom)
            if not blocks and page.page_index > 0 and params["page_break"] == "source_page":
                writer.add(empty_paragraph(1.0, page_break_before=writer.pop_break() == "page"))
            continue
        for sh in page.shapes:
            ctx.degrade(sh.id, f"形狀 {sh.geometry}", "可重排模式不輸出形狀", True)
        prev_bottom: dict = {}
        anchors: list = []  # 本頁已輸出的文字框（框, 第一段）：旁置圖片的錨點候選
        cur_col: Optional[int] = None
        floated: set[str] = set()
        for b in blocks:
            if b.elem_id in floated:
                continue
            fl = None
            if b.kind == "para" and b.first_in_box and params["float_offflow_boxes"]:
                fl = _float_anchor(b, anchors, prev_bottom.get(b.col if b.col is not None else "span", top) / s)
            if fl is not None:
                # 不屬於主文字流的文字框：浮動文字方塊，錨在最近的段落
                abox, ap, _ = fl
                beside = _beside_all(b, blocks)
                floated.add(b.elem_id)
                members = [x for x in blocks if x.elem_id == b.elem_id]
                latin, ea = context_fonts(b.box_paras, ctx)
                paras = [build_paragraph(x.item, ctx, x.elem_id, latin, ea, styles.get(x.item.style_id or ""), diff=True,
                                         numpr=x.numpr, strip_chars=x.strip, hanging_tw=x.hanging_tw) for x in members]
                expect.extend("".join(r.text for r in x.item.runs)[x.strip:] for x in members)
                w, h, x0 = b.box.w * s, b.box.h * s, b.box.x * s
                if not b.wrap:
                    extra = w * params["nowrap_slack_ratio"]
                    aligns = {x.item.align for x in members}
                    x0 -= extra / 2 if aligns == {Align.CENTER} else (extra if aligns == {Align.RIGHT} else 0.0)
                    w += extra
                pid = ctx.new_id()
                hx, hrel = (x0 - left, "margin") if b.col is None else (x0, "page")
                a = drawing_anchor(textbox_wsp(paras, w, h, 0.0, b.wrap, autofit=True), URI_WPS, x=hx, y=(b.box.y - abox.y) * s, w=w, h=h,
                                   rot=0.0, rel_height=251659264 + pid, behind=False, pr_id=pid, name=b.elem_id, h_rel=hrel,
                                   v_rel="paragraph", wrap="square" if beside else "none", layout_in_cell=False)
                ap.append(drawing_run(a, True))
                ctx.degrade(b.elem_id, "文字框位置", "不在主文字流的位置上（並排或壓在其他元素上）：輸出成錨在最近段落的浮動文字方塊", False)
                continue
            in_cols = b.col is not None
            want = col_info[0] if in_cols else None
            if want != writer.cols:
                writer.new_section(want, new_page=new_page_pending and writer.pending == "page")
                cur_col = None
            if in_cols and cur_col is not None and b.col > cur_col:
                writer.pending = writer.pending or "column"
            elif in_cols and cur_col is not None and b.col < cur_col:
                ctx.degrade(b.elem_id, "分欄順序", "閱讀順序回到前一欄，接在目前這欄之後", True)
            if in_cols:
                cur_col = max(cur_col or 0, b.col)
            new_page_pending = False
            avail = col_info[0][b.col][0] if in_cols else right_x - left
            key = b.col if in_cols else "span"
            start_y = prev_bottom.get(key, prev_bottom.get("span", top))
            gap = b.box.y * s - start_y
            extra = twip(gap) if params["preserve_block_gaps"] and gap > params["min_gap_pt"] else 0
            brk = writer.pop_break() if b.kind != "table" else None
            if b.kind == "para":
                latin, ea = context_fonts(b.box_paras, ctx)
                indent = b.box.x * s - left
                p = build_paragraph(b.item, ctx, b.elem_id, latin, ea, styles.get(b.item.style_id or ""), diff=True,
                                    extra_before_tw=extra if b.first_in_box else 0, page_break_before=brk == "page",
                                    numpr=b.numpr, strip_chars=b.strip, left_tw=twip(indent) if (not in_cols and indent > 1.0) else 0,
                                    hanging_tw=b.hanging_tw)
                expect.append("".join(r.text for r in b.item.runs)[b.strip:])
            elif b.kind == "image" and params["float_side_images"] and (side := _float_anchor(b, anchors, prev_bottom.get(key, top) / s)) is not None:
                # 不在文字流下方：浮動圖片，錨在最近的段落，水平相對邊界、垂直相對段落
                abox, ap, _ = side
                beside = _beside_all(b, blocks)
                w, h = b.box.w * s, b.box.h * s
                rid, _ = docx_doc.part.get_or_add_image(image_descriptor(store, b.item.image))
                pid = ctx.new_id()
                pic = picture_pic(rid, w, h, 0.0, pid, b.elem_id)
                hx, hrel = (b.box.x * s - left, "margin") if not in_cols else (b.box.x * s, "page")
                a = drawing_anchor(pic, URI_PIC, x=hx, y=(b.box.y - abox.y) * s, w=w, h=h, rot=0.0, rel_height=251659264 + pid,
                                   behind=False, pr_id=pid, name=b.elem_id, h_rel=hrel, v_rel="paragraph", wrap="square" if beside else "none",
                                   layout_in_cell=False)  # LibreOffice 把 layoutInCell 當成「不得超出文字區」，會把錨點段落推到下一頁
                ap.append(drawing_run(a, False))
                ctx.degrade(b.elem_id, "圖片位置", "不在文字流下方：輸出成錨在最近段落的浮動圖片", False)
                prev_bottom[key] = max(prev_bottom.get(key, 0.0), (b.box.y + b.box.h) * s)
                continue
            elif b.kind == "image":
                w = b.box.w * s
                h = b.box.h * s
                if w > avail:
                    ctx.degrade(b.elem_id, "圖片大小", f"超過版心，依原比例縮小為 {avail / w:.0%}", True)
                    w, h = avail, h * avail / w
                inline = docx_doc.part.new_pic_inline(image_descriptor(store, b.item.image), Emu(emu(w)), Emu(emu(h)))
                p = el("w:p")
                ppr = sub(p, "w:pPr")
                if brk == "page":
                    sub(ppr, "w:pageBreakBefore")
                if extra:
                    sub(ppr, "w:spacing", {"w:before": extra})
                off = b.box.x * s - (left if not in_cols else 0)
                if not in_cols and abs((b.box.x + b.box.w / 2) * s - (left + right_x) / 2) < params["center_tolerance"] * (right_x - left):
                    sub(ppr, "w:jc", {"w:val": "center"})
                elif not in_cols and off > 1.0 and off + w <= avail + 0.5:
                    sub(ppr, "w:ind", {"w:left": twip(off)})
                r = sub(p, "w:r")
                sub(r, "w:drawing").append(inline)
            else:
                indent = b.box.x * s - left
                p = build_table(b.item, ctx, styles, exact_rows=False, diff=True, width_limit_pt=avail,
                                indent_pt=indent if (not in_cols and 1.0 < indent and indent + sum(b.item.col_widths_pt) * s <= avail + 0.5) else 0.0)
                expect.extend(table_texts(b.item))
            if brk == "column" and p.tag == q("w:p"):
                r = el("w:r")
                sub(r, "w:br", {"w:type": "column"})
                p.find(q("w:pPr")).addnext(r)
            writer.add(p)
            if (b.kind == "para" and b.first_in_box) or b.kind == "image":
                anchors.append((b.box, p))
            prev_bottom[key] = max(prev_bottom.get(key, 0.0), (b.box.y + b.box.h) * s)
            if not in_cols:
                for k in list(prev_bottom):
                    prev_bottom[k] = max(prev_bottom[k], prev_bottom["span"])
        if not blocks and page.page_index > 0 and params["page_break"] == "source_page":
            writer.add(empty_paragraph(1.0, page_break_before=writer.pop_break() == "page"))
    if writer.count == 0:
        writer.add(empty_paragraph(1.0))
    writer.finish()
    finish_package(docx_doc, ctx)
    data = save_bytes(docx_doc)
    ok, notes = selfcheck(data, writer.sections, expect, ctx.fonts)
    blob = store.put_blob(data, MIME, "docx")
    return OutputFile(format=OutputFormat.DOCX_FLOW, file=blob, filename=f"{basename}.docx", degradations=ctx.degradations,
                      fonts_used=sorted(ctx.fonts), selfcheck_ok=ok, selfcheck_notes=notes)
