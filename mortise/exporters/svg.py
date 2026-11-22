"""整頁 SVG 輸出器（OutputFormat.SVG）與共用的 SVG 寫入函式（svg_assets 與⑦的 SVG 驗證也用這裡的函式）。

單頁輸出一個 .svg；多頁（或圖片設定成外部檔）打包成 .zip，裡面是 page-001.svg、page-002.svg…。
整頁 SVG 同時是前端的預覽與修正介面：每個元素包成一個群組，id 就是 Mortise 的元素編號，另帶 data-kind。

元件與授權：fontTools（MIT，讀字型量度、把字形轉成路徑）、uharfbuzz（Apache-2.0，排字，量含字距微調的行寬）、
lxml（BSD-3-Clause，寫出後重新解析做自檢）。排版（斷行、行距、對齊、每行基線）沿用 ai_pdf 的 _Fonts 與 _Layout，
與 7A 中性渲染是同一套算法；這裡只換掉字型的載入方式（直接讀字型檔的 hmtx，CFF 外框也收，因為 SVG 不內嵌字型）。

寫法與理由（依 docs/architecture/research/svg.md；這台機器沒有 Illustrator、Inkscape、Figma、PowerPoint，
凡是「某軟體打開後會怎樣」都沒有實測，只有 resvg 與 Chromium 是實測）：

  根元素
    - viewBox 以 pt 為使用者單位（與成品文件相同）。width、height 預設帶 pt 單位（svg_size_unit = "pt"）：
      瀏覽器、Inkscape、PowerPoint 依規格把 1 pt 換成 96/72 px，實體尺寸正確；Illustrator 據論壇說法把沒有單位的數字當成 pt，
      這裡數字本來就等於 viewBox，所以它不論認不認單位，尺寸都一樣（推論，未實測）。
      研究筆記暫定的預設是不帶單位（Illustrator 正確、瀏覽器與 Inkscape 會是 75% 大小），可用 svg_size_unit = "none" 換回去。
    - 全檔只用展示屬性，不寫 style 元素、style 屬性、class；不用 use、symbol、巢狀 svg、mask；不寫任何指令碼與外部連結。

  群組
    - 依 z 順序由下到上，每個元素一個最上層群組：id 是元素編號（只留英數與連字號，必要時加字首與序號保證唯一；
      有改動時原編號另寫在 data-element-id），data-kind 是 background、text_box、image、shape、vector、table 之一。
    - 最上層群組另帶 inkscape:label 與 inkscape:groupmode="layer"（Inkscape 裡點一下就選到裡面的物件，不必先進入群組）。
      Illustrator 不會把群組變成圖層，無解。svg_layers = "none" 可以不寫。
    - 母片：成品文件的每一頁都還留著母片取代的那些元素，SVG 沒有母片，所以每頁照畫自己的元素、不另外畫母片
      （不會重複），這些群組帶 data-master 標明屬於哪個母片。
    - 頁面底色：最底層的 data-kind="background" 群組裡畫一個與頁面同大小的 rect。⑤的頁面底色是白色時 background_color 是 None，
      這時也畫白色（svg_page_background = "auto"，預設）：SVG 不畫底色就是透明，深色模式的看圖軟體會變成黑底、字看不見。
      svg_page_background = "none" 時只有⑤指定了底色才畫。單獨的向量圖（svg_assets）一律透明，不畫底色。

  文字（svg_text = "text"，預設）
    - 一行一個 text，x、y 是這一行的起點與基線，由自家排版算定；不寫 text-anchor、不依賴任何軟體的自動換行。
      理由：SVG 1.1 沒有自動換行；Illustrator 不使用 text-anchor；Inkscape 會重排帶 sodipodi:role 的多行文字。
    - 同一行內樣式不同的片段寫成不帶座標的 tspan（行內文字照常接續，改字時後面的字會跟著移動）；
      各片段相同的屬性提到 text 上。Illustrator 會把 tspan 拆成獨立文字，這是已知的失真。
    - 每個 text 寫 xml:space="preserve"（否則 tspan 邊界的空格在各軟體收合結果不同），所以 text 裡面不排版、不換行。
    - font-family 先寫推論的字型名稱，再寫伺服器上實際用來量行寬的字型（兩者不同時），最後是通用字型；
      粗體寫 font-weight 數字、斜體寫 font-style，字級與顏色也是展示屬性，都不帶單位。
      「Inter SemiBold」這類帶字重的舊式家族名，另外補上去掉字重的家族名與對應的 font-weight（resvg 只認後者）。
    - 置中與靠右的行：各軟體畫字時都會套用字距微調（kerning）與連字，行寬比逐字相加的窄。
      所以行的起點用 harfbuzz 量到的行寬修正，畫出來才會真的置中、靠右（svg_kerning_aware = false 可關閉）。
    - 左右對齊：有空白的行寫 word-spacing，沒有空白的行（中日韓文字）寫 letter-spacing；部分軟體會忽略 letter-spacing。
    - 旋轉的文字框：群組上寫 transform="rotate(角度 中心x 中心y)"，裡面的座標是旋轉前的。

  文字轉外框（svg_text = "outline"）
    - 用 uharfbuzz 排字、fontTools 取字形，寫成 path（一個樣式片段一條），原文字留在 data-text。
      外觀不再依賴對方電腦的字型，但文字不能再編輯（降級紀錄會寫明）。沒有粗體檔時以同色描邊模擬、沒有斜體檔時把字形傾斜。

  圖片、形狀、向量圖、表格
    - 圖片預設以 data URI 內嵌，屬性只寫 xlink:href；svg_images = "external" 時圖檔另存在 zip 的 images/ 下。
    - 形狀：矩形、圓角矩形、橢圓、直線寫成 rect、ellipse、line，自訂路徑寫成 path（絕對座標）。
    - 向量圖：畫到帶 vector_id 的圖片時改畫向量圖、跳過那張圖片（一律由圖片找向量圖）。一個群組，裡面由下到上逐條路徑；
      有 primitive 的寫成 rect、circle、ellipse、line、polygon（旋轉用 rotate）；漸層放在檔頭的 defs，
      gradientUnits="userSpaceOnUse"；fill-rule、stroke-linecap、stroke-linejoin 不是預設值時照實寫；
      尖角接合另寫 stroke-miterlimit="10"（PDF 的預設，SVG 的預設是 4）。群組帶 data-origin（pdf_paths 或 traced）。
    - 表格：儲存格底色是 rect、框線是 line（相鄰儲存格共用的框線只寫一次），每格的文字包在 data-kind="cell" 的子群組。

降階對照表（成品文件的概念 → SVG 的寫法 → 是否有損）：
  頁面尺寸（每頁可不同）     → 每頁一個檔，各自的 viewBox                       → 無損
  整頁底色、底圖             → 最底層群組裡的 rect、image                        → 無損（底圖裡的內容本來就不可編輯）
  文字框、段落、文字流       → 沒有對應；一行一個 text                           → 有損（以行為單位編輯，不會自動換行）
  行距、段距、首行縮排、對齊 → 只體現在每行的座標                                → 位置無損；屬性本身捨棄
  字型                       → 不內嵌，只寫名稱與後備                            → 對方沒有該字型時會被換掉，有損
  粗體、斜體                 → font-weight、font-style                           → 無損（沒有對應字型檔時各軟體多半不模擬）
  左右對齊                   → word-spacing 或 letter-spacing                    → 部分軟體忽略 letter-spacing，有損
  圖片                       → image（原檔內嵌，不重新壓縮）                     → 無損
  形狀                       → 基本元素或 path                                   → 無損
  向量圖                     → 群組加逐條路徑、基本元素、漸層                    → 無損（PDF 的零寬細線改成很細的線）
  表格                       → 底色、框線、逐格文字                              → 有損（沒有表格物件）
  數字格式、公式             → 只留顯示文字                                      → 有損
  母片                       → 每頁各畫一次，以 data-master 標示                 → 有損（改一處不會連動）
  樣式表、主題色             → 沒有                                              → 捨棄
  圖層前後（z）              → 文件順序                                          → 無損
  閱讀順序                   → 沒有                                              → 與 z 順序不同時有損
"""

from __future__ import annotations

import base64
import io
import math
import re
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

from ..artifacts import (
    Align,
    Degradation,
    OutputFile,
    OutputFormat,
    StyledDoc,
    StyledImage,
    StyledPage,
    StyledParagraph,
    StyledShape,
    StyledTable,
    StyledTextBox,
    StyledVector,
    StyledVectorPath,
)
from ..artifacts.common import BlobRef, Color
from ..artifacts.element_list import Gradient
from ..artifacts.styled_doc import Box, vector_index
from ..config import Config
from ..pipeline.store import Store
from . import ai_pdf
from .ai_pdf import _Fonts, _Layout, _Match

# 這個輸出器自己的預設值。設定檔 [export] 裡以 svg_ 開頭的項目會蓋過這裡（例如 svg_text = "outline"）；
# ai_pdf 的排版參數也可以加上 svg_ 前綴只對本輸出器生效（例如 svg_cjk_fallback）。
DEFAULT_PARAMS: dict[str, Any] = {
    "text": "text",  # "text"：可編輯的文字；"outline"：文字轉成外框路徑（不可再編輯）
    "size_unit": "pt",  # 根元素 width、height 的單位："pt"（帶單位）或 "none"（不帶單位，數字等於 viewBox）
    "precision": 2,  # 座標與長度的小數位數
    "images": "embed",  # "embed"：圖片以 data URI 內嵌；"external"：圖檔另存在 zip 的 images/ 下（輸出一律是 zip）
    "layers": "element",  # "element"：每個元素的群組標成 Inkscape 圖層並帶名稱；"none"：不寫 inkscape: 屬性
    "primitives": True,  # 向量圖的路徑有 primitive 時寫成基本元素
    "vectors": True,  # 圖片有對應的向量圖時改畫向量圖（關掉就照常畫那張圖片）
    "kerning_aware": True,  # 置中、靠右、左右對齊的行，用含字距微調的行寬修正起點與間距
    "tspan_y": False,  # 行內片段的 tspan 是否另寫 y（讓 resvg 這類渲染器逐片段選字型；編輯軟體的行為未實測）
    "hairline_pt": 0.25,  # 來源是 PDF 零寬細線的筆畫，在 SVG 裡寫成多寬（pt）
    "outline_slant_deg": 12.0,  # 文字轉外框而字型沒有斜體檔時，字形傾斜的角度
    "miter_limit": 10.0,  # 尖角接合的 stroke-miterlimit（PDF 的預設值）
    "canva_limit_bytes": 3_000_000,  # 單一 SVG 超過這個大小時在自檢紀錄提醒（Canva 的上傳上限，搜尋摘要，未實測）
    "page_background": "auto",  # "auto"：整頁最底層一定畫頁面底色（⑤沒有底色就是白色）；"none"：只有⑤指定了底色才畫，其餘保持透明
}

MIME = "image/svg+xml"
SVG_NS = "http://www.w3.org/2000/svg"
XLINK_NS = "http://www.w3.org/1999/xlink"
INKSCAPE_NS = "http://www.inkscape.org/namespaces/inkscape"
XML_NS = "http://www.w3.org/XML/1998/namespace"
_GENERIC = ("serif", "sans-serif", "monospace")
_EMBEDDABLE = {"image/png": "png", "image/jpeg": "jpg"}
_KIND_LABEL = {"background": "背景", "text_box": "文字", "image": "圖片", "shape": "形狀", "vector": "向量圖", "table": "表格"}
_VECTOR_LABEL = {"logo": "標誌", "icon": "圖示", "chart": "圖表", "illustration": "插畫"}
_SHAPE_LABEL = {"rect": "矩形", "rounded_rect": "圓角矩形", "ellipse": "橢圓", "line": "直線", "path": "自訂路徑"}
# 寫出後允許出現的元素（白名單）。其他一律視為自檢失敗：script、style、foreignObject、a、動畫、use、symbol、mask 都不在裡面。
ALLOWED_ELEMENTS = frozenset({
    "svg", "title", "defs", "linearGradient", "radialGradient", "stop", "g", "rect", "circle", "ellipse", "line",
    "polygon", "polyline", "path", "text", "tspan", "image",
})


def params_for(cfg: Optional[Config]) -> tuple[dict, dict]:
    """回傳（本輸出器的參數、排版參數）。排版參數與 ai_pdf 相同，7A 中性渲染的斷行與基線才會與這裡一致。"""
    lp = ai_pdf.params_from(cfg, ai_pdf.DEFAULT_PARAMS, "ai_pdf")
    p = dict(DEFAULT_PARAMS)
    if cfg is not None:
        for key, value in (cfg.section("export") or {}).items():
            if not key.startswith("svg_"):
                continue
            name = key[4:]
            if name in p:
                p[name] = value
            elif name in lp:
                lp[name] = value
    lp["basefont_name"] = "embedded"
    if p["text"] not in ("text", "outline"):
        raise ValueError(f"svg_text 只能是 \"text\" 或 \"outline\"，現在是 {p['text']!r}。")
    if p["size_unit"] not in ("pt", "none"):
        raise ValueError(f"svg_size_unit 只能是 \"pt\" 或 \"none\"，現在是 {p['size_unit']!r}。")
    if p["images"] not in ("embed", "external"):
        raise ValueError(f"svg_images 只能是 \"embed\" 或 \"external\"，現在是 {p['images']!r}。")
    if p["page_background"] not in ("auto", "none"):
        raise ValueError(f"svg_page_background 只能是 \"auto\" 或 \"none\"，現在是 {p['page_background']!r}。")
    p["precision"] = max(0, min(6, int(p["precision"])))
    return p, lp


# ================================================================ 數字、跳脫、編號

_XML_BAD = re.compile("[^\x09\x0a\x0d\x20-퟿-�\U00010000-\U0010ffff]")


def num(v: float, digits: int = 2) -> str:
    """數字 → 字串：四捨五入到指定位數，去掉多餘的 0，不會寫出 -0。"""
    s = f"{float(v):.{digits}f}"
    if "." in s:
        s = s.rstrip("0").rstrip(".")
    return "0" if s in ("", "-0") else s


def clean(text: str) -> str:
    """濾掉 XML 不允許的控制字元。"""
    return _XML_BAD.sub("", text)


def esc_text(text: str) -> str:
    return clean(text).replace("\r", "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def esc_attr(text: str) -> str:
    out = clean(text).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;")
    return out.replace("\t", "&#9;").replace("\n", "&#10;").replace("\r", "&#13;")


def attrs_str(attrs: list[tuple[str, Optional[str]]]) -> str:
    return "".join(f' {k}="{esc_attr(v)}"' for k, v in attrs if v is not None)


def hex_color(c: Color) -> str:
    return f"#{c.r:02x}{c.g:02x}{c.b:02x}"


class _Ids:
    """把元素編號變成合法且唯一的 id：只留英數與連字號（不用底線，Illustrator 匯出時會把它轉成 _x5F_），
    開頭必須是英文字母，重複時加序號。"""

    def __init__(self) -> None:
        self.used: set[str] = set()

    def make(self, raw: str) -> str:
        base = re.sub(r"[^A-Za-z0-9]+", "-", raw or "").strip("-")
        if not base or not base[0].isalpha():
            base = ("m-" + base).rstrip("-")
        out, k = base, 1
        while out in self.used:
            k += 1
            out = f"{base}-{k}"
        self.used.add(out)
        return out


class _Writer:
    """有縮排的 XML 寫入器。text 元素整個寫在同一行（xml:space="preserve" 之下不能有排版用的空白）。"""

    def __init__(self, depth: int = 1) -> None:
        self.lines: list[str] = []
        self.depth = depth

    def raw(self, line: str) -> None:
        self.lines.append("  " * self.depth + line)

    def start(self, tag: str, attrs: list[tuple[str, Optional[str]]]) -> None:
        self.raw(f"<{tag}{attrs_str(attrs)}>")
        self.depth += 1

    def end(self, tag: str) -> None:
        self.depth -= 1
        self.raw(f"</{tag}>")

    def empty(self, tag: str, attrs: list[tuple[str, Optional[str]]]) -> None:
        self.raw(f"<{tag}{attrs_str(attrs)}/>")


# ================================================================ 字型


class quiet_fonttools:
    """讀字型檔時暫時不顯示 fontTools 的警告（有些字型的建立時間不合規格，每讀一次就警告一次，與輸出無關）。"""

    def __enter__(self):
        import logging

        self.logger = logging.getLogger("fontTools")
        self.level = self.logger.level
        self.logger.setLevel(logging.ERROR)
        return self

    def __exit__(self, *exc) -> None:
        self.logger.setLevel(self.level)


@dataclass
class _SvgFace(ai_pdf._Face):
    """給 _Layout 用的字型（欄位同 ai_pdf._Face）。字寬直接取自字型檔的 hmtx，不經過 ReportLab，CFF 外框也能用。"""

    index: int = 0  # 在字型集合檔中的序號
    upm: int = 1000
    weight: int = 400  # OS/2 usWeightClass
    names: frozenset = frozenset()  # 所有語系的家族名稱（name 表的 1 與 16）
    generic: str = "sans-serif"  # 最接近的通用字型
    outlines: bool = True  # 有沒有向量外框（點陣字、彩色字沒有，轉不了外框）
    advance: dict = field(default_factory=dict)  # 碼位 → 1 pt 字級下的前進寬度
    space: Optional[float] = None
    notdef: float = 0.5
    _hb: Any = None
    _ft: Any = None
    _glyphset: Any = None
    _glyphs: dict = field(default_factory=dict)

    def width(self, ch: str) -> float:
        w = self.widths.get(ch)
        if w is None:
            w = self.advance.get(ord(ch))
            if w is None:
                w = self.space if (ch.isspace() and self.space is not None) else self.notdef
            self.widths[ch] = w
        return w

    def shape(self, text: str, ligatures: bool = True) -> list[tuple[int, int, float, float, float]]:
        """用 harfbuzz 排字（含字距微調；各軟體畫 SVG 文字時也是這樣）。
        回傳每個字形的（字形編號、對應到原文第幾個字、前進寬度、x 位移、y 位移），長度單位是 1 pt 字級。"""
        import uharfbuzz as hb

        if self._hb is None:
            self._hb = hb.Font(hb.Face(hb.Blob.from_file_path(self.path), self.index))
        buf = hb.Buffer()
        buf.add_str(text)
        buf.guess_segment_properties()
        hb.shape(self._hb, buf, None if ligatures else {"liga": False, "clig": False})
        u = float(self.upm)
        return [(info.codepoint, info.cluster, pos.x_advance / u, pos.x_offset / u, pos.y_offset / u) for info, pos in zip(buf.glyph_infos, buf.glyph_positions)]

    def shaped_width(self, text: str) -> float:
        """一段字在 1 pt 字級下的寬度（含字距微調與連字）。排字失敗時退回逐字相加。"""
        try:
            return sum(g[2] for g in self.shape(text))
        except Exception:
            return sum(self.width(ch) for ch in text)

    def glyph(self, gid: int):
        """一個字形的外框（fontTools 的 RecordingPen，字型單位、y 向上）。"""
        rec = self._glyphs.get(gid)
        if rec is None:
            from fontTools.pens.recordingPen import DecomposingRecordingPen
            from fontTools.ttLib import TTFont

            with quiet_fonttools():
                if self._ft is None:
                    kw = {"fontNumber": self.index} if self.path.lower().endswith((".ttc", ".otc")) else {}
                    self._ft = TTFont(self.path, lazy=True, **kw)
                    self._glyphset = self._ft.getGlyphSet()
                rec = DecomposingRecordingPen(self._glyphset)
                self._glyphset[self._ft.getGlyphName(gid)].draw(rec)
            self._glyphs[gid] = rec
        return rec


class _Loader:
    """字型檔 → _SvgFace。介面同 ai_pdf._load_face，交給 _Fonts 使用。"""

    _cache: dict[tuple, tuple[Optional[_SvgFace], str]] = {}

    def __call__(self, m: _Match, metric: str, rename: Optional[str] = None) -> tuple[Optional[_SvgFace], str]:
        ck = (m.path, m.index, metric)
        if ck not in self._cache:
            try:
                with quiet_fonttools():
                    self._cache[ck] = self._load(m, metric)
            except Exception as e:  # 壞檔、讀不到
                self._cache[ck] = (None, f"error: {e}")
        return self._cache[ck]

    @staticmethod
    def _load(m: _Match, metric: str) -> tuple[Optional[_SvgFace], str]:
        import hashlib

        from fontTools.ttLib import TTFont

        ft = TTFont(m.path, fontNumber=m.index, lazy=True) if m.path.lower().endswith((".ttc", ".otc")) else TTFont(m.path, lazy=True)
        try:
            head, hhea = ft["head"], ft["hhea"]
            os2 = ft["OS/2"] if "OS/2" in ft else None
            post = ft["post"] if "post" in ft else None
            upm = head.unitsPerEm
            metrics = {"hhea": (hhea.ascent / upm, hhea.descent / upm)}
            metrics["typo"] = (os2.sTypoAscender / upm, os2.sTypoDescender / upm) if os2 is not None else metrics["hhea"]
            metrics["win"] = (os2.usWinAscent / upm, -os2.usWinDescent / upm) if os2 is not None else metrics["hhea"]
            asc, desc = metrics.get(metric, metrics["hhea"])
            table = ft["name"]
            family = table.getDebugName(16) or table.getDebugName(1) or Path(m.path).stem
            names = set()
            for rec in table.names:
                if rec.nameID in (1, 16):
                    try:
                        names.add(rec.toUnicode())
                    except Exception:
                        pass
            names.add(family)
            weight = int(os2.usWeightClass) if os2 is not None else (700 if head.macStyle & 1 else 400)
            italic = bool(head.macStyle & 2) or bool(os2 is not None and os2.fsSelection & 1) or bool(post is not None and post.italicAngle)
            hmtx = ft["hmtx"].metrics
            best = ft.getBestCmap() or {}
            advance = {cp: hmtx[name][0] / upm for cp, name in best.items() if name in hmtx}
            order = ft.getGlyphOrder()
            generic = "sans-serif"
            if post is not None and post.isFixedPitch:
                generic = "monospace"
            elif os2 is not None and os2.panose.bFamilyType == 2 and 2 <= os2.panose.bSerifStyle <= 10:
                generic = "serif"
            face = _SvgFace(
                key="MS" + hashlib.sha1(f"{m.path}|{m.index}".encode()).hexdigest()[:12], family=family, path=m.path, cmap=frozenset(advance),
                ascent=round(asc * 1000.0) / 1000.0, descent=round(desc * 1000.0) / 1000.0, bold=weight >= 600, italic=italic, metrics=metrics,
                index=m.index, upm=upm, weight=weight, names=frozenset(names), generic=generic,
                outlines="glyf" in ft or "CFF " in ft or "CFF2" in ft, advance=advance, space=advance.get(0x20),
                notdef=hmtx[order[0]][0] / upm if order and order[0] in hmtx else 0.5,
            )
            return face, ""
        finally:
            ft.close()


_WEIGHT_WORD = re.compile(
    r"^(?P<base>.*\S)[ \-](?P<word>Thin|Hairline|Extra ?Light|Ultra ?Light|Light|Semi ?Light|Book|Medium|Semi ?Bold|Demi ?Bold|Demi|Extra ?Bold|Ultra ?Bold|Black|Heavy)$",
    re.IGNORECASE,
)
_WEIGHT_OF = {
    "thin": 100, "hairline": 100, "extralight": 200, "ultralight": 200, "light": 300, "semilight": 350, "book": 400, "medium": 500,
    "semibold": 600, "demibold": 600, "demi": 600, "extrabold": 800, "ultrabold": 800, "black": 900, "heavy": 900,
}
_MONO_WORDS = ("mono", "courier", "consolas", "menlo", "等寬", "等宽")
_SERIF_WORDS = ("serif", "times", "georgia", "garamond", "cambria", "palatino", "baskerville", "bodoni", "didot", "charter", "caladea",
                "ming", "song", "mincho", "simsun", "kaiti", "明", "宋", "楷")
_SANS_WORDS = ("sans", "gothic", "hei", "黑", "arial", "helvetica", "calibri", "carlito", "verdana", "tahoma", "segoe", "inter", "roboto")


def split_weight(name: str) -> tuple[Optional[str], Optional[int]]:
    """「Inter SemiBold」這類帶字重的舊式家族名 →（去掉字重的家族名, 字重）。不是這種名稱就回傳（None, None）。"""
    m = _WEIGHT_WORD.match(name.strip())
    if not m:
        return None, None
    return m.group("base").strip(), _WEIGHT_OF[m.group("word").replace(" ", "").casefold()]


def generic_family(name: str, face: Optional[_SvgFace] = None) -> str:
    """字型名稱（加上字型檔的分類）→ 通用字型（serif、sans-serif、monospace）。"""
    low = name.casefold()
    if any(w in low for w in _MONO_WORDS):
        return "monospace"
    if "sans" in low:
        return "sans-serif"
    if any(w in low for w in _SERIF_WORDS):
        return "serif"
    if any(w in low for w in _SANS_WORDS):
        return "sans-serif"
    return face.generic if face is not None else "sans-serif"


def family_attr(names: list[str]) -> str:
    """font-family 的值：通用字型與單純的英文名稱不加引號，其餘用單引號包起來。"""
    parts = []
    for n in names:
        if n in _GENERIC or re.fullmatch(r"[A-Za-z][A-Za-z0-9\-]*", n):
            parts.append(n)
        else:
            parts.append("'" + n.replace("\\", "\\\\").replace("'", "\\'") + "'")
    return ", ".join(parts)


def parse_family(value: str) -> list[str]:
    """family_attr 的反向：font-family 的值 → 名稱清單。"""
    out, cur, quote, i = [], "", None, 0
    while i < len(value):
        ch = value[i]
        if quote:
            if ch == "\\" and i + 1 < len(value):
                cur += value[i + 1]
                i += 1
            elif ch == quote:
                quote = None
            else:
                cur += ch
        elif ch in "'\"":
            quote = ch
        elif ch == ",":
            out.append(cur.strip())
            cur = ""
        else:
            cur += ch
        i += 1
    out.append(cur.strip())
    return [n for n in out if n]


# ================================================================ 排好的文字行


@dataclass
class Span:
    """一行裡樣式相同的一段字。"""

    text: str
    face: _SvgFace  # 伺服器上實際用來量行寬的字型
    families: tuple[str, ...]  # 寫進 font-family 的清單（最後一個是通用字型）
    size: float
    weight: int
    italic: bool
    color: Color
    faux_bold: bool  # 要粗體，但字型檔不是粗體
    faux_italic: bool  # 要斜體，但字型檔不是斜體

    def key(self) -> tuple:
        return (self.face.key, self.families, round(self.size, 4), self.weight, self.italic, self.color.r, self.color.g, self.color.b, round(self.color.a, 4))


@dataclass
class Line:
    """排好的一行。x、y 是自家排版算出的行起點與基線（頁面座標 pt，原點左上；所屬的框有旋轉時是旋轉前的座標）。"""

    element_id: str
    paragraph_id: str
    align: Align
    spans: list[Span]
    x: float
    y: float
    width: float  # 自家排版的行寬（逐字前進寬度相加，不含字距微調）
    shaped_width: float  # harfbuzz 量到的行寬（含字距微調與連字）
    char_space: float = 0.0  # 左右對齊：整行一致的字距
    word_space: float = 0.0  # 左右對齊：整行一致的詞距
    nspaces: int = 0
    nchars: int = 0
    rotation: float = 0.0  # 所屬的框的旋轉（順時針，度），以（cx, cy）為中心
    cx: float = 0.0
    cy: float = 0.0
    cell: Optional[tuple[int, int]] = None  # 表格儲存格的（列, 欄）；文字框為 None

    @property
    def text(self) -> str:
        return "".join(s.text for s in self.spans)

    def placed(self, kerning_aware: bool = True) -> tuple[float, float, float]:
        """實際寫進 SVG 的（行起點 x、letter-spacing、word-spacing）。
        畫 SVG 文字的軟體都會套用字距微調，行寬會比自家排版的窄一點：置中的行往右補一半、靠右的行補全部，
        左右對齊的行把差額攤進間距，畫出來的行才會落在自家排版要的位置。"""
        x, cs, ws = self.x, self.char_space, self.word_space
        if kerning_aware:
            d = self.width - self.shaped_width
            if self.align == Align.CENTER:
                x += d / 2
            elif self.align == Align.RIGHT:
                x += d
            elif ws and self.nspaces:
                ws += d / self.nspaces
            elif cs and self.nchars > 1:
                cs += d / (self.nchars - 1)
        return x, cs, ws

    def anchor(self) -> tuple[float, float]:
        """這一行的對齊基準點（頁面座標，已套用旋轉）：靠左與左右對齊是行的起點，置中是行的中點，靠右是行的終點。"""
        ax = self.x + (self.width / 2 if self.align == Align.CENTER else self.width if self.align == Align.RIGHT else 0.0)
        return rotate_point(ax, self.y, self.rotation, self.cx, self.cy)


def rotate_point(x: float, y: float, deg: float, cx: float, cy: float) -> tuple[float, float]:
    if not deg:
        return x, y
    th = math.radians(deg)
    c, s = math.cos(th), math.sin(th)
    dx, dy = x - cx, y - cy
    return cx + c * dx - s * dy, cy + s * dx + c * dy


class Typesetter:
    """把文字框與表格儲存格排成行。斷行、行距、對齊、基線都由 ai_pdf 的 _Layout 算（與 7A 中性渲染同一套），
    這裡把結果換成頁面座標，並決定每段字要寫的 font-family。⑦的 SVG 驗證也用它算「應該在哪裡」。"""

    def __init__(self, cfg: Optional[Config], degradations: list[Degradation], params: Optional[tuple[dict, dict]] = None):
        self.p, self.lp = params or params_for(cfg)
        self.deg = degradations
        self.fonts = _Fonts(self.lp, [], loader=_Loader())  # 字型的降級紀錄由這裡自己寫（ai_pdf 的說法是「內嵌」，SVG 不內嵌）
        slack = float(cfg.section("settings").get("box_width_slack_pt", 0.0)) if (cfg is not None and self.lp["wrap_slack_from_settings"]) else 0.0
        self.layout = _Layout(self.fonts, self.lp, self.deg, slack)
        self.outline = self.p["text"] == "outline"
        self._noted: set[tuple] = set()

    def note(self, key: tuple, element_id: str, concept: str, action: str, lossy: bool = True) -> None:
        if key not in self._noted:
            self._noted.add(key)
            self.deg.append(Degradation(element_id=element_id, concept=concept, action=action, lossy=lossy))

    # ---------- 字型名稱
    def _span(self, sg, element_id: str) -> Span:
        run = sg.run
        want = clean(run.font.family.value).strip()
        primary, _, _ = self.fonts.primary(run.font, run.bold, run.italic, element_id)
        face: _SvgFace = sg.face
        weight = 700 if run.bold else 400
        if face is primary:
            names = [want] if want else []
            base, w = split_weight(want)
            if base:
                names.append(base)
                weight = max(weight, w) if run.bold else w
            if not (set(names) & set(face.names)):
                names.append(face.family)
                self.note(("sub", want, face.family), element_id, "字型",
                          f"伺服器沒有推論的字型「{want}」，行寬與置中、靠右的位置依「{face.family}」量測；SVG 的 font-family 仍把「{want}」寫在最前面，「{face.family}」列為後備")
            names.append(generic_family(want or face.family, face))
        else:
            names = [face.family, generic_family(face.family, face)]
            sample = next((ch for ch in sg.text if not ch.isspace()), sg.text[:1])
            self.note(("glyph", primary.family, face.family), element_id, "缺字",
                      f"「{primary.family}」沒有部分字形（例如「{sample}」），這些字另外寫成一個片段，改用「{face.family}」")
        faux_bold = bool(run.bold and not face.bold)
        faux_italic = bool(run.italic and not face.italic)
        if faux_bold:
            how = "以同色描邊模擬" if self.outline else "SVG 仍寫 font-weight，對方電腦有粗體檔就會用到；沒有時多數軟體不會模擬粗體"
            self.note(("bold", face.family), element_id, "粗體", f"伺服器上的「{face.family}」沒有粗體字型檔：{how}")
        if faux_italic:
            how = f"把字形傾斜 {num(self.p['outline_slant_deg'], 1)} 度模擬" if self.outline else "SVG 仍寫 font-style，對方電腦有斜體檔就會用到；沒有時由各軟體決定是否模擬"
            self.note(("italic", face.family), element_id, "斜體", f"伺服器上的「{face.family}」沒有斜體字型檔：{how}")
        seen: list[str] = []
        for n in names:
            if n and n not in seen:
                seen.append(n)
        return Span(text=sg.text, face=face, families=tuple(seen), size=float(sg.size), weight=weight, italic=bool(run.italic), color=run.color,
                    faux_bold=faux_bold, faux_italic=faux_italic)

    def _convert(self, raw_lines, paragraphs: list[StyledParagraph], element_id: str, ox: float, oy: float, rot: tuple[float, float, float],
                 cell: Optional[tuple[int, int]] = None) -> list[Line]:
        align_of = {sp.id: sp.align for sp in paragraphs}
        out: list[Line] = []
        for ln in raw_lines:
            if not ln.segs:
                continue
            spans: list[Span] = []
            for sg in ln.segs:
                sp = self._span(sg, element_id)
                if spans and spans[-1].key() == sp.key():
                    spans[-1].text += sp.text
                else:
                    spans.append(sp)
            shaped = sum(sp.face.shaped_width(sp.text) * sp.size for sp in spans)
            out.append(Line(
                element_id=element_id, paragraph_id=ln.paragraph, align=align_of.get(ln.paragraph, Align.LEFT), spans=spans,
                x=ox + ln.x, y=oy + ln.baseline, width=ln.width, shaped_width=shaped, char_space=ln.char_space, word_space=ln.word_space,
                nspaces=ln.nspaces, nchars=ln.nchars, rotation=rot[0], cx=rot[1], cy=rot[2], cell=cell,
            ))
        return out

    # ---------- 文字框與表格
    def text_box(self, tb: StyledTextBox) -> list[Line]:
        b = tb.box
        raw = self.layout.block(tb.paragraphs, b.w, tb.wrap, tb.id, self.layout.slack if tb.wrap else 0.0, b.h, getattr(tb, "first_baseline_pt", None))
        lines = self._convert(raw, tb.paragraphs, tb.id, b.x, b.y, (b.rotation, b.x + b.w / 2, b.y + b.h / 2))
        if len(lines) > 1:
            self.note(("box", tb.id), tb.id, "段落與文字框", f"SVG 沒有段落與文字框，寫成 {len(lines)} 個逐行的{'外框路徑' if self.outline else ' text'}；行距、段距、對齊只體現在每行的座標上，改字後不會自動換行")
        if raw and raw[-1].baseline - raw[-1].descent > b.h + 0.5 * raw[-1].size:
            self.note(("overflow", tb.id), tb.id, "文字框高度", "排出的文字超出文字框下緣（SVG 沒有文字框，照樣畫出）", lossy=False)
        return lines

    def table_cells(self, tb: StyledTable) -> tuple[list[tuple], list[tuple]]:
        """回傳（每個儲存格的（儲存格, x, y, 寬, 高），每個有文字的儲存格的（儲存格, 行））。座標是頁面座標（旋轉前）。"""
        b = tb.box
        cols = [0.0]
        for w in tb.col_widths_pt:
            cols.append(cols[-1] + w)
        rows = [0.0]
        for h in tb.row_heights_pt:
            rows.append(rows[-1] + h)
        nr, nc = len(tb.row_heights_pt), len(tb.col_widths_pt)
        rot = (b.rotation, b.x + b.w / 2, b.y + b.h / 2)
        rects, texts = [], []
        for cell in tb.cells:
            r0, c0 = min(nr, max(0, cell.row)), min(nc, max(0, cell.col))
            r1, c1 = min(nr, cell.row + cell.row_span), min(nc, cell.col + cell.col_span)
            rects.append((cell, b.x + cols[c0], b.y + rows[r0], cols[c1] - cols[c0], rows[r1] - rows[r0]))
        for cell, x, y, w, h in rects:
            if not cell.paragraphs:
                continue
            t, r, btm, l = cell.margins_pt
            inner_w, inner_h = max(1.0, w - l - r), h - t - btm
            raw = self.layout.block(cell.paragraphs, inner_w, True, tb.id)
            if not raw:
                continue
            content = raw[-1].baseline - raw[-1].descent
            dy = (inner_h - content) / 2 if cell.v_align == "middle" else (inner_h - content) if cell.v_align == "bottom" else 0.0
            texts.append((cell, self._convert(raw, cell.paragraphs, tb.id, x + l, y + t + dy, rot, (cell.row, cell.col))))
        return rects, texts


def page_items(page: StyledPage) -> list[tuple[str, Any]]:
    """一頁的元素，依圖層前後由下到上（同一層時的先後與 ai_pdf 相同：文字、圖片、形狀、表格）。"""
    items = [(t.z, 0, "text_box", t) for t in page.text_boxes] + [(i.z, 1, "image", i) for i in page.images]
    items += [(s.z, 2, "shape", s) for s in page.shapes] + [(t.z, 3, "table", t) for t in page.tables]
    items.sort(key=lambda x: (x[0], x[1]))
    return [(kind, item) for _, _, kind, item in items]


def intended_lines(doc: StyledDoc, cfg: Optional[Config]) -> list[list[Line]]:
    """成品文件每一頁的文字行應該在哪裡（自家排版的結果，與輸出時是同一套算法、同一個順序）。給⑦的 SVG 驗證比對用。"""
    ts = Typesetter(cfg, [])
    out: list[list[Line]] = []
    for page in doc.pages:
        lines: list[Line] = []
        for kind, item in page_items(page):
            if kind == "text_box":
                lines += ts.text_box(item)
            elif kind == "table":
                for _, cell_lines in ts.table_cells(item)[1]:
                    lines += cell_lines
        out.append(lines)
    return out


# ================================================================ 路徑、填色、漸層、向量圖


def path_d(segments: list[list[float]], ox: float, oy: float, digits: int) -> str:
    """成品文件的路徑編碼（0 起筆、1 直線、2 三次貝茲、3 閉合）→ SVG 的 d，一律絕對座標。"""
    out = []
    for sg in segments:
        kind = int(sg[0])
        if kind == 0 and len(sg) >= 3:
            out.append(f"M{num(ox + sg[1], digits)} {num(oy + sg[2], digits)}")
        elif kind == 1 and len(sg) >= 3:
            out.append(f"L{num(ox + sg[1], digits)} {num(oy + sg[2], digits)}")
        elif kind == 2 and len(sg) >= 7:
            out.append("C" + " ".join(num((ox if i % 2 == 0 else oy) + v, digits) for i, v in enumerate(sg[1:7])))
        elif kind == 3:
            out.append("Z")
    return " ".join(out)


def fill_attrs(c: Optional[Color]) -> list[tuple[str, Optional[str]]]:
    if c is None:
        return [("fill", "none")]
    return [("fill", hex_color(c)), ("fill-opacity", num(c.a, 3) if c.a < 1 else None)]


def stroke_attrs(c: Optional[Color], width: float, digits: int, cap: str = "butt", join: str = "miter", miter: Optional[float] = None) -> list[tuple[str, Optional[str]]]:
    if c is None:
        return []
    return [
        ("stroke", hex_color(c)), ("stroke-opacity", num(c.a, 3) if c.a < 1 else None), ("stroke-width", num(width, max(digits, 3))),
        ("stroke-linecap", cap if cap in ("round", "square") else None), ("stroke-linejoin", join if join in ("round", "bevel") else None),
        ("stroke-miterlimit", num(miter, 2) if (miter and join not in ("round", "bevel")) else None),
    ]


class _Defs:
    """檔頭 defs 裡的漸層。內容完全相同的漸層只寫一次。"""

    def __init__(self, ids: _Ids, digits: int) -> None:
        self.ids = ids
        self.d = digits
        self.lines: list[str] = []
        self.cache: dict[str, str] = {}

    def gradient(self, g: Gradient, ox: float, oy: float) -> Optional[str]:
        """登記一個漸層，回傳它的 id；色標或座標不合法時回傳 None（呼叫端改用單色）。"""
        stops = sorted(g.stops, key=lambda s: s.offset)
        if not stops:
            return None
        if len(stops) == 1:
            stops = [stops[0].model_copy(update={"offset": 0.0}), stops[0].model_copy(update={"offset": 1.0})]
        d = self.d
        if g.kind == "linear" and len(g.coords) >= 4 and math.hypot(g.coords[2] - g.coords[0], g.coords[3] - g.coords[1]) > 1e-6:
            tag = "linearGradient"
            geo = [("x1", num(ox + g.coords[0], d)), ("y1", num(oy + g.coords[1], d)), ("x2", num(ox + g.coords[2], d)), ("y2", num(oy + g.coords[3], d))]
        elif g.kind == "radial" and len(g.coords) >= 3 and g.coords[2] > 1e-6:
            tag = "radialGradient"
            geo = [("cx", num(ox + g.coords[0], d)), ("cy", num(oy + g.coords[1], d)), ("r", num(g.coords[2], d))]
        else:
            return None
        body = "".join(
            "<stop" + attrs_str([("offset", num(min(1.0, max(0.0, s.offset)), 4)), ("stop-color", hex_color(s.color)), ("stop-opacity", num(s.color.a, 3) if s.color.a < 1 else None)]) + "/>"
            for s in stops
        )
        sig = tag + attrs_str(geo) + body
        if sig not in self.cache:
            gid = self.ids.make(f"grad-{len(self.cache) + 1}")
            self.cache[sig] = gid
            self.lines.append(f"<{tag}{attrs_str([('id', gid), ('gradientUnits', 'userSpaceOnUse')] + geo)}>{body}</{tag}>")
        return self.cache[sig]


def _primitive(vp: StyledVectorPath, ox: float, oy: float, d: int) -> Optional[tuple[str, list[tuple[str, Optional[str]]]]]:
    """路徑其實是基本圖形時，回傳（元素名稱, 幾何屬性）；寫不成基本元素就回傳 None（照常寫 path）。"""
    pr = vp.primitive
    if pr is None:
        return None
    cx, cy, w, h = ox + pr.cx, oy + pr.cy, float(pr.w), float(pr.h)
    ang = float(pr.angle_deg) % 360.0
    rot = [("transform", f"rotate({num(ang, 3)} {num(cx, d)} {num(cy, d)})")] if min(ang, 360.0 - ang) > 1e-4 else []
    if pr.kind in ("rect", "rounded_rect") and w > 0 and h > 0:
        r = max(0.0, min(float(pr.corner_radius), w / 2, h / 2)) if pr.kind == "rounded_rect" else 0.0
        geo = [("x", num(cx - w / 2, d)), ("y", num(cy - h / 2, d)), ("width", num(w, d)), ("height", num(h, d))]
        if r > 0:
            geo += [("rx", num(r, d)), ("ry", num(r, d))]
        return "rect", geo + rot
    if pr.kind == "circle" and w > 0:
        return "circle", [("cx", num(cx, d)), ("cy", num(cy, d)), ("r", num(w / 2, d))]
    if pr.kind == "ellipse" and w > 0 and h > 0:
        return "ellipse", [("cx", num(cx, d)), ("cy", num(cy, d)), ("rx", num(w / 2, d)), ("ry", num(h / 2, d))] + rot
    if pr.kind == "line" and w > 0 and vp.stroke is not None:
        th = math.radians(pr.angle_deg)
        dx, dy = math.cos(th) * w / 2, math.sin(th) * w / 2
        return "line", [("x1", num(cx - dx, d)), ("y1", num(cy - dy, d)), ("x2", num(cx + dx, d)), ("y2", num(cy + dy, d))]
    if pr.kind == "polygon" and len(pr.points) >= 3 and all(len(pt) >= 2 for pt in pr.points):
        return "polygon", [("points", " ".join(f"{num(ox + pt[0], d)},{num(oy + pt[1], d)}" for pt in pr.points))]
    return None


def write_vector(w: _Writer, v: StyledVector, ox: float, oy: float, defs: _Defs, p: dict, deg: list[Degradation], group_attrs: list[tuple[str, Optional[str]]]) -> int:
    """把一個向量圖寫成一個群組，由下到上逐條路徑。（ox, oy）是向量圖的框左上角在這個 SVG 裡的位置。回傳寫出的路徑數。"""
    d = p["precision"]
    w.start("g", group_attrs)
    count = 0
    noted: set[str] = set()
    for vp in v.paths:
        paint: list[tuple[str, Optional[str]]] = []
        filled = False
        if vp.gradient is not None:
            gid = defs.gradient(vp.gradient, ox, oy)
            if gid is not None:
                paint.append(("fill", f"url(#{gid})"))
                filled = True
            elif vp.fill is not None and "grad" not in noted:
                noted.add("grad")
                deg.append(Degradation(element_id=v.id, concept="漸層", action="漸層的色標或座標不合法，改用平均色填滿", lossy=True))
        if not filled:
            paint += fill_attrs(vp.fill)
            filled = vp.fill is not None
        if filled and vp.fill_rule == "evenodd":
            paint.append(("fill-rule", "evenodd"))
        if vp.stroke is not None:
            width = float(vp.stroke_width_pt or 0.0)
            if width <= 0:
                width = float(p["hairline_pt"])
                if "hair" not in noted:
                    noted.add("hair")
                    deg.append(Degradation(element_id=v.id, concept="零寬細線", action=f"來源的筆畫寬度是 0（PDF 的最細線），SVG 沒有對應，寫成 {num(width, 3)} pt", lossy=True))
            paint += stroke_attrs(vp.stroke, width, d, vp.stroke_cap, vp.stroke_join, p["miter_limit"])
        if not filled and vp.stroke is None:
            continue  # 沒有填色也沒有筆畫：畫出來看不見，不寫
        prim = _primitive(vp, ox, oy, d) if p["primitives"] else None
        if prim is not None:
            geo = [a for a in prim[1] if a[0] != "transform"]
            w.empty(prim[0], geo + paint + [a for a in prim[1] if a[0] == "transform"])
        else:
            data = path_d(vp.path, ox, oy, d)
            if not data:
                continue
            w.empty("path", [("d", data)] + paint)
        count += 1
    w.end("g")
    return count


def _size_attrs(width: float, height: float, p: dict) -> list[tuple[str, Optional[str]]]:
    d = p["precision"]
    unit = "pt" if p["size_unit"] == "pt" else ""
    return [("width", num(width, d) + unit), ("height", num(height, d) + unit), ("viewBox", f"0 0 {num(width, d)} {num(height, d)}")]


def vector_svg(vector: StyledVector, cfg: Optional[Config] = None, *, degradations: Optional[list[Degradation]] = None, title: Optional[str] = None) -> str:
    """把單一向量圖寫成獨立的 SVG：viewBox 是圖形的外框（左上角為原點，單位 pt），裡面是一個帶 id、data-kind、data-origin 的群組。"""
    p, _ = params_for(cfg)
    deg = degradations if degradations is not None else []
    ids = _Ids()
    gid = ids.make(vector.id)
    defs = _Defs(ids, p["precision"])
    body = _Writer(1)
    write_vector(body, vector, 0.0, 0.0, defs, p, deg, [
        ("id", gid), ("data-element-id", vector.id if gid != vector.id else None), ("data-kind", "vector"), ("data-origin", vector.origin),
        ("data-image-id", vector.image_id),
    ])
    head = [("xmlns", SVG_NS), ("version", "1.1")] + _size_attrs(vector.box.w, vector.box.h, p)
    lines = ['<?xml version="1.0" encoding="UTF-8"?>', f"<svg{attrs_str(head)}>"]
    lines.append(f"  <title>{esc_text(title if title is not None else vector.id)}</title>")
    if defs.lines:
        lines += ["  <defs>"] + ["    " + ln for ln in defs.lines] + ["  </defs>"]
    return "\n".join(lines + body.lines + ["</svg>"]) + "\n"


# ================================================================ 整頁


class _Exporter:
    """一次輸出共用的狀態：參數、排版、降級紀錄、用到的字型、外部圖檔。"""

    def __init__(self, doc: StyledDoc, cfg: Optional[Config], store: Store):
        self.doc = doc
        self.store = store
        self.deg: list[Degradation] = []
        self.ts = Typesetter(cfg, self.deg)
        self.p = self.ts.p
        self.d = self.p["precision"]
        self.outline = self.p["text"] == "outline"
        self.vectors = vector_index(doc) if self.p["vectors"] else {}
        self.master_of = {eid: m.id for m in doc.masters for eid in m.member_ids}
        self.fonts_used: set[str] = set()
        self.files: dict[str, bytes] = {}  # 外部圖檔：zip 內的路徑 → 內容
        self._href: dict[str, str] = {}
        self.justified = False
        self.kern_max = 0.0  # 自家排版的行寬與含字距微調的行寬，最多差多少（pt）

    # ---------- 群組
    def _group(self, w: _Writer, ids: _Ids, element_id: str, kind: str, label: str, box: Optional[Box] = None, extra: Optional[list] = None) -> None:
        gid = ids.make(element_id)
        attrs: list[tuple[str, Optional[str]]] = [("id", gid), ("data-element-id", element_id if gid != element_id else None), ("data-kind", kind)]
        attrs.append(("data-master", self.master_of.get(element_id)))
        attrs += extra or []
        if self.p["layers"] == "element":
            attrs += [("inkscape:label", label), ("inkscape:groupmode", "layer")]
        if box is not None and box.rotation:
            attrs.append(("transform", f"rotate({num(box.rotation, 3)} {num(box.x + box.w / 2, self.d)} {num(box.y + box.h / 2, self.d)})"))
        w.start("g", attrs)

    # ---------- 圖片
    def _image_href(self, ref: BlobRef, element_id: str) -> str:
        if ref.sha256 in self._href:
            return self._href[ref.sha256]
        data = self.store.read_blob(ref)
        media = ref.media_type
        if media not in _EMBEDDABLE:  # 只內嵌 PNG 與 JPEG；其他格式轉成 PNG
            from PIL import Image

            buf = io.BytesIO()
            Image.open(io.BytesIO(data)).save(buf, "PNG")
            data, media = buf.getvalue(), "image/png"
            self.deg.append(Degradation(element_id=element_id, concept="圖片格式", action=f"「{ref.media_type}」不是各軟體都讀得懂的內嵌格式，轉成 PNG（不失真）", lossy=False))
        if self.p["images"] == "external":
            href = f"images/{ref.sha256[:16]}.{_EMBEDDABLE[media]}"
            self.files[href] = data
        else:
            href = f"data:{media};base64," + base64.b64encode(data).decode("ascii")
        self._href[ref.sha256] = href
        return href

    def _image(self, w: _Writer, ref: BlobRef, box: Box, element_id: str) -> None:
        d = self.d
        w.empty("image", [
            ("x", num(box.x, d)), ("y", num(box.y, d)), ("width", num(box.w, d)), ("height", num(box.h, d)), ("preserveAspectRatio", "none"),
            ("xlink:href", self._image_href(ref, element_id)),
        ])

    # ---------- 形狀
    def _shape(self, w: _Writer, sh: StyledShape) -> None:
        d, b = self.d, sh.box
        if sh.fill is None and sh.stroke is None:
            self.deg.append(Degradation(element_id=sh.id, concept="形狀", action="沒有填色也沒有框線，畫出來看不見", lossy=False))
        if sh.geometry == "line":
            # 直線以 rotation 記方向（同⑤的約定）：0 表示左上到右下，非 0 表示左下到右上
            y0, y1 = (b.y + b.h, b.y) if b.rotation else (b.y, b.y + b.h)
            w.empty("line", [("x1", num(b.x, d)), ("y1", num(y0, d)), ("x2", num(b.x + b.w, d)), ("y2", num(y1, d))]
                    + stroke_attrs(sh.stroke or Color(r=0, g=0, b=0), sh.stroke_width_pt or 1.0, d))
            return
        paint = fill_attrs(sh.fill) + stroke_attrs(sh.stroke, sh.stroke_width_pt or 1.0, d, miter=self.p["miter_limit"] if sh.geometry == "path" else None)
        if sh.geometry == "rounded_rect":
            r = max(0.0, min(sh.corner_radius_pt or 0.0, b.w / 2, b.h / 2))
            w.empty("rect", [("x", num(b.x, d)), ("y", num(b.y, d)), ("width", num(b.w, d)), ("height", num(b.h, d)), ("rx", num(r, d)), ("ry", num(r, d))] + paint)
        elif sh.geometry == "ellipse":
            w.empty("ellipse", [("cx", num(b.x + b.w / 2, d)), ("cy", num(b.y + b.h / 2, d)), ("rx", num(b.w / 2, d)), ("ry", num(b.h / 2, d))] + paint)
        elif sh.geometry == "path" and sh.path:
            # 自訂路徑的填色規則是奇偶（與 ai_pdf 的中性渲染相同）
            w.empty("path", [("d", path_d(sh.path, b.x, b.y, d))] + paint + ([("fill-rule", "evenodd")] if sh.fill is not None else []))
        else:
            if sh.geometry not in ("rect", "path"):
                self.deg.append(Degradation(element_id=sh.id, concept="形狀種類", action=f"不認得的形狀「{sh.geometry}」，以矩形輸出", lossy=True))
            w.empty("rect", [("x", num(b.x, d)), ("y", num(b.y, d)), ("width", num(b.w, d)), ("height", num(b.h, d))] + paint)

    # ---------- 文字
    @staticmethod
    def _style(sp: Span) -> dict[str, str]:
        return {
            "font-family": family_attr(list(sp.families)), "font-size": num(sp.size, 3), "font-weight": str(sp.weight),
            "font-style": "italic" if sp.italic else "normal", "fill": hex_color(sp.color), "fill-opacity": num(sp.color.a, 3),
        }

    _STYLE_DEFAULT = {"font-weight": "400", "font-style": "normal", "fill-opacity": "1"}

    def _text_line(self, w: _Writer, ln: Line, drawn: list[str]) -> None:
        d = self.d
        x, cs, ws = ln.placed(bool(self.p["kerning_aware"]))
        attrs: list[tuple[str, Optional[str]]] = [("xml:space", "preserve"), ("x", num(x, d)), ("y", num(ln.y, d))]
        styles = [self._style(sp) for sp in ln.spans]
        shared = {k for k in styles[0] if all(s[k] == styles[0][k] for s in styles)}
        attrs += [(k, v) for k, v in styles[0].items() if k in shared and self._STYLE_DEFAULT.get(k) != v]
        if cs:
            attrs.append(("letter-spacing", num(cs, 3)))
        if ws:
            attrs.append(("word-spacing", num(ws, 3)))
        if cs or ws:
            self.justified = True
        attrs.append(("data-paragraph", ln.paragraph_id))
        if len(ln.spans) == 1:
            body = esc_text(ln.spans[0].text)
        else:
            y_attr = [("y", num(ln.y, d))] if self.p["tspan_y"] else []
            body = "".join(f"<tspan{attrs_str(y_attr + [(k, v) for k, v in st.items() if k not in shared])}>{esc_text(sp.text)}</tspan>" for sp, st in zip(ln.spans, styles))
        w.raw(f"<text{attrs_str(attrs)}>{body}</text>")
        for sp in ln.spans:
            self.fonts_used.add(sp.families[0])
        drawn.append(ln.text)

    def _outline_span(self, sp: Span, x: float, y: float, cs: float, ws: float) -> tuple[str, float]:
        """一段字 → 路徑的 d 與這段字的總前進寬度。字形位置由 harfbuzz 排字決定，與畫 SVG 文字的軟體一致。"""
        from fontTools.pens.svgPathPen import SVGPathPen
        from fontTools.pens.transformPen import TransformPen

        d = self.d
        pen = SVGPathPen(None, ntos=lambda v: num(v, d))
        slant = math.tan(math.radians(float(self.p["outline_slant_deg"]))) if sp.faux_italic else 0.0
        s = sp.size
        scale = s / sp.face.upm
        cur = x
        for gid, cluster, adv, xo, yo in sp.face.shape(sp.text, ligatures=not cs):
            ch = sp.text[cluster] if 0 <= cluster < len(sp.text) else ""
            if sp.face.outlines and not ch.isspace():
                sp.face.glyph(gid).replay(TransformPen(pen, (scale, 0.0, scale * slant, -scale, cur + xo * s, y - yo * s)))
            cur += adv * s + cs + (ws if ch == " " else 0.0)
        return pen.getCommands(), cur - x

    def _outline_line(self, w: _Writer, ln: Line, drawn: list[str]) -> None:
        x, cs, ws = ln.placed(bool(self.p["kerning_aware"]))
        if cs or ws:
            self.justified = True
        for sp in ln.spans:
            data, adv = self._outline_span(sp, x, ln.y, cs, ws)
            if data:
                stroke = stroke_attrs(sp.color, sp.size * float(self.ts.lp["faux_bold_stroke_ratio"]), self.d, join="round") if sp.faux_bold else []
                w.empty("path", [("d", data)] + fill_attrs(sp.color) + stroke + [("data-text", sp.text), ("data-paragraph", ln.paragraph_id)])
                drawn.append(sp.text)
            elif sp.text.strip():  # 這個字型沒有向量外框（點陣字、彩色字）：這一段只能留成文字
                st = {k: v for k, v in self._style(sp).items() if self._STYLE_DEFAULT.get(k) != v}
                w.raw(f"<text{attrs_str([('xml:space', 'preserve'), ('x', num(x, self.d)), ('y', num(ln.y, self.d))] + list(st.items()) + [('data-paragraph', ln.paragraph_id)])}>{esc_text(sp.text)}</text>")
                self.fonts_used.add(sp.families[0])
                self.ts.note(("nooutline", sp.face.family), ln.element_id, "文字轉外框", f"「{sp.face.family}」沒有向量外框，這些字保留成文字")
                drawn.append(sp.text)
            x += adv

    def _lines(self, w: _Writer, lines: list[Line], drawn: list[str]) -> None:
        for ln in lines:
            self.kern_max = max(self.kern_max, abs(ln.width - ln.shaped_width))
            (self._outline_line if self.outline else self._text_line)(w, ln, drawn)

    # ---------- 表格
    def _table(self, w: _Writer, ids: _Ids, tb: StyledTable, drawn: list[str]) -> None:
        d = self.d
        self.deg.append(Degradation(element_id=tb.id, concept="表格", action="SVG 沒有表格物件：以儲存格底色（rect）、框線（line）與逐格文字繪製", lossy=True))
        if any(cell.number_format or cell.formula for cell in tb.cells):
            self.deg.append(Degradation(element_id=tb.id, concept="數字格式與公式", action="只保留顯示的文字", lossy=True))
        rects, texts = self.ts.table_cells(tb)
        for cell, x, y, cw, ch in rects:
            if cell.fill is not None:
                w.empty("rect", [("x", num(x, d)), ("y", num(y, d)), ("width", num(cw, d)), ("height", num(ch, d))] + fill_attrs(cell.fill))
        seen: dict[tuple, int] = {}
        borders: list[Optional[list]] = []
        for cell, x, y, cw, ch in rects:
            edges = ((x, y, x + cw, y), (x + cw, y, x + cw, y + ch), (x, y + ch, x + cw, y + ch), (x, y, x, y + ch))
            for border, (x0, y0, x1, y1) in zip(cell.borders, edges):
                if border is None or border.width_pt <= 0:
                    continue
                key = (num(x0, d), num(y0, d), num(x1, d), num(y1, d))
                attrs = [("x1", key[0]), ("y1", key[1]), ("x2", key[2]), ("y2", key[3])] + stroke_attrs(border.color, border.width_pt, d)
                if key in seen:  # 相鄰儲存格共用的框線：只留後畫的那一條（外觀與逐格各畫一次相同）
                    borders[seen[key]] = None
                seen[key] = len(borders)
                borders.append(attrs)
        for attrs in borders:
            if attrs is not None:
                w.empty("line", attrs)
        for cell, lines in texts:
            if not lines:
                continue
            w.start("g", [("id", ids.make(f"{tb.id}-r{cell.row + 1}c{cell.col + 1}")), ("data-kind", "cell"), ("data-row", str(cell.row)), ("data-col", str(cell.col))])
            self._lines(w, lines, drawn)
            w.end("g")

    # ---------- 整頁
    def page_fill(self, page: StyledPage) -> Optional[Color]:
        """頁面底色矩形要填的顏色；None 表示不畫底色矩形。
        ⑤在頁面底色是白色時把 background_color 設成 None（代表白）。SVG 沒有「紙張」：不畫底色就是透明，
        在深色模式的看圖軟體或預覽裡會變成黑底、深色的字看不見。所以 svg_page_background = "auto"（預設）時一律畫，
        沒有底色就畫白色；"none" 時只有⑤指定了底色才畫，其餘保持透明（例如要疊在別的底上）。"""
        if page.background_color is not None:
            return page.background_color
        return Color(r=255, g=255, b=255) if self.p["page_background"] == "auto" else None

    def page(self, page: StyledPage, title: str) -> tuple[str, list[str]]:
        """把一頁寫成 SVG。回傳（SVG 內容, 這一頁畫出的文字，依文件順序）。"""
        d = self.d
        ids = _Ids()
        defs = _Defs(ids, d)
        w = _Writer(1)
        drawn: list[str] = []
        n = page.page_index + 1
        fill = self.page_fill(page)
        if fill is not None or page.background_image is not None:
            self._group(w, ids, f"p{n:03d}-background", "background", _KIND_LABEL["background"])
            if fill is not None:
                w.empty("rect", [("x", "0"), ("y", "0"), ("width", num(page.width_pt, d)), ("height", num(page.height_pt, d))] + fill_attrs(fill))
            if page.background_image is not None:
                self._image(w, page.background_image, Box(x=0, y=0, w=page.width_pt, h=page.height_pt), f"background-{n}")
                self.deg.append(Degradation(element_id=f"background-{n}", concept="尚未轉成可編輯物件的內容", action="保留在整頁底圖中", lossy=False))
            w.end("g")
        for kind, item in page_items(page):
            if kind == "text_box":
                lines = self.ts.text_box(item)
                first = next((sp.text.strip() for ln in lines for sp in ln.spans if sp.text.strip()), "")
                self._group(w, ids, item.id, "text_box", (_KIND_LABEL["text_box"] + " " + first[:24]).strip(), item.box)
                self._lines(w, lines, drawn)
                w.end("g")
            elif kind == "image":
                vec = self.vectors.get(item.vector_id) if item.vector_id else None
                if vec is not None and vec.paths:
                    # 這張圖片其實是向量圖的點陣版：改畫向量圖、跳過圖片（由圖片找向量圖，母片成員也適用）
                    self._vector(w, ids, defs, item, vec)
                else:
                    self._group(w, ids, item.id, "image", _KIND_LABEL["image"], item.box)
                    self._image(w, item.image, item.box, item.id)
                    w.end("g")
            elif kind == "shape":
                label = f"{_KIND_LABEL['shape']} {_SHAPE_LABEL.get(item.geometry, item.geometry)}"
                self._group(w, ids, item.id, "shape", label, None if item.geometry == "line" else item.box)
                self._shape(w, item)
                w.end("g")
            else:
                self._group(w, ids, item.id, "table", _KIND_LABEL["table"], item.box)
                self._table(w, ids, item, drawn)
                w.end("g")
        text_ids = [t.id for t in sorted(page.text_boxes + page.tables, key=lambda t: t.z)]  # type: ignore[operator]
        ro = [i for i in page.reading_order if i in set(text_ids)]
        if ro and ro != [i for i in text_ids if i in set(ro)]:
            self.deg.append(Degradation(element_id=f"page-{n}", concept="閱讀順序", action="SVG 的元素依圖層前後排列，沒有另外記錄閱讀順序；螢幕報讀或抽出文字的順序可能與閱讀順序不同", lossy=True))
        head: list[tuple[str, Optional[str]]] = [("xmlns", SVG_NS), ("xmlns:xlink", XLINK_NS)]
        if self.p["layers"] == "element":
            head.append(("xmlns:inkscape", INKSCAPE_NS))
        head += [("version", "1.1")] + _size_attrs(page.width_pt, page.height_pt, self.p) + [("data-page", str(n))]
        lines = ['<?xml version="1.0" encoding="UTF-8"?>', f"<svg{attrs_str(head)}>", f"  <title>{esc_text(title)}</title>"]
        if defs.lines:
            lines += ["  <defs>"] + ["    " + ln for ln in defs.lines] + ["  </defs>"]
        return "\n".join(lines + w.lines + ["</svg>"]) + "\n", drawn

    def _vector(self, w: _Writer, ids: _Ids, defs: _Defs, im: StyledImage, vec: StyledVector) -> None:
        gid = ids.make(vec.id)
        label = f"{_KIND_LABEL['vector']} {_VECTOR_LABEL.get(vec.kind, '')}".strip()
        attrs: list[tuple[str, Optional[str]]] = [
            ("id", gid), ("data-element-id", vec.id if gid != vec.id else None), ("data-kind", "vector"), ("data-origin", vec.origin),
            ("data-image-id", im.id), ("data-master", self.master_of.get(im.id) or self.master_of.get(vec.id)),
        ]
        if self.p["layers"] == "element":
            attrs += [("inkscape:label", label), ("inkscape:groupmode", "layer")]
        # 向量圖的框不旋轉（成品文件的約定），位置以對應圖片的框為準：母片上的圖片取自第一頁，它的向量圖也在第一頁
        write_vector(w, vec, im.box.x, im.box.y, defs, self.p, self.deg, attrs)


# ================================================================ 寫出後的自檢與讀回


@dataclass
class ReadLine:
    """從 SVG 讀回來的一行文字（或文字轉外框後的一個片段）。x、y 是頁面座標（已套用群組的 transform）。"""

    element_id: str
    kind: str  # text_box、table
    cell: Optional[tuple[int, int]]
    paragraph_id: str
    text: str
    x: float
    y: float
    rotation: float  # 累計的旋轉角度（度）
    outlined: bool
    spans: list[dict] = field(default_factory=list)  # 每個片段：text、families、size、weight、italic
    letter_spacing: float = 0.0
    word_spacing: float = 0.0


_TRANSFORM = re.compile(r"(matrix|translate|scale|rotate)\s*\(([^)]*)\)")


def _matrix(value: Optional[str]) -> tuple[float, float, float, float, float, float]:
    """transform 屬性 → 矩陣（a, b, c, d, e, f）。只認 matrix、translate、scale、rotate。"""
    m = (1.0, 0.0, 0.0, 1.0, 0.0, 0.0)
    for name, args in _TRANSFORM.findall(value or ""):
        v = [float(t) for t in re.split(r"[\s,]+", args.strip()) if t]
        if name == "matrix" and len(v) == 6:
            t = tuple(v)
        elif name == "translate" and v:
            t = (1.0, 0.0, 0.0, 1.0, v[0], v[1] if len(v) > 1 else 0.0)
        elif name == "scale" and v:
            t = (v[0], 0.0, 0.0, v[1] if len(v) > 1 else v[0], 0.0, 0.0)
        elif name == "rotate" and v:
            th = math.radians(v[0])
            c, s = math.cos(th), math.sin(th)
            cx, cy = (v[1], v[2]) if len(v) >= 3 else (0.0, 0.0)
            t = (c, s, -s, c, cx - c * cx + s * cy, cy - s * cx - c * cy)
        else:
            continue
        m = _mul(m, t)  # type: ignore[arg-type]
    return m


def _mul(m, t):
    a, b, c, d, e, f = m
    a2, b2, c2, d2, e2, f2 = t
    return (a * a2 + c * b2, b * a2 + d * b2, a * c2 + c * d2, b * c2 + d * d2, a * e2 + c * f2 + e, b * e2 + d * f2 + f)


def parse(data: bytes):
    """用 lxml 重新解析輸出檔（不解析實體、不連網、不載入 DTD）。"""
    from lxml import etree

    parser = etree.XMLParser(resolve_entities=False, no_network=True, load_dtd=False, huge_tree=True, remove_blank_text=False)
    return etree.fromstring(data, parser)


def _local(tag) -> str:
    return tag.rsplit("}", 1)[-1] if isinstance(tag, str) else ""


def read_lines(root) -> list[ReadLine]:
    """把 SVG 裡的文字連同位置讀回來，並對回它所屬的元素（最上層群組的編號）與儲存格。"""
    out: list[ReadLine] = []
    q = "{" + SVG_NS + "}"

    def inherited(el, name: str) -> Optional[str]:
        while el is not None:
            v = el.get(name)
            if v is not None:
                return v
            el = el.getparent()
        return None

    def span_of(el, text: str) -> dict:
        return {
            "text": text, "families": parse_family(inherited(el, "font-family") or ""), "size": float(inherited(el, "font-size") or 0.0),
            "weight": int(float(inherited(el, "font-weight") or 400)), "italic": (inherited(el, "font-style") or "normal") == "italic",
            "fill": inherited(el, "fill") or "#000000",
        }

    for el in root.iter(q + "text", q + "path"):
        outlined = el.tag == q + "path"
        if outlined and el.get("data-text") is None:
            continue
        m = (1.0, 0.0, 0.0, 1.0, 0.0, 0.0)
        chain = []
        node = el
        while node is not None:
            chain.append(node)
            node = node.getparent()
        owner, kind, cell = "", "", None
        for node in reversed(chain):
            m = _mul(m, _matrix(node.get("transform")))
            k = node.get("data-kind")
            if k == "cell":
                cell = (int(node.get("data-row", "0")), int(node.get("data-col", "0")))
            elif k and not owner:
                owner, kind = node.get("data-element-id") or node.get("id") or "", k
        if outlined:
            x = y = 0.0
            text = el.get("data-text") or ""
            spans = [{"text": text}]
        else:
            x, y = float((el.get("x") or "0").split()[0]), float((el.get("y") or "0").split()[0])
            tspans = [c for c in el if c.tag == q + "tspan"]
            spans = [span_of(c, "".join(c.itertext())) for c in tspans] if tspans else [span_of(el, el.text or "")]
            text = "".join(el.itertext())
        out.append(ReadLine(
            element_id=owner, kind=kind, cell=cell, paragraph_id=el.get("data-paragraph") or "", text=text,
            x=m[0] * x + m[2] * y + m[4], y=m[1] * x + m[3] * y + m[5], rotation=math.degrees(math.atan2(m[1], m[0])), outlined=outlined, spans=spans,
            letter_spacing=float(el.get("letter-spacing") or 0.0), word_spacing=float(el.get("word-spacing") or 0.0),
        ))
    return out


def check(data: bytes, external: Optional[set[str]] = None) -> tuple[list[str], Any]:
    """結構與安全自檢。回傳（問題清單, 解析後的根元素）；解析失敗時根元素是 None。
    檢查：XML 可解析、沒有 DOCTYPE 與處理指令、只有白名單裡的元素、id 唯一、沒有事件屬性與 style、class、
    沒有外部參照（圖片只能是內嵌的 PNG、JPEG，或 external 列出的 zip 內檔案；url() 只能指向檔內存在的 id）。"""
    from lxml import etree

    problems: list[str] = []
    head = data[:2000].lower()
    if b"<!doctype" in head or b"<!entity" in data.lower():
        problems.append("有 DOCTYPE 或實體宣告。")
    try:
        root = parse(data)
    except Exception as e:
        return [f"XML 無法解析（{e}）。"], None
    if _local(root.tag) != "svg" or not str(root.tag).startswith("{" + SVG_NS + "}"):
        problems.append("根元素不是 SVG 命名空間的 svg。")
    ids: dict[str, int] = {}
    urls: list[str] = []
    for el in root.iter():
        if isinstance(el, etree._ProcessingInstruction):
            problems.append("有處理指令。")
            continue
        if isinstance(el, etree._Comment) or not isinstance(el.tag, str):
            continue
        name = _local(el.tag)
        if not el.tag.startswith("{" + SVG_NS + "}") or name not in ALLOWED_ELEMENTS:
            problems.append(f"有不允許的元素 {name}。")
            continue
        for key, value in el.attrib.items():
            local = _local(key)
            if key == "id":
                ids[value] = ids.get(value, 0) + 1
            if local.lower().startswith("on") or local in ("style", "class"):
                problems.append(f"{name} 有不允許的屬性 {local}。")
            if local == "href":
                ok = name == "image" and (value.startswith("data:image/png;base64,") or value.startswith("data:image/jpeg;base64,") or (external is not None and value in external))
                if not ok:
                    problems.append(f"{name} 的 href 指向外部或不允許的內容（{value[:60]}）。")
            elif "url(" in value:
                urls += re.findall(r"url\(\s*([^)]*?)\s*\)", value)
            if "javascript:" in value.lower():
                problems.append(f"{name} 的屬性 {local} 含有指令碼。")
    for value, count in ids.items():
        if count > 1:
            problems.append(f"id「{value}」重複 {count} 次。")
        if not re.fullmatch(r"[A-Za-z][A-Za-z0-9\-]*", value):
            problems.append(f"id「{value}」含有英數與連字號以外的字元。")
    for u in urls:
        if not u.startswith("#") or u[1:] not in ids:
            problems.append(f"url({u}) 指向檔案外部或不存在的 id。")
    return problems, root


def selfcheck(data: bytes, expected_text: list[str], outline: bool, external: Optional[set[str]] = None, label: str = "") -> tuple[bool, list[str]]:
    """單一 SVG 的自檢：結構與安全（見 check），再加上文字數量與內容要與畫出的一致。"""
    problems, root = check(data, external)
    notes = [f"自檢{label}：{p}" for p in problems]
    ok = not problems
    if root is None:
        return False, notes
    got = read_lines(root)
    strip = lambda s: re.sub(r"\s+", "", clean(s))  # noqa: E731
    if strip("".join(g.text for g in got)) != strip("".join(expected_text)):
        ok = False
        notes.append(f"自檢{label}：讀回來的文字與畫出的文字不符。")
    if len(got) != len(expected_text):
        ok = False
        notes.append(f"自檢{label}：讀回 {len(got)} {'個片段' if outline else '行'}文字，畫出的是 {len(expected_text)}。")
    return ok, notes


def zip_bytes(files: dict[str, bytes]) -> bytes:
    """打包成 zip。檔案時間固定、依檔名排序，同樣的內容每次得到同樣的檔案。"""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for name in sorted(files):
            info = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o644 << 16
            z.writestr(info, files[name])
    return buf.getvalue()


def page_name(index: int) -> str:
    return f"page-{index + 1:03d}.svg"


def unpack(output: OutputFile, store: Store) -> tuple[dict[int, bytes], dict[str, bytes]]:
    """把 SVG 輸出檔拆回（頁碼 → 該頁的 SVG, 其他檔案）。單一 .svg 當成第一頁。"""
    data = store.read_blob(output.file)
    if output.file.ext != "zip":
        return {0: data}, {}
    pages: dict[int, bytes] = {}
    others: dict[str, bytes] = {}
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        for name in z.namelist():
            m = re.fullmatch(r"page-(\d+)\.svg", name)
            if m:
                pages[int(m.group(1)) - 1] = z.read(name)
            else:
                others[name] = z.read(name)
    return pages, others


# ================================================================ 進入點


def export(doc: StyledDoc, basename: str, cfg: Config, store: Store) -> OutputFile:
    ex = _Exporter(doc, cfg, store)
    p = ex.p
    pages: list[bytes] = []
    texts: list[list[str]] = []
    multi = len(doc.pages) > 1
    for page in doc.pages:
        title = f"{basename}（第 {page.page_index + 1} 頁）" if multi else basename
        svg, drawn = ex.page(page, title)
        pages.append(svg.encode("utf-8"))
        texts.append(drawn)
    deg = ex.deg
    if ex.outline:
        deg.append(Degradation(element_id="doc", concept="文字", action="文字全部轉成外框路徑（svg_text = \"outline\"）：外觀不依賴對方電腦的字型，但文字不能再編輯；原文字留在每條路徑的 data-text", lossy=True))
    elif any(texts):
        deg.append(Degradation(element_id="doc", concept="字型", action="SVG 不內嵌字型，只寫字型名稱與後備：對方電腦沒有該字型時會被換掉，行寬以及置中、靠右的位置會跟著變", lossy=True))
    if ex.kern_max > 0.5:
        deg.append(Degradation(element_id="doc", concept="字距微調", action=(
            f"畫 SVG 文字的軟體會套用字距微調（kerning）與連字，成品文件的排版（與 7A 中性渲染）不套用；這份文件的行寬因此最多差 {ex.kern_max:.1f} pt。"
            + ("置中、靠右的行已依微調後的行寬修正起點；" if ex.p["kerning_aware"] else "")
            + "來源本來沒有字距微調時，這些行會比原圖略窄"), lossy=True))
    if ex.justified and not ex.outline:
        deg.append(Degradation(element_id="doc", concept="左右對齊", action="SVG 沒有左右對齊：有空白的行寫 word-spacing、沒有空白的行寫 letter-spacing；部分軟體會忽略 letter-spacing", lossy=True))
    if doc.masters:
        deg.append(Degradation(element_id="doc", concept="母片", action="SVG 沒有母片：母片上的元素在每一頁各畫一次（群組上以 data-master 標示），改一處不會連動其他頁", lossy=True))
    if doc.styles:
        deg.append(Degradation(element_id="doc", concept="樣式表", action="SVG 沒有段落與字元樣式，只保留直接寫在元素上的外觀", lossy=True))
    if doc.theme_colors:
        deg.append(Degradation(element_id="doc", concept="主題色", action="SVG 沒有色票概念，捨棄", lossy=True))

    ok = True
    notes: list[str] = []
    external = set(ex.files) if ex.files else None
    for i, (data, drawn) in enumerate(zip(pages, texts)):
        page_ok, page_notes = selfcheck(data, drawn, ex.outline, external, f"（第 {i + 1} 頁）" if multi else "")
        ok = ok and page_ok
        notes += page_notes
        if len(data) > int(p["canva_limit_bytes"]):
            notes.append(f"第 {i + 1} 頁的 SVG 有 {len(data) / 1e6:.1f} MB，超過 Canva 的 3 MB 上傳上限（來源是搜尋摘要，未實測）；圖片可改成外部檔（svg_images = \"external\"）。")
    as_zip = multi or bool(ex.files) or p["images"] == "external"
    if as_zip:
        files = {page_name(i): data for i, data in enumerate(pages)}
        files.update(ex.files)
        blob = store.put_blob(zip_bytes(files), "application/zip", "zip")
        filename = f"{basename}_svg.zip"
        if not multi:
            notes.append("圖片設定成外部檔（svg_images = \"external\"），所以單頁也打包成 zip：SVG 以相對路徑引用 images/ 下的圖檔，兩者要放在一起。")
    else:
        blob = store.put_blob(pages[0], MIME, "svg")
        filename = f"{basename}.svg"
    notes.append("Illustrator、Inkscape、Figma 等軟體開啟後的行為（文字是否仍可編輯、實體尺寸、圖層）未經實測；渲染只用 resvg 與 Chromium 驗證過。")
    return OutputFile(
        format=OutputFormat.SVG, file=blob, filename=filename, degradations=deg,
        fonts_used=sorted(ex.fonts_used), selfcheck_ok=ok, selfcheck_notes=notes,
    )
