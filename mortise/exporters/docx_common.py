"""Word（DOCX）兩種模式共用的降階工具：單位、字型欄位、段落與字元屬性、樣式、表格、繪圖物件、自檢。

python-docx（MIT）只負責封裝（部件、關聯、圖片部件）；文字方塊、浮動定位、東亞字型欄位、
分欄、編號定義這些 python-docx 沒有介面的東西，一律在這裡用 lxml 自己寫 XML。

單位（見 research/formats.md 3.13）：
  文字層：twip（1/20 pt）—— 邊界、縮排、段距、欄寬、列高；半點（1/2 pt）—— 字級；
          八分之一點 —— 框線寬度。
  繪圖層：EMU（1 pt = 12700）—— 浮動物件的位置與大小；角度為六萬分之一度；
          透明度為十萬分之一。

降階對照表（兩種模式共用的部分；各模式特有的見 docx_fixed.py、docx_flow.py）：
  | 成品文件的概念                 | DOCX 的物件                                         | 是否有損 |
  |--------------------------------|-----------------------------------------------------|----------|
  | 字型（FontChoice.family）      | w:rFonts 的 ascii、hAnsi、eastAsia、cs 四欄都寫      | 無損     |
  | 東亞文字段的模稜字元           | w:rFonts 的 hint="eastAsia"                         | 無損     |
  | 字級                           | w:sz、w:szCs（半點）                                 | 量化到 0.5 pt（有差才記） |
  | 粗體、斜體                     | w:b、w:i（含 bCs、iCs），關閉時明寫 val="0"         | 無損     |
  | 文字顏色                       | w:color                                             | 透明度有損（以不透明輸出） |
  | 對齊                           | w:jc（left、center、right、both）                    | 無損     |
  | 行距（倍數、固定值）           | w:spacing 的 line 加 lineRule（auto、exact）         | 無損     |
  | 段前、段後、首行縮排           | w:spacing before／after、w:ind firstLine／hanging（twip） | 量化 0.05 pt |
  | 樣式表 Style                   | 具名段落樣式（標題角色基於內建「標題 N」並帶大綱階層） | 無損     |
  | 表格列欄、欄寬、列高           | w:tbl、w:tblGrid、w:trHeight（twip）、固定版面        | 無損     |
  | 合併儲存格                     | w:gridSpan、w:vMerge                                 | 無損     |
  | 儲存格底色、框線、邊距、垂直對齊 | w:shd、w:tcBorders（八分之一點）、w:tcMar、w:vAlign | 底色透明度有損；框線寬限 0.25 到 12 pt |
  | 表頭列                         | w:tblHeader（跨頁重複）                              | 無損     |
  | 數值、數字格式、公式（給 Excel） | 不輸出，只輸出儲存格文字                            | 有損     |
  | 文字中的控制字元               | 清除（XML 不允許）                                   | 有損     |
  | Word 的排版開關                | 文件預設段落：孤行控制關、中英自動間距關、對齊格線關；相容模式 15 | 不適用 |
"""

from __future__ import annotations

import copy
import io
import posixpath
import re
import zipfile
from collections import Counter
from dataclasses import dataclass, field, replace
from typing import Iterable, Optional

from docx.oxml.parser import oxml_parser
from lxml import etree

from ..artifacts import Align, Color, Degradation, LineSpacingMode, StyledDoc, StyledParagraph, StyledRun, StyledTable
from ..artifacts.common import Role

# ---------- 命名空間 ----------

NS = {
    "w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main",
    "wp": "http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing",
    "a": "http://schemas.openxmlformats.org/drawingml/2006/main",
    "pic": "http://schemas.openxmlformats.org/drawingml/2006/picture",
    "r": "http://schemas.openxmlformats.org/officeDocument/2006/relationships",
    "wps": "http://schemas.microsoft.com/office/word/2010/wordprocessingShape",
    "mc": "http://schemas.openxmlformats.org/markup-compatibility/2006",
    "v": "urn:schemas-microsoft-com:vml",
    "o": "urn:schemas-microsoft-com:office:office",
    "w10": "urn:schemas-microsoft-com:office:word",
}
XML_NS = "http://www.w3.org/XML/1998/namespace"
URI_WPS = "http://schemas.microsoft.com/office/word/2010/wordprocessingShape"
URI_PIC = "http://schemas.openxmlformats.org/drawingml/2006/picture"


def q(tag: str) -> str:
    """「w:p」→ Clark 名稱「{命名空間}p」。"""
    prefix, local = tag.split(":")
    if prefix == "xml":
        return f"{{{XML_NS}}}{local}"
    return f"{{{NS[prefix]}}}{local}"


def el(tag: str, attrs: Optional[dict] = None) -> etree._Element:
    """建立一棵子樹的根：用 python-docx 的剖析器（才會是它的自訂元素類別），並宣告標準前綴。"""
    e = oxml_parser.makeelement(q(tag), nsmap=NS)
    for k, v in (attrs or {}).items():
        e.set(q(k) if ":" in k else k, str(v))
    return e


def sub(parent: etree._Element, tag: str, attrs: Optional[dict] = None, text: Optional[str] = None) -> etree._Element:
    e = etree.SubElement(parent, q(tag))
    for k, v in (attrs or {}).items():
        e.set(q(k) if ":" in k else k, str(v))
    if text is not None:
        e.text = text
    return e


# ---------- 單位 ----------

EMU_PER_PT = 12700
MAX_PAGE_PT = 1584.0  # Word 的頁面上限：22 英吋


def twip(pt: float) -> int:
    return int(round(pt * 20))


def emu(pt: float) -> int:
    return int(round(pt * EMU_PER_PT))


def half_pt(pt: float) -> int:
    return max(1, int(round(pt * 2)))


def eighth_pt(pt: float) -> int:
    return int(round(pt * 8))


# ---------- 文字清理 ----------

_ILLEGAL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f￾￿]")
# 拉丁與東亞共用的模稜字元（引號、破折號、刪節號、間隔號等）：東亞文字段要加 hint，Word 才會用東亞字型顯示
_AMBIGUOUS = re.compile(r"[§¨°±´·×÷‐-‧‰-⁞←-↙①-⓿■-⛿]")


def clean_text(s: str) -> tuple[str, int]:
    """去掉 XML 不允許的控制字元，回傳（清理後文字, 被清掉的字元數）。"""
    cleaned = _ILLEGAL.sub("", s)
    return cleaned, len(s) - len(cleaned)


def norm_text(s: str) -> str:
    """文字往返比對用：去掉所有空白。"""
    return re.sub(r"\s+", "", s)


# ---------- 降階脈絡 ----------


@dataclass
class Ctx:
    """一次輸出的共用狀態：預設字型、降級紀錄、寫入的字型、比例、繪圖物件編號。"""

    doc_latin: str
    doc_ea: str
    degradations: list[Degradation] = field(default_factory=list)
    fonts: set[str] = field(default_factory=set)
    scale: float = 1.0
    _seen: set = field(default_factory=set)
    _next_id: int = 1

    def degrade(self, element_id: str, concept: str, action: str, lossy: bool) -> None:
        key = (element_id, concept)
        if key in self._seen:
            return
        self._seen.add(key)
        self.degradations.append(Degradation(element_id=element_id, concept=concept, action=action, lossy=lossy))

    def new_id(self) -> int:
        i = self._next_id
        self._next_id += 1
        return i


def _all_paragraphs(doc: StyledDoc) -> Iterable[StyledParagraph]:
    for page in doc.pages:
        for tb in page.text_boxes:
            yield from tb.paragraphs
        for t in page.tables:
            for c in t.cells:
                yield from c.paragraphs


def make_ctx(doc: StyledDoc) -> Ctx:
    """全文件的預設字型：拉丁與東亞各取字數最多的推論字型。"""
    latin: Counter = Counter()
    ea: Counter = Counter()
    for sp in _all_paragraphs(doc):
        for r in sp.runs:
            (ea if r.font.east_asian else latin)[r.font.family.value] += max(1, len(r.text))
    doc_latin = latin.most_common(1)[0][0] if latin else (ea.most_common(1)[0][0] if ea else "Liberation Sans")
    doc_ea = ea.most_common(1)[0][0] if ea else doc_latin
    return Ctx(doc_latin=doc_latin, doc_ea=doc_ea)


def context_fonts(paragraphs: list[StyledParagraph], ctx: Ctx) -> tuple[str, str]:
    """一群段落（同一文字框或儲存格）的拉丁與東亞字型：給「只有拉丁字」的段落填東亞欄位用。"""
    latin = next((r.font.family.value for sp in paragraphs for r in sp.runs if not r.font.east_asian), ctx.doc_latin)
    ea = next((r.font.family.value for sp in paragraphs for r in sp.runs if r.font.east_asian), ctx.doc_ea)
    return latin, ea


# ---------- 段落與字元格式 ----------

_JC = {Align.LEFT: "left", Align.CENTER: "center", Align.RIGHT: "right", Align.JUSTIFY: "both"}
SINGLE = ("auto", 240)


@dataclass(frozen=True)
class RunFmt:
    ascii: str
    east_asia: str
    size_half: int
    bold: bool
    italic: bool
    color: str  # 六位十六進位


@dataclass(frozen=True)
class ParaFmt:
    align: str = "left"
    line: tuple[str, int] = SINGLE  # （規則, 值）：auto 以 1/240 行為單位、exact 以 twip 為單位
    before: int = 0  # twip
    after: int = 0
    first_line: int = 0  # 正值首行縮排、負值首行凸排


@dataclass
class WordStyle:
    word_id: str
    name: str
    run: RunFmt
    para: ParaFmt
    outline: Optional[int] = None  # 大綱階層 0 起算


def run_format(sr: StyledRun, latin: str, ea: str, scale: float = 1.0) -> RunFmt:
    """字型欄位：東亞文字段四欄都寫它自己的字型（原件裡這段的英數字也是用它畫的）；
    拉丁文字段的東亞欄位寫鄰近的東亞字型，使用者在這裡打中文時才不會落回預設字型。"""
    fam = sr.font.family.value
    return RunFmt(
        ascii=fam,
        east_asia=fam if sr.font.east_asian else (ea or fam),
        size_half=half_pt(sr.size_pt.value * scale),
        bold=sr.bold,
        italic=sr.italic,
        color=sr.color.hex,
    )


def para_format(sp: StyledParagraph, scale: float = 1.0) -> ParaFmt:
    line = SINGLE
    if sp.line_spacing is not None:
        ls = sp.line_spacing.value
        line = ("exact", twip(ls.value * scale)) if ls.mode == LineSpacingMode.EXACT else ("auto", int(round(ls.value * 240)))
    return ParaFmt(
        align=_JC[sp.align],
        line=line,
        before=twip(sp.space_before_pt * scale),
        after=twip(sp.space_after_pt * scale),
        first_line=twip(sp.first_line_indent_pt * scale),
    )


def rfonts(parent: etree._Element, ascii_f: str, ea_f: str, hint_ea: bool = False) -> etree._Element:
    attrs = {"w:ascii": ascii_f, "w:hAnsi": ascii_f, "w:eastAsia": ea_f, "w:cs": ascii_f}
    if hint_ea:
        attrs["w:hint"] = "eastAsia"
    return sub(parent, "w:rFonts", attrs)


def rpr_element(fmt: RunFmt, base: Optional[RunFmt], tag: str = "w:rPr", hint_ea: bool = False) -> Optional[etree._Element]:
    """字元屬性；base 不為空時只寫與 base 不同的欄位（樣式差異）。子元素照綱要順序。"""
    rpr = el(tag)
    if base is None or hint_ea or (fmt.ascii, fmt.east_asia) != (base.ascii, base.east_asia):
        rfonts(rpr, fmt.ascii, fmt.east_asia, hint_ea)
    if base is None or fmt.bold != base.bold:
        sub(rpr, "w:b", None if fmt.bold else {"w:val": "0"})
        sub(rpr, "w:bCs", None if fmt.bold else {"w:val": "0"})
    if base is None or fmt.italic != base.italic:
        sub(rpr, "w:i", None if fmt.italic else {"w:val": "0"})
        sub(rpr, "w:iCs", None if fmt.italic else {"w:val": "0"})
    if base is None or fmt.color != base.color:
        sub(rpr, "w:color", {"w:val": fmt.color})
    if base is None or fmt.size_half != base.size_half:
        sub(rpr, "w:sz", {"w:val": fmt.size_half})
        sub(rpr, "w:szCs", {"w:val": fmt.size_half})
    return rpr if len(rpr) else None


def ppr_element(
    pf: ParaFmt,
    base: Optional[ParaFmt],
    style_id: Optional[str] = None,
    outline: Optional[int] = None,
    keep_next: bool = False,
    page_break_before: bool = False,
    numpr: Optional[tuple[int, int]] = None,
    mark_rpr: Optional[etree._Element] = None,
    left_tw: int = 0,
    hanging_tw: int = 0,
) -> etree._Element:
    """段落屬性；base 不為空時只寫差異。子元素照綱要順序（pStyle … jc、outlineLvl、rPr）。"""
    ppr = el("w:pPr")
    if style_id:
        sub(ppr, "w:pStyle", {"w:val": style_id})
    if keep_next:
        sub(ppr, "w:keepNext")
    if page_break_before:
        sub(ppr, "w:pageBreakBefore")
    if numpr is not None:
        n = sub(ppr, "w:numPr")
        sub(n, "w:ilvl", {"w:val": numpr[1]})
        sub(n, "w:numId", {"w:val": numpr[0]})
    sp_attrs: dict = {}
    if base is None or pf.before != base.before:
        sp_attrs["w:before"] = pf.before
    if base is None or pf.after != base.after:
        sp_attrs["w:after"] = pf.after
    if base is None or pf.line != base.line:
        sp_attrs["w:line"] = pf.line[1]
        sp_attrs["w:lineRule"] = pf.line[0]
    if sp_attrs:
        sub(ppr, "w:spacing", sp_attrs)
    ind: dict = {}
    if numpr is not None:  # 清單：縮排寫在編號定義裡；文字框本身有左縮排時才覆寫
        if left_tw:
            ind = {"w:left": left_tw + hanging_tw, "w:hanging": hanging_tw}
    elif left_tw or base is None or pf.first_line != base.first_line:
        if pf.first_line >= 0:
            ind = {"w:left": left_tw, "w:firstLine": pf.first_line}
        else:
            ind = {"w:left": left_tw - pf.first_line, "w:hanging": -pf.first_line}
    if ind:
        sub(ppr, "w:ind", ind)
    if base is None or pf.align != base.align:
        sub(ppr, "w:jc", {"w:val": pf.align})
    if outline is not None:
        sub(ppr, "w:outlineLvl", {"w:val": outline})
    if mark_rpr is not None:
        ppr.append(mark_rpr)
    return ppr


def text_runs(r: etree._Element, text: str) -> None:
    """把文字寫進 w:r：換行 → w:br、定位字元 → w:tab，前後空白要保留。"""
    for k, piece in enumerate(re.split(r"(\n|\t)", text)):
        if piece == "\n":
            sub(r, "w:br")
        elif piece == "\t":
            sub(r, "w:tab")
        elif piece:
            t = sub(r, "w:t", text=piece)
            if piece != piece.strip() or "  " in piece:
                t.set(q("xml:space"), "preserve")


def build_paragraph(
    sp: StyledParagraph,
    ctx: Ctx,
    element_id: str,
    latin: str,
    ea: str,
    style: Optional[WordStyle] = None,
    diff: bool = False,
    extra_before_tw: int = 0,
    page_break_before: bool = False,
    numpr: Optional[tuple[int, int]] = None,
    strip_chars: int = 0,
    keep_next: bool = False,
    align_override: Optional[str] = None,
    left_tw: int = 0,
    hanging_tw: int = 0,
) -> etree._Element:
    """一個成品段落 → 一個 w:p。diff 為真時只寫與樣式不同的屬性（可重排模式）；
    否則全部明寫（版面固定模式：不依賴樣式繼承，任何軟體開都一樣）。"""
    pf = para_format(sp, ctx.scale)
    if extra_before_tw:
        pf = replace(pf, before=pf.before + extra_before_tw)
    if align_override:
        pf = replace(pf, align=align_override)
    base_pf = style.para if (diff and style) else None
    base_rf = style.run if (diff and style) else None
    p = el("w:p")
    runs_out: list[tuple[RunFmt, str, bool]] = []
    remaining = strip_chars
    for sr in sp.runs:
        text, removed = clean_text(sr.text)
        if removed:
            ctx.degrade(element_id, "文字中的控制字元", f"清除 {removed} 個 XML 不允許的字元", True)
        if remaining:
            cut = min(remaining, len(text))
            text, remaining = text[cut:], remaining - cut
            if not text:
                continue
        fmt = run_format(sr, latin, ea, ctx.scale)
        exact_half = sr.size_pt.value * ctx.scale * 2
        if abs(exact_half - fmt.size_half) > 0.02:
            ctx.degrade(element_id, "字級", f"Word 字級以半點為單位，{sr.size_pt.value:g} pt 量化為 {fmt.size_half / 2:g} pt", True)
        if sr.color.a < 0.999:
            ctx.degrade(element_id, "文字透明度", "Word 的文字顏色沒有透明度，以不透明顏色輸出", True)
        ctx.fonts.update({fmt.ascii, fmt.east_asia})
        hint = sr.font.east_asian and bool(_AMBIGUOUS.search(text))
        runs_out.append((fmt, text, hint))
    mark = None
    if runs_out:
        mark = rpr_element(runs_out[-1][0], base_rf)
    else:
        mark_fmt = RunFmt(latin, ea, half_pt(sp.runs[0].size_pt.value * ctx.scale) if sp.runs else 22, False, False, "000000")
        mark = rpr_element(mark_fmt, base_rf)
    p.append(ppr_element(pf, base_pf, style.word_id if style else None, style.outline if style else None, keep_next, page_break_before, numpr, mark, left_tw, hanging_tw))
    for fmt, text, hint in runs_out:
        r = sub(p, "w:r")
        rpr = rpr_element(fmt, base_rf, hint_ea=hint)
        if rpr is not None:
            r.append(rpr)
        text_runs(r, text)
    return p


def empty_paragraph(size_pt: float = 1.0, page_break_before: bool = False) -> etree._Element:
    """幾乎不佔高度的空段落：錨定段落、表格後面必要的段落、分頁或分欄的載體。"""
    p = el("w:p")
    ppr = sub(p, "w:pPr")
    if page_break_before:
        sub(ppr, "w:pageBreakBefore")
    sub(ppr, "w:spacing", {"w:before": 0, "w:after": 0, "w:line": max(1, twip(size_pt)), "w:lineRule": "exact"})
    rpr = sub(ppr, "w:rPr")
    sub(rpr, "w:sz", {"w:val": half_pt(size_pt)})
    sub(rpr, "w:szCs", {"w:val": half_pt(size_pt)})
    return p


# ---------- 文件預設與樣式 ----------


def setup_package(docx_doc, ctx: Ctx, default_size_pt: float) -> None:
    """文件預設值：字型四欄明寫（拿掉主題字型）、Word 的排版開關全部明寫、相容模式 15。"""
    styles = docx_doc.styles.element
    dd = styles.find(q("w:docDefaults"))
    rpr = dd.find(f"{q('w:rPrDefault')}/{q('w:rPr')}")
    for ch in list(rpr):
        rpr.remove(ch)
    rfonts(rpr, ctx.doc_latin, ctx.doc_ea)
    sub(rpr, "w:sz", {"w:val": half_pt(default_size_pt)})
    sub(rpr, "w:szCs", {"w:val": half_pt(default_size_pt)})
    sub(rpr, "w:lang", {"w:val": "en-US", "w:eastAsia": "zh-TW", "w:bidi": "ar-SA"})
    ppr = dd.find(f"{q('w:pPrDefault')}/{q('w:pPr')}")
    for ch in list(ppr):
        ppr.remove(ch)
    sub(ppr, "w:widowControl", {"w:val": "0"})  # 孤行控制關：分頁貼近來源
    sub(ppr, "w:autoSpaceDE", {"w:val": "0"})  # 中英文之間不自動加空隙（原件的空隙已是真空白）
    sub(ppr, "w:autoSpaceDN", {"w:val": "0"})
    sub(ppr, "w:snapToGrid", {"w:val": "0"})  # 不對齊文件格線
    sub(ppr, "w:spacing", {"w:after": 0, "w:line": 240, "w:lineRule": "auto"})
    ctx.fonts.update({ctx.doc_latin, ctx.doc_ea})
    settings = docx_doc.settings.element
    for cs in settings.iter(q("w:compatSetting")):
        if cs.get(q("w:name")) == "compatibilityMode":
            cs.set(q("w:val"), "15")


def _style_el(styles_root, style_id: str) -> Optional[etree._Element]:
    for s in styles_root.findall(q("w:style")):
        if s.get(q("w:styleId")) == style_id:
            return s
    return None


def _write_style_props(st: etree._Element, ws: WordStyle) -> None:
    for tag in ("w:pPr", "w:rPr"):
        old = st.find(q(tag))
        if old is not None:
            st.remove(old)
    ppr = ppr_element(ws.para, None, None, ws.outline, keep_next=ws.outline is not None)
    rpr = rpr_element(ws.run, None)
    st.append(ppr)
    st.append(rpr)


def build_styles(docx_doc, doc: StyledDoc, ctx: Ctx, builtin_headings: bool = True) -> dict[str, WordStyle]:
    """樣式表 → Word 段落樣式。

    - 段落屬性取「套用這個樣式的段落中最常見的值」，字元屬性取 Style 本身；
      東亞與拉丁字型由套用段落的文字段統計（Style 只有一個字型名稱）。
    - 標題角色（title、subtitle）依字級由大到小給大綱階層；每一階最常用的樣式直接改寫內建
      「heading N」（Word 中文介面顯示為「標題 N」），其餘建成基於它的自訂樣式。
    """
    root = docx_doc.styles.element
    usage: dict[str, list[StyledParagraph]] = {s.id: [] for s in doc.styles}
    for sp in _all_paragraphs(doc):
        if sp.style_id in usage:
            usage[sp.style_id].append(sp)
    heading_styles = [s for s in doc.styles if s.role in (Role.TITLE, Role.SUBTITLE)]
    title_sizes = sorted({round(s.size_pt, 1) for s in heading_styles if s.role == Role.TITLE}, reverse=True)
    sub_sizes = sorted({round(s.size_pt, 1) for s in heading_styles if s.role == Role.SUBTITLE}, reverse=True)
    out: dict[str, WordStyle] = {}
    used_names = {s.find(q("w:name")).get(q("w:val")) for s in root.findall(q("w:style")) if s.find(q("w:name")) is not None}
    primary_of_level: dict[int, str] = {}
    for s in sorted(heading_styles, key=lambda s: -s.usage_count):
        lvl = title_sizes.index(round(s.size_pt, 1)) if s.role == Role.TITLE else len(title_sizes) + sub_sizes.index(round(s.size_pt, 1))
        primary_of_level.setdefault(min(lvl, 8), s.id)
    for s in doc.styles:
        paras = usage[s.id]
        latin = Counter(r.font.family.value for sp in paras for r in sp.runs if not r.font.east_asian)
        ea = Counter(r.font.family.value for sp in paras for r in sp.runs if r.font.east_asian)
        latin_f = latin.most_common(1)[0][0] if latin else s.font_family
        ea_f = ea.most_common(1)[0][0] if ea else (s.font_family if not latin else ctx.doc_ea)
        pf_counts = Counter(para_format(sp, ctx.scale) for sp in paras)
        pf = pf_counts.most_common(1)[0][0] if pf_counts else ParaFmt()
        run = RunFmt(latin_f, ea_f, half_pt(s.size_pt * ctx.scale), s.bold, s.italic, s.color.hex)
        ctx.fonts.update({latin_f, ea_f})
        outline = None
        if s.role in (Role.TITLE, Role.SUBTITLE):
            lvl = title_sizes.index(round(s.size_pt, 1)) if s.role == Role.TITLE else len(title_sizes) + sub_sizes.index(round(s.size_pt, 1))
            outline = min(lvl, 8)
        if outline is not None and builtin_headings and primary_of_level.get(outline) == s.id and _style_el(root, f"Heading{outline + 1}") is not None:
            ws = WordStyle(f"Heading{outline + 1}", f"heading {outline + 1}", run, pf, outline)
            st = _style_el(root, ws.word_id)
        else:
            name = s.name
            while name in used_names:
                name += " "
            used_names.add(name)
            ws = WordStyle(f"Mortise{s.id}", name, run, pf, outline)
            st = sub(root, "w:style", {"w:type": "paragraph", "w:customStyle": "1", "w:styleId": ws.word_id})
            sub(st, "w:name", {"w:val": name})
            based = f"Heading{outline + 1}" if outline is not None and _style_el(root, f"Heading{outline + 1}") is not None else "Normal"
            sub(st, "w:basedOn", {"w:val": based})
            sub(st, "w:next", {"w:val": "Normal"})
            sub(st, "w:qFormat")
        _write_style_props(st, ws)
        out[s.id] = ws
    return out


# ---------- 表格 ----------


def build_table(
    tbl: StyledTable,
    ctx: Ctx,
    styles: dict[str, WordStyle],
    *,
    exact_rows: bool,
    diff: bool,
    width_limit_pt: Optional[float] = None,
    indent_pt: float = 0.0,
    float_pos: Optional[tuple[float, float]] = None,
) -> etree._Element:
    """成品表格 → w:tbl。固定欄寬（不讓 Word 自動調整）；exact_rows 時列高固定，否則為「至少」。
    float_pos 給了就寫成相對頁面定位的浮動表格（tblpPr）。"""
    s = ctx.scale
    widths = [w * s for w in tbl.col_widths_pt]
    heights = [h * s for h in tbl.row_heights_pt]
    if width_limit_pt and sum(widths) > width_limit_pt + 0.5:
        f = width_limit_pt / sum(widths)
        widths = [w * f for w in widths]
        ctx.degrade(tbl.id, "表格寬度", f"超過版心，欄寬等比縮小為 {f:.0%}", True)
    nrows, ncols = len(heights), len(widths)
    t = el("w:tbl")
    tpr = sub(t, "w:tblPr")
    if float_pos is not None:
        sub(tpr, "w:tblpPr", {"w:leftFromText": 0, "w:rightFromText": 0, "w:topFromText": 0, "w:bottomFromText": 0,
                              "w:vertAnchor": "page", "w:horzAnchor": "page", "w:tblpX": twip(float_pos[0]), "w:tblpY": twip(float_pos[1])})
        sub(tpr, "w:tblOverlap", {"w:val": "overlap"})
    sub(tpr, "w:tblW", {"w:w": sum(twip(w) for w in widths), "w:type": "dxa"})
    if indent_pt:
        sub(tpr, "w:tblInd", {"w:w": twip(indent_pt), "w:type": "dxa"})
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

    starts: dict[tuple[int, int], object] = {}
    owner: dict[tuple[int, int], object] = {}
    for c in tbl.cells:
        if not (0 <= c.row < nrows and 0 <= c.col < ncols):
            ctx.degrade(tbl.id, "儲存格位置", f"儲存格 ({c.row},{c.col}) 超出表格範圍，未輸出", True)
            continue
        span_cells = [(r, k) for r in range(c.row, min(nrows, c.row + c.row_span)) for k in range(c.col, min(ncols, c.col + c.col_span))]
        if any(rc in owner for rc in span_cells):
            ctx.degrade(tbl.id, "合併儲存格", f"儲存格 ({c.row},{c.col}) 與其他合併範圍重疊，未輸出", True)
            continue
        starts[(c.row, c.col)] = c
        for rc in span_cells:
            owner[rc] = c
        if c.value is not None or c.number_format or c.formula:
            ctx.degrade(tbl.id, "儲存格數值、數字格式、公式", "Word 表格只輸出儲存格文字", True)

    def cell_props(tc, c, span: int, vmerge: Optional[str]) -> None:
        pr = sub(tc, "w:tcPr")
        sub(pr, "w:tcW", {"w:w": sum(twip(w) for w in widths[c.col:c.col + span]), "w:type": "dxa"})
        if span > 1:
            sub(pr, "w:gridSpan", {"w:val": span})
        if vmerge:
            sub(pr, "w:vMerge", {"w:val": vmerge} if vmerge == "restart" else None)
        borders = sub(pr, "w:tcBorders")
        for side, b in zip(("top", "left", "bottom", "right"), (c.borders[0], c.borders[3], c.borders[2], c.borders[1])):
            if b is None:
                sub(borders, f"w:{side}", {"w:val": "nil"})
            else:
                sz = eighth_pt(b.width_pt * s)
                if not 2 <= sz <= 96:
                    ctx.degrade(tbl.id, "框線寬度", "Word 框線寬限 0.25 到 12 pt，已夾到範圍內", True)
                    sz = min(96, max(2, sz))
                sub(borders, f"w:{side}", {"w:val": "single", "w:sz": sz, "w:space": 0, "w:color": b.color.hex})
        if c.fill is not None:
            sub(pr, "w:shd", {"w:val": "clear", "w:color": "auto", "w:fill": c.fill.hex})
            if c.fill.a < 0.999:
                ctx.degrade(tbl.id, "儲存格底色透明度", "Word 儲存格底色沒有透明度，以不透明顏色輸出", True)
        m = sub(pr, "w:tcMar")
        for side, v in zip(("top", "left", "bottom", "right"), (c.margins_pt[0], c.margins_pt[3], c.margins_pt[2], c.margins_pt[1])):
            sub(m, f"w:{side}", {"w:w": twip(v * s), "w:type": "dxa"})
        sub(pr, "w:vAlign", {"w:val": {"top": "top", "middle": "center", "bottom": "bottom"}.get(c.v_align, "top")})

    for r in range(nrows):
        tr = sub(t, "w:tr")
        trpr = sub(tr, "w:trPr")
        if exact_rows:
            sub(trpr, "w:cantSplit")
        sub(trpr, "w:trHeight", {"w:val": twip(heights[r]), "w:hRule": "exact" if exact_rows else "atLeast"})
        if r < tbl.header_rows:
            sub(trpr, "w:tblHeader")
        k = 0
        while k < ncols:
            c = starts.get((r, k))
            o = owner.get((r, k))
            tc = sub(tr, "w:tc")
            if c is not None:
                span = min(c.col_span, ncols - k)
                cell_props(tc, c, span, "restart" if c.row_span > 1 else None)
                latin, ea = context_fonts(c.paragraphs, ctx)
                for sp in c.paragraphs:
                    tc.append(build_paragraph(sp, ctx, tbl.id, latin, ea, styles.get(sp.style_id or ""), diff))
                if not c.paragraphs:
                    tc.append(el("w:p"))
                k += span
            elif o is not None and o.col == k:  # 垂直合併的延續格
                span = min(o.col_span, ncols - k)
                cell_props(tc, o, span, "continue")
                tc.append(el("w:p"))
                k += span
            else:  # 沒有資料的格，或水平合併中被不完整覆蓋的格
                pr = sub(tc, "w:tcPr")
                sub(pr, "w:tcW", {"w:w": twip(widths[k]), "w:type": "dxa"})
                tc.append(el("w:p"))
                k += 1
    return t


# ---------- 繪圖物件（文字方塊、形狀、圖片） ----------


def _norm_rot(deg: float) -> int:
    return int(round((deg % 360.0) * 60000)) % 21600000


def xfrm(parent: etree._Element, tag: str, w: float, h: float, rot: float = 0.0, flip_v: bool = False) -> None:
    attrs = {}
    if rot:
        attrs["rot"] = _norm_rot(rot)
    if flip_v:
        attrs["flipV"] = 1
    x = sub(parent, tag, attrs)
    sub(x, "a:off", {"x": 0, "y": 0})
    sub(x, "a:ext", {"cx": emu(w), "cy": emu(h)})


def solid_fill(parent: etree._Element, color: Color) -> None:
    sf = sub(parent, "a:solidFill")
    c = sub(sf, "a:srgbClr", {"val": color.hex})
    if color.a < 0.999:
        sub(c, "a:alpha", {"val": int(round(max(0.0, color.a) * 100000))})


def line_props(parent: etree._Element, color: Optional[Color], width_pt: Optional[float]) -> None:
    if color is None:
        ln = sub(parent, "a:ln")
        sub(ln, "a:noFill")
    else:
        ln = sub(parent, "a:ln", {"w": emu(width_pt or 1.0)})
        solid_fill(ln, color)


def cust_geom(parent: etree._Element, path: list[list[float]], w: float, h: float, scale: float) -> bool:
    """自訂路徑 → a:custGeom。座標相對於方塊左上角（pt）。回傳是否有可用的路徑。"""
    g = sub(parent, "a:custGeom")
    for tag in ("a:avLst", "a:gdLst", "a:ahLst", "a:cxnLst"):
        sub(g, tag)
    sub(g, "a:rect", {"l": 0, "t": 0, "r": "r", "b": "b"})
    pl = sub(g, "a:pathLst")
    pth = sub(pl, "a:path", {"w": max(1, emu(w)), "h": max(1, emu(h))})
    n = 0

    def pt(parent_el, x, y):
        sub(parent_el, "a:pt", {"x": emu(x * scale), "y": emu(y * scale)})

    for seg in path:
        kind = int(seg[0]) if seg else -1
        if kind == 0 and len(seg) >= 3:
            pt(sub(pth, "a:moveTo"), seg[1], seg[2])
        elif kind == 1 and len(seg) >= 3:
            pt(sub(pth, "a:lnTo"), seg[1], seg[2])
        elif kind == 2 and len(seg) >= 7:
            cb = sub(pth, "a:cubicBezTo")
            for i in range(3):
                pt(cb, seg[1 + 2 * i], seg[2 + 2 * i])
        elif kind == 3:
            sub(pth, "a:close")
        else:
            continue
        n += 1
    return n > 0


def body_pr(parent: etree._Element, wrap: bool = True, autofit: bool = False) -> None:
    """autofit 為真時寫 a:spAutoFit（文字放不下時框往下長，不會被裁掉）；否則 a:noAutofit。"""
    bp = sub(parent, "wps:bodyPr", {"rot": 0, "vert": "horz", "wrap": "square" if wrap else "none",
                                    "lIns": 0, "tIns": 0, "rIns": 0, "bIns": 0, "anchor": "t", "anchorCtr": 0})
    sub(bp, "a:spAutoFit" if autofit else "a:noAutofit")


def textbox_wsp(blocks: list[etree._Element], w: float, h: float, rot: float, wrap: bool, autofit: bool = False) -> etree._Element:
    """文字方塊（wps:wsp，txBox）：內距 0、無填色、無框線、不縮小文字。"""
    wsp = el("wps:wsp")
    sub(wsp, "wps:cNvSpPr", {"txBox": 1})
    sp = sub(wsp, "wps:spPr")
    xfrm(sp, "a:xfrm", w, h, rot)
    g = sub(sp, "a:prstGeom", {"prst": "rect"})
    sub(g, "a:avLst")
    sub(sp, "a:noFill")
    line_props(sp, None, None)
    txbx = sub(wsp, "wps:txbx")
    content = sub(txbx, "w:txbxContent")
    for b in blocks:
        content.append(b)
    if not len(content) or content[-1].tag != q("w:p"):
        content.append(empty_paragraph())
    body_pr(wsp, wrap, autofit)
    return wsp


def drawing_anchor(graphic_child: etree._Element, uri: str, *, x: float, y: float, w: float, h: float, rot: float,
                   rel_height: int, behind: bool, pr_id: int, name: str, h_rel: str = "page", v_rel: str = "page",
                   wrap: str = "none", layout_in_cell: bool = True) -> etree._Element:
    """浮動物件：wp:anchor。預設位置相對頁面（EMU）、不文繞圖、允許重疊、錨點鎖定（版面固定模式）；
    可重排模式的旁置圖片改用相對邊界與段落、矩形文繞圖。"""
    a = el("wp:anchor", {"distT": 0, "distB": 0, "distL": 0, "distR": 0, "simplePos": 0, "relativeHeight": rel_height,
                         "behindDoc": 1 if behind else 0, "locked": 1 if v_rel == "page" else 0, "layoutInCell": 1 if layout_in_cell else 0,
                         "allowOverlap": 1 if wrap == "none" else 0})
    sub(a, "wp:simplePos", {"x": 0, "y": 0})
    ph = sub(a, "wp:positionH", {"relativeFrom": h_rel})
    sub(ph, "wp:posOffset", text=str(emu(x)))
    pv = sub(a, "wp:positionV", {"relativeFrom": v_rel})
    sub(pv, "wp:posOffset", text=str(emu(y)))
    sub(a, "wp:extent", {"cx": emu(w), "cy": emu(h)})
    # 旋轉後外接框超出原框的部分（只影響文繞圖，寫實際值讓 Word 不必重算）
    import math

    r = math.radians(rot or 0.0)
    bw = abs(w * math.cos(r)) + abs(h * math.sin(r))
    bh = abs(w * math.sin(r)) + abs(h * math.cos(r))
    ex, ey = max(0, emu((bw - w) / 2)), max(0, emu((bh - h) / 2))
    sub(a, "wp:effectExtent", {"l": ex, "t": ey, "r": ex, "b": ey})
    if wrap == "square":
        sub(a, "wp:wrapSquare", {"wrapText": "bothSides"})
    else:
        sub(a, "wp:wrapNone")
    sub(a, "wp:docPr", {"id": pr_id, "name": name})
    sub(a, "wp:cNvGraphicFramePr")
    g = sub(a, "a:graphic")
    gd = sub(g, "a:graphicData", {"uri": uri})
    gd.append(graphic_child)
    return a


def drawing_run(anchor: etree._Element, needs_wps: bool, fallback: Optional[etree._Element] = None) -> etree._Element:
    """把繪圖包進 w:r。wps 物件包在 mc:AlternateContent 裡（Word 2010 以上走 Choice），可附 VML 退路。"""
    r = el("w:r")
    if not needs_wps:
        d = sub(r, "w:drawing")
        d.append(anchor)
        return r
    ac = sub(r, "mc:AlternateContent")
    ch = sub(ac, "mc:Choice", {"Requires": "wps"})
    d = sub(ch, "w:drawing")
    d.append(anchor)
    if fallback is not None:
        fb = sub(ac, "mc:Fallback")
        fb.append(fallback)
    return r


def vml_textbox_fallback(blocks: list[etree._Element], *, x: float, y: float, w: float, h: float, rot: float, z_index: int) -> etree._Element:
    """舊版 Word（2007）看得懂的 VML 文字方塊退路。"""
    pict = el("w:pict")
    style = (f"position:absolute;margin-left:{x:.2f}pt;margin-top:{y:.2f}pt;width:{w:.2f}pt;height:{h:.2f}pt;"
             f"z-index:{z_index};mso-position-horizontal-relative:page;mso-position-vertical-relative:page")
    if rot:
        style += f";rotation:{rot % 360:.2f}"
    shape = sub(pict, "v:rect", {"style": style, "filled": "f", "stroked": "f"})
    tb = sub(shape, "v:textbox", {"inset": "0,0,0,0"})
    content = sub(tb, "w:txbxContent")
    for b in blocks:
        content.append(copy.deepcopy(b))
    if not len(content) or content[-1].tag != q("w:p"):
        content.append(empty_paragraph())
    return pict


def picture_pic(rid: str, w: float, h: float, rot: float, pr_id: int, name: str) -> etree._Element:
    pic = el("pic:pic")
    nv = sub(pic, "pic:nvPicPr")
    sub(nv, "pic:cNvPr", {"id": pr_id, "name": name})
    cn = sub(nv, "pic:cNvPicPr")
    sub(cn, "a:picLocks", {"noChangeAspect": 1})
    bf = sub(pic, "pic:blipFill")
    sub(bf, "a:blip", {"r:embed": rid})
    st = sub(bf, "a:stretch")
    sub(st, "a:fillRect")
    sp = sub(pic, "pic:spPr")
    xfrm(sp, "a:xfrm", w, h, rot)
    g = sub(sp, "a:prstGeom", {"prst": "rect"})
    sub(g, "a:avLst")
    return pic


_DOCX_IMAGE_EXT = {"png", "jpg", "jpeg", "gif", "bmp", "tif", "tiff"}


def image_descriptor(store, ref):
    """圖片檔：Word 認得的格式直接用；其他（例如 webp）先轉成 PNG。"""
    path = store.blob_path(ref)
    if ref.ext.lower() in _DOCX_IMAGE_EXT:
        return str(path)
    from PIL import Image

    buf = io.BytesIO()
    Image.open(path).save(buf, format="PNG")
    buf.seek(0)
    return buf


# ---------- 分節 ----------


def set_sect(sect: etree._Element, w_pt: float, h_pt: float, margins: tuple[float, float, float, float] = (0, 0, 0, 0),
             header_pt: float = 0.0, footer_pt: float = 0.0, cols: Optional[list[tuple[float, float]]] = None,
             sect_type: Optional[str] = None) -> None:
    """改寫一個 w:sectPr：紙張、邊界（上右下左，pt）、頁首頁尾距離、分欄、分節方式。保留頁首頁尾參照。"""
    keep = [c for c in sect if c.tag in (q("w:headerReference"), q("w:footerReference"))]
    for c in list(sect):
        sect.remove(c)
    for c in keep:
        sect.append(c)
    if sect_type:
        sub(sect, "w:type", {"w:val": sect_type})
    attrs = {"w:w": twip(w_pt), "w:h": twip(h_pt)}
    if w_pt > h_pt:
        attrs["w:orient"] = "landscape"
    sub(sect, "w:pgSz", attrs)
    t, r, b, l = margins
    sub(sect, "w:pgMar", {"w:top": twip(t), "w:right": twip(r), "w:bottom": twip(b), "w:left": twip(l),
                          "w:header": twip(header_pt), "w:footer": twip(footer_pt), "w:gutter": 0})
    if cols and len(cols) > 1:
        c = sub(sect, "w:cols", {"w:num": len(cols), "w:space": twip(cols[0][1]), "w:equalWidth": 0})
        for i, (cw, gap) in enumerate(cols):
            attrs = {"w:w": twip(cw)}
            if i < len(cols) - 1:
                attrs["w:space"] = twip(gap)
            sub(c, "w:col", attrs)
    else:
        sub(sect, "w:cols", {"w:space": 720})
    sub(sect, "w:docGrid", {"w:linePitch": 360})


# ---------- 檔案自檢 ----------


def _story_parts(z: zipfile.ZipFile) -> list[str]:
    return [n for n in z.namelist() if re.match(r"word/(document|header\d*|footer\d*)\.xml$", n)]


def extract_texts(data: bytes) -> dict[str, str]:
    """各文字部件的全部文字（不含 mc:Fallback 退路，以免重複）。"""
    out = {}
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        for name in _story_parts(z):
            root = etree.fromstring(z.read(name))
            for fb in root.iter(q("mc:Fallback")):
                fb.getparent().remove(fb)
            out[name] = "".join(t.text or "" for t in root.iter(q("w:t"), q("w:instrText")) if t.tag == q("w:t"))
    return out


def selfcheck(data: bytes, expect_sections: int, expect_texts: list[str], fonts_used: set[str]) -> tuple[bool, list[str]]:
    """封裝結構、重新讀入、分節數、文字往返、字型核對、禁止內容。回傳（是否通過, 說明）。"""
    notes: list[str] = []
    ok = True
    try:
        z = zipfile.ZipFile(io.BytesIO(data))
        bad = z.testzip()
        if bad:
            return False, [f"自檢：壓縮檔損毀（{bad}）。"]
        names = set(z.namelist())
        for need in ("[Content_Types].xml", "_rels/.rels", "word/document.xml"):
            if need not in names:
                ok = False
                notes.append(f"自檢：缺少必要部件 {need}。")
        for n in names:
            if n.endswith(".xml") or n.endswith(".rels"):
                etree.fromstring(z.read(n))  # 每個 XML 必須格式正確
            if "vbaProject" in n or n.startswith("word/embeddings/"):
                ok = False
                notes.append(f"自檢：含禁止內容 {n}。")
        for n in names:
            if not n.endswith(".rels"):
                continue
            base = posixpath.dirname(posixpath.dirname(n))
            for rel in etree.fromstring(z.read(n)):
                if rel.get("TargetMode") == "External":
                    ok = False
                    notes.append(f"自檢：含外部連結 {rel.get('Target')}。")
                    continue
                target = rel.get("Target", "")
                path = target.lstrip("/") if target.startswith("/") else posixpath.normpath(posixpath.join(base, target))
                if path not in names:
                    ok = False
                    notes.append(f"自檢：關聯指向不存在的部件 {path}。")
        # 字型核對：文字部件與文件預設中出現的字型名稱都要在字型使用表裡
        written: set[str] = set()
        for n in _story_parts(z) + ["word/styles.xml"]:
            root = etree.fromstring(z.read(n))
            scope = root.iter(q("w:rFonts")) if n != "word/styles.xml" else (
                f for s in root if s.tag == q("w:docDefaults") or (s.tag == q("w:style") and (s.get(q("w:styleId")) or "").startswith(("Mortise", "Heading")))
                for f in s.iter(q("w:rFonts")))
            for f in scope:
                for attr in ("w:ascii", "w:hAnsi", "w:eastAsia", "w:cs"):
                    v = f.get(q(attr))
                    if v:
                        written.add(v)
        extra = written - fonts_used
        if extra:
            ok = False
            notes.append(f"自檢：檔內有未列入字型使用表的字型 {sorted(extra)}。")
        from docx import Document

        again = Document(io.BytesIO(data))
        if len(again.sections) != expect_sections:
            ok = False
            notes.append(f"自檢：分節數 {len(again.sections)} 與預期 {expect_sections} 不符。")
        all_text = norm_text("".join(extract_texts(data).values()))
        missing = [t for t in expect_texts if norm_text(t) and norm_text(t) not in all_text]
        if missing:
            ok = False
            notes.append(f"自檢：{len(missing)} 段文字在輸出檔中找不到，例如「{missing[0][:30]}」。")
    except Exception as e:  # pragma: no cover - 防禦：任何讀不回來的狀況都算失敗
        return False, notes + [f"自檢：輸出檔無法重新開啟（{type(e).__name__}: {e}）。"]
    return ok, notes


def anchor_geometry(data: bytes) -> dict[str, tuple[int, int, int, int]]:
    """讀回每個浮動物件的位置與大小（EMU），以 docPr 的名稱為鍵：幾何往返檢查用。"""
    out = {}
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        root = etree.fromstring(z.read("word/document.xml"))
    for a in root.iter(q("wp:anchor")):
        name = a.find(q("wp:docPr")).get("name")
        x = int(a.find(f"{q('wp:positionH')}/{q('wp:posOffset')}").text)
        y = int(a.find(f"{q('wp:positionV')}/{q('wp:posOffset')}").text)
        ext = a.find(q("wp:extent"))
        out[name] = (x, y, int(ext.get("cx")), int(ext.get("cy")))
    return out


def save_bytes(docx_doc) -> bytes:
    buf = io.BytesIO()
    docx_doc.save(buf)
    return buf.getvalue()


def table_texts(tbl: StyledTable) -> list[str]:
    return ["".join(r.text for r in sp.runs) for c in tbl.cells for sp in c.paragraphs]
