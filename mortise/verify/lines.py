"""逐行文字對照（⑦驗證用）：③讀到的每一行字，在成品的渲染結果上找不找得到、位置偏了多少、有沒有和別的行疊在一起。

為什麼要有這一項
  整頁相似度（s7_verify.similarity）是把整頁縮到約 480 像素寬、略為模糊後比像素。一頁 A4 上少了十幾行小字，
  或一行標題錯開幾 pt，平均到整頁只差一兩個百分點，分數照樣九十幾分。逐行看，才看得出「內容不見」與「文字錯位」。

做法（影像為主；輸出檔讀得回文字時另外比對文字層。任何輸出格式、任何渲染引擎都用同一套，也可以拿去比 7A 的中性渲染）
  1. 墨跡：原圖（工作影像）與成品的渲染結果各自以「這一行附近最常見的顏色」當底色，和底色差得夠多的像素算墨跡
     （0 到 1 的軟值）。深字淺底、淺字深底、有顏色的字都一樣處理。工作影像先縮放成渲染結果的大小。
  2. 整行找位置：原圖這一行的墨跡當樣板，在渲染結果同一位置附近（水平約一個字高、垂直約四分之三個字高）
     找最像的地方（兩邊先略為模糊、再算正規化相關係數，對換字型的細微差異不敏感；離原位置越遠要越像才選）。
  3. 分段：依③量到的每個字的位置把一行切成幾段（每段至少約兩個半字高寬，在詞與詞或中日韓字之間切），
     每一段只在「原位置」與「整行找到的位置」附近的小範圍（約 0.4 個字高）找。這樣抓得到「同一行前半對得上、
     後半錯開」（例如換了字寬不同的字型、中英交界的間距不同），也不會把一段誤認成隔壁的詞。
     一段算找到：相關係數至少 line_ncc_min，而且那裡的墨跡量至少是原圖的 line_mass_min 倍（不是殘影或被蓋住）。
  4. 一行的影像結果：
       找到的比例：找到的各段的墨跡量 ÷ 全行的墨跡量。
       偏移（pt）：找到的各段當中偏移最大的那一段與原位置的距離。
       疊字：(a) 有些段在找到的位置上，字的中段（去掉上下各兩成）墨跡變成原圖的 line_overlap_mass 倍以上——
             別的字疊了上來（行距很緊時上下行字母的上下緣碰進來不算，因為只看中段）；這些段佔全行至少 line_crowded_share，
             或有一段到 line_overlap_mass_strong 倍（不論佔多少）。一到三個字的短行（頁碼）換了字型中段墨跡就差很多，只認後者。
             (b) 兩行在成品上找到的位置互相重疊，在原圖上卻沒有。
       被畫到別處：原位置找不到的行，在上下各 line_relocate_rows 個字高的範圍裡整行再找一次（要更像：line_relocate_ncc）；
             找到了、而且落點上那一行的中段墨跡真的變多，就記成「被畫到那裡、和那一行疊在一起」。
       字的顏色：找到的段裡「確定是字」的像素，原圖與成品各取「字的顏色 − 底色」的中位數，比兩個方向的夾角
             （反鋸齒與縮放會把細筆畫的顏色往底色拉淡，深淺量不準，但往哪個方向淡是一樣的）；超過 line_color_angle 度算顏色不同。
  5. 文字層（輸出檔讀得回文字時：可編輯 PDF、SVG、LibreOffice 的渲染、中性渲染）：影像只看墨跡的形狀，長度相近的詞
     形狀幾乎一樣——換行後別的詞剛好落在原位置，影像會說「找到了」。所以把讀回來的每個字分給它所在的那一行
     （影像找到這一行的，用找到的位置），看那裡寫的是不是這一行的字：
       這一行的字有八成以上在那裡：字在原位置；那裡又多出別行的字 → 兩行疊在一起；影像卻對不上 → 字在但畫出來的樣子不對
         （字級、字型、行距不同），或畫出來看不見（被蓋住、顏色和底色太接近）。
       只有一部分在那裡、其餘在這一頁的別處 → 換行與原圖不同；在這一頁哪裡都讀不到 → 字不在成品裡。
       原位置上沒有字、但這一頁別處讀得到 → 字在檔案裡、沒有畫在原位置（若落在別行上，說出疊在哪一行）。
       讀回來、但不落在任何一行範圍裡的字 → 成品上多出原圖沒有的字（換行後被擠出框外、或多畫了一次），
         依字的內容對回原本的那一行，紅框畫在那一行所屬的元素上。
     很短的行（頁碼、一兩個字）只認「讀回來的某一行整行就是它」或「它出現在沒分給任何一行的字裡」，
     不然「1」幾乎一定出現在別行的「12%」裡。旋轉、直排的行只靠影像判斷。
     沒有文字層時（例如文字轉成外框的 SVG）改用「墨跡守恆」：同一個元素裡多數行在原位置找不到、但元素範圍內畫出來的墨跡
     仍有原圖的 line_conserve_min 倍以上，就說「字多半還在，只是位置偏太多、字級不同或疊在一起」，不說「找不到」。
  6. 判斷（每一個都開成一條問題，element_id 指到這一行所屬的文字框或表格；不屬於任何一個時指到包住它的圖片；
     同一行同一種問題只開一條）：
       找不到（line_missing，error）：影像找到的比例低於 line_found_min，文字層也沒有這些字。
       字在但不在原位置或樣子不對（line_misplaced，error）。
       換行與原圖不同（line_rewrapped，原位置上這一行的字不到一半時是 error，否則 warning）。
       一部分找不到（line_partial，warning）：找到的比例低於 line_partial_min。
       和別的行疊在一起（line_overlap，error）。
       位置偏移（line_offset，warning）：偏移超過 max(line_offset_tolerance_pt, line_offset_tolerance_ratio × 字高)。
         渲染結果若是替身引擎自己重新排字的（LibreOffice 代替 PowerPoint、Word，中性渲染），它的字距與項目符號後的
         間距本來就和目標軟體不同：水平方向放寬到 line_offset_relayout_ratio 個字高，垂直方向不放寬。
         可編輯 PDF 與 SVG 的每一行位置是寫死在檔案裡的，不放寬。
       字的顏色不同（line_color，warning）。
       多出原圖沒有的字（line_extra，warning）。
  7. 不判斷的行（不計入、不扣分）：不可見的文字（OCR 文字層）、沒有看得見的字、原圖上那一行幾乎沒有墨跡
     （少於「字高的平方 × line_min_ink × 字數（最多算 2 個字）」：墨跡量與字高的平方成正比，門檻也跟著走，
     縮小頁面、降低解析度時頁碼這種短行不會被略過；略過的是被圖片蓋住、或與底色太接近的字）、使用者改過文字、字型或字級的段落；
     以及含有「驗證用的電腦沒有字型可以畫出來」的字的行（只限不內嵌字型的格式，例如 SVG、Word、PowerPoint：
     使用者的電腦有字型就看得到；摘要裡會註明有幾行沒有驗證到）；同一個框裡的其他行也不判斷（畫不出來的字換了寬度，
     整段的換行都會跟著變）。可編輯 PDF 內嵌字型，畫不出來就是真的缺字，照常判斷。

逐行分數（line_score，0 到 1；⑦把它乘上畫面分數成為這一頁的分數，為什麼這樣組合寫在 stages/s7_verify.py 開頭）
  逐行分數 = 1 − 各行嚴重程度的總和 ÷ 判斷的行數 − min(line_problem_cap, line_problem_floor × 內容問題的嚴重程度總和)
    第一項：每一行算一份（沒問題的行是 0）；多出原圖沒有的字也各算一份嚴重程度。
    第二項只算內容問題（找不到、字不在原位置、換行不同、一部分找不到、疊在一起）：長文件少一行也必須看得出來，
    所以每個再多扣 2%，最多多扣 10%（問題很多時第一項已經反映嚴重程度）。位置偏移、顏色不同不多扣。
  嚴重程度：找不到 1；字在但不在原位置或樣子不對 0.6；疊在一起 0.6；換行不同 最多 0.5（原位置上這一行的字越少越重）；
            一部分找不到 = 不見的比例；字的顏色不同 0.2；多出原圖沒有的一行字 0.2；
            位置偏移 0.15 到 0.35（剛超過容許差是 0.15，再多偏一個字高以上是 0.35），再乘上偏掉的段佔這一行的比例
            （整行都偏了算滿；換了字寬不同的字型、只有後半行漸漸偏掉的，只算偏掉的那部分）。
  同一行有好幾種問題時取最重的一種計分（每一種都開問題）。沒有任何可判斷的行時不給分數（None），⑦就只看畫面分數。

設定在 default.toml 的 [verify]（名稱以 line_ 開頭）。
"""

from __future__ import annotations

import difflib
import math
import unicodedata
from dataclasses import dataclass, field
from typing import Any, Optional

import cv2
import numpy as np
from PIL import Image

from ..artifacts import ElementList, Issue, PageLayout, Provenance, Severity, StyledPage, TextLine

# 這個模組開出的問題代碼（mortise/api/app.py 的 _flagged 依它們在 Overlay 上畫紅框）
CODES = ("line_missing", "line_partial", "line_overlap", "line_offset", "line_rewrapped", "line_misplaced", "line_color", "line_extra")
STATUSES = ("missing", "partial", "overlap", "offset", "rewrapped", "misplaced", "color")

DEFAULTS: dict[str, Any] = {
    "line_check": True,  # 是否做逐行文字對照
    "line_found_min": 0.5,  # 一行找到的比例低於此值：這一行在成品裡找不到
    "line_partial_min": 0.8,  # 一行找到的比例低於此值：一部分找不到
    "line_ncc_min": 0.5,  # 一段算「找到」的最低相關係數
    "line_mass_min": 0.4,  # 一段算「找到」時，渲染結果的墨跡量至少是原圖的多少倍（太少是殘影或被蓋住）
    "line_overlap_mass": 1.4,  # 疊字：一段字的中段墨跡至少是原圖的此倍數，就算有東西疊上來
    "line_overlap_mass_strong": 1.6,  # 疊字：有一段的中段墨跡到這個倍數，不論佔全行多少都算疊在一起
    "line_color_angle": 20.0,  # 字的顏色：兩邊「字的顏色 − 底色」方向的夾角超過此值（度）就算顏色不同
    "line_conserve_min": 0.6,  # 沒有文字層時：找不到的行所在的範圍裡墨跡至少是原圖的此倍數，就改記成「字還在、位置不對」
    "line_crowded_share": 0.25,  # 疊字：這樣的段佔全行墨跡至少此比例，這一行就算和別的東西疊在一起
    "line_offset_tolerance_pt": 2.5,  # 位置偏移的容許差（pt）
    "line_offset_tolerance_ratio": 0.1,  # 位置偏移的容許差（字高的比例）；兩者取大
    "line_offset_relayout_ratio": 0.35,  # 替身引擎（LibreOffice 代替 PowerPoint、中性渲染代替 Word）：水平偏移在此比例個字高以內不算
    "line_problem_floor": 0.02,  # 逐行分數：每一個內容問題（找不到、疊在一起）另外至少扣這麼多（乘上嚴重程度）
    "line_problem_cap": 0.10,  # 上一項最多扣到這麼多
    "line_min_ink": 0.02,  # 原圖上一行的墨跡少於「字高的平方 × 此值 × 字數（最多算 2 個字）」就不判斷（被蓋住或與底色太接近的字）
    "line_relocate_rows": 3.0,  # 找不到的行：上下各這麼多個字高的範圍裡再找一次，看是不是被畫到別處
    "line_relocate_ncc": 0.7,  # 上一項要多像才算找到（比一般的段落嚴格：範圍大，容易誤認）
}

_OVERLAP_SEVERITY = 0.6  # 疊在一起的嚴重程度（找不到是 1）
_MISPLACED_SEVERITY = 0.6  # 字還在、但沒有畫在原位置或樣子對不上（位置偏太多、字級不同）
_REWRAP_SEVERITY = 0.5  # 換行與原圖不同（原位置上是別的字，這一行的字被排到別處）：整行都換掉時
_COLOR_SEVERITY = 0.2  # 字的顏色不同（字在、讀得懂）
_EXTRA_SEVERITY = 0.2  # 成品上多出原圖沒有的一行字（每一行）
_OFFSET_SEVERITY = (0.15, 0.35)  # 位置偏移的嚴重程度：剛超過容許差、多偏一個字高以上


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
    core_mass: float = 0.0  # 同上，只算字的中段（去掉上下各兩成）
    extra: float = 0.0  # 多出、原圖附近沒有的墨跡 ÷ 原圖墨跡量（只供參考）
    dx: float = 0.0  # 偏移（像素）
    dy: float = 0.0
    crowded: bool = False  # 找到的位置上，字的中段墨跡明顯變多（有東西疊上來）


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
    mass: float = 0.0  # 找到的位置上，渲染結果的墨跡量 ÷ 原圖的墨跡量
    crowded: float = 0.0  # 中段墨跡明顯變多的段佔全行墨跡的比例
    overlap_with: Optional[str] = None  # 疊在一起的另一行（行編號）
    moved_to: Optional[tuple[float, float]] = None  # 找不到的行：在附近別的位置找到它（偏移，像素）
    unit_px: float = 0.0  # 字高（像素）
    severity: float = 0.0  # 嚴重程度（0 到 1）
    chunks: list[Chunk] = field(default_factory=list)
    ink: float = 0.0  # 原圖這一行的墨跡量（以字高的平方為單位）
    place_mass: float = 0.0  # 原位置（不偏移）上渲染結果的墨跡量 ÷ 原圖這一行的墨跡量
    color_angle: Optional[float] = None  # 字的顏色差：兩邊「字的顏色 − 底色」方向的夾角（度；量不到是 None）
    color_orig: Optional[tuple[int, int, int]] = None
    color_rend: Optional[tuple[int, int, int]] = None
    # 文字層（輸出檔讀得回文字時才有）：這一行的位置上讀到的字與這一行的字比對
    near_text: Optional[str] = None  # 這一行的位置（或影像找到它的位置）上讀到的字（不計空白；依讀回的行、再依 x 排）
    text_cover: Optional[float] = None  # 這一行的字有多少比例出現在那個位置
    text_extra: float = 0.0  # 那個位置上多出、不屬於這一行的字 ÷ 這一行的字數
    text_elsewhere: bool = False  # 這一行的字有沒有出現在這一頁讀回來的文字裡（任何位置）
    text_overlap_with: Optional[str] = None  # 多出來的字屬於哪一行
    horizontal: bool = True
    conserved: bool = False  # 沒有文字層時：找不到，但所在範圍的墨跡量正常（字多半還在）


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
    no_font: int = 0  # 因為驗證用的電腦沒有字型而沒有判斷的行數
    no_font_example: str = ""
    rewrapped: int = 0  # 換行或位置與原圖不同（原位置上是別的字）
    misplaced: int = 0  # 字還在，但沒有畫在原位置或樣子對不上
    color: int = 0  # 字的顏色不同
    extra: int = 0  # 成品上多出原圖沒有的字（讀回來的行數）
    text_layer: bool = False  # 有沒有用到輸出檔的文字層

    def summary(self) -> str:
        more = "".join(f"、{name} {v} 行" for name, v in (("換行與原圖不同", self.rewrapped), ("字在但位置或樣子不對", self.misplaced),
                                                         ("字的顏色不同", self.color), ("多出原圖沒有的字", self.extra)) if v)
        text = (f"逐行文字對照{'（影像＋文字層）' if self.text_layer else '（只看影像）'}：判斷 {self.checked} 行，找不到 {self.missing} 行、"
                f"一部分找不到 {self.partial} 行、疊在一起 {self.overlap} 行、位置偏移 {self.offset} 行{more}；逐行分數 "
                + ("無" if self.score is None else f"{self.score:.3f}") + "。")
        skipped = [r for r in self.lines if r.status == "skipped" and r.skip_reason == "原圖上這一行幾乎沒有墨跡"]
        if skipped:
            text += f"另有 {len(skipped)} 行因為原圖上幾乎沒有墨跡（被蓋住或與底色太接近）沒有判斷，例如「{_clip(skipped[0].text, 12)}」。"
        if self.no_font:
            text += f"另有 {self.no_font} 行沒有判斷：{self.no_font_example}（輸出檔沒有內嵌字型，你的電腦有字型就看得到；這些行不扣分，但也沒有驗證到）。"
        return text


# ================================================================ 讀回的文字層


@dataclass
class Glyph:
    """從輸出檔讀回來的一個字（輸出頁面 pt，原點左上、y 向下）：x 是字的起點、y 是基線，size 是字級。"""

    ch: str
    x: float
    y: float
    size: float
    line: int = -1  # 讀回來的第幾行（同一行的字編號相同；找「多出來的字」時用來分組）


def glyphs_from_lines(lines: list[TextLine], width_pt: float) -> list[Glyph]:
    """讀回來的文字行（頁寬為 1 的相對座標，③的擷取函式的結果）→ 每個字在輸出頁面上的位置。
    字的位置沿基線量（runs 的 char_x）；量不到時把整行的字平均分配在行的範圍裡。旋轉的行照它的方向排。"""
    out: list[Glyph] = []
    for n, line in enumerate(lines):
        b0, b1 = line.baseline
        dx, dy = b1.x - b0.x, b1.y - b0.y
        length = math.hypot(dx, dy)
        ux, uy = (dx / length, dy / length) if length > 1e-9 else (1.0, 0.0)
        starts = [cx for r in line.runs for cx in r.char_x]
        base = min(starts) if starts else 0.0
        vis_all = [(ch, r) for r in line.runs for ch in r.text if ch.strip()]
        even = not starts or len(starts) != len(vis_all)
        k = 0
        for r in line.runs:
            size = float(r.font_size.value) * width_pt
            vis = [ch for ch in r.text if ch.strip()]
            xs = list(r.char_x)
            for j, ch in enumerate(vis):
                s = (k / max(1, len(vis_all))) * length if even else ((xs[j] if j < len(xs) else base) - base)
                out.append(Glyph(ch=ch, x=(b0.x + ux * s) * width_pt, y=(b0.y + uy * s) * width_pt, size=size, line=n))
                k += 1
    return out


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


def _color_angle(a: np.ndarray, b: np.ndarray) -> Optional[float]:
    """字的顏色差：兩邊「字的顏色 − 底色」這個方向（RGB）的夾角（度）。
    為什麼比方向而不是比顏色本身：小字的筆畫只有一兩個像素寬，反鋸齒與縮放會把字的顏色往底色拉淡，
    同一個顏色的字在原圖（縮放過的工作影像）與渲染結果上的深淺差很多（實測灰字 157 對 68）；但往哪個方向淡是一樣的。
    所以黑字變紅字（夾角約 30 度）、白字變黃字看得出來，深灰變黑這種只差深淺的不算（量不準）。差太小時回傳 None。"""
    na, nb = float(np.linalg.norm(a)), float(np.linalg.norm(b))
    if na < 30.0 or nb < 30.0:
        return None
    cos = float(np.dot(a, b)) / (na * nb)
    return math.degrees(math.acos(max(-1.0, min(1.0, cos))))


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


def _even(x0: float, x1: float, min_w: float) -> list[tuple[float, float]]:
    n = max(1, int((x1 - x0) // min_w))
    step = (x1 - x0) / n
    return [(x0 + i * step, x0 + (i + 1) * step) for i in range(n)]


def _chunks(line: TextLine, W: float, x0: float, x1: float, min_w: float) -> list[tuple[float, float]]:
    """依每個字的位置把一行切成幾段（像素）。每段至少 min_w 寬，盡量在空白處切；量不到字的位置就平均切。"""
    starts: list[tuple[float, bool]] = []  # （字的起點 x，這個字前面可不可以切：前面是空白，或是中日韓字）
    all_x = [cx for r in line.runs for cx in r.char_x]
    base = min(all_x) if all_x else 0.0  # 字的位置是沿基線、從行首量起（與 pdf_target 讀法相同）
    origin = line.baseline[0].x
    for r in line.runs:
        vis = [i for i, ch in enumerate(r.text) if ch.strip()]
        if len(vis) != len(r.char_x):
            return _even(x0, x1, min_w)
        for i, cx in zip(vis, r.char_x):
            breakable = (i > 0 and not r.text[i - 1].strip()) or (i == 0 and bool(starts)) or ord(r.text[i]) >= 0x2E80
            starts.append(((origin + cx - base) * W, breakable))
    if len(starts) < 2:
        return _even(x0, x1, min_w)
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
               frame: tuple[float, float, float], p: Optional[dict] = None, where: str = "", relayout: bool = False,
               unrenderable: frozenset[str] = frozenset(), glyphs: Optional[list[Glyph]] = None) -> PageLines:
    """逐行比對一頁。

    working：工作影像（③的座標所在的影像）。rendered：成品的渲染結果，已裁成與工作影像同一個範圍
    （有輸出尺寸時用 s7_verify.content_region 裁出原始內容那一塊）。尺寸不必相同：工作影像會縮放成渲染結果的大小。
    frame：來源座標換到輸出頁面 pt 的（比例, x 位移, y 位移）（s7_verify.source_frame），用來把像素換成 pt。
    where：問題說明前面加的渲染來源（例如「可編輯 PDF」）；空字串就不加。
    relayout：這份渲染是「替身引擎自己重新排字」的結果（例如用 LibreOffice 代替 PowerPoint）：它的字距、項目符號後的間距
      本來就和目標軟體不同，所以水平方向的偏移在 line_offset_relayout_ratio 個字高以內不算（垂直方向照常）。
    unrenderable：驗證用的這台電腦沒有字型可以畫出來的字（輸出檔不內嵌字型時，使用者的電腦有字型就看得到）。
      含有這些字的行不判斷，只在摘要裡註明，不當成「找不到」。"""
    p = dict(DEFAULTS) if p is None else {**DEFAULTS, **p}
    Wr, Hr = rendered.size
    R = _rgb(rendered)
    O = _rgb(working, (Wr, Hr))
    W = float(Wr)  # 相對單位 1 = 渲染結果的寬（像素）
    scale_pt = frame[0] / W  # 1 像素 = 多少 pt
    own = owners(layout)
    changed = _user_changed(layout, page)
    # 畫不出來的字會用別的寬度（或缺字方塊）排版，同一個框裡其他行的換行也跟著變：整個框都不判斷
    lacking_boxes = {own[t.id] for t in el.texts if t.id in own and any(ch in unrenderable for ch in t.text)}
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
        lacking = [ch for ch in line.text if ch in unrenderable]
        if lacking:
            res.status, res.skip_reason = "skipped", f"驗證用的電腦沒有字型可以畫出「{lacking[0]}」"
            continue
        if own.get(line.id) in lacking_boxes:
            res.status, res.skip_reason = "skipped", "驗證用的電腦沒有字型可以畫出同一個框裡的字（換行會跟著變）"
            continue
        _match_line(line, res, O, R, W, font_px, p)
    _relocate(results, O, R, p)
    _overlaps(results, p)
    extras: list[tuple[str, Optional[str]]] = []
    if glyphs is not None:
        extras = _text_layer(results, glyphs, frame, W, page, p)
    else:
        _conserve(results, O, R, p)
    return _summarize(results, scale_pt, p, where, relayout, extras, glyphs is not None)


def _best(score: np.ndarray, ox: np.ndarray, oy: np.ndarray, centers: list[tuple[float, float]], radius: tuple[float, float],
          penalty: float) -> Optional[tuple[int, int]]:
    """在相關係數圖上，只看離 centers 任一點不超過 radius（水平、垂直）的位置，取「相關係數 − 距離懲罰」最大的。
    ox、oy 是每個位置對應的偏移（像素）。距離懲罰讓離預期位置遠的地方要更像才會被選上，減少誤認成隔壁的字。"""
    rx, ry = max(radius[0], 1e-6), max(radius[1], 1e-6)
    best_val, best_at = None, None
    for cx, cy in centers:
        nx = (ox - cx) / rx
        ny = (oy - cy) / ry
        inside = (np.abs(nx) <= 1.0) & (np.abs(ny) <= 1.0)
        if not inside.any():
            continue
        val = np.where(inside, score - penalty * np.hypot(nx, ny), -np.inf)
        iy, ix = np.unravel_index(int(np.argmax(val)), val.shape)
        if best_val is None or val[iy, ix] > best_val:
            best_val, best_at = float(val[iy, ix]), (int(iy), int(ix))
    return best_at


def _match_line(line: TextLine, res: LineResult, O: np.ndarray, R: np.ndarray, W: float, font_px: float, p: dict) -> None:
    """一行的比對，分兩步：
    1. 整行當一個樣板，在較大的範圍（水平約一個字高、垂直約四分之三個字高）找整行的位置。整行很長，不容易誤認成別行。
    2. 每一段只在「原位置」與「整行找到的位置」附近的小範圍（約 0.4 個字高）找，量出行內各段各自的偏移；
       小範圍裡找不到就算這一段不見了（例如換了字寬不同的字型、後半行跑到別處去）。"""
    Hr, Wr = R.shape[:2]
    x0, y0, x1, y1 = res.box_px
    h = max(y1 - y0, 1.0)
    unit = max(font_px, 0.6 * min(h, line.quad.height * W))  # 字高（像素）：取③量的字級，量得太小時以行高補（旋轉的行取行本身的高）
    res.unit_px = unit
    pad = max(1.0, 0.1 * h)
    wx = max(4.0, 1.0 * unit)  # 整行的搜尋範圍
    wy = max(3.0, 0.75 * unit)
    local = (max(2.0, 0.4 * unit), max(2.0, 0.3 * unit))  # 每一段的搜尋範圍
    ty0, ty1 = int(math.floor(y0 - pad)), int(math.ceil(y1 + pad))
    if ty1 <= 0 or ty0 >= Hr or x1 <= 0 or x0 >= Wr:
        res.status, res.skip_reason = "skipped", "在頁面外"
        return
    ty0, ty1 = max(ty0, 0), min(ty1, Hr)
    # 墨跡：以這一行連同搜尋範圍的區域估底色（兩張影像各自估）
    ry0, ry1 = max(0, int(ty0 - wy)), min(Hr, int(math.ceil(ty1 + wy)))
    rx0, rx1 = max(0, int(x0 - wx)), min(Wr, int(math.ceil(x1 + wx)))
    ink_o = _ink(O[ry0:ry1, rx0:rx1])
    ink_r = _ink(R[ry0:ry1, rx0:rx1])
    bg_o = np.median(O[ry0:ry1, rx0:rx1].reshape(-1, 3), axis=0) if ry1 > ry0 and rx1 > rx0 else np.zeros(3, np.float32)
    bg_r = np.median(R[ry0:ry1, rx0:rx1].reshape(-1, 3), axis=0) if ry1 > ry0 and rx1 > rx0 else np.zeros(3, np.float32)
    sigma = max(0.7, 0.08 * unit)
    blur_o = cv2.GaussianBlur(ink_o, (0, 0), sigma)
    blur_r = cv2.GaussianBlur(ink_r, (0, 0), sigma)
    rad = max(1, int(round(0.12 * unit)))  # 算「多出的墨跡」（只供參考）時，原圖的字往外放寬多少
    dil_o = cv2.dilate(ink_o, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (2 * rad + 1, 2 * rad + 1)))
    la, lb = max(int(math.floor(x0)), rx0), min(int(math.ceil(x1)), rx1)
    total_ink = float(ink_o[ty0 - ry0:ty1 - ry0, la - rx0:lb - rx0].sum())
    res.ink = total_ink / (unit * unit)
    # 墨跡量與字高的平方成正比（字的面積）：門檻也跟著字高平方走，縮小頁面或低解析度時短行（頁碼、一兩個字）不會被略過
    nvis = sum(1 for ch in line.text if ch.strip())
    if total_ink < p["line_min_ink"] * unit * unit * min(max(nvis, 1), 2) or lb - la < 2:
        res.status, res.skip_reason = "skipped", "原圖上這一行幾乎沒有墨跡"
        return
    res.place_mass = float(ink_r[ty0 - ry0:ty1 - ry0, la - rx0:lb - rx0].sum()) / max(total_ink, 1e-6)

    def search(a: int, b: int):
        """樣板（原圖 a 到 b 欄）與它在渲染結果的搜尋區：回傳（樣板、原始墨跡、相關係數圖、偏移 x、偏移 y、搜尋區左上角）。"""
        T = blur_o[ty0 - ry0:ty1 - ry0, a - rx0:b - rx0]
        T_raw = ink_o[ty0 - ry0:ty1 - ry0, a - rx0:b - rx0]
        sx0, sx1 = max(0, a - rx0 - int(wx)), min(ink_r.shape[1], b - rx0 + int(wx))
        sy0, sy1 = max(0, ty0 - ry0 - int(wy)), min(ink_r.shape[0], ty1 - ry0 + int(wy))
        S = blur_r[sy0:sy1, sx0:sx1]
        if S.shape[0] < T.shape[0] or S.shape[1] < T.shape[1] or float(T.std()) < 1e-4 or float(S.max()) <= 1e-6:
            return None
        score = np.nan_to_num(cv2.matchTemplate(S, T, cv2.TM_CCOEFF_NORMED), nan=0.0, posinf=0.0, neginf=0.0)
        oy = (np.arange(score.shape[0])[:, None] + sy0 - (ty0 - ry0)).astype(np.float32) * np.ones((1, score.shape[1]), np.float32)
        ox = (np.arange(score.shape[1])[None, :] + sx0 - (a - rx0)).astype(np.float32) * np.ones((score.shape[0], 1), np.float32)
        return T, T_raw, score, ox, oy, sx0, sy0

    def measure(ch: Chunk, a: int, b: int, found_at, T, T_raw, score, ox, oy, sx0, sy0) -> None:
        iy, ix = found_at
        ch.ncc = float(score[iy, ix])
        ch.dx, ch.dy = float(ox[iy, ix]), float(oy[iy, ix])
        py0, px0 = sy0 + iy, sx0 + ix
        patch = ink_r[py0:py0 + T.shape[0], px0:px0 + T.shape[1]]
        ch.mass = float(patch.sum()) / max(ch.ink, 1e-6)
        dil = dil_o[ty0 - ry0:ty1 - ry0, a - rx0:b - rx0]
        ch.extra = float(np.clip(patch - dil, 0.0, None).sum()) / max(ch.ink, 1e-6)
        # 中段（去掉上下各兩成）的墨跡比：別的字疊上來會落在中段；行距很緊時上下行的字母上下緣碰進來，只會落在上下緣
        c0, c1 = max(0, int(round(y0 + 0.2 * h)) - ty0), max(1, int(round(y1 - 0.2 * h)) - ty0)
        core_o = float(T_raw[c0:c1].sum())
        ch.core_mass = float(patch[c0:c1].sum()) / core_o if core_o > 0 else 0.0
        ch.found = ch.ncc >= p["line_ncc_min"] and ch.mass >= p["line_mass_min"]
        ch.crowded = ch.found and ch.core_mass >= p["line_overlap_mass"]
        if ch.found:  # 字的顏色：兩邊各取「確定是字」的像素（墨跡 0.85 以上），記下它們與底色的差
            o_px = O[ty0:ty1, a:b][T_raw >= 0.85]
            r_px = R[ry0 + py0:ry0 + py0 + T.shape[0], rx0 + px0:rx0 + px0 + T.shape[1]][patch >= 0.85]
            if len(o_px) and len(r_px):
                colors_o.append(o_px - bg_o)
                colors_r.append(r_px - bg_r)

    colors_o: list[np.ndarray] = []
    colors_r: list[np.ndarray] = []

    # 1. 整行
    centers = [(0.0, 0.0)]
    whole = search(la, lb)
    if whole is not None:
        at = _best(whole[2], whole[3], whole[4], [(0.0, 0.0)], (wx, wy), 0.1)
        if at is not None and whole[2][at] >= p["line_ncc_min"]:
            centers.append((float(whole[3][at]), float(whole[4][at])))
    # 2. 每一段
    min_w = max(2.5 * unit, 12.0)
    horizontal = line.direction.value == "horizontal" and abs(line.baseline[1].y - line.baseline[0].y) * W <= max(2.0, 0.15 * h)
    res.horizontal = horizontal
    spans = _chunks(line, W, x0, x1, min_w) if horizontal else [(x0, x1)]
    for cx0, cx1 in spans:
        a, b = max(int(math.floor(cx0)), rx0), min(int(math.ceil(cx1)), rx1)
        if b - a < 2:
            continue
        ch = Chunk(x0=cx0, x1=cx1, ink=float(ink_o[ty0 - ry0:ty1 - ry0, a - rx0:b - rx0].sum()))
        res.chunks.append(ch)
        if ch.ink < 0.5 * p["line_min_ink"] * unit * unit:
            ch.ink = 0.0  # 這一段幾乎沒有墨跡（例如只有標點）：不判斷
            continue
        found = search(a, b)
        if found is None:
            continue
        at = _best(found[2], found[3], found[4], centers, local, 0.05)
        if at is not None:
            measure(ch, a, b, at, *found)
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
        res.offset_pt = max(math.hypot(c.dx, c.dy) for c in found)
        res.extra = sum(c.extra * c.ink for c in found) / fw
        res.mass = sum(c.mass * c.ink for c in found) / fw
        res.crowded = sum(c.ink for c in found if c.crowded) / total
    co = np.concatenate(colors_o) if colors_o else np.zeros((0, 3), np.float32)
    cr = np.concatenate(colors_r) if colors_r else np.zeros((0, 3), np.float32)
    if len(co) >= 6 and len(cr) >= 6:
        va, vb = np.median(co, axis=0), np.median(cr, axis=0)
        res.color_orig = tuple(int(np.clip(round(v), 0, 255)) for v in bg_o + va)
        res.color_rend = tuple(int(np.clip(round(v), 0, 255)) for v in bg_r + vb)
        res.color_angle = _color_angle(va, vb)
    # 偏移先以像素記，_summarize 再換成 pt


def _relocate(results: list[LineResult], O: np.ndarray, R: np.ndarray, p: dict) -> None:
    """原位置附近找不到的行：在上下各 line_relocate_rows 個字高、左右各兩個字高的範圍裡，整行當一個樣板再找一次，
    看它是不是被畫到別的位置（例如被擠到下一行、和那一行疊在一起）。要很像（相關係數至少 line_relocate_ncc）才算，
    而且整行至少要四個字高寬（太短的字串在一大片範圍裡容易誤認）。找到了只記下位置，仍然算這一行在原位置找不到。"""
    Hr, Wr = R.shape[:2]
    for r in results:
        if r.status == "skipped" or r.found >= p["line_found_min"] or r.unit_px <= 0:
            continue
        x0, y0, x1, y1 = r.box_px
        unit = r.unit_px
        if x1 - x0 < 4.0 * unit:
            continue
        pad = max(1.0, 0.1 * (y1 - y0))
        ty0, ty1 = max(0, int(math.floor(y0 - pad))), min(Hr, int(math.ceil(y1 + pad)))
        tx0, tx1 = max(0, int(math.floor(x0))), min(Wr, int(math.ceil(x1)))
        reach_y, reach_x = float(p["line_relocate_rows"]) * unit, 2.0 * unit
        sy0, sy1 = max(0, int(ty0 - reach_y)), min(Hr, int(ty1 + reach_y))
        sx0, sx1 = max(0, int(tx0 - reach_x)), min(Wr, int(tx1 + reach_x))
        if ty1 - ty0 < 2 or tx1 - tx0 < 2 or sy1 - sy0 < ty1 - ty0 or sx1 - sx0 < tx1 - tx0:
            continue
        sigma = max(0.7, 0.08 * unit)
        T = cv2.GaussianBlur(_ink(O[ty0:ty1, tx0:tx1]), (0, 0), sigma)
        S_raw = _ink(R[sy0:sy1, sx0:sx1])
        S = cv2.GaussianBlur(S_raw, (0, 0), sigma)
        if float(T.std()) < 1e-4 or float(S.max()) <= 1e-6:
            continue
        score = np.nan_to_num(cv2.matchTemplate(S, T, cv2.TM_CCOEFF_NORMED), nan=0.0, posinf=0.0, neginf=0.0)
        iy, ix = np.unravel_index(int(np.argmax(score)), score.shape)
        if float(score[iy, ix]) < p["line_relocate_ncc"]:
            continue
        mass = float(S[iy:iy + T.shape[0], ix:ix + T.shape[1]].sum()) / max(float(T.sum()), 1e-6)
        if mass < p["line_mass_min"]:
            continue
        r.moved_to = (float(sx0 + ix - tx0), float(sy0 + iy - ty0))


def _overlaps(results: list[LineResult], p: dict) -> None:
    """兩行在成品上找到的位置互相重疊、在原圖上卻沒有：兩行都記成疊在一起。
    被畫到別的位置的行（_relocate 找到的）也算：它在新位置上和哪一行重疊，就記成和那一行疊在一起。
    只看每一行中間那一半高度（上下各去掉四分之一），行距很緊的原稿本來就會碰到的上下緣不算。"""
    boxes = []
    for r in results:
        if r.status == "skipped":
            continue
        x0, y0, x1, y1 = r.box_px
        h = y1 - y0
        if r.moved_to is not None:
            dx, dy = r.moved_to
            core = (x0, y0 + 0.25 * h, x1, y1 - 0.25 * h)
            boxes.append((r, core, (core[0] + dx, core[1] + dy, core[2] + dx, core[3] + dy), None))
        for c in r.chunks:
            if c.ink <= 0 or not c.found:
                continue
            core = (c.x0, y0 + 0.25 * h, c.x1, y1 - 0.25 * h)
            moved = (core[0] + c.dx, core[1] + c.dy, core[2] + c.dx, core[3] + c.dy)
            boxes.append((r, core, moved, c))

    def inter(a, b) -> float:
        w = min(a[2], b[2]) - max(a[0], b[0])
        hh = min(a[3], b[3]) - max(a[1], b[1])
        return max(0.0, w) * max(0.0, hh)

    rejected: set[str] = set()
    accepted: set[str] = set()
    for i in range(len(boxes)):
        ri, ci, mi, ch_i = boxes[i]
        ai = (mi[2] - mi[0]) * (mi[3] - mi[1])
        for j in range(i + 1, len(boxes)):
            rj, cj, mj, ch_j = boxes[j]
            if rj is ri:
                continue
            aj = (mj[2] - mj[0]) * (mj[3] - mj[1])
            if not (inter(mi, mj) > 0.3 * min(ai, aj) and inter(ci, cj) <= 0.05 * min(ai, aj)):
                continue
            if ch_i is None or ch_j is None:
                # 被畫到別處的行落在另一行上：那一行在那個位置要真的多出墨跡才算疊在一起；否則多半是把那一行自己的字誤認成它
                moved, landing = (ri, ch_j) if ch_i is None else (rj, ch_i)
                if landing is None or not landing.crowded:
                    rejected.add(moved.line_id)
                    continue
                accepted.add(moved.line_id)
            ri.overlap_with = ri.overlap_with or rj.line_id
            rj.overlap_with = rj.overlap_with or ri.line_id
    for r in results:
        if r.moved_to is not None and r.line_id in rejected and r.line_id not in accepted:
            r.moved_to = None  # 新位置不可信：當成單純找不到


def _norm_text(text: str) -> str:
    """比對用的字：統一全形半形與相容字（NFKC），去掉空白與看不見的控制字元。"""
    return "".join(ch for ch in unicodedata.normalize("NFKC", text) if ch.strip() and unicodedata.category(ch) not in ("Cc", "Cf"))


def _present(want: str, haystack: str, shingles: set[str], k: int = 6) -> bool:
    """want 有沒有出現在 haystack 裡（都已正規化）：整串找得到，或八成以上的 k 字片段找得到（換行把一行拆開也算）。"""
    if not want:
        return True
    if want in haystack:
        return True
    if len(want) <= k:
        return False
    parts = [want[i:i + k] for i in range(len(want) - k + 1)]
    return sum(1 for t in parts if t in shingles) / len(parts) >= 0.8


def _text_layer(results: list[LineResult], glyphs: list[Glyph], frame: tuple[float, float, float], W: float,
                page: Optional[StyledPage], p: dict) -> list[tuple[str, Optional[str]]]:
    """用輸出檔讀回來的文字層，看每一行「位置上寫的是什麼字」。影像比對只看墨跡的形狀，長度相近的詞形狀幾乎一樣
    （換行後別的詞剛好落在原位置時，影像會說「找到了」）；文字層說得出那裡到底是哪些字。
    每個讀回的字分給「它落在哪一行的範圍裡」：影像找到這一行的，用找到的位置；找不到的用原位置。範圍是行高中間七成、
    左右各多三成字高；同時落在幾行裡時給最近的那一行。記下：
      near_text：分到這一行的字（依 x 排）；text_cover：這一行的字有多少比例出現在那裡（依序比對）；
      text_extra：那裡多出、不屬於這一行的字 ÷ 這一行的字數；text_overlap_with：多出來的字屬於哪一行；
      text_elsewhere：這一行的字有沒有出現在這一頁讀回來的文字裡（任何位置）。
    旋轉、直排的行不比（字的位置量不準），只靠影像判斷。
    回傳「多出來的字」：讀回來、但不落在任何一行（不論原位置或找到的位置）範圍裡的字，依讀回來的行分組，
    每組（字、所在的元素編號）。"""
    if not glyphs:
        for r in results:
            if r.status != "skipped" and r.horizontal:
                r.near_text, r.text_cover, r.text_extra, r.text_elsewhere = "", 0.0, 0.0, False
        return []
    k, ox, oy = frame
    f = W / max(k, 1e-9)
    gch = [g.ch for g in glyphs]
    size = np.array([g.size * f for g in glyphs], np.float32)
    wide = np.array([ord(g.ch) >= 0x2E80 for g in glyphs])
    gx = np.array([(g.x - ox) * f for g in glyphs], np.float32) + np.where(wide, 0.5, 0.28) * size
    gy = np.array([(g.y - oy) * f for g in glyphs], np.float32) - 0.35 * size
    all_text = _norm_text("".join(gch))
    shingles = {all_text[i:i + 6] for i in range(max(0, len(all_text) - 5))}
    best = np.full(len(glyphs), np.inf, np.float32)
    owner = np.full(len(glyphs), -1, np.int32)
    accounted = np.zeros(len(glyphs), bool)

    def band(r: LineResult, dx: float, dy: float) -> tuple[float, float, float, float]:
        x0, y0, x1, y1 = r.box_px
        u = max(r.unit_px, 0.6 * (y1 - y0), 1.0)
        h = y1 - y0
        return x0 - 0.3 * u + dx, y0 + 0.15 * h + dy, x1 + 0.3 * u + dx, y1 - 0.15 * h + dy

    for n, r in enumerate(results):
        if not r.text.strip():
            continue
        x0, y0, x1, y1 = r.box_px
        u = max(r.unit_px, 0.6 * (y1 - y0), 1.0)
        accounted |= (gx >= x0 - 0.5 * u) & (gx <= x1 + 0.5 * u) & (gy >= y0) & (gy <= y1)  # 原位置的整個範圍：不算多出來的字
        shifted = r.status != "skipped" and r.found >= p["line_found_min"]
        dx, dy = (r.dx_pt, r.dy_pt) if shifted else (0.0, 0.0)  # 這時還是像素
        if r.moved_to is not None:
            accounted |= _inside(gx, gy, band(r, *r.moved_to))
        bx0, by0, bx1, by1 = band(r, dx, dy)
        inside = _inside(gx, gy, (bx0, by0, bx1, by1))
        accounted |= inside
        cy = (by0 + by1) / 2
        xout = np.maximum(0.0, np.maximum(x0 + dx - gx, gx - (x1 + dx)))
        d = np.abs(gy - cy) / max(by1 - by0, 1.0) + 2.0 * xout / u
        take = inside & (d < best)
        best[take] = d[take]
        owner[take] = n
    by_line_text = {r.line_id: _norm_text(r.text) for r in results}
    # 很短的行（頁碼、一兩個字）用「在別處找得到」判斷不可靠（「1」幾乎一定出現在別行的「12%」裡）：
    # 只認讀回來的某一行整行就是它，或它出現在沒有分給任何一行的字裡
    read_lines: dict[int, list[int]] = {}
    for i, g in enumerate(glyphs):
        read_lines.setdefault(g.line, []).append(i)
    whole_lines = {_norm_text("".join(gch[i] for i in sorted(idx, key=lambda i: gx[i]))) for idx in read_lines.values()}
    free_text = [_norm_text("".join(gch[i] for i in sorted(idx, key=lambda i: gx[i]) if owner[i] < 0)) for idx in read_lines.values()]
    for n, r in enumerate(results):
        if r.status == "skipped" or not r.horizontal:
            continue
        idx = np.nonzero(owner == n)[0]
        # 依讀回來的行、再依 x 排：兩行疊在同一個位置時，各自的字仍然連在一起（不會交錯成亂碼）
        near = _norm_text("".join(gch[i] for i in sorted(idx, key=lambda i: (glyphs[i].line, gx[i]))))
        want = by_line_text[r.line_id]
        sm = difflib.SequenceMatcher(None, want, near, autojunk=False)
        blocks = sm.get_matching_blocks()
        matched = sum(b.size for b in blocks)
        r.near_text = near
        r.text_cover = matched / max(len(want), 1)
        r.text_extra = (len(near) - matched) / max(len(want), 1)
        if len(want) <= 6:
            r.text_elsewhere = want in whole_lines or any(want in t for t in free_text)
        else:
            r.text_elsewhere = _present(want, all_text, shingles)
        if r.text_extra >= 0.5:  # 多出來的字是哪一行的
            used = set()
            for b in blocks:
                used.update(range(b.b, b.b + b.size))
            extra = "".join(ch for i, ch in enumerate(near) if i not in used)
            best_q, best_v = None, 0.0
            for q in results:
                if q is r or not by_line_text[q.line_id]:
                    continue
                m = difflib.SequenceMatcher(None, extra, by_line_text[q.line_id], autojunk=False)
                v = sum(b.size for b in m.get_matching_blocks()) / max(len(extra), 1)
                if v > best_v:
                    best_q, best_v = q, v
            if best_q is not None and best_v >= 0.6:
                r.text_overlap_with = best_q.line_id
    # 多出來的字：依讀回來的行分組
    groups: dict[int, list[int]] = {}
    for i in np.nonzero(~accounted)[0]:
        groups.setdefault(glyphs[i].line, []).append(int(i))
    extras: list[tuple[str, Optional[str]]] = []
    for idx in groups.values():
        text = _norm_text("".join(gch[i] for i in sorted(idx, key=lambda i: gx[i])))
        if len(text) < 2:
            continue
        # 多出來的字屬於哪個元素：先看它是哪一行的字（換行後被擠出框外的字，紅框要畫在原本的框上），
        # 對不到任何一行時，才用它落在哪個元素裡
        eid, best_v = None, 0.0
        for q in results:
            want = by_line_text[q.line_id]
            if not want or q.element_id is None:
                continue
            v = sum(b.size for b in difflib.SequenceMatcher(None, text, want, autojunk=False).get_matching_blocks()) / len(text)
            if v > best_v:
                eid, best_v = q.element_id, v
        if best_v < 0.6:
            cx, cy = float(np.mean(gx[idx])), float(np.mean(gy[idx]))
            eid = _containing_element(page, cx / f + ox, cy / f + oy)
        extras.append((text, eid))
    return extras


def _inside(gx: np.ndarray, gy: np.ndarray, b: tuple[float, float, float, float]) -> np.ndarray:
    return (gx >= b[0]) & (gx <= b[2]) & (gy >= b[1]) & (gy <= b[3])


def _containing_element(page: Optional[StyledPage], x: float, y: float) -> Optional[str]:
    """輸出頁面上一點（pt）落在哪個文字框、表格或圖片裡（最上層的）；都沒有就是 None。"""
    if page is None:
        return None
    best = None
    for item in list(page.text_boxes) + list(page.tables) + list(page.images):
        b = item.box
        if b.x <= x <= b.x + b.w and b.y <= y <= b.y + b.h and (best is None or item.z > best.z):
            best = item
    return best.id if best is not None else None


def _conserve(results: list[LineResult], O: np.ndarray, R: np.ndarray, p: dict) -> None:
    """沒有文字層時（例如文字轉成外框的 SVG），用「墨跡守恆」區分「找不到」與「字還在、但位置或樣子對不上」：
    同一個元素（文字框或表格）裡，原位置找不到的行若過半，而且整個元素範圍內渲染出來的墨跡至少是原圖的
    line_conserve_min 倍（不超過 2.5 倍，避免把蓋上來的照片當成字），就把這些行改記成 misplaced。
    單獨一行找不到時看它自己的原位置：墨跡量正常（line_conserve_min 到 2 倍之間）也改記成 misplaced。"""
    by_el: dict[Optional[str], list[LineResult]] = {}
    for r in results:
        if r.status != "skipped":
            by_el.setdefault(r.element_id, []).append(r)
    lo = float(p["line_conserve_min"])
    for eid, rs in by_el.items():
        lost = [r for r in rs if r.found < p["line_found_min"] and r.moved_to is None]
        if not lost:
            continue
        if len(lost) * 2 >= len(rs) and len(rs) >= 2:
            x0 = int(max(0, min(r.box_px[0] for r in rs)))
            y0 = int(max(0, min(r.box_px[1] for r in rs)))
            x1 = int(min(R.shape[1], max(r.box_px[2] for r in rs)))
            y1 = int(min(R.shape[0], max(r.box_px[3] for r in rs)))
            if x1 - x0 < 2 or y1 - y0 < 2:
                continue
            io, ir = float(_ink(O[y0:y1, x0:x1]).sum()), float(_ink(R[y0:y1, x0:x1]).sum())
            if io > 0 and lo <= ir / io <= 2.5:
                for r in lost:
                    r.conserved = True
        else:
            for r in lost:
                if lo <= r.place_mass <= 2.0:
                    r.conserved = True


def _clip(text: str, n: int = 24) -> str:
    text = text.strip()
    return text if len(text) <= n else text[:n] + "…"


def _offset_severity(dx: float, dy: float, tol_x: float, tol_y: float, font_pt: float) -> float:
    """偏移（dx、dy，pt）的嚴重程度：水平、垂直都在容許差內是 0；超過時是 0.15，超出的部分再多一個字高就到上限 0.35。
    偏移的行字還在、讀得懂，只是位置不對，所以比「找不到」（1）與「疊在一起」（0.6）輕得多。"""
    over = math.hypot(max(0.0, abs(dx) - tol_x), max(0.0, abs(dy) - tol_y))
    if over <= 0.0:
        return 0.0
    return min(_OFFSET_SEVERITY[1], _OFFSET_SEVERITY[0] + (_OFFSET_SEVERITY[1] - _OFFSET_SEVERITY[0]) * over / max(font_pt, 1e-6))


def _summarize(results: list[LineResult], scale_pt: float, p: dict, where: str, relayout: bool = False,
               extras: Optional[list[tuple[str, Optional[str]]]] = None, has_text: bool = False) -> PageLines:
    by_id = {r.line_id: r for r in results}
    checked = [r for r in results if r.status != "skipped"]
    n = len(checked)
    issues: list[Issue] = []
    counts = {k: 0 for k in STATUSES}
    prefix = f"{where}：" if where else ""
    total_sev = 0.0  # 各行嚴重程度的總和
    content_sev = 0.0  # 其中內容問題（找不到、一部分找不到、疊在一起、換行不同、字還在但位置不對）的總和
    found_min, partial_min = p["line_found_min"], p["line_partial_min"]
    # 文字層：某一行的位置上讀到另一行的字（text_overlap_with）→ 那另一行是被畫到這一行上面
    landed_on: dict[str, LineResult] = {}
    for r in checked:
        if r.text_overlap_with and r.text_overlap_with not in landed_on:
            landed_on[r.text_overlap_with] = r
    for r in checked:
        r.offset_pt *= scale_pt
        r.dx_pt *= scale_pt
        r.dy_pt *= scale_pt
        tol = max(float(p["line_offset_tolerance_pt"]), float(p["line_offset_tolerance_ratio"]) * r.font_pt)
        tol_x = max(tol, float(p["line_offset_relayout_ratio"]) * r.font_pt) if relayout else tol
        quote = f"「{_clip(r.text)}」"
        cands: list[tuple[float, str, Severity, str, str]] = []  # （嚴重程度、狀態、等級、代碼、說明）
        other = by_id.get(r.overlap_with) if r.overlap_with else None
        text_other = by_id.get(r.text_overlap_with) if r.text_overlap_with else None
        img_ok = r.found >= found_min
        cover = r.text_cover if (has_text and r.horizontal and r.text_cover is not None) else None
        near = f"「{_clip(r.near_text or '', 16)}」"
        moved = ""
        if r.moved_to is not None:
            moved = f"，被畫到約 {math.hypot(*r.moved_to) * scale_pt:.0f} pt 外的地方" + (f"，和「{_clip(other.text)}」疊在一起" if other is not None else "")
        elif r.line_id in landed_on and landed_on[r.line_id] is not r:
            moved = f"，被畫到「{_clip(landed_on[r.line_id].text)}」那一行的位置、和它疊在一起"
        if cover is not None and cover >= 0.8:
            # 文字層：這一行的字在它的位置上
            if r.text_extra >= 0.5 and text_other is not None:
                cands.append((_OVERLAP_SEVERITY, "overlap", Severity.ERROR, "line_overlap",
                              f"{prefix}這一行和「{_clip(text_other.text)}」疊在一起（同一個位置上讀到兩行的字）：{quote}。"))
            if not img_ok:
                if r.place_mass < 0.25 and not moved:
                    cands.append((1.0, "missing", Severity.ERROR, "line_missing",
                                  f"{prefix}這一行的字在檔案裡、位置也對，但畫出來看不見（可能被別的東西蓋住，或字的顏色和底色太接近）：{quote}。"))
                else:
                    cands.append((_MISPLACED_SEVERITY, "misplaced", Severity.ERROR, "line_misplaced",
                                  f"{prefix}這一行的字在原位置，但畫出來的樣子和原圖對不上（字級、字型、字距或行距不同，或和別的行疊在一起）{moved}：{quote}。"))
            elif r.found < partial_min:
                # 字都在原位置（文字層證明），只是有一部分畫出來的樣子對不上：比「找不到」輕
                cands.append((0.5 * (1.0 - r.found), "partial", Severity.WARNING, "line_partial",
                              f"{prefix}這一行的字都在原位置，但有一部分畫出來的樣子和原圖不同（字型、字級或字距）：{quote}（影像對得上 {math.floor(r.found * 100)}%）。"))
        elif cover is not None and (cover >= 0.5 or r.near_text):
            # 文字層：原位置上有字，但不全是（或不是）這一行的字
            if r.text_elsewhere:
                sev = _REWRAP_SEVERITY * min(1.0, 2.0 * (1.0 - cover))
                cands.append((sev, "rewrapped", Severity.WARNING if cover >= 0.5 else Severity.ERROR, "line_rewrapped",
                              f"{prefix}這一行的換行或位置與原圖不同：原位置上讀到的是{near}（這一行的字有 {math.floor(cover * 100)}% 在那裡，其餘排到別的地方）：{quote}。"))
            elif cover >= 0.5:
                cands.append((1.0 - cover, "partial", Severity.WARNING, "line_partial",
                              f"{prefix}這一行有一部分的字不在成品裡：原位置上讀到的是{near}：{quote}。"))
            else:
                cands.append((1.0, "missing", Severity.ERROR, "line_missing",
                              f"{prefix}這一行的字不在成品裡：原位置上讀到的是別的字{near}：{quote}。"))
        elif cover is not None and not (img_ok and r.found >= partial_min):
            # 文字層：原位置上沒有字（影像也沒有找到整行）
            if r.text_elsewhere:
                cands.append((_MISPLACED_SEVERITY, "misplaced", Severity.ERROR, "line_misplaced",
                              f"{prefix}這一行的字在檔案裡，但沒有畫在原位置{moved or '（被排到別的地方）'}：{quote}。"))
            elif not img_ok:
                cands.append((1.0, "missing", Severity.ERROR, "line_missing", f"{prefix}這一行在成品裡找不到（檔案裡也讀不到這些字）：{quote}。"))
            else:
                cands.append((1.0 - r.found, "partial", Severity.WARNING, "line_partial", f"{prefix}這一行有一部分在成品裡找不到：{quote}（只找到 {math.floor(r.found * 100)}%）。"))
        else:
            # 沒有文字層（或文字層看不到這一行、影像卻整行找到了：例如文字轉成外框或留在圖片裡）：只靠影像
            if not img_ok:
                if getattr(r, "conserved", False):
                    cands.append((_MISPLACED_SEVERITY, "misplaced", Severity.ERROR, "line_misplaced",
                                  f"{prefix}這一行在原位置對不上，但那裡的墨跡量正常：字多半還在，只是位置偏太多、字級或字型不同、換行不同，"
                                  f"或和別的行疊在一起{moved}：{quote}。"))
                elif moved:
                    cands.append((1.0, "missing", Severity.ERROR, "line_missing", f"{prefix}這一行沒有畫在原位置{moved}：{quote}。"))
                else:
                    cands.append((1.0, "missing", Severity.ERROR, "line_missing",
                                  f"{prefix}這一行在成品裡找不到：{quote}（原位置附近只找到 {math.floor(r.found * 100)}%）。"))
            elif r.found < partial_min:
                cands.append((1.0 - r.found, "partial", Severity.WARNING, "line_partial", f"{prefix}這一行有一部分在成品裡找不到：{quote}（只找到 {math.floor(r.found * 100)}%）。"))
        if img_ok:
            if other is not None:
                cands.append((_OVERLAP_SEVERITY, "overlap", Severity.ERROR, "line_overlap", f"{prefix}這一行和「{_clip(other.text)}」疊在一起：{quote}。"))
            elif ((r.crowded >= p["line_crowded_share"] and len(_norm_text(r.text)) >= 4)
                  or any(c.crowded and c.core_mass >= p["line_overlap_mass_strong"] for c in r.chunks)):
                # 只有一到三個字的行（頁碼之類）：換了字型、字重，中段墨跡就差很多，要到 line_overlap_mass_strong 才算疊字
                worst_mass = max(c.core_mass for c in r.chunks if c.crowded)
                cands.append((_OVERLAP_SEVERITY, "overlap", Severity.ERROR, "line_overlap",
                              f"{prefix}這一行上面多畫了原圖沒有的字（有一段字的中段墨跡是原圖的 {worst_mass:.1f} 倍），多半是別的行疊了上來：{quote}。"))
            # 每一段的偏移嚴重程度 × 這一段佔全行墨跡的比例：整行都偏了算滿，只有後半行偏了算一半
            judged = sum(c.ink for c in r.chunks if c.ink > 0)
            sev = sum(c.ink / judged * _offset_severity(c.dx * scale_pt, c.dy * scale_pt, tol_x, tol, r.font_pt)
                      for c in r.chunks if c.ink > 0 and c.found) if judged else 0.0
            if sev > 0:
                allowed = f"容許 {tol:.1f} pt" if tol_x == tol else f"容許水平 {tol_x:.1f} pt、垂直 {tol:.1f} pt；替身引擎的字距本來就和目標軟體不同"
                cands.append((sev, "offset", Severity.WARNING, "line_offset", f"{prefix}這一行的位置與原圖相差 {r.offset_pt:.1f} pt（{allowed}）：{quote}。"))
            if r.color_angle is not None and r.color_angle > p["line_color_angle"]:
                co, cr = r.color_orig, r.color_rend
                cands.append((_COLOR_SEVERITY, "color", Severity.WARNING, "line_color",
                              f"{prefix}這一行字的顏色與原圖不同：原圖約 #{co[0]:02X}{co[1]:02X}{co[2]:02X}，成品約 #{cr[0]:02X}{cr[1]:02X}{cr[2]:02X}：{quote}。"))
        if not cands:
            continue
        worst = max(cands, key=lambda c: c[0])
        r.status, r.severity = worst[1], worst[0]
        total_sev += worst[0]
        if worst[1] not in ("offset", "color"):
            content_sev += worst[0]
        seen: set[str] = set()
        for sev, status, level, code, msg in cands:  # 同一行同一種問題只開一條（先判斷出來的說明較具體）
            if status in seen:
                continue
            seen.add(status)
            counts[status] += 1  # 行數：同一行有好幾種問題時，每一種各算一行
            issues.append(Issue(severity=level, code=code, element_id=r.element_id, message=msg))
    extra_lines = 0
    for text, eid in extras or []:
        extra_lines += 1
        total_sev += _EXTRA_SEVERITY
        issues.append(Issue(severity=Severity.WARNING, code="line_extra", element_id=eid,
                            message=f"{prefix}成品上多出原圖這個位置沒有的字「{_clip(text, 16)}」（多半是換行與原圖不同、字被擠到這裡，或多畫了一次）。"))
    score = line_score(total_sev, content_sev, n, p)
    no_font = [r for r in results if r.skip_reason.startswith("驗證用的電腦沒有字型")]
    return PageLines(lines=results, score=score, checked=n, missing=counts["missing"], partial=counts["partial"],
                     overlap=counts["overlap"], offset=counts["offset"], issues=issues, no_font=len(no_font),
                     no_font_example=next((r.skip_reason for r in no_font if "「" in r.skip_reason), no_font[0].skip_reason) if no_font else "",
                     rewrapped=counts["rewrapped"],
                     misplaced=counts["misplaced"], color=counts["color"], extra=extra_lines, text_layer=has_text)


def line_score(total_sev: float, content_sev: float, n: int, p: Optional[dict] = None) -> Optional[float]:
    """逐行分數 = 1 − 各行嚴重程度的平均 − min(上限, 每個問題的最低扣分 × 內容問題的嚴重程度總和)。見模組開頭。"""
    if n <= 0:
        return None
    p = dict(DEFAULTS) if p is None else {**DEFAULTS, **p}
    deduct = total_sev / n + min(float(p["line_problem_cap"]), float(p["line_problem_floor"]) * content_sev)
    return max(0.0, 1.0 - deduct)
