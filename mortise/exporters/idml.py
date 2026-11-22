"""InDesign IDML 輸出器（自寫；IDML 是 ZIP 封裝的一組 XML，沒有合適的開源寫出函式庫，見 research/formats.md 3.7、3.8、4.5）。

此格式的納入還等創辦人最終確認；整個模組可以整塊拿掉，不影響其他格式。
**未經 InDesign 開啟驗證。** 結構依 2012 年的 IDML 8.0 規格與常見範例檔推出，只做了結構與交互參照檢查。

交付物是一個 ZIP：
    <basename>.idml          IDML 封裝（mimetype 第一個、不壓縮）
    Links/<圖檔>             IDML 裡的 Image 以 LinkResourceURI="file:Links/<圖檔>" 指向這裡（相對路徑，未經 InDesign 驗證）
    mortise-map.json         元素對照表：元素編號 → 跨頁檔或內文檔、物件 Self

座標：IDML 的長度單位是 pt。每頁一個單頁跨頁；跨頁座標原點在跨頁中心，頁面的 ItemTransform 是
「1 0 0 1 −W/2 −H/2」，所以頁面座標（左上原點、y 向下）＝ 跨頁座標 ＋（W/2, H/2）。
每個頁面物件的 PathGeometry 寫在物件自身座標（以框中心為原點），ItemTransform 帶旋轉與平移到跨頁座標。

降階對照表（成品文件的概念 → IDML 的物件 → 是否有損）：
  頁面與尺寸（每頁可不同）  → 每頁一個 Spread（單頁）＋ Page 的 GeometricBounds      → 無損
  整頁底色                  → 「背景」圖層最底下的滿版 Rectangle                    → 無損
  整頁底圖                  → 「背景」圖層的滿版 Rectangle ＋ Image（連結圖檔）      → 無損（底圖本身不可編輯）
  文字框（含旋轉）          → TextFrame：PathGeometry ＋ ItemTransform               → 無損
  文字框的段落              → 各自的 Story：ParagraphStyleRange、CharacterStyleRange、Content、Br → 無損
  不自動換行（wrap=False）  → TextFramePreference AutoSizingType="WidthOnly"、不斷行  → 待 InDesign 驗證
  首行基線位置              → FirstBaselineOffset="FixedHeight" ＋ 依字型上緣算出的距離 → 字型量度不同時有誤差
  段落樣式表                → Styles.xml 的 ParagraphStyle（由 StyledDoc.styles 產生）→ 無損
  字型（推論的字型名稱）    → AppliedFont（字族）＋ FontStyle（樣式名），Fonts.xml 列出 → 不能內嵌，需安裝
  拉丁／東亞字型            → IDML（非 CJK 版）一段字只有一個 AppliedFont；整段寫推論字型 → 推論字型沒有中文字形時會缺字
  字級、粗斜體、顏色        → PointSize、FontStyle、FillColor（Graphic.xml 的 RGB Color）→ 無損
  文字不透明度              → 字元層級沒有透明度                                     → 有損（以不透明輸出）
  行距                      → Leading（pt；倍數換成字級 × 1.2 × 倍數）               → 無損
  段距、首行縮排、對齊      → SpaceBefore、SpaceAfter、FirstLineIndent、Justification → 無損
  表格（列欄、合併、底色、框線、邊距、垂直對齊） → Story 中的 Table、Row、Column、Cell → 無損
  數字格式、公式            → 只留顯示文字                                           → 有損
  圖片                      → Rectangle ＋ Image ＋ Link（連結 Links/ 中的原檔）      → 無損
  形狀：矩形、圓角矩形、橢圓、直線、自訂路徑 → Rectangle（圓角用 CornerOption）、Oval、GraphicLine、Polygon → 無損
  填色、框線不透明度        → FillTransparencySetting、StrokeTransparencySetting      → 無損
  圖層前後（z）             → 同一圖層內的物件順序                                   → 無損
  閱讀順序                  → 沒有對應；Story 的順序依閱讀順序排列                   → 有損（InDesign 不使用）
  主題色                    → Graphic.xml 的具名色票                                 → 無損
"""

from __future__ import annotations

import io
import json
import math
import re
import subprocess
import zipfile
from typing import Any, Optional

from lxml import etree
from PIL import Image as PILImage

from ..artifacts import (
    Align,
    Degradation,
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
from ..artifacts.common import BlobRef, Color
from ..artifacts.styled_doc import Box
from ..config import Config
from ..pipeline.store import Store

DEFAULT_PARAMS: dict[str, Any] = {
    "dom_version": "20.0",  # designmap 與各 XML 的 DOMVersion（第三方範例檔用 20.0；InDesign 21 寫的值沒有查到）
    "font_line_height": 1.2,  # 倍數行距換成 Leading（pt）：字級 × 此值 × 倍數
    "first_baseline": "fixed",  # "fixed"：FirstBaselineOffset=FixedHeight，距離＝首行最大字級 × 字型上緣；"ascent"：交給 InDesign（AscentOffset）
    "default_ascent": 0.9,  # 查不到字型檔時，上緣佔字級的比例
    "auto_size_single_line": True,  # wrap=False 的文字框設 AutoSizingType=WidthOnly，避免 InDesign 字寬略大時折行或溢排
    "links_folder": "Links",
    "layer_names": {"content": "內容", "background": "背景"},
}

MIME = "application/zip"
IDML_MIMETYPE = "application/vnd.adobe.indesign-idml-package"
PKG = "http://ns.adobe.com/AdobeInDesign/idml/1.0/packaging"
_JUST = {Align.LEFT: "LeftAlign", Align.CENTER: "CenterAlign", Align.RIGHT: "RightAlign", Align.JUSTIFY: "LeftJustified"}
_VJUST = {"top": "TopAlign", "middle": "CenterAlign", "bottom": "BottomAlign"}
_LINK_FORMAT = {"png": "$ID/Portable Network Graphics (PNG)", "jpg": "$ID/JPEG", "jpeg": "$ID/JPEG", "tif": "$ID/TIFF", "tiff": "$ID/TIFF", "gif": "$ID/GIF"}
_NO_PSTYLE = "ParagraphStyle/$ID/[No paragraph style]"
_NORMAL_PSTYLE = "ParagraphStyle/$ID/NormalParagraphStyle"
_NO_CSTYLE = "CharacterStyle/$ID/[No character style]"
_BLACK = "Color/Black"
_NONE = "Swatch/None"
_KAPPA = 0.5522847498  # 以三次貝茲近似四分之一圓弧的控制點比例
_XML_BAD = re.compile("[\x00-\x08\x0b\x0c\x0e-\x1f￾￿]")


def params_from(cfg: Optional[Config], defaults: dict, key: str) -> dict:
    p = dict(defaults)
    if cfg is not None:
        p.update(cfg.section("export").get(key, {}) or {})
    return p


def _n(v: float) -> str:
    """數字寫法：最多 4 位小數，去掉多餘的 0（IDML 是浮點，不取整到整數）。"""
    s = f"{v:.4f}".rstrip("0").rstrip(".")
    return "0" if s in ("-0", "") else s


def _font_style(bold: bool, italic: bool) -> str:
    return {(False, False): "Regular", (True, False): "Bold", (False, True): "Italic", (True, True): "Bold Italic"}[(bold, italic)]


_ASC_CACHE: dict[str, Optional[float]] = {}


def _ascent_ratio(family: str) -> Optional[float]:
    """找字型檔（mortise.tools.fontfind），回傳 hhea 上緣 ÷ 字身（找不到或字族不符時回傳 None）。"""
    if family not in _ASC_CACHE:
        val = None
        try:
            from ..tools import fontfind

            found = fontfind.find_file(family)
            if found is not None:
                from fontTools.ttLib import TTFont

                ft = TTFont(found[0], fontNumber=found[1], lazy=True)
                val = ft["hhea"].ascent / ft["head"].unitsPerEm
        except Exception:
            val = None
        _ASC_CACHE[family] = val
    return _ASC_CACHE[family]


def _installed(family: str) -> bool:
    return _ascent_ratio(family) is not None


class _Ids:
    """IDML 的 Self 編號：整份文件唯一。"""

    def __init__(self) -> None:
        self.n = 0x100

    def __call__(self, prefix: str = "u") -> str:
        self.n += 1
        return f"{prefix}{self.n:x}"


def _el(parent, tag: str, **attrs) -> etree._Element:
    e = etree.SubElement(parent, tag) if parent is not None else etree.Element(tag)
    for k, v in attrs.items():
        e.set(k, v if isinstance(v, str) else _n(v) if isinstance(v, float) else str(v))
    return e


def _prop(parent, tag: str, value: str, typ: str = "string") -> etree._Element:
    props = parent.find("Properties")
    if props is None:
        props = etree.Element("Properties")
        parent.insert(0, props)
    e = etree.SubElement(props, tag, type=typ)
    e.text = value
    return e


def _pkg_root(tag: str, dom: str) -> etree._Element:
    return etree.Element(f"{{{PKG}}}{tag}", nsmap={"idPkg": PKG}, DOMVersion=dom)


def _xml_bytes(root: etree._Element, pi: Optional[str] = None) -> bytes:
    body = etree.tostring(root, encoding="UTF-8", xml_declaration=False, pretty_print=True)
    head = b'<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
    if pi:
        head += pi.encode() + b"\n"
    return head + body


def _clean(text: str) -> str:
    """Content 的文字：段內強制換行寫 U+2028；去掉 XML 不允許的控制字元。"""
    return _XML_BAD.sub("", text.replace("\r\n", " ").replace("\n", " ").replace("\v", " "))


# ================================================================ 文件狀態


class _Doc:
    def __init__(self, doc: StyledDoc, params: dict, store: Store, degradations: list[Degradation]):
        self.doc = doc
        self.p = params
        self.store = store
        self.deg = degradations
        self.dom = params["dom_version"]
        self.ids = _Ids()
        self.colors: dict[str, Color] = {}  # Self → 顏色
        self.fonts: dict[str, set[str]] = {}  # 字族 → 用到的樣式名
        self.stories: list[tuple[str, bytes]] = []  # （Self, 檔案內容）
        self.links: dict[str, bytes] = {}  # Links/ 中的檔名 → 內容
        self.map: dict[str, dict] = {}  # 元素對照表
        self.layer_content = self.ids()
        self.layer_background = self.ids()
        self.pstyles: dict[str, str] = {}  # StyledDoc 的樣式 id → ParagraphStyle 的 Self
        self._pstyle_xml: list[etree._Element] = []
        self._build_styles()

    # ---------- 顏色、字型、樣式
    def color(self, c: Optional[Color]) -> str:
        if c is None:
            return _NONE
        key = f"Color/R={c.r} G={c.g} B={c.b}"
        self.colors[key] = c
        return key

    def font(self, family: str, style: str) -> None:
        self.fonts.setdefault(family, set()).add(style)

    def _build_styles(self) -> None:
        used_names: set[str] = set()
        for st in self.doc.styles:
            name = re.sub(r"[:\\]", "_", st.name).strip() or st.id
            base = name
            k = 2
            while name in used_names:
                name = f"{base} {k}"
                k += 1
            used_names.add(name)
            self_id = f"ParagraphStyle/{name}"
            self.pstyles[st.id] = self_id
            style = _font_style(st.bold, st.italic)
            e = _el(None, "ParagraphStyle", Self=self_id, Name=name, Imported="false", NextStyle=self_id, PointSize=float(st.size_pt), FontStyle=style, FillColor=self.color(st.color))
            _prop(e, "BasedOn", "$ID/NormalParagraphStyle")
            _prop(e, "AppliedFont", st.font_family)
            self.font(st.font_family, style)
            self._pstyle_xml.append(e)

    # ---------- 文字
    def _leading(self, sp: StyledParagraph, size: float) -> Optional[float]:
        if sp.line_spacing is None:
            return None
        ls = sp.line_spacing.value
        return float(ls.value) if ls.mode == LineSpacingMode.EXACT else size * self.p["font_line_height"] * float(ls.value)

    def paragraphs(self, parent, paragraphs: list[StyledParagraph], element_id: str) -> None:
        """段落 → ParagraphStyleRange；樣式片段 → CharacterStyleRange；段與段之間放 Br。"""
        alpha_noted = False
        for i, sp in enumerate(paragraphs):
            psr = _el(parent, "ParagraphStyleRange", AppliedParagraphStyle=self.pstyles.get(sp.style_id or "", _NORMAL_PSTYLE), Justification=_JUST[sp.align],
                      SpaceBefore=float(sp.space_before_pt), SpaceAfter=float(sp.space_after_pt), FirstLineIndent=float(sp.first_line_indent_pt))
            last_csr = None
            for run in sp.runs:
                last_csr = self.run(psr, sp, run)
                if run.color.a < 0.999 and not alpha_noted:
                    alpha_noted = True
                    self.deg.append(Degradation(element_id=element_id, concept="文字透明度", action="IDML 的字元沒有透明度，以不透明顏色輸出", lossy=True))
            if last_csr is None:
                last_csr = _el(psr, "CharacterStyleRange", AppliedCharacterStyle=_NO_CSTYLE)
            if i < len(paragraphs) - 1:
                _el(last_csr, "Br")

    def run(self, psr, sp: StyledParagraph, run: StyledRun) -> etree._Element:
        size = float(run.size_pt.value)
        style = _font_style(run.bold, run.italic)
        family = run.font.family.value
        csr = _el(psr, "CharacterStyleRange", AppliedCharacterStyle=_NO_CSTYLE, PointSize=size, FontStyle=style, FillColor=self.color(run.color))
        _prop(csr, "AppliedFont", family)
        lead = self._leading(sp, size)
        _prop(csr, "Leading", _n(lead) if lead is not None else "Auto", "unit" if lead is not None else "enumeration")
        self.font(family, style)
        content = _el(csr, "Content")
        content.text = _clean(run.text)
        return csr

    def first_baseline(self, paragraphs: list[StyledParagraph]) -> float:
        """首行基線距框上緣：首段最大字級 × 該字型的上緣比例。"""
        best = 0.0
        for sp in paragraphs[:1]:
            for run in sp.runs:
                size = float(run.size_pt.value)
                ratio = _ascent_ratio(run.font.family.value) or _ascent_ratio(run.font.verification_family) or self.p["default_ascent"]
                best = max(best, size * ratio)
        return best

    def story(self, build) -> str:
        sid = self.ids()
        root = _pkg_root("Story", self.dom)
        st = _el(root, "Story", Self=sid, AppliedTOCStyle="n", UserText="true", IsEndnoteStory="false", TrackChanges="false", StoryTitle="$ID/", AppliedNamedGrid="n")
        _el(st, "StoryPreference", OpticalMarginAlignment="false", OpticalMarginSize="12", FrameType="TextFrameType", StoryOrientation="Horizontal", StoryDirection="LeftToRightDirection")
        _el(st, "InCopyExportOption", IncludeGraphicProxies="true", IncludeAllResources="false")
        build(st)
        self.stories.append((sid, _xml_bytes(root, '<?aid style="50" type="snippet" readerVersion="6.0" featureSet="257" product="20.0(32)" ?>')))
        return sid


# ================================================================ 幾何


def _transform(box: Box, page: StyledPage) -> str:
    """物件自身座標（框中心為原點）→ 跨頁座標。旋轉順時針為正（y 向下時的標準旋轉矩陣）。"""
    th = math.radians(box.rotation)
    c, s = math.cos(th), math.sin(th)
    tx = box.x + box.w / 2 - page.width_pt / 2
    ty = box.y + box.h / 2 - page.height_pt / 2
    return " ".join(_n(v) for v in (c, s, -s, c, tx, ty))


def _geometry(item, paths: list[tuple[list[tuple[tuple[float, float], tuple[float, float], tuple[float, float]]], bool]]) -> None:
    """寫 PathGeometry。paths 的每一條是（點列, 是否開放）；每點是（錨點, 左控制點, 右控制點）。"""
    props = item.find("Properties")
    if props is None:
        props = etree.Element("Properties")
        item.insert(0, props)
    pg = _el(props, "PathGeometry")
    for pts, is_open in paths:
        gp = _el(pg, "GeometryPathType", PathOpen="true" if is_open else "false")
        arr = _el(gp, "PathPointArray")
        for a, left, right in pts:
            _el(arr, "PathPointType", Anchor=f"{_n(a[0])} {_n(a[1])}", LeftDirection=f"{_n(left[0])} {_n(left[1])}", RightDirection=f"{_n(right[0])} {_n(right[1])}")


def _rect_points(w: float, h: float):
    """矩形的四個角（InDesign 的順序：左上、左下、右下、右上），以框中心為原點。"""
    x0, y0, x1, y1 = -w / 2, -h / 2, w / 2, h / 2
    return [((x, y), (x, y), (x, y)) for x, y in ((x0, y0), (x0, y1), (x1, y1), (x1, y0))]


def _oval_points(w: float, h: float):
    rx, ry = w / 2, h / 2
    kx, ky = rx * _KAPPA, ry * _KAPPA
    # 左、下、右、上四個錨點；沿 左→下→右→上 的方向走
    return [
        ((-rx, 0.0), (-rx, -ky), (-rx, ky)),
        ((0.0, ry), (-kx, ry), (kx, ry)),
        ((rx, 0.0), (rx, ky), (rx, -ky)),
        ((0.0, -ry), (kx, -ry), (-kx, -ry)),
    ]


def _path_points(shape: StyledShape):
    """自訂路徑（相對框左上，種類 0 起筆、1 直線、2 三次貝茲、3 閉合）→ IDML 的子路徑（相對框中心）。"""
    ox, oy = shape.box.w / 2, shape.box.h / 2
    subs: list[tuple[list[list], bool]] = []
    cur: Optional[list[list]] = None
    for sg in shape.path:
        kind = int(sg[0])
        if kind == 0:
            if cur:
                subs.append((cur, True))
            p = (sg[1] - ox, sg[2] - oy)
            cur = [[p, p, p]]
        elif kind == 1 and cur is not None:
            p = (sg[1] - ox, sg[2] - oy)
            cur.append([p, p, p])
        elif kind == 2 and cur is not None:
            c1, c2, p = (sg[1] - ox, sg[2] - oy), (sg[3] - ox, sg[4] - oy), (sg[5] - ox, sg[6] - oy)
            cur[-1][2] = c1  # 前一點的右控制點
            cur.append([p, c2, p])
        elif kind == 3 and cur is not None:
            first, last = cur[0], cur[-1]
            if len(cur) > 1 and abs(first[0][0] - last[0][0]) < 1e-6 and abs(first[0][1] - last[0][1]) < 1e-6:
                first[1] = last[1]  # 最後一點與起點重合：把它的左控制點併到起點
                cur.pop()
            subs.append((cur, False))
            cur = None
    if cur:
        subs.append((cur, True))
    return [([(tuple(a), tuple(l), tuple(r)) for a, l, r in pts], is_open) for pts, is_open in subs]


# ================================================================ 頁面物件


class _Spread:
    def __init__(self, d: _Doc, page: StyledPage, master_id: Optional[str]):
        self.d = d
        self.page = page
        self.sid = d.ids()
        self.root = _pkg_root("Spread", d.dom)
        self.spread = _el(self.root, "Spread", Self=self.sid, FlattenerOverride="Default", ShowMasterItems="true", PageCount="1", BindingLocation="0", AllowPageShuffle="true", ItemTransform="1 0 0 1 0 0", PageTransitionType="None", PageTransitionDirection="NotApplicable", PageTransitionDuration="Medium")
        W, H = page.width_pt, page.height_pt
        self.page_id = d.ids()
        pg = _el(self.spread, "Page", Self=self.page_id, Name=str(page.page_index + 1), AppliedTrapPreset="TrapPreset/$ID/kDefaultTrapStyleName", AppliedMaster=master_id or "n", OverrideList="", TabOrder="",
                 GridStartingPoint="TopOutside", UseMasterGrid="true", GeometricBounds=f"0 0 {_n(H)} {_n(W)}", ItemTransform=f"1 0 0 1 {_n(-W / 2)} {_n(-H / 2)}", MasterPageTransform="1 0 0 1 0 0")
        _el(pg, "MarginPreference", ColumnCount="1", ColumnGutter="12", Top="0", Bottom="0", Left="0", Right="0", ColumnDirection="Horizontal", ColumnsPositions=f"0 {_n(W)}")
        self.file = f"Spreads/Spread_{self.sid}.xml"

    def _common(self, e, element_id: str, layer: Optional[str] = None) -> None:
        e.set("ItemLayer", layer or self.d.layer_content)
        e.set("Locked", "false")
        e.set("Visible", "true")
        e.set("Name", element_id)
        self.d.map[element_id] = {"spread": self.file, "self": e.get("Self"), "kind": e.tag, "page": self.page.page_index}

    def _paint(self, e, fill: Optional[Color], stroke: Optional[Color], width: Optional[float]) -> None:
        e.set("FillColor", self.d.color(fill))
        e.set("StrokeColor", self.d.color(stroke))
        e.set("StrokeWeight", _n(float(width or 1.0)) if stroke is not None else "0")
        if fill is not None and fill.a < 0.999:
            ts = _el(e, "FillTransparencySetting")
            _el(ts, "BlendingSetting", BlendMode="Normal", Opacity=_n(fill.a * 100))
        if stroke is not None and stroke.a < 0.999:
            ts = _el(e, "StrokeTransparencySetting")
            _el(ts, "BlendingSetting", BlendMode="Normal", Opacity=_n(stroke.a * 100))

    # ---------- 文字框
    def text_frame(self, element_id: str, box: Box, build, first_baseline: Optional[float], single_line: bool, align: Align) -> str:
        sid = self.d.story(build)
        tf = _el(self.spread, "TextFrame", Self=self.d.ids(), ParentStory=sid, PreviousTextFrame="n", NextTextFrame="n", ContentType="TextType",
                 AppliedObjectStyle="ObjectStyle/$ID/[Normal Text Frame]", ItemTransform=_transform(box, self.page), FillColor=_NONE, StrokeColor=_NONE, StrokeWeight="0")
        self._common(tf, element_id)
        self.d.map[element_id]["story"] = f"Stories/Story_{sid}.xml"
        self.d.map[element_id]["story_self"] = sid
        _geometry(tf, [(_rect_points(box.w, box.h), False)])
        pref = _el(tf, "TextFramePreference", TextColumnCount="1", TextColumnGutter="12", UseFixedColumnWidth="false", VerticalJustification="TopAlign", IgnoreWrap="false",
                   FirstBaselineOffset="AscentOffset", VerticalThreshold="0")
        _prop(pref, "InsetSpacing", "", "list")
        ins = pref.find("Properties/InsetSpacing")
        for _ in range(4):
            li = etree.SubElement(ins, "ListItem", type="unit")
            li.text = "0"
        if first_baseline is not None and self.d.p["first_baseline"] == "fixed":
            pref.set("FirstBaselineOffset", "FixedHeight")
            pref.set("MinimumFirstBaselineOffset", _n(first_baseline))
        if single_line and self.d.p["auto_size_single_line"]:
            ref = {Align.CENTER: "CenterPoint", Align.RIGHT: "RightCenterPoint"}.get(align, "LeftCenterPoint")
            pref.set("AutoSizingType", "WidthOnly")
            pref.set("AutoSizingReferencePoint", ref)
            pref.set("UseNoLineBreaksForAutoSizing", "true")
        return sid

    def text_box(self, tb: StyledTextBox) -> None:
        single = not tb.wrap
        fb = tb.first_baseline_pt if getattr(tb, "first_baseline_pt", None) is not None else self.d.first_baseline(tb.paragraphs)
        self.text_frame(tb.id, tb.box, lambda st: self.d.paragraphs(st, tb.paragraphs, tb.id), fb, single, tb.paragraphs[0].align if tb.paragraphs else Align.LEFT)

    # ---------- 表格
    def table(self, t: StyledTable) -> None:
        d = self.d
        if any(c.number_format or c.formula for c in t.cells):
            d.deg.append(Degradation(element_id=t.id, concept="數字格式與公式", action="IDML 的表格只有文字，保留顯示的文字", lossy=True))
        nr, nc = len(t.row_heights_pt), len(t.col_widths_pt)

        def build(st):
            psr = _el(st, "ParagraphStyleRange", AppliedParagraphStyle=_NORMAL_PSTYLE)
            csr = _el(psr, "CharacterStyleRange", AppliedCharacterStyle=_NO_CSTYLE)
            tid = d.ids()
            hdr = max(0, min(t.header_rows, nr))
            tbl = _el(csr, "Table", Self=tid, HeaderRowCount=str(hdr), FooterRowCount="0", BodyRowCount=str(nr - hdr), ColumnCount=str(nc),
                      AppliedTableStyle="TableStyle/$ID/[Basic Table]", TableDirection="LeftToRightDirection")
            for r, h in enumerate(t.row_heights_pt):
                _el(tbl, "Row", Self=f"{tid}Row{r}", Name=str(r), SingleRowHeight=float(h), MinimumHeight=float(h), AutoGrow="false")
            for c, w in enumerate(t.col_widths_pt):
                _el(tbl, "Column", Self=f"{tid}Column{c}", Name=str(c), SingleColumnWidth=float(w))
            for cell in sorted(t.cells, key=lambda x: (x.row, x.col)):
                top, right, bottom, left = cell.margins_pt
                ce = _el(tbl, "Cell", Self=f"{tid}i{cell.row}_{cell.col}", Name=f"{cell.col}:{cell.row}", RowSpan=str(cell.row_span), ColumnSpan=str(cell.col_span),
                         AppliedCellStyle="CellStyle/$ID/[None]", AppliedCellStylePriority="0", FillColor=d.color(cell.fill),
                         TopInset=float(top), RightInset=float(right), BottomInset=float(bottom), LeftInset=float(left), VerticalJustification=_VJUST.get(cell.v_align, "TopAlign"))
                if cell.fill is not None:
                    ce.set("FillTint", "100")
                    if cell.fill.a < 0.999:
                        d.deg.append(Degradation(element_id=t.id, concept="儲存格底色透明度", action="IDML 的儲存格底色沒有透明度，以不透明顏色輸出", lossy=True))
                for name, border in zip(("Top", "Right", "Bottom", "Left"), cell.borders):
                    ce.set(f"{name}EdgeStrokeWeight", _n(border.width_pt) if border else "0")
                    ce.set(f"{name}EdgeStrokeColor", d.color(border.color) if border else _NONE)
                    ce.set(f"{name}EdgeStrokeType", "StrokeStyle/$ID/Solid")
                if cell.paragraphs:
                    d.paragraphs(ce, cell.paragraphs, t.id)
            table_self.append(tid)

        table_self: list[str] = []
        self.text_frame(t.id, t.box, build, None, False, Align.LEFT)
        d.map[t.id]["table_self"] = table_self[0]

    # ---------- 圖片
    def image(self, element_id: str, ref: BlobRef, box: Box, layer: Optional[str] = None) -> None:
        d = self.d
        data = d.store.read_blob(ref)
        try:
            with PILImage.open(io.BytesIO(data)) as im:
                pw, ph = im.size
        except Exception:
            pw, ph = max(1, round(box.w)), max(1, round(box.h))
        name = f"{ref.sha256[:16]}.{ref.ext}"
        d.links[name] = data
        rect = _el(self.spread, "Rectangle", Self=d.ids(), ContentType="GraphicType", AppliedObjectStyle="ObjectStyle/$ID/[Normal Graphics Frame]", ItemTransform=_transform(box, self.page), FillColor=_NONE, StrokeColor=_NONE, StrokeWeight="0")
        self._common(rect, element_id, layer)
        _geometry(rect, [(_rect_points(box.w, box.h), False)])
        sx, sy = box.w / pw, box.h / ph
        img = _el(rect, "Image", Self=d.ids(), Space="$ID/#Links_RGB", ActualPpi="72 72", EffectivePpi=f"{_n(72 / sx)} {_n(72 / sy)}", ImageRenderingIntent="UseColorSettings", LocalDisplaySetting="Default",
                  ItemTransform=" ".join(_n(v) for v in (sx, 0.0, 0.0, sy, -box.w / 2, -box.h / 2)))
        props = _el(img, "Properties")
        _el(props, "Profile", type="string").text = "$ID/None"
        _el(props, "GraphicBounds", Left="0", Top="0", Right=str(pw), Bottom=str(ph))
        _el(img, "Link", Self=d.ids(), AssetURL="$ID/", AssetID="$ID/", LinkResourceURI=f"file:{d.p['links_folder']}/{name}", LinkResourceFormat=_LINK_FORMAT.get(ref.ext.lower(), "$ID/"),
            StoredState="Normal", LinkClassID="35906", LinkClientID="257", LinkResourceModified="false", LinkObjectModified="false", ShowInUI="true", CanEmbed="true", CanUnembed="true",
            CanPackage="true", ImportPolicy="NoAutoImport", ExportPolicy="NoAutoExport", LinkImportStamp="", LinkImportModificationTime="1970-01-01T00:00:00", LinkImportTime="1970-01-01T00:00:00", LinkResourceSize=f"0~{len(data):x}")
        d.map[element_id]["link"] = f"{d.p['links_folder']}/{name}"

    # ---------- 形狀
    def shape(self, sh: StyledShape) -> None:
        d, b = self.d, sh.box
        if sh.geometry == "line":
            # 直線以 rotation 記方向（⑤的約定）：0 表示左上到右下，非 0 表示左下到右上
            p0, p1 = ((-b.w / 2, b.h / 2), (b.w / 2, -b.h / 2)) if b.rotation else ((-b.w / 2, -b.h / 2), (b.w / 2, b.h / 2))
            e = _el(self.spread, "GraphicLine", Self=d.ids(), ContentType="Unassigned", AppliedObjectStyle="ObjectStyle/$ID/[None]", ItemTransform=_transform(b.model_copy(update={"rotation": 0.0}), self.page))
            self._common(e, sh.id)
            self._paint(e, None, sh.stroke or Color(r=0, g=0, b=0), sh.stroke_width_pt)
            _geometry(e, [([(p0, p0, p0), (p1, p1, p1)], True)])
            return
        tag = {"ellipse": "Oval", "path": "Polygon"}.get(sh.geometry, "Rectangle")
        e = _el(self.spread, tag, Self=d.ids(), ContentType="Unassigned", AppliedObjectStyle="ObjectStyle/$ID/[None]", ItemTransform=_transform(b, self.page))
        self._common(e, sh.id)
        self._paint(e, sh.fill, sh.stroke, sh.stroke_width_pt)
        if sh.geometry == "ellipse":
            _geometry(e, [(_oval_points(b.w, b.h), False)])
        elif sh.geometry == "path":
            subs = _path_points(sh)
            if not subs:
                d.deg.append(Degradation(element_id=sh.id, concept="自訂路徑", action="路徑是空的，以外框矩形輸出", lossy=True))
                subs = [(_rect_points(b.w, b.h), False)]
            _geometry(e, subs)
        else:
            if sh.geometry not in ("rect", "rounded_rect"):
                d.deg.append(Degradation(element_id=sh.id, concept="形狀種類", action=f"不認得的形狀「{sh.geometry}」，以矩形輸出", lossy=True))
            _geometry(e, [(_rect_points(b.w, b.h), False)])
            if sh.geometry == "rounded_rect" and sh.corner_radius_pt:
                r = _n(max(0.0, min(sh.corner_radius_pt, b.w / 2, b.h / 2)))
                for corner in ("TopLeft", "TopRight", "BottomLeft", "BottomRight"):
                    e.set(f"{corner}CornerOption", "RoundedCorner")
                    e.set(f"{corner}CornerRadius", r)

    def background(self) -> None:
        page = self.page
        full = Box(x=0, y=0, w=page.width_pt, h=page.height_pt)
        if page.background_color is not None:
            e = _el(self.spread, "Rectangle", Self=self.d.ids(), ContentType="Unassigned", AppliedObjectStyle="ObjectStyle/$ID/[None]", ItemTransform=_transform(full, page))
            self._common(e, f"background-color-{page.page_index + 1}", self.d.layer_background)
            self._paint(e, page.background_color, None, None)
            _geometry(e, [(_rect_points(full.w, full.h), False)])
        if page.background_image is not None:
            self.image(f"background-{page.page_index + 1}", page.background_image, full, self.d.layer_background)
            self.d.deg.append(Degradation(element_id=f"background-{page.page_index + 1}", concept="尚未轉成可編輯物件的內容", action="保留在整頁底圖中", lossy=False))

    def build(self) -> bytes:
        page = self.page
        self.background()
        items = [(t.z, 0, "text", t) for t in page.text_boxes] + [(i.z, 1, "image", i) for i in page.images] + [(s.z, 2, "shape", s) for s in page.shapes] + [(t.z, 3, "table", t) for t in page.tables]
        for _, _, kind, item in sorted(items, key=lambda x: (x[0], x[1])):
            if kind == "text":
                self.text_box(item)
            elif kind == "image":
                self.image(item.id, item.image, item.box)
            elif kind == "shape":
                self.shape(item)
            else:
                self.table(item)
        return _xml_bytes(self.root)


# ================================================================ 資源檔


def _graphic(d: _Doc) -> bytes:
    root = _pkg_root("Graphic", d.dom)
    _el(root, "Color", Self="Color/Black", Model="Process", Space="CMYK", ColorValue="0 0 0 100", ColorOverride="Specialblack", AlternateSpace="NoAlternateColor", AlternateColorValue="", Name="Black", ColorEditable="false", ColorRemovable="false", Visible="true", SwatchCreatorID="7937")
    _el(root, "Color", Self="Color/Paper", Model="Process", Space="CMYK", ColorValue="0 0 0 0", ColorOverride="Specialpaper", AlternateSpace="NoAlternateColor", AlternateColorValue="", Name="Paper", ColorEditable="true", ColorRemovable="false", Visible="true", SwatchCreatorID="7937")
    _el(root, "Color", Self="Color/Registration", Model="Registration", Space="CMYK", ColorValue="100 100 100 100", ColorOverride="Specialregistration", AlternateSpace="NoAlternateColor", AlternateColorValue="", Name="Registration", ColorEditable="false", ColorRemovable="false", Visible="true", SwatchCreatorID="7937")
    for name in ("Cyan", "Magenta", "Yellow", "Black"):
        _el(root, "Ink", Self=f"Ink/$ID/Process {name}", Name=f"$ID/Process {name}", Angle="0", ConvertToProcess="false", Frequency="70", NeutralDensity="1", PrintInk="true", TrapOrder="1", InkType="Normal")
    for i, c in enumerate(d.doc.theme_colors):  # 主題色寫成色票（同色會與下面重複，取同一個 Self）
        d.color(c)
    for key, c in sorted(d.colors.items()):
        _el(root, "Color", Self=key, Model="Process", Space="RGB", ColorValue=f"{c.r} {c.g} {c.b}", ColorOverride="Normal", AlternateSpace="NoAlternateColor", AlternateColorValue="", Name=key.split("/", 1)[1], ColorEditable="true", ColorRemovable="true", Visible="true", SwatchCreatorID="7937")
    _el(root, "Swatch", Self="Swatch/None", Name="None", ColorEditable="false", ColorRemovable="false", Visible="true", SwatchCreatorID="7937")
    _el(root, "StrokeStyle", Self="StrokeStyle/$ID/Solid", Name="$ID/Solid")
    return _xml_bytes(root)


def _fonts_xml(d: _Doc) -> bytes:
    root = _pkg_root("Fonts", d.dom)
    for i, (family, styles) in enumerate(sorted(d.fonts.items())):
        fid = f"di{i + 1:x}"
        fam = _el(root, "FontFamily", Self=fid, Name=family)
        installed = _installed(family)
        for style in sorted(styles):
            ps = re.sub(r"[^A-Za-z0-9]", "", family) + ("" if style == "Regular" else "-" + style.replace(" ", ""))
            _el(fam, "Font", Self=f"{fid}Font{style.replace(' ', '')}", FontFamily=family, Name=f"{family}\t{style}", PostScriptName=ps or family, Status="Installed" if installed else "NotAvailable",
                FontStyleName=style, FontType="OpenTypeTT" if installed else "Unknown", WritingScript="0", FullName=f"{family} {style}", FullNameNative=f"{family} {style}", FontStyleNameNative=style, PlatformName="$ID/", Version="")
    return _xml_bytes(root)


def _styles(d: _Doc) -> bytes:
    root = _pkg_root("Styles", d.dom)
    cg = _el(root, "RootCharacterStyleGroup", Self=d.ids())
    _el(cg, "CharacterStyle", Self=_NO_CSTYLE, Imported="false", Name="$ID/[No character style]")
    pg = _el(root, "RootParagraphStyleGroup", Self=d.ids())
    _el(pg, "ParagraphStyle", Self=_NO_PSTYLE, Name="$ID/[No paragraph style]", Imported="false", NextStyle=_NO_PSTYLE, FillColor=_BLACK, FontStyle="Regular", PointSize="12")
    normal = _el(pg, "ParagraphStyle", Self=_NORMAL_PSTYLE, Name="$ID/NormalParagraphStyle", Imported="false", NextStyle=_NORMAL_PSTYLE)
    _prop(normal, "BasedOn", "$ID/[No paragraph style]")
    for e in d._pstyle_xml:
        pg.append(e)
    clg = _el(root, "RootCellStyleGroup", Self=d.ids())
    _el(clg, "CellStyle", Self="CellStyle/$ID/[None]", AppliedParagraphStyle=_NO_PSTYLE, Name="$ID/[None]")
    tg = _el(root, "RootTableStyleGroup", Self=d.ids())
    _el(tg, "TableStyle", Self="TableStyle/$ID/[No table style]", Name="$ID/[No table style]")
    _el(tg, "TableStyle", Self="TableStyle/$ID/[Basic Table]", Name="$ID/[Basic Table]")
    og = _el(root, "RootObjectStyleGroup", Self=d.ids())
    for name in ("[None]", "[Normal Graphics Frame]", "[Normal Text Frame]", "[Normal Grid]"):
        _el(og, "ObjectStyle", Self=f"ObjectStyle/$ID/{name}", Name=f"$ID/{name}")
    return _xml_bytes(root)


def _preferences(d: _Doc) -> bytes:
    root = _pkg_root("Preferences", d.dom)
    first = d.doc.pages[0]
    _el(root, "DocumentPreference", PageHeight=float(first.height_pt), PageWidth=float(first.width_pt), PagesPerDocument=str(len(d.doc.pages)), FacingPages="false",
        DocumentBleedTopOffset="0", DocumentBleedBottomOffset="0", DocumentBleedInsideOrLeftOffset="0", DocumentBleedOutsideOrRightOffset="0", DocumentBleedUniformSize="true",
        AllowPageShuffle="true", OverprintBlack="true", PageBinding="LeftToRight", ColumnDirection="Horizontal", Intent="PrintIntent", CreateLinksOnContentPlace="false")
    _el(root, "MarginPreference", ColumnCount="1", ColumnGutter="12", Top="0", Bottom="0", Left="0", Right="0", ColumnDirection="Horizontal", ColumnsPositions=f"0 {_n(first.width_pt)}")
    _el(root, "ViewPreference", HorizontalMeasurementUnits="Points", VerticalMeasurementUnits="Points", LineMeasurementUnits="Points", TextSizeMeasurementUnits="Points", TypographicMeasurementUnits="Points", PointsPerInch="72", CursorKeyIncrement="1", RulerOrigin="PageOrigin")
    return _xml_bytes(root)


def _master(d: _Doc, page: StyledPage) -> tuple[str, str, bytes]:
    mid = d.ids()
    root = _pkg_root("MasterSpread", d.dom)
    ms = _el(root, "MasterSpread", Self=mid, ItemTransform="1 0 0 1 0 0", NamePrefix="A", BaseName="主版", ShowMasterItems="true", PageCount="1", OverriddenPageItemProps="", PrimaryTextFrame="n")
    _prop(ms, "Label", "", "string")
    W, H = page.width_pt, page.height_pt
    pg = _el(ms, "Page", Self=d.ids(), Name="A", AppliedTrapPreset="TrapPreset/$ID/kDefaultTrapStyleName", AppliedMaster="n", OverrideList="", TabOrder="", GridStartingPoint="TopOutside", UseMasterGrid="true",
             GeometricBounds=f"0 0 {_n(H)} {_n(W)}", ItemTransform=f"1 0 0 1 {_n(-W / 2)} {_n(-H / 2)}", MasterPageTransform="1 0 0 1 0 0")
    _el(pg, "MarginPreference", ColumnCount="1", ColumnGutter="12", Top="0", Bottom="0", Left="0", Right="0", ColumnDirection="Horizontal", ColumnsPositions=f"0 {_n(W)}")
    return mid, f"MasterSpreads/MasterSpread_{mid}.xml", _xml_bytes(root)


# ================================================================ 封裝檢查（自檢與測試共用）

_REF_ATTRS = ("ParentStory", "ItemLayer", "AppliedParagraphStyle", "AppliedCharacterStyle", "FillColor", "StrokeColor", "AppliedMaster", "AppliedCellStyle", "AppliedTableStyle", "AppliedObjectStyle", "NextStyle", "PreviousTextFrame", "NextTextFrame",
              "TopEdgeStrokeColor", "RightEdgeStrokeColor", "BottomEdgeStrokeColor", "LeftEdgeStrokeColor", "TopEdgeStrokeType", "RightEdgeStrokeType", "BottomEdgeStrokeType", "LeftEdgeStrokeType")


def validate_package(idml: bytes) -> tuple[list[str], dict[str, etree._Element]]:
    """檢查 IDML 封裝：ZIP 結構、mimetype、每個 XML 合法、designmap 參照的檔案都在、Self 唯一、交互參照都對得到、字型都列在 Fonts.xml。
    回傳（問題清單, 檔名 → 解析後的根元素）。"""
    problems: list[str] = []
    trees: dict[str, etree._Element] = {}
    try:
        z = zipfile.ZipFile(io.BytesIO(idml))
    except zipfile.BadZipFile as e:
        return [f"不是 ZIP：{e}"], trees
    infos = z.infolist()
    if not infos or infos[0].filename != "mimetype":
        problems.append("mimetype 不是 ZIP 的第一個項目。")
    else:
        if infos[0].compress_type != zipfile.ZIP_STORED:
            problems.append("mimetype 被壓縮了（必須不壓縮）。")
        if z.read("mimetype") != IDML_MIMETYPE.encode():
            problems.append("mimetype 內容不對。")
        if infos[0].extra:
            problems.append("mimetype 項目帶有 extra 欄位。")
    names = set(z.namelist())
    for need in ("designmap.xml", "META-INF/container.xml"):
        if need not in names:
            problems.append(f"缺少 {need}。")
    for n in names:
        if n.endswith(".xml"):
            try:
                trees[n] = etree.fromstring(z.read(n))
            except etree.XMLSyntaxError as e:
                problems.append(f"{n} 不是合法的 XML：{e}")
    dm = trees.get("designmap.xml")
    if dm is None:
        return problems, trees
    if dm.tag != "Document" or not dm.get("DOMVersion"):
        problems.append("designmap.xml 的根元素必須是帶 DOMVersion 的 Document。")
    src_tags = {"Graphic", "Fonts", "Styles", "Preferences", "MasterSpread", "Spread", "Story", "BackingStory", "Tags"}
    seen_tags = set()
    for e in dm:
        if isinstance(e.tag, str) and e.tag.startswith(f"{{{PKG}}}"):
            tag = e.tag.split("}")[1]
            seen_tags.add(tag)
            src = e.get("src")
            if tag in src_tags and src not in names:
                problems.append(f"designmap 參照的 {src} 不在封裝中。")
            elif src in trees and trees[src].tag != f"{{{PKG}}}{tag}":
                problems.append(f"{src} 的根元素應為 idPkg:{tag}。")
    for need in ("Graphic", "Fonts", "Styles", "Preferences", "Spread", "MasterSpread"):
        if need not in seen_tags:
            problems.append(f"designmap 沒有 idPkg:{need}。")
    stories = [e.get("src") for e in dm.findall(f"{{{PKG}}}Story")]
    story_ids = set((dm.get("StoryList") or "").split())
    # Self 唯一與交互參照
    selfs: dict[str, str] = {}
    for n, root in trees.items():
        for e in root.iter():
            s = e.get("Self") if isinstance(e.tag, str) else None
            if s:
                if s in selfs:
                    problems.append(f"Self「{s}」重複（{selfs[s]} 與 {n}）。")
                selfs[s] = n
    for n, root in trees.items():
        for e in root.iter():
            if not isinstance(e.tag, str):
                continue
            for a in _REF_ATTRS:
                v = e.get(a)
                if v and v != "n" and v not in selfs:
                    problems.append(f"{n}：{e.tag} 的 {a}=「{v}」對不到任何 Self。")
    for n in stories:
        st = trees.get(n)
        node = st.find("Story") if st is not None else None
        if node is None:
            problems.append(f"{n} 沒有 Story 元素。")
        elif node.get("Self") not in story_ids:
            problems.append(f"Story {node.get('Self')} 沒有列在 designmap 的 StoryList。")
    families = {f.get("Name") for f in trees.get("Resources/Fonts.xml", etree.Element("x")).iter("FontFamily")}
    for n in stories:
        for e in trees.get(n, etree.Element("x")).iter("AppliedFont"):
            if e.text not in families:
                problems.append(f"{n}：字型「{e.text}」沒有列在 Fonts.xml。")
    # 頁面物件必要的幾何
    for n, root in trees.items():
        if not n.startswith("Spreads/"):
            continue
        for e in root.iter("TextFrame", "Rectangle", "Oval", "Polygon", "GraphicLine"):
            if not e.get("ItemTransform") or len(e.get("ItemTransform").split()) != 6:
                problems.append(f"{n}：{e.get('Self')} 沒有正確的 ItemTransform。")
            if e.find("Properties/PathGeometry/GeometryPathType/PathPointArray/PathPointType") is None:
                problems.append(f"{n}：{e.get('Self')} 沒有 PathGeometry。")
    return problems, trees


# ================================================================ 進入點


def export(doc: StyledDoc, basename: str, cfg: Config, store: Store) -> OutputFile:
    params = params_from(cfg, DEFAULT_PARAMS, "idml")
    degradations: list[Degradation] = []
    d = _Doc(doc, params, store, degradations)
    dom = d.dom
    files: dict[str, bytes] = {}
    first = doc.pages[0]
    master_id, master_file, master_xml = _master(d, first)
    files[master_file] = master_xml
    spread_files: list[str] = []
    for page in doc.pages:
        same = abs(page.width_pt - first.width_pt) < 0.01 and abs(page.height_pt - first.height_pt) < 0.01
        sp = _Spread(d, page, master_id if same else None)
        files[sp.file] = sp.build()
        spread_files.append(sp.file)
        ro = [i for i in page.reading_order if i in d.map]
        if ro:
            d.deg.append(Degradation(element_id=f"page-{page.page_index + 1}", concept="閱讀順序", action="IDML 沒有閱讀順序：Story 依閱讀順序排在 StoryList 中，但 InDesign 不使用", lossy=True))
    # Story 依閱讀順序排列（InDesign 不使用，但 StoryList 的順序是唯一能放的地方）
    sid_page = {v["story_self"]: (v["page"], k) for k, v in d.map.items() if "story_self" in v}
    ro_index = {eid: i for pg in doc.pages for i, eid in enumerate(pg.reading_order)}
    order = {sid: n for n, (sid, _) in enumerate(d.stories)}
    d.stories.sort(key=lambda s: (sid_page.get(s[0], (0, ""))[0], ro_index.get(sid_page.get(s[0], (0, ""))[1], 10**6), order[s[0]]))
    for sid, data in d.stories:
        files[f"Stories/Story_{sid}.xml"] = data
    files["Resources/Graphic.xml"] = _graphic(d)
    files["Resources/Fonts.xml"] = _fonts_xml(d)
    files["Resources/Styles.xml"] = _styles(d)
    files["Resources/Preferences.xml"] = _preferences(d)
    tags = _pkg_root("Tags", dom)
    tag = _el(tags, "XMLTag", Self="XMLTag/Root", Name="Root")
    _prop(tag, "TagColor", "LightBlue", "enumeration")
    files["XML/Tags.xml"] = _xml_bytes(tags)
    bs = _pkg_root("BackingStory", dom)
    xs = _el(bs, "XmlStory", Self=d.ids(), AppliedTOCStyle="n", UserText="true", IsEndnoteStory="false", TrackChanges="false", StoryTitle="$ID/", AppliedNamedGrid="n")
    _el(_el(xs, "ParagraphStyleRange", AppliedParagraphStyle=_NORMAL_PSTYLE), "CharacterStyleRange", AppliedCharacterStyle=_NO_CSTYLE)
    files["XML/BackingStory.xml"] = _xml_bytes(bs)
    container = etree.Element("container", nsmap={None: "urn:oasis:names:tc:opendocument:xmlns:container"}, version="1.0")
    rf = etree.SubElement(etree.SubElement(container, "rootfiles"), "rootfile")
    rf.set("full-path", "designmap.xml")
    rf.set("media-type", "text/xml")
    files["META-INF/container.xml"] = _xml_bytes(container)

    # designmap.xml（順序依 InDesign 匯出的慣例）
    page_ids = [e.get("Self") for f in spread_files for e in etree.fromstring(files[f]).iter("Page")]
    dm = etree.Element("Document", nsmap={"idPkg": PKG}, DOMVersion=dom, Self="d", StoryList=" ".join(sid for sid, _ in d.stories), Name=f"{basename}.indd", ZeroPoint="0 0", ActiveLayer=d.layer_content,
                       CMYKProfile="U.S. Web Coated (SWOP) v2", RGBProfile="sRGB IEC61966-2.1", SolidColorIntent="UseColorSettings", AfterBlendingIntent="UseColorSettings", DefaultImageIntent="UseColorSettings",
                       RGBPolicy="PreserveEmbeddedProfiles", CMYKPolicy="CombinationOfPreserveAndSafeCmyk", AccurateLABSpots="false")
    for tag_name, src in (("Graphic", "Resources/Graphic.xml"), ("Fonts", "Resources/Fonts.xml"), ("Styles", "Resources/Styles.xml"), ("Preferences", "Resources/Preferences.xml"), ("Tags", "XML/Tags.xml")):
        etree.SubElement(dm, f"{{{PKG}}}{tag_name}", src=src)
    names = params["layer_names"]
    for lid, lname in ((d.layer_content, names.get("content", "內容")), (d.layer_background, names.get("background", "背景"))):  # 先列的圖層在上面
        layer = _el(dm, "Layer", Self=lid, Name=lname, Visible="true", Locked="false", IgnoreWrap="false", ShowGuides="true", LockGuides="false", UI="true", Expendable="true", Printable="true")
        _prop(layer, "LayerColor", "LightBlue" if lid == d.layer_content else "Red", "enumeration")
    etree.SubElement(dm, f"{{{PKG}}}MasterSpread", src=master_file)
    for f in spread_files:
        etree.SubElement(dm, f"{{{PKG}}}Spread", src=f)
    _el(dm, "Section", Self=d.ids(), Length=str(len(doc.pages)), Name="", ContinueNumbering="false", IncludeSectionPrefix="false", PageNumberStyle="Arabic", PageStart=page_ids[0], SectionPrefix="", Marker="", PageNumberStart="1")
    etree.SubElement(dm, f"{{{PKG}}}BackingStory", src="XML/BackingStory.xml")
    for sid, _ in d.stories:
        etree.SubElement(dm, f"{{{PKG}}}Story", src=f"Stories/Story_{sid}.xml")
    files["designmap.xml"] = _xml_bytes(dm, '<?aid style="50" type="document" readerVersion="6.0" featureSet="257" product="20.0(32)" ?>')

    # IDML 封裝：mimetype 第一個、不壓縮
    ib = io.BytesIO()
    with zipfile.ZipFile(ib, "w") as z:
        zi = zipfile.ZipInfo("mimetype", date_time=(2026, 1, 1, 0, 0, 0))
        zi.compress_type = zipfile.ZIP_STORED
        z.writestr(zi, IDML_MIMETYPE)
        order = ["designmap.xml", "META-INF/container.xml"] + sorted(k for k in files if k not in ("designmap.xml", "META-INF/container.xml"))
        for name in order:
            zi = zipfile.ZipInfo(name, date_time=(2026, 1, 1, 0, 0, 0))
            zi.compress_type = zipfile.ZIP_DEFLATED
            z.writestr(zi, files[name])
    idml = ib.getvalue()

    if doc.theme_colors:
        pass  # 主題色已寫成 Graphic.xml 的色票，無損
    used_fonts = sorted(d.fonts)
    missing = [f for f in used_fonts if not _installed(f)]
    degradations.append(Degradation(element_id="doc", concept="字型內嵌", action="IDML 不能內嵌字型；寫入推論的字型名稱，使用者需要安裝" + (f"（本機也沒有：{'、'.join(missing)}）" if missing else ""), lossy=False))

    # 外層 ZIP：IDML ＋ Links ＋ 元素對照表
    ob = io.BytesIO()
    with zipfile.ZipFile(ob, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr(f"{basename}.idml", idml)
        for name, data in sorted(d.links.items()):
            z.writestr(f"{params['links_folder']}/{name}", data)
        z.writestr("mortise-map.json", json.dumps(d.map, ensure_ascii=False, indent=1, sort_keys=True))
    out = ob.getvalue()

    # 自檢：重新讀入
    problems, trees = validate_package(idml)
    n_spreads = sum(1 for n in trees if n.startswith("Spreads/"))
    if n_spreads != len(doc.pages):
        problems.append(f"跨頁數 {n_spreads} 與成品文件頁數 {len(doc.pages)} 不符。")
    with zipfile.ZipFile(io.BytesIO(out)) as z:
        have = set(z.namelist())
    for e in (e for n, r in trees.items() if n.startswith("Spreads/") for e in r.iter("Link")):
        target = e.get("LinkResourceURI", "")[len("file:"):]
        if target not in have:
            problems.append(f"連結的圖檔 {target} 不在交付的 ZIP 中。")
    notes = [f"自檢：{p}" for p in problems]
    notes.append("未經 InDesign 開啟驗證：只做了 ZIP 結構、XML 合法性、必要元素與交互參照的檢查；座標約定、首行基線、圖片相對連結都依規格與範例推論。")
    blob = store.put_blob(out, MIME, "zip")
    return OutputFile(format=OutputFormat.IDML, file=blob, filename=f"{basename}-idml.zip", degradations=degradations, fonts_used=used_fonts, selfcheck_ok=not problems, selfcheck_notes=notes)


def read_idml(package: bytes) -> bytes:
    """從交付的 ZIP 取出 IDML 本體（測試與⑦用）。"""
    with zipfile.ZipFile(io.BytesIO(package)) as z:
        name = next(n for n in z.namelist() if n.endswith(".idml"))
        return z.read(name)
