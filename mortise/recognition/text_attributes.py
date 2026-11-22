"""文字屬性量測（3B.3 筆畫遮罩、3B.4 顏色、3B.5 字級與字型聯合求解、3B.6 其他屬性）。

公開介面::

    measure_line(image, line, fonts, params=None) -> LineAttributes

只做量測與感知，不呼叫任何大型語言模型；只交「看得到的事實」：原始量測值、誤差、候選清單與信心值，
不吸附成整齊的數字、不替候選定案（那是⑤的工作）。

座標系
------
- 工作影像：連續像素座標，像素 i 涵蓋 [i, i+1)，原點左上、y 向下。
- 攤平的行（mask 座標）：x 沿基線、y 垂直基線向下；可能放大 f 倍（小字才放大）。
  ``mask_to_image``（3×3）把 mask 的連續座標映到工作影像的連續座標。內部量測都在 mask 座標，
  交出前除以 f 換回工作影像像素。

做法（每一步的演算法）
----------------------
(1) 筆畫遮罩
    a. 行的四角向外擴 margin（行高的 30 %），以仿射變換裁出並拉正（cv2.warpAffine，立方內插）；
       行高不到 upsample_below_px 時放大 2 到 4 倍再算（只在計算當下）。
    b. 兩群分色：k-means（K=2）把像素分成兩群，像素投影到兩群中心的連線上得到 0 到 1 的覆蓋率。
       極性自動判斷：行框內佔比明顯高於外擴區的那一群是筆畫（筆畫集中在行框內、較少碰框邊），
       平手時取較少的那一群。
    c. 局部二值化（底色不均時才啟用）：底色以灰階開運算（結構元素大於筆畫粗細）估計，
       筆色以正規化卷積估計，覆蓋率 = (投影值 − 局部底色) ÷ (局部筆色 − 局部底色)。這是 Sauvola
       類方法在「已知兩色連線」上的版本。兩群分不開時升到第 3 級：K=3 分群，挑最像字的一群。
    d. 清理：連通元件分析，刪除面積小於字級平方 0.5 % 的雜點、整塊落在行框外的鄰行墨跡。
    e. 底線與刪除線：水平開運算（長度 1.5 個字級）挑出長橫線候選；先從遮罩拿掉再量測，
       量出基線後依位置分類（基線下方 0.03 到 0.3 個字級為底線；基線上方 0.1 到 0.6 個字級且連續跨過
       字間空隙為刪除線）；其他位置的長橫線若沒有超出文字範圍就放回遮罩（可能是字形本身的橫筆）。
(2) 顏色：從工作影像原始像素（最近鄰，不經內插）取樣。遮罩內縮（筆畫粗細的 25 %，至少半個影像像素）
    後取各色版中位數；核心像素不足或筆畫細於 thin_stroke_px 時，取覆蓋率投影最高的 5 % 像素的中位數
    （極值法），再以「線寬 w、模糊 σ 的峰值覆蓋率 erf(w ÷ 2√2σ)」從鄰近底色外插回筆色，信心值降低。
    鄰近背景色取遮罩外擴 2 到 6 px 的環狀區域中位數。
(3) 字級與字型聯合求解（檢索加重繪比對）
    a. 單位切分：以空白與文字類別（全形字／其他）把行切成「單位」（拉丁的詞、中文的連續全形字）。
       以參考字型的排字結果線性映到量到的墨跡範圍，在預期位置附近找最寬（空白）或最近（換文字類別）
       的空隙，定出每個單位的墨跡左右緣（覆蓋率 0.5 的次像素交點）。
    b. 每個字的上下緣：以排字結果把每個字映到一段欄，取中央 70 % 的欄，量覆蓋率 0.5 的上緣與下緣。
    c. 高度證據（每個候選字型各算一次）：對所有字的上緣與下緣做「字型專屬的垂直模型」穩健最小平方
       （Tukey 雙權重 IRLS）：y = b + t·(x − x̄) − s·e，e 是該字型中同一個字的墨跡上緣或下緣（em）。
       解出基線 b、斜率 t、字級 s。這把「由 x 字高推字級」推廣到所有字（x 字高、大寫字高、上伸、
       下伸同時用上），而且因為比的是同一批字在字型中的墨跡，圓字母的過衝不會造成偏差；
       g、p、y 的下伸部也被模型化，不會把基線拉偏。殘差的大小就是「垂直比例吻合度」。
    d. 寬度證據：每個單位的墨跡寬 ÷ 該字型同一串字（HarfBuzz，含字距調整）在 em 為 1 時的墨跡寬。
       整段（含空白）再算一次；整段比逐詞大超過 justify_tol 時判為「左右對齊嫌疑」，只用詞內寬度。
    e. 中文捷徑（與字型無關）：連續全形字逐格取墨跡中心（覆蓋率 0.5 交點的中點），以穩健直線擬合
       c_i = c0 + i·p 求間距 p（標點不參與）；字級 = p ÷ 該字型的平均前進寬度（全形字為 1 em）。
       與高度證據比對：不一致超過 tracking_tol 就是「調字距嫌疑」，差值記成字距原始量測。
       間距變異超過 pitch_cv_max 判為「比例寬度嫌疑」，不用捷徑。
    f. 量度分數 = 垂直吻合 × 寬高吻合 × 傾斜吻合 × 粗細吻合（筆畫粗細比與字型探針的同一估計法相比）；
       斜體字型的傾斜以「同一個量法量它自己的重繪」為準（字型檔的 italicAngle 與墨跡傾斜常不同）。
    g. 模糊校準與自我校正：以量度第一名重繪，搜尋讓重疊率最高的額外高斯模糊；再把它以同樣的模糊畫出、
       用同一套量法量回來，量到的字級 ÷ 畫的字級就是模糊（與放大取樣）造成的偏差，所有候選的高度與寬度
       證據都除以這個偏差（誤差另加偏差量的 bias_unc 倍）。
    h. 重繪比對：取前 topk_redraw 名（並補上前幾名字族的粗體、斜體樣式），以各自解出的字級用 FreeType
       逐單位重繪（調字距時全形字逐字放在間距格點上）、做同等模糊、在小範圍的垂直位移與縮放中取
       「軟重疊率」（Σmin ÷ Σmax）的最大值。綜合分數 = metric_weight × 量度分數 + redraw_weight × 重繪分數。
(4) 其他屬性
    - 斜體：把覆蓋率遮罩以各角度反向推斜，取欄投影「集中度」（Σp² ÷ (Σp)²）最高的角度，拋物線內插；
      誤差取集中度曲線的半寬。
    - 粗體：筆畫粗細（2 × 覆蓋率總和 ÷ 總變差，對模糊不敏感）與最佳字族 Regular、Bold 以同字級重繪（同等模糊）
      的粗細比較，再加上兩者重繪重疊率的差。沒有 Bold 字型時以 bold_stem_ratio 推估。
    - 行內分段：先依文字類別（全形／拉丁）切，再對單位序列的「核心顏色、高度字級」做二元切分的
      改變點偵測（每切一刀要有 run_split_penalty 的證據），每段各自求解一次。
    - 特效：緊鄰筆畫的一圈顏色若不在「筆色到底色」的連線上、又與遠處底色不同，判為外框、陰影或光暈，
      在 notes 標示「量測不可靠」並降低信心值。
(5) 精修四角：左右取墨跡範圍；上下由基線推算最佳字型在該字級下的上伸與下伸（hhea）。

誤差模型
--------
σ_edge（邊緣定位誤差，mask 像素）= √((edge_err_px·f)² + (覆蓋率雜訊 ÷ 邊緣斜率)²) + 0.1·模糊 σ。
- 中文間距法：σ_p = max(σ_c, σ_edge)·√(12 ÷ (N(N² − 1)))，N 為參與擬合的字數，σ_c 為擬合殘差；
  再加系統誤差 pitch_sys_rel·p。約為「邊緣定位誤差 ÷ 字數」。
- 行寬法：σ_W = √2·σ_edge；σ_s/s = √((σ_W/W)² + width_model_rel²)。字型不對時行寬法有系統誤差，
  這不在 σ 裡，而是反映在寬高吻合分數上；最後再把前幾名候選字級的離散度併入總誤差。
- 高度法：加權最小平方的共變異數，每個觀測的 σ = √(σ_edge² + (height_model_rel·s)²)，
  殘差比預期大時按比例放大。
- 綜合：各證據以誤差平方倒數加權平均；證據彼此不一致（χ² 大於自由度）時按 √(χ²/dof) 放大。
- 字數不足、無吻合字型、特效、遮罩不可靠等情況，誤差另乘放大係數並降低信心值。

已知限制
--------
- 直排文字（line.vertical）未支援，當成橫排量並標示不可靠。
- 沿曲線排列、藝術字、漸層填色只偵測不到或只標示，不還原參數。
- 水平縮放與調字距的區分只在能逐字切出墨跡（連通元件數等於字數，或全形字格）時才做。
- 粗體只以候選字族內 Regular 與 Bold 的差距判斷；軟體硬加的假粗體與真粗體分不出來。
- 截圖對齊整數像素的小字（hinting）未加試「對齊像素」畫法。

可單獨升級的點
--------------
- 第 4 級筆畫分割模型（影像進、覆蓋率遮罩出）：換掉 ``_stroke_mask``。
- 字型類別分類器：字型庫變大時先縮小候選範圍；插在 ``_candidates``。
- 重繪比對可換成學習到的字形相似度（目前是軟重疊率）：``_redraw_score``。
- 第二輪精修：用選定字型的字形外框修正每個中文字的墨跡中心偏移：``_cjk_pitch``。
"""

from __future__ import annotations

import math
import unicodedata
from dataclasses import dataclass, field
from typing import Optional

import cv2
import numpy as np

from .fontlib import FontFace, FontLibrary, is_cjk_char, stroke_width_soft
from .types import Estimate, FontMatch, LineAttributes, OcrLine, RunAttributes

DEFAULT_PARAMS: dict = {
    # ---------------- 取樣與遮罩（3B.3）
    "margin_frac": 0.30,  # 行框向外擴的比例（行高的倍數）
    "margin_min_px": 3.0,  # 外擴的最小值（工作影像像素）
    "upsample_below_px": 30.0,  # 行高低於此值（像素）時放大再算
    "upsample_target_px": 48.0,  # 放大後的目標行高（像素）
    "upsample_max": 4,  # 最大放大倍率
    "max_line_px": 320.0,  # 行高超過此值（像素）時縮小再算，省時間
    "quad_em_ratio": 1.15,  # 行框高度約等於幾個字級（用來在求解前粗估字級）
    "kmeans_max_samples": 30000,  # k-means 取樣像素數上限
    "min_contrast": 40.0,  # 筆畫與底的最低色差（RGB 歐氏距離，0 到 441），低於此值標「遮罩不可靠」
    "nonuniform_bg": 0.12,  # 局部底色估計的變動範圍（覆蓋率單位）超過此值才啟用局部二值化
    "bg_kernel_frac": 0.6,  # 局部底色開運算的結構元素大小（行高的倍數）
    "level3_spread": 0.15,  # 離「底色—筆色」連線超過 1/4 色差的像素比例高於此值時，升到第 3 級多色分群
    "speck_area_frac": 0.005,  # 小於「字級平方 × 此比例」的連通元件視為雜點刪除
    "rule_min_len_em": 1.5,  # 長於此值（字級的倍數）的水平線列為底線或刪除線候選
    "rule_max_thick_em": 0.14,  # 底線或刪除線的最大粗細（字級的倍數）
    "underline_range_em": (0.03, 0.30),  # 底線中心在基線下方的範圍（字級的倍數）
    "strike_range_em": (0.10, 0.60),  # 刪除線中心在基線上方的範圍（字級的倍數）
    # ---------------- 誤差模型
    "edge_err_px": 0.25,  # 乾淨影像的邊緣定位誤差（工作影像像素，一個標準差）
    "height_quant_px": 0.3,  # 高度特徵的量化誤差（工作影像像素）：hinting 把 x 字高、大寫字高對齊整數像素，同一行所有字一起偏，不會因平均而變小
    "blur_search": (0.0, 0.5, 0.8, 1.2, 1.7, 2.4, 3.4, 4.8),  # 模糊校準的候選高斯 σ（mask 像素）
    "bias_unc": 0.3,  # 模糊偏差校正本身的不確定度（偏差量的比例）
    "bias_max": 0.15,  # 偏差比超出 1 ± 此值時不校正（多半是重繪失敗）
    "height_model_rel": 0.02,  # 高度模型誤差（字級的比例）：hinting 把 x 字高對齊整數像素、渲染與字型版本差異
    "width_model_rel": 0.008,  # 寬度模型誤差（比例）：hinting、渲染器差異
    "pitch_sys_rel": 0.002,  # 中文間距法的系統誤差（比例）：攤平殘餘比例誤差等
    "generic_rel_err": 0.10,  # 無吻合字型時字級的相對誤差
    # ---------------- 字型求解（3B.5）
    "reference_families": ["Liberation Sans", "DejaVu Sans", "WenQuanYi Zen Hei", "Noto Sans CJK TC"],  # 映射用參考字型（依序找第一個涵蓋的）
    "rescore_top": 30,  # 第二輪（以第一名字型重新映射欄範圍）重新評分的候選數
    "topk_redraw": 12,  # 進入重繪比對的候選數
    "style_families": 3,  # 補上前幾名字族的其他樣式（粗體、斜體）一起重繪
    "max_candidates": 10,  # 交出的候選字型數上限
    "metric_weight": 0.35,  # 綜合分數中量度分數的權重
    "redraw_weight": 0.65,  # 綜合分數中重繪分數的權重（干擾下量度排名較不可靠，重繪較能分辨）
    "redraw_iou_ref": 0.95,  # 重繪重疊率達此值算滿分（乾淨、未 hinting 的正確字型約 0.9 到 0.96）
    "redraw_iou_floor": 0.40,  # 重繪重疊率低於此值算零分
    "min_match_score": 0.35,  # 全部候選低於此綜合分數就標「無吻合字型」
    "min_match_iou": 0.55,  # 最佳候選的重繪重疊率低於此值也標「無吻合字型」
    "slant_sigma_deg": 3.0,  # 傾斜吻合的容差（度）
    "synthetic_slant_penalty": 0.85,  # 直立字型以假斜體比對時的分數折扣
    "weight_sigma": 0.30,  # 粗細吻合的容差（筆畫粗細比的對數）
    "weight_floor": 0.5,  # 粗細吻合分數的下限
    "justify_tol": 0.03,  # 整段寬推得的字級比逐詞大超過此比例，標「左右對齊嫌疑」
    "tracking_tol": 0.04,  # 寬度（或間距）字級與高度字級相差超過此比例，標「調字距嫌疑」
    "xscale_tol": 0.5,  # 逐字墨跡寬的倍率與寬高比倍率差距小於「偏差的此比例」時判為水平縮放
    # ---------------- 中文捷徑
    "min_cjk_chars": 3,  # 間距捷徑至少要幾個全形字（2 個字也會算，但誤差很大）
    "pitch_cv_max": 0.06,  # 間距變異（穩健標準差 ÷ 間距）超過此值標「比例寬度嫌疑」
    "cjk_punct": "，。、；：？！「」『』（）《》〈〉【】…—～·．,.;:!?()[]",  # 不參與間距擬合的標點
    # ---------------- 字數
    "short_line_chars": 3,  # 不超過此字數標「字數不足」
    "short_score_compress": 0.5,  # 字數不足時候選分數向 0.5 壓縮的比例
    # ---------------- 顏色（3B.4）
    "color_erode_frac": 0.25,  # 內縮量（筆畫粗細的比例）
    "min_core_pixels": 30,  # 核心像素少於此數改用極值法
    "thin_stroke_px": 2.5,  # 筆畫粗細低於此值（工作影像像素）時，內縮後的核心仍是混色，改用極值法
    "extreme_percentile": 95.0,  # 極值法取的百分位
    "ring_inner_px": 2.0,  # 鄰近背景環的內距（工作影像像素）
    "ring_outer_px": 6.0,  # 外距
    "effect_delta": 45.0,  # 緊鄰環與遠處底色的色差超過此值（RGB）才可能是特效
    "effect_offline": 35.0,  # 緊鄰環顏色離「筆色—底色」連線超過此值（RGB）才判為特效
    # ---------------- 其他屬性（3B.6）
    "slant_range": (-6.0, 26.0),  # 傾斜角搜尋範圍（度）
    "slant_step": 1.0,  # 搜尋步長（度）
    "italic_min_slant": 4.0,  # 傾斜角超過此值才判斜體（實測直立字型在乾淨影像上 |傾斜| < 1 度）
    "min_stroke_px": 1.6,  # 筆畫粗細低於此值（工作影像像素）不判粗細
    "bold_stem_ratio": 1.6,  # 字族沒有 Bold 時，Bold 與 Regular 的筆畫粗細比先驗
    "bold_iou_scale": 0.08,  # 粗體判斷中，Bold 與 Regular 重繪重疊率差的尺度
    "run_split_penalty": 12.0,  # 行內分段的切點代價（標準化後的平方誤差單位）
    "run_color_scale": 30.0,  # 分段時顏色差的尺度（RGB）
    "run_size_scale": 0.10,  # 分段時字級差的尺度（對數）
    "run_min_latin": 3,  # 因字型或屬性不同而切段的最短長度：拉丁字母數
    "run_min_cjk": 2,  # 中文字數
    "split_by_script": True,  # 全形字與拉丁文字分段量測
    # ---------------- 信心值
    "conf_rel_scale": 0.06,  # 字級相對誤差達此值時信心值約 0.37（exp(−1)）
    # ---------------- 除錯
    "debug": False,  # True 時印出每一段的候選字型評分（開發用）
    "debug_family": None,  # 除錯時額外印出這個字族的評分
}

_DESCENDERS = set("gjpqy,;Q()[]{}|/@$")


def _merge(params: dict | None) -> dict:
    out = dict(DEFAULT_PARAMS)
    if params:
        out.update(params)
    return out


# ====================================================================== 內部資料


@dataclass
class _Geom:
    crop: np.ndarray  # mask 座標的 RGB（float32）
    M: np.ndarray  # 3×3：mask 連續座標 → 影像連續座標
    f: float  # mask 像素 ÷ 影像像素
    band: tuple[float, float]  # 原行框在 mask 中的 y 範圍
    xband: tuple[float, float]  # 原行框在 mask 中的 x 範圍
    em0: float  # 粗估字級（mask 像素）


@dataclass
class _Mask:
    cov: np.ndarray  # 清理後的覆蓋率（float32）
    binary: np.ndarray  # 清理後的二值遮罩
    ink: np.ndarray  # 筆色中心（RGB float）
    bg: np.ndarray  # 底色中心
    g: np.ndarray  # 投影值（0 底、1 筆）
    polarity: str
    level: int
    contrast: float
    rules: list[dict]
    sigma_edge: float  # mask 像素
    blur: float  # 由邊緣斜率估計的總模糊 σ（mask 像素）
    notes: list[str]
    blur_extra: float = 0.0  # 重繪比對時要加的額外模糊（由重繪校準，mask 像素）
    sw_run: float = 0.0  # 目前這一段的筆畫粗細（mask 像素，soft 估計）
    run_cjk: bool = False
    f: float = 1.0  # mask 像素 ÷ 影像像素


@dataclass
class _Unit:
    i0: int  # 字的索引（含）
    i1: int  # （不含）
    cjk: bool
    a: float = 0.0  # 墨跡左緣（mask x）
    b: float = 0.0  # 墨跡右緣
    exact: bool = False  # 左右緣由空隙定出（可用於寬度證據）
    center: Optional[float] = None  # 重繪時以墨跡中心對齊（調字距時逐字放在間距格點上）


@dataclass
class _FaceEval:
    face: FontFace
    s_h: float = float("nan")
    sig_h: float = float("inf")
    base_b: float = 0.0
    base_t: float = 0.0
    xm: float = 0.0
    v_rms: float = float("inf")
    s_w: float = float("nan")
    sig_w: float = float("inf")
    s_span: float = float("nan")
    s_p: float = float("nan")
    sig_p: float = float("inf")
    size: float = float("nan")
    sig: float = float("inf")
    synthetic_slant: float = 0.0
    metric: float = 0.0
    redraw: float = 0.0
    redraw_iou: float = 0.0
    score: float = 0.0
    evidence: dict = field(default_factory=dict)


# ====================================================================== 公開介面


def measure_line(image: np.ndarray, line: OcrLine, fonts: FontLibrary, params: dict | None = None) -> LineAttributes:
    """量測一行文字的屬性：筆畫遮罩、顏色、字級與字型候選、粗斜體、底線刪除線、基線、精修四角。"""
    P = _merge(params)
    img = np.ascontiguousarray(np.asarray(image)[..., :3])
    text = line.text or ""
    notes: list[str] = []
    if line.vertical:
        notes.append("直排文字尚未支援，以橫排方式量測，量測不可靠")
    geo = _flatten(img, np.asarray(line.quad, float).reshape(4, 2), P)
    mk = _stroke_mask(geo, P)
    notes += mk.notes
    idx_ns = [i for i, c in enumerate(text) if not c.isspace()]
    colmax = mk.cov.max(axis=0) if mk.cov.size else np.zeros(1)
    x_l, x_r = _first_cross(colmax), _last_cross(colmax)
    if not idx_ns or x_l is None or x_r is None or x_r - x_l < 1:
        notes.append("遮罩中沒有墨跡或沒有文字內容，無法量測")
        return _empty_result(line, geo, mk, notes)

    slant, sig_slant, slant_q = _slant(mk.cov, geo, P)
    work_cov = _deslant(mk.cov, slant) if abs(slant) > 2.5 else mk.cov
    ref = _reference_face(fonts, text, P)
    units = _make_units(text)
    _locate_units(units, text, ref, colmax, x_l, x_r, geo.em0, P)

    # ---------------- 行內分段：先依文字類別切，再依屬性改變點切
    groups = _script_groups(units, text, P) if P["split_by_script"] else [list(range(len(units)))]
    runs_out: list[RunAttributes] = []
    run_infos: list[dict] = []
    for gi in groups:
        sub_units = [units[k] for k in gi]
        for seg in _split_by_attributes(img, geo, mk, work_cov, sub_units, text, ref, P):
            info = _solve_run(img, geo, mk, work_cov, seg, text, fonts, ref, slant, sig_slant, slant_q, P)
            run_infos.append(info)
            runs_out.append(info["run"])
    if not runs_out:
        return _empty_result(line, geo, mk, notes)

    # ---------------- 基線、四角、每個字的起點
    main = max(run_infos, key=lambda r: r["n_chars"])
    fe: _FaceEval = main["eval"]
    f = geo.f

    def y_base(x: float) -> float:
        return fe.base_b + fe.base_t * (x - fe.xm)

    top_off = max(r["asc_px"] for r in run_infos)
    bot_off = min(r["desc_px"] for r in run_infos)
    corners_m = np.array(
        [[x_l, y_base(x_l) - top_off], [x_r, y_base(x_r) - top_off], [x_r, y_base(x_r) - bot_off], [x_l, y_base(x_l) - bot_off]]
    )
    quad = _to_image(geo.M, corners_m)
    baseline = _to_image(geo.M, np.array([[x_l, y_base(x_l)], [x_r, y_base(x_r)]]))
    char_x: list[float] = []
    for r in run_infos:
        char_x += r["char_x_m"]
    char_x = [(x - x_l) / f for x in sorted(char_x)]
    for r in run_infos:
        for n in r["line_notes"]:
            if n not in notes:
                notes.append(n)
    return LineAttributes(
        quad=quad,
        baseline=baseline,
        runs=runs_out,
        char_x=char_x,
        mask=mk.binary.copy(),
        mask_to_image=geo.M.copy(),
        notes=notes,
        baseline_error_px=float(main["base_err"] / f),
        polarity=mk.polarity,
        blur_px=float(mk.blur_extra / f),
    )


# ====================================================================== (1) 取樣與遮罩


def _flatten(img: np.ndarray, q: np.ndarray, P: dict) -> _Geom:
    tl, tr, br, bl = q
    u = ((tr - tl) + (br - bl)) / 2.0
    lw = float(np.hypot(*u)) or 1.0
    e1 = u / lw
    e2 = np.array([-e1[1], e1[0]])
    v = ((bl - tl) + (br - tr)) / 2.0
    if float(v @ e2) < 0:
        e2 = -e2
    p1 = (q - tl) @ e1
    p2 = (q - tl) @ e2
    wq, hq = float(p1.max() - p1.min()), float(max(p2.max() - p2.min(), 1.0))
    margin = max(P["margin_min_px"], P["margin_frac"] * hq)
    if hq < P["upsample_below_px"]:
        f = float(min(P["upsample_max"], max(1, math.ceil(P["upsample_target_px"] / hq))))
    elif hq > P["max_line_px"]:
        f = P["max_line_px"] / hq
    else:
        f = 1.0
    origin = tl + (p1.min() - margin) * e1 + (p2.min() - margin) * e2
    A = np.column_stack([e1, e2]) / f
    wc = int(math.ceil((wq + 2 * margin) * f))
    hc = int(math.ceil((hq + 2 * margin) * f))
    mcv = np.zeros((2, 3))
    mcv[:, :2] = A
    mcv[:, 2] = A @ np.array([0.5, 0.5]) + origin - 0.5
    crop = cv2.warpAffine(img.astype(np.float32), mcv, (max(wc, 2), max(hc, 2)), flags=cv2.INTER_CUBIC | cv2.WARP_INVERSE_MAP, borderMode=cv2.BORDER_REPLICATE)
    crop = np.clip(crop, 0, 255)
    M = np.eye(3)
    M[:2, :2] = A
    M[:2, 2] = origin
    band = (margin * f, (margin + hq) * f)
    xband = (margin * f, (margin + wq) * f)
    return _Geom(crop, M, f, band, xband, hq * f / P["quad_em_ratio"])


def _to_image(M: np.ndarray, pts: np.ndarray) -> np.ndarray:
    pts = np.asarray(pts, float).reshape(-1, 2)
    return (M[:2, :2] @ pts.T).T + M[:2, 2]


def _stroke_mask(geo: _Geom, P: dict) -> _Mask:
    crop = geo.crop
    hc, wc = crop.shape[:2]
    notes: list[str] = []
    pix = crop.reshape(-1, 3)
    step = max(1, pix.shape[0] // int(P["kmeans_max_samples"]))
    sample = np.ascontiguousarray(pix[::step], dtype=np.float32)
    crit = (cv2.TERM_CRITERIA_EPS + cv2.TERM_CRITERIA_MAX_ITER, 30, 0.5)
    yy, xx = np.mgrid[0:hc, 0:wc]
    in_band = (yy + 0.5 >= geo.band[0]) & (yy + 0.5 <= geo.band[1]) & (xx + 0.5 >= geo.xband[0]) & (xx + 0.5 <= geo.xband[1])
    if not in_band.any() or in_band.all():
        in_band = np.zeros_like(in_band)
        in_band[hc // 4 : hc - hc // 4, :] = True
    level = 1
    if np.ptp(sample, axis=0).max() < 1.0:
        c0 = c1 = sample.mean(axis=0)
        c1 = c0 + 1.0
    else:
        _, _, centers = _kmeans(sample, 2, crit)
        c0, c1 = centers[0].astype(np.float64), centers[1].astype(np.float64)
    d = c1 - c0
    dd = float(d @ d) or 1.0
    g = ((crop - c0) @ d / dd).astype(np.float32)
    b1 = g > 0.5
    fc, fm = b1[in_band].mean(), b1[~in_band].mean()
    if abs(fc - fm) < 0.02:
        ink_is_1 = b1.mean() < 0.5
    else:
        ink_is_1 = fc > fm
    if not ink_is_1:
        g = 1.0 - g
        c0, c1 = c1, c0
    ink, bg = c1, c0
    contrast = float(np.linalg.norm(ink - bg))
    # 兩色模型的適合度：像素離「底色—筆色」連線的垂直距離；很多像素離線很遠表示不只兩色（字壓在照片上）
    gs = np.clip(((sample - c0) @ d) / dd, 0, 1)
    perp = np.linalg.norm(sample - (c0 + gs[:, None] * d), axis=1) / max(math.sqrt(dd), 1.0)
    spread = float((perp > 0.25).mean())
    cov = np.clip(g, 0.0, 1.0)
    if spread > P["level3_spread"] and min(hc, wc) > 4:
        alt = _level3(crop, sample, in_band, crit)
        if alt is not None:
            cov, ink, bg = alt
            level = 3
            contrast = float(np.linalg.norm(ink - bg))
            notes.append("底色複雜，筆畫遮罩改用多色分群（第 3 級），量測較不可靠")
    if level == 1:
        k = int(max(3, P["bg_kernel_frac"] * (geo.band[1] - geo.band[0]))) | 1
        bg_local = cv2.morphologyEx(g, cv2.MORPH_OPEN, np.ones((k, k), np.uint8))
        bg_local = cv2.blur(bg_local, (k, k))
        rng = float(np.percentile(bg_local, 98) - np.percentile(bg_local, 2))
        if rng > P["nonuniform_bg"]:
            level = 2
            b = (g > 0.5).astype(np.float32)
            sig = max(2.0, (geo.band[1] - geo.band[0]))
            num = cv2.GaussianBlur(g * b, (0, 0), sig)
            den = cv2.GaussianBlur(b, (0, 0), sig)
            ink_local = np.where(den > 1e-3, num / np.maximum(den, 1e-3), 1.0)
            cov = np.clip((g - bg_local) / np.maximum(ink_local - bg_local, 0.2), 0.0, 1.0).astype(np.float32)
            notes.append("底色不均，已用局部二值化（第 2 級）")
    if contrast < P["min_contrast"]:
        notes.append("筆畫與底色太接近，遮罩不可靠")
    lum = lambda c: 0.299 * c[0] + 0.587 * c[1] + 0.114 * c[2]  # noqa: E731
    polarity = "dark_on_light" if lum(ink) < lum(bg) else "light_on_dark"

    # ---- 清理連通元件
    binary = cov >= 0.5
    n, lab, stats, _ = cv2.connectedComponentsWithStats(binary.astype(np.uint8), connectivity=8)
    keep = np.zeros(n, bool)
    em = geo.em0
    by0, by1 = geo.band
    hb = by1 - by0
    for i in range(1, n):
        x, y, w, h, area = stats[i]
        if area < P["speck_area_frac"] * em * em:
            continue
        if y + h <= by0 + 0.12 * hb or y >= by1 - 0.12 * hb:
            continue
        overlap = max(0.0, min(y + h, by1) - max(y, by0))
        if (y == 0 or y + h >= hc) and overlap < 0.5 * h:
            continue
        keep[i] = True
    kept = keep[lab]
    # 整塊在左右外擴區、且離其他墨跡很遠的元件（鄰近的別的文字）也刪掉
    if kept.any():
        cols = np.flatnonzero(kept.any(axis=0))
        core_cols = cols[(cols + 0.5 >= geo.xband[0]) & (cols + 0.5 <= geo.xband[1])]
        if core_cols.size:
            for i in np.flatnonzero(keep):
                x, y, w, h, _ = stats[i]
                if x + w < geo.xband[0] and core_cols.min() - (x + w) > 0.8 * em:
                    keep[i] = False
                elif x > geo.xband[1] and x - core_cols.max() > 0.8 * em:
                    keep[i] = False
            kept = keep[lab]
    near = cv2.dilate(kept.astype(np.uint8), np.ones((3, 3), np.uint8)) > 0
    cov = (cov * near).astype(np.float32)
    binary = kept

    # ---- 底線與刪除線候選（先從遮罩拿掉）
    rules: list[dict] = []
    L = int(max(5, P["rule_min_len_em"] * em))
    if binary.shape[1] > L:
        opened = cv2.morphologyEx(binary.astype(np.uint8), cv2.MORPH_OPEN, np.ones((1, L), np.uint8))
        if opened.any():
            n2, lab2, st2, _ = cv2.connectedComponentsWithStats(opened, connectivity=8)
            for i in range(1, n2):
                x, y, w, h, _ = st2[i]
                if h <= P["rule_max_thick_em"] * em + 2 and w >= L:
                    pix_r = lab2 == i
                    rows = np.flatnonzero(pix_r.any(axis=1))
                    rules.append({"x0": float(x), "x1": float(x + w), "y0": float(rows.min()), "y1": float(rows.max() + 1), "pix": pix_r})
    for r in rules:
        rm = cv2.dilate(r["pix"].astype(np.uint8), np.ones((3, 1), np.uint8)) > 0
        r["removed"] = rm
        binary = binary & ~rm
        cov = np.where(rm, 0.0, cov).astype(np.float32)

    # ---- 模糊與邊緣定位誤差
    gx = cv2.Sobel(cov, cv2.CV_32F, 1, 0, ksize=3) / 8.0
    gy = cv2.Sobel(cov, cv2.CV_32F, 0, 1, ksize=3) / 8.0
    gm = np.hypot(gx, gy)
    mid = (cov > 0.3) & (cov < 0.7)
    G = float(np.median(gm[mid])) if mid.sum() > 10 else 0.5
    G = max(G, 1e-3)
    blur = 1.0 / (G * math.sqrt(2 * math.pi))
    far_bg = (cv2.dilate(binary.astype(np.uint8), np.ones((7, 7), np.uint8)) == 0) & in_band
    noise = float(np.std(g[far_bg])) if far_bg.sum() > 20 else 0.05
    sigma_edge = math.sqrt((P["edge_err_px"] * geo.f) ** 2 + (noise / G) ** 2) + 0.1 * blur
    return _Mask(cov, binary, ink, bg, g, polarity, level, contrast, rules, sigma_edge, blur, notes, 0.0, geo.f)


def _kmeans(sample: np.ndarray, k: int, crit) -> tuple:
    """k-means 分群（k 為 2 或 3），初始點用固定規則挑，不靠亂數。

    原本用 OpenCV 的 k-means++ 挑初始點，它用的是 OpenCV 每個執行緒共用、會一直往前跑的亂數產生器：
    同一行字第幾次被量，分群結果就不同（實測同一行字級 32.05 → 31.48 → 32.05 像素，字型分數 0.91 → 0.68），
    結果取決於之前量過哪些字，測試也會因執行順序而時好時壞；只固定種子則是固定在某個不一定好的結果。
    現在沿顏色分布的主軸取兩端（第 5 與第 95 百分位；k = 3 時再加中位數）最接近的實際像素當初始中心，
    再讓 k-means 收斂：墨跡與底色一定落在兩端，結果只取決於輸入。"""
    x = sample.astype(np.float64)
    mean = x.mean(axis=0)
    centered = x - mean
    _, _, vt = np.linalg.svd(centered, full_matrices=False)
    proj = centered @ vt[0]
    qs = (5.0, 95.0) if k == 2 else (5.0, 50.0, 95.0)
    seeds = np.array([x[int(np.argmin(np.abs(proj - np.percentile(proj, q))))] for q in qs])
    if k > len(qs):
        raise ValueError("k 只支援 2 或 3")
    dist = ((x[:, None, :] - seeds[None, :, :]) ** 2).sum(axis=2)
    labels = dist.argmin(axis=1).astype(np.int32).reshape(-1, 1)
    if len(np.unique(labels)) < k:  # 顏色幾乎一樣：初始分群不足 k 群時，退回固定種子的 k-means++
        cv2.setRNGSeed(0x6D6F7274)
        return cv2.kmeans(sample, k, None, crit, 3, cv2.KMEANS_PP_CENTERS)
    return cv2.kmeans(sample, k, labels, crit, 1, cv2.KMEANS_USE_INITIAL_LABELS)


def _level3(crop: np.ndarray, sample: np.ndarray, in_band: np.ndarray, crit) -> Optional[tuple[np.ndarray, np.ndarray, np.ndarray]]:
    """第 3 級：K=3 分群，挑行框內佔比最高、外擴區佔比最低的一群當筆畫。"""
    try:
        _, _, centers = _kmeans(sample, 3, crit)
    except cv2.error:
        return None
    centers = centers.astype(np.float64)
    pix = crop.reshape(-1, 3).astype(np.float64)
    dist = np.stack([np.linalg.norm(pix - c, axis=1) for c in centers], axis=1)
    labels = dist.argmin(axis=1).reshape(crop.shape[:2])
    best, best_s = None, -1e9
    for k in range(3):
        m = labels == k
        frac = m.mean()
        if frac < 0.01 or frac > 0.6:
            continue
        s = m[in_band].mean() - m[~in_band].mean()
        if s > best_s:
            best, best_s = k, s
    if best is None:
        return None
    d_ink = dist[:, best]
    others = np.delete(dist, best, axis=1).min(axis=1)
    cov = np.clip(others / np.maximum(d_ink + others, 1e-6), 0, 1)
    cov = np.clip((cov - 0.5) * 2.0 + 0.5, 0, 1).reshape(crop.shape[:2]).astype(np.float32)
    bg_pix = pix[(labels != best).ravel()]
    bg = np.median(bg_pix, axis=0) if bg_pix.size else centers[best] * 0
    return cov, centers[best], bg


# ====================================================================== 共用小工具


def _first_cross(v: np.ndarray, thr: float = 0.5) -> Optional[float]:
    idx = np.flatnonzero(v >= thr)
    if idx.size == 0:
        return None
    j = int(idx[0])
    if j == 0:
        return 0.0
    a, b = float(v[j - 1]), float(v[j])
    frac = (thr - a) / (b - a) if b > a else 0.5
    return j - 0.5 + frac


def _last_cross(v: np.ndarray, thr: float = 0.5) -> Optional[float]:
    idx = np.flatnonzero(v >= thr)
    if idx.size == 0:
        return None
    j = int(idx[-1])
    if j >= len(v) - 1:
        return float(len(v))
    a, b = float(v[j]), float(v[j + 1])
    frac = (a - thr) / (a - b) if a > b else 0.5
    return j + 0.5 + frac


def _gaps(colmax: np.ndarray, x_l: float, x_r: float) -> list[tuple[float, float]]:
    """x_l 到 x_r 之間覆蓋率低於 0.5 的欄段（連續座標）。"""
    on = colmax >= 0.5
    out = []
    j = int(max(0, math.floor(x_l)))
    n = len(colmax)
    end = int(min(n, math.ceil(x_r)))
    while j < end:
        if not on[j]:
            k = j
            while k < end and not on[k]:
                k += 1
            if j > 0 and k < n:
                g0 = _last_cross(colmax[: j + 1]) if j > 0 else float(j)
                g1 = (k - 1) + (_first_cross(colmax[k - 1 :]) or 0.0) if k - 1 >= 0 else float(k)
                out.append((g0 if g0 is not None else float(j), g1))
            j = k
        else:
            j += 1
    return out


def _deslant(cov: np.ndarray, slant_deg: float) -> np.ndarray:
    t = math.tan(math.radians(slant_deg))
    h, w = cov.shape
    yc = h / 2.0
    m = np.float32([[1, t, -t * yc], [0, 1, 0]])
    return cv2.warpAffine(cov, m, (w, h), flags=cv2.INTER_LINEAR, borderValue=0)


def _is_punct(ch: str, P: dict) -> bool:
    return ch in P["cjk_punct"] or unicodedata.category(ch).startswith("P")


def _reference_face(fonts: FontLibrary, text: str, P: dict) -> Optional[FontFace]:
    cover = fonts.faces(text)
    for fam in P["reference_families"]:
        for f in cover:
            if f.family == fam and f.style == "Regular":
                return f
    if cover:
        reg = [f for f in cover if f.style == "Regular"]
        return (reg or cover)[0]
    faces = fonts.faces()
    return faces[0] if faces else None


# ====================================================================== (3a) 單位切分與定位


def _make_units(text: str) -> list[_Unit]:
    units: list[_Unit] = []
    i = 0
    n = len(text)
    while i < n:
        if text[i].isspace():
            i += 1
            continue
        cjk = is_cjk_char(text[i])
        j = i + 1
        while j < n and not text[j].isspace() and is_cjk_char(text[j]) == cjk:
            j += 1
        units.append(_Unit(i, j, cjk))
        i = j
    return units


def _locate_units(units: list[_Unit], text: str, ref: Optional[FontFace], colmax: np.ndarray, x_l: float, x_r: float, em: float, P: dict) -> None:
    if not units:
        return
    units[0].a, units[-1].b = x_l, x_r
    units[0].exact = units[-1].exact = True
    if len(units) == 1:
        return
    lay = ref.layout(text) if ref is not None else None
    ib = lay.ink_bounds() if lay else None
    if lay is None or ib is None or ib[1] - ib[0] <= 0:
        # 沒有參考字型：按字數平均分配
        tot = len(text)
        for k, u in enumerate(units):
            u.a = x_l + (x_r - x_l) * u.i0 / tot if k else x_l
            u.b = x_l + (x_r - x_l) * u.i1 / tot if k < len(units) - 1 else x_r
        return
    sc = (x_r - x_l) / (ib[1] - ib[0])

    def to_px(e: float) -> float:
        return x_l + (e - ib[0]) * sc

    gaps = _gaps(colmax, x_l, x_r)
    used_until = x_l
    left_ok = [k == 0 for k in range(len(units))]
    right_ok = [k == len(units) - 1 for k in range(len(units))]
    for k in range(len(units) - 1):
        u, w = units[k], units[k + 1]
        bu = lay.ink_bounds(u.i0, u.i1)
        bw = lay.ink_bounds(w.i0, w.i1)
        e_end = to_px(bu[1]) if bu else to_px(lay.starts[u.i1 - 1])
        e_start = to_px(bw[0]) if bw else to_px(lay.starts[w.i0])
        center = 0.5 * (e_end + e_start)
        spaced = any(text[j].isspace() for j in range(u.i1, w.i0))
        tol = max(0.8 * em, 0.08 * (x_r - x_l)) if spaced else max(0.5 * em, 0.04 * (x_r - x_l))
        best, best_s = None, -1e18
        for g0, g1 in gaps:
            if g0 < used_until - 1e-6:
                continue
            gc = 0.5 * (g0 + g1)
            dist = abs(gc - center)
            if dist > tol:
                continue
            s = (g1 - g0) - 0.3 * dist if spaced else -dist + 0.2 * (g1 - g0)
            if s > best_s:
                best, best_s = (g0, g1), s
        if best is not None:
            u.b, w.a = best
            right_ok[k] = left_ok[k + 1] = True
            used_until = best[1]
        else:
            u.b, w.a = e_end, e_start
    # 第一個與最後一個單位的外緣由整行墨跡定出；內緣若沒找到空隙就不精確
    for k, u in enumerate(units):
        u.exact = left_ok[k] and right_ok[k]
        if u.b <= u.a:
            u.b = u.a + 1.0
            u.exact = False


# ====================================================================== (3b) 每個字的上下緣


def _char_columns(units: list[_Unit], text: str, face: FontFace) -> dict[int, tuple[float, float]]:
    """以 face 的排字結果把每個字映到 mask 的欄範圍（墨跡左右）。"""
    out: dict[int, tuple[float, float]] = {}
    for u in units:
        sub = text[u.i0 : u.i1]
        lay = face.layout(sub)
        ib = lay.ink_bounds()
        if ib is None or ib[1] <= ib[0]:
            continue
        sc = (u.b - u.a) / (ib[1] - ib[0])
        for j, box in enumerate(lay.ink):
            if box is None:
                continue
            out[u.i0 + j] = (u.a + (box[0] - ib[0]) * sc, u.a + (box[1] - ib[0]) * sc)
    return out


def _char_vertical(cov: np.ndarray, cols: dict[int, tuple[float, float]]) -> dict[int, tuple[float, float, float]]:
    """每個字的 (x 中心, 上緣 y, 下緣 y)；取該字欄範圍的中央 70 %。"""
    out: dict[int, tuple[float, float, float]] = {}
    w = cov.shape[1]
    for i, (c0, c1) in cols.items():
        span = c1 - c0
        m0, m1 = c0 + 0.15 * span, c1 - 0.15 * span
        j0, j1 = int(max(0, math.floor(m0))), int(min(w, math.ceil(m1)))
        if j1 <= j0:
            j0, j1 = int(max(0, math.floor(0.5 * (c0 + c1)))), int(min(w, math.floor(0.5 * (c0 + c1)) + 1))
        if j1 <= j0:
            continue
        rowmax = cov[:, j0:j1].max(axis=1)
        top, bot = _first_cross(rowmax), _last_cross(rowmax)
        if top is None or bot is None or bot - top < 0.5:
            continue
        out[i] = (0.5 * (c0 + c1), top, bot)
    return out


def _vfit(face: FontFace, text: str, vert: dict[int, tuple[float, float, float]], idxs: list[int], sigma: float, P: dict, run_off: int = 0, run_text: str = "") -> Optional[tuple]:
    """字型專屬的垂直模型：y = b + t·(x − x̄) − s·e，Tukey 雙權重 IRLS。"""
    lay = face.layout(run_text or text)
    ys, xs, es = [], [], []
    for i in idxs:
        if i not in vert:
            continue
        j = i - run_off
        if j < 0 or j >= len(lay.ink) or lay.ink[j] is None:
            continue
        xc, top, bot = vert[i]
        box = lay.ink[j]
        ys += [top, bot]
        xs += [xc, xc]
        es += [box[2], box[3]]
    if len(ys) < 2:
        return None
    y, x, e = np.array(ys), np.array(xs), np.array(es)
    if np.ptp(e) < 0.05:
        return None
    xm = float(x.mean())
    n_chars = len(ys) // 2
    slope = n_chars >= 3 and np.ptp(x) > 0
    A = np.column_stack([np.ones_like(y), x - xm, -e]) if slope else np.column_stack([np.ones_like(y), -e])
    w = np.ones_like(y)
    sol = None
    s_guess = None
    for _ in range(8):
        sw = np.sqrt(w)
        try:
            sol, *_ = np.linalg.lstsq(A * sw[:, None], y * sw, rcond=None)
        except np.linalg.LinAlgError:
            return None
        s_guess = sol[-1]
        r = y - A @ sol
        sig_obs = math.sqrt(sigma**2 + (P["height_model_rel"] * max(abs(s_guess), 1.0)) ** 2)
        scale = max(sig_obs, 1.4826 * float(np.median(np.abs(r))))
        uu = r / (4.685 * scale)
        w = np.where(np.abs(uu) < 1, (1 - uu**2) ** 2, 0.0)
        if w.sum() < A.shape[1] + 0.5:
            w = np.ones_like(y)
            break
    r = y - A @ sol
    s = float(sol[-1])
    if not np.isfinite(s) or s <= 0:
        return None
    sig_obs = math.sqrt(sigma**2 + (P["height_model_rel"] * s) ** 2)
    dof = max(1.0, w.sum() - A.shape[1])
    var = max(sig_obs**2, float((w * r**2).sum() / dof))
    try:
        cov = var * np.linalg.inv((A * w[:, None]).T @ A)
    except np.linalg.LinAlgError:
        return None
    rms = math.sqrt(float((w * r**2).sum() / max(w.sum(), 1e-9)))
    b = float(sol[0])
    t = float(sol[1]) if slope else 0.0
    return s, math.sqrt(max(cov[-1, -1], 0.0)), b, t, xm, rms, math.sqrt(max(cov[0, 0], 0.0))


# ====================================================================== (3e) 中文間距


def _cjk_pitch(cov: np.ndarray, u: _Unit, text: str, sigma: float, P: dict) -> Optional[dict]:
    """連續全形字的間距：逐格取墨跡中心，穩健直線擬合 c_i = c0 + i·p。"""
    n = u.i1 - u.i0
    if n < 2:
        return None
    colmax = cov.max(axis=0)
    p = (u.b - u.a) / max(n - 0.1, 1.0)
    c0 = u.a + 0.45 * p
    use = [k for k in range(n) if not _is_punct(text[u.i0 + k], P)]
    if len(use) < 2:
        return None
    centers = np.full(n, np.nan)
    for _ in range(4):
        for k in range(n):
            lo, hi = c0 + (k - 0.5) * p, c0 + (k + 0.5) * p
            j0, j1 = int(max(0, math.floor(lo))), int(min(len(colmax), math.ceil(hi)))
            seg = colmax[j0:j1]
            if seg.size < 2 or seg.max() < 0.5:
                centers[k] = np.nan
                continue
            l, r = _first_cross(seg), _last_cross(seg)
            centers[k] = j0 + 0.5 * (l + r)
        ks = np.array([k for k in use if np.isfinite(centers[k])], float)
        if ks.size < 2:
            return None
        cs = centers[ks.astype(int)]
        w = np.ones_like(cs)
        for _it in range(6):
            A = np.column_stack([np.ones_like(ks), ks])
            sw = np.sqrt(w)
            sol, *_ = np.linalg.lstsq(A * sw[:, None], cs * sw, rcond=None)
            r = cs - A @ sol
            scale = max(sigma, 1.4826 * float(np.median(np.abs(r))) if r.size > 2 else sigma)
            uu = r / (4.685 * scale)
            w = np.where(np.abs(uu) < 1, (1 - uu**2) ** 2, 0.0)
            if w.sum() < 2:
                w = np.ones_like(cs)
                break
        c0, p = float(sol[0]), float(sol[1])
        if p <= 0:
            return None
    N = int((w > 0).sum())
    r = cs - (c0 + p * ks)
    resid = math.sqrt(float((w * r**2).sum() / max(w.sum() - 2, 1))) if N > 2 else sigma
    sig_c = max(resid, sigma)
    sig_p = sig_c * math.sqrt(12.0 / (N * (N * N - 1))) if N >= 2 else float("inf")
    sig_p = math.sqrt(sig_p**2 + (P["pitch_sys_rel"] * p) ** 2)
    diffs = np.diff(cs[w > 0]) / np.maximum(np.diff(ks[w > 0]), 1)
    cv = float(1.4826 * np.median(np.abs(diffs - np.median(diffs))) / p) if diffs.size >= 3 else 0.0
    widths = []
    for k in range(n):
        lo, hi = c0 + (k - 0.5) * p, c0 + (k + 0.5) * p
        j0, j1 = int(max(0, math.floor(lo))), int(min(len(colmax), math.ceil(hi)))
        seg = colmax[j0:j1]
        if seg.size >= 2 and seg.max() >= 0.5 and not _is_punct(text[u.i0 + k], P):
            widths.append((k, _last_cross(seg) - _first_cross(seg)))
    return {"p": p, "sig_p": sig_p, "c0": c0, "n": N, "cv": cv, "resid": resid, "widths": widths}


# ====================================================================== (4) 傾斜


def _slant(cov: np.ndarray, geo: _Geom, P: dict) -> tuple[float, float, float]:
    """傾斜角（度）、誤差、峰的顯著程度（0 到 1）。"""
    h, w = cov.shape
    sc = min(1.0, 48.0 / max(h, 1))
    small = cv2.resize(cov, (max(2, int(w * sc)), max(2, int(h * sc))), interpolation=cv2.INTER_AREA) if sc < 1 else cov
    lo, hi = P["slant_range"]
    angles = np.arange(lo, hi + 1e-9, P["slant_step"])
    vals = []
    hs, ws = small.shape
    pad = int(math.ceil(hs * math.tan(math.radians(max(abs(lo), abs(hi)))))) + 2
    padded = np.pad(small, ((0, 0), (pad, pad)))
    for a in angles:
        t = math.tan(math.radians(a))
        m = np.float32([[1, t, -t * hs / 2.0], [0, 1, 0]])
        sh = cv2.warpAffine(padded, m, (padded.shape[1], hs), flags=cv2.INTER_LINEAR, borderValue=0)
        p = sh.sum(axis=0)
        tot = p.sum()
        vals.append(float((p * p).sum() / (tot * tot)) if tot > 0 else 0.0)
    v = np.array(vals)
    k = int(v.argmax())
    a = float(angles[k])
    if 0 < k < len(v) - 1:
        den = v[k - 1] - 2 * v[k] + v[k + 1]
        if den < 0:
            a += float(0.5 * (v[k - 1] - v[k + 1]) / den) * P["slant_step"]
    vmax, vmin = float(v.max()), float(v.min())
    prom = (vmax - vmin) / vmax if vmax > 0 else 0.0
    thr = vmax - 0.1 * (vmax - vmin)
    above = angles[v >= thr]
    half = float(above.max() - above.min()) / 2.0 if above.size else 10.0
    sig = float(np.clip(0.5 * half + 0.5, 0.5, 10.0))
    return a, sig, prom


# ====================================================================== (3) 字型與字級求解


def _solve_run(img, geo: _Geom, mk: _Mask, wcov: np.ndarray, units: list[_Unit], text: str, fonts: FontLibrary, ref, slant: float, sig_slant: float, slant_q: float, P: dict) -> dict:
    f = geo.f
    notes: list[str] = []
    line_notes: list[str] = []
    i0, i1 = units[0].i0, units[-1].i1
    run_text = text[i0:i1]
    idxs = [i for u in units for i in range(u.i0, u.i1)]
    n_chars = len(idxs)
    faces = fonts.faces(run_text) or ([ref] if ref else [])
    sigma = mk.sigma_edge
    italic_meas = slant > P["italic_min_slant"]
    xa = int(max(0, math.floor(units[0].a - 1)))
    xb = int(min(mk.cov.shape[1], math.ceil(units[-1].b + 1)))
    mk.sw_run = stroke_width_soft(mk.cov[:, xa:xb])
    mk.run_cjk = sum(1 for u in units if u.cjk) * 2 >= len(units)

    # ---- 中文間距（與字型無關）
    pitch = None
    cjk_units = [u for u in units if u.cjk]
    pitches = []
    for u in cjk_units:
        pr = _cjk_pitch(mk.cov, u, text, sigma, P)
        if pr:
            pitches.append((u, pr))
    if pitches:
        ps = np.array([pr["p"] for _, pr in pitches])
        ws_ = np.array([1.0 / pr["sig_p"] ** 2 for _, pr in pitches])
        p_all = float((ps * ws_).sum() / ws_.sum())
        sig_all = float(1.0 / math.sqrt(ws_.sum()))
        n_all = sum(pr["n"] for _, pr in pitches)
        cv = max(pr["cv"] for _, pr in pitches)
        pitch = {"p": p_all, "sig": sig_all, "n": n_all, "cv": cv, "items": pitches}
        if n_all < P["min_cjk_chars"]:
            sig_all *= 2.0
            pitch["sig"] = sig_all
        if cv > P["pitch_cv_max"] and n_all >= 4:
            notes.append("比例寬度嫌疑：相鄰全形字的間距忽大忽小，不用間距捷徑")
            pitch["proportional"] = True

    # ---- 第一輪：參考字型映射 → 每個字的上下緣 → 所有候選的量度分數
    map_face = ref if (ref is not None and ref.covers(run_text)) else (faces[0] if faces else None)
    if map_face is None:
        return _fallback_run(units, text, mk, geo, img, P, ["字型庫中沒有涵蓋這些字的字型"])
    vert = _char_vertical(wcov, _char_columns(units, text, map_face))
    evals = [_eval_face(fc, units, text, run_text, i0, idxs, vert, mk, pitch, slant, sig_slant, italic_meas, P) for fc in faces]
    evals = [e for e in evals if e is not None]
    if not evals:
        return _fallback_run(units, text, mk, geo, img, P, ["無法由遮罩量出字的上下緣"])
    evals.sort(key=lambda e: -e.metric)
    # ---- 第二輪：以第一名字型重新映射欄範圍
    top = evals[0].face
    if top is not map_face:
        vert2 = _char_vertical(wcov, _char_columns(units, text, top))
        if len(vert2) >= max(1, len(vert) - 1):
            vert = vert2
            top_faces = [e.face for e in evals[: int(P["rescore_top"])]]
            evals = [_eval_face(fc, units, text, run_text, i0, idxs, vert, mk, pitch, slant, sig_slant, italic_meas, P) for fc in top_faces]
            evals = [e for e in evals if e is not None]
            evals.sort(key=lambda e: -e.metric)

    # ---- 模糊校準：用量度第一名的重繪結果，找出讓重疊率最高的額外模糊（之後所有候選都做同等模糊）
    sl0 = slant if evals[0].synthetic_slant else 0.0
    mk.blur_extra = _calibrate_blur(evals[0], units, text, mk, sl0, P)
    # ---- 模糊偏差自我校正：把第一名字型以同樣的模糊畫出來、用同樣的方法量，量到的字級與畫的字級之比就是偏差
    bias = _blur_bias(evals[0], units, text, run_text, i0, idxs, mk, sl0, slant, P) if mk.blur_extra > 0 else None
    if bias is not None:
        rh, rw = bias
        for e in evals:
            e.s_h /= rh
            e.sig_h = math.hypot(e.sig_h, P["bias_unc"] * abs(rh - 1) * e.s_h)
            if np.isfinite(e.s_w):
                e.s_w /= rw
                e.sig_w = math.hypot(e.sig_w, P["bias_unc"] * abs(rw - 1) * e.s_w)
            if np.isfinite(e.s_span):
                e.s_span /= rw
            _combine(e, P, use_width=True)
            _metric_score(e, P, use_width=True, slant=slant, sig_slant=sig_slant, italic_meas=italic_meas)
        evals.sort(key=lambda e: -e.metric)
    # ---- 例外偵測：左右對齊、調字距、水平縮放
    lead = evals[: min(5, len(evals))]
    justified = False
    spaced_units = len([u for u in units if not u.cjk]) >= 2 and any(text[j].isspace() for j in range(i0, i1))
    if spaced_units:
        rat = [e.s_span / e.s_w - 1 for e in lead[:3] if np.isfinite(e.s_span) and np.isfinite(e.s_w)]
        if rat and np.median(rat) > P["justify_tol"]:
            justified = True
            notes.append("左右對齊嫌疑：詞間空白比字型的空白寬，字級只用詞內寬度")
    tracking = False
    x_scale = None
    char_spacing = None
    rho = [e.evidence.get("rho") for e in lead if e.evidence.get("rho") is not None]
    if len(rho) >= 1:
        lr = np.log(np.array(rho))
        if (np.all(lr > P["tracking_tol"]) or np.all(lr < -P["tracking_tol"])) and n_chars >= 3:
            tracking = True
    if tracking:
        best = lead[0]
        q = _glyph_width_ratio(mk, units, text, best, pitch, P)
        r0 = float(np.median(rho))
        if q is not None and abs(math.log(q) - math.log(r0)) < P["xscale_tol"] * abs(math.log(r0)):
            x_scale = Estimate(q, 0.02 * q, 0.6)
            notes.append(f"水平縮放嫌疑：字身寬度一致地變為 {q:.3f} 倍，字級以高度為準")
        else:
            notes.append("調字距嫌疑：寬度（或間距）推得的字級與高度推得的不一致，字級以高度為準，差值記成字距")
        for e in evals:
            e.evidence["tracking"] = True
            _combine(e, P, use_width=False)
            _metric_score(e, P, use_width=False, slant=slant, sig_slant=sig_slant, italic_meas=italic_meas)
        evals.sort(key=lambda e: -e.metric)
    elif justified:
        for e in evals:
            _combine(e, P, use_width=True)
    # ---- 重繪用的單位：調字距時，全形字逐字放在量到的間距格點上（否則整段縮放會對不齊）
    r_units = units
    if tracking and pitch and not pitch.get("proportional"):
        r_units = []
        for u in units:
            pr = next((q for uu, q in pitch["items"] if uu is u), None)
            if pr is None:
                r_units.append(u)
                continue
            for k in range(u.i1 - u.i0):
                c = pr["c0"] + k * pr["p"]
                r_units.append(_Unit(u.i0 + k, u.i0 + k + 1, True, c - 0.5 * pr["p"], c + 0.5 * pr["p"], False, c))
    # ---- 重繪比對
    cands = _redraw_candidates(evals, fonts, P)
    for e in cands:
        e.redraw_iou = _redraw_score(e, r_units, text, mk, slant if e.synthetic_slant else 0.0, P)
        lo_, hi_ = P["redraw_iou_floor"], P["redraw_iou_ref"]
        e.redraw = float(np.clip((e.redraw_iou - lo_) / (hi_ - lo_), 0, 1))
        e.score = P["metric_weight"] * e.metric + P["redraw_weight"] * e.redraw
    rest = [e for e in evals if e not in cands]
    for e in rest:
        e.score = P["metric_weight"] * e.metric * 0.8
    ranked = sorted(cands, key=lambda e: -e.score) + sorted(rest, key=lambda e: -e.score)
    best = ranked[0]
    if P.get("debug"):
        for e in ranked[:12] + [e for e in evals if e.face.family == P.get("debug_family")]:
            print(f"    {e.face.family:22s} {e.face.style:14s} sc={e.score:.3f} met={e.metric:.3f} sk={e.evidence.get('sk',0):.2f} sv={e.evidence.get('sv',0):.2f} sw={e.evidence.get('sw',0):.2f} ss={e.evidence.get('ss',0):.2f} iou={e.redraw_iou:.3f} s_h={e.s_h:.2f}±{e.sig_h:.2f} s_w={e.s_w:.2f} s_p={e.s_p:.2f} rms={e.v_rms:.2f} sig={e.evidence.get('sigma',0):.2f}")
    # ---- 字級：最佳候選的證據組合 + 候選間的離散（模型不確定性）
    size, sig = best.size, best.sig
    top_sc = [e for e in ranked[:5] if np.isfinite(e.size)]
    wts = np.array([max(e.score, 1e-3) ** 4 for e in top_sc])
    if len(top_sc) > 1:
        sz = np.array([e.size for e in top_sc])
        spread = math.sqrt(float((wts * (sz - size) ** 2).sum() / wts.sum()))
        sig = math.sqrt(sig**2 + spread**2)
    conf_pen = 1.0
    no_match = best.score < P["min_match_score"] or best.redraw_iou < P["min_match_iou"]
    if no_match:
        notes.append("無吻合字型：候選字型的量度與重繪分數都偏低，字級改用與字型無關的證據並放大誤差")
        if pitch and not pitch.get("proportional") and not tracking and pitch["n"] >= P["min_cjk_chars"]:
            size, sig = pitch["p"], max(pitch["sig"], 0.01 * pitch["p"])  # 間距捷徑與字型無關（假設全形字 1 em）
        else:
            sig = max(sig, P["generic_rel_err"] * size)
        conf_pen *= 0.5
    short = n_chars <= P["short_line_chars"]
    if short:
        notes.append("字數不足：候選字型的分數差距不可靠")
        conf_pen *= 0.7
        sig = max(sig, 0.03 * size)
    if any("特效" in n for n in mk.notes) or mk.level == 3:
        sig *= 2.0
        conf_pen *= 0.5
    if mk.contrast < P["min_contrast"]:
        sig *= 2.0
        conf_pen *= 0.5
    if tracking:
        conf_pen *= 0.85
        best_adv = best.evidence.get("w_em_per_gap")
        if pitch and not pitch.get("proportional"):
            adv = best.evidence.get("cjk_adv", 1.0)
            char_spacing = Estimate((pitch["p"] - best.s_h * adv) / f, math.hypot(pitch["sig"], best.sig_h * adv) / f, 0.6)
        elif best_adv:
            W_meas, W_em, n_gap = best_adv
            if n_gap > 0:
                cs = (W_meas - best.s_h * W_em) / n_gap
                char_spacing = Estimate(cs / f, (best.sig_h * W_em / n_gap) / f, 0.5)
        if x_scale is not None:
            char_spacing = None
    rel = sig / size if size > 0 else 1.0
    conf = float(np.clip(math.exp(-rel / P["conf_rel_scale"]), 0.02, 0.98) * conf_pen)
    # ---- 候選清單（不定案）
    matches: list[FontMatch] = []
    for e in ranked[: P["max_candidates"]]:
        sc = e.score
        if short:
            sc = 0.5 + (sc - 0.5) * P["short_score_compress"]
        matches.append(FontMatch(e.face.family, e.face.style, float(np.clip(sc, 0, 1)), e.face.path))
    eq = fonts.equivalents(best.face) if best.face.family not in ("Unifont",) else []
    if eq:
        notes.append("量度等價（字寬相容）：" + "、".join(sorted({x.family for x in eq})))
    # ---- 粗體
    bold, sw_est = _bold(mk, r_units, text, best, fonts, slant if best.synthetic_slant else 0.0, P, notes, f)
    # ---- 斜體
    conf_it = 0.5 + 0.5 * math.tanh(abs(slant - P["italic_min_slant"]) / max(sig_slant, 1.0))
    conf_it *= 0.6 + 0.4 * min(1.0, slant_q * 20)
    if best.face.is_italic == italic_meas:
        conf_it = min(0.99, conf_it + 0.1)
    italic = (bool(italic_meas), float(np.clip(conf_it, 0.05, 0.99)))
    # ---- 顏色
    color, color_conf, background, effect_note = _colors(img, geo, mk, units, sw_est, P)
    if effect_note:
        notes.append(effect_note)
        conf *= 0.5
        sig *= 1.5
    # ---- 底線、刪除線
    underline, strike = False, False
    base_err = best.evidence.get("sig_b", sigma)
    for r in mk.rules:
        ov = max(0.0, min(r["x1"], units[-1].b) - max(r["x0"], units[0].a)) / max(units[-1].b - units[0].a, 1.0)
        if ov < 0.3:
            continue
        yc = 0.5 * (r["y0"] + r["y1"])
        xb = 0.5 * (r["x0"] + r["x1"])
        dy = (yc - (best.base_b + best.base_t * (xb - best.xm))) / best.size
        ulo, uhi = P["underline_range_em"]
        slo, shi = P["strike_range_em"]
        if ulo <= dy <= uhi:
            underline = True
        elif slo <= -dy <= shi:
            strike = True
        else:
            line_notes.append("行框內有長橫線（不在底線或刪除線的位置，可能是表格線或分隔線）")
    # ---- 每個字的起點（mask x）
    char_x_m = _char_starts(units, text, best)
    asc_px = best.face.ascender * size
    desc_px = best.face.descender * size
    ev = {k: Estimate(v[0] / f, v[1] / f, v[2]) for k, v in best.evidence.get("sizes", {}).items()}
    run = RunAttributes(
        start=i0,
        end=i1,
        color=color,
        color_confidence=color_conf,
        background=background,
        font_size_px=Estimate(float(size / f), float(sig / f), conf),
        fonts=matches,
        bold=bold,
        italic=italic,
        underline=underline,
        strikethrough=strike,
        char_spacing_px=char_spacing,
        notes=notes,
        slant_deg=Estimate(float(slant), float(sig_slant), float(np.clip(slant_q * 20, 0.05, 0.95))),
        stroke_width_px=Estimate(float(sw_est / f), float(max(0.3, 0.15 * sw_est) / f), 0.7) if sw_est > 0 else None,
        x_scale=x_scale,
        size_evidence=ev,
    )
    return {"run": run, "eval": best, "n_chars": n_chars, "asc_px": asc_px, "desc_px": desc_px, "char_x_m": char_x_m, "line_notes": line_notes, "base_err": base_err}


def _eval_face(face: FontFace, units, text, run_text, i0, idxs, vert, mk: _Mask, pitch, slant, sig_slant, italic_meas, P) -> Optional[_FaceEval]:
    e = _FaceEval(face)
    vf = _vfit(face, text, vert, idxs, mk.sigma_edge, P, run_off=i0, run_text=run_text)
    if vf is None:
        return None
    e.s_h, e.sig_h, e.base_b, e.base_t, e.xm, e.v_rms, sig_b = vf
    # 高度量化誤差（相關誤差，不隨字數平均）：相對誤差 = 量化像素 ÷ 主要高度（x 字高或字面高）
    xh = max(0.3, face.x_height if any(not is_cjk_char(c) for c in run_text if not c.isspace()) else 0.85)
    q = P["height_quant_px"] * mk_f_of(mk) / max(xh * e.s_h, 1.0)
    e.sig_h = math.hypot(e.sig_h, q * e.s_h)
    e.evidence["sig_b"] = sig_b
    e.evidence["sigma"] = mk.sigma_edge
    e.evidence["sw_meas"] = mk.sw_run
    try:
        e.evidence["stem"] = face.stem_ratio(cjk=mk.run_cjk)
    except Exception:
        e.evidence["stem"] = None
    e.synthetic_slant = slant if (italic_meas and not face.is_italic) else 0.0
    t = math.tan(math.radians(e.synthetic_slant))
    # ---- 寬度證據（逐單位）與整段
    num = den = 0.0
    W_meas = W_em = 0.0
    n_gap = 0
    sigma = mk.sigma_edge
    lat = [u for u in units if not u.cjk or not pitch or pitch.get("proportional")]
    for u in lat:
        if not u.exact:
            continue
        ib = face.layout(text[u.i0 : u.i1]).ink_bounds()
        if ib is None:
            continue
        w_em = (ib[1] - ib[0]) + t * (ib[2] - ib[3])
        if w_em <= 0:
            continue
        W = u.b - u.a
        s_k = W / w_em
        sig_k = s_k * math.sqrt((math.sqrt(2) * sigma / max(W, 1e-6)) ** 2 + P["width_model_rel"] ** 2)
        num += s_k / sig_k**2
        den += 1 / sig_k**2
        W_meas += W
        W_em += w_em
        n_gap += max(0, u.i1 - u.i0 - 1)
    if den > 0:
        e.s_w, e.sig_w = num / den, 1 / math.sqrt(den)
        e.evidence["w_em_per_gap"] = (W_meas, W_em, n_gap)
    if len(lat) >= 2 and all(u.exact for u in (lat[0], lat[-1])):
        a, b = lat[0].a, lat[-1].b
        span = text[lat[0].i0 : lat[-1].i1]
        ib = face.layout(span).ink_bounds()
        if ib is not None and ib[1] > ib[0]:
            e.s_span = (b - a) / ((ib[1] - ib[0]) + t * (ib[2] - ib[3]))
            e.evidence["sig_span"] = e.s_span * math.sqrt((math.sqrt(2) * sigma / (b - a)) ** 2 + P["width_model_rel"] ** 2)
    # ---- 中文間距：字級 = 間距 ÷ 該字型的平均前進寬度
    if pitch and not pitch.get("proportional"):
        advs = []
        for u, _pr in pitch["items"]:
            lay = face.layout(text[u.i0 : u.i1])
            if len(lay.starts) >= 2:
                advs.append((lay.advance - lay.starts[0]) / len(lay.starts))
        adv = float(np.mean(advs)) if advs else 1.0
        e.evidence["cjk_adv"] = adv
        e.s_p, e.sig_p = pitch["p"] / adv, pitch["sig"] / adv
    _combine(e, P, use_width=True)
    _metric_score(e, P, use_width=True, slant=slant, sig_slant=sig_slant, italic_meas=italic_meas)
    return e


def mk_f_of(mk: _Mask) -> float:
    return mk.f


def _combine(e: _FaceEval, P: dict, use_width: bool) -> None:
    """各證據以誤差平方倒數加權；不一致時依 χ² 放大誤差。"""
    items = [("height", e.s_h, e.sig_h)]
    if use_width and not e.evidence.get("tracking"):
        if np.isfinite(e.s_w):
            items.append(("width", e.s_w, e.sig_w))
        if np.isfinite(e.s_p):
            items.append(("pitch", e.s_p, e.sig_p))
    sizes = {"height": (e.s_h, e.sig_h, 0.8)}
    if np.isfinite(e.s_w):
        sizes["width"] = (e.s_w, e.sig_w, 0.7)
    if np.isfinite(e.s_p):
        sizes["pitch"] = (e.s_p, e.sig_p, 0.8)
    if np.isfinite(e.s_span):
        sizes["span"] = (e.s_span, e.evidence.get("sig_span", e.sig_w), 0.5)
    e.evidence["sizes"] = sizes
    vals = np.array([v for _, v, s in items if np.isfinite(v) and np.isfinite(s) and s > 0])
    sigs = np.array([s for _, v, s in items if np.isfinite(v) and np.isfinite(s) and s > 0])
    if vals.size == 0:
        e.size, e.sig = e.s_h, e.sig_h
        return
    w = 1 / sigs**2
    m = float((w * vals).sum() / w.sum())
    sig = float(1 / math.sqrt(w.sum()))
    if vals.size > 1:
        chi2 = float((w * (vals - m) ** 2).sum())
        dof = vals.size - 1
        if chi2 > dof:
            sig *= math.sqrt(chi2 / dof)
    e.size, e.sig = m, sig
    ref = e.s_w if np.isfinite(e.s_w) else (e.s_p if np.isfinite(e.s_p) else float("nan"))
    e.evidence["rho"] = ref / e.s_h if np.isfinite(ref) and e.s_h > 0 else None


def _metric_score(e: _FaceEval, P: dict, use_width: bool, slant: float, sig_slant: float, italic_meas: bool) -> None:
    sig_v = math.sqrt(e.evidence.get("sigma", 1.0) ** 2 + (P["height_model_rel"] * e.s_h) ** 2)
    sv = math.exp(-0.5 * (e.v_rms / sig_v) ** 2 / 4.0)
    sw = 1.0
    if use_width and not e.evidence.get("tracking"):
        for key, val, sg in (("width", e.s_w, e.sig_w), ("pitch", e.s_p, e.sig_p)):
            if np.isfinite(val):
                d = math.log(val / e.s_h)
                sd = math.sqrt((sg / val) ** 2 + (e.sig_h / e.s_h) ** 2 + P["width_model_rel"] ** 2 + 0.01**2)
                sw *= math.exp(-0.5 * (d / sd) ** 2 / 2.0)
    face_slant = _face_slant(e.face, P) if e.face.is_italic else (slant if e.synthetic_slant else 0.0)
    ss = math.exp(-0.5 * ((slant - face_slant) / math.hypot(sig_slant, P["slant_sigma_deg"])) ** 2)
    if e.synthetic_slant:
        ss *= P["synthetic_slant_penalty"]
    # 粗細吻合：量到的筆畫粗細比與字型的筆畫粗細比（同一個 soft 估計法）；設下限，避免單一證據主導
    sk = 1.0
    stem = e.evidence.get("stem")
    if stem and e.size > 0 and e.evidence.get("sw_meas", 0) > 0:
        d = math.log((e.evidence["sw_meas"] / e.size) / stem)
        sk = max(P["weight_floor"], math.exp(-0.5 * (d / P["weight_sigma"]) ** 2))
    e.metric = float(sv * sw * ss * sk)
    e.evidence["sv"], e.evidence["sw"], e.evidence["ss"], e.evidence["sk"] = sv, sw, ss, sk


_FACE_SLANT: dict[tuple[str, int], float] = {}


def _face_slant(face: FontFace, P: dict) -> float:
    """斜體字型「量得到的」傾斜角：以同一個量測方法量探針字串的重繪結果（字型檔的 italicAngle 與墨跡的實際傾斜常不同）。"""
    key = (face.path, face.index)
    if key not in _FACE_SLANT:
        probe = "".join(c for c in "Italic slant nmhdbq" if face.covers(c)) or "nmhd"
        m = face.render_layout(probe, 40.0, pad=6).mask
        geo = _Geom(np.zeros((1, 1, 3), np.float32), np.eye(3), 1.0, (0, m.shape[0]), (0, m.shape[1]), 40.0)
        _FACE_SLANT[key] = _slant(m, geo, P)[0] if m.any() else face.italic_angle
    return _FACE_SLANT[key]


def _redraw_candidates(evals: list[_FaceEval], fonts: FontLibrary, P: dict) -> list[_FaceEval]:
    k = int(P["topk_redraw"])
    chosen = list(evals[:k])
    fams = []
    for e in evals[:k]:
        if e.face.family not in fams:
            fams.append(e.face.family)
    by_face = {id(e.face): e for e in evals}
    for fam in fams[: int(P["style_families"])]:
        for fc in fonts.styles_of(fam):
            if fc.style in ("Regular", "Bold", "Italic", "Bold Italic") and id(fc) in by_face and by_face[id(fc)] not in chosen:
                chosen.append(by_face[id(fc)])
    return chosen


def _render_run(e: _FaceEval, units: list[_Unit], text: str, shape: tuple[int, int], slant: float, embolden: float = 0.0, face: Optional[FontFace] = None) -> np.ndarray:
    face = face or e.face
    canvas = np.zeros(shape, np.float32)
    s = e.size
    for u in units:
        sub = text[u.i0 : u.i1]
        rt = face.render_layout(sub, s, slant_deg=slant, embolden_px=embolden)
        m = rt.mask
        cm = m.max(axis=0)
        rl, rr = _first_cross(cm), _last_cross(cm)
        if rl is None or rr is None or rr - rl < 0.5:
            continue
        xs = 1.0
        if u.exact and u.center is None:
            xs = float(np.clip((u.b - u.a) / (rr - rl), 0.85, 1.15))
        xc = 0.5 * (u.a + u.b) if u.center is None else u.center
        yb = e.base_b + e.base_t * (xc - e.xm)
        dx = u.a - rl * xs if u.center is None else u.center - 0.5 * (rl + rr)
        tm = np.float32([[xs, 0, dx], [0, 1, yb - rt.baseline_y]])
        warped = cv2.warpAffine(m, tm, (shape[1], shape[0]), flags=cv2.INTER_LINEAR, borderValue=0)
        np.maximum(canvas, warped, out=canvas)
    return canvas


def _blur_like(canvas: np.ndarray, mk: _Mask) -> np.ndarray:
    extra = mk.blur_extra
    if extra > 0.2:
        return cv2.GaussianBlur(canvas, (0, 0), extra)
    return canvas


def _blur_bias(e: _FaceEval, units, text, run_text, i0, idxs, mk: _Mask, slant_render: float, slant_meas: float, P: dict) -> Optional[tuple[float, float]]:
    """(高度字級的偏差比, 寬度字級的偏差比)：以 e 的字型與字級畫出、加同等模糊，再用同一套量法量回來。"""
    if not np.isfinite(e.size) or e.size <= 1:
        return None
    canvas = _blur_like(_render_run(e, units, text, mk.cov.shape, slant_render), mk)
    wc = _deslant(canvas, slant_meas) if abs(slant_meas) > 2.5 else canvas
    vert = _char_vertical(wc, _char_columns(units, text, e.face))
    vf = _vfit(e.face, text, vert, idxs, mk.sigma_edge, P, run_off=i0, run_text=run_text)
    if vf is None:
        return None
    rh = vf[0] / e.size
    cm = canvas.max(axis=0)
    ratios = []
    pad = 0.4 * e.size
    for k, u in enumerate(units):
        if not u.exact or u.b - u.a < 2:
            continue
        lo = u.a - pad if k == 0 else max(u.a - pad, 0.5 * (units[k - 1].b + u.a))
        hi = u.b + pad if k == len(units) - 1 else min(u.b + pad, 0.5 * (u.b + units[k + 1].a))
        j0, j1 = int(max(0, math.floor(lo))), int(min(len(cm), math.ceil(hi)))
        seg = cm[j0:j1]
        l, r = _first_cross(seg), _last_cross(seg)
        if l is not None and r is not None and r > l:
            ratios.append((r - l) / (u.b - u.a))
    rw = float(np.median(ratios)) if ratios else 1.0
    lim = P["bias_max"]
    if not (1 - lim < rh < 1 + lim) or not (1 - lim < rw < 1 + lim):
        return None
    return float(rh), rw


def _calibrate_blur(e: _FaceEval, units, text, mk: _Mask, slant: float, P: dict) -> float:
    if not np.isfinite(e.size) or e.size <= 1:
        return 0.0
    canvas = _render_run(e, units, text, mk.cov.shape, slant)
    x0 = int(max(0, math.floor(units[0].a - 2)))
    x1 = int(min(mk.cov.shape[1], math.ceil(units[-1].b + 2)))
    R, C = canvas[:, x0:x1], mk.cov[:, x0:x1]
    best, best_v = 0.0, -1.0
    for sg in P["blur_search"]:
        Rb = cv2.GaussianBlur(R, (0, 0), sg) if sg > 0 else R
        den = float(np.maximum(Rb, C).sum())
        v = float(np.minimum(Rb, C).sum() / den) if den > 0 else 0.0
        if v > best_v:
            best, best_v = sg, v
    return float(best)


def _redraw_score(e: _FaceEval, units: list[_Unit], text: str, mk: _Mask, slant: float, P: dict) -> float:
    if not np.isfinite(e.size) or e.size <= 1:
        return 0.0
    canvas = _blur_like(_render_run(e, units, text, mk.cov.shape, slant), mk)
    x0 = int(max(0, math.floor(units[0].a - 2)))
    x1 = int(min(mk.cov.shape[1], math.ceil(units[-1].b + 2)))
    R = canvas[:, x0:x1]
    C = mk.cov[:, x0:x1]
    for r in mk.rules:
        R = np.where(r["removed"][:, x0:x1], 0.0, R)
    return _best_iou(R, C, mk)


def _best_iou(R: np.ndarray, C: np.ndarray, mk: _Mask) -> float:
    """軟重疊率 Σmin ÷ Σmax；在小範圍的垂直位移與垂直縮放（以墨跡中線為軸）中取最大值，
    吸收基線與字級的次像素誤差（hinting 讓 x 字高對齊整數像素時特別需要）。"""
    rows = np.flatnonzero(R.max(axis=1) > 0.05)
    if rows.size == 0:
        return 0.0
    yc = 0.5 * (rows[0] + rows[-1] + 1)
    best = 0.0
    step = max(0.25, 0.5 * mk.sigma_edge)
    h, w = R.shape
    for sy in (0.985, 1.0, 1.015):
        for dy in (-2 * step, -step, 0.0, step, 2 * step):
            if sy == 1.0 and dy == 0.0:
                Rw = R
            else:
                m = np.float32([[1, 0, 0], [0, sy, (1 - sy) * (yc - 0.5) + dy]])
                Rw = cv2.warpAffine(R, m, (w, h), flags=cv2.INTER_LINEAR, borderValue=0)
            den = float(np.maximum(Rw, C).sum())
            if den > 0:
                best = max(best, float(np.minimum(Rw, C).sum() / den))
    return best


def _glyph_width_ratio(mk: _Mask, units, text, e: _FaceEval, pitch, P) -> Optional[float]:
    """逐字墨跡寬 ÷（字型墨跡寬 × 高度字級）的中位數；切不出逐字墨跡時回傳 None。"""
    ratios = []
    if pitch and not pitch.get("proportional"):
        for u, pr in pitch["items"]:
            lay = e.face.layout(text[u.i0 : u.i1])
            for k, wm in pr["widths"]:
                box = lay.ink[k]
                if box is not None and box[1] > box[0]:
                    ratios.append(wm / (e.s_h * (box[1] - box[0])))
    else:
        bin_ = mk.binary.astype(np.uint8)
        for u in units:
            if u.cjk or not u.exact:
                continue
            j0, j1 = int(max(0, math.floor(u.a))), int(min(bin_.shape[1], math.ceil(u.b)))
            n, lab, st, _ = cv2.connectedComponentsWithStats(np.ascontiguousarray(bin_[:, j0:j1]), connectivity=8)
            spans = sorted([(st[i][0], st[i][0] + st[i][2]) for i in range(1, n) if st[i][4] > 2])
            merged: list[list[float]] = []
            for a, b in spans:
                if merged and min(b, merged[-1][1]) - max(a, merged[-1][0]) > 0.5 * min(b - a, merged[-1][1] - merged[-1][0]):
                    merged[-1][1] = max(merged[-1][1], b)
                    merged[-1][0] = min(merged[-1][0], a)
                else:
                    merged.append([a, b])
            sub = text[u.i0 : u.i1]
            if len(merged) != len(sub):
                continue
            lay = e.face.layout(sub)
            for (a, b), box in zip(merged, lay.ink):
                if box is not None and box[1] - box[0] > 0.15:
                    ratios.append((b - a) / (e.s_h * (box[1] - box[0])))
    if len(ratios) < 2:
        return None
    return float(np.median(ratios))


def _char_starts(units: list[_Unit], text: str, e: _FaceEval) -> list[float]:
    out = []
    for u in units:
        sub = text[u.i0 : u.i1]
        lay = e.face.layout(sub)
        ib = lay.ink_bounds()
        if ib is None:
            continue
        sc = (u.b - u.a) / (ib[1] - ib[0]) if (u.exact and ib[1] > ib[0]) else e.size
        for j, st in enumerate(lay.starts):
            if not sub[j].isspace():
                out.append(u.a + (st - ib[0]) * sc)
    return out


# ====================================================================== (4) 粗體、顏色、特效


def _bold(mk: _Mask, units, text, best: _FaceEval, fonts: FontLibrary, slant: float, P: dict, notes: list[str], f: float) -> tuple[tuple[bool, float], float]:
    x0 = int(max(0, math.floor(units[0].a - 1)))
    x1 = int(min(mk.cov.shape[1], math.ceil(units[-1].b + 1)))
    sw_m = stroke_width_soft(mk.cov[:, x0:x1])
    if sw_m <= 0:
        return (False, 0.1), 0.0
    fam = fonts.styles_of(best.face.family)
    want_it = best.face.is_italic
    reg = next((x for x in fam if x.style == ("Italic" if want_it else "Regular")), None) or next((x for x in fam if not x.is_bold), None)
    bol = next((x for x in fam if x.style == ("Bold Italic" if want_it else "Bold")), None) or next((x for x in fam if x.is_bold and x.weight <= 800), None)

    def measure(face: Optional[FontFace], emb: float = 0.0) -> tuple[float, float]:
        if face is None and emb <= 0:
            return float("nan"), float("nan")
        canvas = _blur_like(_render_run(best, units, text, mk.cov.shape, slant, emb, face or best.face), mk)
        sub = canvas[:, x0:x1]
        C = mk.cov[:, x0:x1]
        den = float(np.maximum(sub, C).sum())
        return stroke_width_soft(sub), _best_iou(sub, C, mk)

    sw_r, iou_r = measure(reg)
    sw_b, iou_b = measure(bol)
    if not np.isfinite(sw_b) and np.isfinite(sw_r):
        sw_b, iou_b = measure(reg, emb=0.5 * (P["bold_stem_ratio"] - 1) * sw_r)
        if not np.isfinite(sw_b) or sw_b <= sw_r:
            sw_b = sw_r * P["bold_stem_ratio"]
    if not np.isfinite(sw_r) and np.isfinite(sw_b):
        sw_r, iou_r = sw_b / P["bold_stem_ratio"], 0.0
    if not (np.isfinite(sw_r) and np.isfinite(sw_b)) or sw_b <= sw_r:
        is_b = best.face.is_bold
        return (is_b, 0.4), sw_m
    tpos = (sw_m - sw_r) / (sw_b - sw_r)
    ev = (tpos - 0.5) / 0.25
    if np.isfinite(iou_b) and np.isfinite(iou_r) and iou_b > 0 and iou_r > 0:
        ev += (iou_b - iou_r) / P["bold_iou_scale"]
    conf = 0.5 + 0.5 * math.tanh(abs(ev) / 2.0)
    if sw_m / f < P["min_stroke_px"]:
        conf = min(conf, 0.6)
        notes.append("筆畫太細，粗細判斷不可靠")
    return (bool(ev > 0), float(np.clip(conf, 0.05, 0.99))), sw_m


def _colors(img: np.ndarray, geo: _Geom, mk: _Mask, units, sw: float, P: dict):
    """筆畫核心色、信心值、鄰近背景色、特效旗標。"""
    f = geo.f
    x0 = int(max(0, math.floor(units[0].a - 1)))
    x1 = int(min(mk.binary.shape[1], math.ceil(units[-1].b + 1)))
    sub = np.zeros_like(mk.binary)
    sub[:, x0:x1] = mk.binary[:, x0:x1]
    er = int(max(1, round(P["color_erode_frac"] * sw), math.ceil(0.5 * f)))  # 至少內縮半個影像像素（放大過的 mask 要按倍率內縮）
    core = cv2.erode(sub.astype(np.uint8), np.ones((3, 3), np.uint8), iterations=er) > 0
    H, W = img.shape[:2]

    def sample(mask: np.ndarray) -> np.ndarray:
        ys, xs = np.nonzero(mask)
        if ys.size == 0:
            return np.zeros((0, 3))
        pts = _to_image(geo.M, np.column_stack([xs + 0.5, ys + 0.5]))
        ix = np.clip(np.floor(pts[:, 0]).astype(int), 0, W - 1)
        iy = np.clip(np.floor(pts[:, 1]).astype(int), 0, H - 1)
        keys = np.unique(iy * W + ix)
        return img.reshape(-1, 3)[keys].astype(np.float64)

    cp = sample(core)
    conf = 0.95
    alpha = 1.0
    if cp.shape[0] < P["min_core_pixels"] or (sw > 0 and sw / f < P["thin_stroke_px"]):
        sel = sub & (mk.g >= np.percentile(mk.g[sub], P["extreme_percentile"])) if sub.any() else sub
        cp = sample(sel)
        conf = 0.6
        # 筆畫比像素細或被模糊時，最濃的像素仍是筆色與底色的混合：以「線寬 w、模糊 σ 時的峰值覆蓋率」外插
        w_img = sw / f if sw > 0 else 1.0
        sg = max(0.29, mk.blur_extra / f)
        alpha = float(np.clip(math.erf(w_img / (2 * math.sqrt(2) * sg)), 0.3, 1.0))
    if cp.shape[0] == 0:
        color = tuple(int(round(v)) for v in np.clip(mk.ink, 0, 255))
        conf = 0.3
    else:
        med = np.median(cp, axis=0)
        mad = np.median(np.abs(cp - med), axis=0).mean()
        if alpha < 0.95:
            bgc = np.median(sample((cv2.dilate(sub.astype(np.uint8), np.ones((5, 5), np.uint8)) > 0) & ~sub), axis=0) if sub.any() else mk.bg
            med = bgc + (med - bgc) / alpha
            conf *= 0.75
        conf *= float(np.clip(1.0 - mad / 60.0, 0.3, 1.0))
        color = tuple(int(round(v)) for v in np.clip(med, 0, 255))
    ri, ro = max(1, int(round(P["ring_inner_px"] * f))), max(2, int(round(P["ring_outer_px"] * f)))
    d_in = cv2.dilate(sub.astype(np.uint8), np.ones((2 * ri + 1, 2 * ri + 1), np.uint8)) > 0
    d_out = cv2.dilate(sub.astype(np.uint8), np.ones((2 * ro + 1, 2 * ro + 1), np.uint8)) > 0
    ring = d_out & ~d_in
    bp = sample(ring)
    background = tuple(int(round(v)) for v in (np.median(bp, axis=0) if bp.shape[0] else np.clip(mk.bg, 0, 255)))
    # 特效：緊鄰一圈（1 到 2 px）的顏色不在筆色—底色連線上、又與遠處底色不同
    effect = None
    n1 = int(max(1, round(1.0 * f + mk.blur)))
    n2 = int(max(n1 + 1, round(2.5 * f + mk.blur)))
    near = (cv2.dilate(sub.astype(np.uint8), np.ones((2 * n2 + 1, 2 * n2 + 1), np.uint8)) > 0) & ~(cv2.dilate(sub.astype(np.uint8), np.ones((2 * n1 + 1, 2 * n1 + 1), np.uint8)) > 0)
    npx = sample(near)
    if npx.shape[0] > 20 and bp.shape[0] > 20:
        nc = np.median(npx, axis=0)
        bc = np.median(bp, axis=0)
        ic = np.array(color, float)
        seg = ic - bc
        tt = float(np.clip((nc - bc) @ seg / max(seg @ seg, 1e-6), 0, 1))
        off = float(np.linalg.norm(nc - (bc + tt * seg)))
        if np.linalg.norm(nc - bc) > P["effect_delta"] and off > P["effect_offline"]:
            effect = "有外框、陰影或光暈等特效（筆畫外圍有第三種顏色），量測不可靠"
            conf *= 0.6
    return color, float(np.clip(conf, 0.05, 0.99)), background, effect


# ====================================================================== 分段


def _script_groups(units: list[_Unit], text: str, P: dict) -> list[list[int]]:
    """依文字類別（全形／拉丁）分組；太短的段（例如單一標點、項目符號）併入相鄰的段。"""
    if not units:
        return []
    groups: list[list[int]] = [[0]]
    for k in range(1, len(units)):
        if units[k].cjk == units[groups[-1][-1]].cjk:
            groups[-1].append(k)
        else:
            groups.append([k])

    def weight(g: list[int]) -> int:
        n = 0
        for k in g:
            u = units[k]
            n += sum(1 for c in text[u.i0 : u.i1] if c.isalnum() or is_cjk_char(c) and not _is_punct(c, P))
        return n

    changed = True
    while changed and len(groups) > 1:
        changed = False
        for gi, g in enumerate(groups):
            u = units[g[0]]
            need = P["run_min_cjk"] if u.cjk else P["run_min_latin"]
            if weight(g) < need:
                if gi == 0:
                    groups[1] = g + groups[1]
                else:
                    groups[gi - 1] = groups[gi - 1] + g
                del groups[gi]
                changed = True
                break
    # 合併後同一組的單位要依序
    return [sorted(g) for g in groups]


def _split_by_attributes(img, geo: _Geom, mk: _Mask, wcov, units: list[_Unit], text: str, ref, P: dict) -> list[list[_Unit]]:
    """對單位序列（拉丁的詞、中文逐字）的顏色與高度做二元切分改變點偵測。"""
    elems: list[_Unit] = []
    for u in units:
        if u.cjk and u.i1 - u.i0 > 1:
            step = (u.b - u.a) / (u.i1 - u.i0)
            for k in range(u.i0, u.i1):
                elems.append(_Unit(k, k + 1, True, u.a + (k - u.i0) * step, u.a + (k - u.i0 + 1) * step, u.exact))
        else:
            elems.append(u)
    if len(elems) < 2:
        return [units]
    feats = []
    H, W = img.shape[:2]
    for el in elems:
        j0, j1 = int(max(0, math.floor(el.a))), int(min(mk.binary.shape[1], math.ceil(el.b)))
        m = np.zeros_like(mk.binary)
        m[:, j0:j1] = mk.binary[:, j0:j1]
        core = cv2.erode(m.astype(np.uint8), np.ones((3, 3), np.uint8)) > 0
        use = core if core.sum() >= 5 else m
        ys, xs = np.nonzero(use)
        if ys.size == 0:
            feats.append(None)
            continue
        pts = _to_image(geo.M, np.column_stack([xs + 0.5, ys + 0.5]))
        ix = np.clip(np.floor(pts[:, 0]).astype(int), 0, W - 1)
        iy = np.clip(np.floor(pts[:, 1]).astype(int), 0, H - 1)
        col = np.median(img[iy, ix].astype(float), axis=0)
        rows = np.flatnonzero(m.any(axis=1))
        hgt = float(rows.max() - rows.min() + 1)
        feats.append((col, hgt))
    valid = [f for f in feats if f is not None]
    if len(valid) < 2:
        return [units]
    hmed = float(np.median([f[1] for f in valid]))
    Z = []
    for f in feats:
        if f is None:
            Z.append(None)
        else:
            Z.append(np.concatenate([f[0] / P["run_color_scale"], [math.log(max(f[1], 1) / hmed) / P["run_size_scale"] * 0.5]]))
    # 高度特徵只在字的高矮差很大時才有意義（同字級的字本身就高矮不一），所以權重減半
    cuts = _binseg(Z, elems, text, P)
    if not cuts:
        return [units]
    # 把切點（元素索引）換回原單位；中文單位可能被切開
    out: list[list[_Unit]] = []
    bounds = [0] + cuts + [len(elems)]
    for a, b in zip(bounds[:-1], bounds[1:]):
        seg = elems[a:b]
        merged: list[_Unit] = []
        for el in seg:
            if merged and el.cjk and merged[-1].cjk and merged[-1].i1 == el.i0:
                merged[-1] = _Unit(merged[-1].i0, el.i1, True, merged[-1].a, el.b, merged[-1].exact and el.exact)
            else:
                merged.append(_Unit(el.i0, el.i1, el.cjk, el.a, el.b, el.exact))
        out.append(merged)
    # 被切開的中文單位，切口的邊緣不是由空隙定出
    return out


def _binseg(Z: list, elems: list[_Unit], text: str, P: dict) -> list[int]:
    idx = [k for k, z in enumerate(Z) if z is not None]
    if len(idx) < 2:
        return []
    X = np.array([Z[k] for k in idx])

    def count(a: int, b: int) -> int:
        n = 0
        for k in range(a, b):
            el = elems[idx[k]]
            n += sum(1 for c in text[el.i0 : el.i1] if c.isalnum() or is_cjk_char(c))
        return n

    def need(a: int, b: int) -> int:
        cj = all(elems[idx[k]].cjk for k in range(a, b))
        return P["run_min_cjk"] if cj else P["run_min_latin"]

    def cost(a: int, b: int) -> float:
        seg = X[a:b]
        return float(((seg - seg.mean(axis=0)) ** 2).sum()) if b - a > 0 else 0.0

    cuts: list[int] = []

    def rec(a: int, b: int) -> None:
        if b - a < 2:
            return
        base = cost(a, b)
        best, best_g = None, 0.0
        for c in range(a + 1, b):
            if count(a, c) < need(a, c) or count(c, b) < need(c, b):
                continue
            gain = base - cost(a, c) - cost(c, b)
            if gain > best_g:
                best, best_g = c, gain
        if best is not None and best_g > P["run_split_penalty"]:
            cuts.append(idx[best])
            rec(a, best)
            rec(best, b)

    rec(0, len(idx))
    return sorted(cuts)


# ====================================================================== 退路


def _fallback_run(units, text, mk: _Mask, geo: _Geom, img, P: dict, why: list[str]) -> dict:
    f = geo.f
    em = geo.em0
    color, cc, bg, eff = _colors(img, geo, mk, units, 2.0, P)
    run = RunAttributes(
        start=units[0].i0, end=units[-1].i1, color=color, color_confidence=cc, background=bg,
        font_size_px=Estimate(float(em / f), float(0.25 * em / f), 0.1), fonts=[], bold=(False, 0.1), italic=(False, 0.1),
        notes=why + ["字級只由行框高度粗估，量測不可靠"],
    )
    fake = _FaceEval(face=None)  # type: ignore[arg-type]
    fake.base_b = geo.band[1] - 0.2 * em
    fake.size = em
    return {"run": run, "eval": fake, "n_chars": units[-1].i1 - units[0].i0, "asc_px": 0.9 * em, "desc_px": -0.2 * em, "char_x_m": [], "line_notes": [], "base_err": 0.2 * em}


def _empty_result(line: OcrLine, geo: _Geom, mk: _Mask, notes: list[str]) -> LineAttributes:
    q = np.asarray(line.quad, float).reshape(4, 2)
    bl = np.array([q[3], q[2]])
    run = RunAttributes(
        start=0, end=len(line.text or ""), color=tuple(int(v) for v in np.clip(mk.ink, 0, 255)), color_confidence=0.1,
        background=tuple(int(v) for v in np.clip(mk.bg, 0, 255)),
        font_size_px=Estimate(float(geo.em0 / geo.f), float(0.3 * geo.em0 / geo.f), 0.05), fonts=[], bold=(False, 0.05), italic=(False, 0.05),
        notes=["量測不可靠"],
    )
    return LineAttributes(quad=q.copy(), baseline=bl, runs=[run], char_x=[], mask=mk.binary.copy(), mask_to_image=geo.M.copy(), notes=notes, polarity=mk.polarity)
