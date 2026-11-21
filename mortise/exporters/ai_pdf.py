"""給 Illustrator 的文字可編輯 PDF 輸出器（ReportLab，BSD-3-Clause；圖層用 pikepdf，MPL-2.0，只做後處理）。

這份 PDF 同時是⑦ 7A 中性驗證用的「自家確定性的渲染」，所以每個字的位置都由這裡算定，
不交給任何排版引擎：斷行、行距、對齊都在本模組完成，畫的時候一行一次畫出。

寫法（依 research/formats.md 3.9 與 06-export.md 6.11 的假設 H1 到 H6）：
  - 一行一個文字物件（BT…ET），整行一次畫出（Tj），不逐字調整位置；
    行內不同樣式的片段接續畫在同一個文字物件中（只換 Tf 與顏色）。
  - 左右對齊的行用整行一致的字距（Tc），不逐字調整。
  - 字型從單一 TrueType 檔載入；字型集合檔（TTC）先用 fontTools 抽出單一字型，避免 BaseFont 被加上「-序號」。
  - CFF 外框的 OpenType 字型 ReportLab 不吃，退回下一個候選並記降級紀錄。

降階對照表（成品文件的概念 → PDF 的物件 → 是否有損）：
  頁面尺寸（每頁可不同）     → 每頁各自的 MediaBox                          → 無損
  整頁底色                   → 最底層的滿版填色矩形（「背景」圖層）          → 無損
  整頁底圖                   → 最底層的滿版影像（「背景」圖層）              → 無損（底圖中的內容本來就不可編輯）
  文字框、段落、文字流       → 沒有對應概念；拆成一行一個文字物件            → 有損（Illustrator 內以行為單位編輯）
  文字行                     → 文字矩陣（Tm）加整行字串（Tj）                → 位置無損
  旋轉                       → 文字矩陣與座標變換（cm）                      → 無損
  字型（推論的字型名稱）     → 內嵌子集的 TrueType 字型，附 ToUnicode        → 系統沒有推論字型時改用驗證字型，有損
  拉丁／東亞字型             → PDF 沒有分欄；依字形涵蓋切成片段，各用一種字型 → 無損（片段在同一個文字物件中）
  粗體、斜體                 → 對應的字型檔；沒有粗體檔時以描邊模擬          → 模擬粗體有損；沒有斜體檔時以正體輸出，有損
  字級、顏色、不透明度       → Tf、填色、ExtGState 的 ca                      → 無損
  行距、段距、首行縮排、對齊 → 由本模組算出每行的基線位置                    → 位置無損；屬性本身捨棄
  左右對齊                   → 整行一致的字距（Tc）                          → 無損（末行靠左）
  圖片                       → 影像物件，原解析度；透明用 SMask              → 無損
  形狀（矩形、圓角矩形、橢圓、直線、自訂路徑） → 路徑運算元（含三次貝茲）   → 無損
  表格                       → 儲存格底色矩形、框線、逐格文字                → 有損（沒有表格物件）
  數字格式、公式             → 只留顯示文字                                  → 有損
  樣式表                     → 沒有                                          → 捨棄
  主題色                     → 沒有色票概念                                  → 捨棄
  圖層前後（z）              → 繪製順序                                      → 無損
  圖層分類                   → OCG 選擇性內容群組：背景、圖片、形狀、文字    → Illustrator 是否採用未實測
  閱讀順序                   → 沒有寫結構標記（Tagged PDF），依 z 順序繪製   → 與 z 順序不同時有損
"""

from __future__ import annotations

import hashlib
import io
import json
import math
import re
import subprocess
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

from reportlab.lib.colors import Color as RLColor
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFError, TTFont
from reportlab.pdfgen import canvas as rl_canvas

from ..artifacts import (
    Align,
    Degradation,
    FontChoice,
    LineSpacingMode,
    OutputFile,
    OutputFormat,
    StyledDoc,
    StyledPage,
    StyledParagraph,
    StyledRun,
    StyledShape,
    StyledTable,
    StyledTextBox,
)
from ..artifacts.common import Color
from ..artifacts.styled_doc import Box
from ..config import Config
from ..pipeline.store import Store
from ..tools.fonts import is_cjk

DEFAULT_PARAMS: dict[str, Any] = {
    "font_line_height": 1.2,  # 倍數行距的基準：一行高 = 字級 × 此值 × 倍數；沒有行距設定時取倍數 1
    "ascent_metric": "hhea",  # 首行基線距文字框上緣的量度："hhea"（hhea 表 ascender）、"typo"（OS/2 sTypoAscender）、"win"（OS/2 usWinAscent）
    "wrap_widen": [1.0, 1.01, 1.02, 1.04],  # 斷行寬度試算倍率：斷行數與原圖行數不符時依序放寬，取第一個相符的
    "basefont_name": "embedded",  # "embedded"：BaseFont 寫內嵌字型自己的名稱；"inferred"：改寫成推論的字型名稱（字形仍是替代字型）
    "cjk_fallback": "WenQuanYi Zen Hei",  # 中日韓文字的後備字型
    "latin_fallback": "DejaVu Sans",  # 其他文字的後備字型
    "layers": True,  # 是否寫入 OCG 圖層
    "layer_names": {"background": "背景", "image": "圖片", "shape": "形狀", "text": "文字"},
    "faux_bold_stroke_ratio": 0.03,  # 沒有粗體字型檔時以描邊模擬：描邊寬 = 字級 × 此值
    "compress": True,  # 內容串流是否壓縮
}

MIME = "application/pdf"
_LAYER_ORDER = ("background", "image", "shape", "text")
_NO_START = set("，。、；：？！）」』】〕》〉…‥・ー,.;:?!)]}%")  # 不可放在行首的標點（簡化的禁則）
_NO_END = set("（「『【〔《〈([{")  # 不可放在行尾的標點
_BREAKS = "\n \v"


def params_from(cfg: Optional[Config], defaults: dict, key: str) -> dict:
    p = dict(defaults)
    if cfg is not None:
        p.update(cfg.section("export").get(key, {}) or {})
    return p


# ================================================================ 字型


@dataclass
class _Face:
    key: str  # ReportLab 的註冊名稱
    family: str  # 寫入檔案的字族名稱
    path: str
    cmap: frozenset
    ascent: float  # 每 1 pt 字級的上緣（pt）
    descent: float  # 每 1 pt 字級的下緣（pt，負值）
    bold: bool  # 字型檔本身是否為粗體
    italic: bool
    widths: dict = field(default_factory=dict)

    def width(self, ch: str) -> float:
        """一個字在 1 pt 字級下的前進寬度（pt）。"""
        w = self.widths.get(ch)
        if w is None:
            w = pdfmetrics.stringWidth(ch, self.key, 1.0)
            self.widths[ch] = w
        return w


@dataclass
class _Match:
    path: str
    index: int
    families: list[str]
    weight: int
    slant: int


_MATCH_CACHE: dict[str, Optional[_Match]] = {}
_CACHE_DIR = Path(tempfile.gettempdir()) / "mortise-font-cache"


def _norm(name: str) -> str:
    return re.sub(r"[\s_\-]", "", name).casefold()


def _fc_escape(name: str) -> str:
    return re.sub(r"([\\\-:,])", r"\\\1", name)


def _fc_match(family: str, bold: bool, italic: bool) -> Optional[_Match]:
    """用 fontconfig 找字型檔。回傳 None 表示 fc-match 不能用。"""
    pattern = _fc_escape(family) + (":bold" if bold else "") + (":italic" if italic else "")
    if pattern not in _MATCH_CACHE:
        m = None
        try:
            out = subprocess.run(["fc-match", "-f", "%{file}\t%{index}\t%{family}\t%{weight}\t%{slant}", pattern], capture_output=True, text=True, timeout=20).stdout
            parts = out.strip().split("\t")
            if len(parts) == 5 and parts[0]:
                m = _Match(path=parts[0], index=int(parts[1] or 0), families=[f.strip().replace("\\-", "-") for f in parts[2].split(",")], weight=int(float(parts[3] or 80)), slant=int(float(parts[4] or 0)))
        except Exception:
            m = None
        _MATCH_CACHE[pattern] = m
    return _MATCH_CACHE[pattern]


def _single_font_file(path: str, index: int) -> str:
    """字型集合檔（TTC）抽出單一字型存成 TTF（快取在暫存目錄）。一般 TTF 原樣回傳。"""
    if not path.lower().endswith((".ttc", ".otc")):
        return path
    _CACHE_DIR.mkdir(parents=True, exist_ok=True)
    st = Path(path).stat()
    out = _CACHE_DIR / (hashlib.sha1(f"{path}|{index}|{st.st_size}|{st.st_mtime_ns}".encode()).hexdigest()[:16] + ".ttf")
    if not out.exists():
        from fontTools.ttLib import TTCollection

        coll = TTCollection(path)
        tmp = out.with_suffix(".tmp")
        coll.fonts[index].save(str(tmp))
        tmp.replace(out)
    return str(out)


_FACE_CACHE: dict[tuple, tuple[Optional[_Face], str]] = {}


def _load_face(m: _Match, metric: str, rename: Optional[str]) -> tuple[Optional[_Face], str]:
    """載入字型檔並登記到 ReportLab。回傳（字型、失敗原因）。"""
    ck = (m.path, m.index, metric, rename)
    if ck in _FACE_CACHE:
        return _FACE_CACHE[ck]
    from fontTools.ttLib import TTFont as FTFont

    result: tuple[Optional[_Face], str]
    try:
        if m.path.lower().endswith((".ttc", ".otc")):
            ft = FTFont(m.path, fontNumber=m.index, lazy=True)
        else:
            ft = FTFont(m.path, lazy=True)
        if "CFF " in ft or "CFF2" in ft:
            result = (None, "cff")
        else:
            upm = ft["head"].unitsPerEm
            hhea, os2 = ft["hhea"], ft.get("OS/2")
            if metric == "typo" and os2 is not None:
                asc, desc = os2.sTypoAscender, os2.sTypoDescender
            elif metric == "win" and os2 is not None:
                asc, desc = os2.usWinAscent, -os2.usWinDescent
            else:
                asc, desc = hhea.ascent, hhea.descent
            family = ft["name"].getDebugName(16) or ft["name"].getDebugName(1) or Path(m.path).stem
            cmap = frozenset(ft.getBestCmap() or {})
            single = _single_font_file(m.path, m.index)
            key = "MTS" + hashlib.sha1(f"{single}|{metric}|{rename}".encode()).hexdigest()[:12]
            tt = TTFont(key, single)
            # 讓 FontDescriptor 的 /Ascent /Descent 與排版用的量度一致：讀回時文字框上緣 = 基線 − 上緣
            tt.face.ascent = round(asc * 1000.0 / upm)
            tt.face.descent = round(desc * 1000.0 / upm)
            if rename:
                tt.face.name = rename.encode("latin-1", "ignore") if isinstance(tt.face.name, bytes) else rename
                tt.face.familyName = tt.face.name
            pdfmetrics.registerFont(tt)
            result = (_Face(key=key, family=rename_family(rename) or family, path=m.path, cmap=cmap, ascent=tt.face.ascent / 1000.0, descent=tt.face.descent / 1000.0, bold=m.weight >= 180, italic=m.slant > 0), "")
    except TTFError as e:
        result = (None, "cff" if "postscript" in str(e).lower() else f"ttf: {e}")
    except Exception as e:  # 壞檔、讀不到
        result = (None, f"error: {e}")
    _FACE_CACHE[ck] = result
    return result


def rename_family(rename: Optional[str]) -> Optional[str]:
    if not rename:
        return None
    return rename.split("-")[0]


def _ps_name(family: str, bold: bool, italic: bool) -> str:
    """推論的字型名稱 → PostScript 名稱（basefont_name = "inferred" 時用）。"""
    base = re.sub(r"[^A-Za-z0-9]", "", family) or "Font"
    style = ("Bold" if bold else "") + ("Italic" if italic else "")
    return f"{base}-{style}" if style else base


class _Fonts:
    """字型解析：推論字型 → 驗證字型 → 後備字型；記下替代與降級。"""

    def __init__(self, params: dict, degradations: list[Degradation]):
        self.p = params
        self.deg = degradations
        self.metric = params["ascent_metric"]
        self.used: dict[str, _Face] = {}  # 寫入檔案的字族 → 字型
        self.inferred: dict[str, str] = {}  # 推論字型 → 實際寫入的字族
        self._primary: dict[tuple, tuple[_Face, bool, bool]] = {}
        self._noted: set[tuple] = set()

    def used_names(self) -> list[str]:
        return list(self.used)

    def _note(self, key: tuple, d: Degradation) -> None:
        if key not in self._noted:
            self._noted.add(key)
            self.deg.append(d)

    def _try(self, family: str, bold: bool, italic: bool, rename: Optional[str] = None) -> tuple[Optional[_Face], Optional[_Match], str]:
        m = _fc_match(family, bold, italic)
        if m is None:
            return None, None, "missing"
        if _norm(family) not in {_norm(f) for f in m.families}:
            return None, m, "missing"
        face, why = _load_face(m, self.metric, rename)
        return face, m, why or "ok"

    def primary(self, fc: FontChoice, bold: bool, italic: bool, element_id: str) -> tuple[_Face, bool, bool]:
        """回傳（字型、是否需模擬粗體、是否缺斜體）。"""
        want = fc.family.value
        ck = (want, fc.verification_family, fc.east_asian, bold, italic)
        if ck in self._primary:
            return self._primary[ck]
        cands = [want]
        for extra in (fc.verification_family, self.p["cjk_fallback"] if fc.east_asian else None, self.p["latin_fallback"], "Liberation Sans"):
            if extra and extra not in cands:
                cands.append(extra)
        rename = _ps_name(want, bold, italic) if self.p["basefont_name"] == "inferred" else None
        chosen: Optional[_Face] = None
        match: Optional[_Match] = None
        for i, fam in enumerate(cands):
            face, m, why = self._try(fam, bold, italic, rename)
            if why == "cff":
                self._note(("cff", fam), Degradation(element_id=element_id, concept="字型（CFF 外框）", action=f"「{fam}」是 CFF 外框的 OpenType，ReportLab 無法內嵌，改用下一個候選字型", lossy=True))
                continue
            if face is not None:
                chosen, match = face, m
                if i > 0:
                    self._note(("sub", want, face.family), Degradation(element_id=element_id, concept="字型", action=f"系統沒有推論的字型「{want}」，內嵌「{face.family}」代替（Illustrator 編輯文字時需要安裝字型）", lossy=True))
                break
        if chosen is None:  # 最後手段：fontconfig 給什麼用什麼
            for fam in ("sans-serif", "serif"):
                m = _fc_match(fam, bold, italic)
                if m is not None:
                    face, why = _load_face(m, self.metric, rename)
                    if face is not None:
                        chosen, match = face, m
                        self._note(("sub", want, face.family), Degradation(element_id=element_id, concept="字型", action=f"找不到推論字型「{want}」與驗證字型，內嵌「{face.family}」代替", lossy=True))
                        break
        if chosen is None:
            raise RuntimeError("找不到任何可內嵌的 TrueType 字型。")
        faux_bold = bold and not chosen.bold
        no_italic = italic and not chosen.italic
        if faux_bold:
            self._note(("bold", chosen.family), Degradation(element_id=element_id, concept="粗體", action=f"「{chosen.family}」沒有粗體字型檔，以填色加描邊模擬", lossy=True))
        if no_italic:
            self._note(("italic", chosen.family), Degradation(element_id=element_id, concept="斜體", action=f"「{chosen.family}」沒有斜體字型檔，以正體輸出", lossy=True))
        self.inferred[want] = chosen.family
        self._primary[ck] = (chosen, faux_bold, no_italic)
        return self._primary[ck]

    def for_char(self, ch: str, primary: _Face, bold: bool, italic: bool, element_id: str) -> _Face:
        """字形涵蓋：主字型沒有這個字時，中日韓文字用中文後備字型，其他用拉丁後備字型。"""
        o = ord(ch)
        if o in primary.cmap or ch.isspace() and o not in (0x3000,) and 0x20 in primary.cmap:
            return primary
        order = (self.p["cjk_fallback"], self.p["latin_fallback"]) if is_cjk(ch) else (self.p["latin_fallback"], self.p["cjk_fallback"])
        for fam in order:
            face, _, _ = self._try(fam, bold, italic)
            if face is None:
                face, _, _ = self._try(fam, False, False)
            if face is not None and o in face.cmap:
                self._note(("glyph", primary.family, face.family), Degradation(element_id=element_id, concept="缺字", action=f"「{primary.family}」沒有部分字形（例如「{ch}」），這些字改用「{face.family}」", lossy=True))
                return face
        self._note(("notdef", primary.family, ch), Degradation(element_id=element_id, concept="缺字", action=f"所有候選字型都沒有「{ch}」（U+{o:04X}），顯示為缺字方框", lossy=True))
        return primary


# ================================================================ 排版（斷行與基線）


@dataclass
class _Seg:
    text: str
    face: _Face
    run: StyledRun
    size: float
    faux_bold: bool


@dataclass
class _Line:
    segs: list[_Seg]
    width: float  # 不含行尾空白
    nchars: int  # 不含行尾空白
    size: float
    ascent: float
    descent: float
    x: float = 0.0  # 相對於文字區左上（pt，y 向下）
    baseline: float = 0.0
    char_space: float = 0.0
    paragraph: str = ""


def _can_break(a: str, b: str) -> bool:
    """a 與 b 之間可否斷行：英文在空白後斷；中日韓文字任兩字之間可斷（避開行首行尾禁則）。"""
    if b.isspace():
        return False
    if a.isspace():
        return True
    if (is_cjk(a) or is_cjk(b)) and b not in _NO_START and a not in _NO_END:
        return True
    return False


def _break_lines(chars: list[tuple[str, float]], first_avail: float, avail: float, wrap: bool) -> list[tuple[int, int]]:
    """貪婪斷行。chars 是（字、寬度 pt）；回傳每行的［起, 迄）索引。"""
    lines: list[tuple[int, int]] = []
    start, cur, last = 0, 0.0, 0
    for i, (ch, w) in enumerate(chars):
        if ch in _BREAKS:
            lines.append((start, i))
            start, cur, last = i + 1, 0.0, i + 1
            continue
        if i > start and _can_break(chars[i - 1][0], ch):
            last = i
        limit = first_avail if not lines else avail
        if wrap and i > start and not ch.isspace() and cur + w > limit + 1e-6:
            brk = last if last > start else i
            lines.append((start, brk))
            start = brk
            cur = sum(c[1] for c in chars[start:i])
            last = start
        cur += w
    lines.append((start, len(chars)))
    return lines


class _Layout:
    """把段落排成行，算出每行的起點與基線（相對於文字區左上，y 向下）。"""

    def __init__(self, fonts: _Fonts, params: dict, degradations: list[Degradation]):
        self.fonts = fonts
        self.p = params
        self.deg = degradations

    def _chars(self, sp: StyledParagraph, element_id: str) -> list[tuple[str, float, _Face, StyledRun, float, bool]]:
        out = []
        for run in sp.runs:
            size = float(run.size_pt.value)
            face, faux, _ = self.fonts.primary(run.font, run.bold, run.italic, element_id)
            for ch in run.text:
                f = face if ch in _BREAKS else self.fonts.for_char(ch, face, run.bold, run.italic, element_id)
                w = 0.0 if ch in _BREAKS else f.width(ch) * size
                out.append((ch, w, f, run, size, faux and f is face))
        return out

    def _pitch(self, sp: StyledParagraph, line: _Line) -> float:
        base = line.size * self.p["font_line_height"]
        if sp.line_spacing is None:
            return base
        ls = sp.line_spacing.value
        return float(ls.value) if ls.mode == LineSpacingMode.EXACT else base * float(ls.value)

    def paragraph_lines(self, sp: StyledParagraph, width: float, wrap: bool, element_id: str) -> list[_Line]:
        chars = self._chars(sp, element_id)
        widths = [(c[0], c[1]) for c in chars]
        indent = sp.first_line_indent_pt
        spans = _break_lines(widths, width - indent, width, wrap)
        want = sp.source_line_count
        if wrap and want > 0 and not sp.text_locked and len(spans) != want:
            for k in self.p["wrap_widen"]:
                trial = _break_lines(widths, width * k - indent, width * k, wrap)
                if len(trial) == want:
                    spans = trial
                    break
            else:
                self.deg.append(Degradation(element_id=element_id, concept="斷行", action=f"段落 {sp.id} 依文字框寬度斷成 {len(spans)} 行，原圖是 {want} 行", lossy=True))
        last_size = max((float(r.size_pt.value) for r in sp.runs), default=12.0)
        lines: list[_Line] = []
        for a, b in spans:
            seg_chars = [c for c in chars[a:b] if c[0] not in _BREAKS]
            while seg_chars and seg_chars[-1][0].isspace():  # 行尾空白不畫、不計寬
                seg_chars.pop()
            segs: list[_Seg] = []
            for ch, w, face, run, size, faux in seg_chars:
                if segs and segs[-1].face is face and segs[-1].run is run:
                    segs[-1].text += ch
                else:
                    segs.append(_Seg(text=ch, face=face, run=run, size=size, faux_bold=faux))
            if segs:
                size = max(s.size for s in segs)
                asc = max(s.face.ascent * s.size for s in segs)
                desc = min(s.face.descent * s.size for s in segs)
            else:  # 空行：沿用段落字級
                size = last_size
                face = self.fonts.primary(sp.runs[0].font, False, False, element_id)[0] if sp.runs else None
                asc = (face.ascent if face else 0.9) * size
                desc = (face.descent if face else -0.2) * size
            lines.append(_Line(segs=segs, width=sum(c[1] for c in seg_chars), nchars=len(seg_chars), size=size, ascent=asc, descent=desc, paragraph=sp.id))
        # 水平位置
        for i, ln in enumerate(lines):
            ind = indent if i == 0 else 0.0
            avail = width - ind
            if sp.align == Align.CENTER:
                ln.x = ind + (avail - ln.width) / 2
            elif sp.align == Align.RIGHT:
                ln.x = ind + avail - ln.width
            else:
                ln.x = ind
                if sp.align == Align.JUSTIFY and i < len(lines) - 1 and ln.nchars > 1 and ln.width < avail:
                    ln.char_space = (avail - ln.width) / (ln.nchars - 1)
        return lines

    def block(self, paragraphs: list[StyledParagraph], width: float, wrap: bool, element_id: str) -> list[_Line]:
        """一個文字區（文字框或儲存格）中所有段落的行，含基線位置。"""
        out: list[_Line] = []
        prev_sp: Optional[StyledParagraph] = None
        y: Optional[float] = None
        for sp in paragraphs:
            lines = self.paragraph_lines(sp, width, wrap, element_id)
            for k, ln in enumerate(lines):
                if y is None:
                    y = sp.space_before_pt + ln.ascent
                elif k == 0:
                    y += (prev_sp.space_after_pt if prev_sp else 0.0) + sp.space_before_pt + self._pitch(sp, ln)
                else:
                    y += self._pitch(sp, ln)
                ln.baseline = y
                out.append(ln)
            prev_sp = sp
        return out


# ================================================================ 座標


class _Frame:
    """元素自身的座標（原點在框的左上、y 向下、單位 pt）→ PDF 使用者空間（原點左下、y 向上）。
    旋轉以框的中心為準，順時針為正（與成品文件相同）。"""

    def __init__(self, box: Box, page_h: float, rotation: Optional[float] = None):
        th = math.radians(box.rotation if rotation is None else rotation)
        self.c, self.s = math.cos(th), math.sin(th)
        self.w, self.h = box.w, box.h
        self.cx, self.cy = box.x + box.w / 2, box.y + box.h / 2
        self.H = page_h

    def pt(self, lx: float, ly: float) -> tuple[float, float]:
        dx, dy = lx - self.w / 2, ly - self.h / 2
        px = self.cx + self.c * dx - self.s * dy
        py = self.cy + self.s * dx + self.c * dy
        return px, self.H - py

    def flipped(self) -> tuple[float, float, float, float, float, float]:
        """cm 矩陣：之後可直接用元素自身的 y 向下座標畫路徑。"""
        e, f = self.pt(0.0, 0.0)
        return (self.c, -self.s, -self.s, -self.c, e, f)

    def upright(self, lx: float, ly: float) -> tuple[float, float, float, float, float, float]:
        """以（lx, ly）為原點、y 向上的矩陣（文字矩陣與影像用）。"""
        e, f = self.pt(lx, ly)
        return (self.c, -self.s, self.s, self.c, e, f)


def _rl(color: Color) -> RLColor:
    return RLColor(color.r / 255.0, color.g / 255.0, color.b / 255.0, alpha=float(color.a))


# ================================================================ 繪製


class _Painter:
    def __init__(self, c: rl_canvas.Canvas, page: StyledPage, layout: _Layout, store: Store, params: dict, degradations: list[Degradation], drawn_text: list[str]):
        self.c = c
        self.page = page
        self.H = page.height_pt
        self.layout = layout
        self.store = store
        self.p = params
        self.deg = degradations
        self.drawn = drawn_text
        self.layer: Optional[str] = None
        self.layers_used: set[str] = set()

    # ---------- 圖層（標記內容 /OC /名稱 BDC … EMC；資源在後處理補上）
    def enter(self, layer: str) -> None:
        if not self.p["layers"] or layer == self.layer:
            return
        if self.layer is not None:
            self.c.addLiteral("EMC")
        self.c.addLiteral(f"/OC /MOC{_LAYER_ORDER.index(layer)} BDC")
        self.layer = layer
        self.layers_used.add(layer)

    def close(self) -> None:
        if self.layer is not None:
            self.c.addLiteral("EMC")
            self.layer = None

    # ---------- 文字
    def text_lines(self, lines: list[_Line], frame: _Frame, ox: float, oy: float) -> None:
        """一行一個文字物件；行內片段只換字型與顏色，接續畫出。"""
        self.enter("text")
        for ln in lines:
            if not ln.segs:
                continue
            t = self.c.beginText()
            t.setTextTransform(*frame.upright(ox + ln.x, oy + ln.baseline))
            if ln.char_space:
                t.setCharSpace(ln.char_space)
            mode = 0
            for sg in ln.segs:
                t.setFont(sg.face.key, sg.size)
                t.setFillColor(_rl(sg.run.color))
                want_mode = 2 if sg.faux_bold else 0
                if want_mode != mode:
                    if want_mode == 2:
                        t.setStrokeColor(_rl(sg.run.color))
                        t._code.append(f"{sg.size * self.p['faux_bold_stroke_ratio']:.3f} w")
                    t.setTextRenderMode(want_mode)
                    mode = want_mode
                t.textOut(sg.text)
                self.drawn.append(sg.text)
                self.layout.fonts.used[sg.face.family] = sg.face
            if mode:
                t.setTextRenderMode(0)
            if ln.char_space:
                t.setCharSpace(0)
            self.c.drawText(t)

    def text_box(self, tb: StyledTextBox) -> None:
        frame = _Frame(tb.box, self.H)
        lines = self.layout.block(tb.paragraphs, tb.box.w, tb.wrap, tb.id)
        if len(lines) > 1:
            self.deg.append(Degradation(element_id=tb.id, concept="段落與文字框", action=f"PDF 沒有段落與文字框，寫成 {sum(1 for ln in lines if ln.segs)} 個逐行的文字物件", lossy=True))
        if lines and lines[-1].baseline - lines[-1].descent > tb.box.h + 0.5 * lines[-1].size:
            self.deg.append(Degradation(element_id=tb.id, concept="文字框高度", action="排出的文字超出文字框下緣（PDF 沒有文字框，照樣畫出）", lossy=False))
        self.text_lines(lines, frame, 0.0, 0.0)

    # ---------- 圖片
    def image(self, ref, box: Box, layer: str) -> None:
        self.enter(layer)
        frame = _Frame(box, self.H)
        c = self.c
        c.saveState()
        c.transform(*frame.upright(0.0, box.h))
        c.drawImage(str(self.store.blob_path(ref)), 0, 0, box.w, box.h, mask="auto")
        c.restoreState()

    # ---------- 形狀
    def _paint(self, fill: Optional[Color], stroke: Optional[Color], width: Optional[float]) -> tuple[int, int]:
        c = self.c
        if fill is not None:
            c.setFillColor(_rl(fill))
        if stroke is not None:
            c.setStrokeColor(_rl(stroke))
            c.setLineWidth(width or 1.0)
        return (1 if fill is not None else 0, 1 if stroke is not None else 0)

    def shape(self, sh: StyledShape) -> None:
        self.enter("shape")
        c, b = self.c, sh.box
        if sh.fill is None and sh.stroke is None:
            self.deg.append(Degradation(element_id=sh.id, concept="形狀", action="沒有填色也沒有框線，畫出來看不見", lossy=False))
        c.saveState()
        if sh.geometry == "line":
            # 直線以 rotation 記方向（同⑤的約定）：0 表示左上到右下，非 0 表示左下到右上
            y0, y1 = (b.y + b.h, b.y) if b.rotation else (b.y, b.y + b.h)
            c.setStrokeColor(_rl(sh.stroke or Color(r=0, g=0, b=0)))
            c.setLineWidth(sh.stroke_width_pt or 1.0)
            c.line(b.x, self.H - y0, b.x + b.w, self.H - y1)
            c.restoreState()
            return
        fill, stroke = self._paint(sh.fill, sh.stroke, sh.stroke_width_pt)
        c.transform(*_Frame(b, self.H).flipped())
        if sh.geometry == "rounded_rect":
            r = max(0.0, min(sh.corner_radius_pt or 0.0, b.w / 2, b.h / 2))
            c.roundRect(0, 0, b.w, b.h, r, stroke=stroke, fill=fill)
        elif sh.geometry == "ellipse":
            c.ellipse(0, 0, b.w, b.h, stroke=stroke, fill=fill)
        elif sh.geometry == "path" and sh.path:
            p = c.beginPath()
            for sg in sh.path:
                kind = int(sg[0])
                if kind == 0:
                    p.moveTo(sg[1], sg[2])
                elif kind == 1:
                    p.lineTo(sg[1], sg[2])
                elif kind == 2:
                    p.curveTo(sg[1], sg[2], sg[3], sg[4], sg[5], sg[6])
                elif kind == 3:
                    p.close()
            c.drawPath(p, stroke=stroke, fill=fill, fillMode=0)
        else:
            if sh.geometry not in ("rect", "path"):
                self.deg.append(Degradation(element_id=sh.id, concept="形狀種類", action=f"不認得的形狀「{sh.geometry}」，以矩形輸出", lossy=True))
            c.rect(0, 0, b.w, b.h, stroke=stroke, fill=fill)
        c.restoreState()

    # ---------- 表格
    def table(self, tb: StyledTable) -> None:
        self.deg.append(Degradation(element_id=tb.id, concept="表格", action="PDF 沒有表格物件：以儲存格底色、框線與逐格文字繪製", lossy=True))
        if any(cell.number_format or cell.formula for cell in tb.cells):
            self.deg.append(Degradation(element_id=tb.id, concept="數字格式與公式", action="只保留顯示的文字", lossy=True))
        frame = _Frame(tb.box, self.H)
        cols = [0.0]
        for w in tb.col_widths_pt:
            cols.append(cols[-1] + w)
        rows = [0.0]
        for h in tb.row_heights_pt:
            rows.append(rows[-1] + h)
        nr, nc = len(tb.row_heights_pt), len(tb.col_widths_pt)
        rects = []
        for cell in tb.cells:
            r1, c1 = min(nr, cell.row + cell.row_span), min(nc, cell.col + cell.col_span)
            rects.append((cell, cols[cell.col], rows[cell.row], cols[c1] - cols[cell.col], rows[r1] - rows[cell.row]))
        c = self.c
        # 1. 底色
        self.enter("shape")
        c.saveState()
        c.transform(*frame.flipped())
        for cell, x, y, w, h in rects:
            if cell.fill is not None:
                c.setFillColor(_rl(cell.fill))
                c.rect(x, y, w, h, stroke=0, fill=1)
        # 2. 框線（上、右、下、左）
        for cell, x, y, w, h in rects:
            edges = ((x, y, x + w, y), (x + w, y, x + w, y + h), (x, y + h, x + w, y + h), (x, y, x, y + h))
            for border, (x0, y0, x1, y1) in zip(cell.borders, edges):
                if border is not None and border.width_pt > 0:
                    c.setStrokeColor(_rl(border.color))
                    c.setLineWidth(border.width_pt)
                    c.line(x0, y0, x1, y1)
        c.restoreState()
        # 3. 文字
        for cell, x, y, w, h in rects:
            if not cell.paragraphs:
                continue
            t, r, btm, l = cell.margins_pt
            inner_w, inner_h = max(1.0, w - l - r), h - t - btm
            lines = self.layout.block(cell.paragraphs, inner_w, True, tb.id)
            if not lines:
                continue
            content = lines[-1].baseline - lines[-1].descent
            dy = 0.0
            if cell.v_align == "middle":
                dy = (inner_h - content) / 2
            elif cell.v_align == "bottom":
                dy = inner_h - content
            self.text_lines(lines, frame, x + l, y + t + dy)

    # ---------- 整頁
    def page_items(self) -> None:
        page = self.page
        if page.background_color is not None:
            self.enter("background")
            self.c.saveState()
            self.c.setFillColor(_rl(page.background_color))
            self.c.rect(0, 0, page.width_pt, page.height_pt, stroke=0, fill=1)
            self.c.restoreState()
        if page.background_image is not None:
            self.image(page.background_image, Box(x=0, y=0, w=page.width_pt, h=page.height_pt), "background")
            self.deg.append(Degradation(element_id=f"background-{page.page_index + 1}", concept="尚未轉成可編輯物件的內容", action="保留在整頁底圖中", lossy=False))
        items = [(t.z, 0, "text", t) for t in page.text_boxes] + [(i.z, 1, "image", i) for i in page.images] + [(s.z, 2, "shape", s) for s in page.shapes] + [(t.z, 3, "table", t) for t in page.tables]
        items.sort(key=lambda x: (x[0], x[1]))
        for _, _, kind, item in items:
            if kind == "text":
                self.text_box(item)
            elif kind == "image":
                self.image(item.image, item.box, "image")
            elif kind == "shape":
                self.shape(item)
            else:
                self.table(item)
        self.close()
        # 閱讀順序：PDF 依 z 順序繪製，沒有寫結構標記
        text_ids = [t.id for t in sorted(page.text_boxes + page.tables, key=lambda t: t.z)]  # type: ignore[operator]
        ro = [i for i in page.reading_order if i in set(text_ids)]
        if ro and ro != [i for i in text_ids if i in set(ro)]:
            self.deg.append(Degradation(element_id=f"page-{page.page_index + 1}", concept="閱讀順序", action="PDF 依圖層前後繪製，沒有寫入結構標記（Tagged PDF），抽出文字的順序可能與閱讀順序不同", lossy=True))


# ================================================================ 圖層後處理與自檢


def _add_layers(data: bytes, layer_pages: list[set[str]], names: dict) -> bytes:
    """用 pikepdf 寫入 OCG 圖層定義，並在每頁資源補上 /Properties。"""
    import pikepdf

    pdf = pikepdf.open(io.BytesIO(data))
    ocgs = {}
    for i, key in enumerate(_LAYER_ORDER):
        ocgs[key] = pdf.make_indirect(pikepdf.Dictionary(Type=pikepdf.Name.OCG, Name=pikepdf.String(names.get(key, key))))
    arr = pikepdf.Array([ocgs[k] for k in _LAYER_ORDER])
    pdf.Root.OCProperties = pikepdf.Dictionary(OCGs=arr, D=pikepdf.Dictionary(Order=pikepdf.Array([ocgs[k] for k in _LAYER_ORDER]), ON=pikepdf.Array([ocgs[k] for k in _LAYER_ORDER]), BaseState=pikepdf.Name.ON))
    for page, used in zip(pdf.pages, layer_pages):
        if not used:
            continue
        res = page.obj.get("/Resources")
        if res is None:
            page.obj.Resources = res = pikepdf.Dictionary()
        props = pikepdf.Dictionary()
        for i, key in enumerate(_LAYER_ORDER):
            props[f"/MOC{i}"] = ocgs[key]
        res.Properties = props
    out = io.BytesIO()
    pdf.save(out, deterministic_id=True)
    return out.getvalue()


def _set_info(data: bytes, inferred: dict[str, str]) -> bytes:
    """在文件資訊記下「推論字型 → 實際內嵌字型」，供還原度報告與重組腳本使用。"""
    import pikepdf

    pdf = pikepdf.open(io.BytesIO(data))
    with pdf.open_metadata(set_pikepdf_as_editor=False) as meta:
        meta["dc:format"] = "application/pdf"
    pdf.docinfo["/MortiseFonts"] = pikepdf.String(json.dumps(inferred, ensure_ascii=False, sort_keys=True))
    out = io.BytesIO()
    pdf.save(out, deterministic_id=True)
    return out.getvalue()


def selfcheck(data: bytes, doc: StyledDoc, expected_text: list[str], want_layers: bool) -> tuple[bool, list[str]]:
    """重新讀入：頁數、頁面尺寸、字型內嵌與 ToUnicode、圖層、抽出的文字。"""
    import pikepdf
    import pypdfium2 as pdfium

    notes: list[str] = []
    ok = True
    try:
        pdf = pikepdf.open(io.BytesIO(data))
    except Exception as e:
        return False, [f"自檢：pikepdf 無法開啟輸出檔（{e}）。"]
    if len(pdf.pages) != len(doc.pages):
        ok = False
        notes.append(f"自檢：頁數 {len(pdf.pages)} 與成品文件 {len(doc.pages)} 不符。")
    for i, (pg, sp) in enumerate(zip(pdf.pages, doc.pages)):
        mb = [float(v) for v in pg.mediabox]
        if abs(mb[2] - mb[0] - sp.width_pt) > 0.01 or abs(mb[3] - mb[1] - sp.height_pt) > 0.01:
            ok = False
            notes.append(f"自檢：第 {i + 1} 頁尺寸 {mb} 與成品文件不符。")
        fonts = pg.obj.get("/Resources", {}).get("/Font", {})
        for name, f in dict(fonts).items():
            desc = f.get("/FontDescriptor")
            if f.get("/Subtype") == "/Type1" and desc is None:
                notes.append(f"自檢：第 {i + 1} 頁有未內嵌的標準字型 {f.get('/BaseFont')}（ReportLab 的初始字型，未用來畫字）。")
                continue
            if desc is None or "/FontFile2" not in desc:
                ok = False
                notes.append(f"自檢：第 {i + 1} 頁字型 {f.get('/BaseFont')} 沒有內嵌 TrueType 字型檔。")
            if "/ToUnicode" not in f:
                ok = False
                notes.append(f"自檢：第 {i + 1} 頁字型 {f.get('/BaseFont')} 沒有 ToUnicode。")
    if want_layers and "/OCProperties" not in pdf.Root:
        ok = False
        notes.append("自檢：沒有寫入 OCG 圖層。")
    try:
        d = pdfium.PdfDocument(data)
        got = "".join(d[i].get_textpage().get_text_range() for i in range(len(d)))
        d.close()
        strip = lambda s: re.sub(r"\s+", "", s)  # noqa: E731
        if strip(got) != strip("".join(expected_text)):
            ok = False
            notes.append("自檢：PDFium 抽出的文字與畫出的文字不符。")
    except Exception as e:  # pragma: no cover
        ok = False
        notes.append(f"自檢：PDFium 無法開啟輸出檔（{e}）。")
    return ok, notes


# ================================================================ 進入點


def export(doc: StyledDoc, basename: str, cfg: Config, store: Store) -> OutputFile:
    params = params_from(cfg, DEFAULT_PARAMS, "ai_pdf")
    degradations: list[Degradation] = []
    fonts = _Fonts(params, degradations)
    layout = _Layout(fonts, params, degradations)
    # 先解析一個字型當 ReportLab 的初始字型，避免每頁的前置碼引用未內嵌的 Helvetica
    first_run = next((r for p in doc.pages for tb in p.text_boxes for sp in tb.paragraphs for r in sp.runs), None)
    init_face = fonts.primary(first_run.font, first_run.bold, first_run.italic, "doc")[0] if first_run else None
    buf = io.BytesIO()
    first = doc.pages[0]
    c = rl_canvas.Canvas(buf, pagesize=(first.width_pt, first.height_pt), invariant=1, pageCompression=1 if params["compress"] else 0, initialFontName=init_face.key if init_face else None)
    c.setTitle(basename)
    c.setCreator("Mortise")
    c.setProducer("Mortise ai_pdf (ReportLab)")
    drawn: list[str] = []
    layer_pages: list[set[str]] = []
    for page in doc.pages:
        c.setPageSize((page.width_pt, page.height_pt))
        painter = _Painter(c, page, layout, store, params, degradations, drawn)
        painter.page_items()
        layer_pages.append(painter.layers_used)
        c.showPage()
    c.save()
    data = buf.getvalue()
    notes: list[str] = []
    if params["layers"]:
        try:
            data = _add_layers(data, layer_pages, params["layer_names"])
        except Exception as e:  # pragma: no cover
            degradations.append(Degradation(element_id="doc", concept="圖層", action=f"無法寫入 OCG 圖層（{e}），只保留繪製順序", lossy=True))
            params = dict(params, layers=False)
    try:
        data = _set_info(data, fonts.inferred)
    except Exception as e:  # pragma: no cover
        notes.append(f"無法寫入字型對照資訊（{e}）。")
    if doc.styles:
        degradations.append(Degradation(element_id="doc", concept="樣式表", action="PDF 沒有段落與字元樣式，只保留直接設定的外觀", lossy=True))
    if doc.theme_colors:
        degradations.append(Degradation(element_id="doc", concept="主題色", action="PDF 沒有色票概念，捨棄", lossy=True))
    ok, check_notes = selfcheck(data, doc, drawn, bool(params["layers"]))
    notes += check_notes
    notes.append("Illustrator 開啟後的文字可編輯性（預期一行一個點狀文字物件）與 OCG 圖層對應未經 Illustrator 實測。")
    blob = store.put_blob(data, MIME, "pdf")
    return OutputFile(
        format=OutputFormat.AI_PDF, file=blob, filename=f"{basename}.pdf", degradations=degradations,
        fonts_used=sorted(fonts.used_names()), selfcheck_ok=ok, selfcheck_notes=notes,
    )
