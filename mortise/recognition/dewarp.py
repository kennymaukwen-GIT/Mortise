"""紙張攤平（②頁面用）：把彎曲、捲起、有折痕的紙攤平，交出「輸出像素 → 原照片」的對應圖與量測殘差。

    r = dewarp(photo, quad_or_none, params)   # DewarpResult

鐵律在這裡怎麼落實
    學習式模型（UVDoc）只提供兩樣東西：頁面外框曲線與粗位移場，都只當候選與初值；最後的外框由原解析度的
    邊緣剖面量出來，頁內的彎曲由格線與文字行的直線度量出來。每一步都保留對應圖（輸出像素中心 → 原照片座標）
    與殘差，量到的位置都能回推到原照片驗證。平的紙維持透視攤平（研究實測透視攤平在平的紙上誤差為 0，
    UVDoc 反而多 2～5 像素），只有判定彎曲時才攤平。

座標慣例
    與 page_geometry 相同：連續像素座標，原點在影像左上角的「角」，第 i 列第 j 行像素的中心是 (j+0.5, i+0.5)。
    map_x[i, j]、map_y[i, j] 是輸出第 i 列第 j 行像素中心在原照片的位置；交給 cv2.remap 時要各減 0.5。

做法
    1. 平的紙基準：有四角（find_page_quad 成功）時先做透視對應圖，量
       a. 四條邊的弓高：沿直線邊取法線剖面找邊緣點，擬合平滑曲線，量它與直線的最大距離（也採用 find_page_quad 量到的弓高）；
       b. 頁內直線度：透視攤平的縮圖上找長的橫、直格線與文字行，每條擬合三次曲線，量它與直線的最大距離（佔頁寬 %）；
       c. 紙有沒有被四角裁掉：UVDoc 找的外框（精修後、有邊緣支持的部分）是否明顯超出四角。
       三者都沒問題 → 判定為平的紙，只做透視（applied=False）。
    2. 候選外框：UVDoc（MIT 授權，ONNX 在 CPU 上約 0.3 秒）推出 45×31 的網格，取網格的四條邊當候選外框。
       有四角時裁到四角外接框外擴 5% 再推論（研究實測比全圖準）；推出的外框貼到裁切邊時（四角可能只框到紙的一部分）改用全圖重推。
       沒有四角時，以精修後外框的四角當「四角」，同樣做第 1 步的平的紙檢查。
       四角框住的面積不到量到的外框的 75%（框到紙上的一塊）時，改用量到的外框。
       注意：find_page_quad 對彎的紙信心值本來就很低（0.05），那不代表四角錯，所以不拿信心值來判斷。
    3. 外框精修（原解析度）：沿候選曲線每隔約 4 像素取法線剖面，以「內側→外側」色差方向投影的一階導數找邊緣峰值
       （偏好靠近候選的峰、要通過內外顏色檢查），拋物線內插到次像素；每條邊的法線偏移以三次 B 樣條加平滑懲罰、
       Tukey 雙權重迭代擬合（排除手指、文字、陰影等離群點）。回報每邊支持率；量不到的邊用 UVDoc 的推估（或四角連線）
       並寫進 notes，這時 source 記為 uvdoc_prior。
    4. 建對應圖：頁內初值是四角透視（interior_prior="perspective"，預設），加上 Coons 曲面內插的外框修正量
       （精修後的四條外框曲線相對於四角連線的位移，雙線性混合到頁內）。也可以改用 UVDoc 的網格當頁內初值
       （interior_prior="uvdoc"），但合成評測裡比較差（見研究筆記 page-dewarp.md 與本模組的測試）。
    5. 頁內修正（量測式，最多 correct_iters 輪）：在攤平縮圖上找長的橫線（格線、文字行）與直線（格線），
       每條以三次曲線平滑後，量它與直線擬合的差：橫線給垂直位移、直線給水平位移；以粗控制格點的雙線性曲面
       加二階差分平滑懲罰、Tukey 重加權的最小平方擬合成位移場（外框已量過的那兩邊固定為 0），與對應圖合成。
       殘差（各條線平滑後離直線的距離，第 95 百分位）沒有變小就不採用。
    6. 驗證後才採用（每條路徑都驗證，包括紙被四角裁掉、沒有四角）：
       a. 前後比的要是同一組線：攤平後量到的線至少要有攤平前的 70%（線被扭壞就量不到，殘差會假性變小），否則不採用；
       b. 攤平後的殘差比透視攤平大（超過量測雜訊 15% + 0.03 像素）就不採用；
       c. 紙沒有被四角裁掉、頁內有 3 條以上的線時，還要明顯變好（少 max(0.06% 頁寬, 30%)；文字行有字形雜訊，
          修正場會「修掉雜訊」，所以要求相對進步）；紙被裁掉時放寬成「不變差」（攤平多救回一塊紙）；
       d. 頁內完全量不到線時，要有量到的彎曲紙邊（或紙超出四角）才採用。
       不採用就退回透視並寫明原因。量線前，深色底的頁面（字是亮的）先反轉，否則字與字之間的深色底會被當成線。
       沒有四角時，學習式模型的外框精修後至少要有三條邊是量到的「紙與背景交界」（有內外色差），才當四角用
       （pseudo_quad；rectify 會採用，上層應請使用者確認）；否則外框只放在 candidate_outline 當候選，維持整張圖。
       信心值 = 外框支持率 × 頁內殘差項（有修正時用交叉驗證殘差：線分兩組，一組擬合位移場、量另一組的預測誤差）
       × 來源（外框有推估的邊 × 0.5、紙超出四角 × 0.6、四角是自己量的 × 0.8）× 直線度進步幅度。

    輸出尺寸：與 rectify 相同，「最長的那一組對邊」1:1 取樣（量的是外框曲線的弧長）；長寬比優先用 find_page_quad
    由透視反推的值，沒有四角或紙被裁掉時以外框四角用 Zhang–He 方法估。對原照片只做一次 remap（避免兩次內插變模糊）。

UVDoc 權重
    不放進版本庫：第一次用時從 UVDoc 的 GitHub 倉庫（固定 commit）下載 model/best_model.pkl，驗證 SHA-256，
    不需要 torch：以白名單的 unpickler 讀出權重、把 BatchNorm 摺進卷積、用本檔的最小 protobuf 編碼器組成 ONNX，
    存到 mortise.tools.fontfind.cache_dir()/models/uvdoc/（旁邊附 MIT 授權全文），之後直接載入。
    scripts/export_uvdoc_onnx.py 用 torch 官方匯出做交叉比對（網格差異 < 1e-6，torch 不是產品相依）。
    攤平時快取裡沒有模型，就在背景執行緒下載（這張照片先只用量測式，不讓使用者的請求等下載）；部署時可先執行
    python -m mortise.recognition.dewarp --download。下載有總時限，失敗後一小時內不再重試，.part 一律清掉。
    環境變數 MORTISE_OFFLINE=1 或 params["uvdoc"]="cache_only" 時不下載；下載或載入失敗就退回純量測式並寫進 notes。

已知限制
    - 捲起處的透視縮短（字被壓扁、行距變小）直線度量不到；只有 UVDoc 的初值在網格間距上帶有這個資訊，
      沒有 UVDoc 時捲起的那條會被壓縮。弓起的紙兩側的水平壓縮也一樣（只修得直，修不回間距）。
    - 頁內沒有長的格線或文字行（例如整頁照片的海報）時，頁內只能靠外框內插與 UVDoc 初值，信心值較低。
    - UVDoc 在縮成 488×712 的影像上推論，外框會偏內幾個像素；精修需要紙邊有對比。紙與背景完全同色時精修會失敗，
      外框只能用 UVDoc 的推估（notes 會寫明）。
    - 長寬比在捲起時會偏小（捲起的部分在照片裡被壓縮）。
    - 判定「平的紙」的門檻（直線度中位數 0.1%、最大 0.3%，弓高 2 像素）是由合成資料初定，要用真實照片校正。

可單獨升級的點
    - set_dewarper()（page_geometry）可換掉整個攤平函數；本模組的 uvdoc_grid() 可換成別的外框／網格模型。
    - 頁內量測可加入「行高與字高一致性」估捲起處的壓縮率。
"""

from __future__ import annotations

import collections
import hashlib
import io
import json
import math
import os
import pickle
import threading
import time
import urllib.request
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Optional

import cv2
import numpy as np

from .types import PageQuad

DEFAULT_PARAMS: dict[str, Any] = {
    "dewarp_mode": "auto",  # "auto"：判定彎曲才攤平；"force"：一律攤平（評測用）；"off"：只做透視
    "uvdoc": "auto",  # 學習式初值："auto"（快取沒有就在背景下載，這次先不用）、"sync"（在這次請求裡下載，腳本用）、"cache_only"（只用快取）、"off"（不用）
    "uvdoc_retry_s": 3600.0,  # 下載失敗後這麼多秒內不再重試
    "uvdoc_threads": 2,  # UVDoc 推論的執行緒數
    "uvdoc_crop_margin": 0.05,  # 有四角時，裁到四角外接框外擴這個比例再給 UVDoc（研究實測比全圖準）
    "uvdoc_interp": "cubic",  # UVDoc 網格放大成對應圖的內插："cubic" 或 "linear"
    "interior_prior": "perspective",  # 頁內初值："perspective"（四角透視＋外框 Coons 修正；合成評測較準）或 "uvdoc"（UVDoc 網格）
    "download_timeout_s": 120.0,  # 下載權重的總時限（秒；整個下載，不是每次讀取）
    # ---- 平的紙判定
    "bow_px": 2.0,  # 邊的弓高超過 max(bow_px, bow_frac × 邊長) 視為彎曲（原圖像素）
    "bow_frac": 0.002,
    "flat_sag_median_pct": 0.1,  # 頁內直線度：各條線離直線的最大距離（佔頁寬 %）的中位數低於這個……
    "flat_sag_max_pct": 0.3,  # ……而且最大值低於這個，才算平的紙
    "flat_min_lines": 3,  # 至少要有幾條線，直線度判定才有效
    "outside_px": 4.0,  # UVDoc 外框（有邊緣支持的部分）超出四角連線 max(outside_px, outside_frac × 對角線) → 紙被四角裁掉了
    "outside_frac": 0.006,
    # ---- 外框精修（原圖解析度）
    "edge_blur_sigma": 1.0,  # 取剖面前的高斯模糊 σ（像素）
    "edge_search_frac": 0.012,  # 法線方向的搜尋半徑（佔對角線）
    "edge_search_min_px": 6.0,  # 搜尋半徑下限（像素）
    "edge_step_px": 4.0,  # 沿邊取樣間距（像素）
    "edge_max_samples": 400,  # 每條邊取樣點上限
    "edge_end_margin": 0.03,  # 每條邊兩端略過的比例（避開角落）
    "edge_prior_sigma": 0.6,  # 偏好靠近候選的峰：高斯權重的 σ（佔搜尋半徑）
    "edge_min_contrast": 4.0,  # 內外色差低於這個（色階）改用不分方向的梯度大小
    "edge_min_grad": 1.0,  # 邊緣點的最小梯度（色階／像素）
    "edge_segments": 8,  # 每條邊的偏移用幾段三次 B 樣條表示
    "edge_smooth": 3.0,  # B 樣條的二階差分平滑懲罰
    "edge_tukey_c": 4.685,  # Tukey 雙權重常數（以 MAD 尺度為單位）
    "edge_inlier_px": 1.5,  # 離擬合曲線這麼近（或 2.5σ 內）算有邊緣支持
    "min_edge_support": 0.35,  # 每條邊至少要有這個比例有邊緣支持，精修結果才採用
    # ---- 頁內量測與修正（縮圖上）
    "measure_side": 1200,  # 量直線度的縮圖長邊（像素）
    "dark_page_luma": 110.0,  # 頁面灰階中位數低於這個視為深色底（字是亮的），量線前先反轉
    "sauvola_window_frac": 0.025,  # Sauvola 視窗（佔縮圖長邊）
    "sauvola_k": 0.2,
    "rule_min_len_frac": 0.2,  # 格線至少佔頁寬（或頁高）的比例
    "text_min_len_frac": 0.12,  # 文字行至少佔頁寬的比例
    "line_bin_px": 8,  # 沿線取樣的間距（縮圖像素）
    "correct_iters": 2,  # 頁內修正最多幾輪
    "correct_grid": (12, 9),  # 位移場控制格點（列, 欄）
    "correct_smooth": 2.0,  # 位移場二階差分平滑懲罰
    "correct_pin": 5.0,  # 外框已量過的邊固定為 0 的權重
    "correct_min_gain": 0.05,  # 殘差至少要下降這個比例才採用這一輪修正
    "accept_margin_frac": 0.0006,  # 攤平後的殘差要比透視攤平少 max(這個 × 頁寬, accept_rel × 透視殘差)（輸出像素）才算更好
    "accept_rel": 0.3,  # 文字行的直線度有字形雜訊，修正場會「修掉雜訊」；要求相對進步，避免平的紙被誤攤
    "keep_lines_frac": 0.7,  # 攤平後量到的線至少要有攤平前的這個比例（線被攤壞就量不到，殘差會假性變小），否則不採用
    "worse_tol_rel": 0.15,  # 攤平後的殘差比透視攤平大超過「這個比例 + worse_tol_px」就不採用（任何情況都檢查，含紙超出四角）；
    "worse_tol_px": 0.03,  # 這是量測雜訊（字形、取樣）的容許量（輸出像素）
    # ---- 信心值（攤平後）：外框支持率 × 頁內殘差（有修正時用交叉驗證殘差）× 來源 × 直線度進步
    "conf_uvdoc_prior": 0.5,  # 外框有一邊是學習式模型推估的（量不到紙邊）
    "conf_truncated": 0.6,  # 紙超出四角（捲起處的位置與壓縮量量不到）
    "conf_pseudo": 0.8,  # 沒有現成的四角，四角是攤平時自己量的
    "quad_doubt_area": 0.75,  # 四角框住的面積不到量到的外框面積的這個比例（框到紙上的一塊、或把紙切掉一大半）時改用量到的外框
    "quad_doubt_support": 0.7,  # ……而且外框至少三邊量到、平均支持率至少這麼高
    "crop_touch_frac": 0.015,  # UVDoc 的外框貼到裁切邊（這個比例內，且裁切邊不是畫面邊）：四角可能框小了，改用全圖重推
    # ---- 輸出
    "max_output_side": 12000,  # 輸出長邊上限（像素）
    "output_interp": "linear",  # 最後取樣的內插："linear" 或 "cubic"
}


# 設定檔 [page] 裡帶 dewarp_ 前綴、但不是本模組參數的鍵（由 s2 讀，不是給本模組的），不發警告
_EXTERNAL_KEYS = {"dewarp_photos"}


def _p(params: dict | None) -> dict:
    """參數：本模組的鍵直接採用；設定檔的 dewarp_xxx（例如 dewarp_bow_px）去掉前綴對應到 xxx（同時給了不帶前綴的鍵時，以不帶前綴的為準）；
    dewarp_ 開頭卻對不上任何參數的鍵發出警告（拼錯的設定不再被悄悄忽略）。"""
    p = dict(DEFAULT_PARAMS)
    if params:
        for k, v in params.items():
            if k.startswith("dewarp_") and k not in DEFAULT_PARAMS:
                base = k[len("dewarp_"):]
                if base in DEFAULT_PARAMS:
                    if base not in params:
                        p[base] = v
                elif k not in _EXTERNAL_KEYS:
                    import warnings

                    warnings.warn(f"攤平參數 {k} 對不上任何已知參數，已忽略", stacklevel=3)
        p.update({k: v for k, v in params.items() if k in DEFAULT_PARAMS})
    return p


@dataclass
class DewarpResult:
    """攤平結果。applied=False 表示判定為平的紙（或攤平不可信），影像與對應圖就是透視攤平的結果。"""

    image: Optional[np.ndarray]  # 輸出影像（RGB uint8）；沒有任何可用的頁面時為 None
    map_x: Optional[np.ndarray]  # Ho×Wo float32：輸出像素中心 → 原照片 x（連續座標）
    map_y: Optional[np.ndarray]  # Ho×Wo float32：輸出像素中心 → 原照片 y
    applied: bool  # 是否做了透視以外的攤平
    source: str  # "measured"（外框、格線、文字行量出）、"uvdoc_prior"（UVDoc 初值再量測修正）、""（只有透視）
    curved: bool  # 是否判定紙是彎的（含被四角裁掉的捲邊）
    corners: Optional[np.ndarray] = None  # (4, 2) 頁面四角在原照片的位置：左上、右上、右下、左下
    boundary: list[np.ndarray] = field(default_factory=list)  # 四條外框曲線（上、右、下、左；原照片座標，上與下由左到右、左與右由上到下）
    support: list[float] = field(default_factory=list)  # 四條邊的邊緣支持率（0 到 1）
    bow_px: list[float] = field(default_factory=list)  # 四條邊的弓高（原照片像素；量不到為 0）
    residual_px: Optional[float] = None  # 攤平後格線與文字行離直線的殘差（輸出像素，第 95 百分位）
    residual_before_px: Optional[float] = None  # 透視攤平時同樣的殘差（比較用）
    straightness: dict = field(default_factory=dict)  # 直線度細節：線數、各線最大偏離佔頁寬 % 的中位數與最大值（攤平前後）
    confidence: float = 0.0
    homography: Optional[np.ndarray] = None  # 3×3：原照片 → 輸出影像的單應矩陣（由四角求；攤平時只是近似）
    notes: list[str] = field(default_factory=list)
    timings: dict = field(default_factory=dict)  # 各步驟耗時（秒）
    # 沒有給四角（或 find_page_quad 給整張圖）時：由紙邊量出的四角（學習式模型的外框只當候選，精修後至少三邊有量測支持才給）。
    # image 就是用這組四角做的透視攤平（或攤平）結果；rectify 會採用它，上層應把它當成「需要使用者確認」的四角。
    pseudo_quad: Optional[np.ndarray] = None
    measured_sides: int = 0  # 外框有幾條邊是在原照片上量到的（紙與背景的交界，有足夠支持率）
    candidate_outline: list[np.ndarray] = field(default_factory=list)  # 沒有採用、只當候選的外框（學習式模型推估，量測支持不足）；給介面讓使用者確認
    needs_review: bool = False  # 建議請使用者確認外框（信心值低、外框有推估的邊、紙超出四角、或四角是自己量的）

    def maps_npz(self) -> bytes:
        """對應圖存成 npz（map_x、map_y，float32），給 PageSpec.dewarp_map 用。"""
        buf = io.BytesIO()
        np.savez_compressed(buf, map_x=self.map_x.astype(np.float32), map_y=self.map_y.astype(np.float32))
        return buf.getvalue()


# ============================================================ 小工具


def _rgb(image: np.ndarray) -> np.ndarray:
    if image.ndim == 2:
        return np.ascontiguousarray(np.dstack([image] * 3))
    return np.ascontiguousarray(image[..., :3])


def _remap(img: np.ndarray, mx: np.ndarray, my: np.ndarray, interp: int = cv2.INTER_LINEAR, border=cv2.BORDER_REPLICATE) -> np.ndarray:
    """以連續座標取樣（內部換成 OpenCV 的像素中心約定）。"""
    return cv2.remap(img, (mx - 0.5).astype(np.float32), (my - 0.5).astype(np.float32), interp, borderMode=border)


def _homography(src: np.ndarray, dst: np.ndarray) -> np.ndarray:
    from .page_geometry import homography_from_points

    return homography_from_points(np.asarray(src, np.float64), np.asarray(dst, np.float64))


def _apply_h(h: np.ndarray, x: np.ndarray, y: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    d = h[2, 0] * x + h[2, 1] * y + h[2, 2]
    return (h[0, 0] * x + h[0, 1] * y + h[0, 2]) / d, (h[1, 0] * x + h[1, 1] * y + h[1, 2]) / d


def _arclen(pts: np.ndarray) -> float:
    return float(np.sum(np.linalg.norm(np.diff(pts, axis=0), axis=1))) if len(pts) > 1 else 0.0


def _bspline_basis(t: np.ndarray, K: int) -> np.ndarray:
    """均勻三次 B 樣條在 [0, 1] 上分 K 段：回傳 (N, K+3) 的基底矩陣。"""
    t = np.clip(np.asarray(t, np.float64), 0.0, 1.0)
    u = t * K
    seg = np.minimum(np.floor(u).astype(int), K - 1)
    f = u - seg
    w = np.stack([(1 - f) ** 3 / 6, (3 * f**3 - 6 * f**2 + 4) / 6, (-3 * f**3 + 3 * f**2 + 3 * f + 1) / 6, f**3 / 6], 1)
    B = np.zeros((len(t), K + 3))
    rows = np.arange(len(t))
    for k in range(4):
        B[rows, seg + k] = w[:, k]
    return B


def _spd_solve(A: np.ndarray, b: np.ndarray) -> np.ndarray:
    """對稱正定方程組（Cholesky）。numpy 的 solve 在中型矩陣上會觸發多執行緒 BLAS，機器忙時反而慢上千倍。"""
    from scipy.linalg import cho_factor, cho_solve

    return cho_solve(cho_factor(A, check_finite=False), b, check_finite=False)


def _diff2(n: int) -> np.ndarray:
    D = np.zeros((max(n - 2, 0), n))
    for i in range(n - 2):
        D[i, i : i + 3] = [1.0, -2.0, 1.0]
    return D


def _robust_spline(t: np.ndarray, d: np.ndarray, w0: np.ndarray, K: int, lam: float, c: float, iters: int = 8, min_scale: float = 0.25):
    """偏移 d(t) 的穩健平滑擬合（三次 B 樣條＋二階差分懲罰＋Tukey 雙權重 IRLS）。回傳（係數、最終權重、殘差尺度）。"""
    B = _bspline_basis(t, K)
    P = lam * _diff2(K + 3).T @ _diff2(K + 3) + 1e-6 * np.eye(K + 3)
    w = w0.astype(np.float64).copy()
    coef = np.zeros(K + 3)
    s = 99.0
    for it in range(iters):
        coef = _spd_solve(B.T @ (w[:, None] * B) + P, B.T @ (w * d))
        r = d - B @ coef
        act = w > 0
        if act.sum() < 4:
            break
        mad = max(1.4826 * float(np.median(np.abs(r[act]))), min_scale)
        s = mad if it == 0 else min(s, mad)  # 尺度只縮不放，避免被離群點拉大
        u = r / (c * s)
        w_new = w0 * np.where(np.abs(u) < 1, (1 - u * u) ** 2, 0.0)
        if (w_new > 0).sum() < 4:
            break
        w = w_new
    return coef, w, s


def _eval_spline(coef: np.ndarray, t: np.ndarray, K: int) -> np.ndarray:
    return _bspline_basis(t, K) @ coef


def sauvola(gray: np.ndarray, window: int, k: float = 0.2, R: float = 128.0) -> np.ndarray:
    """Sauvola 二值化：回傳「墨跡」遮罩（比局部門檻暗的像素）。window 為奇數視窗邊長。"""
    g = gray.astype(np.float32)
    w = max(3, int(window) | 1)
    m = cv2.boxFilter(g, -1, (w, w), borderType=cv2.BORDER_REFLECT)
    m2 = cv2.boxFilter(g * g, -1, (w, w), borderType=cv2.BORDER_REFLECT)
    sd = np.sqrt(np.maximum(m2 - m * m, 0.0))
    return g < m * (1.0 + k * (sd / R - 1.0))


def guided_filter(guide: np.ndarray, src: np.ndarray, r: int, eps: float) -> np.ndarray:
    """引導濾波（He 等人 2010，保邊平滑）。guide 為單通道 float32，src 可為多通道；r 為視窗半徑（像素）。"""
    I = guide.astype(np.float32)
    ksz = (2 * r + 1, 2 * r + 1)
    box = lambda a: cv2.boxFilter(a, -1, ksz, borderType=cv2.BORDER_REFLECT)  # noqa: E731
    mI = box(I)
    vI = box(I * I) - mI * mI
    chans = [src] if src.ndim == 2 else [src[..., k] for k in range(src.shape[2])]
    out = []
    for P in chans:
        P = P.astype(np.float32)
        mP = box(P)
        cov = box(I * P) - mI * mP
        a = cov / (vI + eps)
        b = mP - a * mI
        out.append(box(a) * I + box(b))
    return out[0] if src.ndim == 2 else np.stack(out, -1)


# ============================================================ 外框：邊緣剖面與精修（原圖解析度）


def _normals(pts: np.ndarray, outward_ref: np.ndarray) -> np.ndarray:
    """折線各點的單位法向量，方向朝外（遠離 outward_ref 這個頁內點）。"""
    tg = np.gradient(pts, axis=0)
    tg /= np.maximum(np.linalg.norm(tg, axis=1, keepdims=True), 1e-9)
    n = np.stack([tg[:, 1], -tg[:, 0]], 1)
    if np.mean(np.sum((pts - outward_ref) * n, axis=1)) < 0:
        n = -n
    return n


def _edge_offsets(img_f: np.ndarray, pts: np.ndarray, nout: np.ndarray, R: float, p: dict) -> dict:
    """沿每個取樣點的外法線方向取 ±R 的剖面，找紙邊（內側→外側）的次像素位置。
    回傳 offset（沿外法線的偏移，像素）、peak（梯度強度）、valid（剖面在畫面內且通過內外顏色檢查）、contrast。"""
    H, W = img_f.shape[:2]
    hstep = 0.5
    offs = np.arange(-R, R + hstep / 2, hstep)
    X = pts[:, 0:1] + offs[None, :] * nout[:, 0:1]
    Y = pts[:, 1:2] + offs[None, :] * nout[:, 1:2]
    inside = np.all((X >= 0.5) & (X <= W - 0.5) & (Y >= 0.5) & (Y <= H - 0.5), axis=1)
    P = _remap(img_f, X, Y)
    if P.ndim == 2:
        P = P[:, :, None]
    M = P.shape[1]
    q = max(2, M // 5)
    in_ref = np.median(P[inside, :q].reshape(-1, P.shape[2]), axis=0) if inside.sum() >= 3 else np.zeros(P.shape[2])
    out_ref = np.median(P[inside, -q:].reshape(-1, P.shape[2]), axis=0) if inside.sum() >= 3 else np.zeros(P.shape[2])
    diff = out_ref - in_ref
    contrast = float(np.linalg.norm(diff))
    D = (P[:, 2:] - P[:, :-2]) / (2 * hstep)
    if contrast >= p["edge_min_contrast"]:
        g = D @ (diff / contrast)
    else:
        g = np.linalg.norm(D, axis=2)
    so = offs[1:-1]
    prior = np.exp(-0.5 * (so / max(1e-6, p["edge_prior_sigma"] * R)) ** 2)
    score = np.maximum(g, 0) * (0.35 + 0.65 * prior[None, :])
    j = np.clip(np.argmax(score[:, 1:-1], axis=1) + 1, 1, g.shape[1] - 2)
    idx = np.arange(len(pts))
    y0, y1, y2 = g[idx, j - 1], g[idx, j], g[idx, j + 1]
    den = y0 - 2 * y1 + y2
    delta = np.where(np.abs(den) > 1e-9, 0.5 * (y0 - y2) / np.where(np.abs(den) > 1e-9, den, 1.0), 0.0)
    delta = np.clip(delta, -0.5, 0.5)
    s_hat = so[j] + delta * hstep
    peak = y1
    # 內外顏色檢查：峰的內側像紙、外側像背景（沒有色差時跳過）
    ok_color = np.ones(len(pts), bool)
    if contrast >= p["edge_min_contrast"]:
        k_in = np.clip(j - 4, 0, M - 1)
        k_out = np.clip(j + 6, 0, M - 1)
        c_in = P[idx, k_in]
        c_out = P[idx, k_out]
        ok_color = (np.linalg.norm(c_in - in_ref, axis=1) < np.linalg.norm(c_in - out_ref, axis=1) + 0.25 * contrast) & (
            np.linalg.norm(c_out - out_ref, axis=1) < np.linalg.norm(c_out - in_ref, axis=1) + 0.25 * contrast
        )
    med = float(np.median(peak[inside])) if inside.any() else 0.0
    valid = inside & ok_color & (peak >= p["edge_min_grad"]) & (peak >= 0.25 * med)
    return {"offset": s_hat, "peak": peak, "valid": valid, "inside": inside, "contrast": contrast}


def refine_side(img_f: np.ndarray, curve_fn: Callable[[np.ndarray], np.ndarray], inner_ref: np.ndarray, R: float, p: dict) -> dict:
    """精修一條外框邊。curve_fn(t) 給候選曲線上參數 t（0 到 1）的點（原圖座標）。
    回傳 coef（法線偏移的 B 樣條係數）、support、bow（精修後的曲線離兩端連線的最大距離）、sigma、n。"""
    K = int(p["edge_segments"])
    tt = np.linspace(0, 1, 201)
    dense = curve_fn(tt)
    L = _arclen(dense)
    n = int(np.clip(L / p["edge_step_px"], 24, p["edge_max_samples"]))
    m = p["edge_end_margin"]
    t = np.linspace(m, 1 - m, n)
    pts = curve_fn(t)
    nout = _normals(pts, inner_ref)
    e = _edge_offsets(img_f, pts, nout, R, p)
    w0 = np.where(e["valid"], np.sqrt(np.maximum(e["peak"], 0)), 0.0)
    res = {"coef": np.zeros(K + 3), "support": 0.0, "bow": 0.0, "sigma": 99.0, "n": int(e["valid"].sum()), "contrast": e["contrast"],
           "visible": float(e["inside"].mean()), "length": L}
    if (w0 > 0).sum() < 8:
        return res
    coef, w, s = _robust_spline(t, e["offset"], w0, K, p["edge_smooth"], p["edge_tukey_c"])
    fit = _eval_spline(coef, t, K)
    r = np.abs(e["offset"] - fit)
    tol = max(p["edge_inlier_px"], 2.5 * s)
    vis = max(int(e["inside"].sum()), 1)
    support = float(np.sum((r <= tol) & e["valid"])) / vis
    # 弓高：精修後的曲線（有支持的範圍內）離兩端連線的最大距離
    ref = curve_fn(t) + _eval_spline(coef, t, K)[:, None] * nout
    good = (r <= tol) & e["valid"]
    bow = 0.0
    if good.sum() >= 8:
        a, b = ref[0], ref[-1]
        dvec = (b - a) / max(np.linalg.norm(b - a), 1e-9)
        nrm = np.array([-dvec[1], dvec[0]])
        dist = (ref - a) @ nrm
        lin = np.polyval(np.polyfit(t[good], dist[good], 1), t)
        bow = float(np.max(np.abs(dist - lin)[good]))
    res.update({"coef": coef, "support": support, "bow": bow, "sigma": float(s), "t": t, "pts": pts, "nout": nout, "fit": fit, "good": good})
    return res


# ============================================================ 頁內：格線與文字行的直線度


def _ink_mask(gray: np.ndarray, p: dict) -> np.ndarray:
    win = int(round(p["sauvola_window_frac"] * max(gray.shape)))
    ink = sauvola(gray, win, p["sauvola_k"])
    return ink & (gray < 200)


def _line_points(mask: np.ndarray, weight: np.ndarray, lbl: np.ndarray, k: int, bbox, bin_px: int) -> Optional[tuple[np.ndarray, np.ndarray]]:
    """連通元件 k 的中心線：沿 x 分箱，每箱取（以墨色深淺加權的）y 重心。"""
    x, y, w, h = bbox
    sub = (lbl[y : y + h, x : x + w] == k)
    wt = weight[y : y + h, x : x + w] * sub
    col = wt.sum(axis=0)
    ys = np.arange(h, dtype=np.float64)[:, None] + 0.5
    xs_out, ys_out = [], []
    for b0 in range(0, w, bin_px):
        cw = col[b0 : b0 + bin_px]
        if cw.sum() <= 0:
            continue
        blk = wt[:, b0 : b0 + bin_px]
        xs_out.append(x + b0 + 0.5 * min(bin_px, w - b0))
        ys_out.append(y + float((blk * ys).sum() / blk.sum()))
    if len(xs_out) < 5:
        return None
    return np.array(xs_out), np.array(ys_out)


def find_lines(rgb: np.ndarray, p: dict) -> dict:
    """在（攤平後的）縮圖上找長的橫線與直線：格線、文字行。座標為這張縮圖的連續像素座標。
    回傳 {"h": [(x, y), ...], "v": [(x, y), ...]}（直線的 x、y 對調存：沿 y 取樣，值是 x），以及 text_height。"""
    gray = cv2.cvtColor(rgb, cv2.COLOR_RGB2GRAY) if rgb.ndim == 3 else rgb
    Hs, Ws = gray.shape
    if float(np.median(gray)) < p.get("dark_page_luma", 110.0):
        # 深色底（深色投影片）：字是亮的。不反過來的話，字與字之間的深色底會被當成墨跡、量出假的「線」
        gray = (255 - gray).astype(gray.dtype)
    ink = _ink_mask(gray, p).astype(np.uint8)
    weight = np.clip(255.0 - gray.astype(np.float32), 0, None)
    bin_px = int(p["line_bin_px"])
    out: dict[str, list] = {"h": [], "v": [], "kind_h": []}
    rules = np.zeros_like(ink)
    for axis in ("h", "v"):
        m = ink if axis == "h" else ink.T
        wgt = weight if axis == "h" else weight.T
        Wd = m.shape[1]
        L = max(15, Wd // 40)
        op = cv2.morphologyEx(m, cv2.MORPH_OPEN, cv2.getStructuringElement(cv2.MORPH_RECT, (L, 1)))
        op = cv2.morphologyEx(op, cv2.MORPH_CLOSE, cv2.getStructuringElement(cv2.MORPH_RECT, (L, 3)))
        n, lbl, st, _ = cv2.connectedComponentsWithStats(op, connectivity=8)
        for k in range(1, n):
            x, y, w, h, a = st[k]
            if w < p["rule_min_len_frac"] * Wd or h > max(12, 0.04 * w):
                continue
            pts = _line_points(op, wgt * (m > 0), lbl, k, (x, y, w, h), bin_px)
            if pts is not None:
                out[axis].append(pts)
                if axis == "h":
                    out["kind_h"].append("rule")
        rules |= (op if axis == "h" else op.T)
    # 文字行：去掉格線後的墨跡，橫向膨脹成一條條
    txt = ink & (cv2.dilate(rules, np.ones((3, 3), np.uint8)) == 0)
    n, lbl, st, _ = cv2.connectedComponentsWithStats(txt, connectivity=8)
    hs = st[1:, 3][(st[1:, 4] >= 6) & (st[1:, 3] >= 4)] if n > 1 else np.array([])
    th = float(np.median(hs)) if len(hs) else 10.0
    th = float(np.clip(th, 4, Hs / 20))
    kx = max(3, int(round(1.6 * th)))
    blob = cv2.morphologyEx(txt, cv2.MORPH_CLOSE, cv2.getStructuringElement(cv2.MORPH_RECT, (kx, 1)))
    n, lbl, st, _ = cv2.connectedComponentsWithStats(blob, connectivity=8)
    for k in range(1, n):
        x, y, w, h, a = st[k]
        if w < p["text_min_len_frac"] * Ws or h > 3.0 * th or w < 6 * h:
            continue
        pts = _line_points(blob, weight * (txt > 0), lbl, k, (x, y, w, h), max(bin_px, int(round(1.5 * th))))
        if pts is not None:
            out["h"].append(pts)
            out["kind_h"].append("text")
    out["text_height"] = th
    return out


def _poly_dev(x: np.ndarray, y: np.ndarray, c: float = 4.685, iters: int = 4) -> Optional[dict]:
    """一條線：穩健三次擬合（平滑）與直線擬合，回傳兩者之差（平滑後離直線的距離）與直線係數。"""
    if len(x) < 5:
        return None
    x0, sx = float(x.mean()), float(max(np.ptp(x), 1e-6) / 2)
    u = (x - x0) / sx
    w = np.ones_like(u)
    deg = 3 if len(x) >= 8 else 2
    for _ in range(iters):
        cs = np.polyfit(u, y, deg, w=np.sqrt(w))
        r = y - np.polyval(cs, u)
        s = max(1.4826 * float(np.median(np.abs(r[w > 0]))), 0.3) if (w > 0).any() else 1.0
        z = r / (c * s)
        w = np.where(np.abs(z) < 1, (1 - z * z) ** 2, 0.0)
        if (w > 0).sum() < deg + 2:
            w = np.ones_like(u)
            break
    smooth = np.polyval(cs, u)
    cl = np.polyfit(u, smooth, 1, w=np.sqrt(np.maximum(w, 1e-3)))
    lin = np.polyval(cl, u)
    return {"x": x, "smooth": smooth, "lin": lin, "dev": smooth - lin, "w": w, "n": int((w > 0).sum())}


def straightness(lines: dict, width: float, height: float) -> dict:
    """直線度：每條線平滑後離直線的最大距離（佔頁寬 %）的中位數與最大值，以及所有取樣點偏離的第 95 百分位（像素）。"""
    devs_all, sag = [], []
    per = {"h": [], "v": []}
    for axis in ("h", "v"):
        for x, y in lines[axis]:
            d = _poly_dev(x, y)
            if d is None:
                continue
            per[axis].append(d)
            ok = d["w"] > 0
            devs_all.append(np.abs(d["dev"][ok]))
            sag.append(float(np.max(np.abs(d["dev"][ok]))) / (width if axis == "h" else height) * 100.0)
    n = len(sag)
    res = {"n_lines": n, "n_h": len(per["h"]), "n_v": len(per["v"]), "per": per}
    if n:
        allv = np.concatenate(devs_all) if devs_all else np.zeros(1)
        srt = sorted(sag)
        res.update({"sag_median_pct": float(np.median(sag)), "sag_max_pct": float(srt[-1]), "sag_2nd_pct": float(srt[-2]) if n >= 2 else 0.0,
                    "p95_px": float(np.percentile(allv, 95))})
    return res


def _fit_field(xs: np.ndarray, ys: np.ndarray, vals: np.ndarray, wts: np.ndarray, W: float, H: float, grid: tuple[int, int], lam: float,
               pin_rows: float, pin_cols: float, c: float = 4.685) -> np.ndarray:
    """散佈的位移樣本 → 粗控制格點上的雙線性曲面（二階差分平滑、Tukey 重加權的最小平方）。
    pin_rows > 0：上、下兩列以這個權重固定為 0；pin_cols > 0：左、右兩欄固定為 0。回傳 (gy, gx) 的格點值。"""
    gy, gx = grid
    U = gx * gy
    fx = np.clip(xs / W * (gx - 1), 0, gx - 1 - 1e-9)
    fy = np.clip(ys / H * (gy - 1), 0, gy - 1 - 1e-9)
    ix, iy = np.floor(fx).astype(int), np.floor(fy).astype(int)
    ax, ay = fx - ix, fy - iy
    idx = [iy * gx + ix, iy * gx + ix + 1, (iy + 1) * gx + ix, (iy + 1) * gx + ix + 1]
    bw = [(1 - ax) * (1 - ay), ax * (1 - ay), (1 - ax) * ay, ax * ay]
    # 平滑懲罰（二階差分）與固定點：常數矩陣
    reg = np.zeros((U, U))
    for r_ in range(gy):
        for c_ in range(gx):
            for d in ((0, 1), (1, 0)):
                r0, c0 = r_ - d[0], c_ - d[1]
                r2, c2 = r_ + d[0], c_ + d[1]
                if 0 <= r0 and 0 <= c0 and r2 < gy and c2 < gx:
                    v = np.zeros(U)
                    v[r0 * gx + c0], v[r_ * gx + c_], v[r2 * gx + c2] = 1.0, -2.0, 1.0
                    reg += lam * np.outer(v, v)
    if pin_rows > 0:
        for i in [r_ * gx + c_ for r_ in (0, gy - 1) for c_ in range(gx)]:
            reg[i, i] += pin_rows
    if pin_cols > 0:
        for i in [r_ * gx + c_ for r_ in range(gy) for c_ in (0, gx - 1)]:
            reg[i, i] += pin_cols
    reg += 1e-6 * np.eye(U)
    w = wts.astype(np.float64).copy()
    sol = np.zeros(U)
    for _ in range(3):
        AtA = reg.copy()
        Atv = np.zeros(U)
        for a in range(4):
            Atv += np.bincount(idx[a], weights=w * bw[a] * vals, minlength=U)
            for b in range(4):
                np.add.at(AtA, (idx[a], idx[b]), w * bw[a] * bw[b])
        sol = _spd_solve(AtA, Atv)
        pred = sum(bw[a] * sol[idx[a]] for a in range(4))
        r = vals - pred
        s = max(1.4826 * float(np.median(np.abs(r[w > 0]))), 0.3) if (w > 0).any() else 1.0
        z = r / (c * s)
        w_new = wts * np.where(np.abs(z) < 1, (1 - z * z) ** 2, 0.0)
        if (w_new > 0).sum() < 6:
            break
        w = w_new
    return sol.reshape(gy, gx)


def _upsample_grid(g: np.ndarray, Wo: int, Ho: int) -> np.ndarray:
    """控制格點（角點對齊頁面四角）→ 每個輸出像素中心的值（雙三次）。"""
    gy, gx = g.shape
    xs = (np.arange(Wo, dtype=np.float32) + 0.5) / Wo * (gx - 1)
    ys = (np.arange(Ho, dtype=np.float32) + 0.5) / Ho * (gy - 1)
    mx, my = np.meshgrid(xs, ys)
    return cv2.remap(g.astype(np.float32), mx, my, cv2.INTER_CUBIC, borderMode=cv2.BORDER_REPLICATE)


# ============================================================ UVDoc（學習式初值；只當候選）

UVDOC_COMMIT = "4c9b82b537057aff2526e6dd118a847cdd072e82"
UVDOC_URL = f"https://raw.githubusercontent.com/tanguymagne/UVDoc/{UVDOC_COMMIT}/model/best_model.pkl"
UVDOC_SHA256 = "7e90861b8a516eb4bc51f84bd889cb77275743d2d1d3ca8091951ec9f2b7da23"
UVDOC_SIZE = 32158393
UVDOC_INPUT = (488, 712)  # 模型輸入（寬, 高）
UVDOC_LICENSE = """UVDoc（https://github.com/tanguymagne/UVDoc）的程式碼與權重以 MIT 授權釋出；Mortise 只在第一次使用時下載權重、
在本機換成 ONNX，沒有修改模型。授權全文如下：

MIT License

Copyright (c) 2023 Tanguy MAGNE

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
"""


class _Storage:
    def __init__(self, dtype):
        self.dtype = dtype


_TORCH_DTYPES = {"FloatStorage": np.float32, "LongStorage": np.int64, "DoubleStorage": np.float64, "IntStorage": np.int32, "HalfStorage": np.float16}


def _rebuild_tensor(storage, offset, size, stride, *args):
    arr, itemsize = storage
    out = np.lib.stride_tricks.as_strided(arr[offset:], shape=tuple(size), strides=tuple(s * itemsize for s in stride))
    return np.array(out)


class _TorchUnpickler(pickle.Unpickler):
    """只認得 torch.save 的張量格式（白名單）：不需要 torch，也不會執行檔案裡的任意程式。"""

    def __init__(self, f, zf: zipfile.ZipFile, prefix: str):
        super().__init__(f)
        self._zf, self._prefix = zf, prefix

    def find_class(self, module, name):
        if (module, name) == ("collections", "OrderedDict"):
            return collections.OrderedDict
        if (module, name) == ("torch._utils", "_rebuild_tensor_v2"):
            return _rebuild_tensor
        if module == "torch" and name in _TORCH_DTYPES:
            return _Storage(_TORCH_DTYPES[name])
        raise pickle.UnpicklingError(f"權重檔裡有不認得的物件 {module}.{name}，拒絕載入")

    def persistent_load(self, pid):
        kind, storage, key, _loc, numel = pid
        if kind != "storage" or not isinstance(storage, _Storage):
            raise pickle.UnpicklingError("權重檔格式不對")
        raw = self._zf.read(f"{self._prefix}/data/{key}")
        arr = np.frombuffer(raw, dtype=np.dtype(storage.dtype).newbyteorder("<"))[: int(numel)]
        return arr, arr.itemsize


def load_torch_state_dict(path: Path) -> dict[str, np.ndarray]:
    """讀 torch.save 存的檢查點（zip 格式），回傳 model_state（名稱 → numpy 陣列）。不需要安裝 torch。"""
    with zipfile.ZipFile(path) as zf:
        pk = next(n for n in zf.namelist() if n.endswith("/data.pkl"))
        prefix = pk[: -len("/data.pkl")]
        obj = _TorchUnpickler(io.BytesIO(zf.read(pk)), zf, prefix).load()
    state = obj.get("model_state", obj) if isinstance(obj, dict) else obj
    return {k: np.asarray(v) for k, v in state.items()}


# ---- 最小的 ONNX（protobuf）編碼器：只寫本模型用到的欄位


def _varint(n: int) -> bytes:
    n &= (1 << 64) - 1
    out = bytearray()
    while True:
        b = n & 0x7F
        n >>= 7
        if n:
            out.append(b | 0x80)
        else:
            out.append(b)
            return bytes(out)


def _f_varint(field: int, n: int) -> bytes:
    return _varint(field << 3) + _varint(n)


def _f_bytes(field: int, b: bytes) -> bytes:
    return _varint((field << 3) | 2) + _varint(len(b)) + b


def _f_str(field: int, s: str) -> bytes:
    return _f_bytes(field, s.encode("utf-8"))


def _onnx_tensor(name: str, arr: np.ndarray) -> bytes:
    arr = np.ascontiguousarray(arr)
    dtype = {np.dtype(np.float32): 1, np.dtype(np.int64): 7}[arr.dtype]
    out = b"".join(_f_varint(1, int(d)) for d in arr.shape)
    return out + _f_varint(2, dtype) + _f_str(8, name) + _f_bytes(9, arr.astype(arr.dtype.newbyteorder("<")).tobytes())


def _onnx_attr_ints(name: str, vals) -> bytes:
    return _f_str(1, name) + b"".join(_f_varint(8, int(v)) for v in vals) + _f_varint(20, 7)


def _onnx_attr_int(name: str, v: int) -> bytes:
    return _f_str(1, name) + _f_varint(3, int(v)) + _f_varint(20, 2)


def _onnx_attr_str(name: str, v: str) -> bytes:
    return _f_str(1, name) + _f_bytes(4, v.encode()) + _f_varint(20, 3)


def _onnx_node(op: str, inputs: list[str], outputs: list[str], attrs: list[bytes] = ()) -> bytes:
    out = b"".join(_f_str(1, i) for i in inputs) + b"".join(_f_str(2, o) for o in outputs) + _f_str(4, op)
    return out + b"".join(_f_bytes(5, a) for a in attrs)


def _onnx_value_info(name: str, shape) -> bytes:
    dims = b"".join(_f_bytes(1, _f_varint(1, int(d))) for d in shape)
    tensor_type = _f_varint(1, 1) + _f_bytes(2, dims)
    return _f_str(1, name) + _f_bytes(2, _f_bytes(1, tensor_type))


class _OnnxBuilder:
    def __init__(self, state: dict[str, np.ndarray]):
        self.s = state
        self.nodes: list[bytes] = []
        self.inits: list[bytes] = []
        self.n = 0

    def _name(self, base: str) -> str:
        self.n += 1
        return f"{base}_{self.n}"

    def conv(self, x: str, wkey: str, bkey: Optional[str], bn: Optional[str], stride=1, pad=0, dilation=1, reflect=False) -> str:
        """卷積（BatchNorm 摺進權重與偏移）。"""
        w = self.s[wkey].astype(np.float64)
        b = self.s[bkey].astype(np.float64) if bkey else np.zeros(w.shape[0])
        if bn:
            g, beta = self.s[bn + ".weight"].astype(np.float64), self.s[bn + ".bias"].astype(np.float64)
            mean, var = self.s[bn + ".running_mean"].astype(np.float64), self.s[bn + ".running_var"].astype(np.float64)
            k = g / np.sqrt(var + 1e-5)
            w = w * k[:, None, None, None]
            b = (b - mean) * k + beta
        wn, bn_ = self._name("w"), self._name("b")
        self.inits += [_onnx_tensor(wn, w.astype(np.float32)), _onnx_tensor(bn_, b.astype(np.float32))]
        if reflect and pad:
            pn = self._name("pads")
            self.inits.append(_onnx_tensor(pn, np.array([0, 0, pad, pad, 0, 0, pad, pad], np.int64)))
            y = self._name("pad")
            self.nodes.append(_onnx_node("Pad", [x, pn], [y], [_onnx_attr_str("mode", "reflect")]))
            x, pad = y, 0
        kh = w.shape[2]
        out = self._name("conv")
        attrs = [_onnx_attr_ints("kernel_shape", [kh, kh]), _onnx_attr_ints("strides", [stride, stride]), _onnx_attr_ints("pads", [pad] * 4),
                 _onnx_attr_ints("dilations", [dilation, dilation]), _onnx_attr_int("group", 1)]
        self.nodes.append(_onnx_node("Conv", [x, wn, bn_], [out], attrs))
        return out

    def op(self, op: str, inputs: list[str], attrs: list[bytes] = ()) -> str:
        out = self._name(op.lower())
        self.nodes.append(_onnx_node(op, inputs, [out], attrs))
        return out

    def relu(self, x: str) -> str:
        return self.op("Relu", [x])

    def prelu(self, x: str, key: str) -> str:
        sn = self._name("slope")
        self.inits.append(_onnx_tensor(sn, self.s[key].astype(np.float32).reshape(-1, 1, 1)))
        return self.op("PRelu", [x, sn])

    def model(self, inp: str, out: str, out_shape) -> bytes:
        graph = b"".join(_f_bytes(1, n) for n in self.nodes) + _f_str(2, "uvdoc2d") + b"".join(_f_bytes(5, t) for t in self.inits)
        graph += _f_bytes(11, _onnx_value_info(inp, (1, 3, UVDOC_INPUT[1], UVDOC_INPUT[0]))) + _f_bytes(12, _onnx_value_info(out, out_shape))
        opset = _f_str(1, "") + _f_varint(2, 17)
        return _f_varint(1, 8) + _f_str(2, "mortise") + _f_bytes(7, graph) + _f_bytes(8, opset)


def build_uvdoc_onnx(state: dict[str, np.ndarray]) -> bytes:
    """依 UVDoc 的網路結構（model.py 的 UVDocnet，num_filter=32、kernel 5）組出只含 2D 網格輸出的 ONNX。"""
    b = _OnnxBuilder(state)
    x = "img"
    x = b.relu(b.conv(x, "resnet_head.0.weight", None, "resnet_head.1", stride=2, pad=2))
    x = b.relu(b.conv(x, "resnet_head.3.weight", None, "resnet_head.4", stride=2, pad=2))
    for li, (nblocks, stride) in enumerate(((3, 1), (4, 2), (6, 2)), start=1):
        for bi in range(nblocks):
            pre = f"resnet_down.layer{li}.{bi}"
            top = bi == 0
            if top:
                c1 = b.relu(b.conv(x, f"{pre}.conv1.weight", f"{pre}.conv1.bias", f"{pre}.bn1", stride=stride, pad=2))
                c2 = b.conv(c1, f"{pre}.conv2.weight", f"{pre}.conv2.bias", f"{pre}.bn2", pad=2)
            else:
                c1 = b.relu(b.conv(x, f"{pre}.conv1.0.weight", f"{pre}.conv1.0.bias", f"{pre}.bn1", pad=6, dilation=3))
                c2 = b.conv(c1, f"{pre}.conv2.0.weight", f"{pre}.conv2.0.bias", f"{pre}.bn2", pad=6, dilation=3)
            res = x
            if f"{pre}.downsample.0.weight" in state:
                res = b.conv(x, f"{pre}.downsample.0.weight", f"{pre}.downsample.0.bias", f"{pre}.downsample.1", stride=stride, pad=2)
            x = b.relu(b.op("Add", [c2, res]))
    bridges = []
    for name, dils in (("bridge_1", [1]), ("bridge_2", [2]), ("bridge_3", [5]), ("bridge_4", [8, 3, 2]), ("bridge_5", [12, 7, 4]), ("bridge_6", [18, 12, 6])):
        y = x
        for k, d in enumerate(dils):
            y = b.relu(b.conv(y, f"{name}.{k}.0.weight", None, f"{name}.{k}.1", pad=d, dilation=d))
        bridges.append(y)
    cat = b.op("Concat", bridges, [_onnx_attr_int("axis", 1)])
    br = b.relu(b.conv(cat, "bridge_concat.0.weight", None, "bridge_concat.1"))
    y = b.conv(br, "out_point_positions2D.0.weight", None, "out_point_positions2D.1", pad=2, reflect=True)
    y = b.prelu(y, "out_point_positions2D.2.weight")
    y = b.conv(y, "out_point_positions2D.3.weight", "out_point_positions2D.3.bias", None, pad=2, reflect=True)
    b.nodes.append(_onnx_node("Identity", [y], ["grid2d"]))
    return b.model("img", "grid2d", (1, 2, 45, 31))


_UV_LOCK = threading.Lock()
_UV_SESSIONS: dict[str, Any] = {}


def uvdoc_model_dir() -> Path:
    from ..tools.fontfind import cache_dir

    return cache_dir() / "models" / "uvdoc"


def _sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _download(url: str, dest: Path, sha256: str, size: int, timeout: float) -> None:
    """下載到 dest（先寫 .part，驗證 SHA-256 與大小後才改名）。timeout 是整個下載的時限（秒），不是每個 socket 操作的；
    任何失敗（逾時、太大、SHA 不符、連線中斷）都會刪掉 .part。"""
    tmp = dest.with_name(dest.name + ".part")
    h = hashlib.sha256()
    n = 0
    deadline = time.monotonic() + timeout
    req = urllib.request.Request(url, headers={"User-Agent": "mortise-dewarp"})
    ok = False
    try:
        with urllib.request.urlopen(req, timeout=min(timeout, 30.0)) as r, open(tmp, "wb") as f:
            while True:
                if time.monotonic() > deadline:
                    raise TimeoutError(f"下載超過 {timeout:.0f} 秒")
                chunk = r.read(1 << 20)
                if not chunk:
                    break
                n += len(chunk)
                if n > size + (1 << 20):
                    raise ValueError("下載的檔案比預期大")
                f.write(chunk)
                h.update(chunk)
        if h.hexdigest() != sha256 or n != size:
            raise ValueError(f"下載的權重 SHA-256 不符（{h.hexdigest()[:12]}…，預期 {sha256[:12]}…）")
        tmp.replace(dest)
        ok = True
    finally:
        if not ok:
            tmp.unlink(missing_ok=True)


_FAIL_FILE = "download_failed.json"


def _cached_uvdoc(d: Path) -> Optional[Path]:
    """快取裡已有、而且驗證過的 ONNX 路徑；沒有就 None。"""
    onnx = d / f"uvdoc2d-{UVDOC_SHA256[:12]}.onnx"
    meta = d / "uvdoc2d.json"
    if onnx.exists() and meta.exists():
        try:
            m = json.loads(meta.read_text(encoding="utf-8"))
            if m.get("source_sha256") == UVDOC_SHA256 and m.get("onnx_sha256") == _sha256_file(onnx):
                return onnx
        except (OSError, ValueError):
            pass
    return None


def _recent_failure(d: Path, retry_s: float) -> Optional[str]:
    """retry_s 秒內下載失敗過：回傳當時的錯誤（不再重試，免得每張照片都卡在下載）。"""
    try:
        m = json.loads((d / _FAIL_FILE).read_text(encoding="utf-8"))
        if time.time() - float(m.get("time", 0)) < retry_s:
            return str(m.get("error", ""))
    except (OSError, ValueError):
        pass
    return None


def ensure_uvdoc_model(params: dict | None = None) -> tuple[Optional[Path], list[str]]:
    """確保快取裡有 UVDoc 的 ONNX（沒有就下載權重、驗證 SHA-256、在本機組成 ONNX）。回傳（路徑或 None、notes）。
    這個函式會在呼叫的執行緒裡下載（安裝、部署時預先下載用：python -m mortise.recognition.dewarp --download）；
    攤平時預設改在背景下載（見 _uvdoc_session），不讓使用者的請求等下載。下載失敗後 uvdoc_retry_s 秒內不再重試。"""
    p = _p(params)
    mode = p["uvdoc"]
    if mode == "off":
        return None, ["學習式初值（UVDoc）已關閉，只用量測式攤平。"]
    d = uvdoc_model_dir()
    onnx = _cached_uvdoc(d)
    if onnx is not None:
        return onnx, []
    onnx = d / f"uvdoc2d-{UVDOC_SHA256[:12]}.onnx"
    meta = d / "uvdoc2d.json"
    if mode == "cache_only" or os.environ.get("MORTISE_OFFLINE") == "1":
        return None, ["快取裡沒有學習式初值模型（UVDoc），而且設定為不下載：只用量測式攤平。"]
    err = _recent_failure(d, float(p["uvdoc_retry_s"]))
    if err is not None and not (params or {}).get("_force_retry"):
        return None, [f"學習式初值（UVDoc）無法取得（{err}；{p['uvdoc_retry_s'] / 60:.0f} 分鐘內不再重試），只用量測式攤平。"]
    try:
        d.mkdir(parents=True, exist_ok=True)
        pkl = d / "best_model.pkl"
        if not (pkl.exists() and _sha256_file(pkl) == UVDOC_SHA256):
            _download(UVDOC_URL, pkl, UVDOC_SHA256, UVDOC_SIZE, float(p["download_timeout_s"]))
        data = build_uvdoc_onnx(load_torch_state_dict(pkl))
        tmp = onnx.with_name(onnx.name + ".part")
        tmp.write_bytes(data)
        tmp.replace(onnx)
        (d / "LICENSE-UVDoc.txt").write_text(UVDOC_LICENSE, encoding="utf-8")
        meta.write_text(json.dumps({"source_url": UVDOC_URL, "source_sha256": UVDOC_SHA256, "onnx_sha256": hashlib.sha256(data).hexdigest(),
                                    "license": "MIT（見 LICENSE-UVDoc.txt）", "built_by": "mortise.recognition.dewarp.build_uvdoc_onnx"},
                                   ensure_ascii=False, indent=1), encoding="utf-8")
        pkl.unlink(missing_ok=True)
        (d / _FAIL_FILE).unlink(missing_ok=True)
        return onnx, ["第一次使用：已下載 UVDoc 權重（MIT 授權）並在本機轉成 ONNX。"]
    except Exception as e:  # noqa: BLE001 —— 離線、被擋、檔案壞掉：退回量測式
        msg = f"{type(e).__name__}: {e}"
        try:
            d.mkdir(parents=True, exist_ok=True)
            (d / _FAIL_FILE).write_text(json.dumps({"time": time.time(), "error": msg}, ensure_ascii=False), encoding="utf-8")
        except OSError:
            pass
        return None, [f"學習式初值（UVDoc）無法取得（{msg}），只用量測式攤平。"]


_UV_BG: dict[str, threading.Thread] = {}


def _start_background_download(p: dict) -> list[str]:
    """在背景執行緒下載並組裝模型（每個快取目錄只啟動一次）；這次的請求不等它。"""
    d = uvdoc_model_dir()
    key = str(d)
    with _UV_LOCK:
        th = _UV_BG.get(key)
        if th is not None and th.is_alive():
            return ["學習式外框模型（UVDoc）正在背景下載，這張照片先只用量測式攤平。"]
        err = _recent_failure(d, float(p["uvdoc_retry_s"]))
        if err is not None:
            return [f"學習式初值（UVDoc）無法取得（{err}；{p['uvdoc_retry_s'] / 60:.0f} 分鐘內不再重試），只用量測式攤平。"]
        th = threading.Thread(target=ensure_uvdoc_model, args=(dict(p, uvdoc="sync"),), name="mortise-uvdoc-download", daemon=True)
        _UV_BG[key] = th
        th.start()
    return ["第一次使用：學習式外框模型（UVDoc，MIT 授權，約 32 MB）開始在背景下載，這張照片先只用量測式攤平；"
            "部署時可先執行 python -m mortise.recognition.dewarp --download。"]


def _uvdoc_session(p: dict):
    mode = p["uvdoc"]
    if mode == "off":
        return None, ["學習式初值（UVDoc）已關閉，只用量測式攤平。"]
    path = _cached_uvdoc(uvdoc_model_dir())
    notes: list[str] = []
    if path is None:
        if mode == "auto" and os.environ.get("MORTISE_OFFLINE") != "1":
            return None, _start_background_download(p)
        path, notes = ensure_uvdoc_model(p)  # "sync"：在這次請求裡下載（腳本、評測用）；"cache_only"／離線：不下載
        if path is None:
            return None, notes
    with _UV_LOCK:
        key = f"{path}|{int(p['uvdoc_threads'])}"
        if key not in _UV_SESSIONS:
            try:
                import onnxruntime as ort

                so = ort.SessionOptions()
                so.intra_op_num_threads = max(1, int(p["uvdoc_threads"]))
                so.inter_op_num_threads = 1
                _UV_SESSIONS[key] = ort.InferenceSession(str(path), so, providers=["CPUExecutionProvider"])
            except Exception as e:  # noqa: BLE001
                return None, notes + [f"學習式初值（UVDoc）載入失敗（{type(e).__name__}: {e}），只用量測式攤平。"]
        return _UV_SESSIONS[key], notes


def uvdoc_grid(session, image: np.ndarray) -> np.ndarray:
    """UVDoc 推論：回傳 (45, 31, 2) 的網格，每點是頁面上均勻參數位置在 image 中的連續座標。"""
    h, w = image.shape[:2]
    small = cv2.resize(image, UVDOC_INPUT, interpolation=cv2.INTER_AREA if w > UVDOC_INPUT[0] else cv2.INTER_LINEAR)
    x = (small.astype(np.float32) / 255.0).transpose(2, 0, 1)[None].copy()
    g = session.run(None, {"img": x})[0][0]  # (2, 45, 31)，[-1, 1]，align_corners=True
    gx = (g[0] + 1.0) / 2.0 * (w - 1) + 0.5
    gy = (g[1] + 1.0) / 2.0 * (h - 1) + 0.5
    return np.stack([gx, gy], -1).astype(np.float64)


# ============================================================ 主流程


_UNIT = np.array([[0.0, 0.0], [1.0, 0.0], [1.0, 1.0], [0.0, 1.0]])


def _out_size(lengths: list[float], aspect: float, p: dict) -> tuple[int, int]:
    """與 rectify 相同：最長的那一組對邊 1:1 取樣，另一邊依長寬比（高 ÷ 寬）。"""
    top, right, bottom, left = lengths
    if max(top, bottom) >= max(left, right):
        Wf = max(top, bottom)
        Hf = Wf * aspect
    else:
        Hf = max(left, right)
        Wf = Hf / aspect
    s = min(1.0, p["max_output_side"] / max(Wf, Hf, 1.0))
    return max(1, int(round(Wf * s))), max(1, int(round(Hf * s)))


class _Prior:
    """初值對應：頁面參數 (S, T)（0 到 1，左上為原點）→ 原照片座標。UVDoc 網格或單應矩陣。"""

    def __init__(self, grid: Optional[np.ndarray] = None, hu: Optional[np.ndarray] = None, interp: str = "cubic"):
        self.grid = None if grid is None else grid.astype(np.float32)
        self.hu = hu
        self.interp = cv2.INTER_CUBIC if interp == "cubic" else cv2.INTER_LINEAR

    def __call__(self, S, T):
        S, T = np.broadcast_arrays(np.asarray(S, np.float64), np.asarray(T, np.float64))
        if self.grid is None:
            return _apply_h(self.hu, S, T)
        gh, gw = self.grid.shape[:2]
        shp = S.shape
        n = S.size
        cols = 1024 if n > 1024 else max(n, 1)
        rows = -(-n // cols)
        pad = rows * cols - n
        mx = np.concatenate([(S.ravel() * (gw - 1)), np.zeros(pad)]).astype(np.float32).reshape(rows, cols)
        my = np.concatenate([(T.ravel() * (gh - 1)), np.zeros(pad)]).astype(np.float32).reshape(rows, cols)
        out = cv2.remap(self.grid, mx, my, self.interp, borderMode=cv2.BORDER_REPLICATE).reshape(-1, 2)[:n]
        return out[:, 0].astype(np.float64).reshape(shp), out[:, 1].astype(np.float64).reshape(shp)


def _side_st(k: int, t: np.ndarray):
    """第 k 條邊（上、右、下、左）上參數 t 對應的頁面參數 (S, T)；上與下由左到右、左與右由上到下。"""
    z, o = np.zeros_like(t), np.ones_like(t)
    return [(t, z), (o, t), (t, o), (z, t)][k]


class _Coons:
    """外框修正量的 Coons 曲面：四條邊各自的修正量（tables[k]：參數 0 到 1 均勻取樣的 (n, 2) 位移）混合到頁內。
    corners：四角（左上、右上、右下、左下）的位移；None 時取相鄰兩邊端點的平均。"""

    def __init__(self, tables: list[np.ndarray], corners: Optional[dict] = None):
        self.n = len(tables[0])
        self.tt = np.linspace(0.0, 1.0, self.n)
        top, right, bottom, left = [np.asarray(t, np.float64) for t in tables]
        if corners is None:
            corners = {"tl": 0.5 * (top[0] + left[0]), "tr": 0.5 * (top[-1] + right[0]), "br": 0.5 * (bottom[-1] + right[-1]), "bl": 0.5 * (bottom[0] + left[-1])}
        c = self.c = corners
        t = self.tt[:, None]
        self.tab = [top + (1 - t) * (c["tl"] - top[0]) + t * (c["tr"] - top[-1]),
                    right + (1 - t) * (c["tr"] - right[0]) + t * (c["br"] - right[-1]),
                    bottom + (1 - t) * (c["bl"] - bottom[0]) + t * (c["br"] - bottom[-1]),
                    left + (1 - t) * (c["tl"] - left[0]) + t * (c["bl"] - left[-1])]

    def _lookup(self, k: int, t: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        tc = np.clip(t, 0.0, 1.0)
        return np.interp(tc, self.tt, self.tab[k][:, 0]), np.interp(tc, self.tt, self.tab[k][:, 1])

    def __call__(self, S, T):
        S = np.clip(np.asarray(S, np.float64), 0.0, 1.0)
        T = np.clip(np.asarray(T, np.float64), 0.0, 1.0)
        tx, ty = self._lookup(0, S)
        bx, by = self._lookup(2, S)
        lx, ly = self._lookup(3, T)
        rx, ry = self._lookup(1, T)
        c = self.c
        dx = (1 - T) * tx + T * bx + (1 - S) * lx + S * rx - (
            (1 - S) * (1 - T) * c["tl"][0] + S * (1 - T) * c["tr"][0] + (1 - S) * T * c["bl"][0] + S * T * c["br"][0])
        dy = (1 - T) * ty + T * by + (1 - S) * ly + S * ry - (
            (1 - S) * (1 - T) * c["tl"][1] + S * (1 - T) * c["tr"][1] + (1 - S) * T * c["bl"][1] + S * T * c["br"][1])
        return dx, dy


_NT = 513  # 外框曲線與修正量表的取樣數


def _side_curves(prior: _Prior, sides: list[dict], use: list[bool], K: int, inner: np.ndarray) -> list[np.ndarray]:
    """候選外框（prior 的四條邊）＋精修量到的法線偏移 → 四條外框折線（_NT 點，方向同 _side_st）。沒支持的邊維持候選。"""
    tt = np.linspace(0.0, 1.0, _NT)
    out = []
    for k in range(4):
        x, y = prior(*_side_st(k, tt))
        pts = np.stack([x, y], 1)
        if use[k]:
            pts = pts + _eval_spline(sides[k]["coef"], tt, K)[:, None] * _normals(pts, inner)
        out.append(pts)
    return out


def _normal_tables(prior: _Prior, sides: list[dict], use: list[bool], K: int, inner: np.ndarray) -> tuple[list[np.ndarray], dict]:
    """（UVDoc 初值用）各邊的法線偏移表與四角位移（相鄰兩邊的法線偏移聯立）。"""
    tt = np.linspace(0.0, 1.0, _NT)
    tabs = []
    for k in range(4):
        if use[k]:
            x, y = prior(*_side_st(k, tt))
            nrm = _normals(np.stack([x, y], 1), inner)
            tabs.append(_eval_spline(sides[k]["coef"], tt, K)[:, None] * nrm)
        else:
            tabs.append(np.zeros((_NT, 2)))
    top, right, bottom, left = tabs
    corners = {}
    for key, (u, v) in {"tl": (top[0], left[0]), "tr": (top[-1], right[0]), "br": (bottom[-1], right[-1]), "bl": (bottom[0], left[-1])}.items():
        nu, nv = np.linalg.norm(u), np.linalg.norm(v)
        if nu < 1e-9 or nv < 1e-9:
            corners[key] = u + v
            continue
        A = np.array([u / nu, v / nv])
        corners[key] = np.linalg.solve(A, np.array([nu, nv])) if abs(np.linalg.det(A)) >= 0.2 else 0.5 * (u + v)
    return tabs, corners


def _abs_tables(hu: np.ndarray, curves: list[Optional[np.ndarray]]) -> list[np.ndarray]:
    """（透視初值用）外框折線 → 相對於四角透視的位移表：把折線上的點以透視反推到頁面參數，
    沿邊的參數就是它在這條邊上的位置。curves[k] 為 None 的邊位移為 0。"""
    hinv = np.linalg.inv(hu)
    tt = np.linspace(0.0, 1.0, _NT)
    tabs = []
    for k in range(4):
        cv = curves[k]
        if cv is None:
            tabs.append(np.zeros((_NT, 2)))
            continue
        s_, t_ = _apply_h(hinv, cv[:, 0], cv[:, 1])
        par = s_ if k in (0, 2) else t_
        order = np.argsort(par)
        par = par[order]
        pts = cv[order]
        px, py = _apply_h(hu, *_side_st(k, par))
        dx, dy = pts[:, 0] - px, pts[:, 1] - py
        tabs.append(np.stack([np.interp(tt, par, dx), np.interp(tt, par, dy)], 1))
    return tabs


class _Small:
    """縮小的原照片（量直線度用），避免從大照片直接取樣成縮圖時的鋸齒。"""

    def __init__(self, img: np.ndarray, f: float):
        H, W = img.shape[:2]
        if f < 0.9:
            self.img = cv2.resize(img, (max(8, int(round(W * f))), max(8, int(round(H * f)))), interpolation=cv2.INTER_AREA)
        else:
            self.img = img
        self.kx = self.img.shape[1] / W
        self.ky = self.img.shape[0] / H

    def render(self, X: np.ndarray, Y: np.ndarray) -> np.ndarray:
        return _remap(self.img, (X * self.kx).astype(np.float32), (Y * self.ky).astype(np.float32))


def _centers(n: int) -> np.ndarray:
    return (np.arange(n, dtype=np.float64) + 0.5) / n


_ROW_BLOCK = 256  # 大圖逐列區塊算對應圖、取樣（12 MP 的照片整張一次算，float64 的網格就要好幾百 MB）


def _render_rows(img: Optional[np.ndarray], fn: Callable, Wo: int, Ho: int, interp: int = cv2.INTER_LINEAR):
    """逐列區塊算對應圖並取樣：fn(S, T, r0, r1) -> (X, Y)，S、T 是第 r0 到 r1 列像素中心的參數座標。
    回傳（float32 的 map_x、map_y，以及取樣後的影像；img 為 None 時影像也是 None）。"""
    X = np.empty((Ho, Wo), np.float32)
    Y = np.empty((Ho, Wo), np.float32)
    out = None if img is None else np.empty((Ho, Wo) + img.shape[2:], img.dtype)
    s = _centers(Wo)
    for r0 in range(0, Ho, _ROW_BLOCK):
        r1 = min(Ho, r0 + _ROW_BLOCK)
        S, T = np.meshgrid(s, (np.arange(r0, r1, dtype=np.float64) + 0.5) / Ho)
        x, y = fn(S, T, r0, r1)
        X[r0:r1] = x
        Y[r0:r1] = y
        if out is not None:
            out[r0:r1] = _remap(img, X[r0:r1], Y[r0:r1], interp)
    return X, Y, out


def _lines_kept(n_before: int, n_after: int, p: dict) -> bool:
    """攤平後量到的線夠不夠多，前後的殘差才能比（同一組線）：至少 max(min(前, flat_min_lines), keep_lines_frac × 前) 條。"""
    if n_before <= 0:
        return True
    need = max(min(n_before, int(p["flat_min_lines"])), int(math.ceil(p["keep_lines_frac"] * n_before)))
    return n_after >= need


def _cv_residual(st: dict, Wm: int, Hm_: int, p: dict, pin_rows: float, pin_cols: float) -> Optional[float]:
    """修正場的交叉驗證殘差（縮圖像素，第 95 百分位）：線依位置分兩組，用一組擬合位移場、量另一組的預測誤差。
    修正場是拿頁內的線擬合的，擬合後那些線一定變直；沒參與擬合的線才看得出位移場本身準不準。"""
    gy, gx = p["correct_grid"]
    errs = []
    for axis, pin in (("h", (pin_rows, 0.0)), ("v", (0.0, pin_cols))):
        per = st.get("per", {}).get(axis, [])
        if len(per) < 4:
            continue
        rows = []
        for d in per:
            ok = d["w"] > 0
            if axis == "h":
                rows.append(np.stack([d["x"][ok], d["lin"][ok], d["dev"][ok]], 1))
            else:
                rows.append(np.stack([d["lin"][ok], d["x"][ok], d["dev"][ok]], 1))
        key = [float(np.median(r[:, 1] if axis == "h" else r[:, 0])) for r in rows]
        order = np.argsort(key)
        for fold in (0, 1):
            tr = [rows[i] for j, i in enumerate(order) if j % 2 != fold]
            te = [rows[i] for j, i in enumerate(order) if j % 2 == fold]
            a = np.concatenate(tr)
            if len(a) < 12:
                continue
            F = _fit_field(a[:, 0], a[:, 1], a[:, 2], np.ones(len(a)), Wm, Hm_, (gy, gx), p["correct_smooth"], pin[0], pin[1])
            for b in te:
                if len(b) < 5:
                    continue
                fx = np.clip(b[:, 0] / Wm * (gx - 1), 0, gx - 1 - 1e-9)
                fy = np.clip(b[:, 1] / Hm_ * (gy - 1), 0, gy - 1 - 1e-9)
                ix, iy = np.floor(fx).astype(int), np.floor(fy).astype(int)
                ax, ay = fx - ix, fy - iy
                pred = (F[iy, ix] * (1 - ax) * (1 - ay) + F[iy, ix + 1] * ax * (1 - ay) + F[iy + 1, ix] * (1 - ax) * ay + F[iy + 1, ix + 1] * ax * ay)
                u = b[:, 0] if axis == "h" else b[:, 1]  # 沿線的座標：線本身的偏差已去掉直線部分，預測也要去掉
                r = b[:, 2] - pred
                r = r - np.polyval(np.polyfit(u, r, 1), u)
                errs.append(np.abs(r))
    if not errs:
        return None
    return float(np.percentile(np.concatenate(errs), 95))


def _field_samples(st: dict) -> tuple[list, list]:
    """直線度量測 → 位移樣本：橫線給（位置 x、y_直線、垂直位移），直線給（位置 x_直線、y、水平位移）。"""
    hs, vs = [], []
    for d in st.get("per", {}).get("h", []):
        ok = d["w"] > 0
        hs.append(np.stack([d["x"][ok], d["lin"][ok], d["dev"][ok]], 1))
    for d in st.get("per", {}).get("v", []):
        ok = d["w"] > 0
        vs.append(np.stack([d["lin"][ok], d["x"][ok], d["dev"][ok]], 1))
    return hs, vs


def _corners_from_curves(cv: list[np.ndarray], frac: float = 0.12) -> np.ndarray:
    """四條外框折線 → 四角：相鄰兩條在角落附近（各取 frac 長度）的切線相交；交不出來就取兩端點的平均。"""
    def tangent(pts):
        c = pts.mean(axis=0)
        u, s_, vt = np.linalg.svd(pts - c, full_matrices=False)
        d = vt[0]
        return c, d

    def inter(a, b):
        (c1, d1), (c2, d2) = a, b
        A = np.array([d1, -d2]).T
        if abs(np.linalg.det(A)) < 1e-6:
            return None
        t = np.linalg.solve(A, c2 - c1)
        return c1 + t[0] * d1

    n = len(cv[0])
    m = max(3, int(round(frac * n)))
    top, right, bottom, left = cv
    pairs = [(top[:m], left[:m], top[0], left[0]), (top[-m:], right[:m], top[-1], right[0]),
             (bottom[-m:], right[-m:], bottom[-1], right[-1]), (bottom[:m], left[-m:], bottom[0], left[-1])]
    out = []
    for a, b, ea, eb in pairs:
        x = inter(tangent(a), tangent(b))
        mid = 0.5 * (ea + eb)
        out.append(x if x is not None and np.linalg.norm(x - mid) < 0.05 * max(_arclen(top), _arclen(left)) else mid)
    return np.array(out)


def _corners_from_sides(q_sides: list[dict], p: dict, fallback: np.ndarray) -> Optional[np.ndarray]:
    """四條直邊的精修結果（有支持的邊緣點）各擬合一條直線，相鄰兩條相交得四角；有邊量不到就回傳 None。"""
    lines = []
    for sd in q_sides:
        if sd["support"] < p["min_edge_support"] or "good" not in sd or sd["good"].sum() < 8:
            return None
        pts = (sd["pts"] + sd["fit"][:, None] * sd["nout"])[sd["good"]]
        c = pts.mean(axis=0)
        _, _, vt = np.linalg.svd(pts - c, full_matrices=False)
        lines.append((c, vt[0]))
    top, right, bottom, left = lines
    out = []
    for a, b, fb in ((top, left, fallback[0]), (top, right, fallback[1]), (bottom, right, fallback[2]), (bottom, left, fallback[3])):
        A = np.array([a[1], -b[1]]).T
        if abs(np.linalg.det(A)) < 1e-6:
            return None
        t = np.linalg.solve(A, b[0] - a[0])
        x = a[0] + t[0] * a[1]
        if np.linalg.norm(x - fb) > 0.03 * np.linalg.norm(fallback[2] - fallback[0]):
            return None
        out.append(x)
    return np.array(out)


def _poly_area(q: np.ndarray) -> float:
    x, y = q[:, 0], q[:, 1]
    return 0.5 * abs(float(np.dot(x, np.roll(y, -1)) - np.dot(y, np.roll(x, -1))))


def _quad_signed_out(poly: np.ndarray, pts: np.ndarray) -> np.ndarray:
    """點在四邊形外的距離（在內為 0）。"""
    c = poly.astype(np.float32).reshape(-1, 1, 2)
    return np.array([max(0.0, -cv2.pointPolygonTest(c, (float(x), float(y)), True)) for x, y in pts])


def dewarp(photo: np.ndarray, quad: Optional[PageQuad] = None, params: dict | None = None) -> DewarpResult:
    """攤平。photo 為原照片（RGB）；quad 為 find_page_quad 的結果（或 None、或 method="full_image"）。見模組說明。"""
    p = _p(params)
    tm: dict[str, float] = {}
    t_all = time.time()
    img = _rgb(photo)
    H, W = img.shape[:2]
    diag = math.hypot(W, H)
    notes: list[str] = []
    mode = p["dewarp_mode"]
    have_quad = quad is not None and getattr(quad, "method", "") != "full_image" and quad.corners is not None
    t0 = time.time()
    img_f = cv2.GaussianBlur(img.astype(np.float32), (0, 0), p["edge_blur_sigma"])
    R = max(p["edge_search_min_px"], p["edge_search_frac"] * diag)
    names = ["上", "右", "下", "左"]

    # ---- 1. 平的紙基準（有四角時）：透視對應、四邊弓高、頁內直線度
    st_before: dict = {}
    bows = [0.0] * 4
    lengths_q = [0.0] * 4
    q_sides: list[dict] = []
    persp: dict = {}
    small_src = None

    def setup_persp(c: np.ndarray, aspect: Optional[float], bow_hint=None) -> None:
        nonlocal st_before, bows, lengths_q, q_sides, small_src
        c = np.asarray(c, np.float64)
        lengths_q = [float(np.linalg.norm(c[(k + 1) % 4] - c[k])) for k in range(4)]
        if not aspect or aspect <= 0:
            aspect = (lengths_q[1] + lengths_q[3]) / max(lengths_q[0] + lengths_q[2], 1e-9)
        Wo, Ho = _out_size(lengths_q, aspect, p)
        Hm = _homography(c, np.array([[0, 0], [Wo, 0], [Wo, Ho], [0, Ho]], np.float64))
        hu = _homography(_UNIT, c)
        persp.update({"H": Hm, "Wo": Wo, "Ho": Ho, "hu": hu, "aspect": float(aspect), "corners": c})
        cen = c.mean(axis=0)
        ends = [(c[0], c[1]), (c[1], c[2]), (c[3], c[2]), (c[0], c[3])]  # 方向同 _side_st：上、下由左到右，左、右由上到下
        q_sides = []
        bows = [0.0] * 4
        for k in range(4):
            a_, b_ = ends[k]
            fn = (lambda t, a_=a_, b_=b_: a_[None, :] + np.asarray(t)[:, None] * (b_ - a_)[None, :])
            r = refine_side(img_f, fn, cen, R, p)
            r["fn"] = fn
            q_sides.append(r)
            bows[k] = r["bow"] if r["support"] >= p["min_edge_support"] else 0.0
        f = min(1.0, p["measure_side"] / max(Wo, Ho))
        small_src = _Small(img, f)
        Wm, Hm_ = max(8, int(round(Wo * f))), max(8, int(round(Ho * f)))
        S0, T0 = np.meshgrid(_centers(Wm), _centers(Hm_))
        X, Y = _apply_h(hu, S0, T0)
        st_before = straightness(find_lines(small_src.render(X, Y), p), Wm, Hm_)
        if st_before.get("n_lines"):
            st_before["p95_out_px"] = st_before["p95_px"] / f
        if bow_hint and st_before.get("n_lines", 0) < p["flat_min_lines"]:
            # 頁內沒有線可量時才參考 find_page_quad 量到的弓高（它的搜尋範圍比較大；有線時以直線度為準，免得平的紙被誤攤）
            bows = [max(b_, float(v or 0.0)) for b_, v in zip(bows, bow_hint)]

    if have_quad:
        setup_persp(quad.corners, quad.aspect_ratio, getattr(quad, "edge_bow_px", None))
    tm["flat_check"] = time.time() - t0

    pseudo_info: dict = {}  # 沒有四角時由紙邊量出的四角（交給 rectify 與上層確認）

    def perspective_result(extra: list[str], curved: bool, conf: Optional[float] = None, support=None) -> DewarpResult:
        Wo, Ho, Hm = persp["Wo"], persp["Ho"], persp["H"]
        t_in = np.array([[1, 0, 0.5], [0, 1, 0.5], [0, 0, 1]], dtype=np.float64)  # 連續座標 → OpenCV 像素中心（與 rectify 相同）
        t_out = np.array([[1, 0, -0.5], [0, 1, -0.5], [0, 0, 1]], dtype=np.float64)
        out = cv2.warpPerspective(img, t_out @ Hm @ t_in, (Wo, Ho), flags=cv2.INTER_LINEAR, borderMode=cv2.BORDER_REPLICATE)
        X, Y, _ = _render_rows(None, lambda S, T, r0, r1: _apply_h(persp["hu"], S, T), Wo, Ho)
        res = st_before.get("p95_out_px")
        c = persp["corners"]
        if conf is None:
            conf = float(getattr(quad, "confidence", 0.5))
        if support is None:
            support = list(getattr(quad, "edge_support", None) or [float(sd["support"]) for sd in q_sides])
        tm["total"] = time.time() - t_all
        review = bool(pseudo_info) or conf < 0.6 or curved
        return DewarpResult(image=out, map_x=X, map_y=Y, applied=False, source="", curved=curved,
                            corners=c, boundary=[c[[0, 1]], c[[1, 2]], c[[3, 2]], c[[0, 3]]], support=[float(v) for v in support],
                            bow_px=[float(b_) for b_ in bows], residual_px=res, residual_before_px=res, straightness=_st_summary(st_before, None),
                            confidence=float(conf), homography=Hm, notes=notes + extra, timings=tm,
                            pseudo_quad=pseudo_info.get("corners"),
                            measured_sides=int(pseudo_info.get("measured", sum(1 for sd in q_sides if sd["support"] >= p["min_edge_support"]))),
                            needs_review=review)

    if have_quad and mode == "off":
        return perspective_result(["攤平已關閉，只做透視攤平。"], False)

    # ---- 2. 候選外框（UVDoc）
    t0 = time.time()
    grid = None
    crop = (0, 0, W, H)
    sess = None
    if p["uvdoc"] != "off":
        sess, n_ = _uvdoc_session(p)
        notes += n_

    def run_uvdoc(box):
        x0, y0, x1, y1 = box
        try:
            g = uvdoc_grid(sess, img[y0:y1, x0:x1])
        except Exception as e:  # noqa: BLE001
            notes.append(f"學習式初值（UVDoc）推論失敗（{type(e).__name__}: {e}），只用量測式攤平。")
            return None
        g[..., 0] += x0
        g[..., 1] += y0
        return g

    if sess is not None:
        if have_quad:  # 裁到四角外接框外擴一點（研究實測比全圖準）
            c = np.asarray(quad.corners, np.float64)
            lo, hi = c.min(axis=0), c.max(axis=0)
            mg = p["uvdoc_crop_margin"] * (hi - lo)
            x0, y0 = [int(max(0, math.floor(v))) for v in lo - mg]
            crop = (x0, y0, int(min(W, math.ceil(hi[0] + mg[0]))), int(min(H, math.ceil(hi[1] + mg[1]))))
        grid = run_uvdoc(crop)
        if grid is not None and crop != (0, 0, W, H):
            # 外框貼到裁切邊（而那條裁切邊不是畫面邊）：四角可能只框到紙的一部分，改用全圖重推
            x0, y0, x1, y1 = crop
            tol = p["crop_touch_frac"] * max(x1 - x0, y1 - y0)
            edge = np.concatenate([grid[0], grid[-1], grid[:, 0], grid[:, -1]])
            touch = ((x0 > 0) & (edge[:, 0] < x0 + tol)) | ((y0 > 0) & (edge[:, 1] < y0 + tol)) | \
                    ((x1 < W) & (edge[:, 0] > x1 - tol)) | ((y1 < H) & (edge[:, 1] > y1 - tol))
            if touch.mean() > 0.05:
                g2 = run_uvdoc((0, 0, W, H))
                if g2 is not None:
                    grid = g2
                    notes.append("學習式模型找的外框貼到四角的裁切邊：改用全圖重找。")
    tm["uvdoc"] = time.time() - t0
    if grid is None and not have_quad:
        tm["total"] = time.time() - t_all
        return DewarpResult(image=None, map_x=None, map_y=None, applied=False, source="", curved=False, confidence=0.0, timings=tm,
                            notes=notes + ["沒有頁面四角，也沒有學習式初值可用：無法攤平，維持整張圖。"])

    # ---- 3. 外框精修（UVDoc 的候選外框）與彎曲判定
    t0 = time.time()
    K = int(p["edge_segments"])
    uv_sides: list[dict] = []
    uv_use = [False] * 4
    prior_uv = None
    inner_uv = None
    if grid is not None:
        prior_uv = _Prior(grid=grid, interp=p["uvdoc_interp"])
        inner_uv = np.array(prior_uv(np.array([0.5]), np.array([0.5]))).ravel()
        for k in range(4):
            def fn(t, k=k):
                x, y = prior_uv(*_side_st(k, np.asarray(t, np.float64)))
                return np.stack([x, y], 1)
            uv_sides.append(refine_side(img_f, fn, inner_uv, R, p))
        # 紙邊是「紙 → 背景」的階梯：只有梯度、內外沒有色差的（背景紋理、表格框線）不算量到
        uv_use = [sd["support"] >= p["min_edge_support"] and sd["contrast"] >= p["edge_min_contrast"] for sd in uv_sides]
    q_use = [bool(q_sides) and q_sides[k]["support"] >= p["min_edge_support"] for k in range(4)]
    tm["boundary"] = time.time() - t0

    # 四角框住的面積明顯比紙邊量到的外框小（框到紙上的一塊、或把紙切掉一大半）：改用量到的外框
    if have_quad and grid is not None and sum(uv_use) >= 3 and float(np.mean([sd["support"] for sd in uv_sides])) >= p["quad_doubt_support"]:
        c4 = _corners_from_curves(_side_curves(prior_uv, uv_sides, uv_use, K, inner_uv))
        ratio = _poly_area(np.asarray(quad.corners, np.float64)) / max(_poly_area(c4), 1e-9)
        if ratio < p["quad_doubt_area"]:
            notes.append(f"找到的四角只框住紙邊量到的外框的 {ratio:.0%}：改用量到的外框。")
            have_quad = False
            st_before = {}

    # 沒有四角（find_page_quad 失敗）：以精修後的外框四角當「四角」，同樣先檢查是不是平的紙
    pseudo = False
    n_meas = int(sum(uv_use))
    if not have_quad and grid is not None and sum(uv_use) >= 3:
        cv_ = _side_curves(prior_uv, uv_sides, uv_use, K, inner_uv)
        c4 = _corners_from_curves(cv_)
        from .page_geometry import estimate_aspect

        est, _, _ = estimate_aspect(c4, (W, H))
        t1 = time.time()
        setup_persp(c4, float(est.value))
        c5 = _corners_from_sides(q_sides, p, c4)  # 再以四條直邊的擬合直線相交（平的紙時比曲線端點準）
        if c5 is not None and np.max(np.linalg.norm(c5 - c4, axis=1)) > 0.3:
            est, _, _ = estimate_aspect(c5, (W, H))
            setup_persp(c5, float(est.value))
        tm["flat_check"] += time.time() - t1
        pseudo = True
        # 在原照片上量到的邊：直邊精修有支持、而且是紙與背景的交界（有內外色差；表格框線兩側同色，不算）
        meas = [k for k in range(4) if any(sd["support"] >= p["min_edge_support"] and sd["contrast"] >= p["edge_min_contrast"]
                                           for sd in (q_sides[k], uv_sides[k]))]
        pseudo_info.update({"corners": np.asarray(persp["corners"], np.float64).copy(), "measured": len(meas)})
        n_meas = len(meas)
        if len(meas) < 3:
            pseudo = False
            pseudo_info.clear()

    if not have_quad and not pseudo:
        # 沒有四角、量得到的紙邊又不到三條：學習式模型的外框只當候選（鐵律：學習式模型不決定最後的位置），維持整張圖
        cand = _side_curves(prior_uv, uv_sides, uv_use, K, inner_uv) if grid is not None else []
        sup = [float(sd["support"]) for sd in uv_sides]
        tm["total"] = time.time() - t_all
        c4 = _corners_from_curves(cand) if cand else None
        return DewarpResult(image=None, map_x=None, map_y=None, applied=False, source="", curved=False, confidence=0.0, timings=tm,
                            corners=c4, support=sup, candidate_outline=cand, measured_sides=int(n_meas), needs_review=True,
                            notes=notes + [f"沒有頁面四角，紙與背景的交界只量到 {n_meas} 條（學習式模型外框的四邊支持率 {', '.join(f'{v:.2f}' for v in sup)}）："
                                           "學習式模型找的外框只當候選（請使用者確認），維持整張圖。"])

    # 紙有沒有被四角裁掉（捲起的上緣常被丟掉）：UVDoc 外框有邊緣支持的部分明顯在四邊形外
    truncated = []
    if have_quad and grid is not None:
        thr = max(p["outside_px"], p["outside_frac"] * diag)
        poly = np.asarray(quad.corners, np.float64)
        for k, sd in enumerate(uv_sides):
            if not uv_use[k] or "good" not in sd:
                continue
            pts = (sd["pts"] + sd["fit"][:, None] * sd["nout"])[sd["good"]]
            if len(pts) < 8:
                continue
            out_d = _quad_signed_out(poly, pts[:: max(1, len(pts) // 60)])
            if np.percentile(out_d, 80) > thr:
                truncated.append(k)
    sag_bad = bool(st_before.get("n_lines", 0) >= p["flat_min_lines"] and (st_before["sag_median_pct"] > p["flat_sag_median_pct"]
                                                                          or st_before.get("sag_2nd_pct", 0.0) > p["flat_sag_max_pct"]))
    bow_bad = [k for k in range(4) if (have_quad or pseudo) and bows[k] > max(p["bow_px"], p["bow_frac"] * lengths_q[k])]
    curved = (not have_quad and not pseudo) or sag_bad or bool(bow_bad) or bool(truncated)
    why = []
    if bow_bad:
        why.append(f"{'、'.join(names[k] for k in bow_bad)}邊彎曲（弓高 {', '.join(f'{bows[k]:.1f}' for k in bow_bad)} 像素）")
    if sag_bad:
        why.append(f"頁內格線與文字行不直（{st_before['n_lines']} 條線，最大偏離中位數 {st_before['sag_median_pct']:.2f}%、第二大 {st_before['sag_2nd_pct']:.2f}% 頁寬）")
    if truncated:
        why.append(f"{'、'.join(names[k] for k in truncated)}邊的紙超出四角（可能捲起）")
    if (have_quad or pseudo) and mode == "auto" and not curved:
        if pseudo:
            sup = [float(sd["support"]) for sd in uv_sides]
            srt = sorted(sup)
            conf = 0.05 + 0.85 * float(np.clip((srt[1] - 0.3) / 0.5, 0.0, 1.0))
            return perspective_result([f"沒有現成的四角：以學習式模型（UVDoc）找的外框為候選、由紙邊量出四角（四邊支持率 {', '.join(f'{v:.2f}' for v in sup)}）；"
                                       f"判定為平的紙（四邊弓高 ≤ {max(bows):.1f} 像素、頁內 {st_before.get('n_lines', 0)} 條線都直）：只做透視攤平。"],
                                      False, conf, sup)
        return perspective_result([f"判定為平的紙（四邊弓高 ≤ {max(bows):.1f} 像素、頁內 {st_before.get('n_lines', 0)} 條線都直）：只做透視攤平。"], False)

    # ---- 4. 對應圖：初值 + Coons 外框修正
    t0 = time.time()
    uv_curves = _side_curves(prior_uv, uv_sides, uv_use, K, inner_uv) if grid is not None else None
    from_uv = False  # 外框或四角是否用到 UVDoc 沒有量測支持的推估
    if p["interior_prior"] == "uvdoc" and grid is not None:
        prior = prior_uv
        tabs, cdisp = _normal_tables(prior_uv, uv_sides, uv_use, K, inner_uv)
        coons = _Coons(tabs, cdisp)
        support = [float(sd["support"]) for sd in uv_sides]
        use = uv_use
        from_uv = True
    else:
        if have_quad and not truncated:
            corners0 = np.asarray(quad.corners, np.float64)
        else:
            # 四角取自精修後的外框：相鄰兩條曲線端點的平均
            corners0 = _corners_from_curves(uv_curves)
            from_uv = not all(uv_use)
        hu0 = _homography(_UNIT, corners0)
        prior = _Prior(hu=hu0)
        targets: list[Optional[np.ndarray]] = []
        support, use = [], []
        for k in range(4):
            if grid is not None and uv_use[k] and (not q_use[k] or uv_sides[k]["support"] >= q_sides[k]["support"] - 0.05 or k in truncated):
                targets.append(uv_curves[k])
                support.append(float(uv_sides[k]["support"]))
                use.append(True)
            elif q_use[k] and not truncated:
                tt = np.linspace(0.0, 1.0, _NT)
                base = q_sides[k]["fn"](tt)
                targets.append(base + _eval_spline(q_sides[k]["coef"], tt, K)[:, None] * _normals(base, corners0.mean(axis=0)))
                support.append(float(q_sides[k]["support"]))
                use.append(True)
            elif grid is not None:
                targets.append(uv_curves[k])  # 量不到：用 UVDoc 推估的位置
                support.append(float(uv_sides[k]["support"]))
                use.append(False)
                from_uv = True
            else:
                targets.append(None)
                support.append(float(q_sides[k]["support"]) if q_sides else 0.0)
                use.append(False)
        coons = _Coons(_abs_tables(hu0, targets))
    source = "uvdoc_prior" if from_uv else "measured"
    weak = [names[k] for k in range(4) if not use[k]]
    if weak:
        notes.append(f"{'、'.join(weak)}邊的紙邊量不到（支持率 {', '.join(f'{support[k]:.2f}' for k in range(4) if not use[k])}），"
                     + ("用學習式模型推估的位置" if grid is not None else "維持四角連線"))

    def M1(S, T):
        x, y = prior(S, T)
        dx, dy = coons(S, T)
        return x + dx, y + dy

    tt = np.linspace(0, 1, 129)
    boundary = []
    for k in range(4):
        x, y = M1(*_side_st(k, tt))
        boundary.append(np.stack([x, y], 1))
    corners = np.array([boundary[0][0], boundary[0][-1], boundary[2][-1], boundary[2][0]])
    lengths = [_arclen(bd) for bd in boundary]
    if have_quad and not truncated:
        aspect = persp["aspect"]
    else:
        from .page_geometry import estimate_aspect

        est, _, _ = estimate_aspect(corners, (W, H))
        aspect = float(est.value)
    Wo, Ho = _out_size(lengths, aspect, p)

    # ---- 5. 頁內修正（量測式）
    f = min(1.0, p["measure_side"] / max(Wo, Ho))
    if small_src is None or abs(small_src.kx - f) > 0.15 * f:
        small_src = _Small(img, f)
    Wm, Hm_ = max(8, int(round(Wo * f))), max(8, int(round(Ho * f)))
    S0, T0 = np.meshgrid(_centers(Wm), _centers(Hm_))
    eS, eT = np.zeros_like(S0), np.zeros_like(T0)  # 參數修正（相對於均勻參數的偏差）

    def render(eS_, eT_):
        X, Y = M1(S0 + eS_, T0 + eT_)
        return small_src.render(X, Y)

    sm = render(eS, eT)
    st = straightness(find_lines(sm, p), Wm, Hm_)
    st_first = st
    gy, gx = p["correct_grid"]
    pin_rows = p["correct_pin"] if (use[0] or use[2]) else 0.0
    pin_cols = p["correct_pin"] if (use[1] or use[3]) else 0.0
    n_iter = 0
    for it in range(int(p["correct_iters"])):
        if not st.get("n_lines"):
            break
        hs, vs = _field_samples(st)
        if not hs and not vs:
            break
        dS = np.zeros_like(S0)
        dT = np.zeros_like(T0)
        if hs and sum(len(h) for h in hs) >= 12:
            a = np.concatenate(hs)
            Fy = _fit_field(a[:, 0], a[:, 1], a[:, 2], np.ones(len(a)), Wm, Hm_, (gy, gx), p["correct_smooth"], pin_rows, 0.0)
            dT = _upsample_grid(Fy, Wm, Hm_) / Hm_
        if vs and sum(len(v) for v in vs) >= 12:
            a = np.concatenate(vs)
            Fx = _fit_field(a[:, 0], a[:, 1], a[:, 2], np.ones(len(a)), Wm, Hm_, (gy, gx), p["correct_smooth"], 0.0, pin_cols)
            dS = _upsample_grid(Fx, Wm, Hm_) / Wm
        # 新參數：(S, T) → (S + dS, T + dT) 再經舊的修正
        qx = ((S0 + dS) * Wm - 0.5).astype(np.float32)
        qy = ((T0 + dT) * Hm_ - 0.5).astype(np.float32)
        eS2 = dS + cv2.remap(eS.astype(np.float32), qx, qy, cv2.INTER_LINEAR, borderMode=cv2.BORDER_REPLICATE)
        eT2 = dT + cv2.remap(eT.astype(np.float32), qx, qy, cv2.INTER_LINEAR, borderMode=cv2.BORDER_REPLICATE)
        sm2 = render(eS2, eT2)
        st2 = straightness(find_lines(sm2, p), Wm, Hm_)
        # 同一組線才比：修正後線少了一大截（被扭壞而量不到），殘差變小也不算數
        if st2.get("n_lines") and _lines_kept(st["n_lines"], st2["n_lines"], p) and st2["p95_px"] <= st["p95_px"] * (1 - p["correct_min_gain"]):
            eS, eT, st, sm = eS2, eT2, st2, sm2
            n_iter += 1
        else:
            break
    tm["interior"] = time.time() - t0

    # ---- 6. 驗證後才採用（每條路徑都驗證，含紙超出四角）
    res_after = st["p95_px"] / f if st.get("n_lines") else None
    res_before = st_before.get("p95_out_px")
    n_b, n_a = int(st_before.get("n_lines", 0) or 0), int(st.get("n_lines", 0) or 0)
    accept, reason = True, ""
    if have_quad or pseudo:
        if n_b and not _lines_kept(n_b, n_a, p):
            accept, reason = False, f"攤平後只量到 {n_a} 條格線／文字行（透視攤平時 {n_b} 條；線被扭壞就量不到，殘差不能拿來比）"
        elif res_after is not None and res_before is not None:
            if res_after > res_before * (1.0 + p["worse_tol_rel"]) + p["worse_tol_px"]:
                accept, reason = False, f"攤平後的直線度（殘差 {res_after:.2f} 像素）比透視攤平（{res_before:.2f} 像素）差"
            elif n_b >= p["flat_min_lines"] and not truncated and res_before - res_after < max(p["accept_margin_frac"] * Wo, p["accept_rel"] * res_before):
                # 紙超出四角時（捲起的部分被四角切掉）放寬：攤平多救回一塊紙，只要直線度沒有變差就採用
                accept, reason = False, f"攤平後的直線度（殘差 {res_after:.1f} 像素）沒有比透視攤平（{res_before:.1f} 像素）明顯好"
        elif not bow_bad and not truncated:
            accept, reason = False, "頁內量不到格線或文字行，紙邊也沒有量到彎曲"
    if (have_quad or pseudo) and not accept and mode != "force":
        if pseudo:
            sup_ = [float(sd["support"]) for sd in q_sides]
            conf_ = 0.05 + 0.6 * float(np.clip((sorted(sup_)[1] - 0.3) / 0.5, 0.0, 1.0))
            return perspective_result([f"沒有現成的四角：以學習式模型找的外框為候選、由紙邊量出四角（{pseudo_info['measured']} 條邊有量測支持）；"
                                       f"看起來是彎的紙（{'；'.join(why) or '—'}），但{reason}：只做透視攤平，四角請使用者確認。"], curved, conf_, sup_)
        return perspective_result(why and [f"看起來是彎的紙（{'；'.join(why)}），但{reason}：退回透視攤平。"] or [f"{reason}：退回透視攤平。"], curved)

    t0 = time.time()
    eS32, eT32 = eS.astype(np.float32), eT.astype(np.float32)

    def final_map(S, T, r0, r1):
        if n_iter:
            qx = (S * Wm - 0.5).astype(np.float32)
            qy = (T * Hm_ - 0.5).astype(np.float32)
            S = S + cv2.remap(eS32, qx, qy, cv2.INTER_CUBIC, borderMode=cv2.BORDER_REPLICATE)
            T = T + cv2.remap(eT32, qx, qy, cv2.INTER_CUBIC, borderMode=cv2.BORDER_REPLICATE)
        return M1(S, T)

    interp = cv2.INTER_CUBIC if p["output_interp"] == "cubic" else cv2.INTER_LINEAR
    X, Y, out = _render_rows(img, final_map, Wo, Ho, interp)
    tm["render"] = time.time() - t0

    # ---- 信心值與說明
    # 頁內殘差：修正場是拿這些線擬合的，擬合後的殘差一定小；有修正時改用交叉驗證殘差（沒參與擬合的線的預測誤差）
    res_cv = None
    if n_iter:
        cvr = _cv_residual(st_first, Wm, Hm_, p, pin_rows, pin_cols)
        res_cv = cvr / f if cvr is not None else None
    res_conf = max(v for v in (res_after, res_cv) if v is not None) if (res_after is not None or res_cv is not None) else None
    srt = sorted(support)
    f_sup = float(np.clip((srt[1] - 0.3) / 0.5, 0.0, 1.0))
    f_res = 1.0 / (1.0 + (res_conf / (0.003 * Wo)) ** 2) if res_conf is not None else 0.6
    # 外框有學習式模型推估的邊、紙超出四角（捲起處的位置量不到）、四角是自己量的：位置誤差量不到，信心值壓低
    f_src = (p["conf_uvdoc_prior"] if source == "uvdoc_prior" else 1.0) * (p["conf_truncated"] if truncated else 1.0) * (p["conf_pseudo"] if pseudo else 1.0)
    # 直線度幾乎沒有進步：攤平的好處只來自外框，頁內量不到有沒有攤對
    f_gain = 1.0
    if res_before is not None and res_after is not None and res_before > 0:
        f_gain = float(np.clip(0.6 + 0.4 * (res_before - res_after) / (0.5 * res_before), 0.6, 1.0))
    conf = 0.05 + 0.9 * f_sup * f_res * f_src * f_gain * (1.0 if st.get("n_lines", 0) >= 3 else 0.85)
    msg = "已攤平彎曲的紙" + (f"（{'；'.join(why)}）" if why else "")
    msg += f"：外框由紙邊量出（四邊支持率 {', '.join(f'{v:.2f}' for v in support)}）"
    if p["interior_prior"] == "uvdoc" and grid is not None:
        msg += "，頁內初值來自學習式模型（UVDoc）"
    else:
        msg += "，外框候選來自學習式模型（UVDoc）再以紙邊精修" if grid is not None else "，外框以四角連線為候選再以紙邊精修"
        msg += "，頁內以四角透視為初值"
    if source == "uvdoc_prior":
        msg += "（有些位置是學習式模型的推估，不是量測）"
    if st.get("n_lines"):
        msg += f"，頁內 {st['n_lines']} 條格線／文字行量得殘差 {res_after:.1f} 像素（第 95 百分位）"
        if res_before is not None:
            msg += f"，透視攤平時為 {res_before:.1f} 像素"
        if n_iter:
            msg += f"，依這些線修正位移場 {n_iter} 輪"
            if res_cv is not None:
                msg += f"（沒參與擬合的線量得 {res_cv:.1f} 像素）"
    else:
        msg += "，頁內沒有可量的長線，頁內只靠外框內插" + ("與學習式初值（推估，非量測）" if source == "uvdoc_prior" else "")
    if pseudo:
        msg += f"；沒有現成的四角，四角由紙邊量出（{pseudo_info.get('measured', 0)} 條邊有量測支持）"
    notes.append(msg + "。")
    review = conf < 0.6 or source == "uvdoc_prior" or bool(truncated) or pseudo
    if review:
        notes.append("攤平的信心值不高（或外框有推估的部分、紙超出四角、四角是自己量的），建議請使用者確認紙的外框。")
    Hc = _homography(corners, np.array([[0, 0], [Wo, 0], [Wo, Ho], [0, Ho]], np.float64))
    tm["total"] = time.time() - t_all
    return DewarpResult(image=out, map_x=X, map_y=Y, applied=True, source=source, curved=curved,
                        corners=corners, boundary=boundary, support=support, bow_px=[float(b) for b in bows], residual_px=res_after,
                        residual_before_px=res_before, straightness=_st_summary(st_before, st), confidence=float(conf), homography=Hc,
                        notes=notes, timings=tm, pseudo_quad=corners.copy() if pseudo else None,
                        measured_sides=int(pseudo_info.get("measured", sum(1 for u in use if u))), needs_review=review)


def _st_summary(before: dict, after: Optional[dict]) -> dict:
    keys = ("n_lines", "n_h", "n_v", "sag_median_pct", "sag_2nd_pct", "sag_max_pct")
    out = {"before": {k: before.get(k) for k in keys if k in before}}
    if after is not None:
        out["after"] = {k: after.get(k) for k in keys if k in after}
    return out


if __name__ == "__main__":  # 部署時預先下載模型：python -m mortise.recognition.dewarp --download
    import sys

    if "--download" in sys.argv:
        path, msgs = ensure_uvdoc_model({"uvdoc": "sync", "_force_retry": True})
        print("\n".join(msgs) or "快取裡已有模型。")
        print(path if path is not None else "下載失敗")
        sys.exit(0 if path is not None else 1)
    print("用法：python -m mortise.recognition.dewarp --download")
