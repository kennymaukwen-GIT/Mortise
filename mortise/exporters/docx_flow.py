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
  | 文字框之間的垂直空隙         | 下一個區塊第一段的段前距離                                    | 近似     |
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
    emu,
    empty_paragraph,
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
    for s in page.shapes:
        ctx.degrade(s.id, f"形狀 {s.geometry}", "可重排模式不輸出形狀", True)
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
            fam = marker_runs[0].font.family.value
            latin, ea = context_fonts(members[0].box_paras, ctx)
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


# ---------- 主程式 ----------


def export(doc: StyledDoc, basename: str, cfg: Config, store: Store) -> OutputFile:
    params = params_from(cfg, DEFAULT_PARAMS, "docx_flow")
    ctx = make_ctx(doc)
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

    def clamp(v: float, dim: float) -> float:
        return min(max(v, params["min_margin_pt"]), params["max_margin_ratio"] * dim)

    left = clamp(min(xs0, default=72.0), W)
    right = clamp(W - max(xs1, default=W - 72.0), W)
    top = clamp(min(ys0, default=72.0), H)
    bottom = clamp(H - max(ys1, default=H - 72.0) - max(params["bottom_slack_pt"], params["bottom_slack_ratio"] * H), H)
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
    data = save_bytes(docx_doc)
    ok, notes = selfcheck(data, writer.sections, expect, ctx.fonts)
    blob = store.put_blob(data, MIME, "docx")
    return OutputFile(format=OutputFormat.DOCX_FLOW, file=blob, filename=f"{basename}.docx", degradations=ctx.degradations,
                      fonts_used=sorted(ctx.fonts), selfcheck_ok=ok, selfcheck_notes=notes)
