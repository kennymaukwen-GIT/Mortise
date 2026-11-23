"""SVG 的目標驗證（7B）：把輸出的 SVG 重新打開、畫出來、讀回來，確認它真的是成品文件要的樣子。

做三件事，只讀不寫（不修改任何上游產物，也不修改輸出檔）：
  1. 結構與安全：用 lxml 重新解析，確認只有白名單裡的元素、id 唯一、沒有指令碼、事件屬性、外部參照（exporters/svg.py 的 check）。
  2. 讀回文字：把每個 text 的內容與位置（套用群組的 transform）讀回來，對回所屬的文字框或表格儲存格，與成品文件比對。
     - text_match_ratio：文字內容與成品文件一致的文字框、儲存格所佔的比例。
     - position_error_pt：每一行的對齊基準點（靠左是行的起點、置中是行的中點、靠右是行的終點）與成品文件依自家排版
       算出的位置的最大差。行寬用伺服器上「渲染時真的會用到的字型」以 harfbuzz 量（含字距微調），
       所以字型對不上、或置中的行沒有真的置中，這裡都看得出來。
     - 另外把每行的起點與③辨識到的原始文字行相比，寫在 notes（這是相對於原圖的誤差，包含排版本身的誤差）。
  3. 渲染比對：用 resvg（resvg-py，MIT；resvg 本體 Apache-2.0 或 MIT）把 SVG 畫成影像，與工作影像比相似度
     （算法沿用 stages/s7_verify.similarity；有指定輸出尺寸時只比 placement 那一塊，留白不算）。resvg 不是瀏覽器，也不是 Illustrator、Inkscape、Figma，engine 欄會寫明。
     resvg-py 裝不上或執行失敗時，退回只做前兩項，並在 notes 寫明。
     畫好的每一頁也經 on_render 交給⑦做逐行文字對照（verify/lines.py），問題併進這份報告、計入整體分數。

字型：resvg 找不到字型時不報錯、直接換後備字型，所以這裡自己建立伺服器字型的清單（家族名稱、字重、斜體），
逐一核對 SVG 用到的組合；對不上的寫進 issues，不靠渲染結果發現。

渲染前在記憶體裡對 SVG 做的調整（都不改輸出檔，notes 會寫明）：
  - 行內片段的 tspan 補上與所屬 text 相同的 y。resvg 0.48 把同一個文字區塊整段交給每個片段的字型排字，
    只要區塊裡有這個字型沒有的字、而後備字型又涵蓋整個區塊，連這個片段自己的字也會被換成後備字型
    （中英混排的行會整行變成中文字型）。補上 y 會另起一個文字區塊、x 仍然接續，版面不變，與 Chromium 畫的一致（實測）。
  - 要粗體而伺服器上那個字型沒有粗體檔時，補上同色描邊模擬（resvg 與這台機器的 Chromium 都不會自己模擬）；
    對方電腦有粗體檔時畫出來就是粗體，這樣比對才不會因為伺服器少一個字型檔而失真。verify 的 svg_emulate_bold 可關閉。
  - 圖片是外部檔（zip 內的 images/）時換成內嵌，resvg 才讀得到。
"""

from __future__ import annotations

import base64
import copy
import io
import math
import re
import statistics
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Optional

from PIL import Image

from ..artifacts import Align, ElementList, FormatPageReport, FormatReport, Issue, Layout, OutputFile, PageSpec, Severity, StyledDoc
from ..config import Config
from ..exporters import ai_pdf
from ..exporters import svg as svgx
from ..pipeline.store import Store
from ..stages.s7_verify import content_region, similarity, source_frame, verify_dpi
from . import lines as line_check

DEFAULTS: dict[str, Any] = {
    "svg_similarity_pass": 0.95,  # 相似度低於此值就在 issues 提醒
    "svg_position_tolerance_pt": 0.1,  # 讀回的位置與成品文件的容許差（pt）
    "svg_font_dirs": [],  # 渲染用的字型目錄；空的表示用 fontconfig 列出的字型（與輸出時量行寬用的是同一批）
    "svg_emulate_bold": True,  # 伺服器沒有粗體檔時，渲染前補描邊模擬粗體
    "svg_render": True,  # 是否渲染比對（關掉就只做結構檢查與讀回）
}
_FONT_EXT = (".ttf", ".otf", ".ttc", ".otc")
_GENERIC_FALLBACK = {
    "serif": ("DejaVu Serif", "Liberation Serif", "Noto Serif"),
    "sans-serif": ("DejaVu Sans", "Liberation Sans", "Noto Sans"),
    "monospace": ("DejaVu Sans Mono", "Liberation Mono", "Noto Sans Mono"),
}


def _params(cfg: Optional[Config]) -> dict:
    p = dict(DEFAULTS)
    c = cfg.section("verify") if cfg is not None else {}
    for key in DEFAULTS:
        if key in c:
            p[key] = c[key]
    p["render_dpi"] = float(c.get("render_dpi", 100))
    p["position_tolerance_pt"] = float(c.get("position_tolerance_pt", 1.5))
    return p


# ================================================================ 伺服器字型清單


@dataclass(frozen=True)
class FontInfo:
    path: str
    index: int
    families: tuple[str, ...]  # resvg 認得的家族名稱：有「排版家族名」（name 表 16）就用它，否則用家族名（1）
    legacy: tuple[str, ...]  # 只有部分軟體認得的舊式家族名（有 16 時的 1，例如「Inter SemiBold」）
    weight: int
    italic: bool
    width: int  # OS/2 usWidthClass（5 是正常寬度）


class FontBook:
    """伺服器上的字型清單，以及「font-family 清單加字重加斜體 → 實際會用到哪個字型檔」的比對（照 resvg 的規則）。"""

    def __init__(self, files: list[str]):
        self.files = files
        self.faces: list[FontInfo] = []
        self.by_family: dict[str, list[FontInfo]] = {}
        self.legacy: set[str] = set()
        self._svg_faces: dict[tuple, Any] = {}
        for path in files:
            with svgx.quiet_fonttools():
                found = _scan(path)
            for info in found:
                self.faces.append(info)
                for fam in info.families:
                    self.by_family.setdefault(fam, []).append(info)
                self.legacy.update(info.legacy)
        self.generic = {g: self._generic(g) for g in _GENERIC_FALLBACK}

    def _generic(self, name: str) -> Optional[str]:
        """通用字型在這台伺服器上對應到哪個家族：先問 fontconfig，問不到就從常見的開放字型裡挑一個有的。"""
        try:
            from ..tools import fontfind

            m = fontfind.match(name)
            for fam in (m.families if m is not None else []):
                if fam in self.by_family:
                    return fam
        except Exception:
            pass
        for fam in _GENERIC_FALLBACK[name]:
            if fam in self.by_family:
                return fam
        return next(iter(self.by_family), None)

    def resolve(self, families: list[str], weight: int, italic: bool) -> tuple[Optional[FontInfo], Optional[str], bool]:
        """回傳（實際會用到的字型, 對上的家族名稱, 是否只對上通用字型）。清單裡的名稱依序找，大小寫要完全相同（resvg 的行為）。"""
        for name in families:
            generic = name in self.generic
            fam = self.generic.get(name) if generic else name
            cands = self.by_family.get(fam or "")
            if cands:
                return _best(cands, weight, italic), fam, generic
        return None, None, False

    def face(self, info: FontInfo):
        """清單裡的字型 → 可以量行寬的字型物件（exporters/svg.py 的 _SvgFace）。"""
        key = (info.path, info.index)
        if key not in self._svg_faces:
            m = ai_pdf._Match(path=info.path, index=info.index, families=list(info.families), weight=200 if info.weight >= 600 else 80, slant=100 if info.italic else 0)
            self._svg_faces[key] = svgx._Loader()(m, "hhea")[0]
        return self._svg_faces[key]


def _best(cands: list[FontInfo], weight: int, italic: bool) -> FontInfo:
    """同一個家族裡挑最接近的字型（CSS 的字型比對順序：寬度、斜體、字重）。"""
    near = min(abs(c.width - 5) for c in cands)
    pool = [c for c in cands if abs(c.width - 5) == near]
    styled = [c for c in pool if c.italic == italic]
    pool = styled or pool

    def rank(c: FontInfo) -> tuple:
        w = c.weight
        if 400 <= weight <= 500:
            if weight <= w <= 500:
                return (0, w - weight)
            return (1, weight - w) if w < weight else (2, w - weight)
        if weight < 400:
            return (0, weight - w) if w <= weight else (1, w - weight)
        return (0, w - weight) if w >= weight else (1, weight - w)

    return min(pool, key=rank)


def _scan(path: str) -> list[FontInfo]:
    from fontTools.ttLib import TTCollection, TTFont

    out: list[FontInfo] = []
    try:
        fonts = TTCollection(path, lazy=True).fonts if path.lower().endswith((".ttc", ".otc")) else [TTFont(path, lazy=True)]
    except Exception:
        return out
    for index, ft in enumerate(fonts):
        try:
            table = ft["name"]

            def collect(name_id: int) -> list[str]:
                seen: list[str] = []
                for rec in table.names:
                    if rec.nameID == name_id:
                        try:
                            value = rec.toUnicode()
                        except Exception:
                            continue
                        if value and value not in seen:
                            seen.append(value)
                return seen

            typo, fam = collect(16), collect(1)
            os2 = ft["OS/2"] if "OS/2" in ft else None
            head = ft["head"]
            weight = int(os2.usWeightClass) if os2 is not None else (700 if head.macStyle & 1 else 400)
            italic = bool(head.macStyle & 2) or bool(os2 is not None and os2.fsSelection & 0x201)
            out.append(FontInfo(path=path, index=index, families=tuple(typo or fam), legacy=tuple(n for n in fam if typo and n not in typo),
                                weight=weight, italic=italic, width=int(os2.usWidthClass) if os2 is not None else 5))
        except Exception:
            continue
        finally:
            try:
                ft.close()
            except Exception:
                pass
    return out


_BOOKS: dict[tuple, FontBook] = {}


def font_files(dirs: Optional[list[str]] = None) -> list[str]:
    """渲染用的字型檔。沒有指定目錄時用這台電腦的字型（mortise.tools.fontfind；輸出器量行寬時也是透過它找字型）。"""
    files: set[str] = set()
    if not dirs:
        try:
            from ..tools import fontfind

            files = {e.path for e in fontfind.list_fonts() if e.path.lower().endswith(_FONT_EXT)}
        except Exception:
            files = set()
        if not files:
            from ..tools import fontfind

            dirs = fontfind.system_font_dirs()
    for d in dirs or []:
        root = Path(d)
        if root.is_dir():
            files.update(str(f) for f in root.rglob("*") if f.suffix.lower() in _FONT_EXT)
    return sorted(f for f in files if Path(f).is_file())


def font_book(dirs: Optional[list[str]] = None) -> FontBook:
    files = font_files(dirs)
    key = tuple(files)
    if key not in _BOOKS:
        _BOOKS[key] = FontBook(files)
    return _BOOKS[key]


# ================================================================ 渲染


def engine() -> tuple[Optional[Any], str]:
    """回傳（resvg_py 模組或 None, engine 欄要寫的說明）。"""
    try:
        import resvg_py

        ver = getattr(resvg_py, "__version__", "?")
        core = getattr(resvg_py, "__resvg_version__", "")
        return resvg_py, f"resvg-py {ver}" + (f"（內含 resvg {core}）" if core else "") + "渲染，lxml 讀回文字；不是瀏覽器，也不是 Illustrator、Inkscape、Figma，各軟體的顯示可能不同"
    except Exception as e:  # 沒有安裝，或這個平台載入失敗
        return None, f"只做結構檢查與文字讀回（lxml）；resvg-py 無法載入（{type(e).__name__}: {e}），沒有渲染比對"


def render_copy(root, book: FontBook, others: dict[str, bytes], emulate_bold: bool, bold_ratio: float = 0.03) -> tuple[str, list[str]]:
    """做一份只給 resvg 渲染用的 SVG（見模組開頭的說明）。回傳（SVG 字串, 做了哪些調整）。不改動傳進來的樹。"""
    from lxml import etree

    root = copy.deepcopy(root)
    q = "{" + svgx.SVG_NS + "}"
    href = "{" + svgx.XLINK_NS + "}href"
    did: set[str] = set()

    def inherited(el, name: str) -> Optional[str]:
        while el is not None:
            v = el.get(name)
            if v is not None:
                return v
            el = el.getparent()
        return None

    for text in root.iter(q + "text"):
        spans = [c for c in text if c.tag == q + "tspan"]
        y = text.get("y")
        for sp in spans:
            if y is not None and sp.get("y") is None and sp.get("x") is None:
                sp.set("y", y)
                did.add("行內片段的 tspan 補上 y（resvg 才會逐片段選字型；版面不變）")
        if not emulate_bold:
            continue
        for el in spans or [text]:
            weight = int(float(inherited(el, "font-weight") or 400))
            if weight < 600 or inherited(el, "stroke") not in (None, "none"):
                continue
            info, _, _ = book.resolve(svgx.parse_family(inherited(el, "font-family") or ""), weight, (inherited(el, "font-style") or "normal") == "italic")
            if info is None or info.weight >= 600:
                continue
            size = float(inherited(el, "font-size") or 0.0)
            fill = inherited(el, "fill") or "#000000"
            if size > 0 and fill != "none":
                el.set("stroke", fill)
                el.set("stroke-width", svgx.num(size * bold_ratio, 3))
                el.set("stroke-linejoin", "round")
                opacity = inherited(el, "fill-opacity")
                if opacity is not None:
                    el.set("stroke-opacity", opacity)
                did.add("伺服器沒有粗體檔的文字補上同色描邊模擬粗體")
    for im in root.iter(q + "image"):
        ref = im.get(href)
        if ref and not ref.startswith("data:") and ref in others:
            media = "image/jpeg" if ref.lower().endswith((".jpg", ".jpeg")) else "image/png"
            im.set(href, f"data:{media};base64," + base64.b64encode(others[ref]).decode("ascii"))
            did.add("外部圖檔換成內嵌")
    return etree.tostring(root, encoding="unicode"), sorted(did)


def render(resvg_py, svg_text: str, width_px: int, book: FontBook) -> Image.Image:
    """用 resvg 把 SVG 畫成影像。固定傳 dpi=96（resvg-py 的預設 0 會讓帶單位的尺寸出錯）、只用清單裡的字型、白底。"""
    kw: dict[str, Any] = {}
    for arg, name in (("serif_family", "serif"), ("sans_serif_family", "sans-serif"), ("monospace_family", "monospace")):
        if book.generic.get(name):
            kw[arg] = book.generic[name]
    png = resvg_py.svg_to_bytes(svg_string=svg_text, width=int(width_px), dpi=96.0, background="#ffffff", skip_system_fonts=True, font_files=book.files, **kw)
    return Image.open(io.BytesIO(png)).convert("RGB")


def _renders_pdf(images: list[tuple[Image.Image, float, float]]) -> bytes:
    """把各頁的渲染結果收成一份 PDF（一頁一張圖、頁面尺寸同成品文件），與 PowerPoint 目標驗證的渲染結果同一種形式。"""
    from reportlab.lib.utils import ImageReader
    from reportlab.pdfgen import canvas

    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=(images[0][1], images[0][2]), invariant=1)
    for im, w, h in images:
        c.setPageSize((w, h))
        c.drawImage(ImageReader(im), 0, 0, w, h)
        c.showPage()
    c.save()
    return buf.getvalue()


# ================================================================ 讀回與比對


def _norm(text: str) -> str:
    return re.sub(r"\s+", "", svgx.clean(text))


def _expected_texts(page) -> dict[tuple, str]:
    """成品文件這一頁每個文字框、每個有文字的儲存格的文字。鍵是（元素編號, 儲存格或 None）。"""
    out: dict[tuple, str] = {}
    for tb in page.text_boxes:
        out[(tb.id, None)] = "".join(r.text for sp in tb.paragraphs for r in sp.runs)
    for t in page.tables:
        for cell in t.cells:
            text = "".join(r.text for sp in cell.paragraphs for r in sp.runs)
            if _norm(text):
                out[(t.id, (cell.row, cell.col))] = text
    return {k: v for k, v in out.items() if _norm(v)}


def _read_anchor(rl: svgx.ReadLine, align: Align, book: FontBook) -> Optional[tuple[float, float]]:
    """讀回來的一行的對齊基準點。置中、靠右需要行寬：用渲染時會用到的字型量（含字距微調）；量不出來回傳 None。"""
    if align not in (Align.CENTER, Align.RIGHT):
        return rl.x, rl.y
    width = 0.0
    for sp in rl.spans:
        info, _, _ = book.resolve(sp.get("families") or [], int(sp.get("weight", 400)), bool(sp.get("italic")))
        face = book.face(info) if info is not None else None
        if face is None:
            return None
        width += face.shaped_width(sp["text"]) * float(sp.get("size") or 0.0)
    k = 0.5 if align == Align.CENTER else 1.0
    th = math.radians(rl.rotation)
    return rl.x + math.cos(th) * width * k, rl.y + math.sin(th) * width * k


def _glyphs(read: dict[tuple, list[svgx.ReadLine]], book: FontBook) -> list[line_check.Glyph]:
    """讀回來的每一行 → 每個字的位置（輸出頁面 pt），給⑦的逐行文字對照比對「原位置上寫的是什麼字」。
    讀回的 x 是行的起點（輸出器一律從行首寫起）；每個字的位置是逐字前進寬度（用渲染時會用到的字型量）加上字距、詞距。
    文字轉外框、旋轉的行、量不出字寬的行不列（那幾行只靠影像判斷）。"""
    out: list[line_check.Glyph] = []
    n = -1
    for rls in read.values():
        for rl in rls:
            n += 1
            if rl.outlined or abs(rl.rotation) > 0.5 or not rl.spans:
                continue
            chars: list[tuple[str, float, float]] = []  # （字、前進寬度 pt、字級 pt）
            ok = True
            for sp in rl.spans:
                size = float(sp.get("size") or 0.0)
                info, _, _ = book.resolve(sp.get("families") or [], int(sp.get("weight", 400)), bool(sp.get("italic")))
                face = book.face(info) if info is not None else None
                if face is None or size <= 0:
                    ok = False
                    break
                for ch in sp["text"]:
                    adv = face.shaped_width(ch) * size + rl.letter_spacing + (rl.word_spacing if ch == " " else 0.0)
                    chars.append((ch, adv, size))
            if not ok or not chars:
                continue
            x = rl.x
            for ch, adv, size in chars:
                if ch.strip():
                    out.append(line_check.Glyph(ch=ch, x=x, y=rl.y, size=size, line=n))
                x += adv
    return out


def _font_issues(lines: list[svgx.ReadLine], book: FontBook) -> list[Issue]:
    """核對 SVG 用到的「家族名稱加字重加斜體」在伺服器上對到什麼。"""
    issues: list[Issue] = []
    seen: set[tuple] = set()

    def add(key: tuple, severity: Severity, code: str, message: str, element_id: str) -> None:
        if key not in seen:
            seen.add(key)
            issues.append(Issue(severity=severity, code=code, message=message, element_id=element_id or None))

    for rl in lines:
        if rl.outlined:
            continue
        for sp in rl.spans:
            fams = sp.get("families") or []
            if not fams:
                add(("nofamily",), Severity.WARNING, "font_missing", "有文字沒有寫 font-family，各軟體會用自己的預設字型。", rl.element_id)
                continue
            weight, italic = int(sp.get("weight", 400)), bool(sp.get("italic"))
            info, used, generic = book.resolve(fams, weight, italic)
            first = fams[0]
            if info is None:
                add(("none", first), Severity.WARNING, "font_missing", f"伺服器上找不到「{'、'.join(fams)}」任何一個字型，渲染時這些文字不會被畫出來。", rl.element_id)
                continue
            if generic:
                add(("generic", first), Severity.WARNING, "font_missing", f"伺服器上沒有「{'、'.join(f for f in fams if f not in book.generic)}」，渲染驗證只能用通用字型「{used}」；行寬與輸出時量的可能不同。", rl.element_id)
            elif used != first:
                why = "這個名稱是帶字重的舊式家族名，resvg 不認得" if first in book.legacy else "伺服器沒有這個字型"
                add(("sub", first, used), Severity.INFO, "font_substituted", f"font-family 的第一個名稱「{first}」沒有對上（{why}），渲染驗證用清單裡的「{used}」。對方電腦若有「{first}」，字寬可能略有不同。", rl.element_id)
            if weight >= 600 and info.weight < 600:
                add(("bold", used), Severity.INFO, "font_bold_missing", f"伺服器上的「{used}」沒有粗體檔（最重的是 {info.weight}）；SVG 寫的是 font-weight={weight}，對方電腦有粗體檔才會是真的粗體。", rl.element_id)
            if italic and not info.italic:
                add(("italic", used), Severity.INFO, "font_italic_missing", f"伺服器上的「{used}」沒有斜體檔；SVG 寫的是 font-style=italic，由各軟體決定是否模擬。", rl.element_id)
    return issues


def _source_offsets(page_index: int, read: dict[tuple, list[svgx.ReadLine]], want_lines: dict[tuple, list[svgx.Line]], book: FontBook,
                    elements: list[ElementList], layout: Layout, styled: StyledDoc) -> list[tuple[str, float]]:
    """每個文字框各行的對齊基準點與③辨識到的原始文字行相差多少（pt）：靠左比行的起點、置中比中點、靠右比終點。
    行數對不上、文字已轉外框、或使用者改過文字的框不比。回傳（元素編號, 距離），每個框依行的順序。"""
    if page_index >= len(elements) or page_index >= len(layout.pages):
        return []
    page = styled.pages[page_index]
    scale, ox, oy = source_frame(page)  # 來源文字行（原始頁面的相對座標）→ 輸出頁面 pt；有輸出尺寸時經過縮放與置中
    src = {t.id: t for t in elements[page_index].texts}
    pl = layout.pages[page_index]
    pars = {p.id: p for p in pl.paragraphs}
    out: list[tuple[str, float]] = []
    for lb in pl.text_boxes:
        tb = next((t for t in page.text_boxes if t.id == lb.id), None)
        got = read.get((lb.id, None))
        want = want_lines.get((lb.id, None), [])
        if tb is None or not got or any(sp.text_locked for sp in tb.paragraphs):
            continue
        try:
            expect = [src[lid] for pid in lb.paragraph_ids for lid in pars[pid].line_ids]
        except KeyError:
            continue
        if len(expect) != len(got) or len(want) != len(got) or any(g.outlined for g in got):
            continue
        for e, g, ln in zip(expect, got, want):
            anchor = _read_anchor(g, ln.align, book)
            (x0, y0), (x1, y1) = (ox + e.baseline[0].x * scale, oy + e.baseline[0].y * scale), (ox + e.baseline[1].x * scale, oy + e.baseline[1].y * scale)
            k = 0.5 if ln.align == Align.CENTER else 1.0 if ln.align == Align.RIGHT else 0.0
            if anchor is None:
                anchor, k = (g.x, g.y), 0.0
            out.append((lb.id, math.hypot(x0 + (x1 - x0) * k - anchor[0], y0 + (y1 - y0) * k - anchor[1])))
    return out


def verify_page(data: bytes, page, intended: list[svgx.Line], book: FontBook, p: dict, others: dict[str, bytes]) -> tuple[FormatPageReport, Any, dict, dict]:
    """單頁的結構檢查與文字讀回比對（不含渲染）。
    回傳（頁面報告, 解析後的根元素, 讀回的文字行, 依成品文件排版應有的文字行）；後兩者的鍵是（元素編號, 儲存格或 None）。"""
    issues: list[Issue] = []
    problems, root = svgx.check(data, set(others) if others else None)
    for msg in problems:
        issues.append(Issue(severity=Severity.ERROR, code="svg_structure", message=msg))
    if root is None:
        return FormatPageReport(page_index=page.page_index, issues=issues), None, {}, {}
    lines = svgx.read_lines(root)
    read: dict[tuple, list[svgx.ReadLine]] = {}
    for rl in lines:
        read.setdefault((rl.element_id, rl.cell), []).append(rl)
    expected = _expected_texts(page)
    good = 0
    for key, want in expected.items():
        got = "".join(rl.text for rl in read.get(key, []))
        if _norm(got) == _norm(want):
            good += 1
        else:
            where = key[0] if key[1] is None else f"{key[0]} 第 {key[1][0] + 1} 列第 {key[1][1] + 1} 欄"
            issues.append(Issue(severity=Severity.ERROR, code="text_missing" if not got else "text_mismatch", element_id=key[0],
                                message=f"{where} 的文字{'在 SVG 裡找不到' if not got else '與成品文件不一致'}。"))
    for key in read:
        if key not in expected and _norm("".join(rl.text for rl in read[key])):
            issues.append(Issue(severity=Severity.ERROR, code="text_extra", element_id=key[0] or None, message=f"SVG 裡有成品文件沒有的文字（所屬元素：{key[0] or '不明'}）。"))
    ratio = good / len(expected) if expected else None

    # 位置：依元素把讀回的行與應有的行逐行配對
    want_lines: dict[tuple, list[svgx.Line]] = {}
    for ln in intended:
        want_lines.setdefault((ln.element_id, ln.cell), []).append(ln)
    worst: Optional[float] = None
    worst_at = ""
    outlined = any(rl.outlined for rl in lines)
    if not outlined:
        for key, want in want_lines.items():
            got = read.get(key, [])
            if len(got) != len(want):
                issues.append(Issue(severity=Severity.ERROR, code="line_count_mismatch", element_id=key[0], message=f"{key[0]} 在 SVG 裡是 {len(got)} 行，依成品文件排版應該是 {len(want)} 行。"))
                continue
            for ln, rl in zip(want, got):
                ax, ay = ln.anchor()
                anchor = _read_anchor(rl, ln.align, book)
                if anchor is None:  # 量不出行寬：退而比較行的起點
                    ax, ay = svgx.rotate_point(ln.placed(True)[0], ln.y, ln.rotation, ln.cx, ln.cy)
                    anchor = (rl.x, rl.y)
                dist = math.hypot(anchor[0] - ax, anchor[1] - ay)
                if worst is None or dist > worst:
                    worst, worst_at = dist, key[0]
        if worst is not None and worst > p["svg_position_tolerance_pt"]:
            issues.append(Issue(severity=Severity.ERROR, code="position_offset", element_id=worst_at or None,
                                message=f"SVG 裡文字行的位置與成品文件最多相差 {worst:.2f} pt，超過容許值 {p['svg_position_tolerance_pt']} pt。"))
    issues += _font_issues(lines, book)
    return FormatPageReport(page_index=page.page_index, text_match_ratio=ratio, position_error_pt=worst, issues=issues), root, read, want_lines


def verify(output: OutputFile, specs: list[PageSpec], elements: list[ElementList], layout: Layout, styled: StyledDoc, cfg: Config, store: Store,
           on_render: Optional[Callable[[int, Image.Image], None]] = None) -> FormatReport:
    """on_render：每畫好一頁就以（頁碼, 裁成與工作影像同一範圍的渲染結果）呼叫一次（⑦用它做逐行文字對照）。"""
    p = _params(cfg)
    notes: list[str] = []
    pages_svg, others = svgx.unpack(output, store)
    intended = svgx.intended_lines(styled, cfg)
    book = font_book(list(p["svg_font_dirs"]) or None)
    resvg_py, engine_name = engine()
    if not p["svg_render"]:
        resvg_py = None
        notes.append("設定關閉了渲染比對（svg_render = false），只做結構檢查與文字讀回。")
    elif resvg_py is None:
        notes.append("resvg-py 無法載入，退回只做結構檢查與文字讀回，沒有相似度。")
    _, lp = svgx.params_for(cfg)
    reports: list[FormatPageReport] = []
    renders: list[tuple[Image.Image, float, float]] = []
    adjustments: set[str] = set()
    offsets: list[float] = []
    outlined_seen = False
    for page in styled.pages:
        i = page.page_index
        data = pages_svg.get(i)
        if data is None:
            reports.append(FormatPageReport(page_index=i, issues=[Issue(severity=Severity.ERROR, code="page_missing", message="輸出檔裡沒有這一頁的 SVG。")]))
            continue
        report, root, read, want_lines = verify_page(data, page, intended[i] if i < len(intended) else [], book, p, others)
        issues = list(report.issues)
        outlined_seen = outlined_seen or any(rl.outlined for rls in read.values() for rl in rls)
        per_box: dict[str, float] = {}
        for eid, dist in _source_offsets(i, read, want_lines, book, elements, layout, styled):
            offsets.append(dist)
            per_box.setdefault(eid, dist)  # 每個框的第一行
        for eid, dist in per_box.items():
            if dist > p["position_tolerance_pt"]:
                issues.append(Issue(severity=Severity.WARNING, code="source_position_offset", element_id=eid, message=f"第一行的位置與原圖相差 {dist:.1f} pt（這是成品文件的排版與原圖的差，不是 SVG 寫錯）。"))
        sim: Optional[float] = None
        spec = next((s for s in specs if s.page_index == i), None)
        if resvg_py is not None and root is not None:
            try:
                text, did = render_copy(root, book, others, bool(p["svg_emulate_bold"]), float(lp.get("faux_bold_stroke_ratio", 0.03)))
                adjustments.update(did)
                image = render(resvg_py, text, max(1, round(page.width_pt * verify_dpi(p["render_dpi"], page, spec) / 72.0)), book)
                renders.append((image, page.width_pt, page.height_pt))
                if spec is not None:
                    working = Image.open(store.blob_path(spec.working_image))  # 白紙類頁面比去光照後的影像；其他頁面工作影像就是原圖
                    region = content_region(image, page)
                    sim = similarity(working, region)  # 有輸出尺寸時只比原始內容那一塊（留白不算）
                    if on_render is not None:
                        on_render(i, region, glyphs=_glyphs(read, book))
                    if sim < p["svg_similarity_pass"]:
                        issues.append(Issue(severity=Severity.WARNING, code="low_similarity", message=f"SVG 渲染後與原圖的相似度 {sim:.3f}，低於合格線 {p['svg_similarity_pass']}。"))
            except Exception as e:  # 渲染失敗：這一頁退回只做結構檢查
                notes.append(f"第 {i + 1} 頁渲染失敗（{type(e).__name__}: {e}），這一頁只做了結構檢查與文字讀回。")
        reports.append(report.model_copy(update={"similarity": sim, "issues": issues}))
    for name in sorted(n for n in pages_svg if n >= len(styled.pages)):
        notes.append(f"輸出檔多了成品文件沒有的第 {name + 1} 頁。")
    if adjustments:
        notes.append("渲染前在記憶體裡調整了 SVG（輸出檔沒有改）：" + "；".join(sorted(adjustments)) + "。")
    if offsets:
        notes.append(f"各行的對齊基準點與原圖文字行相比（{len(offsets)} 行）：中位數 {statistics.median(offsets):.2f} pt，最大 {max(offsets):.2f} pt。")
    if outlined_seen:
        notes.append("文字已轉成外框路徑，沒有可讀回的位置；文字內容以 data-text 比對，位置只由相似度檢查。")
    if not output.selfcheck_ok:
        notes.append("輸出器的自檢沒有通過：" + "；".join(output.selfcheck_notes[:5]))
    render_blob = None
    if renders:
        try:
            render_blob = store.put_blob(_renders_pdf(renders), "application/pdf", "pdf")
        except Exception as e:  # pragma: no cover
            notes.append(f"渲染結果無法收成 PDF（{type(e).__name__}: {e}）。")
    ok = output.selfcheck_ok and all(i.severity != Severity.ERROR for r in reports for i in r.issues)
    return FormatReport(format=output.format, engine=engine_name, ok=ok, pages=reports, notes=notes, render=render_blob)
