"""可編輯 PDF 的目標驗證（7B）：把輸出的 PDF 重新打開、畫出來、讀回來，確認檔案裡真的是我們要的東西。

引擎是 PDFium（自家工具鏈裡的讀取器），不是 Acrobat、Foxit 這些使用者手上的編輯軟體。
所以這裡能證明的是「檔案裡寫了什麼、一個獨立的 PDF 讀取器讀得出什麼」，不能證明「編輯軟體打開後好不好改」。

每一頁檢查五件事。全部只讀不寫：不改輸出檔，也不改任何上游產物；有問題寫進 issues。

  1. 外觀（similarity）
     PDFium 把這一頁畫成影像，與工作影像比相似度（沿用 s7_verify.similarity）。

  2. 文字（text_match_ratio、position_error_pt）
     用③的擷取函式（s3_recognize.read_text_lines，與讀原生 PDF 是同一支）把文字行讀回來，
     與「成品文件經輸出器排版後的每一行」（pdf_editable.placed_lines）逐行比對。
     比對的單位是字：把讀回來的每個字放回頁面座標，在預期那一行的基線上找出同樣的一串字。
     這樣讀取端把同一條基線上相鄰的兩行接成一行、或把一行切成兩段，都不影響比對。
       text_match_ratio  = 文字一致的行數 ÷（預期的行數 ＋ 讀回來多出的行數）
       position_error_pt = 文字一致的行當中，讀回來的每個字的起點與預期位置的最大距離（pt）
     字級不一致、字型與預期內嵌的不同，各開一條問題。

  3. 與來源比對（只在 issues 與 notes 裡報告，不計入上面兩個數字）
     同一批讀回來的字，再與③讀到的來源文字行比對。第 2 項的預期值出自輸出器自己的排版，
     只能抓到「寫檔寫錯」（字寬表、文字矩陣、編碼）；排版本身偏了（行距、斷行、首行位置）要靠這一項。
     擷取產線（原生 PDF）的來源位置是精確的，超過容許差就開警告；影像產線的來源位置是量測值，只列數字。

  4. 物件
     圖片：數量，以及每張圖片的位置。向量圖：每條路徑是不是各自成為一個路徑物件（填色規則、筆畫寬度、
     端點與接合、範圍），漸層是不是寫成漸層物件（shading）。有結構標記時靠標記內容編號（MCID）把頁面上的物件
     對回成品文件的元素；沒有結構標記時只能比整頁的總數。

  5. 結構標記（輸出器有開才檢查）
     PDFium 認不認得這是 Tagged PDF；每個段落、表格、圖是否都有結構元素、種類對不對（H1、H2、P、Table、Figure）；
     結構樹的順序是否就是成品文件的閱讀順序。

逐行對位（propose，由⑦呼叫；只對擷取產線的頁面）
  第 3 項若發現排版結果與來源不同，光報告沒有用：來源軟體在中英交界加寬的間距、左右對齊時的分配方式、
  行距取整後的累積誤差，成品文件都沒有欄位可以記。所以這裡把「未對位的排版」與③的來源文字行逐字比對，
  算出每一行要怎麼修（行首位移、整行字距、少數幾處位移），開成送給⑥本格式輸出器的修正單（欄位 pdf.line_fit）。
  這與 PowerPoint 的 pptx.offset_pt 是同一種東西：只影響這個格式的輸出器，不改成品文件；數值全部是量出來的。
  輸出器把套用的對位記在文件資訊（MortiseLineFit），下一輪驗證讀它來算每一行的預期位置。
  影像產線不做：那裡的來源位置是量測值，照著它對位只會把量測雜訊寫進檔案。

設定在 default.toml 的 [verify]（名稱以 pdf_ 開頭）。
"""

from __future__ import annotations

import ctypes
import math
import unicodedata
from dataclasses import dataclass, field
from typing import Any, Optional

import pypdfium2.raw as raw
from PIL import Image

from ..artifacts import (
    Correction, CorrectionSource, ElementList, FormatPageReport, FormatReport, Issue, Layout, OutputFile, OutputFormat, PageSpec, Severity, StyledDoc,
    StyledPage, TextLine,
)
from ..artifacts.styled_doc import vector_index
from ..config import Config
from ..exporters import ai_pdf, pdf_editable
from ..pipeline.store import Store
from ..stages import s2_page, s3_recognize
from ..tools import pdfium as pdf

ENGINE = "PDFium 回讀（自家工具，不是 Acrobat；只能證明檔案裡寫了什麼，編輯軟體裡的行為沒有實測）"

DEFAULTS: dict[str, Any] = {
    "pdf_similarity_pass": 0.95,  # 重新畫出來與工作影像的相似度合格線
    "pdf_position_tolerance_pt": 0.5,  # 讀回來的字與排版預期位置的容許差（pt）
    "pdf_size_tolerance_pt": 0.05,  # 讀回來的字級與成品文件的容許差（pt）
    "pdf_source_position_tolerance_pt": 0.5,  # 原生輸入：讀回來的行首與來源文字行的容許差（pt）
    "pdf_object_tolerance_pt": 0.1,  # 圖片與向量路徑的範圍容許差（pt）
    "pdf_check_objects": True,  # 是否檢查圖片與向量圖
    "pdf_check_structure": True,  # 是否檢查結構標記（輸出器關掉結構標記時自動略過）
    "pdf_check_source": True,  # 是否另外與來源文字行比對
    "pdf_fit": True,  # 原生輸入：排版結果與來源文字行的差超過容許差時，開修正單請輸出器逐行對位
    "pdf_fit_tolerance_pt": 0.25,  # 逐行對位：行首或行內累積的位置差超過此值才修正（pt）
    "pdf_fit_max_gap_ratio": 0.4,  # 逐行對位：一行需要的位移點超過字數的此比例，就改用整行一致的字距
    "pdf_fit_max_width_change": 0.12,  # 逐行對位：來源行寬與排版行寬相差超過此比例就不對位
}


def _params(cfg: Optional[Config]) -> dict:
    p = dict(DEFAULTS)
    if cfg is not None:
        section = cfg.section("verify")
        p.update({k: v for k, v in section.items() if k in DEFAULTS})
        p["render_dpi"] = section.get("render_dpi", 100)
        p["max_rounds"] = int(section.get("max_rounds", 2))
    else:
        p["render_dpi"], p["max_rounds"] = 100, 2
    return p


# ================================================================ 讀回：文字


@dataclass
class _Char:
    """讀回來的一個字（頁面座標 pt，原點左上、y 向下）。"""

    ch: str
    x: float
    y: float
    size: float
    angle: float  # 所在那一行的方向（度，順時針為正）
    line: int  # 讀回來的第幾行
    family: str
    used: bool = False


@dataclass
class _Want:
    """預期的一行：應該出現的字與位置。"""

    key: str  # 報告用的名稱（元素編號）
    text: str
    x: float  # 基線起點（頁面座標 pt）
    y: float
    angle: float  # 度，順時針為正
    size: float
    chars: list[tuple[str, float, float]]  # （字、沿基線距起點 pt、字級 pt），不含空白
    families: list[str] = field(default_factory=list)
    ref: Any = None  # 這一行的出處（排好的行 PlacedLine；來源文字行沒有）


@dataclass
class _Hit:
    want: _Want
    found: bool
    start_error: float = 0.0  # 行首的位置差（pt）
    char_error: float = 0.0  # 逐字位置差的最大值（pt）
    size_error: float = 0.0  # 字級差的最大值（pt）
    families: list[str] = field(default_factory=list)  # 讀回來的字族
    got_text: str = ""  # 沒找到時：那個位置附近讀到的字
    found_at: list[tuple[float, float, float]] = field(default_factory=list)  # 找到的每個字：（沿預期基線距預期起點 pt、垂直基線的差 pt、字級 pt）


def _visible(ch: str) -> bool:
    """比對時算不算數：空白與看不見的控制字元（零寬字元、軟連字號）不算，各家讀取器對它們的處理不一。"""
    return bool(ch.strip()) and unicodedata.category(ch) not in ("Cc", "Cf")


def _strip(text: str) -> str:
    return "".join(ch for ch in text if _visible(ch))


def _line_angle(line: TextLine) -> float:
    b0, b1 = line.baseline
    dx, dy = b1.x - b0.x, b1.y - b0.y
    if math.hypot(dx, dy) < 1e-9:  # 只有一個字、量不出方向：用四角的上邊
        dx, dy = line.quad.tr.x - line.quad.tl.x, line.quad.tr.y - line.quad.tl.y
    return math.degrees(math.atan2(dy, dx)) if math.hypot(dx, dy) > 1e-12 else 0.0


def _line_chars(line: TextLine, index: int, w: float) -> list[_Char]:
    """讀回來的一行 → 每個字在頁面上的位置。行的座標是頁寬為 1 的相對單位，乘上頁寬（pt）還原。"""
    ang = _line_angle(line)
    ux, uy = math.cos(math.radians(ang)), math.sin(math.radians(ang))
    starts = [cx for r in line.runs for cx in r.char_x]
    base = min(starts) if starts else 0.0
    ox, oy = line.baseline[0].x * w, line.baseline[0].y * w
    out: list[_Char] = []
    for r in line.runs:
        size = float(r.font_size.value) * w
        family = r.font_candidates[0].name if r.font_candidates else ""
        vis = [ch for ch in r.text if ch.strip()]
        xs = list(r.char_x)
        if len(xs) != len(vis):  # 字數與位置對不上（不該發生）：整段當成都在段首，文字仍可比對
            first = xs[0] if xs else base
            xs = [first] * len(vis)
        for ch, cx in zip(vis, xs):
            if not _visible(ch):
                continue
            s = (cx - base) * w
            out.append(_Char(ch=ch, x=ox + ux * s, y=oy + uy * s, size=size, angle=ang, line=index, family=family))
    return out


def read_lines(reader: pdf.PageReader, page_index: int, cfg: Optional[Config]) -> tuple[list[TextLine], float]:
    """用③的擷取函式讀回這一頁看得見的文字行。回傳（文字行、頁寬 pt）。"""
    boxes = reader.boxes()
    matrix, dw, _ = s2_page.pdf_matrix(boxes.get("crop", boxes["media"]), reader.rotation())
    rc = cfg.section("recognize") if cfg is not None else Config.load().section("recognize")
    lines = [t for t in s3_recognize.read_text_lines(reader, page_index, matrix, dw, rc) if not t.hidden]
    return lines, dw


def _angle_diff(a: float, b: float) -> float:
    return abs((a - b + 180.0) % 360.0 - 180.0)


def _match(wants: list[_Want], chars: list[_Char]) -> list[_Hit]:
    """把每一行預期的字，在讀回來的字裡找出來。chars 的 used 會被標記（之後用來找多出來的字）。"""
    hits: list[_Hit] = []
    for w in wants:
        target = [c for c in w.chars if _visible(c[0])]
        if not target:
            continue
        ux, uy = math.cos(math.radians(w.angle)), math.sin(math.radians(w.angle))
        row_tol = max(0.4 * w.size, 1.5)  # 同一條基線：垂直方向差不到這麼多
        length = target[-1][1]
        reach = max(3.0 * w.size, 12.0)
        row: list[tuple[float, float, _Char]] = []  # （沿基線、垂直基線、字）
        for c in chars:
            if _angle_diff(c.angle, w.angle) > 2.0:
                continue
            dx, dy = c.x - w.x, c.y - w.y
            along, across = dx * ux + dy * uy, -dx * uy + dy * ux
            if abs(across) <= row_tol and -reach <= along <= length + reach:
                row.append((along, across, c))
        row.sort(key=lambda t: t[0])
        text = "".join(ch for ch, _, _ in target)
        got = "".join(c.ch for _, _, c in row)
        best: Optional[int] = None
        pos = got.find(text)
        while pos >= 0:  # 同一串字出現不只一次時，取起點最接近預期位置、而且還沒被別行用掉的
            free = not any(c.used for _, _, c in row[pos:pos + len(text)])
            if free and (best is None or abs(row[pos][0]) < abs(row[best][0])):
                best = pos
            pos = got.find(text, pos + 1)
        if best is None:
            near = "".join(c.ch for along, _, c in row if -0.5 * w.size <= along <= length + w.size)
            hits.append(_Hit(want=w, found=False, got_text=near))
            continue
        picked = row[best:best + len(text)]
        char_err = size_err = 0.0
        families: list[str] = []
        for (along, across, c), (_, adv, size) in zip(picked, target):
            c.used = True
            char_err = max(char_err, math.hypot(along - adv, across))
            size_err = max(size_err, abs(c.size - size))
            if c.family and c.family not in families:
                families.append(c.family)
        first = picked[0]
        hits.append(_Hit(want=w, found=True, start_error=math.hypot(first[0] - target[0][1], first[1]), char_error=char_err, size_error=size_err, families=families,
                         found_at=[(along, across, c.size) for along, across, c in picked]))
    return hits


def _wants_from_placed(placed: list[ai_pdf.PlacedLine]) -> list[_Want]:
    out = []
    for ln in placed:
        key = ln.paragraph if ln.cell is None else f"{ln.owner}#{ln.cell[0]},{ln.cell[1]}"
        out.append(_Want(key=key, text=ln.text, x=ln.x, y=ln.y, angle=ln.angle_deg, size=ln.size,
                         chars=[c for c in ln.chars if _visible(c[0])], families=list(ln.families), ref=ln))
    return out


def _wants_from_source(lines: list[TextLine], w: float) -> list[_Want]:
    """③讀到的來源文字行 → 預期的位置（頁寬換成成品文件的 pt）。"""
    out = []
    for i, line in enumerate(lines):
        if line.hidden or not _strip(line.text):
            continue
        cs = _line_chars(line, i, w)
        if not cs:
            continue
        ang = _line_angle(line)
        ux, uy = math.cos(math.radians(ang)), math.sin(math.radians(ang))
        x0, y0 = line.baseline[0].x * w, line.baseline[0].y * w
        out.append(_Want(key=line.id, text=line.text, x=x0, y=y0, angle=ang, size=line.font_size * w,
                         chars=[(c.ch, (c.x - x0) * ux + (c.y - y0) * uy, c.size) for c in cs]))
    return out


def _fam(name: str) -> str:
    return "".join(ch for ch in name.casefold() if ch.isalnum())


# ================================================================ 讀回：頁面物件與結構樹（PDFium）


@dataclass
class PageObject:
    """頁面上的一個物件（PDFium 的列舉順序就是繪製順序）。座標是 PDF 使用者空間（原點左下、y 向上）。"""

    kind: str  # text、path、image、shading、form、other
    mcid: int  # 所在標記內容的編號；不在任何標記內容裡是 -1
    bounds: tuple[float, float, float, float]  # 左、下、右、上。路徑用控制點算（不含筆畫寬度）；其餘是 PDFium 回報的外框
    filled: bool = False
    fill_rule: str = "nonzero"
    stroked: bool = False
    stroke_width: float = 0.0
    stroke_cap: str = "butt"
    stroke_join: str = "miter"
    segments: int = 0


_KINDS = {raw.FPDF_PAGEOBJ_TEXT: "text", raw.FPDF_PAGEOBJ_PATH: "path", raw.FPDF_PAGEOBJ_IMAGE: "image", raw.FPDF_PAGEOBJ_SHADING: "shading", raw.FPDF_PAGEOBJ_FORM: "form"}
_CAPS = {raw.FPDF_LINECAP_BUTT: "butt", raw.FPDF_LINECAP_ROUND: "round", raw.FPDF_LINECAP_PROJECTING_SQUARE: "square"}
_JOINS = {raw.FPDF_LINEJOIN_MITER: "miter", raw.FPDF_LINEJOIN_ROUND: "round", raw.FPDF_LINEJOIN_BEVEL: "bevel"}


def read_objects(reader: pdf.PageReader) -> list[PageObject]:
    """列出這一頁直接畫在頁面上的物件（本輸出器不用表單物件，所以不往表單裡走）。"""
    out: list[PageObject] = []
    with pdf.LOCK:
        page = reader.page
        for i in range(raw.FPDFPage_CountObjects(page)):
            obj = raw.FPDFPage_GetObject(page, i)
            kind = _KINDS.get(raw.FPDFPageObj_GetType(obj), "other")
            l, b, r, t = (ctypes.c_float() for _ in range(4))
            raw.FPDFPageObj_GetBounds(obj, ctypes.byref(l), ctypes.byref(b), ctypes.byref(r), ctypes.byref(t))
            item = PageObject(kind=kind, mcid=int(raw.FPDFPageObj_GetMarkedContentID(obj)), bounds=(l.value, b.value, r.value, t.value))
            if kind == "path":
                m = raw.FS_MATRIX()
                raw.FPDFPageObj_GetMatrix(obj, ctypes.byref(m))
                xs, ys = [], []
                n = raw.FPDFPath_CountSegments(obj)
                for k in range(n):
                    seg = raw.FPDFPath_GetPathSegment(obj, k)
                    x, y = ctypes.c_float(), ctypes.c_float()
                    raw.FPDFPathSegment_GetPoint(seg, ctypes.byref(x), ctypes.byref(y))
                    xs.append(m.a * x.value + m.c * y.value + m.e)
                    ys.append(m.b * x.value + m.d * y.value + m.f)
                if xs:
                    item.bounds = (min(xs), min(ys), max(xs), max(ys))
                fill_mode, stroke = ctypes.c_int(), ctypes.c_int()
                raw.FPDFPath_GetDrawMode(obj, ctypes.byref(fill_mode), ctypes.byref(stroke))
                width = ctypes.c_float()
                raw.FPDFPageObj_GetStrokeWidth(obj, ctypes.byref(width))
                scale = math.sqrt(abs(m.a * m.d - m.b * m.c)) or 1.0
                item.filled = fill_mode.value != raw.FPDF_FILLMODE_NONE
                item.fill_rule = "evenodd" if fill_mode.value == raw.FPDF_FILLMODE_ALTERNATE else "nonzero"
                item.stroked = bool(stroke.value)
                item.stroke_width = width.value * scale
                item.stroke_cap = _CAPS.get(raw.FPDFPageObj_GetLineCap(obj), "butt")
                item.stroke_join = _JOINS.get(raw.FPDFPageObj_GetLineJoin(obj), "miter")
                item.segments = n
            out.append(item)
    return out


@dataclass
class StructNode:
    """結構樹的一個元素（PDFium 讀回）。"""

    type: str  # Document、Sect、H1、P、Table、TR、TH、TD、Figure…
    id: str = ""  # 結構元素的 /ID：本輸出器寫的是成品文件的元素編號
    alt: str = ""
    lang: str = ""
    mcids: list[int] = field(default_factory=list)  # 這個元素直接擁有、而且在這一頁上的標記內容編號
    kids: list["StructNode"] = field(default_factory=list)

    def walk(self):
        yield self
        for k in self.kids:
            yield from k.walk()


def _utf16(fn, handle) -> str:
    n = fn(handle, None, 0)
    if not n:
        return ""
    buf = ctypes.create_string_buffer(n)
    fn(handle, buf, n)
    return buf.raw[:n].decode("utf-16-le", "replace").rstrip("\x00")


def is_tagged(doc) -> bool:
    with pdf.LOCK:
        return bool(raw.FPDFCatalog_IsTagged(doc.raw))


def read_struct_tree(reader: pdf.PageReader) -> list[StructNode]:
    """讀這一頁的結構樹（PDFium 的 fpdf_structtree 介面）。回傳最上層的元素；沒有結構樹回傳空清單。
    PDFium 給的是「與這一頁有關的部分」：別頁的段落不會出現，容器（Document、Sect、Table）會出現。"""

    def node(el, depth: int) -> Optional[StructNode]:
        if not el or depth > 64:
            return None
        n = StructNode(type=_utf16(raw.FPDF_StructElement_GetType, el), id=_utf16(raw.FPDF_StructElement_GetID, el),
                       alt=_utf16(raw.FPDF_StructElement_GetAltText, el), lang=_utf16(raw.FPDF_StructElement_GetLang, el))
        count = raw.FPDF_StructElement_GetMarkedContentIdCount(el)
        n.mcids = [m for m in (raw.FPDF_StructElement_GetMarkedContentIdAtIndex(el, i) for i in range(max(count, 0))) if m >= 0]
        for i in range(max(raw.FPDF_StructElement_CountChildren(el), 0)):
            kid = node(raw.FPDF_StructElement_GetChildAtIndex(el, i), depth + 1)
            if kid is not None:
                n.kids.append(kid)
        return n

    with pdf.LOCK:
        tree = raw.FPDF_StructTree_GetForPage(reader.page)
        if not tree:
            return []
        try:
            out = []
            for i in range(max(raw.FPDF_StructTree_CountChildren(tree), 0)):
                kid = node(raw.FPDF_StructTree_GetChildAtIndex(tree, i), 0)
                if kid is not None:
                    out.append(kid)
            return out
        finally:
            raw.FPDF_StructTree_Close(tree)


# ================================================================ 各項檢查


def _box_bounds(box, page_h: float) -> tuple[float, float, float, float]:
    """成品文件的框（可旋轉）在 PDF 使用者空間的外接範圍（左、下、右、上）。"""
    fr = ai_pdf._Frame(box, page_h)
    pts = [fr.pt(x, y) for x, y in ((0, 0), (box.w, 0), (box.w, box.h), (0, box.h))]
    return min(p[0] for p in pts), min(p[1] for p in pts), max(p[0] for p in pts), max(p[1] for p in pts)


def _bounds_diff(a: tuple[float, float, float, float], b: tuple[float, float, float, float]) -> float:
    return max(abs(x - y) for x, y in zip(a, b))


def _expected_vector_ops(vec, page_h: float) -> list[dict]:
    """一個向量圖應該畫出哪些物件（依繪製順序）。規則與 ai_pdf._Painter.vector_path 相同：
    有合法漸層的路徑 → 一個漸層物件（另有筆畫時再加一個只描邊的路徑物件）；其餘有填色或筆畫的 → 一個路徑物件。"""
    ops: list[dict] = []
    for i, vp in enumerate(vec.paths):
        xs = [v for sg in vp.path for v in sg[1::2]]
        ys = [v for sg in vp.path for v in sg[2::2]]
        if not xs:
            continue
        bounds = (vec.box.x + min(xs), page_h - (vec.box.y + max(ys)), vec.box.x + max(xs), page_h - (vec.box.y + min(ys)))
        gradient = vp.gradient is not None and ai_pdf.gradient_drawable(vp.gradient)
        if gradient:
            ops.append({"kind": "shading", "index": i, "bounds": bounds})
        filled = (not gradient) and vp.fill is not None
        if filled or vp.stroke is not None:
            ops.append({"kind": "path", "index": i, "bounds": bounds, "filled": filled, "fill_rule": vp.fill_rule, "stroked": vp.stroke is not None,
                        "stroke_width": float(vp.stroke_width_pt or 0.0), "stroke_cap": vp.stroke_cap, "stroke_join": vp.stroke_join})
    return ops


def _check_objects(page: StyledPage, vectors: dict, objects: list[PageObject], by_id: dict[str, list[int]], tagged: bool, drew_vectors: bool, tol: float) -> list[Issue]:
    """圖片與向量圖。by_id 是「結構元素的編號 → 這一頁的標記內容編號」。"""
    issues: list[Issue] = []
    H = page.height_pt
    as_vector = {im.id: vectors[im.vector_id] for im in page.images if drew_vectors and im.vector_id and im.vector_id in vectors and vectors[im.vector_id].paths}
    plain = [im for im in page.images if im.id not in as_vector]
    want_images = len(plain) + (1 if page.background_image is not None else 0)
    got_images = sum(1 for o in objects if o.kind == "image")
    if got_images != want_images:
        issues.append(Issue(severity=Severity.ERROR, code="image_count", message=f"圖片數量不符：成品文件要畫 {want_images} 張（含底圖），輸出檔裡有 {got_images} 個影像物件。"))
    ops_of = {img_id: _expected_vector_ops(v, H) for img_id, v in as_vector.items()}
    want_shadings = sum(1 for ops in ops_of.values() for op in ops if op["kind"] == "shading")
    got_shadings = sum(1 for o in objects if o.kind == "shading")
    if got_shadings != want_shadings:
        issues.append(Issue(severity=Severity.ERROR, code="gradient_count", message=f"漸層數量不符：向量圖共有 {want_shadings} 個漸層，輸出檔裡有 {got_shadings} 個漸層物件。"))
    if not tagged:
        want_paths = sum(1 for ops in ops_of.values() for op in ops if op["kind"] == "path")
        got_paths = sum(1 for o in objects if o.kind == "path")
        if got_paths < want_paths:
            issues.append(Issue(severity=Severity.ERROR, code="vector_path_count", message=f"向量圖共有 {want_paths} 條要畫的路徑，輸出檔裡只有 {got_paths} 個路徑物件。"))
        return issues  # 沒有結構標記：頁面上的物件對不回成品文件的元素，只能比總數
    for im in plain:
        mcids = set(by_id.get(im.id, []))
        if not mcids:
            continue  # 母片上的圖片標成 Artifact，沒有結構元素（它已經算在上面的總數裡）；該有卻沒有的由結構標記那一項回報
        imgs = [o for o in objects if o.mcid in mcids and o.kind == "image"]
        if len(imgs) != 1:
            issues.append(Issue(severity=Severity.ERROR, code="image_missing", element_id=im.id, message=f"這張圖片在輸出檔裡對到 {len(imgs)} 個影像物件（應該恰好 1 個）。"))
            continue
        d = _bounds_diff(imgs[0].bounds, _box_bounds(im.box, H))
        if d > tol:
            issues.append(Issue(severity=Severity.WARNING, code="image_position", element_id=im.id, message=f"圖片的位置與成品文件相差 {d:.2f} pt。"))
    for img_id, vec in as_vector.items():
        mcids = set(by_id.get(vec.id, []))
        if not mcids:
            continue  # 母片上的向量圖標成 Artifact：只算進總數
        mine = [o for o in objects if o.mcid in mcids]
        if any(o.kind == "image" for o in mine):
            issues.append(Issue(severity=Severity.ERROR, code="vector_as_image", element_id=vec.id, message="這個向量圖在輸出檔裡仍是影像物件，沒有畫成路徑。"))
        want = ops_of[img_id]
        got = [o for o in mine if o.kind in ("path", "shading")]
        n_paths = sum(1 for op in want if op["kind"] == "path")
        if [o.kind for o in got] != [op["kind"] for op in want]:
            issues.append(Issue(severity=Severity.ERROR, code="vector_path_count", element_id=vec.id,
                                message=f"向量圖的物件數不符：成品文件有 {len(vec.paths)} 條路徑（應畫成 {n_paths} 個路徑物件、{len(want) - n_paths} 個漸層物件），"
                                        f"輸出檔裡是 {sum(1 for o in got if o.kind == 'path')} 個路徑物件、{sum(1 for o in got if o.kind == 'shading')} 個漸層物件。"))
            continue
        bad: list[str] = []
        for o, op in zip(got, want):
            n = op["index"] + 1
            if op["kind"] == "shading":
                # 漸層物件的外框是裁切範圍（PDFium 回報的是裁切路徑的外框，貝茲曲線時可能比控制點的外框小）
                if any(a < b - tol for a, b in ((o.bounds[0], op["bounds"][0]), (o.bounds[1], op["bounds"][1]), (-o.bounds[2], -op["bounds"][2]), (-o.bounds[3], -op["bounds"][3]))):
                    bad.append(f"第 {n} 條的漸層範圍超出路徑")
                continue
            if _bounds_diff(o.bounds, op["bounds"]) > tol:
                bad.append(f"第 {n} 條的範圍差 {_bounds_diff(o.bounds, op['bounds']):.2f} pt")
            if o.filled != op["filled"] or o.stroked != op["stroked"]:
                bad.append(f"第 {n} 條的填色或筆畫有無不符")
            if op["filled"] and o.fill_rule != op["fill_rule"]:
                bad.append(f"第 {n} 條的填色規則是 {o.fill_rule}（應為 {op['fill_rule']}）")
            if op["stroked"]:
                if abs(o.stroke_width - op["stroke_width"]) > max(0.01, 0.01 * op["stroke_width"]):
                    bad.append(f"第 {n} 條的筆畫寬 {o.stroke_width:.3f} pt（應為 {op['stroke_width']:.3f} pt）")
                if o.stroke_cap != op["stroke_cap"] or o.stroke_join != op["stroke_join"]:
                    bad.append(f"第 {n} 條的端點或接合是 {o.stroke_cap}／{o.stroke_join}（應為 {op['stroke_cap']}／{op['stroke_join']}）")
        if bad:
            issues.append(Issue(severity=Severity.ERROR, code="vector_path_mismatch", element_id=vec.id, message="向量圖的路徑與成品文件不符：" + "；".join(bad[:6]) + ("…" if len(bad) > 6 else "") + "。"))
    return issues


def _check_structure(page: StyledPage, tree: list[StructNode], tags: dict, vectors: dict, drew_vectors: bool, artifact_ids: set[str]) -> list[Issue]:
    """結構樹：每個該有結構元素的段落、表格、圖都在，種類正確，順序就是閱讀順序。"""
    issues: list[Issue] = []
    nodes = [n for top in tree for n in top.walk()]
    by_id: dict[str, StructNode] = {}
    for n in nodes:
        if n.id and n.id not in by_id:
            by_id[n.id] = n
    owner_of: dict[str, str] = {}
    missing: list[str] = []
    wrong: list[str] = []
    for (pg, owner, cell, pid), (tag, _) in tags.items():
        if pg != page.page_index:
            continue
        drawn = any(r.text.strip() for tb in page.text_boxes if tb.id == owner for sp in tb.paragraphs if sp.id == pid for r in sp.runs) or any(
            r.text.strip() for t in page.tables if t.id == owner for c in t.cells if (c.row, c.col) == cell for sp in c.paragraphs if sp.id == pid for r in sp.runs)
        if not drawn:
            continue
        owner_of[pid] = owner
        n = by_id.get(pid)
        if tag.startswith("/Artifact"):
            if n is not None:
                wrong.append(f"{pid} 應是 Artifact，卻有結構元素 {n.type}")
            continue
        if n is None:
            missing.append(pid)
        elif n.type != tag:
            wrong.append(f"{pid} 是 {n.type}（應為 {tag}）")
    for im in page.images:
        if im.id in artifact_ids:  # 母片上的圖片標成 Artifact，不進結構樹
            continue
        vec = vectors.get(im.vector_id) if (drew_vectors and im.vector_id) else None
        eid = vec.id if (vec is not None and vec.paths) else im.id
        owner_of[eid] = im.id
        n = by_id.get(eid)
        if n is None:
            missing.append(eid)
        elif n.type != "Figure":
            wrong.append(f"{eid} 是 {n.type}（應為 Figure）")
        elif not n.alt:
            wrong.append(f"{eid} 的 Figure 沒有替代文字")
    for t in page.tables:
        owner_of[t.id] = t.id
        n = by_id.get(t.id)
        if n is None:
            missing.append(t.id)
            continue
        rows = [k for k in n.kids if k.type == "TR"]
        cells = [c for r in rows for c in r.kids if c.type in ("TH", "TD")]
        if n.type != "Table" or len(rows) != len({c.row for c in t.cells}) or len(cells) != len(t.cells):
            wrong.append(f"表格 {t.id} 的結構是 {n.type}、{len(rows)} 列、{len(cells)} 格（成品文件是 {len({c.row for c in t.cells})} 列、{len(t.cells)} 格）")
    if missing:
        issues.append(Issue(severity=Severity.ERROR, code="struct_missing", message=f"結構樹少了 {len(missing)} 個元素：" + "、".join(missing[:5]) + ("…" if len(missing) > 5 else "") + "。"))
    if wrong:
        issues.append(Issue(severity=Severity.ERROR, code="struct_mismatch", message="結構樹與成品文件不符：" + "；".join(wrong[:5]) + ("…" if len(wrong) > 5 else "") + "。"))
    # 閱讀順序：結構樹裡各元素出現的先後，要與成品文件的閱讀順序一致
    seen: list[str] = []
    for n in nodes:
        owner = owner_of.get(n.id)
        if owner and owner not in seen:
            seen.append(owner)
    rank = pdf_editable._reading_rank(page)
    want = sorted(seen, key=lambda o: rank.get(o, 1 << 30))
    if seen != want:
        issues.append(Issue(severity=Severity.WARNING, code="reading_order", message="結構樹裡元素的順序與成品文件的閱讀順序不一致。"))
    return issues


# ================================================================ 進入點


def compare_text(placed: list[ai_pdf.PlacedLine], got: list[TextLine], width_pt: float) -> tuple[list[_Hit], list[_Char]]:
    """讀回來的文字行與預期的行比對。回傳（每一行預期的比對結果、讀回來但沒有對到任何預期行的字）。"""
    chars = [c for i, line in enumerate(got) for c in _line_chars(line, i, width_pt)]
    hits = _match(_wants_from_placed(placed), chars)
    return hits, [c for c in chars if not c.used]


def compare_source(source: list[TextLine], got: list[TextLine], width_pt: float) -> list[_Hit]:
    """讀回來的文字行與③讀到的來源文字行比對（位置以成品文件的頁寬換算成 pt）。"""
    chars = [c for i, line in enumerate(got) for c in _line_chars(line, i, width_pt)]
    return _match(_wants_from_source(source, width_pt), chars)


def verify(output: OutputFile, specs: list[PageSpec], elements: list[ElementList], layout: Layout, styled: StyledDoc, cfg: Config, store: Store) -> FormatReport:
    from ..stages.s7_verify import similarity  # 延後匯入：s7_verify 會回頭匯入本套件

    p = _params(cfg)
    ep = pdf_editable.params_for(cfg)
    notes: list[str] = []
    pages: list[FormatPageReport] = []
    path = str(store.blob_path(output.file))
    fit = applied_fit(path)
    placed_pages = pdf_editable.placed_lines(styled, cfg, fit)
    vectors = vector_index(styled)
    drew_vectors = bool(ep.get("vectors", True))
    master_ids = {eid for m in styled.masters for eid in m.member_ids}
    doc_lang = ep["lang"] if ep["lang"] != "auto" else (pdf_editable._language(pdf_editable._doc_text(styled), ep) or ep["lang_latin"])
    tags = pdf_editable._assign_tags(styled, ep, master_ids, doc_lang)
    artifact_ids = master_ids if ep.get("master_artifacts", True) else set()
    pdoc = pdf.open_document(path)
    fonts_seen: list[str] = []
    try:
        tagged = is_tagged(pdoc)
        want_tagged = bool(ep["tagged"])
        doc_issues: list[Issue] = []
        if want_tagged and not tagged:
            doc_issues.append(Issue(severity=Severity.ERROR, code="not_tagged", message="輸出器有寫結構標記，但 PDFium 不認為這份檔案是 Tagged PDF。"))
        if len(pdoc) != len(styled.pages):
            doc_issues.append(Issue(severity=Severity.ERROR, code="page_count", message=f"輸出檔有 {len(pdoc)} 頁，成品文件有 {len(styled.pages)} 頁。"))
        for sp in styled.pages:
            i = sp.page_index
            spec = next((s for s in specs if s.page_index == i), None)
            el = next((e for e in elements if e.page_index == i), None)
            issues: list[Issue] = list(doc_issues) if i == 0 else []
            if i >= len(pdoc):
                pages.append(FormatPageReport(page_index=i, issues=issues + [Issue(severity=Severity.ERROR, code="page_missing", message="輸出檔裡沒有這一頁。")]))
                continue
            reader = pdf.PageReader(pdoc, i)
            try:
                image = reader.render(p["render_dpi"])
                got, width = read_lines(reader, i, cfg)
                objects = read_objects(reader) if p["pdf_check_objects"] else []
                tree = read_struct_tree(reader) if tagged else []
                boxes = reader.boxes()
            finally:
                reader.close()
            media = boxes.get("crop", boxes["media"])
            if abs((media[2] - media[0]) - sp.width_pt) > 0.01 or abs((media[3] - media[1]) - sp.height_pt) > 0.01:
                issues.append(Issue(severity=Severity.ERROR, code="page_size", message=f"頁面尺寸 {media[2] - media[0]:.2f} × {media[3] - media[1]:.2f} pt，成品文件是 {sp.width_pt:.2f} × {sp.height_pt:.2f} pt。"))
            # 1. 外觀
            sim: Optional[float] = None
            if spec is not None:
                working = Image.open(store.blob_path(spec.working_image))
                sim = similarity(working, image)
                if sim < p["pdf_similarity_pass"]:
                    issues.append(Issue(severity=Severity.WARNING, code="low_similarity", message=f"輸出檔重新畫出來與原圖的相似度 {sim:.3f}，低於合格線 {p['pdf_similarity_pass']}。"))
            # 2. 文字
            hits, extra = compare_text(placed_pages[i], got, width)
            found = [h for h in hits if h.found]
            extra_lines = len({c.line for c in extra})
            total = len(hits) + extra_lines
            ratio = (len(found) / total) if total else 1.0
            pos = max((h.char_error for h in found), default=None)
            for h in hits:
                if not h.found:
                    issues.append(Issue(severity=Severity.ERROR, code="text_mismatch", element_id=h.want.key,
                                        message=f"這一行在輸出檔裡讀不回來。預期「{_clip(h.want.text)}」，那個位置讀到「{_clip(h.got_text)}」。"))
            if extra:
                sample = "".join(c.ch for c in extra if c.line == extra[0].line)
                issues.append(Issue(severity=Severity.WARNING, code="text_extra", message=f"輸出檔裡多出 {extra_lines} 行成品文件沒有的文字，例如「{_clip(sample)}」。"))
            off = [h for h in found if h.char_error > p["pdf_position_tolerance_pt"]]
            if off:
                worst = max(off, key=lambda h: h.char_error)
                issues.append(Issue(severity=Severity.WARNING, code="position_offset", element_id=worst.want.key,
                                    message=f"有 {len(off)} 行讀回來的位置與排版預期相差超過 {p['pdf_position_tolerance_pt']} pt，最大 {worst.char_error:.2f} pt（「{_clip(worst.want.text)}」）。"))
            sized = [h for h in found if h.size_error > p["pdf_size_tolerance_pt"]]
            if sized:
                worst = max(sized, key=lambda h: h.size_error)
                issues.append(Issue(severity=Severity.WARNING, code="font_size_mismatch", element_id=worst.want.key,
                                    message=f"有 {len(sized)} 行讀回來的字級與成品文件不同，最大相差 {worst.size_error:.2f} pt（「{_clip(worst.want.text)}」）。"))
            odd = [h for h in found if h.families and h.want.families and not ({_fam(f) for f in h.families} <= {_fam(f) for f in h.want.families})]
            if odd:
                issues.append(Issue(severity=Severity.WARNING, code="font_mismatch", element_id=odd[0].want.key,
                                    message=f"有 {len(odd)} 行讀回來的字型（{'、'.join(odd[0].families)}）不是輸出器要內嵌的字型（{'、'.join(odd[0].want.families)}）。"))
            for h in found:
                for f in h.families:
                    if f not in fonts_seen:
                        fonts_seen.append(f)
            # 3. 與來源比對
            if p["pdf_check_source"] and el is not None:
                issues += _source_issues(el, sp, got, width, p)
            # 4. 物件
            by_id: dict[str, list[int]] = {}
            for top in tree:
                for n in top.walk():
                    if n.id:
                        by_id.setdefault(n.id, []).extend(n.mcids)
            if p["pdf_check_objects"]:
                issues += _check_objects(sp, vectors, objects, by_id, tagged and want_tagged, drew_vectors, p["pdf_object_tolerance_pt"])
            # 5. 結構標記
            if p["pdf_check_structure"] and want_tagged and tagged:
                issues += _check_structure(sp, tree, tags, vectors, drew_vectors, artifact_ids)
            pages.append(FormatPageReport(page_index=i, similarity=sim, text_match_ratio=ratio, position_error_pt=pos, issues=issues))
    finally:
        pdoc.close()
    ok = all(not any(x.severity == Severity.ERROR for x in pg.issues) and (pg.text_match_ratio is None or pg.text_match_ratio >= 1.0)
             and (pg.position_error_pt is None or pg.position_error_pt <= p["pdf_position_tolerance_pt"]) for pg in pages)
    if not output.selfcheck_ok:
        ok = False
        notes.append("輸出器的自檢沒有通過（見輸出檔的自檢紀錄）。")
    notes.append("文字一致率 = 文字一致的行數 ÷（預期的行數 ＋ 多出來的行數）；位置誤差 = 文字一致的行裡，每個字的起點與排版預期位置的最大距離。"
                 "預期值是成品文件經輸出器排版後的每一行；與來源文字行的差距另外列在各頁的問題清單（代碼 source_compare）。")
    if fit:
        notes.append(f"這份輸出套用了逐行對位（{sum(len(v) for v in fit.values())} 行的行首位置與字距對回來源的文字行）；預期位置已算入對位。")
    if fonts_seen:
        notes.append("讀回來的字型：" + "、".join(fonts_seen) + "。")
    notes.append("結構標記：" + ("PDFium 認得（Tagged PDF）。" if tagged else ("輸出器已關閉結構標記。" if not want_tagged else "PDFium 不認得。")))
    return FormatReport(format=OutputFormat.PDF, engine=ENGINE, ok=ok, pages=pages, notes=notes, render=output.file)


def _clip(text: str, n: int = 24) -> str:
    text = text.strip()
    return text if len(text) <= n else text[:n] + "…"


def _source_issues(el: ElementList, sp: StyledPage, got: list[TextLine], width: float, p: dict) -> list[Issue]:
    """與③讀到的來源文字行比對。擷取產線的來源位置是精確的，超過容許差就開警告；影像產線只列數字。"""
    hits = compare_source(el.texts, got, width)
    if not hits:
        return []
    found = [h for h in hits if h.found]
    start = max((h.start_error for h in found), default=0.0)
    char = max((h.char_error for h in found), default=0.0)
    size = max((h.size_error for h in found), default=0.0)
    exact = el.production_line.value == "extract"
    kind = "來源是原生 PDF，位置是精確值" if exact else "來源位置是影像量測值，僅供參考"
    out = [Issue(severity=Severity.INFO, code="source_compare",
                 message=f"與來源文字行比對（{kind}）：{len(hits)} 行中 {len(found)} 行文字一致；行首位置最大差 {start:.2f} pt、逐字最大差 {char:.2f} pt、字級最大差 {size:.2f} pt。")]
    edited = any(par.text_locked for tb in sp.text_boxes for par in tb.paragraphs) or any(par.text_locked for t in sp.tables for c in t.cells for par in c.paragraphs)
    if exact and not edited:
        lost = [h for h in hits if not h.found]
        if lost:
            out.append(Issue(severity=Severity.WARNING, code="source_text_missing", element_id=lost[0].want.key,
                             message=f"來源的 {len(lost)} 行文字在輸出檔的原位置讀不到（斷行或位置與原檔不同），例如「{_clip(lost[0].want.text)}」。"))
        far = [h for h in found if h.start_error > p["pdf_source_position_tolerance_pt"]]
        if far:
            worst = max(far, key=lambda h: h.start_error)
            out.append(Issue(severity=Severity.WARNING, code="source_position_offset", element_id=worst.want.key,
                             message=f"有 {len(far)} 行的行首與原檔相差超過 {p['pdf_source_position_tolerance_pt']} pt，最大 {worst.start_error:.2f} pt（「{_clip(worst.want.text)}」）。"))
    return out


# ================================================================ 逐行對位（修正單）


def applied_fit(path: str) -> dict[str, dict]:
    """輸出檔自己記的「套用了哪些逐行對位」（文件資訊的 MortiseLineFit）。沒有就回傳空的。"""
    import json

    import pikepdf

    try:
        with pikepdf.open(path) as f:
            raw_value = f.docinfo.get(pdf_editable.LINE_FIT_KEY)
            return json.loads(str(raw_value)) if raw_value is not None else {}
    except Exception:  # 檔案打不開或內容不是 JSON：當成沒有對位，交給 verify 回報其他問題
        return {}


def _fit_line(ln: ai_pdf.PlacedLine, hit: _Hit, p: dict) -> Optional[list]:
    """一行的對位：[dx, dy, [每一段字的字距], [[第幾個字, 位移], …]]。來源與排版已經夠近、或不該對位時回傳 None。

    ln 是「未對位的排版」的一行；hit.found_at 是來源裡同一串字的位置（以這一行的基線起點為原點）。
    1. 逐字都在容許差內：不必對位。
    2. 字級與來源差超過 2%、或行寬與來源差太多：不對位（多半是使用者改過字級或字型，照來源對位會蓋掉使用者的修正）。
    3. 行首：dx、dy 對到來源第一個字（dy 取各字的中位數）。
    4. 行內：先把字本身的寬度（不含排版為了左右對齊加的字距、詞距）排出來，與來源逐字相減，得到每兩個字之間還差多少。
       行內每一段同字型的字各取一個字距（這些差的中位數）：靠左的行是 0；左右對齊的行，來源軟體多半只撐開中文字之間，
       所以中文那一段有字距、英文那一段沒有。剩下的差沿著行累加，超過容許差才在那個字之前插入一個位移
       （中英交界多出來的間距、左右對齊時被撐開的空白）。
    5. 位移點太多（來源字型的字寬與我們內嵌的處處不同）：不插位移，改成整行一致的字距，讓行寬與來源相同。"""
    tol = p["pdf_fit_tolerance_pt"]
    idx = [j for j, c in enumerate(ln.chars) if _visible(c[0])]  # 看得見的字在整行（含空白）裡的位置
    if len(idx) != len(hit.found_at) or not idx:
        return None
    placed = [ln.chars[j][1] for j in idx]
    src = [a for a, _, _ in hit.found_at]
    if max(abs(a - b) for a, b in zip(src, placed)) <= tol and max(abs(d) for _, d, _ in hit.found_at) <= tol:
        return None
    if any(abs(sz - ln.chars[j][2]) > 0.02 * ln.chars[j][2] for j, (_, _, sz) in zip(idx, hit.found_at)):
        return None
    starts = [0.0]
    for w in ln.widths:
        starts.append(starts[-1] + w)
    natural = [starts[j] for j in idx]
    span_src, span_nat = src[-1] - src[0], natural[-1] - natural[0]
    if abs(span_src - span_nat) > p["pdf_fit_max_width_change"] * max(span_nat, ln.size):
        return None
    across = sorted(d for _, d, _ in hit.found_at)
    dx, dy = src[0] - natural[0], across[len(across) // 2]
    seg_starts = ln.seg_starts or [0]
    seg_of = [0] * len(ln.chars)  # 行內每個字屬於第幾段
    for i, a in enumerate(seg_starts):
        for j in range(a, seg_starts[i + 1] if i + 1 < len(seg_starts) else len(ln.chars)):
            seg_of[j] = i
    extra = [(src[k] - src[k - 1]) - (natural[k] - natural[k - 1]) for k in range(1, len(idx))]  # 第 k 個字之前還差多少
    tcs = []
    for i in range(len(seg_starts)):
        # 這一段裡相鄰兩個字（中間沒有空白）之間的差：字距就是它們的中位數
        inside = sorted(extra[k - 1] for k in range(1, len(idx)) if idx[k] - idx[k - 1] == 1 and seg_of[idx[k]] == i and seg_of[idx[k - 1]] == i)
        tc = inside[len(inside) // 2] if len(inside) >= 3 else 0.0
        length = sum(1 for j in seg_of if j == i)
        tcs.append(tc if abs(tc) * length > tol else 0.0)  # 整段累積起來也不到容許差：不寫字距
    gaps: list[list] = []
    rest = 0.0
    for k in range(1, len(idx)):
        rest += extra[k - 1] - sum(tcs[seg_of[j]] for j in range(idx[k - 1], idx[k]))  # 字距加在每個字（含空白）之後
        if abs(rest) > tol:
            gaps.append([idx[k], round(rest, 3)])
            rest = 0.0
    if len(idx) >= 4 and len(gaps) > p["pdf_fit_max_gap_ratio"] * len(idx):
        tcs, gaps = [(span_src - span_nat) / max(idx[-1] - idx[0], 1)] * len(seg_starts), []
    if abs(dx) <= 1e-3 and abs(dy) <= 1e-3 and not gaps and all(abs(tc) <= 1e-4 for tc in tcs):
        return None
    return [round(dx, 3), round(dy, 3), [round(tc, 4) for tc in tcs], gaps]


def line_fit(styled: StyledDoc, elements: list[ElementList], cfg: Optional[Config]) -> dict[str, dict]:
    """整份文件該有的逐行對位：{元素編號: {行的鍵: [dx, dy, 字距, 位移清單]}}（格式見 ai_pdf._Layout.apply_fit）。
    由「未對位的排版」與③的來源文字行比對得到，所以同一份成品文件每次算出來都一樣。只算擷取產線的頁面。"""
    p = _params(cfg)
    out: dict[str, dict] = {}
    natural = pdf_editable.placed_lines(styled, cfg)
    for sp, placed in zip(styled.pages, natural):
        el = next((e for e in elements if e.page_index == sp.page_index), None)
        if el is None or el.production_line.value != "extract":
            continue
        w = sp.width_pt
        src_chars = [c for i, line in enumerate(el.texts) if not line.hidden for c in _line_chars(line, i, w)]
        for hit in _match(_wants_from_placed(placed), src_chars):
            if not hit.found:
                continue
            ln: ai_pdf.PlacedLine = hit.want.ref
            item = _fit_line(ln, hit, p)
            if item is None:
                continue
            prefix = f"{ln.cell[0]},{ln.cell[1]}/" if ln.cell is not None else ""
            out.setdefault(ln.owner, {})[f"{prefix}{ln.paragraph}/{ln.line_no}"] = item
    return out


def propose(output: OutputFile, specs: list[PageSpec], elements: list[ElementList], layout: Layout, styled: StyledDoc, cfg: Config, store: Store, round_no: int = 0) -> list[Correction]:
    """⑦呼叫：需要逐行對位、而且輸出檔還沒套用（或套用的不是現在該有的）時，開修正單給⑥的可編輯 PDF 輸出器。
    一個文字框或表格一張修正單，內容是該元素全部的行（新的一張會整個取代舊的）。"""
    p = _params(cfg)
    if not p["pdf_fit"] or round_no >= p["max_rounds"] or not pdf_editable.params_for(cfg).get("line_fit", True):
        return []
    want = line_fit(styled, elements, cfg)
    have = applied_fit(str(store.blob_path(output.file)))
    page_of = {tb.id: sp.page_index for sp in styled.pages for tb in [*sp.text_boxes, *sp.tables]}
    out: list[Correction] = []
    for owner in sorted(set(want) | set(have)):
        if want.get(owner, {}) == have.get(owner, {}):
            continue
        out.append(Correction(
            id=f"v{round_no + 1}-{owner}-fit", target_stage=6, target_step="s6.pdf", page_index=page_of.get(owner), element_id=owner,
            field="pdf.line_fit", new_value=want.get(owner, {}), instruction="排版結果與來源文字行的行首位置或字距不同，逐行對回來源。",
            source=CorrectionSource.VERIFY, round=round_no + 1,
        ))
    return out
