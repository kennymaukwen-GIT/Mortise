"""逐行文字對照（⑦驗證用）：③讀到的每一行字，在成品的渲染結果上找不找得到、位置偏了多少、有沒有和別的行疊在一起。

為什麼要有這一項
  整頁相似度（s7_verify.similarity）是把整頁縮到約 480 像素寬、略為模糊後比像素。一頁 A4 上少了十幾行小字，
  或一行標題錯開幾 pt，平均到整頁只差一兩個百分點，分數照樣九十幾分。逐行看，才看得出「內容不見」與「文字錯位」。

做法（只看影像，不讀輸出檔的內部結構，所以任何輸出格式、任何渲染引擎都用同一套，也可以拿去比 7A 的中性渲染）
  1. 墨跡：原圖（工作影像）與成品的渲染結果各自以「這一行附近最常見的顏色」當底色，和底色差得夠多的像素算墨跡
     （0 到 1 的軟值）。深字淺底、淺字深底、有顏色的字都一樣處理。
  2. 分段：每一行依③量到的每個字的位置，切成幾段（每段至少約三個字高寬，在字與字或詞與詞之間切），一段一段比。
     這樣才抓得到「同一行前半對得上、後半錯開」（例如換了字寬不同的字型）。量不到每個字的位置時整行當一段。
  3. 找位置：把原圖這一段的墨跡當樣板，在渲染結果同一位置附近（水平約一個字高、垂直約四分之三個字高的範圍）
     找最像的地方（兩邊先略為模糊、再算正規化相關係數，對字型的細微差異不敏感）。
     相關係數夠高、墨跡量也相當（不是只剩一點殘影），就算找到；找到的地方與原位置的距離就是這一段的偏移。
  4. 一行的結果：
       找到的比例：找到的各段的墨跡量 ÷ 全行的墨跡量。
       偏移（pt）：找到的各段當中偏移最大的那一段（只看墨跡量夠多的段，避免一兩個字的雜訊）。
       疊字：渲染結果在這一行找到的位置上，多出原圖沒有的墨跡（超過這一行本身墨跡量的一定比例）；
             或兩行在成品上找到的位置互相重疊，在原圖上卻沒有重疊。
  5. 判斷：
       找不到（error）：找到的比例低於 line_found_min。
       一部分找不到（warning）：找到的比例低於 line_partial_min。
       和別的行疊在一起（error）。
       位置偏移（warning）：偏移超過 max(line_offset_tolerance_pt, line_offset_tolerance_ratio × 字高)。
  6. 不判斷的行（不計入、不扣分）：不可見的文字（OCR 文字層）、沒有看得見的字、原圖上那一行幾乎沒有墨跡
     （例如原生 PDF 裡被圖片蓋住的字、與底色太接近的字）、使用者改過文字、字型或字級的段落。

逐行分數（line_score，0 到 1；⑦把它與畫面相似度組合成整體分數，公式寫在 stages/s7_verify.py 開頭）
  每一個有問題的行扣「嚴重程度 × 每行份量」：
    每行份量 = max(1 ÷ 判斷的行數, line_min_share)
      ——每一行算一份；但一頁有上百行時，少一行也至少扣 line_min_share，才不會被行數稀釋到看不出來。
    嚴重程度：找不到 1；一部分找不到 = 找不到的比例；疊在一起 0.6；
              位置偏移 0.25 到 0.6（偏移剛超過容許差是 0.25，偏到一個字高以上是 0.6）。
    同一行有好幾種問題時取最重的一種。
  line_score = max(0, 1 − 各行扣分的總和)。沒有任何可判斷的行時不給分數（None），⑦就只看畫面相似度。

設定在 default.toml 的 [verify]（名稱以 line_ 開頭）。
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any, Optional

import cv2
import numpy as np
from PIL import Image

from ..artifacts import ElementList, Issue, PageLayout, Provenance, Severity, StyledPage, TextLine

# 這個模組開出的問題代碼（mortise/api/app.py 的 _flagged 依它們在 Overlay 上畫紅框）
CODES = ("line_missing", "line_partial", "line_overlap", "line_offset")

DEFAULTS: dict[str, Any] = {
    "line_check": True,  # 是否做逐行文字對照
    "line_found_min": 0.5,  # 一行找到的比例低於此值：這一行在成品裡找不到
    "line_partial_min": 0.8,  # 一行找到的比例低於此值：一部分找不到
    "line_ncc_min": 0.5,  # 一段算「找到」的最低相關係數
    "line_mass_min": 0.4,  # 一段算「找到」時，渲染結果的墨跡量至少是原圖的多少倍（太少是殘影或被蓋住）
    "line_extra_max": 0.6,  # 渲染結果在這一行的位置多出的墨跡超過原本的此比例：和別的東西疊在一起
    "line_offset_tolerance_pt": 2.0,  # 位置偏移的容許差（pt）
    "line_offset_tolerance_ratio": 0.2,  # 位置偏移的容許差（字高的比例）；兩者取大
    "line_min_share": 0.02,  # 逐行分數：每一個有問題的行至少扣這麼多（乘上嚴重程度）
    "line_min_ink_px": 12.0,  # 原圖上一行的墨跡少於這麼多像素（以字高 1 的墨跡計）就不判斷
}

_SEVERITY_OVERLAP = 0.6


def params(cfg) -> dict:
    p = dict(DEFAULTS)
    if cfg is not None:
        section = cfg.section("verify")
        p.update({k: v for k, v in section.items() if k in DEFAULTS})
    return p


# ================================================================ 結果


@dataclass
class Chunk:
    x0: float  # 原圖上的範圍（像素，渲染結果的尺度）
    x1: float
    ink: float  # 原圖墨跡量
    found: bool = False
    ncc: float = 0.0
    mass: float = 0.0  # 渲染結果墨跡量 ÷ 原圖墨跡量（找到的位置）
    extra: float = 0.0  # 多出的墨跡 ÷ 原圖墨跡量
    dx: float = 0.0  # 偏移（像素）
    dy: float = 0.0


@dataclass
class LineResult:
    line_id: str
    text: str
    element_id: Optional[str]  # 所屬的文字框或表格（不屬於任何一個時，是包住它的圖片；都沒有就留空）
    box_px: tuple[float, float, float, float]  # 原圖上的範圍（左、上、右、下，像素）
    font_pt: float  # 字高（pt）
    status: str = "ok"  # ok、missing、partial、overlap、offset、skipped
    skip_reason: str = ""
    found: float = 1.0  # 找到的比例（墨跡加權）
    offset_pt: float = 0.0  # 偏移最大的那一段（pt）
    dx_pt: float = 0.0  # 找到的各段的平均偏移（pt）
    dy_pt: float = 0.0
    extra: float = 0.0  # 多出的墨跡 ÷ 原本的墨跡
    overlap_with: Optional[str] = None  # 疊在一起的另一行（行編號）
    severity: float = 0.0  # 嚴重程度（0 到 1）
    chunks: list[Chunk] = field(default_factory=list)


@dataclass
class PageLines:
    lines: list[LineResult]
    score: Optional[float]  # 逐行分數；沒有可判斷的行時為 None
    checked: int
    missing: int
    partial: int
    overlap: int
    offset: int
    issues: list[Issue]

    def summary(self) -> str:
        return (f"逐行文字對照：判斷 {self.checked} 行，找不到 {self.missing} 行、一部分找不到 {self.partial} 行、"
                f"疊在一起 {self.overlap} 行、位置偏移 {self.offset} 行；逐行分數 "
                + ("無" if self.score is None else f"{self.score:.3f}") + "。")


# ================================================================ 影像


def _rgb(image: Image.Image, size: Optional[tuple[int, int]] = None) -> np.ndarray:
    im = image.convert("RGB")
    arr = np.asarray(im)
    if size is not None and im.size != size:
        interp = cv2.INTER_AREA if im.size[0] > size[0] else cv2.INTER_LINEAR
        arr = cv2.resize(arr, size, interpolation=interp)
    return arr.astype(np.float32)


def _ink(region: np.ndarray, lo: float = 16.0, hi: float = 64.0) -> np.ndarray:
    """區域內的墨跡（0 到 1）：與「區域裡最常見的顏色」（逐色版取中位數）差得越多越接近 1。取三個色版差異的最大值，
    有顏色但明度與底色相近的字也看得到。"""
    if region.size == 0:
        return np.zeros(region.shape[:2], np.float32)
    bg = np.median(region.reshape(-1, 3), axis=0)
    d = np.abs(region - bg).max(axis=2)
    return np.clip((d - lo) / (hi - lo), 0.0, 1.0)


# ================================================================ 行 → 元素


def owners(layout: Optional[PageLayout]) -> dict[str, str]:
    """文字行編號 → 所屬的文字框或表格編號（④的版面）。"""
    out: dict[str, str] = {}
    if layout is None:
        return out
    pars = {p.id: p for p in layout.paragraphs}
    for tb in layout.text_boxes:
        for pid in tb.paragraph_ids:
            for lid in pars[pid].line_ids if pid in pars else []:
                out[lid] = tb.id
    for t in layout.tables:
        for cell in t.cells:
            for pid in cell.paragraph_ids:
                for lid in pars[pid].line_ids if pid in pars else []:
                    out[lid] = t.id
    return out


def _user_changed(layout: Optional[PageLayout], page: Optional[StyledPage]) -> set[str]:
    """使用者改過文字、字型或字級的段落裡的行：外觀本來就該與原圖不同，不判斷。"""
    if layout is None or page is None:
        return set()
    pars = {p.id: p for p in layout.paragraphs}
    out: set[str] = set()
    styled_pars = [sp for tb in page.text_boxes for sp in tb.paragraphs] + [sp for t in page.tables for c in t.cells for sp in c.paragraphs]
    for sp in styled_pars:
        changed = sp.text_locked or any(r.size_pt.source == Provenance.USER or r.font.family.source == Provenance.USER for r in sp.runs)
        if changed and sp.id in pars:
            out.update(pars[sp.id].line_ids)
    return out


def _containing_image(page: Optional[StyledPage], cx: float, cy: float, scale: float, ox: float, oy: float) -> Optional[str]:
    """不屬於任何文字框的行（例如④留在圖片裡的字）：包住它中心點的最上層圖片。cx、cy 是相對單位。"""
    if page is None:
        return None
    x, y = ox + cx * scale, oy + cy * scale
    best = None
    for im in page.images:
        b = im.box
        if b.x <= x <= b.x + b.w and b.y <= y <= b.y + b.h and (best is None or im.z > best.z):
            best = im
    return best.id if best is not None else None


# ================================================================ 分段


def _chunks(line: TextLine, W: float, x0: float, x1: float, min_w: float) -> list[tuple[float, float]]:
    """依每個字的位置把一行切成幾段（像素）。每段至少 min_w 寬，盡量在空白處切；量不到字的位置就整行一段。"""
    starts: list[tuple[float, bool]] = []  # （字的起點 x，這個字前面可不可以切：前面是空白，或是中日韓字）
    for r in line.runs:
        vis = [i for i, ch in enumerate(r.text) if ch.strip()]
        if len(vis) != len(r.char_x):
            return [(x0, x1)]
        for i, cx in zip(vis, r.char_x):
            breakable = (i > 0 and not r.text[i - 1].strip()) or (i == 0 and bool(starts)) or ord(r.text[i]) >= 0x2E80
            starts.append((cx * W, breakable))
    if len(starts) < 2:
        return [(x0, x1)]
    starts.sort(key=lambda t: t[0])
    cuts = [x0]
    last = x0
    for nx, breakable in starts[1:]:
        if nx >= x1:
            break
        # 在詞與詞之間（或中日韓字之間）切；一直沒有空白的長字串，寬到兩倍還是切
        if (breakable and nx - last >= min_w) or nx - last >= 2.0 * min_w:
            cuts.append(nx)
            last = nx
    if x1 - cuts[-1] < 0.5 * min_w and len(cuts) > 1:
        cuts.pop()  # 最後一段太短：併進前一段
    cuts.append(x1)
    return [(a, b) for a, b in zip(cuts, cuts[1:]) if b - a > 1.0]


# ================================================================ 比對


def check_page(working: Image.Image, rendered: Image.Image, el: ElementList, layout: Optional[PageLayout], page: Optional[StyledPage],
               frame: tuple[float, float, float], p: Optional[dict] = None, where: str = "") -> PageLines:
    """逐行比對一頁。

    working：工作影像（③的座標所在的影像）。rendered：成品的渲染結果，已裁成與工作影像同一個範圍
    （有輸出尺寸時用 s7_verify.content_region 裁出原始內容那一塊）。尺寸不必相同：工作影像會縮放成渲染結果的大小。
    frame：來源座標換到輸出頁面 pt 的（比例, x 位移, y 位移）（s7_verify.source_frame），用來把像素換成 pt。
    where：問題說明前面加的渲染來源（例如「可編輯 PDF」）；空字串就不加。"""
    p = dict(DEFAULTS) if p is None else {**DEFAULTS, **p}
    Wr, Hr = rendered.size
    R = _rgb(rendered)
    O = _rgb(working, (Wr, Hr))
    W = float(Wr)  # 相對單位 1 = 渲染結果的寬（像素）
    scale_pt = frame[0] / W  # 1 像素 = 多少 pt
    own = owners(layout)
    changed = _user_changed(layout, page)
    results: list[LineResult] = []
    for line in el.texts:
        x0r, y0r, x1r, y1r = line.quad.bbox
        box = (x0r * W, y0r * W, x1r * W, y1r * W)
        font_px = max(line.font_size * W, 1.0)
        eid = own.get(line.id) or _containing_image(page, (x0r + x1r) / 2, (y0r + y1r) / 2, frame[0], frame[1], frame[2])
        res = LineResult(line_id=line.id, text=line.text, element_id=eid, box_px=box, font_pt=font_px * scale_pt)
        results.append(res)
        if line.hidden or not line.text.strip():
            res.status, res.skip_reason = "skipped", "不可見或沒有字"
            continue
        if line.id in changed:
            res.status, res.skip_reason = "skipped", "使用者改過這一段"
            continue
        _match_line(line, res, O, R, W, font_px, p)
    _overlaps(results, p)
    return _summarize(results, scale_pt, p, where)


def _match_line(line: TextLine, res: LineResult, O: np.ndarray, R: np.ndarray, W: float, font_px: float, p: dict) -> None:
    Hr, Wr = R.shape[:2]
    x0, y0, x1, y1 = res.box_px
    h = max(y1 - y0, 1.0)
    unit = max(font_px, 0.6 * h)  # 字高（像素）：取③量的字級，量得太小時以框高補
    pad = max(1.0, 0.1 * h)
    wx = max(4.0, 1.0 * unit)  # 搜尋範圍
    wy = max(3.0, 0.75 * unit)
    ty0, ty1 = int(math.floor(y0 - pad)), int(math.ceil(y1 + pad))
    if ty1 <= 0 or ty0 >= Hr or x1 <= 0 or x0 >= Wr:
        res.status, res.skip_reason = "skipped", "在頁面外"
        return
    ty0, ty1 = max(ty0, 0), min(ty1, Hr)
    # 底色：以這一行連同搜尋範圍的區域估（兩張影像各自估）
    ry0, ry1 = max(0, int(ty0 - wy)), min(Hr, int(ty1 + wy))
    rx0, rx1 = max(0, int(x0 - wx)), min(Wr, int(math.ceil(x1 + wx)))
    ink_o = _ink(O[ry0:ry1, rx0:rx1])
    ink_r = _ink(R[ry0:ry1, rx0:rx1])
    sigma = max(0.7, 0.08 * unit)
    blur_o = cv2.GaussianBlur(ink_o, (0, 0), sigma)
    blur_r = cv2.GaussianBlur(ink_r, (0, 0), sigma)
    rad = max(1, int(round(0.12 * unit)))
    dil_o = cv2.dilate(ink_o, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (2 * rad + 1, 2 * rad + 1)))
    total_ink = float(ink_o[ty0 - ry0:ty1 - ry0, max(0, int(x0) - rx0):int(math.ceil(x1)) - rx0].sum())
    if total_ink < p["line_min_ink_px"] * unit:
        res.status, res.skip_reason = "skipped", "原圖上這一行幾乎沒有墨跡"
        return
    min_w = max(3.0 * unit, 12.0)
    horizontal = line.direction.value == "horizontal" and abs(line.baseline[1].y - line.baseline[0].y) * W <= max(2.0, 0.15 * h)
    spans = _chunks(line, W, x0, x1, min_w) if horizontal else [(x0, x1)]
    for cx0, cx1 in spans:
        a, b = int(math.floor(cx0)), int(math.ceil(cx1))
        a, b = max(a, rx0), min(b, rx1)
        if b - a < 2:
            continue
        T = blur_o[ty0 - ry0:ty1 - ry0, a - rx0:b - rx0]
        T_raw = ink_o[ty0 - ry0:ty1 - ry0, a - rx0:b - rx0]
        ink = float(T_raw.sum())
        ch = Chunk(x0=cx0, x1=cx1, ink=ink)
        res.chunks.append(ch)
        if ink < 0.5 * unit or float(T.std()) < 1e-4:
            ch.ink = 0.0  # 這一段幾乎沒有墨跡（例如只有標點）：不判斷
            continue
        # 搜尋區（相對於 ink 區域的座標）
        sx0, sx1 = max(0, a - rx0 - int(wx)), min(ink_r.shape[1], b - rx0 + int(wx))
        sy0, sy1 = max(0, ty0 - ry0 - int(wy)), min(ink_r.shape[0], ty1 - ry0 + int(wy))
        S = blur_r[sy0:sy1, sx0:sx1]
        if S.shape[0] < T.shape[0] or S.shape[1] < T.shape[1]:
            continue
        if float(S.max()) <= 1e-6:
            continue  # 渲染結果這一帶完全沒有墨跡
        score = cv2.matchTemplate(S, T, cv2.TM_CCOEFF_NORMED)
        score = np.nan_to_num(score, nan=0.0, posinf=0.0, neginf=0.0)
        # 偏好離原位置近的地方：相關係數相同時取距離最近的（扣一點點距離）
        oy_idx = np.arange(score.shape[0])[:, None] + sy0 - (ty0 - ry0)
        ox_idx = np.arange(score.shape[1])[None, :] + sx0 - (a - rx0)
        score_d = score - 0.02 * np.hypot(ox_idx / max(wx, 1.0), oy_idx / max(wy, 1.0))
        iy, ix = np.unravel_index(int(np.argmax(score_d)), score_d.shape)
        ch.ncc = float(score[iy, ix])
        ch.dx, ch.dy = float(ox_idx[0, ix]), float(oy_idx[iy, 0])
        py0, px0 = sy0 + iy, sx0 + ix
        patch_raw = ink_r[py0:py0 + T.shape[0], px0:px0 + T.shape[1]]
        ch.mass = float(patch_raw.sum()) / max(ink, 1e-6)
        dil = dil_o[ty0 - ry0:ty1 - ry0, a - rx0:b - rx0]
        ch.extra = float(np.clip(patch_raw - dil, 0.0, None).sum()) / max(ink, 1e-6)
        ch.found = ch.ncc >= p["line_ncc_min"] and ch.mass >= p["line_mass_min"]
    judged = [c for c in res.chunks if c.ink > 0]
    total = sum(c.ink for c in judged)
    if total <= 0:
        res.status, res.skip_reason = "skipped", "原圖上這一行幾乎沒有墨跡"
        return
    found = [c for c in judged if c.found]
    res.found = sum(c.ink for c in found) / total
    if found:
        fw = sum(c.ink for c in found)
        res.dx_pt = sum(c.dx * c.ink for c in found) / fw
        res.dy_pt = sum(c.dy * c.ink for c in found) / fw
        big = [c for c in found if c.ink >= 0.15 * total] or found
        res.offset_pt = max(math.hypot(c.dx, c.dy) for c in big)
        res.extra = sum(c.extra * c.ink for c in found) / fw
    # 先以像素記，_summarize 再換成 pt


def _overlaps(results: list[LineResult], p: dict) -> None:
    """兩行在成品上找到的位置互相重疊、在原圖上卻沒有：兩行都記成疊在一起。
    只看每一行中間那一半高度（上下各去掉四分之一），行距很緊的原稿本來就會碰到的上下緣不算。"""
    boxes = []
    for r in results:
        if r.status == "skipped":
            continue
        x0, y0, x1, y1 = r.box_px
        h = y1 - y0
        for c in r.chunks:
            if c.ink <= 0 or not c.found:
                continue
            core = (c.x0, y0 + 0.25 * h, c.x1, y1 - 0.25 * h)
            moved = (core[0] + c.dx, core[1] + c.dy, core[2] + c.dx, core[3] + c.dy)
            boxes.append((r, core, moved))

    def inter(a, b) -> float:
        w = min(a[2], b[2]) - max(a[0], b[0])
        hh = min(a[3], b[3]) - max(a[1], b[1])
        return max(0.0, w) * max(0.0, hh)

    for i in range(len(boxes)):
        ri, ci, mi = boxes[i]
        ai = (mi[2] - mi[0]) * (mi[3] - mi[1])
        for j in range(i + 1, len(boxes)):
            rj, cj, mj = boxes[j]
            if rj is ri:
                continue
            aj = (mj[2] - mj[0]) * (mj[3] - mj[1])
            if inter(mi, mj) > 0.3 * min(ai, aj) and inter(ci, cj) <= 0.05 * min(ai, aj):
                ri.overlap_with = ri.overlap_with or rj.line_id
                rj.overlap_with = rj.overlap_with or ri.line_id


def _clip(text: str, n: int = 24) -> str:
    text = text.strip()
    return text if len(text) <= n else text[:n] + "…"


def _summarize(results: list[LineResult], scale_pt: float, p: dict, where: str) -> PageLines:
    by_id = {r.line_id: r for r in results}
    checked = [r for r in results if r.status != "skipped"]
    n = len(checked)
    share = max(1.0 / n, float(p["line_min_share"])) if n else 0.0
    issues: list[Issue] = []
    counts = {"missing": 0, "partial": 0, "overlap": 0, "offset": 0}
    prefix = f"{where}：" if where else ""
    deduct = 0.0
    for r in checked:
        r.offset_pt *= scale_pt
        r.dx_pt *= scale_pt
        r.dy_pt *= scale_pt
        tol = max(float(p["line_offset_tolerance_pt"]), float(p["line_offset_tolerance_ratio"]) * r.font_pt)
        quote = f"「{_clip(r.text)}」"
        cands: list[tuple[float, str, Severity, str, str]] = []  # （嚴重程度、狀態、等級、代碼、說明）
        if r.found < p["line_found_min"]:
            cands.append((1.0, "missing", Severity.ERROR, "line_missing", f"{prefix}這一行在成品裡找不到：{quote}（原位置附近只找到 {r.found:.0%}）。"))
        elif r.found < p["line_partial_min"]:
            cands.append((1.0 - r.found, "partial", Severity.WARNING, "line_partial", f"{prefix}這一行有一部分在成品裡找不到：{quote}（只找到 {r.found:.0%}）。"))
        if r.found >= p["line_found_min"]:
            other = by_id.get(r.overlap_with) if r.overlap_with else None
            if other is not None:
                cands.append((_SEVERITY_OVERLAP, "overlap", Severity.ERROR, "line_overlap", f"{prefix}這一行和{('「' + _clip(other.text) + '」') if other else '別的行'}疊在一起：{quote}。"))
            elif r.extra > p["line_extra_max"]:
                cands.append((_SEVERITY_OVERLAP, "overlap", Severity.ERROR, "line_overlap",
                              f"{prefix}這一行的位置上多畫了原圖沒有的東西（多出約 {r.extra:.0%} 的墨跡），多半是和別的行疊在一起：{quote}。"))
            if r.offset_pt > tol:
                sev = 0.25 + 0.35 * min(1.0, (r.offset_pt - tol) / max(r.font_pt, 1e-6))
                cands.append((sev, "offset", Severity.WARNING, "line_offset", f"{prefix}這一行的位置與原圖相差 {r.offset_pt:.1f} pt（容許 {tol:.1f} pt）：{quote}。"))
        if not cands:
            continue
        worst = max(cands, key=lambda c: c[0])
        r.status, r.severity = worst[1], worst[0]
        deduct += worst[0] * share
        for sev, status, level, code, msg in cands:
            counts[status] += 1
            issues.append(Issue(severity=level, code=code, element_id=r.element_id, message=msg))
    score = max(0.0, 1.0 - deduct) if n else None
    return PageLines(lines=results, score=score, checked=n, missing=counts["missing"], partial=counts["partial"],
                     overlap=counts["overlap"], offset=counts["offset"], issues=issues)
