"""文字偵測與文字辨識（③辨識，影像產線；設計見 docs/architecture/03-recognize.md 3B.1、3B.2）。

    engine = OcrEngine()               # 載入模型一次，可重複使用
    lines = engine.read(image_rgb)     # 工作影像（RGB uint8）→ list[OcrLine]

只做量測與感知，不呼叫任何大型語言模型；不替候選定案（候選字全部交給⑤）。

做法（每一步用的演算法）
------------------------
1. **偵測**：PP-OCRv6 small 偵測模型（DB，可微分二值化），以 onnxruntime 執行。
   - 影像長邊不超過 det_tile_threshold：整張一次（短邊過小時先放大到 det_min_side）。
   - 更大的影像：原解析度切成互相重疊的方塊各跑一次，另外整頁縮小再跑一次抓大標題；
     不同方塊、不同尺度找到的框（以及同一次偵測中互相重疊的大字碎片），
     若共線且沿行方向重疊，就以聯集合併（union-find）。辨識之後，同一基線上間隙小於
     line_merge_gap_em 的兩行再合併一次（偵測模型常在中英之間的大字距處斷行）。
   - DB 後處理自己寫（機率圖二值化 → 輪廓 → 最小外接矩形 → 依 DB 論文公式外擴），
     不依賴 pyclipper／shapely。
2. **方向**：框高明顯大於框寬時，同時試 0、90、270 度三種讀法，取平均字機率最高者
   （旋轉讀法要勝出 rotate_margin 才採用）。橫向的框用 PP-OCR 方向分類模型檢查是否
   上下顛倒，分類器有把握時再試讀翻轉版本，仍以辨識機率決定。直排中文不做，vertical 一律 False。
3. **攤平與墨跡**：沿框的方向用仿射轉換把行「攤平」（小字放大到 flat_min_height 以求
   次像素字界）。背景色取框內色彩直方圖的眾數，再以平面擬合修正光照不均；墨跡強度 =
   與背景色的 RGB 距離，以 Otsu 門檻分出墨跡後正規化成 0 到 1 的「軟墨跡」。
   與極性無關，所以深色底淺色字、彩色字都一樣處理。
4. **角度精修**：在攤平影像上，以「水平投影最尖銳」（各角度投影的平方和最大）搜尋
   ±skew_search_deg 內的角度，拋物線內插求次步長精度；行太短（長寬比低於
   skew_min_aspect）時角度不可靠，改用整頁長行角度的加權中位數。
5. **行界精修**：以新角度重新攤平；水平投影找核心帶，連通塊與核心帶重疊且中心在偵測框
   左右範圍內者屬於本行，再併入 i 的點、引號等小塊；墨跡範圍就是貼合文字的旋轉矩形
   （OcrLine.quad，左右上下都取墨跡極值）。其他行伸進來的筆畫在辨識前塗成背景色。
6. **辨識**：PP-OCRv6 small 辨識模型（SVTR + CTC），輸入高 48。淺色字先反相（可關）。
   寬高比超過 rec_max_aspect 的長行在墨跡谷底切段分別辨識。
7. **CTC 解碼（自己寫）**：拿到模型輸出的機率矩陣（時間步 × 字典），逐步取第一名、
   合併重複、去掉 blank；每個字保留它所佔時間步內各類別的最大機率，取前 cand_topk 名
   當候選（含第一名；也含 ""，代表「這裡可能根本沒有字」）。
8. **逐字位置**：CTC 時間步推出每個字的中心，相鄰中心的中點是字界初值；在攤平行影像的
   墨跡直行投影上，於初值附近找零墨跡段（多個時取「寬且近初值」者），沒有零段時取
   投影最低點（谷底）；每個字的 x0、x1 再收緊到格子內的墨跡範圍。
9. **空白**：以量到的墨跡間隙（換算成 em，em 由行高與字的種類估計）決定補入或刪除空白；
   全形標點本身帶留白，門檻另計。補入的空白依間隙超出門檻的幅度給信心值。
10. **濾誤判**：筆畫寬 ÷ 行高（距離轉換的脊線中位數）、墨跡填滿率、字數與行長比例三個
    線索合成「像文字的程度」；分數極低，或「不像文字且分數不夠高」才刪，其餘保留但降信心。
11. **誤差範圍**：字界誤差由取樣間距、邊緣模糊寬度、是否相黏推出（OcrChar.x_error）；
    四角誤差另加角度誤差造成的端點位移（OcrLine.quad_error）；角度誤差取投影銳度曲線
    的峰寬（OcrLine.angle_error_deg）。

已知限制
--------
- 直排中文不支援（vertical 一律 False，直排的行會被當成旋轉 90 度的橫排去讀，分數通常很低）。
- 彎曲的行、透視變形嚴重的行：只交旋轉矩形，不交曲線。
- 文字壓在照片或複雜紋理上時，背景色估計不準，行界與字界誤差大（會反映在信心值上）。
- 斜體字的字界：直行投影對斜體會相黏，字界取谷底，誤差較大（x_error 會變大）。
- CTC 的「漏讀」（某時間步 blank 第一、某字第二）目前不表達成候選，只有「多讀」用 "" 候選表達。
- 1 到 2 個字的短行，角度沿用整頁主要角度；整頁只有短行時沿用偵測框角度。
- 空白判斷依賴字界：模型漏掉空白、而且字界初值被拉偏時，仍可能漏空白（實測 doc_a4 的
  「上半年 H1」讀成「上半年H1」）；模糊影像的空白錯誤率明顯升高（見測試數字）。
- 全形標點常被讀成半形（「（萬元）」→「(萬元)」），候選擴充表（繁簡、全半形）屬⑤之前的
  後續層級，這裡不做；簡繁混淆（「參」→「参」、「輸」→「输」）同理，只留在候選清單中。
- 墨跡四角與「字型上緣到下緣」的行框本質不同：極短的行（「致。」、頁碼「1」）與 PDF 行框的
  IoU 可能低於 0.5，評測時會被算成漏偵測。
- 低解析度（墨跡行高低於約 8 像素）時，模型會在字間多讀空白，字界量測也不夠準去刪掉它們；
  這些行會標「低解析度」並降信心。

可單獨升級的點
--------------
- 偵測模型、辨識模型：換 ONNX 路徑即可（det_model_path、rec_model_path），例如 PP-OCRv6 medium、
  PP-OCRv5_server_rec；解碼與字界程式共用。模型字典自動從 ONNX metadata 讀取。
- 【替換點：方向】_orientation_candidates 與 _cls_upside_down：可換成文字方向小模型。
- 【替換點：誤判】_text_likeness：可換成「是不是文字」的小型分類模型。
- 【替換點：字界】_char_cells：可換成以字形重繪比對（3B.5 求出字型後）求字界。
- 解碼可改成保留多條整行讀法（beam search），候選更完整。
"""

from __future__ import annotations

import copy
import importlib.util
import math
import time
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import cv2
import numpy as np
import onnxruntime as ort

from .types import CharCandidate, OcrChar, OcrLine

# ---------------------------------------------------------------------------- 參數

DEFAULT_PARAMS: dict = {
    # ---- 模型
    "det_model_path": None,  # 偵測模型 ONNX 路徑；None 表示用 rapidocr 內附的 PP-OCRv6 small
    "rec_model_path": None,  # 辨識模型 ONNX 路徑；None 表示用 rapidocr 內附的 PP-OCRv6 small
    "cls_model_path": None,  # 方向分類模型 ONNX 路徑；None 表示用 rapidocr 內附的 mobile v2.0
    "num_threads": 0,  # onnxruntime 執行緒數（個）；0 表示由 onnxruntime 自行決定
    "ort_allow_spinning": False,  # onnxruntime 執行緒空等時是否自旋；共用機器上關掉可省約 25% CPU，專屬伺服器可開
    # ---- 偵測：尺度與切塊
    "det_min_side": 736,  # 影像短邊小於此值（像素）時先放大再偵測（PP-OCR 慣例）
    "det_max_upscale": 2.0,  # 偵測前放大倍率上限（倍）
    "det_tile_threshold": 2560,  # 影像長邊超過此值（像素）就改成切塊偵測，另加一次整頁縮小到此長邊的偵測
    "det_tile_size": 1280,  # 切塊邊長（像素，原解析度）
    "det_tile_overlap": 0.2,  # 相鄰切塊的重疊比例（0 到 1）
    # ---- 偵測：DB 後處理
    "det_thresh": 0.3,  # 機率圖二值化門檻（0 到 1）
    "det_box_thresh": 0.5,  # 框內平均機率低於此值就不要這個框（0 到 1）
    "det_unclip_ratio": 1.6,  # 框外擴係數（DB 論文的 r；外擴距離 = 面積 × r ÷ 周長）
    "det_min_box_side": 3.0,  # 框短邊小於此值（機率圖像素）就丟掉
    "det_max_candidates": 1000,  # 每次偵測最多處理幾個輪廓（個）
    "det_merge_angle_deg": 6.0,  # 不同尺度或切塊的框，長軸夾角在此之內（度）才可能是同一行
    "det_merge_overlap": 0.5,  # 兩框在垂直行方向的重疊 ÷ 較矮框高，至少此值才視為同一行
    "det_merge_height_ratio": 1.8,  # 兩框高度比上限（倍）
    # ---- 方向
    "tall_box_ratio": 1.4,  # 框高 ÷ 框寬超過此值，就另外試 90 度與 270 度的讀法
    "rotate_margin": 0.03,  # 旋轉讀法的平均字機率要比原方向高出此值才採用（0 到 1）
    "use_cls": True,  # 是否用方向分類模型檢查上下顛倒（180 度）的行
    "cls_thresh": 0.9,  # 分類模型判為 180 度的機率高於此值，才另外試讀翻轉的行（0 到 1）
    # ---- 攤平與墨跡
    "flat_min_height": 40.0,  # 偵測框高低於此值（像素）時，攤平影像放大到此高度，以求次像素字界
    "flat_max_scale": 4.0,  # 攤平放大倍率上限（倍）
    "flat_margin": 0.3,  # 攤平時在偵測框四周多取的範圍（× 偵測框高）
    "bg_quant_bits": 4,  # 估背景色眾數時每個色版保留幾個位元（位元）
    "bg_plane_fit": True,  # 是否以平面擬合背景色（處理光照不均）
    "ink_text_pct": 90.0,  # 墨跡像素色差的第幾百分位當「文字色差」（%），用來正規化軟墨跡
    "ink_noise_pct": 90.0,  # 背景像素色差的第幾百分位當雜訊底（%）
    "min_contrast": 30.0,  # 文字與背景色差（RGB 歐氏距離，0 到 441）低於此值視為對比不足並降信心
    "skew_search_deg": 4.0,  # 角度精修的搜尋範圍（± 度，相對偵測框角度）
    "skew_step_deg": 0.2,  # 角度精修的搜尋步長（度）
    "skew_min_aspect": 3.0,  # 行長 ÷ 行高低於此值就不自己估角度，改用整頁長行的主要角度
    "skew_max_points": 20000,  # 估角度時最多取樣幾個墨跡像素（個）
    "core_band_q": 0.25,  # 核心帶 = 偵測框內墨跡列分布的第 q 到第 1-q 分位（0 到 0.5）
    "cc_min_area_frac": 0.0015,  # 連通塊面積 ÷ 核心帶高度平方低於此值視為雜點
    "cc_x_tol": 0.1,  # 連通塊中心可超出偵測框左右的距離（× 偵測框高）
    "cc_max_height": 1.3,  # 連通塊高 ÷ 偵測框高超過此值視為框線或圖形，不屬於本行（倍）
    "cc_attach_above": 0.35,  # 不碰核心帶的小塊，中心在行上緣以上此距離內（× 墨跡行高）就併入（i 的點、引號）
    "cc_attach_max_width": 1.5,  # 併入的小塊寬度上限（× 墨跡行高）：更寬的是文字上下的橫線或底線，不是 i 的點或引號；併進來會填滿字間空隙，空白就判斷不出來
    "clean_dilate_px": 2,  # 塗掉其他行伸進來的筆畫時外擴的像素（攤平影像像素）
    # ---- 辨識
    "rec_height": 48,  # 辨識模型輸入高（像素；PP-OCR 固定 48）
    "rec_min_width": 320,  # 辨識輸入至少補到這個寬（像素；PP-OCR 慣例）
    "rec_batch": 8,  # 每批幾行（行）
    "rec_pad_v": 0.25,  # 辨識裁切時上下留白（× 墨跡行高）
    "rec_pad_h": 0.35,  # 辨識裁切時左右留白（× 墨跡行高）
    "rec_normalize_polarity": True,  # 淺色字深色底時先反相成深色字再辨識
    "rec_max_aspect": 40.0,  # 裁切寬高比超過此值就切成數段分別辨識
    "rec_chunk_aspect": 25.0,  # 切段時每段的目標寬高比
    "cand_topk": 5,  # 每個字保留幾個候選（個，含第一名）
    "cand_min_prob": 0.001,  # 候選機率低於此值就不列（0 到 1）
    # ---- 逐字位置
    "boundary_ctc_weight": 0.5,  # 字界初值中 CTC 時間步推算所佔的權重（其餘來自字寬表；0 到 1）
    "boundary_sigma": 0.3,  # 字界初值的不確定度（× 預期字距）；切點離初值一個 sigma 的代價為 1
    "boundary_depth_weight": 3.0,  # 相黏處（沒有零墨跡段）切點的代價權重（× 谷底墨跡量 ÷ 峰值）
    "boundary_touch_penalty": 1.5,
    "boundary_gap_bonus": 1.5,  # 零墨跡段的獎勵（寬度達 0.3 em 時給滿；字間大空隙一定是字界，即使模型漏了空白）  # 選相黏處而不選零墨跡段的固定代價（避免切進 m、a、s 等字母內部的谷底）
    # ---- 空白（單位都是 em；em 由墨跡行高 ÷ em_ratio 估計）
    "em_ratio": {  # 墨跡行高 ÷ em，依行內字的種類（無單位）
        "cjk": 0.90,  # 有中日韓字
        "cjk_desc": 1.0,  # 有中日韓字也有下伸字母
        "tall_desc": 0.93,  # 有大寫或上伸字母，也有下伸字母
        "tall": 0.72,  # 只有大寫、數字或上伸字母
        "x_desc": 0.73,  # 只有小寫，且有下伸字母
        "x": 0.52,  # 只有 x 字高的小寫字母
    },
    "space_insert_em": 0.21,  # 拉丁字之間（或中英之間）間隙超過此值就補空白（em）
    "space_remove_em": 0.10,  # 模型給的空白，間隙小於此值就刪掉（em）
    "space_cjk_insert_em": 0.60,  # 兩個中日韓字之間間隙超過此值才補空白（em）
    "space_cjk_remove_em": 0.30,  # 兩個中日韓字之間的空白，間隙小於此值就刪（em）
    "space_fw_insert_em": 1.15,  # 鄰接全形標點時，間隙超過此值才補空白（全形標點本身帶留白，實測無空白時可達 0.92；em）
    "space_fw_remove_em": 1.0,  # 鄰接全形標點的空白，間隙小於此值就刪（em）
    "space_soft_em": 0.05,  # 補入空白的信心值斜率：間隙超出門檻此值時信心約 0.73（em）
    # ---- 濾誤判
    "drop_score": 0.3,  # 平均字機率低於此值整行刪除（0 到 1）
    "stroke_ok": 0.35,  # 筆畫寬 ÷ 行高在此之下算像文字
    "stroke_bad": 0.6,  # 筆畫寬 ÷ 行高在此之上算完全不像文字（實心形狀）
    "fill_ok": 0.62,  # 墨跡填滿率在此之下算像文字
    "fill_bad": 0.88,  # 墨跡填滿率在此之上算完全不像文字
    "len_ratio_min": 0.25,  # 行長 ÷ 依字數推估的行長，低於此值不像文字
    "len_ratio_max": 4.0,  # 行長 ÷ 依字數推估的行長，高於此值不像文字
    "drop_short_max_chars": 2,  # 字數不超過此值的短行……
    "drop_short_score": 0.7,  # ……平均字機率低於此值就刪（圖片紋理常被讀成單一字母）
    "line_merge_gap_em": 1.0,  # 同一基線上的兩行，間隙小於此值（em）就合併成一行（偵測模型常在大字距處斷行）
    "line_merge_overlap": 0.6,  # 合併時兩行在垂直方向的重疊至少要有較矮者高度的此比例
    "line_merge_em_ratio": 1.4,  # 合併時兩行估計 em 的比例上限（倍）
    "drop_textlike": 0.35,  # 「像文字的程度」低於此值……
    "drop_textlike_keep_score": 0.97,  # ……且平均字機率低於此值，才刪除整行
    "blob_chars": "•·●■▪◆-—–_|一.。、，,:;：；'\"`~～",  # 本身就是實心小塊的字：只有這些字的行不做形狀檢查
    "dup_iou": 0.5,  # 兩行四角的 IoU 超過此值視為重複，只留分數高的
    # ---- 誤差與信心
    "low_res_px": 10.0,  # 墨跡行高低於此值（像素）時標低解析度並降信心
    "low_res_factor": 0.7,  # 低解析度時信心值乘上此係數
    "touch_error_frac": 0.12,  # 相黏字界（沒有零墨跡段）的誤差（× 字距）
}


def _merge_params(params: Optional[dict], base: Optional[dict] = None) -> dict:
    p = copy.deepcopy(base if base is not None else DEFAULT_PARAMS)
    for k, v in (params or {}).items():
        if isinstance(v, dict) and isinstance(p.get(k), dict):
            p[k] = {**p[k], **v}
        else:
            p[k] = v
    return p


# ---------------------------------------------------------------------------- 字元分類


def _is_cjk(ch: str) -> bool:
    o = ord(ch)
    return (
        0x4E00 <= o <= 0x9FFF
        or 0x3400 <= o <= 0x4DBF
        or 0x20000 <= o <= 0x2FFFF
        or 0xF900 <= o <= 0xFAFF
        or 0x3040 <= o <= 0x30FF
        or 0xAC00 <= o <= 0xD7AF
        or 0x3100 <= o <= 0x312F
    )


def _is_fullwidth_punct(ch: str) -> bool:
    if _is_cjk(ch):
        return False
    return unicodedata.east_asian_width(ch) in ("F", "W") and unicodedata.category(ch)[0] in "PS"


def _is_wide(ch: str) -> bool:
    return unicodedata.east_asian_width(ch) in ("F", "W")


_TALL = set("ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789bdfhiklt!?/\\()[]{}|%$#&@'\"")
_DESC = set("gjpqyQ()[]{}|")


# 比例字的前進寬度（em），取自常見無襯線字（Helvetica 系）的字寬，只當字界初值用
_ADV = dict(zip("abcdefghijklmnopqrstuvwxyz", [0.556, 0.556, 0.5, 0.556, 0.556, 0.278, 0.556, 0.556, 0.222, 0.222, 0.5, 0.222, 0.833, 0.556, 0.556, 0.556, 0.556, 0.333, 0.5, 0.278, 0.556, 0.5, 0.722, 0.5, 0.5, 0.5]))
_ADV.update(zip("ABCDEFGHIJKLMNOPQRSTUVWXYZ", [0.667, 0.667, 0.722, 0.722, 0.667, 0.611, 0.778, 0.722, 0.278, 0.5, 0.667, 0.556, 0.833, 0.722, 0.778, 0.667, 0.778, 0.722, 0.667, 0.611, 0.722, 0.667, 0.944, 0.667, 0.667, 0.611]))
_ADV.update({c: 0.556 for c in "0123456789$#?_"})
_ADV.update({c: 0.278 for c in ".,:;!/\\|[]'`"})
_ADV.update({"(": 0.333, ")": 0.333, "-": 0.333, "%": 0.889, "&": 0.667, "@": 1.015, '"': 0.355, "+": 0.584, "=": 0.584, "*": 0.389, "–": 0.556, "—": 1.0, "{": 0.334, "}": 0.334, "<": 0.584, ">": 0.584, "~": 0.584})


def _advance(ch: str) -> float:
    if ch.isspace():
        return 0.278
    if _is_wide(ch):
        return 1.0
    return _ADV.get(ch, 0.556)


def _em_from_height(text: str, h: float, p: dict) -> float:
    """由墨跡行高與行內字的種類估計 em（只供空白判斷與誤判檢查用，不是字級量測）。"""
    r = p["em_ratio"]
    chars = [c for c in text if not c.isspace()]
    has_cjk = any(_is_cjk(c) for c in chars)
    has_desc = any(c in _DESC for c in chars)
    has_tall = any(c in _TALL for c in chars)
    if has_cjk:
        ratio = r["cjk_desc"] if has_desc else r["cjk"]
    elif has_tall:
        ratio = r["tall_desc"] if has_desc else r["tall"]
    elif any(c.isalpha() for c in chars):
        ratio = r["x_desc"] if has_desc else r["x"]
    else:
        ratio = r["tall"]
    return max(h / ratio, 1.0)


# ---------------------------------------------------------------------------- 小工具


def _model_dir() -> Path:
    spec = importlib.util.find_spec("rapidocr")
    if spec is None or spec.origin is None:
        raise RuntimeError("找不到 rapidocr 套件，無法取得內附的 PP-OCR 模型")
    return Path(spec.origin).parent / "models"


def _session(path: Path, p: dict) -> ort.InferenceSession:
    so = ort.SessionOptions()
    if p["num_threads"] and p["num_threads"] > 0:
        so.intra_op_num_threads = int(p["num_threads"])
    so.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL
    so.add_session_config_entry("session.intra_op.allow_spinning", "1" if p["ort_allow_spinning"] else "0")
    return ort.InferenceSession(str(path), sess_options=so, providers=["CPUExecutionProvider"])


def _otsu(values: np.ndarray) -> float:
    v = values.ravel().astype(np.float64)
    if v.size == 0:
        return 0.0
    vmax = float(v.max())
    if vmax <= 0:
        return 0.0
    hist, edges = np.histogram(v, bins=256, range=(0.0, vmax))
    centers = 0.5 * (edges[:-1] + edges[1:])
    w0 = np.cumsum(hist).astype(np.float64)
    w1 = w0[-1] - w0
    s0 = np.cumsum(hist * centers)
    m0 = s0 / np.maximum(w0, 1e-9)
    m1 = (s0[-1] - s0) / np.maximum(w1, 1e-9)
    between = w0 * w1 * (m0 - m1) ** 2
    return float(centers[int(np.argmax(between))])


def _runs(flags: np.ndarray) -> list[tuple[int, int]]:
    """布林陣列中連續 True 的區段 [a, b)。"""
    if flags.size == 0:
        return []
    f = np.concatenate([[False], flags.astype(bool), [False]])
    d = np.diff(f.astype(np.int8))
    starts = np.nonzero(d == 1)[0]
    ends = np.nonzero(d == -1)[0]
    return list(zip(starts.tolist(), ends.tolist()))


def _quad_iou(a: np.ndarray, b: np.ndarray) -> tuple[float, float]:
    """兩個凸四邊形的 IoU 與「交集 ÷ 較小者面積」。"""
    a32 = a.astype(np.float32)
    b32 = b.astype(np.float32)
    aa = abs(cv2.contourArea(a32))
    ab = abs(cv2.contourArea(b32))
    if aa <= 0 or ab <= 0:
        return 0.0, 0.0
    inter, _ = cv2.intersectConvexConvex(a32, b32)
    inter = max(float(inter), 0.0)
    return inter / (aa + ab - inter + 1e-9), inter / (min(aa, ab) + 1e-9)


def _rect_frame(pts: np.ndarray) -> tuple[np.ndarray, np.ndarray, float, float]:
    """四點 → （中心、最接近水平的邊方向單位向量 e（x 分量為正）、沿 e 的長、垂直 e 的長）。"""
    rect = cv2.minAreaRect(pts.astype(np.float32))
    bp = cv2.boxPoints(rect).astype(np.float64)
    c = bp.mean(axis=0)
    e1 = bp[1] - bp[0]
    e2 = bp[2] - bp[1]
    l1 = float(np.linalg.norm(e1))
    l2 = float(np.linalg.norm(e2))
    if l1 < 1e-9 or l2 < 1e-9:
        return c, np.array([1.0, 0.0]), max(l1, l2, 1.0), max(min(l1, l2), 1.0)
    u1, u2 = e1 / l1, e2 / l2
    if abs(u1[0]) >= abs(u2[0]):
        e, lu, lv = u1, l1, l2
    else:
        e, lu, lv = u2, l2, l1
    if e[0] < 0:
        e = -e
    return c, e, lu, lv


def _rot90cw(e: np.ndarray) -> np.ndarray:
    return np.array([-e[1], e[0]])


def _angle_deg(u: np.ndarray) -> float:
    return math.degrees(math.atan2(u[1], u[0]))


def _norm45(a: float) -> float:
    """角度正規化到 (-45, 45]。"""
    a = (a + 45.0) % 90.0 - 45.0
    return 45.0 if a == -45.0 else a


# ---------------------------------------------------------------------------- 內部資料


@dataclass
class _DetBox:
    pts: np.ndarray  # (4, 2) 影像連續座標
    score: float
    pass_id: int = 0


@dataclass
class _Chunk:
    image: np.ndarray  # 辨識輸入（BGR，高 rec_height）
    x0: float  # 這段裁切左緣在攤平影像中的位置（攤平像素）
    fx: float  # 攤平像素 → 辨識輸入像素的倍率
    probs: Optional[np.ndarray] = None  # (T, C)
    stride: float = 8.0  # 每個時間步對應的辨識輸入寬（像素）


@dataclass
class _Cand:
    """一個偵測框在某個讀法方向下的處理結果。"""

    box: int
    rot: int
    center: np.ndarray
    u: np.ndarray  # 閱讀方向（單位向量，影像座標）
    v: np.ndarray  # 文字「向下」方向
    lu: float  # 偵測框沿 u 的長
    lv: float  # 偵測框沿 v 的長
    k: float = 1.0  # 攤平影像倍率（攤平像素 ÷ 影像像素）
    delta: Optional[float] = None  # 角度修正（度）
    angle_err: float = 0.5  # 角度誤差（度）
    origin: Optional[np.ndarray] = None  # 攤平座標 (0, 0) 在影像中的位置
    flat: Optional[np.ndarray] = None
    soft: Optional[np.ndarray] = None
    kept: Optional[np.ndarray] = None  # 本行墨跡遮罩（bool）
    bounds: tuple[int, int, int, int] = (0, 0, 0, 0)  # 墨跡範圍 left, top, right, bottom（攤平像素，右下不含）
    region: tuple[int, int, int, int] = (0, 0, 0, 0)  # 偵測框在攤平影像中的範圍 r0, r1, c0, c1
    bg: np.ndarray = field(default_factory=lambda: np.zeros(3))
    fg: np.ndarray = field(default_factory=lambda: np.zeros(3))
    contrast: float = 0.0
    blur_px: float = 0.0  # 邊緣模糊寬度（影像像素）
    chunks: list[_Chunk] = field(default_factory=list)
    quality: float = 0.0
    notes: list[str] = field(default_factory=list)
    ok: bool = False


# ---------------------------------------------------------------------------- 主類別


class OcrEngine:
    """文字偵測與辨識。模型在建構時載入一次，read() 可重複呼叫。"""

    def __init__(self, params: dict | None = None):
        self.params = _merge_params(params)
        p = self.params
        mdir = _model_dir()
        self._det = _session(Path(p["det_model_path"] or mdir / "PP-OCRv6_det_small.onnx"), p)
        self._rec = _session(Path(p["rec_model_path"] or mdir / "PP-OCRv6_rec_small.onnx"), p)
        cls_path = Path(p["cls_model_path"] or mdir / "ch_ppocr_mobile_v2.0_cls_mobile.onnx")
        self._cls = _session(cls_path, p) if cls_path.exists() else None
        self._det_in = self._det.get_inputs()[0].name
        self._rec_in = self._rec.get_inputs()[0].name
        self._cls_in = self._cls.get_inputs()[0].name if self._cls is not None else None
        meta = self._rec.get_modelmeta().custom_metadata_map
        if "character" not in meta:
            raise RuntimeError("辨識模型的 metadata 沒有字典（character）")
        # CTC 慣例：第 0 類是 blank，最後一類是空白
        self.charset: list[str] = [""] + meta["character"].splitlines() + [" "]
        self.last_timing: dict[str, float] = {}

    # ------------------------------------------------------------------ 公開介面

    def read(self, image: np.ndarray, params: dict | None = None) -> list[OcrLine]:
        """一張工作影像（RGB uint8）→ 文字行清單（依閱讀順序：上到下、左到右）。"""
        p = _merge_params(params, self.params) if params else self.params
        img = _as_rgb(image)
        t0 = time.perf_counter()
        boxes = self._detect(img[:, :, ::-1], p)
        t1 = time.perf_counter()

        # 1. 每個框建立讀法候選，第一次攤平並估角度
        cands: list[_Cand] = []
        for bi, b in enumerate(boxes):
            for rot in _orientation_candidates(b.pts, p):
                c = _make_cand(bi, rot, b.pts, p)
                _first_pass(img, c, p)
                cands.append(c)
        _apply_page_skew(cands, p)
        # 2. 第二次攤平：行界精修、辨識裁切
        for c in cands:
            _second_pass(img, c, p)
        # 3. 上下顛倒檢查（方向分類模型）
        if p["use_cls"] and self._cls is not None:
            extra = []
            flagged = self._cls_upside_down([c for c in cands if c.ok and c.rot == 0], p)
            for c in flagged:
                f = _make_cand(c.box, 180, boxes[c.box].pts, p)
                _first_pass(img, f, p)
                if f.delta is None:
                    f.delta = c.delta
                _second_pass(img, f, p)
                extra.append(f)
            cands.extend(extra)
        t2 = time.perf_counter()
        # 4. 辨識
        self._recognize([ch for c in cands if c.ok for ch in c.chunks], p)
        t3 = time.perf_counter()
        # 5. 每個框選讀法，解碼、字界、空白、濾誤判
        by_box: dict[int, list[_Cand]] = {}
        for c in cands:
            if c.ok:
                c.quality = _cand_quality(c, self.charset, p)
                by_box.setdefault(c.box, []).append(c)
        lines: list[tuple[OcrLine, float]] = []
        for bi, cs in by_box.items():
            best = _choose_orientation(cs, p)
            line = _finalize(best, boxes[bi], self.charset, p)
            if line is not None:
                lines.append(line)
        lines = _merge_collinear(lines, p)
        out = _dedup([l for l, _ in lines], p)
        out = _reading_order(out)
        t4 = time.perf_counter()
        self.last_timing = {"detect": t1 - t0, "prepare": t2 - t1, "recognize": t3 - t2, "finalize": t4 - t3, "total": t4 - t0}
        return out

    # ------------------------------------------------------------------ 偵測

    def _detect(self, img_bgr: np.ndarray, p: dict) -> list[_DetBox]:
        H, W = img_bgr.shape[:2]
        passes: list[tuple[int, int, int, int, float]] = []
        if max(H, W) <= p["det_tile_threshold"]:
            s = 1.0
            if min(H, W) < p["det_min_side"]:
                s = min(p["det_min_side"] / min(H, W), p["det_max_upscale"])
            passes.append((0, 0, W, H, s))
        else:
            passes.append((0, 0, W, H, p["det_tile_threshold"] / max(H, W)))
            T = int(p["det_tile_size"])
            step = max(int(T * (1.0 - p["det_tile_overlap"])), 32)
            for y0 in _tile_starts(H, T, step):
                for x0 in _tile_starts(W, T, step):
                    passes.append((x0, y0, min(T, W - x0), min(T, H - y0), 1.0))
        boxes: list[_DetBox] = []
        for pid, (x0, y0, w, h, s) in enumerate(passes):
            crop = img_bgr[y0 : y0 + h, x0 : x0 + w]
            rw = max(32, int(round(w * s / 32.0)) * 32)
            rh = max(32, int(round(h * s / 32.0)) * 32)
            interp = cv2.INTER_AREA if (rw < w and rh < h) else cv2.INTER_LINEAR
            inp = cv2.resize(crop, (rw, rh), interpolation=interp)
            x = ((inp.astype(np.float32) / 255.0 - 0.5) / 0.5).transpose(2, 0, 1)[None]
            prob = self._det.run(None, {self._det_in: x})[0][0, 0]
            for pts, score in _db_postprocess(prob, p):
                pts[:, 0] = (pts[:, 0] + 0.5) * (w / rw) + x0
                pts[:, 1] = (pts[:, 1] + 0.5) * (h / rh) + y0
                boxes.append(_DetBox(pts, score, pid))
        # 不同切塊、不同尺度的同一行要合併；同一次偵測中互相重疊的共線框（大字的斷行碎片）也合併
        return _merge_det_boxes(boxes, p)

    # ------------------------------------------------------------------ 方向分類

    def _cls_upside_down(self, cands: list[_Cand], p: dict) -> list[_Cand]:
        """【替換點：方向】用 PP-OCR 方向分類模型找可能上下顛倒的行。"""
        if not cands:
            return []
        imgs = []
        for c in cands:
            im = c.chunks[0].image
            h, w = im.shape[:2]
            nw = min(192, max(1, int(math.ceil(48 * w / h))))
            r = cv2.resize(im, (nw, 48)).astype(np.float32)
            r = (r / 255.0 - 0.5) / 0.5
            pad = np.zeros((48, 192, 3), np.float32)
            pad[:, :nw] = r
            imgs.append(pad.transpose(2, 0, 1))
        out = []
        for i in range(0, len(imgs), 16):
            batch = np.stack(imgs[i : i + 16])
            pr = self._cls.run(None, {self._cls_in: batch})[0]
            for j, row in enumerate(pr):
                if float(row[1]) > p["cls_thresh"]:
                    out.append(cands[i + j])
        return out

    # ------------------------------------------------------------------ 辨識

    def _recognize(self, chunks: list[_Chunk], p: dict) -> None:
        if not chunks:
            return
        order = sorted(range(len(chunks)), key=lambda i: chunks[i].image.shape[1])
        B = int(p["rec_batch"])
        H = int(p["rec_height"])
        for s in range(0, len(order), B):
            idx = order[s : s + B]
            Wmax = max(int(p["rec_min_width"]), max(chunks[i].image.shape[1] for i in idx))
            batch = np.zeros((len(idx), 3, H, Wmax), np.float32)
            for j, i in enumerate(idx):
                im = chunks[i].image.astype(np.float32)
                batch[j, :, :, : im.shape[1]] = ((im / 255.0 - 0.5) / 0.5).transpose(2, 0, 1)
            out = self._rec.run(None, {self._rec_in: batch})[0]
            T = out.shape[1]
            for j, i in enumerate(idx):
                pr = out[j]
                sums = pr.sum(axis=1)
                if not np.allclose(sums, 1.0, atol=1e-2):  # 模型沒有 softmax 時自己做
                    e = np.exp(pr - pr.max(axis=1, keepdims=True))
                    pr = e / e.sum(axis=1, keepdims=True)
                chunks[i].probs = pr
                chunks[i].stride = Wmax / T


# ---------------------------------------------------------------------------- 偵測輔助


def _as_rgb(image: np.ndarray) -> np.ndarray:
    img = np.asarray(image)
    if img.ndim == 2:
        img = np.repeat(img[:, :, None], 3, axis=2)
    elif img.shape[2] == 4:
        img = img[:, :, :3]
    if img.dtype != np.uint8:
        img = np.clip(img, 0, 255).astype(np.uint8)
    return np.ascontiguousarray(img)


def _tile_starts(L: int, T: int, step: int) -> list[int]:
    if L <= T:
        return [0]
    starts = list(range(0, L - T, step))
    starts.append(L - T)
    return sorted(set(starts))


def _box_score(prob: np.ndarray, pts: np.ndarray) -> float:
    h, w = prob.shape
    xmin = int(np.clip(np.floor(pts[:, 0].min()), 0, w - 1))
    xmax = int(np.clip(np.ceil(pts[:, 0].max()), 0, w - 1))
    ymin = int(np.clip(np.floor(pts[:, 1].min()), 0, h - 1))
    ymax = int(np.clip(np.ceil(pts[:, 1].max()), 0, h - 1))
    mask = np.zeros((ymax - ymin + 1, xmax - xmin + 1), np.uint8)
    q = pts.copy()
    q[:, 0] -= xmin
    q[:, 1] -= ymin
    cv2.fillPoly(mask, [q.round().astype(np.int32)], 1)
    return float(cv2.mean(prob[ymin : ymax + 1, xmin : xmax + 1], mask)[0])


def _db_postprocess(prob: np.ndarray, p: dict) -> list[tuple[np.ndarray, float]]:
    """DB 後處理：二值化 → 輪廓 → 最小外接矩形 → 打分 → 外擴（距離 = 面積 × r ÷ 周長）。"""
    bitmap = (prob > p["det_thresh"]).astype(np.uint8)
    bitmap = cv2.dilate(bitmap, np.ones((2, 2), np.uint8))
    contours = cv2.findContours(bitmap, cv2.RETR_LIST, cv2.CHAIN_APPROX_SIMPLE)[-2]
    out = []
    for c in contours[: int(p["det_max_candidates"])]:
        rect = cv2.minAreaRect(c)
        (cx, cy), (rw, rh), ang = rect
        if min(rw, rh) < p["det_min_box_side"]:
            continue
        score = _box_score(prob, cv2.boxPoints(rect))
        if score < p["det_box_thresh"]:
            continue
        d = rw * rh * p["det_unclip_ratio"] / (2.0 * (rw + rh))
        rect2 = ((cx, cy), (rw + 2 * d, rh + 2 * d), ang)
        if min(rect2[1]) < p["det_min_box_side"] + 2:
            continue
        out.append((cv2.boxPoints(rect2).astype(np.float64), score))
    return out


def _long_frame(pts: np.ndarray):
    c, e, lu, lv = _rect_frame(pts)
    if lu >= lv:
        return c, e, _rot90cw(e), lu, lv
    n = _rot90cw(e)
    return c, n, e, lv, lu


def _same_line(a, b, p: dict) -> bool:
    (ca, ea, na, la, ha), (cb, eb, nb, lb, hb) = a, b
    if lb > la:
        (ca, ea, na, la, ha), (cb, eb, nb, lb, hb) = (cb, eb, nb, lb, hb), (ca, ea, na, la, ha)
    if max(ha, hb) / max(min(ha, hb), 1e-6) > p["det_merge_height_ratio"]:
        return False
    if la > 1.5 * ha and lb > 1.5 * hb:
        if abs(float(ea @ eb)) < math.cos(math.radians(p["det_merge_angle_deg"])):
            return False
    corners = cb + np.array([[-1, -1], [1, -1], [1, 1], [-1, 1]], float)[:, :1] * eb * lb / 2 + np.array(
        [[-1, -1], [1, -1], [1, 1], [-1, 1]], float
    )[:, 1:] * nb * hb / 2
    s = (corners - ca) @ ea
    t = (corners - ca) @ na
    t_ov = min(t.max(), ha / 2) - max(t.min(), -ha / 2)
    s_ov = min(s.max(), la / 2) - max(s.min(), -la / 2)
    return t_ov >= p["det_merge_overlap"] * min(ha, hb) and s_ov > 0


def _merge_det_boxes(boxes: list[_DetBox], p: dict) -> list[_DetBox]:
    n = len(boxes)
    if n < 2:
        return boxes
    parent = list(range(n))

    def find(i: int) -> int:
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    geo = [_long_frame(b.pts) for b in boxes]
    aabb = np.array([[b.pts[:, 0].min(), b.pts[:, 1].min(), b.pts[:, 0].max(), b.pts[:, 1].max()] for b in boxes])
    for i in range(n):
        ov = (aabb[:, 0] <= aabb[i, 2]) & (aabb[:, 2] >= aabb[i, 0]) & (aabb[:, 1] <= aabb[i, 3]) & (aabb[:, 3] >= aabb[i, 1])
        for j in np.nonzero(ov)[0]:
            j = int(j)
            if j <= i:
                continue
            if _same_line(geo[i], geo[j], p):
                ri, rj = find(i), find(j)
                if ri != rj:
                    parent[rj] = ri
    groups: dict[int, list[int]] = {}
    for i in range(n):
        groups.setdefault(find(i), []).append(i)
    out = []
    for g in groups.values():
        if len(g) == 1:
            out.append(boxes[g[0]])
            continue
        pts = np.vstack([boxes[i].pts for i in g]).astype(np.float32)
        rect = cv2.minAreaRect(pts)
        out.append(_DetBox(cv2.boxPoints(rect).astype(np.float64), max(boxes[i].score for i in g), -1))
    return out


# ---------------------------------------------------------------------------- 攤平與行界


def _orientation_candidates(pts: np.ndarray, p: dict) -> list[int]:
    """【替換點：方向】框高明顯大於框寬時，另外試 90 與 270 度的讀法。"""
    _, _, lu, lv = _rect_frame(pts)
    if lv > p["tall_box_ratio"] * lu:
        return [0, 90, 270]
    return [0]


def _make_cand(bi: int, rot: int, pts: np.ndarray, p: dict) -> _Cand:
    c, e, lu, lv = _rect_frame(pts)
    if rot == 0:
        u, a, b = e, lu, lv
    elif rot == 90:
        u, a, b = _rot90cw(e), lv, lu
    elif rot == 270:
        u, a, b = -_rot90cw(e), lv, lu
    else:
        u, a, b = -e, lu, lv
    v = _rot90cw(u)
    k = 1.0
    if b < p["flat_min_height"]:
        k = min(p["flat_min_height"] / max(b, 1.0), p["flat_max_scale"])
    return _Cand(box=bi, rot=rot, center=c, u=u, v=v, lu=a, lv=b, k=k)


def _warp(img: np.ndarray, c: _Cand, u: np.ndarray, v: np.ndarray, ext_u: float, ext_v: float):
    """以 c.center 為中心、沿 u（寬 ext_u）與 v（高 ext_v）攤平。回傳（攤平影像、原點）。"""
    k = c.k
    origin = c.center - ext_u / 2 * u - ext_v / 2 * v
    Wf = max(int(math.ceil(ext_u * k)), 4)
    Hf = max(int(math.ceil(ext_v * k)), 4)
    off = origin - 0.5 + (0.5 / k) * (u + v)
    A = np.array([[u[0] / k, v[0] / k, off[0]], [u[1] / k, v[1] / k, off[1]]], np.float64)
    flat = cv2.warpAffine(img, A, (Wf, Hf), flags=cv2.INTER_LINEAR | cv2.WARP_INVERSE_MAP, borderMode=cv2.BORDER_REPLICATE)
    return flat, origin


def _ink_maps(flat: np.ndarray, region: tuple[int, int, int, int], p: dict):
    """背景色（眾數 + 平面擬合）→ 色差 → 軟墨跡。回傳 (soft, bg, fg, contrast) 或 None。"""
    r0, r1, c0, c1 = region
    sub = flat[r0:r1, c0:c1].reshape(-1, 3)
    if sub.shape[0] < 16:
        return None
    bits = int(p["bg_quant_bits"])
    q = (sub >> (8 - bits)).astype(np.int64)
    key = (q[:, 0] << (2 * bits)) | (q[:, 1] << bits) | q[:, 2]
    mode = int(np.bincount(key).argmax())
    bg0 = np.median(sub[key == mode], axis=0)
    f = flat.astype(np.float32)
    d = np.linalg.norm(f - bg0.astype(np.float32), axis=2)
    bg_map = None
    if p["bg_plane_fit"]:
        dsub = d[r0:r1, c0:c1]
        thr = _otsu(dsub)
        ys, xs = np.nonzero(dsub < 0.5 * thr)
        if ys.size > 50:
            if ys.size > 20000:
                sel = np.linspace(0, ys.size - 1, 20000).astype(np.int64)
                ys, xs = ys[sel], xs[sel]
            yy, xx = ys + r0, xs + c0
            Am = np.stack([np.ones_like(xx, dtype=np.float64), xx, yy], axis=1)
            coef, *_ = np.linalg.lstsq(Am, f[yy, xx].astype(np.float64), rcond=None)
            H, W = d.shape
            gy, gx = np.mgrid[0:H, 0:W]
            bg_map = (coef[0][None, None, :] + gx[..., None] * coef[1][None, None, :] + gy[..., None] * coef[2][None, None, :]).astype(np.float32)
            d = np.linalg.norm(f - bg_map, axis=2)
    dsub = d[r0:r1, c0:c1]
    thr = _otsu(dsub)
    ink = dsub > thr
    if ink.sum() < 3 or (~ink).sum() < 3:
        return None
    d_text = float(np.percentile(dsub[ink], p["ink_text_pct"]))
    n0 = float(np.percentile(dsub[~ink], p["ink_noise_pct"]))
    if d_text - n0 < 1e-3:
        return None
    soft = np.clip((d - n0) / (d_text - n0), 0.0, 1.0)
    core = soft[r0:r1, c0:c1] > 0.9
    fg = np.median(flat[r0:r1, c0:c1][core], axis=0) if core.any() else np.median(sub[ink.ravel()], axis=0)
    bgc = bg_map[(r0 + r1) // 2, (c0 + c1) // 2] if bg_map is not None else bg0
    return soft, np.asarray(bgc, float), np.asarray(fg, float), d_text


def _estimate_skew(soft: np.ndarray, region, p: dict) -> tuple[float, float]:
    """投影銳度最大的角度（度，正值表示行往右下傾斜）與誤差（度）。"""
    r0, r1, c0, c1 = region
    sub = soft[r0:r1, c0:c1]
    ys, xs = np.nonzero(sub > 0.3)
    if ys.size < 20:
        return 0.0, p["skew_search_deg"]
    w = sub[ys, xs]
    if ys.size > p["skew_max_points"]:
        sel = np.linspace(0, ys.size - 1, int(p["skew_max_points"])).astype(np.int64)
        ys, xs, w = ys[sel], xs[sel], w[sel]
    xs = xs - xs.mean()
    S, step = p["skew_search_deg"], p["skew_step_deg"]
    angles = np.arange(-S, S + 1e-9, step)
    scores = np.empty(len(angles))
    for i, a in enumerate(angles):
        yy = ys - xs * math.tan(math.radians(a))
        yy = yy - yy.min()
        i0 = np.floor(yy).astype(np.int64)
        fr = yy - i0
        # 線性分配到相鄰兩格，避免整數角度（例如 0 度）時的取樣疊影偏好
        h = np.bincount(i0, weights=w * (1.0 - fr), minlength=int(i0.max()) + 2)
        h[: int(i0.max()) + 2] += np.bincount(i0 + 1, weights=w * fr, minlength=int(i0.max()) + 2)[: int(i0.max()) + 2]
        scores[i] = float((h * h).sum())
    i = int(np.argmax(scores))
    best = float(angles[i])
    if 0 < i < len(angles) - 1:
        y0, y1, y2 = scores[i - 1], scores[i], scores[i + 1]
        den = y0 - 2 * y1 + y2
        if den < 0:
            best += float(np.clip(0.5 * (y0 - y2) / den, -0.5, 0.5)) * step
    near = angles[scores >= 0.98 * scores[i]]
    err = max(step / 2, float(near.max() - near.min()) / 2)
    return best, err


def _first_pass(img: np.ndarray, c: _Cand, p: dict) -> None:
    """第一次攤平（偵測框角度）：估角度。"""
    m = p["flat_margin"] * c.lv
    flat, _ = _warp(img, c, c.u, c.v, c.lu + 2 * m, c.lv + 2 * m)
    k = c.k
    region = (int(m * k), int((m + c.lv) * k), int(m * k), int((m + c.lu) * k))
    res = _ink_maps(flat, region, p)
    if res is None:
        c.delta, c.angle_err = 0.0, p["skew_search_deg"]
        return
    soft = res[0]
    if c.lu / max(c.lv, 1e-6) >= p["skew_min_aspect"]:
        c.delta, c.angle_err = _estimate_skew(soft, region, p)
    else:
        c.delta = None  # 等整頁角度


def _apply_page_skew(cands: list[_Cand], p: dict) -> None:
    """短行沿用整頁長行角度的加權中位數。"""
    angs, wts = [], []
    for c in cands:
        if c.delta is not None and c.rot in (0, 180) and c.lu / max(c.lv, 1e-6) >= p["skew_min_aspect"]:
            angs.append(_norm45(_angle_deg(c.u) + c.delta))
            wts.append(c.lu)
    page, spread = None, p["skew_search_deg"]
    if angs:
        o = np.argsort(angs)
        a = np.asarray(angs)[o]
        w = np.asarray(wts)[o]
        cw = np.cumsum(w)
        page = float(a[np.searchsorted(cw, cw[-1] / 2)])
        spread = float(np.sqrt(np.average((a - page) ** 2, weights=w))) if len(a) > 1 else 0.5
    for c in cands:
        if c.delta is not None:
            continue
        phi = _norm45(_angle_deg(c.u))
        if page is not None and abs(_norm45(page - phi)) <= p["skew_search_deg"]:
            c.delta = _norm45(page - phi)
            c.angle_err = max(spread, 0.3)
        else:
            c.delta = 0.0
            c.angle_err = 1.5


def _second_pass(img: np.ndarray, c: _Cand, p: dict) -> None:
    """以精修角度重新攤平：行界、本行墨跡、辨識裁切。"""
    d = math.radians(c.delta or 0.0)
    u = math.cos(d) * c.u + math.sin(d) * c.v
    u = u / np.linalg.norm(u)
    v = _rot90cw(u)
    c.u, c.v = u, v
    k = c.k
    m = p["flat_margin"] * c.lv
    ext_u, ext_v = c.lu + 2 * m, c.lv + 2 * m
    flat, origin = _warp(img, c, u, v, ext_u, ext_v)
    region = (int(m * k), int((m + c.lv) * k), int(m * k), int((m + c.lu) * k))
    res = _ink_maps(flat, region, p)
    if res is None:
        return
    soft, bg, fg, contrast = res
    c.flat, c.origin, c.soft, c.bg, c.fg, c.contrast, c.region = flat, origin, soft, bg, fg, contrast, region
    r0, r1, c0, c1 = region
    # 核心帶：偵測框範圍內墨跡列分布的四分位距（對中文與拉丁字都穩定）
    prof = soft[r0:r1, c0:c1].sum(axis=1)
    if prof.sum() <= 0:
        return
    cdf = np.cumsum(prof) / prof.sum()
    q = p["core_band_q"]
    a = r0 + int(np.searchsorted(cdf, q))
    b = r0 + int(np.searchsorted(cdf, 1.0 - q)) + 1
    core_h = max(b - a, 1)
    det_h = max(r1 - r0, 1)
    mask = (soft >= 0.5).astype(np.uint8)
    n, labels, stats, _ = cv2.connectedComponentsWithStats(mask, connectivity=8)
    if n <= 1:
        return
    xs, ys, ws, hs, areas = stats[1:, 0], stats[1:, 1], stats[1:, 2], stats[1:, 3], stats[1:, 4]
    cx = xs + ws / 2.0
    cy = ys + hs / 2.0
    tol = p["cc_x_tol"] * det_h
    in_x = (cx >= c0 - tol) & (cx <= c1 + tol)
    overlap = (ys < b) & (ys + hs > a)
    big_enough = areas >= max(p["cc_min_area_frac"] * core_h * core_h, 2)
    not_huge = hs <= p["cc_max_height"] * det_h
    Hf = soft.shape[0]
    inside = (ys > 0) & (ys + hs < Hf)  # 碰到攤平影像上下緣的是框線或圖形，不屬於本行
    keep = in_x & overlap & big_enough & not_huge & inside
    if not keep.any():
        return
    top = int(ys[keep].min())
    bot = int((ys + hs)[keep].max())
    left = int(xs[keep].min())
    right = int((xs + ws)[keep].max())
    lh = max(bot - top, 1)
    # 併入小塊的條件：中心在偵測框上下範圍內（避免吃到上一行斷開的筆畫）、離本行上緣不遠
    above = (cy >= max(top - p["cc_attach_above"] * lh, r0)) & (cy <= bot)
    touch_band = (ys < bot) & (ys + hs > top) & (cy <= r1)  # 逗號、分號下半部會伸出基線
    narrow = ws <= p["cc_attach_max_width"] * lh
    attach = (~keep) & in_x & inside & (areas >= 2) & (hs < 0.6 * lh) & narrow & (above | touch_band)
    keep = keep | attach
    ids = np.nonzero(keep)[0] + 1
    kept = np.isin(labels, ids)
    top = int(ys[keep].min())
    bot = int((ys + hs)[keep].max())
    left = int(xs[keep].min())
    right = int((xs + ws)[keep].max())
    c.kept = kept
    c.bounds = (left, top, right, bot)
    # 邊緣模糊寬度：過渡像素數 ÷ 邊界長度
    band = soft[max(top - 2, 0) : bot + 2, max(left - 2, 0) : right + 2]
    km = kept[max(top - 2, 0) : bot + 2, max(left - 2, 0) : right + 2].astype(np.uint8)
    edge = int((km - cv2.erode(km, np.ones((3, 3), np.uint8))).sum())
    trans = int(((band > 0.15) & (band < 0.85)).sum())
    c.blur_px = (trans / max(edge, 1)) / k
    # 辨識裁切：把不屬於本行的墨跡塗成背景色
    other = ((labels > 0) & ~kept).astype(np.uint8)
    r = int(p["clean_dilate_px"])
    if other.any():
        ker = np.ones((2 * r + 1, 2 * r + 1), np.uint8)
        paint = cv2.dilate(other, ker).astype(bool) & ~cv2.dilate(kept.astype(np.uint8), np.ones((3, 3), np.uint8)).astype(bool)
        clean = flat.copy()
        clean[paint] = bg.astype(np.uint8)
    else:
        clean = flat
    h_ink = bot - top
    pv_ = int(round(p["rec_pad_v"] * h_ink))
    ph_ = int(round(p["rec_pad_h"] * h_ink))
    light = float(fg @ [0.299, 0.587, 0.114]) > float(bg @ [0.299, 0.587, 0.114])
    if light:
        c.notes.append("淺色字")
    crop = _crop_pad(clean, top - pv_, bot + pv_, left - ph_, right + ph_, bg)
    if light and p["rec_normalize_polarity"]:
        crop = 255 - crop
    colbin = kept[top:bot].sum(axis=0)
    c.chunks = _make_chunks(crop, left - ph_, colbin, left - ph_, p)
    c.ok = len(c.chunks) > 0


def _crop_pad(img: np.ndarray, y0: int, y1: int, x0: int, x1: int, fill: np.ndarray) -> np.ndarray:
    H, W = img.shape[:2]
    out = np.empty((max(y1 - y0, 1), max(x1 - x0, 1), 3), np.uint8)
    out[:] = np.clip(fill, 0, 255).astype(np.uint8)
    sy0, sy1, sx0, sx1 = max(y0, 0), min(y1, H), max(x0, 0), min(x1, W)
    if sy1 > sy0 and sx1 > sx0:
        out[sy0 - y0 : sy1 - y0, sx0 - x0 : sx1 - x0] = img[sy0:sy1, sx0:sx1]
    return out


def _make_chunks(crop_rgb: np.ndarray, crop_x0: int, colbin: np.ndarray, colbin_x0: int, p: dict) -> list[_Chunk]:
    """辨識裁切 → 一或多段辨識輸入（長行在墨跡谷底切段）。"""
    Hc, Wc = crop_rgb.shape[:2]
    H = int(p["rec_height"])
    cuts = [0, Wc]
    if Wc / Hc > p["rec_max_aspect"]:
        n = int(math.ceil((Wc / Hc) / p["rec_chunk_aspect"]))
        cuts = [0]
        for i in range(1, n):
            target = i * Wc / n
            lo, hi = int(target - 0.5 * Hc), int(target + 0.5 * Hc)
            # colbin 的座標：crop 座標 + crop_x0 - colbin_x0
            off = crop_x0 - colbin_x0
            seg_lo, seg_hi = max(lo + off, 0), min(hi + off, len(colbin))
            if seg_hi > seg_lo:
                seg = colbin[seg_lo:seg_hi].astype(float)
                sm = np.convolve(seg, np.ones(3) / 3, mode="same")
                cut = int(np.argmin(sm)) + seg_lo - off
            else:
                cut = int(target)
            if cut > cuts[-1] + Hc:
                cuts.append(cut)
        cuts.append(Wc)
    out = []
    bgr = crop_rgb[:, :, ::-1]
    for a, b in zip(cuts[:-1], cuts[1:]):
        piece = bgr[:, a:b]
        if piece.shape[1] < 2:
            continue
        fx = H / Hc
        nw = max(int(round(piece.shape[1] * fx)), 8)
        interp = cv2.INTER_AREA if fx < 1 else cv2.INTER_CUBIC
        im = cv2.resize(np.ascontiguousarray(piece), (nw, H), interpolation=interp)
        out.append(_Chunk(image=im, x0=float(crop_x0 + a), fx=nw / piece.shape[1]))
    return out


# ---------------------------------------------------------------------------- 解碼


@dataclass
class _Dec:
    text: str
    prob: float
    center: float  # 攤平像素
    cands: list[CharCandidate]


def _ctc_decode(ch: _Chunk, charset: list[str], p: dict) -> list[_Dec]:
    """CTC 貪婪解碼，保留每個字的前幾名候選（含 blank → ""）。"""
    pr = ch.probs
    if pr is None:
        return []
    am = pr.argmax(axis=1)
    out: list[_Dec] = []
    T = len(am)
    content_w = ch.image.shape[1]
    t = 0
    topk = int(p["cand_topk"])
    while t < T:
        c = int(am[t])
        if c == 0:
            t += 1
            continue
        s = t
        while t + 1 < T and int(am[t + 1]) == c:
            t += 1
        e = t
        t += 1
        xc = (s + e + 1) / 2.0 * ch.stride
        if xc > content_w + ch.stride:
            continue  # 補白區的幻覺
        span = pr[s : e + 1].max(axis=0)
        idx = np.argpartition(-span, min(topk, len(span) - 1))[: topk + 1]
        idx = idx[np.argsort(-span[idx])]
        cands = [CharCandidate(charset[c], float(span[c]))]
        for i in idx:
            i = int(i)
            if i == c or span[i] < p["cand_min_prob"] or len(cands) >= topk:
                continue
            cands.append(CharCandidate(charset[i], float(span[i])))
        out.append(_Dec(charset[c], float(span[c]), ch.x0 + xc / ch.fx, cands))
    return out


def _decode_cand(c: _Cand, charset: list[str], p: dict) -> list[_Dec]:
    decs: list[_Dec] = []
    for ch in c.chunks:
        decs.extend(_ctc_decode(ch, charset, p))
    return decs


def _cand_quality(c: _Cand, charset: list[str], p: dict) -> float:
    decs = [d for d in _decode_cand(c, charset, p) if d.text != " "]
    if not decs:
        return 0.0
    return float(np.mean([d.prob for d in decs]))


def _choose_orientation(cs: list[_Cand], p: dict) -> _Cand:
    base = [c for c in cs if c.rot == 0]
    best = base[0] if base else cs[0]
    for c in cs:
        if c is best:
            continue
        if c.quality > best.quality + p["rotate_margin"]:
            best = c
    return best


# ---------------------------------------------------------------------------- 字界


def _char_cells(tok_texts: list[str], centers: list[float], L: int, R: int, colbin: np.ndarray, colsoft: np.ndarray, p: dict, em: float = 0.0):
    """【替換點：字界】字界初值 = CTC 中心的中點與字寬表推算位置的加權平均；再用動態規劃在
    墨跡直行投影的候選切點（零墨跡段、投影谷底）中選出一組單調、總代價最小的字界。

    tok_texts 含模型給的空白；centers 只對非空白字。colbin、colsoft 的索引 = 攤平像素 x。
    回傳每個非空白字的 (x0, x1, 左界相黏, 右界相黏, 格內有墨跡)。
    """
    ns = [i for i, t in enumerate(tok_texts) if t != " "]
    n = len(ns)
    if n == 0:
        return []
    if n == 1:
        return [(float(L), float(R), False, False, True)]
    adv = np.array([_advance(t) for t in tok_texts])
    st = np.concatenate([[0.0], np.cumsum(adv)[:-1]])
    en = st + adv
    tc = np.array([(st[i] + en[i]) / 2 for i in ns])
    ctc = np.asarray(centers, float)
    # 加權最小平方：CTC 中心 ≈ α + β × 字寬表中心；墨跡左右緣當錨點
    wa = max(2.0, n / 3.0)
    X = np.array([[1.0, x] for x in tc] + [[1.0, st[ns[0]] + 0.05 * adv[ns[0]]], [1.0, en[ns[-1]] - 0.05 * adv[ns[-1]]]])
    Y = np.concatenate([ctc, [L, R]])
    Wt = np.sqrt(np.concatenate([np.ones(n), [wa, wa]]))
    sol, *_ = np.linalg.lstsq(X * Wt[:, None], Y * Wt, rcond=None)
    alpha, beta = float(sol[0]), float(sol[1])
    if beta <= 0:
        beta = (R - L) / max(en[ns[-1]] - st[ns[0]], 1e-6)
        alpha = L - beta * st[ns[0]]
    w = p["boundary_ctc_weight"]
    prior, sig = [], []
    for k in range(n - 1):
        i, j = ns[k], ns[k + 1]
        b_tab = alpha + beta * 0.5 * (en[i] + st[j])
        b_ctc = 0.5 * (ctc[k] + ctc[k + 1])
        prior.append(w * b_ctc + (1 - w) * b_tab)
        sig.append(max(p["boundary_sigma"] * beta * (tc[k + 1] - tc[k]), 1.0))
    prior_a, sig_a = np.array(prior), np.array(sig)
    # 候選切點：(位置, 左字終點, 右字起點, 谷底代價)
    cands = []
    for a, b in _runs(colbin[L:R] == 0):
        cands.append(((a + b) / 2.0 + L, float(a + L), float(b + L), 0.0))
    seg = colsoft[L:R].astype(float)
    if seg.size >= 3:
        sm = np.convolve(seg, np.ones(3) / 3, mode="same")
        smax = max(float(sm.max()), 1e-6)
        x = np.arange(1, sm.size - 1)
        mins = x[(sm[x] < sm[x - 1]) & (sm[x] <= sm[x + 1]) & (colbin[L + x] > 0)]
        for m in mins:
            cands.append((L + m + 0.5, L + m + 0.5, L + m + 0.5, float(sm[m] / smax) + 1e-3))
    if len(cands) < n - 1:
        for q in np.linspace(L, R, n + 1)[1:-1]:
            cands.append((float(q), float(q), float(q), 1.0))
    cands.sort()
    pos = np.array([c[0] for c in cands])
    depth = np.array([c[3] for c in cands])
    width = np.array([c[2] - c[1] for c in cands])
    bonus = p["boundary_gap_bonus"] * np.clip(width / max(0.3 * em, 1e-6), 0, 1) * (depth == 0) if em > 0 else np.zeros(len(cands))
    C = len(cands)
    INF = 1e18
    cost = np.full((n - 1, C), INF)
    back = np.zeros((n - 1, C), np.int64)
    idx = np.arange(C)
    for k in range(n - 1):
        local = p["boundary_depth_weight"] * depth + p["boundary_touch_penalty"] * (depth > 0) - bonus + ((pos - prior_a[k]) / sig_a[k]) ** 2
        if k == 0:
            cost[0] = local
            continue
        prev = cost[k - 1]
        pm = np.minimum.accumulate(prev)
        newmin = prev <= np.concatenate([[INF * 10], pm[:-1]])
        arg = np.maximum.accumulate(np.where(newmin, idx, 0))
        cost[k, 1:] = local[1:] + pm[:-1]
        back[k, 1:] = arg[:-1]
    c = int(np.argmin(cost[n - 2]))
    chosen = [c]
    for k in range(n - 2, 0, -1):
        c = int(back[k, c])
        chosen.append(c)
    chosen.reverse()
    bounds = [(cands[c][1], cands[c][2], cands[c][3] > 0) for c in chosen]
    cells = []
    for i in range(n):
        cs = float(L) if i == 0 else bounds[i - 1][1]
        ce = float(R) if i == n - 1 else bounds[i][0]
        tl = False if i == 0 else bounds[i - 1][2]
        tr = False if i == n - 1 else bounds[i][2]
        a, b = max(int(math.floor(cs)), 0), min(int(math.ceil(ce)), len(colbin))
        ink = np.nonzero(colbin[a:b] > 0)[0] if b > a else np.array([], int)
        if ink.size:
            x0 = max(float(a + ink[0]), cs)
            x1 = min(float(a + ink[-1] + 1), ce)
            cells.append((x0, max(x1, x0), tl, tr, True))
        else:
            mid = 0.5 * (cs + ce)
            cells.append((mid, mid, tl, tr, False))
    return cells


# ---------------------------------------------------------------------------- 誤判


def _text_likeness(c: _Cand, text: str, em: float, p: dict) -> tuple[float, dict]:
    """【替換點：誤判】筆畫寬 ÷ 行高、填滿率、字數與行長比例 → 像文字的程度（0 到 1）。"""
    left, top, right, bot = c.bounds
    km = c.kept[top:bot, left:right].astype(np.uint8)
    H, W = km.shape
    feats: dict = {}
    if H < 2 or W < 2 or km.sum() == 0:
        return 0.0, feats
    nonspace = [ch for ch in text if not ch.isspace()]
    blob_only = bool(nonspace) and all(ch in p["blob_chars"] for ch in nonspace)
    f_stroke = f_fill = 1.0
    if not blob_only:
        dt = cv2.distanceTransform(np.pad(km, 1), cv2.DIST_L2, 3)[1:-1, 1:-1]
        ridge = (dt >= cv2.dilate(dt, np.ones((3, 3), np.uint8))) & (km > 0)
        sw = 2.0 * float(np.median(dt[ridge])) if ridge.any() else 0.0
        stroke = sw / H
        fill = float(km.mean())
        feats.update(stroke=stroke, fill=fill)
        f_stroke = float(np.clip((p["stroke_bad"] - stroke) / (p["stroke_bad"] - p["stroke_ok"]), 0, 1))
        f_fill = float(np.clip((p["fill_bad"] - fill) / (p["fill_bad"] - p["fill_ok"]), 0, 1))
    expected = sum(1.0 if _is_wide(ch) else (0.28 if ch.isspace() else 0.55) for ch in text) * em
    ratio = W / max(expected, 1e-6)
    feats["len_ratio"] = ratio
    if ratio < p["len_ratio_min"]:
        f_len = ratio / p["len_ratio_min"]
    elif ratio > p["len_ratio_max"]:
        f_len = p["len_ratio_max"] / ratio
    else:
        f_len = 1.0
    return f_stroke * f_fill * f_len, feats


# ---------------------------------------------------------------------------- 收尾


def _space_thresholds(a: str, b: str, p: dict) -> tuple[float, float]:
    """兩個相鄰字之間補入與刪除空白的間隙門檻（em）。"""
    if _is_fullwidth_punct(a) or _is_fullwidth_punct(b):
        return p["space_fw_insert_em"], p["space_fw_remove_em"]
    # 中文字旁的半形標點常是把全形標點讀成半形（例如「（」讀成「(」），字形內建留白，門檻比照全形
    punct = lambda c: unicodedata.category(c)[0] == "P"  # noqa: E731
    if (_is_wide(a) and punct(b)) or (punct(a) and _is_wide(b)):
        return p["space_fw_insert_em"], p["space_fw_remove_em"]
    if _is_wide(a) and _is_wide(b):
        return p["space_cjk_insert_em"], p["space_cjk_remove_em"]
    return p["space_insert_em"], p["space_remove_em"]


def _finalize(c: _Cand, box: _DetBox, charset: list[str], p: dict) -> Optional[tuple[OcrLine, float]]:
    decs = _decode_cand(c, charset, p)
    # 合併連續空白、去掉行首行尾空白
    toks: list[_Dec] = []
    for d in decs:
        if d.text == " " and (not toks or toks[-1].text == " "):
            continue
        toks.append(d)
    while toks and toks[-1].text == " ":
        toks.pop()
    nonspace = [d for d in toks if d.text != " "]
    if not nonspace:
        return None
    mean_prob = float(np.mean([d.prob for d in nonspace]))
    if mean_prob < p["drop_score"]:
        return None
    left, top, right, bot = c.bounds
    k = c.k
    colbin = c.kept[top:bot].sum(axis=0)
    colsoft = (c.soft * c.kept)[top:bot].sum(axis=0)
    h_ink = bot - top
    text_ns = "".join(d.text for d in nonspace)
    if len(nonspace) <= p["drop_short_max_chars"] and mean_prob < p["drop_short_score"]:
        return None
    em = _em_from_height(text_ns, h_ink, p)
    cells = _char_cells([d.text for d in toks], [d.center for d in nonspace], left, right, colbin, colsoft, p, em)
    # 空白：以量到的間隙為準
    had_space = []
    j = 0
    for i, d in enumerate(toks):
        if d.text == " ":
            continue
        if j > 0:
            had_space.append(toks[i - 1].text == " " if i > 0 else False)
        j += 1
    space_prob = {}
    for i, d in enumerate(toks):
        if d.text == " ":
            space_prob[sum(1 for t in toks[:i] if t.text != " ") - 1] = d
    chars: list[OcrChar] = []
    texts: list[str] = []
    pitch_img = (right - left) / max(len(nonspace), 1) / k
    base_err2 = (0.5 / k) ** 2 + (0.5 * c.blur_px) ** 2
    for i, (d, cell) in enumerate(zip(nonspace, cells)):
        if i > 0:
            a, b = nonspace[i - 1].text, d.text
            gap = (cell[0] - cells[i - 1][1]) / em
            ins, rm = _space_thresholds(a, b, p)
            had = had_space[i - 1]
            sp = None
            if had and gap >= rm:
                md = space_prob[i - 1]
                sp = (md.prob, md.cands)
            elif not had and gap > ins:
                conf = 1.0 / (1.0 + math.exp(-(gap - ins) / p["space_soft_em"]))
                sp = (conf, [CharCandidate(" ", conf), CharCandidate("", 1.0 - conf)])
            if sp is not None:
                x0s = (cells[i - 1][1] - left) / k
                x1s = (cell[0] - left) / k
                chars.append(OcrChar(" ", float(sp[0]), x0s, x1s, sp[1], x_error=math.sqrt(base_err2)))
                texts.append(" ")
        x0, x1, tl, tr, has_ink = cell
        err2 = base_err2
        if tl or tr:
            err2 += (p["touch_error_frac"] * pitch_img) ** 2
        if not has_ink:
            err2 += (0.5 * pitch_img) ** 2
        chars.append(OcrChar(d.text, d.prob, (x0 - left) / k, (x1 - left) / k, d.cands, x_error=math.sqrt(err2)))
        texts.append(d.text)
    text = "".join(texts)
    like, feats = _text_likeness(c, text, em, p)
    if like < p["drop_textlike"] and mean_prob < p["drop_textlike_keep_score"]:
        return None
    notes = list(c.notes)
    if c.rot:
        notes.append(f"旋轉 {c.rot} 度的橫排文字")
    score = mean_prob * math.sqrt(max(like, 0.0))
    if like < 0.7:
        notes.append(f"形狀不太像文字（像文字程度 {like:.2f}）")
    h_img = h_ink / k
    if h_img < p["low_res_px"]:
        score *= p["low_res_factor"]
        notes.append(f"低解析度（墨跡行高 {h_img:.1f} 像素）")
    if c.contrast < p["min_contrast"]:
        score *= c.contrast / p["min_contrast"]
        notes.append(f"對比不足（色差 {c.contrast:.0f}）")
    if c.blur_px > 0.15 * h_img:
        notes.append(f"邊緣模糊（約 {c.blur_px:.1f} 像素）")
    o, u, v = c.origin, c.u, c.v

    def to_img(s: float, t: float) -> np.ndarray:
        return o + (s / k) * u + (t / k) * v

    quad = np.array([to_img(left, top), to_img(right, top), to_img(right, bot), to_img(left, bot)], np.float64)
    w_img = (right - left) / k
    quad_err = math.sqrt(base_err2 + (math.sin(math.radians(c.angle_err)) * w_img / 2) ** 2)
    line = OcrLine(
        quad=quad,
        text=text,
        score=float(np.clip(score, 0.0, 1.0)),
        chars=chars,
        vertical=False,
        quad_error=quad_err,
        angle_error_deg=float(c.angle_err),
        notes=notes,
    )
    return line, em / k


def _merge_collinear(items: list[tuple[OcrLine, float]], p: dict) -> list[tuple[OcrLine, float]]:
    """同一基線上、間隙小的兩行合併（偵測模型常在中英之間的大字距處把一行斷成兩段）。"""
    items = list(items)
    cos_tol = math.cos(math.radians(1.5))
    changed = True
    while changed:
        changed = False
        for ia in range(len(items)):
            la, ea = items[ia]
            ua = la.quad[1] - la.quad[0]
            wa = float(np.linalg.norm(ua))
            if wa <= 0:
                continue
            ua = ua / wa
            va = _rot90cw(ua)
            ha = float((la.quad[3] - la.quad[0]) @ va)
            for ib in range(len(items)):
                if ib == ia:
                    continue
                lb, eb = items[ib]
                ub = lb.quad[1] - lb.quad[0]
                if np.linalg.norm(ub) <= 0 or float(ua @ (ub / np.linalg.norm(ub))) < cos_tol:
                    continue
                if max(ea, eb) / max(min(ea, eb), 1e-6) > p["line_merge_em_ratio"]:
                    continue
                sB = (lb.quad - la.quad[0]) @ ua
                tB = (lb.quad - la.quad[0]) @ va
                em = max(ea, eb)
                gap = float(sB.min()) - wa
                if not (-0.2 * em <= gap <= p["line_merge_gap_em"] * em):
                    continue
                hb = float(tB.max() - tB.min())
                ov = min(float(tB.max()), ha) - max(float(tB.min()), 0.0)
                if ov < p["line_merge_overlap"] * min(ha, hb):
                    continue
                items[ia] = (_join_lines(la, lb, ua, va, wa, ha, sB, tB, gap / em, p), em)
                del items[ib]
                changed = True
                break
            if changed:
                break
    return items


def _join_lines(la: OcrLine, lb: OcrLine, ua, va, wa, ha, sB, tB, gap_em: float, p: dict) -> OcrLine:
    q0 = la.quad[0]
    top, bot = min(0.0, float(tB.min())), max(ha, float(tB.max()))
    right = max(wa, float(sB.max()))
    quad = np.array([q0 + top * va, q0 + right * ua + top * va, q0 + right * ua + bot * va, q0 + bot * va])
    shift = float(sB[0])
    chars = list(la.chars)
    text = la.text
    a = la.text.rstrip()[-1:] or " "
    b = lb.text.lstrip()[:1] or " "
    ins, _ = _space_thresholds(a, b, p)
    if gap_em > ins:
        conf = 1.0 / (1.0 + math.exp(-(gap_em - ins) / p["space_soft_em"]))
        err = max([c.x_error for c in chars] or [0.5])
        chars.append(OcrChar(" ", conf, wa, shift, [CharCandidate(" ", conf), CharCandidate("", 1.0 - conf)], x_error=err))
        text += " "
    for c in lb.chars:
        chars.append(OcrChar(c.text, c.score, c.x0 + shift, c.x1 + shift, c.candidates, x_error=c.x_error))
    text += lb.text
    n_a, n_b = max(len(la.text), 1), max(len(lb.text), 1)
    return OcrLine(
        quad=quad,
        text=text,
        score=(la.score * n_a + lb.score * n_b) / (n_a + n_b),
        chars=chars,
        vertical=False,
        quad_error=max(la.quad_error, lb.quad_error),
        angle_error_deg=max(la.angle_error_deg, lb.angle_error_deg),
        notes=sorted(set(la.notes) | set(lb.notes) | {"由兩個偵測框合併"}),
    )


def _dedup(lines: list[OcrLine], p: dict) -> list[OcrLine]:
    order = sorted(range(len(lines)), key=lambda i: -lines[i].score)
    kept: list[OcrLine] = []
    for i in order:
        li = lines[i]
        dup = False
        for lk in kept:
            iou, cont = _quad_iou(li.quad, lk.quad)
            if iou > p["dup_iou"] or (cont > 0.85 and len(li.text.strip()) <= len(lk.text.strip())):
                dup = True
                break
        if not dup:
            kept.append(li)
    return kept


def _reading_order(lines: list[OcrLine]) -> list[OcrLine]:
    if not lines:
        return lines
    info = []
    for l in lines:
        ys = l.quad[:, 1]
        info.append((float(ys.min()), float(ys.max()), float(l.quad[:, 0].min()), l))
    info.sort(key=lambda t: t[0])
    rows: list[list] = []
    for it in info:
        if rows:
            r = rows[-1]
            rt = min(x[0] for x in r)
            rb = max(x[1] for x in r)
            ov = min(rb, it[1]) - max(rt, it[0])
            if ov > 0.5 * min(rb - rt, it[1] - it[0]):
                r.append(it)
                continue
        rows.append([it])
    out = []
    for r in rows:
        out.extend(x[3] for x in sorted(r, key=lambda t: t[2]))
    return out
