"""可編輯 PDF 輸出器（OutputFormat.PDF）：把成品文件整份重建成一般 PDF 編輯軟體改得動的 PDF。

文字是真的文字（可選取、可搜尋、複製出來是正確的 Unicode）、圖片各自是獨立的影像物件、形狀與向量圖是路徑、
漸層是 PDF 的 shading，並附結構標記（Tagged PDF）。排版（斷行、行距、對齊、每行基線）與形狀、圖片、表格、
向量圖的繪製全部沿用 ai_pdf（_Fonts、_Layout、_Frame、_Painter）；這裡只換掉三件事：
文字怎麼寫進內容串流、字型怎麼內嵌、以及每個元素前後的結構標記。

元件與授權：ReportLab（BSD-3-Clause，畫圖形與影像）、pikepdf（MPL-2.0，內嵌字型、結構樹、中繼資料）、
fontTools（MIT，字型子集）、PDFium（BSD-3-Clause 加 Apache-2.0，只用來自檢）。

寫法與理由（依 docs/architecture/research/pdf-editable.md；凡是「某個編輯器打開後會怎樣」都沒有實測，
這台機器沒有 Acrobat、Foxit、PDF-XChange、Illustrator，以下標「推論」的都只是推論）：

  文字物件
    - 一段一個文字物件（BT…ET）：開頭一次 Tm，行與行用 TL 加 T*（各行起點不同時，例如置中、靠右、首行縮排，改用 Td
      做相對位移），段內不再下 Tm。理由：唯一讀得到原始碼的 Inkscape 以 BT 區塊與 Tm 為界，這樣寫一段是一個多行文字；
      PDFium 系看到的是「一行一個文字物件」；Acrobat、Foxit 的文字框是開檔時由它們自己的演算法猜的，
      沒有任何來源保證會因此併成一個文字框（推論）。可用 pdf_text_object = "line" 改回一行一個文字物件。
    - 每行整行一次畫出、同一個字型就是一個 Tj；行內換字型（中英混排）或換顏色時接續畫在同一個文字物件裡。
    - 不逐字調整位置（沒有字距微調）。左右對齊：沒有空白的行（中日韓文字）用整行一致的字距 Tc；
      有空白的行只在空白處加位移（TJ），外觀與文書軟體相同。CID 字型的字碼是兩個位元組，Tw 對它無效，所以不能用 Tw。
      pdf_justify_word_space = false 時一律用 Tc，完全沒有 TJ（研究筆記的假設：TJ 位移可能讓 Illustrator 把文字拆碎，未實測）。
    - 字與字之間的空白是真的空白字元；顏色與渲染模式在畫第一個字之前設好。

  字型
    - 一律寫成 Type0／Identity-H 的 CID 字型，由 fontTools 做子集、pikepdf 組字型物件（TrueType 外框用 CIDFontType2
      加 FontFile2，CFF 外框用 CIDFontType0 加 FontFile3）。不用 ReportLab 的 TTFont：它把字型寫成 8 位元簡單字型，
      一頁超過 256 個不同的字就拆成多個子集字型、把一行切成好幾段；它也不能內嵌 CFF 外框的 OpenType。
    - 字碼就是原字型的字形編號（子集時保留編號），每個用到的字碼都寫進 ToUnicode。
      同一個字形對應到兩個不同的字（例如不換行空白與一般空白）、或字型裡沒有這個字時，那一段另外包一層
      Span 並寫 ActualText，複製出來仍是正確的字。
    - BaseFont 用字型檔自己的 PostScript 名稱，子集加六碼前綴（每個字型、每份子集都不同）；
      從字型集合檔（TTC）取出的字型不會有「-0」這類後綴。不把替代字型改名成推論的字型：
      推論的字型名稱只記在文件資訊（MortiseFonts）、降級紀錄與還原度報告。
    - 內嵌方式不影響可編輯性：Acrobat 與 Foxit 的說明都寫「系統裝有該字型才能改字」。預設子集內嵌（檔案小）；
      pdf_font_embed = "full" 改成整個字型內嵌。字型的嵌入旗標（OS/2 fsType）禁止內嵌的不用，禁止子集的整個內嵌。

  圖片、形狀、向量圖
    - 圖片各自是獨立的影像物件（不用內嵌影像），同一張圖在多頁重複出現時共用一份資料。
    - 形狀與向量圖直接寫成頁面上的路徑，不包成 Form XObject（推論：各編輯器才能逐條選取）。
      畫到帶 vector_id 的圖片時改畫向量圖、跳過圖片；母片上的元素在每一頁各畫一次。

  結構標記（Tagged PDF；pdf_tagged = false 可關閉）
    - 每段一個標記內容（MCID），對到結構樹的 P 或 H1、H2；表格是 Table → TR → TH／TD → P；圖片與向量圖是 Figure
      （有 Alt 與 BBox）；底色、底圖、形狀、表格的底色框線、頁首頁尾頁碼、母片上的元素標成 Artifact。
    - 結構樹的順序是成品文件的閱讀順序；內容串流仍依圖層前後繪製（改順序會改變遮蓋關係）。
    - 每個結構元素的 /ID 是成品文件的元素編號，回讀驗證靠它把 PDF 裡的物件對回成品文件。
    - Catalog 寫 Lang、MarkInfo、ViewerPreferences，XMP 寫標題。Lang 由文字的書寫系統推斷（見 _language）。
    - 結構標記確定的用處是閱讀順序、螢幕報讀與重排；查不到任何來源說編輯器的文字分組會讀它，不要當成改善編輯體驗的手段。
    - 沒有用 veraPDF 驗證過 PDF/UA-1（這台機器裝不了），圖片的替代文字也只是佔位，所以預設不在 XMP 宣告 pdfuaid:part。

降階對照表（只列與 ai_pdf 不同的；其餘見 ai_pdf 開頭）：
  文字框、段落           → 一段一個文字物件；文字框本身沒有對應                → 有損（編輯器怎麼分組未實測）
  段落角色、閱讀順序     → 結構樹（H1、H2、P、Artifact 與元素順序）             → 無損（關閉結構標記時捨棄）
  表格                   → 頁面上是底色、框線、逐格文字；列欄與合併寫在結構樹   → 有損（沒有可編輯的表格物件）
  母片                   → 每頁各畫一次                                        → 有損（改一處不會連動）
  圖層分類               → 預設不寫 OCG（pdf_layers = true 才寫）              → 捨棄
"""

from __future__ import annotations

import hashlib
import io
import json
import logging
import re
from collections import Counter
from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path
from typing import Any, Optional

from reportlab.pdfgen import canvas as rl_canvas

from ..artifacts import Degradation, OutputFile, OutputFormat, Role, StyledDoc, StyledPage, StyledParagraph, StyledTable, StyledTextBox
from ..artifacts.styled_doc import vector_index
from ..config import Config
from ..pipeline.store import Store
from . import ai_pdf
from .ai_pdf import _Fonts, _Frame, _Layout, _Line, _Match, _Painter, _rl

# 這個輸出器自己的預設值（疊在 ai_pdf 的排版參數上）。設定檔 [export] 裡以 pdf_ 開頭的項目會蓋過這裡，
# 例如 pdf_tagged = false；ai_pdf 的任何參數也可以加上 pdf_ 前綴只對本輸出器生效（例如 pdf_layers = true）。
DEFAULT_PARAMS: dict[str, Any] = {
    "editor_name": "PDF 編輯軟體",
    "layers": False,  # 是否寫 OCG 圖層（一般 PDF 編輯軟體用不到；與結構標記並存時標記內容包在圖層裡面）
    "text_object": "paragraph",  # "paragraph"：一段一個文字物件；"line"：一行一個文字物件
    "font_embed": "subset",  # "subset"：只內嵌用到的字形；"full"：整個字型內嵌
    "tagged": True,  # 是否寫結構標記（Tagged PDF）
    "lang": "auto",  # 文件語言（BCP 47）；"auto" 由文字的書寫系統推斷
    "lang_cjk": "zh-Hant",  # 推斷為中文時寫的語言代碼（書寫系統分不出繁簡與地區）
    "lang_latin": "en",  # 推斷為拉丁字母文字時寫的語言代碼（書寫系統分不出是哪一種歐洲語言）
    "page_sections": True,  # 多頁文件每頁包一個 Sect
    "master_artifacts": True,  # 母片上的元素（每頁重複）標成 Artifact
    "artifact_roles": ["header", "footer", "page_number"],  # 這些角色的段落標成 Artifact，不進結構樹
    "figure_alt": "圖片",  # 圖片替代文字的開頭（後面接序號）；我們不知道圖片內容，這只是佔位
    "vector_alt": {"logo": "標誌", "icon": "圖示", "chart": "圖表", "illustration": "插畫", "unknown": "向量圖"},
    "ua_claim": False,  # 是否在 XMP 宣告 pdfuaid:part = 1（沒有用 veraPDF 驗證過，預設不宣告）
    "title": "",  # 文件標題；空字串表示用檔名
}

MIME = "application/pdf"
PRODUCER = "Mortise pdf_editable (ReportLab + pikepdf + fontTools)"
_FS_RESTRICTED, _FS_NO_SUBSET, _FS_BITMAP_ONLY, _FS_PREVIEW_PRINT = 0x0002, 0x0100, 0x0200, 0x0004
_HEADING_ROLES = {Role.TITLE: 1, Role.SUBTITLE: 2}
_ARTIFACT_SUBTYPE = {"header": " /Subtype /Header", "footer": " /Subtype /Footer", "page_number": ""}
_STANDARD_TAGS = {"Document", "Sect", "P", "H1", "H2", "H3", "H4", "H5", "H6", "Figure", "Table", "TR", "TH", "TD"}


def params_for(cfg: Optional[Config]) -> dict:
    """排版參數與 ai_pdf 相同（7A 中性渲染才與這裡一致），再疊上本輸出器的預設與設定檔的 pdf_ 項目。"""
    p = ai_pdf.params_from(cfg, ai_pdf.DEFAULT_PARAMS, "ai_pdf")
    p.update(DEFAULT_PARAMS)
    if cfg is not None:
        for key, value in (cfg.section("export") or {}).items():
            if key.startswith("pdf_"):
                p[key[4:]] = value
    p["basefont_name"] = "embedded"  # 不把替代字型冒名成推論的字型
    return p


def _n(v: float, digits: int = 4) -> str:
    s = f"{v:.{digits}f}".rstrip("0").rstrip(".")
    return "0" if s in ("", "-0") else s


# ================================================================ 字型（Type0／Identity-H）


@dataclass
class _CidFace(ai_pdf._Face):
    """給 _Layout 用的字型（欄位同 ai_pdf._Face），字寬直接取自字型檔的 hmtx，不經過 ReportLab。"""

    ps_name: str = ""  # 字型檔自己的 PostScript 名稱
    index: int = 0  # 在字型集合檔中的序號
    cff: bool = False  # 外框是 CFF（否則是 TrueType）
    upm: int = 1000
    weight: int = 400  # OS/2 usWeightClass
    italic_angle: float = 0.0
    bbox: tuple = (0, 0, 1000, 1000)  # 字型外框（1000 單位）
    cap_height: float = 0.7
    fs_type: int = 0
    variable: bool = False
    glyph_of: dict = field(default_factory=dict)  # 字的 Unicode 碼位 →（字形編號、字碼、前進寬度）；字碼就是寫進內容串流的 CID
    space: Optional[tuple] = None  # 空白字形（字型沒有某個空白字元時代用）
    notdef: tuple = (0, 0, 0.5)

    def glyph(self, ch: str) -> tuple[int, int, float, bool]:
        """回傳（字形編號、字碼、1 pt 字級下的前進寬度、字型裡是否真的有這個字）。"""
        g = self.glyph_of.get(ord(ch))
        if g is not None:
            return g[0], g[1], g[2], True
        if ch.isspace() and self.space is not None:
            return self.space[0], self.space[1], self.space[2], False
        return self.notdef[0], self.notdef[1], self.notdef[2], False

    def width(self, ch: str) -> float:
        w = self.widths.get(ch)
        if w is None:
            w = self.glyph(ch)[2]
            self.widths[ch] = w
        return w


class _CidLoader:
    """字型檔 → _CidFace。介面同 ai_pdf._load_face，交給 _Fonts 使用。"""

    _cache: dict[tuple, tuple[Optional[_CidFace], str]] = {}

    def __init__(self, degradations: list[Degradation]):
        self.deg = degradations
        self._noted: set[str] = set()

    def __call__(self, m: _Match, metric: str, rename: Optional[str]) -> tuple[Optional[_CidFace], str]:
        ck = (m.path, m.index, metric)
        if ck not in self._cache:
            try:
                self._cache[ck] = self._load(m, metric)
            except Exception as e:  # 壞檔、讀不到
                self._cache[ck] = (None, f"error: {e}")
        face, why = self._cache[ck]
        if face is None and why.startswith("license") and m.path not in self._noted:
            self._noted.add(m.path)
            self.deg.append(Degradation(element_id="doc", concept="字型的嵌入許可", action=f"「{Path(m.path).name}」的嵌入旗標不允許內嵌（{why.split(':', 1)[1].strip()}），改用下一個候選字型", lossy=True))
        if face is not None and face.fs_type & _FS_PREVIEW_PRINT and m.path not in self._noted:
            self._noted.add(m.path)
            self.deg.append(Degradation(element_id="doc", concept="字型的嵌入許可", action=f"「{face.family}」的嵌入旗標是「預覽與列印」：可以內嵌顯示，但授權上不一定允許拿內嵌的字型繼續編輯文件，需自行確認", lossy=False))
        return face, why

    @staticmethod
    def _load(m: _Match, metric: str) -> tuple[Optional[_CidFace], str]:
        from fontTools.ttLib import TTFont

        ft = TTFont(m.path, fontNumber=m.index, lazy=True) if m.path.lower().endswith((".ttc", ".otc")) else TTFont(m.path, lazy=True)
        try:
            cff = "CFF " in ft
            if "CFF2" in ft:
                return None, "error: CFF2 外框的字型不能內嵌到 PDF"
            if not cff and "glyf" not in ft:
                return None, "error: 沒有外框（點陣或彩色字型）"
            os2 = ft["OS/2"] if "OS/2" in ft else None
            fs_type = int(os2.fsType) if os2 is not None else 0
            if fs_type & _FS_RESTRICTED:
                return None, "license: 限制授權"
            if fs_type & _FS_BITMAP_ONLY:
                return None, "license: 只允許內嵌點陣"
            head, hhea = ft["head"], ft["hhea"]
            upm = head.unitsPerEm
            metrics = {"hhea": (hhea.ascent / upm, hhea.descent / upm)}
            metrics["typo"] = (os2.sTypoAscender / upm, os2.sTypoDescender / upm) if os2 is not None else metrics["hhea"]
            metrics["win"] = (os2.usWinAscent / upm, -os2.usWinDescent / upm) if os2 is not None else metrics["hhea"]
            asc, desc = metrics.get(metric, metrics["hhea"])
            names = ft["name"]
            family = names.getDebugName(16) or names.getDebugName(1) or Path(m.path).stem
            ps = re.sub(r"[^A-Za-z0-9._-]", "", names.getDebugName(6) or "") or (re.sub(r"[^A-Za-z0-9]", "", family) or "Font")
            order = ft.getGlyphOrder()
            gid_of = {name: i for i, name in enumerate(order)}
            cid_keyed = cff and hasattr(ft["CFF "].cff.topDictIndex[0], "ROS")

            def code(name: str, gid: int) -> int:
                # CID 字型的 CFF：字碼是 CFF 自己的 CID（字形名稱 cidNNNNN），不是字形編號
                if cid_keyed and name.startswith("cid") and name[3:].isdigit():
                    return int(name[3:])
                return gid

            hmtx = ft["hmtx"].metrics
            best = ft.getBestCmap() or {}
            glyph_of = {cp: (gid_of[name], code(name, gid_of[name]), hmtx[name][0] / upm) for cp, name in best.items() if name in gid_of and name in hmtx}
            nd = order[0]
            post = ft["post"] if "post" in ft else None
            face = _CidFace(
                key="MC" + hashlib.sha1(f"{m.path}|{m.index}".encode()).hexdigest()[:12], family=family, path=m.path, cmap=frozenset(glyph_of),
                ascent=round(asc * 1000.0) / 1000.0, descent=round(desc * 1000.0) / 1000.0, bold=m.weight >= 180, italic=m.slant > 0, metrics=metrics,
                ps_name=ps, index=m.index, cff=cff, upm=upm, weight=int(os2.usWeightClass) if os2 is not None else (700 if m.weight >= 180 else 400),
                italic_angle=float(post.italicAngle) if post is not None else 0.0,
                bbox=tuple(round(v * 1000.0 / upm) for v in (head.xMin, head.yMin, head.xMax, head.yMax)),
                cap_height=(getattr(os2, "sCapHeight", 0) or hhea.ascent) / upm if os2 is not None else hhea.ascent / upm,
                fs_type=fs_type, variable="fvar" in ft, glyph_of=glyph_of, space=glyph_of.get(0x20), notdef=(0, code(nd, 0), hmtx[nd][0] / upm),
            )
            return face, ""
        finally:
            ft.close()


class _FontBook:
    """這次輸出用到的字型：資源名稱、每個字型用到的字碼與它對應的文字（之後寫 ToUnicode 與做子集）。"""

    def __init__(self) -> None:
        self.names: dict[str, str] = {}  # 字型的 key → 內容串流裡的資源名稱（MF1、MF2…）
        self.faces: dict[str, _CidFace] = {}
        self.codes: dict[str, dict[int, str]] = {}  # key → {字碼: 文字}
        self.gids: dict[str, set[int]] = {}  # key → 用到的字形編號

    def encode(self, face: _CidFace, text: str) -> tuple[str, list[int], bool]:
        """一段字 →（資源名稱、字碼、複製出來的文字是否與原文一致）。不一致時呼叫端要補 ActualText。"""
        name = self.names.get(face.key)
        if name is None:
            name = self.names[face.key] = f"MF{len(self.names) + 1}"
            self.faces[face.key] = face
        codes, gids = self.codes.setdefault(face.key, {}), self.gids.setdefault(face.key, {0})
        out, faithful = [], True
        for ch in text:
            gid, code, _, has = face.glyph(ch)
            gids.add(gid)
            if has and codes.setdefault(code, ch) == ch:
                pass
            else:  # 字型沒有這個字，或這個字形已經對應到另一個字
                faithful = False
                codes.setdefault(code, ch)
            out.append(code)
        return name, out, faithful


def _subset_tag(face: _CidFace, gids: set[int]) -> str:
    digest = hashlib.sha1((face.ps_name + "|" + ",".join(map(str, sorted(gids)))).encode()).digest()
    return "".join(chr(65 + b % 26) for b in digest[:6])


def _font_program(face: _CidFace, gids: set[int], full: bool) -> bytes:
    """要內嵌的字型程式：整個字型，或只留用到的字形（保留原字形編號，字碼才不用重排）。"""
    from fontTools import subset
    from fontTools.ttLib import TTFont

    if full or face.fs_type & _FS_NO_SUBSET:
        return Path(ai_pdf._single_font_file(face.path, face.index)).read_bytes()
    kw = {"fontNumber": face.index} if face.path.lower().endswith((".ttc", ".otc")) else {}
    ft = TTFont(face.path, recalcTimestamp=False, **kw)
    opt = subset.Options()
    opt.retain_gids = True
    opt.notdef_outline = True
    opt.glyph_names = False
    opt.layout_features = []
    opt.name_IDs = [1, 2, 4, 6]
    opt.hinting = True
    opt.drop_tables += ["BDF ", "FFTM", "DSIG", "kern", "fvar", "gvar", "avar", "STAT", "HVAR", "MVAR", "cvar"]
    logger = logging.getLogger("fontTools.subset")
    level = logger.level
    logger.setLevel(logging.ERROR)  # 不認得的表（例如點陣字的 BDF）直接丟掉，不必警告
    try:
        sub = subset.Subsetter(opt)
        sub.populate(gids=sorted(gids))
        sub.subset(ft)
    finally:
        logger.setLevel(level)
    buf = io.BytesIO()
    ft.save(buf)
    ft.close()
    return buf.getvalue()


def _to_unicode(codes: dict[int, str]) -> bytes:
    """ToUnicode CMap：字碼 → Unicode（UTF-16BE）。"""
    rows = [f"<{code:04X}> <{text.encode('utf-16-be').hex().upper()}>" for code, text in sorted(codes.items())]
    out = [
        "/CIDInit /ProcSet findresource begin", "12 dict begin", "begincmap",
        "/CIDSystemInfo << /Registry (Adobe) /Ordering (UCS) /Supplement 0 >> def",
        "/CMapName /Adobe-Identity-UCS def", "/CMapType 2 def",
        "1 begincodespacerange", "<0000> <FFFF>", "endcodespacerange",
    ]
    for i in range(0, len(rows), 100):
        chunk = rows[i:i + 100]
        out += [f"{len(chunk)} beginbfchar", *chunk, "endbfchar"]
    out += ["endcmap", "CMapName currentdict /CMap defineresource pop", "end", "end"]
    return "\n".join(out).encode("ascii")


def _width_array(face: _CidFace, codes: dict[int, str]):
    """/W：用到的字碼的字寬（1000 單位）。寫到小數三位，與排版用的字寬（hmtx ÷ unitsPerEm）一致。"""
    import pikepdf

    width_of = {code: adv for _, code, adv in face.glyph_of.values()}
    width_of.setdefault(face.notdef[1], face.notdef[2])
    out = pikepdf.Array()
    run: list = []
    start = prev = None
    for code in sorted(set(codes) | {face.notdef[1]}):
        w = round(width_of.get(code, face.notdef[2]) * 1000.0, 3)
        value = int(w) if w == int(w) else Decimal(f"{w:.3f}")
        if prev is not None and code == prev + 1:
            run.append(value)
        else:
            if run:
                out.append(start)
                out.append(pikepdf.Array(run))
            start, run = code, [value]
        prev = code
    if run:
        out.append(start)
        out.append(pikepdf.Array(run))
    return out


def _embed_fonts(pdf, book: _FontBook, params: dict) -> dict[str, Any]:
    """把用到的字型組成 Type0／Identity-H 字型物件。回傳「資源名稱 → 字型物件」。"""
    import pikepdf
    from pikepdf import Array, Dictionary, Name, String

    out: dict[str, Any] = {}
    full = params["font_embed"] == "full"
    for key, res in book.names.items():
        face, codes, gids = book.faces[key], book.codes[key], book.gids[key]
        whole = full or bool(face.fs_type & _FS_NO_SUBSET)
        base = face.ps_name if whole else f"{_subset_tag(face, gids)}+{face.ps_name}"
        program = pdf.make_stream(_font_program(face, gids, full))
        flags = 4 | (64 if (face.italic or face.italic_angle) else 0)
        fd = Dictionary(
            Type=Name.FontDescriptor, FontName=Name("/" + base), Flags=flags, FontBBox=Array([int(v) for v in face.bbox]),
            ItalicAngle=Decimal(_n(face.italic_angle, 2)), Ascent=round(face.ascent * 1000), Descent=round(face.descent * 1000),
            CapHeight=round(face.cap_height * 1000), StemV=round(50 + (face.weight / 65.0) ** 2),
        )
        if face.cff:
            program.Subtype = Name.OpenType
            fd.FontFile3 = program
        else:
            fd.FontFile2 = program
        cid = Dictionary(
            Type=Name.Font, Subtype=Name.CIDFontType0 if face.cff else Name.CIDFontType2, BaseFont=Name("/" + base),
            CIDSystemInfo=Dictionary(Registry=String("Adobe"), Ordering=String("Identity"), Supplement=0),
            FontDescriptor=pdf.make_indirect(fd), DW=1000, W=_width_array(face, codes),
        )
        if not face.cff:
            cid.CIDToGIDMap = Name.Identity
        out[res] = pdf.make_indirect(Dictionary(
            Type=Name.Font, Subtype=Name.Type0, BaseFont=Name("/" + base + ("-Identity-H" if face.cff else "")), Encoding=Name("/Identity-H"),
            DescendantFonts=Array([pdf.make_indirect(cid)]), ToUnicode=pdf.make_stream(_to_unicode(codes)),
        ))
    return out


# ================================================================ 結構標記


@dataclass
class _Mark:
    """內容串流裡的一塊標記內容（一個 MCID），之後掛到結構樹上。"""

    page: int
    mcid: int
    tag: str  # P、H1、H2、Figure
    element_id: str  # 成品文件的元素編號（段落、圖片、向量圖），寫進結構元素的 /ID
    owner: str  # 所屬的文字框、表格、圖片的編號（排閱讀順序用）
    cell: Optional[tuple[int, int]] = None  # 表格儲存格（列、欄）
    lang: Optional[str] = None
    alt: Optional[str] = None
    bbox: Optional[tuple[float, float, float, float]] = None  # PDF 座標（左、下、右、上）


@dataclass
class _Elem:
    tag: str
    kids: list = field(default_factory=list)  # _Elem 或 _Mark
    element_id: Optional[str] = None
    lang: Optional[str] = None
    alt: Optional[str] = None
    bbox: Optional[tuple] = None
    attrs: dict = field(default_factory=dict)  # 表格屬性（RowSpan、ColSpan、Scope）


def _script_counts(text: str) -> Counter:
    c: Counter = Counter()
    for ch in text:
        o = ord(ch)
        if 0x3040 <= o <= 0x30FF:
            c["kana"] += 1
        elif 0xAC00 <= o <= 0xD7AF or 0x1100 <= o <= 0x11FF:
            c["hangul"] += 1
        elif 0x4E00 <= o <= 0x9FFF or 0x3400 <= o <= 0x4DBF or 0x20000 <= o <= 0x2FA1F:
            c["han"] += 1
        elif ch.isalpha() and o < 0x250:
            c["latin"] += 1
    return c


def _language(text: str, params: dict) -> Optional[str]:
    """由書寫系統推斷語言代碼。這是演算法的計數，不是語言辨識：有假名當日文、有諺文當韓文、
    其餘漢字當中文（繁簡與地區分不出來，用設定值）、拉丁字母用設定值。沒有可判斷的字就回傳 None。"""
    c = _script_counts(text)
    total = sum(c.values())
    if not total:
        return None
    if c["kana"] >= 0.05 * total:
        return "ja"
    if c["hangul"] >= 0.3 * total:
        return "ko"
    if c["han"] >= c["latin"] * 0.5 and c["han"] > 0:
        return params["lang_cjk"]
    return params["lang_latin"]


def _doc_text(doc: StyledDoc) -> str:
    parts = []
    for page in doc.pages:
        for tb in page.text_boxes:
            parts += [r.text for sp in tb.paragraphs for r in sp.runs]
        for t in page.tables:
            parts += [r.text for cell in t.cells for sp in cell.paragraphs for r in sp.runs]
    return "".join(parts)


def _reading_rank(page: StyledPage) -> dict[str, int]:
    """元素編號 → 閱讀順序。成品文件的閱讀順序沒列到的元素排在後面，依圖層由下到上。"""
    rank = {eid: i for i, eid in enumerate(page.reading_order)}
    rest = sorted([(x.z, x.id) for x in [*page.text_boxes, *page.tables, *page.images] if x.id not in rank])
    for _, eid in rest:
        rank.setdefault(eid, len(rank))
    return rank


def _assign_tags(doc: StyledDoc, params: dict, master_ids: set[str], doc_lang: Optional[str]) -> dict[tuple, tuple[str, Optional[str]]]:
    """每個段落的標記：（頁、所屬元素、儲存格、段落編號）→（標記、語言）。
    標記是 P、H1、H2，或 "Artifact …"（不進結構樹）。標題依閱讀順序給級數：標題是 H1；副標是 H2，
    但前面還沒出現過 H1 時也給 H1（PDF/UA 要求標題從 H1 起、不跳級）。"""
    artifact_roles = set(params["artifact_roles"])
    out: dict[tuple, tuple[str, Optional[str]]] = {}
    seen_h1 = False

    def tag_of(sp: StyledParagraph, owner: str) -> str:
        nonlocal seen_h1
        if params["master_artifacts"] and owner in master_ids:
            return "/Artifact <</Type /Pagination>>"
        if sp.role.value in artifact_roles:
            return f"/Artifact <</Type /Pagination{_ARTIFACT_SUBTYPE.get(sp.role.value, '')}>>"
        level = _HEADING_ROLES.get(sp.role)
        if level is None:
            return "P"
        if level == 1 or not seen_h1:
            seen_h1 = True
            return "H1"
        return "H2"

    def lang_of(sp: StyledParagraph) -> Optional[str]:
        lang = _language("".join(r.text for r in sp.runs), params)
        return lang if (lang and doc_lang and lang != doc_lang) else None

    for page in doc.pages:
        rank = _reading_rank(page)
        owners: list = sorted([*page.text_boxes, *page.tables], key=lambda x: rank.get(x.id, 1 << 30))
        for o in owners:
            if isinstance(o, StyledTable):
                for cell in sorted(o.cells, key=lambda c: (c.row, c.col)):
                    for sp in cell.paragraphs:
                        out[(page.page_index, o.id, (cell.row, cell.col), sp.id)] = ("P", lang_of(sp))
            else:
                for sp in o.paragraphs:
                    out[(page.page_index, o.id, None, sp.id)] = (tag_of(sp, o.id), lang_of(sp))
    return out


def _struct_tree(doc: StyledDoc, marks: list[_Mark], params: dict) -> _Elem:
    """結構樹：文件 →（多頁時每頁一個 Sect）→ 依閱讀順序的段落、表格、圖。"""
    root = _Elem("Document")
    multi = params["page_sections"] and len(doc.pages) > 1
    by_page: dict[int, list[_Mark]] = {}
    for m in marks:
        by_page.setdefault(m.page, []).append(m)
    for page in doc.pages:
        ms = by_page.get(page.page_index, [])
        parent = _Elem("Sect") if multi else root
        rank = _reading_rank(page)
        tables = {t.id: t for t in page.tables}
        owners: list[str] = []
        for m in ms:
            if m.owner not in owners:
                owners.append(m.owner)
        for tid in tables:  # 沒有文字的表格也要有結構元素
            if tid not in owners:
                owners.append(tid)
        owners.sort(key=lambda o: rank.get(o, 1 << 30))
        for owner in owners:
            mine = [m for m in ms if m.owner == owner]
            t = tables.get(owner)
            if t is None:
                for m in mine:
                    parent.kids.append(_Elem(m.tag, [m], element_id=m.element_id, lang=m.lang, alt=m.alt, bbox=m.bbox))
                continue
            table = _Elem("Table", element_id=t.id)
            rows: dict[int, _Elem] = {}
            for cell in sorted(t.cells, key=lambda c: (c.row, c.col)):
                attrs: dict = {}
                if cell.row_span > 1:
                    attrs["RowSpan"] = cell.row_span
                if cell.col_span > 1:
                    attrs["ColSpan"] = cell.col_span
                head = cell.header or cell.row < t.header_rows
                if head:
                    attrs["Scope"] = "Column"
                td = _Elem("TH" if head else "TD", attrs=attrs)
                for m in mine:
                    if m.cell == (cell.row, cell.col):
                        td.kids.append(_Elem(m.tag, [m], element_id=m.element_id, lang=m.lang))
                rows.setdefault(cell.row, _Elem("TR")).kids.append(td)
            table.kids = [rows[r] for r in sorted(rows)]
            parent.kids.append(table)
        if multi:
            root.kids.append(parent)
    return root


def _write_struct_tree(pdf, root: _Elem, lang: Optional[str]) -> None:
    """用 pikepdf 把結構樹寫進檔案：StructTreeRoot、各結構元素、ParentTree、IDTree、每頁的 StructParents。"""
    from pikepdf import Array, Dictionary, Name, String

    tree = pdf.make_indirect(Dictionary(Type=Name.StructTreeRoot))
    parents: dict[int, dict[int, Any]] = {}  # 頁 → {MCID: 結構元素}
    ids: dict[str, Any] = {}

    def build(e: _Elem, parent):
        obj = pdf.make_indirect(Dictionary(Type=Name.StructElem, S=Name("/" + e.tag), P=parent))
        kids = Array()
        pages = {k.page for k in e.kids if isinstance(k, _Mark)}
        for k in e.kids:
            if isinstance(k, _Mark):
                parents.setdefault(k.page, {})[k.mcid] = obj
                if len(pages) == 1:
                    kids.append(k.mcid)
                else:  # 同一個元素的內容跨頁：每塊各自註明頁面
                    kids.append(Dictionary(Type=Name.MCR, Pg=pdf.pages[k.page].obj, MCID=k.mcid))
            else:
                kids.append(build(k, obj))
        if len(pages) == 1:
            obj.Pg = pdf.pages[next(iter(pages))].obj
        obj.K = kids[0] if len(kids) == 1 else kids
        if e.element_id and e.element_id not in ids:
            obj.ID = String(e.element_id)
            ids[e.element_id] = obj
        if e.lang:
            obj.Lang = String(e.lang)
        if e.alt is not None:
            obj.Alt = String(e.alt)
        if e.bbox is not None:
            obj.A = Dictionary(O=Name.Layout, Placement=Name.Block, BBox=Array([Decimal(_n(v, 3)) for v in e.bbox]))
        elif e.attrs:
            a = Dictionary(O=Name.Table)
            for key, value in e.attrs.items():
                a[Name("/" + key)] = Name("/" + value) if isinstance(value, str) else value
            obj.A = a
        return obj

    tree.K = build(root, tree)
    nums = Array()
    for i, page in enumerate(pdf.pages):
        entries = parents.get(i, {})
        nums.append(i)
        nums.append(pdf.make_indirect(Array([entries[m] for m in range(max(entries) + 1)] if entries else [])))
        page.obj.StructParents = i
        page.obj.Tabs = Name.S
    tree.ParentTree = pdf.make_indirect(Dictionary(Nums=nums))
    tree.ParentTreeNextKey = len(pdf.pages)
    if ids:
        names = Array()
        for key in sorted(ids, key=lambda s: s.encode("utf-8")):
            names.append(String(key))
            names.append(ids[key])
        tree.IDTree = pdf.make_indirect(Dictionary(Names=names))
    pdf.Root.StructTreeRoot = tree
    pdf.Root.MarkInfo = Dictionary(Marked=True)
    if lang:
        pdf.Root.Lang = String(lang)


# ================================================================ 繪製


class _Canvas(rl_canvas.Canvas):
    """ReportLab 每頁開頭會寫一個只設字型、不畫字的文字物件，引用未內嵌的 Helvetica。
    本輸出器的字型全部自己內嵌，不需要它；拿掉後檔案裡就沒有未內嵌的字型。"""

    def _make_preamble(self):
        super()._make_preamble()
        self._preamble = re.sub(r"\s*BT \S+ 12 Tf 14\.4 TL ET", "", self._preamble)


class _EditablePainter(_Painter):
    """沿用 ai_pdf 的繪製；改寫文字的寫法（一段一個文字物件、CID 字型），並在每個元素前後寫結構標記。"""

    def __init__(self, *args, book: _FontBook, tags: dict, master_ids: set[str], marks: list[_Mark], counters: dict, **kw):
        super().__init__(*args, **kw)
        self.book = book
        self.tags = tags
        self.master_ids = master_ids
        self.marks = marks  # 整份文件共用
        self.counters = counters  # 整份文件共用的序號（圖片、向量圖的替代文字用）
        self.tagged = bool(self.p["tagged"])
        self.fonts_on_page: set[str] = set()
        self.mcid = 0
        self._ctx: Optional[tuple] = None  # 目前在畫哪個文字框或儲存格
        self._open: list[bool] = []  # begin 是否寫了 BDC（end 要不要寫 EMC）

    # ---------- 結構標記
    def _mark(self, tag: str, element_id: str, owner: str, **kw) -> None:
        self.c.addLiteral(f"/{tag} <</MCID {self.mcid}>> BDC")
        self.marks.append(_Mark(page=self.page.page_index, mcid=self.mcid, tag=tag, element_id=element_id, owner=owner, **kw))
        self.mcid += 1

    def _figure(self, kind: str, item) -> None:
        if self.p["master_artifacts"] and item.id in self.master_ids:
            self.c.addLiteral("/Artifact <</Type /Pagination>> BDC")
            return
        if kind == "vector":
            label = self.p["vector_alt"].get(item.kind, self.p["vector_alt"].get("unknown", "向量圖"))
            owner = item.image_id  # 閱讀順序裡排的是它對應的那張圖片
        else:
            label, owner = self.p["figure_alt"], item.id
        self.counters[label] = self.counters.get(label, 0) + 1
        b = item.box
        fr = _Frame(b, self.H)
        pts = [fr.pt(x, y) for x, y in ((0, 0), (b.w, 0), (b.w, b.h), (0, b.h))]
        bbox = (min(p[0] for p in pts), min(p[1] for p in pts), max(p[0] for p in pts), max(p[1] for p in pts))
        self._mark("Figure", item.id, owner, alt=f"{label} {self.counters[label]}", bbox=bbox)

    def begin(self, kind: str, item: Any = None, part: Any = None) -> None:
        if kind in ("text_box", "cell"):
            self._ctx = (kind, item, part)
            self._open.append(False)
            return
        self._open.append(self.tagged)
        if not self.tagged:
            return
        if kind == "background":
            self.c.addLiteral(f"/Artifact <</Type /Background /BBox [0 0 {_n(self.page.width_pt)} {_n(self.H)}]>> BDC")
        elif kind in ("image", "vector") and item is not None:
            self._figure(kind, item)
        else:  # 形狀、表格的底色與框線：版面裝飾
            self.c.addLiteral("/Artifact <</Type /Layout>> BDC")

    def end(self) -> None:
        if self._open.pop():
            self.c.addLiteral("EMC")
        else:
            self._ctx = None

    # ---------- 文字
    def text_lines(self, lines: list[_Line], frame: _Frame, ox: float, oy: float) -> None:
        """一段一個文字物件（或設定成一行一個）；每段一塊標記內容。"""
        self.enter("text")
        kind, item, part = self._ctx or ("text_box", None, None)
        owner = item.id if item is not None else ""
        cell = (part.row, part.col) if kind == "cell" else None
        groups: list[list[_Line]] = []
        for ln in lines:
            if not ln.segs:
                continue
            if groups and groups[-1][0].paragraph == ln.paragraph:
                groups[-1].append(ln)
            else:
                groups.append([ln])
        for group in groups:
            pid = group[0].paragraph
            tag, lang = self.tags.get((self.page.page_index, owner, cell, pid), ("P", None))
            if self.tagged:
                if tag.startswith("/Artifact"):
                    self.c.addLiteral(f"{tag} BDC")
                else:
                    self._mark(tag, pid, owner, cell=cell, lang=lang)
            if self.p["text_object"] == "line":
                for ln in group:
                    self._text_object([ln], frame, ox, oy)
            else:
                self._text_object(group, frame, ox, oy)
            if self.tagged:
                self.c.addLiteral("EMC")

    def _text_object(self, lines: list[_Line], frame: _Frame, ox: float, oy: float) -> None:
        """一個 BT…ET：開頭一次 Tm，之後各行用 TL 加 T*（起點相同）或 Td（起點不同）接下去。"""
        t = self.c.beginText()
        code = t._code
        code[:] = [op for op in code if not op.rstrip().endswith("Tm")]  # beginText 會先寫一個原點 Tm；去掉，只留 BT
        first = lines[0]
        m = frame.upright(ox + first.x, oy + first.baseline)
        code.append(" ".join(_n(v, 6) for v in m[:4]) + f" {_n(m[4])} {_n(m[5])} Tm")
        font = color = None
        mode, tc, leading = 0, 0.0, None
        prev = first
        for ln in lines:
            if ln is not first:
                dx, dy = ln.x - prev.x, ln.baseline - prev.baseline
                if abs(dx) < 5e-5:
                    if leading is None or abs(dy - leading) > 5e-5:
                        code.append(f"{_n(dy)} TL")
                        leading = dy
                    code.append("T*")
                else:
                    code.append(f"{_n(dx)} {_n(-dy)} Td")
                prev = ln
            if abs(ln.char_space - tc) > 5e-5:
                code.append(f"{_n(ln.char_space)} Tc")
                tc = ln.char_space
            for sg in ln.segs:
                res, codes, faithful = self.book.encode(sg.face, sg.text)
                self.fonts_on_page.add(res)
                if font != (res, sg.size):
                    code.append(f"/{res} {_n(sg.size)} Tf")
                    font = (res, sg.size)
                if color != sg.run.color:
                    t.setFillColor(_rl(sg.run.color))
                    color = sg.run.color
                want = 2 if sg.faux_bold else 0
                if want != mode:
                    if want == 2:  # 沒有粗體字型檔：填色加描邊模擬
                        t.setStrokeColor(_rl(sg.run.color))
                        code.append(f"{_n(sg.size * self.p['faux_bold_stroke_ratio'], 3)} w")
                    code.append(f"{want} Tr")
                    mode = want
                if not faithful:  # 字形與文字對不上：用 ActualText 記下真正的文字
                    code.append(f"/Span <</ActualText <{(chr(0xFEFF) + sg.text).encode('utf-16-be').hex().upper()}>>> BDC")
                code.append(self._show(sg.text, codes, ln.word_space / sg.size * 1000.0 if ln.word_space else 0.0))
                if not faithful:
                    code.append("EMC")
                self.drawn.append(sg.text)
                self.layout.fonts.used[sg.face.family] = sg.face
        if mode:
            code.append("0 Tr")
        if tc:
            code.append("0 Tc")
        self.c.drawText(t)

    @staticmethod
    def _show(text: str, codes: list[int], space_shift: float) -> str:
        """一段字的顯示運算。space_shift 不為 0 時（左右對齊、有空白的行）在每個空白之後加位移（TJ），其餘用 Tj。"""
        if not space_shift or " " not in text:
            return "<" + "".join(f"{c:04X}" for c in codes) + "> Tj"
        parts, cur = [], []
        for ch, c in zip(text, codes):
            cur.append(f"{c:04X}")
            if ch == " ":
                parts.append("<" + "".join(cur) + ">" + _n(-space_shift, 3))
                cur = []
        if cur:
            parts.append("<" + "".join(cur) + ">")
        return "[" + "".join(parts) + "] TJ"

    # ---------- 降級紀錄
    def note_text_box(self, tb: StyledTextBox, lines: list[_Line]) -> None:
        drawn = [ln for ln in lines if ln.segs]
        if drawn:
            n_par = len({ln.paragraph for ln in drawn})
            how = f"{n_par} 個文字物件（一段一個，共 {len(drawn)} 行）" if self.p["text_object"] != "line" else f"{len(drawn)} 個逐行的文字物件"
            self.deg.append(Degradation(element_id=tb.id, concept="段落與文字框", action=f"PDF 沒有文字框與段落屬性，寫成 {how}；編輯軟體會不會把它們併成一個文字框，由各軟體自己決定", lossy=True))
        if lines and lines[-1].baseline - lines[-1].descent > tb.box.h + 0.5 * lines[-1].size:
            self.deg.append(Degradation(element_id=tb.id, concept="文字框高度", action="排出的文字超出文字框下緣（PDF 沒有文字框，照樣畫出）", lossy=False))

    def note_reading_order(self) -> None:
        if not self.tagged:
            super().note_reading_order()


# ================================================================ 後處理與自檢


def _finish(data: bytes, doc: StyledDoc, book: _FontBook, page_fonts: list[set[str]], marks: list[_Mark], params: dict, title: str, lang: Optional[str], inferred: dict[str, str]) -> bytes:
    """ReportLab 寫出的 PDF → 補上字型、結構樹、中繼資料。"""
    import pikepdf
    from pikepdf import Dictionary, Name, String

    pdf = pikepdf.open(io.BytesIO(data))
    fonts = _embed_fonts(pdf, book, params)
    for page, used in zip(pdf.pages, page_fonts):
        res = page.obj.get("/Resources")
        if res is None:
            page.obj.Resources = res = Dictionary()
        if used:
            res.Font = Dictionary({f"/{name}": fonts[name] for name in sorted(used)})
        elif "/Font" in res:
            del res["/Font"]
    if params["tagged"]:
        _write_struct_tree(pdf, _struct_tree(doc, marks, params), lang)
    elif lang:
        pdf.Root.Lang = String(lang)
    pdf.Root.ViewerPreferences = Dictionary(DisplayDocTitle=True)
    pdf.docinfo["/Title"] = String(title)
    pdf.docinfo["/Creator"] = String("Mortise")
    pdf.docinfo["/Producer"] = String(PRODUCER)
    pdf.docinfo["/MortiseFonts"] = String(json.dumps(inferred, ensure_ascii=False, sort_keys=True))
    with pdf.open_metadata(set_pikepdf_as_editor=False, update_docinfo=False) as meta:
        meta["dc:title"] = title
        meta["dc:format"] = MIME
        meta["pdf:Producer"] = PRODUCER
        meta["xmp:CreatorTool"] = "Mortise"
        if lang:
            meta["dc:language"] = [lang]
        if params["tagged"] and params["ua_claim"]:
            meta["pdfuaid:part"] = "1"
    out = io.BytesIO()
    pdf.save(out, deterministic_id=True, min_version="1.7", compress_streams=True, object_stream_mode=pikepdf.ObjectStreamMode.generate,
             stream_decode_level=pikepdf.StreamDecodeLevel.generalized)
    return out.getvalue()


def struct_problems(pdf) -> list[str]:
    """檢查結構樹是否自洽（pikepdf 讀回）：每塊標記內容恰好掛在一個結構元素上、ParentTree 對得回去、
    標題不跳級、Figure 有替代文字、表格只含 TR、TR 只含 TH／TD。回傳問題清單（空的表示沒問題）。"""
    import pikepdf

    out: list[str] = []
    root = pdf.Root.get("/StructTreeRoot")
    if root is None:
        return ["沒有結構樹（StructTreeRoot）。"]
    if not bool(pdf.Root.get("/MarkInfo", {}).get("/Marked", False)):
        out.append("Catalog 沒有 MarkInfo/Marked。")
    if "/Lang" not in pdf.Root:
        out.append("Catalog 沒有 Lang。")
    page_no = {p.obj.objgen: i for i, p in enumerate(pdf.pages)}
    in_content: list[set[int]] = []
    for page in pdf.pages:
        found: set[int] = set()
        for operands, op in pikepdf.parse_content_stream(page):
            if str(op) == "BDC" and len(operands) == 2 and isinstance(operands[1], pikepdf.Dictionary) and "/MCID" in operands[1]:
                mcid = int(operands[1].MCID)
                if mcid in found:
                    out.append(f"第 {page_no[page.obj.objgen] + 1} 頁的 MCID {mcid} 重複。")
                found.add(mcid)
        in_content.append(found)
    in_tree: dict[tuple[int, int], Any] = {}
    last_level = 0

    def walk(e, parent_tag: str, pg) -> None:
        nonlocal last_level
        tag = str(e.get("/S", ""))[1:]
        if tag not in _STANDARD_TAGS:
            out.append(f"結構元素的種類「{tag}」不是標準標籤。")
        if tag in ("H1", "H2", "H3", "H4", "H5", "H6"):
            level = int(tag[1])
            if level > last_level + 1:
                out.append(f"標題跳級：{tag} 前面最高只到 H{last_level}。")
            last_level = level
        if tag == "Figure" and not str(e.get("/Alt", "")):
            out.append("Figure 沒有替代文字。")
        if tag == "TR" and parent_tag != "Table":
            out.append("TR 不在 Table 裡。")
        if tag in ("TH", "TD") and parent_tag != "TR":
            out.append(f"{tag} 不在 TR 裡。")
        if parent_tag == "Table" and tag != "TR":
            out.append(f"Table 裡出現 {tag}。")
        pg = e.get("/Pg", pg)
        kids = e.get("/K")
        if kids is None:
            return
        for k in (kids if isinstance(kids, pikepdf.Array) else [kids]):
            if isinstance(k, pikepdf.Dictionary) and k.get("/Type") == pikepdf.Name.MCR:
                k_pg, mcid = k.get("/Pg", pg), int(k.MCID)
            elif isinstance(k, pikepdf.Dictionary):
                walk(k, tag, pg)
                continue
            else:
                k_pg, mcid = pg, int(k)
            if k_pg is None:
                out.append(f"MCID {mcid} 的結構元素沒有指明頁面。")
                continue
            key = (page_no.get(k_pg.objgen, -1), mcid)
            if key in in_tree:
                out.append(f"第 {key[0] + 1} 頁的 MCID {mcid} 掛在兩個結構元素上。")
            in_tree[key] = e

    top = root.get("/K")
    for k in (top if isinstance(top, pikepdf.Array) else [top] if top is not None else []):
        walk(k, "", None)
    nums = root.get("/ParentTree", {}).get("/Nums", [])
    parent_tree = {int(nums[i]): nums[i + 1] for i in range(0, len(nums), 2)}
    for i, page in enumerate(pdf.pages):
        tree_ids = {m for (p, m) in in_tree if p == i}
        if tree_ids != in_content[i]:
            out.append(f"第 {i + 1} 頁：內容串流的標記內容 {sorted(in_content[i])} 與結構樹 {sorted(tree_ids)} 不一致。")
        sp = page.obj.get("/StructParents")
        arr = parent_tree.get(int(sp)) if sp is not None else None
        if in_content[i] and arr is None:
            out.append(f"第 {i + 1} 頁沒有 StructParents 或 ParentTree 沒有這一頁。")
            continue
        for mcid in sorted(in_content[i] & tree_ids):
            if arr is None or mcid >= len(arr) or arr[mcid].objgen != in_tree[(i, mcid)].objgen:
                out.append(f"第 {i + 1} 頁的 MCID {mcid} 在 ParentTree 裡對不回它的結構元素。")
    return out


def selfcheck(data: bytes, doc: StyledDoc, expected_text: list[list[str]], params: dict) -> tuple[bool, list[str]]:
    """重新讀入：頁數與尺寸、所有文字可取出且與畫出的一致（逐頁）、字型已內嵌並有 ToUnicode、結構樹存在且自洽。"""
    import pikepdf
    import pypdfium2 as pdfium
    import pypdfium2.raw as raw

    from ..tools.pdfium import LOCK

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
        for name, f in dict(pg.obj.get("/Resources", {}).get("/Font", {})).items():
            base = str(f.get("/BaseFont", name))
            desc = f.DescendantFonts[0].get("/FontDescriptor") if f.get("/Subtype") == "/Type0" and "/DescendantFonts" in f else f.get("/FontDescriptor")
            if desc is None or not ("/FontFile2" in desc or "/FontFile3" in desc or "/FontFile" in desc):
                ok = False
                notes.append(f"自檢：第 {i + 1} 頁字型 {base} 沒有內嵌。")
            if "/ToUnicode" not in f:
                ok = False
                notes.append(f"自檢：第 {i + 1} 頁字型 {base} 沒有 ToUnicode。")
    if params["tagged"]:
        problems = struct_problems(pdf)
        if problems:
            ok = False
            notes += [f"自檢（結構樹）：{p}" for p in problems[:20]]
    try:
        strip = lambda s: re.sub(r"\s+", "", s)  # noqa: E731
        with LOCK:
            d = pdfium.PdfDocument(data)
            try:
                if params["tagged"] and not raw.FPDFCatalog_IsTagged(d.raw):
                    ok = False
                    notes.append("自檢：PDFium 不認為這份檔案有結構標記。")
                for i in range(min(len(d), len(expected_text))):
                    page = d[i]
                    tp = page.get_textpage()
                    got = tp.get_text_range()
                    tp.close()
                    page.close()
                    if strip(got) != strip("".join(expected_text[i])):
                        ok = False
                        notes.append(f"自檢：第 {i + 1} 頁 PDFium 取出的文字與畫出的文字不符。")
            finally:
                d.close()
    except Exception as e:  # pragma: no cover
        ok = False
        notes.append(f"自檢：PDFium 無法開啟輸出檔（{e}）。")
    return ok, notes


# ================================================================ 進入點


def export(doc: StyledDoc, basename: str, cfg: Config, store: Store) -> OutputFile:
    params = params_for(cfg)
    degradations: list[Degradation] = []
    fonts = _Fonts(params, degradations, loader=_CidLoader(degradations))
    slack = float(cfg.section("settings").get("box_width_slack_pt", 0.0)) if (cfg is not None and params["wrap_slack_from_settings"]) else 0.0
    layout = _Layout(fonts, params, degradations, slack)
    lang = params["lang"] if params["lang"] != "auto" else (_language(_doc_text(doc), params) or params["lang_latin"])
    master_ids = {eid for m in doc.masters for eid in m.member_ids}
    tags = _assign_tags(doc, params, master_ids, lang)
    vectors = vector_index(doc) if params.get("vectors", True) else None
    book = _FontBook()
    marks: list[_Mark] = []
    counters: dict[str, int] = {}
    buf = io.BytesIO()
    first = doc.pages[0]
    c = _Canvas(buf, pagesize=(first.width_pt, first.height_pt), invariant=1, pageCompression=1 if params["compress"] else 0)
    drawn: list[list[str]] = []
    layer_pages: list[set[str]] = []
    page_fonts: list[set[str]] = []
    for page in doc.pages:
        c.setPageSize((page.width_pt, page.height_pt))
        text: list[str] = []
        painter = _EditablePainter(c, page, layout, store, params, degradations, text, vectors, book=book, tags=tags, master_ids=master_ids, marks=marks, counters=counters)
        painter.page_items()
        drawn.append(text)
        layer_pages.append(painter.layers_used)
        page_fonts.append(painter.fonts_on_page)
        c.showPage()
    c.save()
    data = buf.getvalue()
    notes: list[str] = []
    title = params["title"] or basename
    data = _finish(data, doc, book, page_fonts, marks, params, title, lang, fonts.inferred)
    if params["layers"]:
        try:
            data = ai_pdf._add_layers(data, layer_pages, params["layer_names"])
        except Exception as e:  # pragma: no cover
            degradations.append(Degradation(element_id="doc", concept="圖層", action=f"無法寫入 OCG 圖層（{e}），只保留繪製順序", lossy=True))
    _doc_degradations(doc, params, vectors or {}, book, degradations)
    ok, check_notes = selfcheck(data, doc, drawn, params)
    notes += check_notes
    if fonts.inferred:
        notes.append("推論的字型 → 實際內嵌的字型：" + "、".join(f"{a} → {b}" for a, b in sorted(fonts.inferred.items())) + "。BaseFont 寫的是內嵌字型自己的名稱；這份對照也記在文件資訊的 MortiseFonts。")
    notes.append(f"文件語言寫成「{lang}」：由文字的書寫系統推斷，分不出繁簡、地區與歐洲語言的種類（可用 pdf_lang 指定）。")
    if params["tagged"]:
        notes.append("結構標記只用 PDFium 與 pikepdf 讀回檢查過；沒有用 veraPDF 驗證 PDF/UA-1。圖片的替代文字是依種類與順序產生的佔位文字，不是內容描述。")
    notes.append("這份 PDF 在 Acrobat、Foxit、PDF-XChange 等編輯軟體裡的編輯行為（文字會不會併成段落文字框、能不能改字）沒有實測；要改字，電腦上需要安裝檔案中寫入的字型。")
    blob = store.put_blob(data, MIME, "pdf")
    return OutputFile(
        format=OutputFormat.PDF, file=blob, filename=f"{basename}_editable.pdf", degradations=degradations,
        fonts_used=sorted(fonts.used_names()), selfcheck_ok=ok, selfcheck_notes=notes,
    )


def _doc_degradations(doc: StyledDoc, params: dict, vectors: dict, book: _FontBook, deg: list[Degradation]) -> None:
    """整份文件層級的降級紀錄：成品文件有、PDF 沒有對應的概念。"""
    if doc.styles:
        deg.append(Degradation(element_id="doc", concept="樣式表", action="PDF 沒有段落與字元樣式，只保留直接設定的外觀", lossy=True))
    if doc.theme_colors:
        deg.append(Degradation(element_id="doc", concept="主題色", action="PDF 沒有色票概念，捨棄", lossy=True))
    if doc.masters:
        deg.append(Degradation(element_id="doc", concept="母片", action="PDF 沒有母片：母片上的元素在每一頁各畫一次，改一處不會連動其他頁" + ("；結構樹裡標成 Artifact" if params["tagged"] and params["master_artifacts"] else ""), lossy=True))
    if any(tb.paragraphs for p in doc.pages for tb in p.text_boxes):
        deg.append(Degradation(element_id="doc", concept="行距、段距、縮排、對齊", action="PDF 沒有這些段落屬性：已換算成每一行的位置，屬性本身不在檔案裡", lossy=True))
    drawn = [v for p in doc.pages for im in p.images if im.vector_id and (v := vectors.get(im.vector_id)) is not None and v.paths]
    if any(vp.primitive is not None for v in drawn for vp in v.paths):
        deg.append(Degradation(element_id="doc", concept="向量圖的基本圖形", action="PDF 沒有矩形、圓、橢圓等基本圖形物件，一律寫成路徑（外觀不變）", lossy=False))
    if any(f.variable for f in book.faces.values()):
        deg.append(Degradation(element_id="doc", concept="可變字型", action="PDF 不能內嵌可變字型的變化軸，只內嵌預設樣式；字重或寬度可能與預期不同", lossy=True))
    if not params["tagged"]:
        deg.append(Degradation(element_id="doc", concept="段落角色與表格結構", action="已關閉結構標記（pdf_tagged = false）：標題、段落、表格的列欄不寫進檔案", lossy=True))
    else:
        if any(p.tables for p in doc.pages):
            deg.append(Degradation(element_id="doc", concept="表格結構", action="列、欄、合併與表頭寫在結構樹（Table、TR、TH、TD）；頁面上仍是底色、框線與逐格文字，不是可編輯的表格物件", lossy=True))
        deg.append(Degradation(element_id="doc", concept="閱讀順序", action="寫在結構樹；內容串流仍依圖層前後繪製，不讀結構樹的工具取出的文字順序可能與閱讀順序不同", lossy=False))
