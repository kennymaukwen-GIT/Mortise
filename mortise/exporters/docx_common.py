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
  | 推論的字型名稱                 | 寫入名稱改用多數裝置都有、或字寬相容的字型（見下）    | 字寬相容的對照無損；依類別退回有損（都記錄） |
  | 字型的類別與替代資訊           | word/fontTable.xml 每個字型的 family、pitch、panose1、charset、altName | 不適用 |

寫入的字型名稱（設定 [export] docx_font_names，預設 "portable"）：
  使用者的裝置上常常沒有⑤推論出的字型（例如 Inter、Loma、FreeSans）。找不到字型時，各軟體只能靠字型表的資訊
  猜替代字型；資訊不足時多半退回 Times 類的襯線字，字寬一變，版面就跑掉。所以寫入檔案的名稱照下面的順序決定：
    1. docx_font_map 有列的（例如 Liberation Sans → Arial、Carlito → Calibri）：寫右邊的名稱，兩者字寬相容（無損）。
    2. 已經是 docx_portable_fonts 列的常見字型：照原名寫。
    3. 其他：判斷類別（無襯線、襯線、等寬；中文分黑體、明體、楷體），寫 docx_fallback_fonts 裡該類別的常見字型（有損）。
  等寬字型只有在整個文字框都是等寬字時才保留（程式碼之類）；夾在一般內文裡的等寬字多半是辨識誤判，改用同框內文的字型。
  成品文件裡的推論字型不改（不回寫上游），原本推論的字型名稱寫在 OutputFile.degradations。
  docx_font_names = "inferred" 時照推論的名稱寫（舊做法），只補字型表的資訊。

字型表（word/fontTable.xml）：只列檔內實際用到的字型，每個都寫 family（swiss 無襯線、roman 有襯線、modern 等寬）、
  pitch、panose1（有就寫）、charset 與 altName（同一字型的其他名稱與字寬相容的替代字型），讓找不到字型的軟體
  退回同類的字型（ECMA-376 第 1 部 17.8.3）。各軟體是否真的照這些資訊退回：需查證（只在 LibreOffice 上看過結果）。

內嵌字型（設定 docx_embed_fonts，預設關）：評估與做法見本檔「內嵌字型」一節。
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
    """一次輸出的共用狀態：預設字型、降級紀錄、寫入的字型、比例、繪圖物件編號、字型名稱的對照。"""

    doc_latin: str
    doc_ea: str
    degradations: list[Degradation] = field(default_factory=list)
    fonts: set[str] = field(default_factory=set)
    faces: set[tuple[str, bool, bool]] = field(default_factory=set)  # 拉丁字型實際用到的字面（名稱, 粗體, 斜體）：內嵌字型用
    scale: float = 1.0
    _seen: set = field(default_factory=set)
    _next_id: int = 1
    policy: Optional["FontPolicy"] = None

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

    def font_name(self, font, element_id: str = "document", context_latin: Optional[str] = None, record: bool = True) -> str:
        """一個文字段（FontChoice）寫進檔案的字型名稱。context_latin 是同框內文的寫入名稱（夾在內文裡的等寬字改用它）。
        record 為假時只查名稱、不記降級（統計字數用）。"""
        if self.policy is None:
            return font.family.value
        return self.policy.written(font.family.value, font.verification_family, font.east_asian, self, element_id, context_latin, record)

    def name_of(self, family: str, east_asian: bool = False, record: bool = True) -> str:
        """只有名稱（例如樣式表的 font_family）時的寫入名稱。"""
        if self.policy is None:
            return family
        return self.policy.written(family, family, east_asian, self, record=record)


# ---------- 寫入的字型名稱：換一台裝置也找得到 ----------

# 字型表資訊（ECMA-376 第 1 部 17.8.3）：（family, pitch, panose1, charset, altName）。
#   family：swiss 無襯線、roman 有襯線、modern 等寬、script 手寫（楷體）；pitch：variable 比例、fixed 等寬；
#   panose1：字型的 PANOSE 分類（十個位元組）；charset：00 西文、88 繁體中文（Big5）；
#   altName：同一個字型的其他名稱（逗號分隔），這裡也列量度相容的替代字型，讓找不到原字型的軟體有名稱可找。
# 各軟體是否真的照這些資訊挑替代字型：需查證（Word、Pages、iOS 預覽都沒有在這台機器上實測過）。
FONT_TABLE_INFO: dict[str, tuple[str, str, str, str, str]] = {
    "Arial": ("swiss", "variable", "020B0604020202020204", "00", "Helvetica,Liberation Sans,Arimo"),
    "Helvetica": ("swiss", "variable", "020B0604020202020204", "00", "Arial,Liberation Sans"),
    "Calibri": ("swiss", "variable", "020F0502020204030204", "00", "Carlito"),
    "Verdana": ("swiss", "variable", "020B0604030504040204", "00", "Arial"),
    "Tahoma": ("swiss", "variable", "020B0604030504040204", "00", "Arial"),
    "Trebuchet MS": ("swiss", "variable", "020B0603020202020204", "00", "Arial"),
    "Segoe UI": ("swiss", "variable", "020B0502040204020203", "00", "Arial"),
    "Times New Roman": ("roman", "variable", "02020603050405020304", "00", "Times,Liberation Serif,Tinos"),
    "Cambria": ("roman", "variable", "02040503050406030204", "00", "Caladea"),
    "Georgia": ("roman", "variable", "02040502050405020303", "00", "Times New Roman"),
    "Courier New": ("modern", "fixed", "02070309020205020404", "00", "Courier,Liberation Mono,Cousine"),
    "Microsoft JhengHei": ("swiss", "variable", "020B0604030504040204", "88", "微軟正黑體,PingFang TC,Noto Sans CJK TC"),
    "PMingLiU": ("roman", "variable", "02020500000000000000", "88", "新細明體,LiSong Pro,Noto Serif CJK TC"),
    "DFKai-SB": ("script", "fixed", "03000509000000000000", "88", "標楷體,BiauKai,Kaiti TC"),
}

FONT_DEFAULTS: dict = {
    "docx_font_names": "portable",
    "docx_font_map": {
        "Liberation Sans": "Arial", "Arimo": "Arial", "Helvetica": "Arial", "Liberation Sans Narrow": "Arial",
        "Liberation Serif": "Times New Roman", "Tinos": "Times New Roman", "Times": "Times New Roman",
        "Liberation Mono": "Courier New", "Cousine": "Courier New", "Courier": "Courier New",
        "Carlito": "Calibri", "Caladea": "Cambria",
        "Noto Sans TC": "Microsoft JhengHei", "Noto Sans CJK TC": "Microsoft JhengHei", "Source Han Sans TC": "Microsoft JhengHei",
        "WenQuanYi Zen Hei": "Microsoft JhengHei", "微軟正黑體": "Microsoft JhengHei",
        "Noto Serif TC": "PMingLiU", "Noto Serif CJK TC": "PMingLiU", "Source Han Serif TC": "PMingLiU", "新細明體": "PMingLiU",
        "MingLiU": "PMingLiU", "細明體": "PMingLiU", "LXGW WenKai TC": "DFKai-SB", "標楷體": "DFKai-SB",
    },
    "docx_portable_fonts": ["Arial", "Times New Roman", "Courier New", "Calibri", "Cambria", "Georgia", "Verdana", "Tahoma",
                            "Trebuchet MS", "Microsoft JhengHei", "PMingLiU", "DFKai-SB"],
    "docx_fallback_fonts": {"sans": "Arial", "serif": "Times New Roman", "mono": "Courier New",
                            "cjk_sans": "Microsoft JhengHei", "cjk_serif": "PMingLiU", "cjk_kai": "DFKai-SB"},
    "docx_embed_fonts": False,
    "docx_embed_max_kb": 3072,
    "docx_embed_deny_prefixes": ["Unifont"],
}

_CATEGORY_LABEL = {"sans": "無襯線", "serif": "有襯線", "mono": "等寬", "cjk_sans": "中文黑體", "cjk_serif": "中文明體", "cjk_kai": "中文楷體"}
_MONO_WORDS = ("mono", "code", "courier", "consol", "menlo", "monaco", "typewriter", "等寬")
_SERIF_WORDS = ("serif", "times", "roman", "garamond", "georgia", "baskerville", "bodoni", "caslon", "cambria", "caladea",
                "palatino", "book antiqua", "century", "minion", "charter", "didot")
_CJK_SERIF_WORDS = ("ming", "song", "mincho", "serif", "明", "宋")
_CJK_KAI_WORDS = ("kai", "楷")
_FONT_FILE_CACHE: dict[str, Optional[dict]] = {}


def font_file_info(family: str) -> Optional[dict]:
    """伺服器上這個字族的字型檔資訊（mortise.tools.fontfind 找檔、fontTools 讀）：是否等寬、PANOSE、類別、可否內嵌（fsType）、檔案路徑。
    找到的不是這個字族（沒有安裝，退回別的字型）就回傳 None。"""
    if family in _FONT_FILE_CACHE:
        return _FONT_FILE_CACHE[family]
    info = None
    try:
        from fontTools.ttLib import TTFont

        from ..tools import fontfind

        found = fontfind.find_file(family)
        if found is not None:
            path, index = found
            f = TTFont(path, fontNumber=index, lazy=True)
            os2 = f["OS/2"]
            p = os2.panose
            pan = [p.bFamilyType, p.bSerifStyle, p.bWeight, p.bProportion, p.bContrast, p.bStrokeVariation, p.bArmStyle, p.bLetterForm, p.bMidline, p.bXHeight]
            info = {"path": path, "fixed": bool(f["post"].isFixedPitch) or pan[3] == 9, "panose": "".join(f"{v:02X}" for v in pan),
                    "family_class": os2.sFamilyClass >> 8, "fs_type": os2.fsType}
    except Exception:
        info = None
    _FONT_FILE_CACHE[family] = info
    return info


def font_category(family: str, verification: Optional[str], east_asian: bool) -> str:
    """字型的類別：sans、serif、mono；中文為 cjk_sans、cjk_serif、cjk_kai。
    先看伺服器上的字型檔（等寬旗標、PANOSE 的襯線樣式、sFamilyClass），看不出來再看名稱。"""
    low = (family or "").lower()
    if east_asian:
        if any(w in low for w in _CJK_KAI_WORDS):
            return "cjk_kai"
        if any(w in low for w in _CJK_SERIF_WORDS) and "sans" not in low:
            return "cjk_serif"
        return "cjk_sans"
    info = None
    for name in dict.fromkeys(n for n in (verification, family) if n):
        info = font_file_info(name)
        if info is not None:
            break
    if info is not None:
        if info["fixed"]:
            return "mono"
        fam_type, serif_style = int(info["panose"][0:2], 16), int(info["panose"][2:4], 16)
        if fam_type == 2 and 11 <= serif_style <= 15:
            return "sans"
        if fam_type == 2 and 2 <= serif_style <= 10:
            return "serif"
        if info["family_class"] == 8:
            return "sans"
        if info["family_class"] in (1, 2, 3, 4, 5, 7):
            return "serif"
    if any(w in low for w in _MONO_WORDS):
        return "mono"
    if any(w in low for w in _SERIF_WORDS) and "sans" not in low:
        return "serif"
    return "sans"


def font_settings(cfg) -> dict:
    """[export] 裡 docx_ 開頭的字型設定（沒寫的用預設值）。
    docx_font_map 在設定檔裡寫成 [[推論的名稱, 寫入的名稱], ...]（TOML 的行內表格不能跨行），這裡轉成對照表。"""
    out = dict(FONT_DEFAULTS)
    if cfg is not None:
        sec = cfg.section("export")
        out.update({k: sec[k] for k in FONT_DEFAULTS if k in sec})
    fm = out["docx_font_map"]
    if not isinstance(fm, dict):
        out["docx_font_map"] = {str(a): str(b) for a, b in fm}
    return out


class FontPolicy:
    """推論的字型名稱 → 寫進檔案的名稱（見模組說明）。同一個名稱只判斷一次，降級只記一次。"""

    def __init__(self, settings: dict):
        self.mode = settings["docx_font_names"]
        self.font_map = dict(settings["docx_font_map"])
        self.portable = set(settings["docx_portable_fonts"])
        self.fallback = dict(settings["docx_fallback_fonts"])
        self.embed = bool(settings["docx_embed_fonts"])
        self.embed_max_kb = float(settings["docx_embed_max_kb"])
        self.embed_deny = tuple(settings["docx_embed_deny_prefixes"])
        self._cache: dict[tuple, tuple[str, str]] = {}
        self.mono_names: set[str] = set()  # 寫入名稱中屬於等寬字型的

    def category(self, family: str, verification: Optional[str], east_asian: bool) -> str:
        return font_category(family, verification, east_asian)

    def is_mono(self, family: str, verification: Optional[str] = None) -> bool:
        return self.category(family, verification, False) == "mono"

    def _decide(self, family: str, verification: Optional[str], east_asian: bool) -> tuple[str, str]:
        key = (family, verification, east_asian)
        if key not in self._cache:
            if self.mode != "portable":
                self._cache[key] = (family, "")
            elif family in self.font_map:
                self._cache[key] = (self.font_map[family], "map")
            elif family in self.portable:
                self._cache[key] = (family, "")
            else:
                cat = self.category(family, verification, east_asian)
                self._cache[key] = (self.fallback.get(cat) or self.fallback.get("sans", "Arial"), cat)
        return self._cache[key]

    def written(self, family: str, verification: Optional[str], east_asian: bool, ctx: "Ctx", element_id: str = "document",
                context_latin: Optional[str] = None, record: bool = True) -> str:
        if (self.mode == "portable" and not east_asian and context_latin and context_latin not in self.mono_names
                and self.is_mono(family, verification)):
            if record:
                ctx.degrade(element_id, f"等寬字型「{family}」", f"夾在一般內文裡（多半是辨識誤判），改用同框內文的字型「{context_latin}」", True)
            return context_latin
        name, why = self._decide(family, verification, east_asian)
        if why == "mono" or (not east_asian and self.mode == "portable" and self.is_mono(family, verification)):
            self.mono_names.add(name)
        if record and why == "map" and name != family:
            ctx.degrade("document", f"字型「{family}」", f"與「{name}」字寬相容，寫成多數裝置都有的「{name}」", False)
        elif record and why and why != "map":
            ctx.degrade("document", f"字型「{family}」", f"推論的字型「{family}」不是多數裝置都有的字型，依類別（{_CATEGORY_LABEL.get(why, why)}）"
                        f"寫成「{name}」；成品文件仍記原本推論的「{family}」", True)
        return name


def _all_paragraphs(doc: StyledDoc) -> Iterable[StyledParagraph]:
    for page in doc.pages:
        for tb in page.text_boxes:
            yield from tb.paragraphs
        for t in page.tables:
            for c in t.cells:
                yield from c.paragraphs


def _box_paragraph_groups(doc: StyledDoc) -> Iterable[list[StyledParagraph]]:
    for page in doc.pages:
        for tb in page.text_boxes:
            yield tb.paragraphs
        for t in page.tables:
            for c in t.cells:
                yield c.paragraphs


def make_ctx(doc: StyledDoc, cfg=None) -> Ctx:
    """全文件的預設字型：拉丁與東亞各取字數最多的寫入字型（等寬字型只在沒有別的字型時才當預設）。"""
    ctx = Ctx(doc_latin="", doc_ea="", policy=FontPolicy(font_settings(cfg)))
    latin: Counter = Counter()
    ea: Counter = Counter()
    mono: Counter = Counter()
    for paras in _box_paragraph_groups(doc):
        box_latin, _ = context_fonts(paras, ctx)
        for sp in paras:
            for r in sp.runs:
                name = ctx.font_name(r.font, context_latin=box_latin, record=False)
                n = max(1, len(r.text))
                if r.font.east_asian:
                    ea[name] += n
                elif name in ctx.policy.mono_names:
                    mono[name] += n
                else:
                    latin[name] += n
    latin = latin or mono
    ctx.doc_latin = latin.most_common(1)[0][0] if latin else (ea.most_common(1)[0][0] if ea else ctx.name_of("Liberation Sans", record=False))
    ctx.doc_ea = ea.most_common(1)[0][0] if ea else ctx.doc_latin
    return ctx


def context_fonts(paragraphs: list[StyledParagraph], ctx: Ctx) -> tuple[str, str]:
    """一群段落（同一文字框或儲存格）的拉丁與東亞寫入字型：字數最多的那個（拉丁優先取非等寬字型）。
    給「只有拉丁字」的段落填東亞欄位用，也是夾在內文裡的等寬字改用的字型。
    docx_font_names = "inferred" 時照舊做法：取第一個出現的推論字型。"""
    if ctx.policy is None or ctx.policy.mode != "portable":
        lat = next((r.font.family.value for sp in paragraphs for r in sp.runs if not r.font.east_asian), ctx.doc_latin)
        e = next((r.font.family.value for sp in paragraphs for r in sp.runs if r.font.east_asian), ctx.doc_ea)
        return lat, e
    latin: Counter = Counter()
    mono: Counter = Counter()
    ea: Counter = Counter()
    for sp in paragraphs:
        for r in sp.runs:
            n = max(1, len(r.text))
            name = ctx.font_name(r.font, record=False)
            if r.font.east_asian:
                ea[name] += n
            elif ctx.policy.is_mono(r.font.family.value, r.font.verification_family):
                mono[name] += n
            else:
                latin[name] += n
    lat_c = latin or mono
    return (lat_c.most_common(1)[0][0] if lat_c else (ctx.doc_latin or "Arial"),
            ea.most_common(1)[0][0] if ea else (ctx.doc_ea or ctx.doc_latin or "Arial"))


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
    spacing: int = 0  # 字距（twip，負值為緊縮）：版面固定模式抵消寫入字型與量測字型的字寬差


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


def run_format(sr: StyledRun, latin: str, ea: str, scale: float = 1.0, ctx: Optional[Ctx] = None, element_id: str = "document") -> RunFmt:
    """字型欄位：東亞文字段的 eastAsia 寫它自己的字型、ascii／hAnsi 寫鄰近的拉丁字型；
    拉丁文字段的 ascii／hAnsi 寫它自己的字型、eastAsia 寫鄰近的東亞字型。
    實測：東亞段尾的半形空白若用中文字型畫，空白寬約 0.5 em，置中的標題因此偏移 5 pt 以上；
    交給拉丁字型（Word 本來就依字元範圍選欄位）後誤差消失。使用者在任何位置打中文或英文都不會落回預設字型。
    給了 ctx 時，字型名稱是寫入名稱（見模組說明「寫入的字型名稱」），latin、ea 也要是寫入名稱。"""
    fam = ctx.font_name(sr.font, element_id, context_latin=latin) if ctx is not None else sr.font.family.value
    return RunFmt(
        ascii=(latin or fam) if sr.font.east_asian else fam,
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
    if fmt.spacing and (base is None or fmt.spacing != base.spacing):
        sub(rpr, "w:spacing", {"w:val": fmt.spacing})
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
    right_tw: int = 0,
    tabs: Optional[list[tuple[str, int]]] = None,
    border_bottom: Optional[tuple[int, int, str]] = None,
) -> etree._Element:
    """段落屬性；base 不為空時只寫差異。子元素照綱要順序（pStyle … pBdr、tabs、spacing、ind、jc、outlineLvl、rPr）。
    tabs：定位點 [(種類 left／right／center, 位置 twip)]，位置從文字區（邊界或儲存格）的左緣算起；
    border_bottom：段落下框線（寬度八分之一點, 與文字的距離 pt, 顏色）。"""
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
    if border_bottom is not None:
        bdr = sub(ppr, "w:pBdr")
        sub(bdr, "w:bottom", {"w:val": "single", "w:sz": border_bottom[0], "w:space": border_bottom[1], "w:color": border_bottom[2]})
    if tabs:
        t = sub(ppr, "w:tabs")
        for kind, pos in tabs:
            sub(t, "w:tab", {"w:val": kind, "w:pos": pos})
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
    if right_tw:
        ind["w:right"] = right_tw
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
    right_tw: int = 0,
    gap_scale: float = 1.0,
    tail: Optional[list[tuple[str, int, StyledParagraph, str]]] = None,
    border_bottom: Optional[tuple[int, int, str]] = None,
    run_spacing: Optional[list[int]] = None,
) -> etree._Element:
    """一個成品段落 → 一個 w:p。diff 為真時只寫與樣式不同的屬性（可重排模式）；
    否則全部明寫（版面固定模式：不依賴樣式繼承，任何軟體開都一樣）。
    gap_scale：段前、段後距離乘上這個比例（可重排模式為了放進一頁而等比壓縮空隙時用）。
    tail：接在同一行後面的其他段落 [(定位點種類, 位置 twip, 段落, 元素編號)]，用定位字元隔開
    （例如經歷標題右邊的日期：原圖是同一行的兩個框，接成一段才不會在別的字型下互相重疊）。
    run_spacing：每個文字段的字距（twip，與 sp.runs 對齊），見 condense_spacing。"""
    pf = para_format(sp, ctx.scale)
    if gap_scale != 1.0:
        pf = replace(pf, before=int(round(pf.before * gap_scale)), after=int(round(pf.after * gap_scale)))
    if extra_before_tw:
        pf = replace(pf, before=pf.before + extra_before_tw)
    if align_override:
        pf = replace(pf, align=align_override)
    base_pf = style.para if (diff and style) else None
    base_rf = style.run if (diff and style) else None
    p = el("w:p")
    runs_out: list[tuple[RunFmt, str, bool]] = []

    def collect(par: StyledParagraph, eid: str, remaining: int, spacing: Optional[list[int]] = None) -> None:
        for k, sr in enumerate(par.runs):
            text, removed = clean_text(sr.text)
            if removed:
                ctx.degrade(eid, "文字中的控制字元", f"清除 {removed} 個 XML 不允許的字元", True)
            if remaining:
                cut = min(remaining, len(text))
                text, remaining = text[cut:], remaining - cut
                if not text:
                    continue
            fmt = run_format(sr, latin, ea, ctx.scale, ctx, eid)
            if spacing and k < len(spacing) and spacing[k]:
                fmt = replace(fmt, spacing=spacing[k])
            exact_half = sr.size_pt.value * ctx.scale * 2
            if abs(exact_half - fmt.size_half) > 0.02:
                ctx.degrade(eid, "字級", f"Word 字級以半點為單位，{sr.size_pt.value:g} pt 量化為 {fmt.size_half / 2:g} pt", True)
            if sr.color.a < 0.999:
                ctx.degrade(eid, "文字透明度", "Word 的文字顏色沒有透明度，以不透明顏色輸出", True)
            ctx.fonts.update({fmt.ascii, fmt.east_asia})
            ctx.faces.add((fmt.ascii, fmt.bold, fmt.italic))
            hint = sr.font.east_asian and bool(_AMBIGUOUS.search(text))
            runs_out.append((fmt, text, hint))

    collect(sp, element_id, strip_chars, run_spacing)
    n_main = len(runs_out)
    tabs = []
    for kind, pos, tsp, tid in tail or []:
        tabs.append((kind, pos))
        runs_out.append((runs_out[-1][0] if runs_out else RunFmt(latin, ea, 22, False, False, "000000"), "\t", False))
        collect(tsp, tid, 0)
    mark = None
    if runs_out:
        mark = rpr_element(runs_out[n_main - 1 if n_main else -1][0], base_rf)
    else:
        mark_fmt = RunFmt(latin, ea, half_pt(sp.runs[0].size_pt.value * ctx.scale) if sp.runs else 22, False, False, "000000")
        mark = rpr_element(mark_fmt, base_rf)
    p.append(ppr_element(pf, base_pf, style.word_id if style else None, style.outline if style else None, keep_next, page_break_before, numpr, mark,
                         left_tw, hanging_tw, right_tw, sorted(tabs, key=lambda t: t[1]) or None, border_bottom))
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
    ctx.faces.add((ctx.doc_latin, False, False))
    settings = docx_doc.settings.element
    for cs in settings.iter(q("w:compatSetting")):
        if cs.get(q("w:name")) == "compatibilityMode":
            cs.set(q("w:val"), "15")


def _is_cjk_name(name: str) -> bool:
    """看名稱判斷是不是中日韓字型（字型表的類別與內嵌字型用）。"""
    return bool(re.search(r"[⺀-鿿豈-﫿]", name)) or any(k in name.lower() for k in ("cjk", " tc", " sc", "hei", "ming", "song", "kai", "gothic"))


_FAMILY_OF_CATEGORY = {"sans": "swiss", "serif": "roman", "mono": "modern", "cjk_sans": "swiss", "cjk_serif": "roman", "cjk_kai": "script"}


def font_table_entry(name: str, ctx: Ctx) -> etree._Element:
    """字型使用表的一筆（子元素照綱要順序：altName、panose1、charset、family、pitch）。
    常見字型用 FONT_TABLE_INFO 的固定值；其他字型由伺服器上的字型檔判斷類別、讀 PANOSE，altName 寫同類的常見字型。"""
    f = el("w:font", {"w:name": name})
    info = FONT_TABLE_INFO.get(name)
    if info is None:
        cjk = _is_cjk_name(name)
        cat = font_category(name, name, cjk)
        fi = font_file_info(name)
        panose = fi["panose"] if fi and fi["panose"][:2] not in ("00",) else ""
        fallback = (ctx.policy.fallback if ctx.policy else FONT_DEFAULTS["docx_fallback_fonts"]).get(cat, "Arial")
        info = (_FAMILY_OF_CATEGORY[cat], "fixed" if cat == "mono" else "variable", panose, "88" if cat.startswith("cjk") else "00",
                fallback if fallback != name else "")
    family, pitch, panose, charset, alt = info
    if alt:
        sub(f, "w:altName", {"w:val": alt})
    if panose:
        sub(f, "w:panose1", {"w:val": panose})
    sub(f, "w:charset", {"w:val": charset})
    sub(f, "w:family", {"w:val": family})
    sub(f, "w:pitch", {"w:val": pitch})
    return f


def finish_package(docx_doc, ctx: Ctx) -> None:
    """存檔前的收尾：
    - word/fontTable.xml 改寫成「檔內實際用到的字型」，每個都有類別與替代資訊（python-docx 範本的字型表只列了
      Times New Roman、Symbol 這類用不到的字型，family 全是 auto；找不到字型的軟體因此只能退回預設的襯線字）。
    - 拿掉範本附的縮圖（docProps/thumbnail.jpeg 是空白範本的縮圖，和內容無關；檔案管理員可能拿它當預覽）。"""
    pkg = docx_doc.part.package
    for part in pkg.iter_parts():
        if str(part.partname) == "/word/fontTable.xml":
            root = etree.fromstring(part.blob)
            for ch in list(root):
                root.remove(ch)
            for name in sorted(ctx.fonts):
                root.append(font_table_entry(name, ctx))
            if ctx.policy is not None and ctx.policy.embed:
                embed_fonts(docx_doc, part, root, ctx)
            part._blob = etree.tostring(root, xml_declaration=True, encoding="UTF-8", standalone=True)
    for rid, rel in list(pkg.rels.items()):
        if rel.reltype.endswith("/metadata/thumbnail"):
            del pkg.rels[rid]


# ---------- 內嵌字型（設定 docx_embed_fonts，預設關） ----------
#
# 評估（2026-11，只在這台機器用 LibreOffice 實測過）：
#   做法：ECMA-376 第 1 部 17.8.1：字型檔放在 word/fonts/*.odttf，前 32 位元組與 fontKey（GUID 去掉括號與連字號、
#     16 個位元組反序）做 XOR；fontTable.xml 的 w:font 加 w:embedRegular／embedBold／embedItalic／embedBoldItalic
#     （r:id 指向字型部件、w:fontKey）；settings.xml 寫 w:embedTrueTypeFonts（Word 再存檔時才會保留內嵌）。
#   哪些軟體會讀：LibreOffice 24.2 會（實測：只留 DejaVu 的環境下，以「Arial」名稱內嵌的 Liberation Sans 確實被拿來排版）。
#     Word for Windows 會讀內嵌的 TrueType 字型（微軟說明文件）；Word for Mac（16.17 以後）、Word for iOS／Android、
#     Pages、Google 文件：需查證，這台機器無法實測。不讀的軟體照字型表的 altName、family 退回同類字型，和不內嵌時一樣。
#   授權：只內嵌 OS/2 fsType 允許「可安裝」（0）或「可編輯」（8）的字型；「限制」（2）、「預覽與列印」（4，會讓文件唯讀）、
#     「只能內嵌點陣」（0x200）都不內嵌。伺服器上用來代替 Arial、Times New Roman、Courier New、Calibri、Cambria 的
#     Liberation、Carlito、Caladea 都是 SIL OFL 1.1、fsType 0：字型檔原封不動放進檔案（只做規格要求的混淆）。
#     以「Arial」這個名稱引用 Liberation Sans 檔是否有商標上的疑慮：需查證（字型檔本身沒有改名、沒有修改）。
#     docx_embed_deny_prefixes 列的字族不內嵌（同 PDF 輸出，GNU Unifont 授權待確認）。
#   只內嵌 TrueType 外框（有 glyf 表）的字型：規格稱內嵌字型為 obfuscated TrueType，CFF 外框（例如 Inter 的 .otf）
#     各軟體能不能讀：需查證，所以不內嵌。中日韓字型動輒十幾 MB，超過 docx_embed_max_kb 也不內嵌。
#   只內嵌「寫入的名稱本身、或與它字寬相容」的字型檔（例如 Arial ← Liberation Sans）：寫入的名稱依類別退回而來的
#     （字寬不同）不內嵌，否則內嵌的字和寫入的名稱字寬不一致，反而在有那個字型的裝置上排得不一樣。
#   檔案大小：一個字面約 300 到 600 KB（壓縮後約一半）；實測履歷的兩個字面讓 docx 由 37 KB 變成 451 KB。
#   整個字型內嵌（不做子集）：使用者改字、加字時才不會缺字。
#   結論：字型表的 family、altName 加上寫入常見字型名稱已經處理大多數裝置；內嵌只在「裝置上真的沒有 Arial、Calibri」
#     而且「軟體會讀內嵌字型」時才有差，檔案卻變大十倍，所以做成設定、預設關。

_FS_RESTRICTED, _FS_PREVIEW_PRINT, _FS_BITMAP_ONLY = 0x0002, 0x0004, 0x0200
_EMBED_TAGS = ((False, False, "w:embedRegular"), (True, False, "w:embedBold"), (False, True, "w:embedItalic"), (True, True, "w:embedBoldItalic"))
CT_OBFUSCATED_FONT = "application/vnd.openxmlformats-officedocument.obfuscatedFont"
REL_FONT = "http://schemas.openxmlformats.org/officeDocument/2006/relationships/font"
# settings.xml 裡排在 w:embedTrueTypeFonts 前面的元素（CT_Settings 的順序）
_SETTINGS_BEFORE_EMBED = ("writeProtection", "view", "zoom", "removePersonalInformation", "removeDateAndTime", "doNotDisplayPageBoundaries",
                          "displayBackgroundShape", "printPostScriptOverText", "printFractionalCharacterWidth", "printFormsData")


def obfuscate_font(data: bytes, font_key: str) -> bytes:
    """ECMA-376 第 1 部 17.8.1 的字型混淆：fontKey（{XXXXXXXX-XXXX-…}）去掉括號與連字號得到 16 個位元組，反序後
    與字型檔的前 32 位元組逐一 XOR（第 0 到 15、16 到 31 位元組各用一次）。同一個函式也能還原。"""
    key = bytes.fromhex(font_key.strip("{}").replace("-", ""))[::-1]
    out = bytearray(data)
    for i in range(min(32, len(out))):
        out[i] ^= key[i % 16]
    return bytes(out)


def _metric_compatible(a: str, families: Iterable[str]) -> bool:
    from ..tools import fontfind

    na = fontfind.norm(a)
    names = {fontfind.norm(f) for f in families}
    return na in names or any(na in {fontfind.norm(x) for x in g} and names & {fontfind.norm(x) for x in g} for g in fontfind.METRIC_ALIASES)


def embeddable_face(name: str, bold: bool, italic: bool, deny: Iterable[str] = ()) -> tuple[Optional[bytes], str]:
    """寫入的字型名稱的一個字面 → （可以內嵌的字型檔內容, 說明）。不能內嵌時內容為 None，說明寫原因。"""
    from fontTools.ttLib import TTFont

    from ..tools import fontfind

    m = fontfind.match(name, bold, italic)
    if m is None:
        return None, "伺服器上找不到這個字型或字寬相容的字型"
    fam = m.families[0] if m.families else name
    if not _metric_compatible(name, m.families):
        return None, f"伺服器上只有字寬不同的「{fam}」，內嵌它會讓字寬和寫入的名稱不一致"
    if any(fam.startswith(p) for p in deny):
        return None, f"「{fam}」列在不內嵌的字型清單（docx_embed_deny_prefixes）"
    if (bold and m.weight < 150) or (italic and m.slant <= 0):
        return None, f"伺服器上沒有「{fam}」的{'粗' if bold else ''}{'斜' if italic else ''}體字面"
    try:
        f = TTFont(m.path, fontNumber=m.index, lazy=True)
        fs = int(f["OS/2"].fsType) if "OS/2" in f else 0
        has_glyf = "glyf" in f
    except Exception as e:  # pragma: no cover - 字型檔讀不了
        return None, f"字型檔讀不了（{type(e).__name__}）"
    if fs & (_FS_RESTRICTED | _FS_PREVIEW_PRINT | _FS_BITMAP_ONLY):
        return None, f"「{fam}」的內嵌旗標（OS/2 fsType = {fs:#06x}）不允許可編輯的內嵌"
    if not has_glyf:
        return None, f"「{fam}」是 CFF 外框（.otf），各軟體能不能讀內嵌的 CFF 字型需查證，不內嵌"
    if m.path.lower().endswith((".ttc", ".otc")):  # 字型集合：取出其中一個字型
        buf = io.BytesIO()
        TTFont(m.path, fontNumber=m.index).save(buf)
        return buf.getvalue(), fam
    with open(m.path, "rb") as fh:
        return fh.read(), fam


def embed_fonts(docx_doc, font_part, root: etree._Element, ctx: Ctx) -> None:
    """把用到的拉丁字型的字面內嵌進檔案（見上面的評估）。root 是改寫中的 fontTable.xml 根元素。"""
    import hashlib
    import uuid

    from docx.opc.packuri import PackURI
    from docx.opc.part import Part

    pkg = docx_doc.part.package
    budget = ctx.policy.embed_max_kb * 1024
    used = 0
    n = 0
    entries = {f.get(q("w:name")): f for f in root.findall(q("w:font"))}
    for name in sorted(entries):
        faces = {(b, i) for fam, b, i in ctx.faces if fam == name}
        if not faces:
            continue
        if _is_cjk_name(name) or name in {v for k, v in ctx.policy.fallback.items() if k.startswith("cjk")}:
            ctx.degrade("document", f"內嵌字型「{name}」", "中日韓字型檔太大（通常十幾 MB），不內嵌", False)
            continue
        done = []
        for bold, italic, tag in _EMBED_TAGS:
            if (bold, italic) not in faces and (bold or italic):  # 一般字面一定內嵌（使用者取消粗體時用得到），其他只內嵌用到的
                continue
            data, why = embeddable_face(name, bold, italic, ctx.policy.embed_deny)
            if data is None:
                if (bold, italic) in faces:
                    ctx.degrade("document", f"內嵌字型「{name}」", f"沒有內嵌：{why}", False)
                continue
            if used + len(data) > budget:
                ctx.degrade("document", f"內嵌字型「{name}」", f"沒有內嵌：超過內嵌字型的大小上限 {ctx.policy.embed_max_kb:g} KB（docx_embed_max_kb）", False)
                continue
            used += len(data)
            n += 1
            digest = hashlib.sha256(data + tag.encode()).hexdigest()
            key = "{" + str(uuid.uuid5(uuid.NAMESPACE_URL, "mortise-font:" + digest)).upper() + "}"  # 同一個字型檔每次得到同一把鑰匙（輸出可重現）
            part = Part(PackURI(f"/word/fonts/font{n}.odttf"), CT_OBFUSCATED_FONT, obfuscate_font(data, key), pkg)
            rid = font_part.relate_to(part, REL_FONT)
            sub(entries[name], tag, {"r:id": rid, "w:fontKey": key})
            done.append((tag.removeprefix("w:embed"), why))
        if done:
            ctx.degrade("document", f"內嵌字型「{name}」", f"內嵌伺服器上的「{done[0][1]}」（{'、'.join(t for t, _ in done)}）：讀得懂內嵌字型的軟體"
                        f"不必另外安裝（LibreOffice 實測會讀；Word 及其他軟體需查證）", False)
    if n:
        st = docx_doc.settings.element
        if st.find(q("w:embedTrueTypeFonts")) is None:
            e = el("w:embedTrueTypeFonts")
            prev = [c for c in st if etree.QName(c).localname in _SETTINGS_BEFORE_EMBED]
            if prev:
                prev[-1].addnext(e)
            else:
                st.insert(0, e)


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
        if paras:
            latin_f, ea_f = context_fonts(paras, ctx)
            has_latin = any(not r.font.east_asian for sp in paras for r in sp.runs)
            has_ea = any(r.font.east_asian for sp in paras for r in sp.runs)
            for sp in paras:  # 記錄降級（寫入名稱與推論名稱不同時）
                for r in sp.runs:
                    ctx.font_name(r.font, context_latin=latin_f)
            if not has_ea:
                ea_f = ctx.doc_ea
            if not has_latin:
                latin_f = ctx.name_of(s.font_family)
        else:
            latin_f = ea_f = ctx.name_of(s.font_family)
        pf_counts = Counter(para_format(sp, ctx.scale) for sp in paras)
        pf = pf_counts.most_common(1)[0][0] if pf_counts else ParaFmt()
        run = RunFmt(latin_f, ea_f, half_pt(s.size_pt * ctx.scale), s.bold, s.italic, s.color.hex)
        ctx.fonts.update({latin_f, ea_f})
        ctx.faces.add((latin_f, s.bold, s.italic))
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


# ---------- 字寬（單行文字框留足寬度用） ----------

_ADVANCE_CACHE: dict[str, Optional[tuple]] = {}


def _advances(family: str, bold: bool) -> Optional[tuple]:
    key = f"{family}|{bold}"
    if key not in _ADVANCE_CACHE:
        val = None
        try:
            from fontTools.ttLib import TTFont

            from ..tools import fontfind

            m = fontfind.match(family, bold)
            if m is not None:
                f = TTFont(m.path, fontNumber=m.index, lazy=True)
                val = (f.getBestCmap(), f["hmtx"], f["head"].unitsPerEm)
        except Exception:
            val = None
        _ADVANCE_CACHE[key] = val
    return _ADVANCE_CACHE[key]


def text_width_pt(text: str, family: str, size_pt: float, bold: bool = False) -> Optional[float]:
    """用伺服器上的字型檔（mortise.tools.fontfind 找到的）量一串字的前進寬度（pt）。缺字以 0.6 em 計（中日韓字 1 em）；找不到字型回傳 None。"""
    adv = _advances(family, bold)
    if adv is None:
        return None
    cmap, hmtx, upm = adv
    w = 0.0
    for ch in text:
        g = cmap.get(ord(ch))
        if g is not None:
            w += hmtx[g][0] / upm
        else:
            w += 1.0 if ord(ch) >= 0x2E80 else 0.6
    return w * size_pt


def worst_case_width_pt(paragraph: StyledParagraph, written: list[str], wide_fallbacks: Iterable[str] = ("DejaVu Sans",)) -> float:
    """一段單行字在「寫入的字型」與「找不到字型時常見的寬字型」之下，最寬會有多寬（pt）。有強制換行（\n）時取最寬的一行。
    寫入的字型在伺服器上由 mortise.tools.fontfind 對到字寬相容的字型（例如 Arial → Liberation Sans）；
    寬字型代表使用者裝置上沒有這個字型、被換成別的字型的情況（DejaVu Sans 與 Verdana 同屬最寬的一類無襯線字）。"""
    best = 0.0
    for fam in list(dict.fromkeys(list(written) + list(wide_fallbacks))):
        lines = [0.0]
        for r in paragraph.runs:
            for k, piece in enumerate(r.text.split("\n")):
                if k:
                    lines.append(0.0)
                w = text_width_pt(piece, fam, r.size_pt.value, r.bold)
                lines[-1] += w if w is not None else 0.62 * r.size_pt.value * len(piece)
        best = max(best, max(lines))
    return best


def hard_breaks(sp: StyledParagraph, width_pt: float) -> Optional[StyledParagraph]:
    """靠右或置中的多行段落（例如右上角的地址、電話）：原圖的每一行通常是各自獨立的一行字，⑤合成一段後就沒有斷行位置。
    用量測時的字型（驗證字型）在原框寬裡逐字排一次，排出來的行數與原圖行數（source_line_count）相同，
    就把斷行處的空白換成強制換行（\n → w:br）：換了字型也照原圖斷行，不會把下一行的字拉上來或把長字（電子郵件）從中間折斷。
    行數對不上（推不出原圖的斷行）就回傳 None，照原樣輸出。"""
    if sp.source_line_count < 2 or sp.align not in (Align.RIGHT, Align.CENTER) or not sp.runs:
        return None
    pieces = []  # （文字段序號, 起點, 終點, 寬度, 是否為空白）
    for k, r in enumerate(sp.runs):
        for m in re.finditer(r"\s+|\S+", r.text):
            w = text_width_pt(m.group(0), r.font.verification_family, r.size_pt.value, r.bold)
            if w is None:
                return None
            pieces.append((k, m.start(), m.end(), w, m.group(0).isspace()))
    lines, cur, breaks = 1, 0.0, []
    for i, (k, a, b, w, space) in enumerate(pieces):
        if space:
            cur += w
            continue
        if cur > 0 and cur + w > width_pt + 0.5:
            j = i - 1
            if j < 0 or not pieces[j][4]:
                return None  # 斷在兩個非空白之間（同一個字跨文字段）：推不出來
            breaks.append(pieces[j])
            lines += 1
            cur = w
        else:
            cur += w
    if lines != sp.source_line_count:
        return None
    runs = [r.text for r in sp.runs]
    for k, a, b, w, _ in reversed(breaks):
        runs[k] = runs[k][:a] + "\n" + runs[k][b:]
    return sp.model_copy(update={"runs": [r.model_copy(update={"text": t}) for r, t in zip(sp.runs, runs)]})


def condense_spacing(sp: StyledParagraph, ctx: Ctx, latin: str, max_ratio: float) -> list[int]:
    """版面固定模式：寫入的字型比⑤量測時用的字型寬（例如 Inter Display 寫成 Arial，寬約 3%）時，
    每個字緊縮一點字距，讓整段的寬度回到量測時的寬度，換行位置才會和原圖一樣，框不會因為多一行而往下長、蓋到下一個框。
    兩個寬度都用伺服器上的字型檔量（寫入的字型由 mortise.tools.fontfind 對到字寬相容的字型，例如 Arial → Liberation Sans）。
    只緊縮、不放寬；每字最多緊縮字級的 max_ratio；中日韓文字不處理。回傳每個文字段的字距（twip，0 表示不改）。"""
    out = []
    for r in sp.runs:
        if r.font.east_asian or not r.text.strip():
            out.append(0)
            continue
        written = ctx.font_name(r.font, context_latin=latin, record=False)
        if written == r.font.verification_family:
            out.append(0)
            continue
        w_out = text_width_pt(r.text, written, r.size_pt.value, r.bold)
        w_ref = text_width_pt(r.text, r.font.verification_family, r.size_pt.value, r.bold)
        if w_out is None or w_ref is None or w_out <= w_ref:
            out.append(0)
            continue
        per_char = min((w_out - w_ref) / len(r.text), max_ratio * r.size_pt.value) * ctx.scale
        out.append(-int(round(per_char * 20)))
    return out


def _char_advances(text: str, family: str, size_pt: float, bold: bool) -> Optional[list[float]]:
    """每個字的前進寬度（pt）；找不到字型回傳 None。缺字的估計同 text_width_pt。"""
    adv = _advances(family, bold)
    if adv is None:
        return None
    cmap, hmtx, upm = adv
    out = []
    for ch in text:
        g = cmap.get(ord(ch))
        out.append(hmtx[g][0] / upm * size_pt if g is not None else (1.0 if ord(ch) >= 0x2E80 else 0.6) * size_pt)
    return out


def wrapped_line_count(chars: list[tuple[str, float, float]], width_pt: float, first_indent_pt: float = 0.0) -> int:
    """逐字（字, 前進寬度, 字距）模擬文字引擎的自動換行，回傳行數：只在空白處斷行（中日韓文字每個字前後都可以斷），
    行尾的空白不算寬度；一個字比整行還寬時才在字中間斷。強制換行（\n）另起一行。"""
    tokens: list[tuple[str, float]] = []
    word = 0.0
    for ch, w, sp in chars:
        if ch == "\n" or ch.isspace() or ord(ch) >= 0x2E80:
            if word:
                tokens.append(("w", word))
                word = 0.0
            tokens.append(("n" if ch == "\n" else ("s" if ch.isspace() else "w"), w + sp))
        else:
            word += w + sp
    if word:
        tokens.append(("w", word))
    lines, x, pend = 1, 0.0, 0.0
    avail = width_pt - max(0.0, first_indent_pt)
    for kind, w in tokens:
        if kind == "n":
            lines, x, pend, avail = lines + 1, 0.0, 0.0, width_pt
        elif kind == "s":
            if x > 0:
                pend += w
        else:
            if x > 0 and x + pend + w > avail:
                lines, x, avail = lines + 1, 0.0, width_pt
            if x == 0 and w > avail > 0:
                extra = int(w // avail)
                lines += extra
                x = w - extra * avail
            else:
                x += w if x == 0 else pend + w
            pend = 0.0
    return lines


def fit_source_lines(sp: StyledParagraph, width_pt: float, ctx: Ctx, latin: str, spacing: list[int], max_ratio: float,
                     margin_pt: float = 0.75) -> tuple[list[int], int, int]:
    """版面固定模式：用寫入的字型（伺服器上字寬相容的字型檔）模擬這段在框寬裡的自動換行；行數比原圖多（框會往下長、
    蓋到下面的框）時，每個字再多緊縮一點字距，直到行數回到原圖的行數，最多緊縮到字級的 max_ratio。
    例如⑤用的驗證字型本來就比原圖的字寬、量出來第二行就超過框寬的段落。中日韓文字只當成可以斷行的位置，不另外處理。
    spacing 是已經算好的字距（twip，condense_spacing 的結果）。回傳（新的字距, 寫入字型的行數, 調整後的行數）。
    找不到字型、或緊縮到上限仍多一行，就照原樣回傳（行數照實回報，呼叫端記錄）。"""
    runs = []
    for k, r in enumerate(sp.runs):
        fam = ctx.font_name(r.font, context_latin=latin, record=False) if not r.font.east_asian else (r.font.verification_family or r.font.family.value)
        adv = _char_advances(r.text, fam, r.size_pt.value * ctx.scale, r.bold)
        if adv is None:
            return spacing, 0, 0
        runs.append((r, adv, spacing[k] if k < len(spacing) else 0))
    target = max(1, sp.source_line_count)
    avail = width_pt - margin_pt
    first = sp.first_line_indent_pt * ctx.scale

    def count(extra_tw: int) -> int:
        chars = []
        for r, adv, base in runs:
            tw = min(base, -extra_tw) if not r.font.east_asian else base
            chars.extend((ch, a, tw / 20.0) for ch, a in zip(r.text, adv))
        return wrapped_line_count(chars, avail, first)

    before = count(0)
    if before <= target:
        return spacing, before, before
    size = max((r.size_pt.value for r in sp.runs), default=10.0) * ctx.scale
    limit = int(max_ratio * size * 20)
    for extra in range(1, limit + 1):
        n = count(extra)
        if n <= target:
            new = [min(base, -extra) if not r.font.east_asian else base for r, _, base in runs]
            return new, before, n
    return spacing, before, count(limit)


# ---------- 字型量度（基線補償、估計行高用） ----------

_LINE_HEIGHT_CACHE: dict[str, Optional[float]] = {}


def font_line_height_em(family: str) -> Optional[float]:
    """文字引擎（LibreOffice）單倍行距的行高，以 em 為單位：hhea 的上緣 − 下緣 + 行距間隙（例如 Liberation Sans 約 1.15）。
    用伺服器上實際的字型檔（mortise.tools.fontfind 找到的；寫入的名稱對到字寬相容的字型）；找不到就回傳 None。"""
    if family in _LINE_HEIGHT_CACHE:
        return _LINE_HEIGHT_CACHE[family]
    val = None
    try:
        from fontTools.ttLib import TTFont

        from ..tools import fontfind

        m = fontfind.match(family)
        if m is not None:
            f = TTFont(m.path, fontNumber=m.index, lazy=True)
            hhea, upm = f["hhea"], f["head"].unitsPerEm
            val = (hhea.ascent - hhea.descent + max(0, hhea.lineGap)) / upm
    except Exception:
        val = None
    _LINE_HEIGHT_CACHE[family] = val
    return val


# ---------- 字型量度（基線補償用） ----------

_ASCENT_CACHE: dict[str, Optional[float]] = {}


def font_first_baseline_em(family: str) -> Optional[float]:
    """文字引擎（LibreOffice）放第一行基線的位置，以 em 為單位：hhea 上緣加行距間隙。
    用伺服器上實際的字型檔（mortise.tools.fontfind 找檔、fontTools 讀量度）；找不到就回傳 None。"""
    if family in _ASCENT_CACHE:
        return _ASCENT_CACHE[family]
    val = None
    try:
        from fontTools.ttLib import TTFont

        from ..tools import fontfind

        m = fontfind.match(family)
        if m is not None:
            f = TTFont(m.path, fontNumber=m.index, lazy=True)
            hhea, upm = f["hhea"], f["head"].unitsPerEm
            val = (hhea.ascent + max(0, hhea.lineGap)) / upm
    except Exception:
        val = None
    _ASCENT_CACHE[family] = val
    return val
