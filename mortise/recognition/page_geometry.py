"""頁面幾何（②頁面用）：在照片裡找出頁面四角、由透視反推真實長寬比、透視攤平、影像增強。

座標慣例
    一律是「連續像素座標」：原點在影像左上角的「角」，y 向下；第 i 列第 j 行像素涵蓋
    [j, j+1) × [i, i+1)，中心在 (j+0.5, i+0.5)。所以整張圖的四角是 (0,0)、(W,0)、(W,H)、(0,H)，
    與 truth 的「頁寬為 1」相對座標乘上影像寬度的結果一致。呼叫 OpenCV（像素中心在整數）時
    在內部換算（平移 0.5），對外不暴露。

做法
    find_page_quad
      0. 上游若已判定是截圖或掃描（params["input_type"]），直接以整張圖為頁面。
      1. 初值（縮圖上做，長邊 detect_max_side）：
         a. 邊緣輪廓：Lab 三通道的 Canny（多組門檻）→ 膨脹 → 所有輪廓的凸包；
         b. 顏色區域：以中央區塊的眾數色為種子，取 Lab 色差小於門檻、且連到中央的區域的外輪廓凸包。
         凸包用 approxPolyDP 化成多邊形；超過四個頂點時，反覆拿掉最短的邊（以相鄰兩邊延長相交），
         這樣被遮住或被裁掉的角也能補回來。以四邊的梯度支持率與面積給候選打分。
      2. 精修（原圖解析度）：對每條邊等距取樣，沿法線方向用雙線性內插取剖面，
         以「內側→外側」的色差方向投影的一階導數找邊緣峰值，拋物線內插到次像素；
         每條邊對這些邊緣點做 Tukey 雙權重的迭代加權總體最小平方直線擬合（排除手指、文字等離群點），
         四條直線兩兩相交得四角。搜尋半徑由大到小迭代四輪；每輪由導數剖面的半高寬量出邊緣模糊 σ，
         嚴重模糊時自動放大搜尋半徑（radius_per_sigma × σ），並把 blur_err_coef × σ 計入四角誤差。
      3. 判定與信心值：四邊都要有足夠的邊緣支持率；四角誤差由直線擬合的協方差傳播到交點。
         四邊都與影像軸平行（axis_tol_deg 內）且影像幾乎沒有感光雜訊時，視為截圖裡的色塊或表格而不是紙張。
         精修全部失敗但縮圖輪廓的四邊梯度支持率都夠高時，退回輪廓近似（method="contour"，低信心、誤差數像素）。
         畫面中有傾斜的長直邊（透視線索）卻湊不成四邊時，判為照片且頁面超出畫面或被遮住。
         找不到可信的四邊：回傳 method="full_image"。若影像像截圖或掃描（無雜訊，或四周邊條是均勻的頁邊色）
         給高信心；否則給低信心（交給上層請使用者手動拉四角），並在 notes 記下找到了哪幾邊。
      4. 長寬比：Zhang 與 He（2007, "Whiteboard scanning and image enhancement"）的方法——
         假設方形像素、主點在影像中心，由四角解出焦距 f，再得真實長寬比。
         以四角誤差做蒙地卡羅傳播得誤差範圍。焦距解不出來或不穩定時：
         近乎正面 → 退回邊長比（aspect_method="side_ratio"）；有明顯單向透視 → 用典型手機焦距的先驗範圍
         （aspect_method="focal_prior"），誤差放大。params["focal_px"]（例如由 EXIF 換算）可直接指定焦距。
    rectify
      由四角與長寬比建立單應矩陣，輸出尺寸依「最長的那一組對邊」決定（那條邊 1:1 取樣，不放大超過原圖密度），
      cv2.warpPerspective 雙線性內插。紙張彎曲的去彎曲未做：留有替換點 set_dewarper()。
    enhance
      去陰影與光照不均：縮圖上以形態學閉運算（淺底）或開運算（深底）去掉文字，再中值與高斯平滑得照度圖，
      原圖除以照度圖；白平衡：淺底時把背景參考色拉成中性灰白（增益有上下限）。只供偵測與辨識用，
      量顏色時請用未增強的原圖。

已知限制
    - 鏡頭畸變未校正：廣角近拍時邊不是直線，擬合殘差變大（會反映在誤差與信心值）。
    - 主點假設在影像中心；照片若被裁切過，透視反推的長寬比會有偏差。
    - 紙張與背景完全同色、沒有陰影時，邊緣不可觀測，只能回報 full_image 低信心。
    - 完全正面、四邊與影像軸平行且沒有感光雜訊的照片，會被當成截圖（整張圖就是頁面）。
    - 截圖裡若有非軸向（旋轉過）的大色塊，可能被誤認為紙張。
    - 頁面彎曲（書本內頁）只做透視攤平。

可單獨升級的點
    - 初值偵測可換成角點熱圖模型（例如 DocAligner 類的 ONNX），精修仍用本模組的直線擬合：_initial_candidates()。
    - 去彎曲：set_dewarper(fn)，fn(影像, PageQuad, params) -> 攤平影像 或 None（例如 UVDoc）。
    - enhance 可換成去陰影模型；介面不變。
"""

from __future__ import annotations

import math
from typing import Callable, Optional

import cv2
import numpy as np

from .types import Estimate, PageQuad

DEFAULT_PARAMS: dict = {
    # ---- 上游提示 ----
    "input_type": None,  # ①給的影像類型："photo"、"screenshot"、"scan" 或 None（自行判斷）
    "focal_px": None,  # 已知焦距（像素，以這張影像的尺度）；None 表示由四角反推
    # ---- 初值偵測（縮圖上）----
    "detect_max_side": 900,  # 偵測初值時縮圖的長邊（像素）
    "detect_blur_sigma": 1.2,  # 初值偵測前的高斯模糊 σ（縮圖像素）
    "canny_thresholds": [(10, 30), (25, 70), (50, 140)],  # Canny 的（低、高）門檻組（Lab 梯度）
    "seed_window_frac": 0.3,  # 顏色區域法：中央種子區塊的邊長佔影像的比例
    "region_de_thresholds": [6.0, 10.0, 16.0, 26.0],  # 顏色區域法：與種子色的 Lab 色差門檻（ΔE）
    "approx_eps_fracs": [0.008, 0.015, 0.025, 0.04],  # approxPolyDP 的容差（佔周長的比例），由小到大嘗試
    "max_hull_vertices": 10,  # 多邊形頂點數超過這個就放棄化成四邊形
    "max_candidates_refined": 4,  # 送去原圖精修的候選數上限
    "min_area_frac": 0.10,  # 頁面面積至少佔整張圖的比例
    "max_area_frac": 0.985,  # 超過這個比例視同整張圖
    "min_corner_angle_deg": 30.0,  # 四角的內角下限（度）；太尖的四邊形不可能是透視下的紙張
    "corner_out_max_frac": 0.12,  # 允許角落落在畫面外的距離上限（佔影像對角線的比例）
    # ---- 精修（原圖解析度）----
    "refine_sigma": 1.0,  # 精修前的高斯模糊 σ（像素）
    "refine_radii": [None, 4.0, 2.5, 2.5],  # 各輪沿法線的搜尋半徑（像素）；None 表示依縮圖比例自動決定
    "radius_per_sigma": 2.5,  # 搜尋半徑至少是上一輪量到的邊緣模糊 σ 的幾倍
    "max_search_radius": 48.0,  # 搜尋半徑上限（像素）
    "blur_err_coef": 0.06,  # 四角誤差另加「邊緣模糊 σ × 這個係數」（像素）
    "err_conf_px": 1.5,  # 四角平均誤差達到這個（像素）時，信心值的誤差項降為一半
    "profile_step": 0.5,  # 剖面取樣間距（像素）
    "sample_step": 4.0,  # 沿邊取樣間距（像素）
    "max_samples_per_side": 400,  # 每條邊取樣點上限
    "corner_margin_frac": 0.04,  # 每條邊兩端各略過的比例（避開角落）
    "min_edge_grad": 1.5,  # 邊緣點的最小梯度（色階／像素）
    "rel_edge_grad": 0.3,  # 邊緣點梯度至少是該邊中位數的幾倍
    "min_side_contrast": 4.0,  # 內外色差低於這個（色階）就改用不分方向的梯度大小
    "tukey_c": 4.685,  # Tukey 雙權重常數（以 MAD 尺度為單位）
    "min_resid_scale": 0.25,  # 殘差尺度的下限（像素），避免過度自信
    "fit_iters": 12,  # 每輪直線擬合的 IRLS 次數
    "inlier_px": 1.5,  # 與擬合直線距離在這個之內（或 2.5σ 內）算邊緣支持
    "corr_samples": 4.0,  # 相鄰邊緣點誤差相關的長度（樣本數），用來折算有效樣本數
    "corner_err_floor": 0.08,  # 角點誤差下限（像素）
    # ---- 判定 ----
    "min_side_support": 0.4,  # 每條邊至少要有這個比例的長度有邊緣支持
    "frame_tol_frac": 0.01,  # 四角都在影像四角這個距離內（佔短邊）→ 這張圖本身就是頁面
    "axis_tol_deg": 0.35,  # 四邊與影像軸的夾角都在這之內 → 可能是截圖內的色塊
    "persp_edge_support": 0.6,  # 透視線索：一條傾斜長直邊至少要有這個邊緣支持率
    "persp_edge_min_deg": 1.0,  # 透視線索：與影像軸的夾角至少這麼多（度）
    "persp_edge_min_len_frac": 0.35,  # 透視線索：邊長至少佔影像短邊的比例
    "digital_noise_max": 0.6,  # 雜訊 σ（色階）低於這個視為數位影像（截圖、軟體匯出）
    "border_strip_frac": 0.02,  # 判斷頁邊時取的影像四周邊條寬度（佔短邊）
    "border_uniform_de": 12.0,  # 邊條內像素與邊條中位色差在這之內算均勻（ΔE）
    "border_uniform_frac": 0.92,  # 邊條至少這個比例的像素均勻
    "good_contrast": 20.0,  # 內外色差達到這個（色階）時，對比項的信心值滿分
    "conf_full_digital": 0.9,  # 判為截圖（無雜訊）時 full_image 的信心值
    "conf_full_margin": 0.7,  # 四周是均勻頁邊色時 full_image 的信心值
    "conf_full_fail": 0.15,  # 找不到可信四邊時 full_image 的信心值
    "contour_min_support": 0.6,  # 退路：縮圖上四邊的快速梯度支持率都達到這個，才可用輪廓近似當答案
    "contour_corner_err_px": 2.0,  # 退路：輪廓近似的四角誤差（縮圖像素）
    "conf_contour": 0.35,  # 退路（method="contour"）的信心值
    # ---- 長寬比 ----
    "mc_samples": 64,  # 蒙地卡羅傳播的樣本數
    "mc_min_corner_sigma": 0.3,  # 蒙地卡羅擾動四角時的 σ 下限（像素）
    "focal_min_frac": 0.3,  # 合理焦距下限（佔影像對角線）
    "focal_max_frac": 4.0,  # 合理焦距上限（佔影像對角線）
    "persp_max_rel_err": 0.03,  # 透視反推長寬比的相對誤差超過這個就視為不穩定
    "frontal_k_tol": 0.015,  # 透視強度（|k−1|）低於這個視為近乎正面，可用邊長比
    "focal_prior_fracs": (0.45, 0.6, 1.0),  # 焦距先驗（佔對角線）：下限、典型、上限（手機主鏡頭等效 26 mm 約 0.6）
    "side_ratio_sys_coef": 0.5,  # 邊長比的系統誤差係數：乘上對邊長度比的對數差
    "aspect_conf_scale": 0.02,  # 長寬比相對誤差為這個值時信心值是 0.5
    # ---- 攤平 ----
    "rectify_interp": "linear",  # 內插："linear" 或 "cubic"
    "max_output_side": 12000,  # 攤平輸出長邊上限（像素）
    "dewarp": False,  # 是否呼叫去彎曲替換點（尚未實作，預設關）
    # ---- 增強 ----
    "enhance_work_side": 1000,  # 估照度圖時的縮圖長邊（像素）
    "bg_kernel_frac": 0.035,  # 形態學核大小（佔縮圖長邊）：要大於筆畫寬度
    "dark_bg_luma": 100.0,  # 背景亮度中位數低於這個（0 到 255）視為深色底
    "max_gain": 3.0,  # 照度補償的最大增益
    "min_gain": 0.5,  # 照度補償的最小增益
    "white_target": 245.0,  # 淺底時背景要拉到的亮度
    "wb_max_chroma": 40.0,  # 背景參考色的彩度（Lab a、b 的距離）超過這個就不做白平衡（可能是彩色紙）
}

_DEWARPER: Optional[Callable] = None


def set_dewarper(fn: Optional[Callable]) -> None:
    """替換點：註冊去彎曲函數 fn(影像, PageQuad, params) -> 攤平後影像（或 None 表示不處理）。"""
    global _DEWARPER
    _DEWARPER = fn


def _p(params: dict | None) -> dict:
    p = dict(DEFAULT_PARAMS)
    if params:
        p.update(params)
    return p


# ============================================================ 小工具


def _order_corners(pts: np.ndarray) -> np.ndarray:
    """排成左上、右上、右下、左下（影像方向，順時針），起點取 x+y 最小者。"""
    pts = np.asarray(pts, dtype=np.float64).reshape(-1, 2)
    c = pts.mean(axis=0)
    ang = np.arctan2(pts[:, 1] - c[1], pts[:, 0] - c[0])
    pts = pts[np.argsort(ang)]  # y 向下時 atan2 遞增即順時針
    start = int(np.argmin(pts[:, 0] + pts[:, 1]))
    return np.roll(pts, -start, axis=0)


def _line_through(a: np.ndarray, b: np.ndarray) -> tuple[np.ndarray, float]:
    d = b - a
    n = np.array([-d[1], d[0]], dtype=np.float64)
    n /= max(np.linalg.norm(n), 1e-12)
    return n, float(n @ a)


def _intersect(l1: tuple[np.ndarray, float], l2: tuple[np.ndarray, float]) -> Optional[np.ndarray]:
    A = np.array([l1[0], l2[0]])
    det = np.linalg.det(A)
    if abs(det) < 1e-9:
        return None
    return np.linalg.solve(A, np.array([l1[1], l2[1]]))


def _poly_area(q: np.ndarray) -> float:
    x, y = q[:, 0], q[:, 1]
    return 0.5 * abs(float(np.dot(x, np.roll(y, -1)) - np.dot(y, np.roll(x, -1))))


def _is_convex(q: np.ndarray) -> bool:
    s = []
    for i in range(4):
        a, b, c = q[i], q[(i + 1) % 4], q[(i + 2) % 4]
        u, v = b - a, c - b
        s.append(u[0] * v[1] - u[1] * v[0])
    s = np.array(s)
    return bool(np.all(s > 0) or np.all(s < 0))


def _corner_angles(q: np.ndarray) -> np.ndarray:
    out = []
    for i in range(4):
        a, b, c = q[i - 1], q[i], q[(i + 1) % 4]
        u, v = a - b, c - b
        cosv = float(u @ v) / max(np.linalg.norm(u) * np.linalg.norm(v), 1e-12)
        out.append(math.degrees(math.acos(max(-1.0, min(1.0, cosv)))))
    return np.array(out)


def _shift_h(h: np.ndarray) -> np.ndarray:
    """連續座標的單應矩陣 → OpenCV（像素中心在整數）的單應矩陣。"""
    t_in = np.array([[1, 0, 0.5], [0, 1, 0.5], [0, 0, 1]], dtype=np.float64)
    t_out = np.array([[1, 0, -0.5], [0, 1, -0.5], [0, 0, 1]], dtype=np.float64)
    return t_out @ h @ t_in


def homography_from_points(src: np.ndarray, dst: np.ndarray) -> np.ndarray:
    """四組對應點的單應矩陣（float64 直接解 8×8 線性方程，比 cv2.getPerspectiveTransform 的 float32 精確）。"""
    A, b = [], []
    for (x, y), (u, v) in zip(np.asarray(src, dtype=np.float64), np.asarray(dst, dtype=np.float64)):
        A.append([x, y, 1, 0, 0, 0, -u * x, -u * y])
        b.append(u)
        A.append([0, 0, 0, x, y, 1, -v * x, -v * y])
        b.append(v)
    h = np.linalg.solve(np.array(A), np.array(b))
    return np.append(h, 1.0).reshape(3, 3)


def _noise_sigma(gray: np.ndarray) -> float:
    """平坦區的雜訊 σ（色階）：高通殘差在低梯度像素上的 MAD。用原圖中央區塊，避免縮放影響。"""
    h, w = gray.shape
    cs = 512
    y0, x0 = max(0, h // 2 - cs // 2), max(0, w // 2 - cs // 2)
    g = gray[y0 : y0 + cs, x0 : x0 + cs].astype(np.float32)
    if g.size < 64:
        return 0.0
    hp = g - cv2.blur(g, (3, 3))
    gx = cv2.Sobel(g, cv2.CV_32F, 1, 0, ksize=3)
    gy = cv2.Sobel(g, cv2.CV_32F, 0, 1, ksize=3)
    mag = np.hypot(gx, gy)
    # 排除飽和像素（過曝的白紙、全黑）：雜訊在那裡被截掉，會低估
    sat = (cv2.dilate(g, np.ones((3, 3), np.uint8)) >= 252) | (cv2.erode(g, np.ones((3, 3), np.uint8)) <= 3)
    ok = ~sat
    if ok.mean() < 0.3:  # 大部分飽和（例如白底文件截圖）：飽和像素也一起算
        ok = np.ones_like(ok)
    flat = ok & (mag <= np.percentile(mag[ok], 40))
    if flat.sum() < 32:
        return 0.0
    # 3×3 平均的高通殘差：白雜訊 σ 的 sqrt(8/9) 倍
    return float(1.4826 * np.median(np.abs(hp[flat])) / math.sqrt(8.0 / 9.0))


def _border_uniform(lab: np.ndarray, p: dict) -> bool:
    """影像四周邊條是否都是均勻且一致的顏色（頁邊）。"""
    h, w = lab.shape[:2]
    b = max(2, int(round(p["border_strip_frac"] * min(h, w))))
    strips = [lab[:b], lab[-b:], lab[:, :b], lab[:, -b:]]
    meds = []
    for s in strips:
        s = s.reshape(-1, 3).astype(np.float32)
        m = np.median(s, axis=0)
        frac = float(np.mean(np.linalg.norm(s - m, axis=1) <= p["border_uniform_de"]))
        if frac < p["border_uniform_frac"]:
            return False
        meds.append(m)
    meds = np.array(meds)
    return bool(np.max(np.linalg.norm(meds - meds.mean(axis=0), axis=1)) <= p["border_uniform_de"] * 0.8)


# ============================================================ 初值偵測


def _reduce_polygon(poly: np.ndarray) -> Optional[np.ndarray]:
    """多邊形（>4 頂點）反覆拿掉最短的邊：以它前後兩邊延長相交取代兩個端點。"""
    poly = [np.asarray(v, dtype=np.float64) for v in poly]
    while len(poly) > 4:
        n = len(poly)
        lens = [np.linalg.norm(poly[(i + 1) % n] - poly[i]) for i in range(n)]
        i = int(np.argmin(lens))
        a0, a1 = poly[i - 1], poly[i]  # 前一邊
        b0, b1 = poly[(i + 1) % n], poly[(i + 2) % n]  # 後一邊
        x = _intersect(_line_through(a0, a1), _line_through(b0, b1))
        if x is None:
            return None
        j = (i + 1) % n
        if j == 0:
            poly = [x] + poly[1:i]
        else:
            poly = poly[:i] + [x] + poly[j + 1 :]
    return np.array(poly)


def _hull_to_quads(hull: np.ndarray, p: dict) -> list[np.ndarray]:
    out = []
    peri = cv2.arcLength(hull, True)
    for eps in p["approx_eps_fracs"]:
        ap = cv2.approxPolyDP(hull, eps * peri, True).reshape(-1, 2).astype(np.float64)
        if len(ap) < 4:
            break
        if len(ap) > p["max_hull_vertices"]:
            continue
        q = ap if len(ap) == 4 else _reduce_polygon(ap)
        if q is not None and len(q) == 4:
            out.append(_order_corners(q))
        if len(ap) == 4:
            break
    return out


def _quick_support(mag: np.ndarray, q: np.ndarray, thr: float) -> np.ndarray:
    """縮圖上快速估每條邊的梯度支持率（取樣點附近 ±1.5 像素內的最大梯度是否過門檻）。"""
    h, w = mag.shape
    sup = np.zeros(4)
    for k in range(4):
        a, b = q[k], q[(k + 1) % 4]
        L = np.linalg.norm(b - a)
        if L < 4:
            return np.zeros(4)
        n = int(min(200, max(16, L / 2)))
        t = np.linspace(0.05, 0.95, n)[:, None]
        pts = a + t * (b - a)
        d = (b - a) / L
        nrm = np.array([-d[1], d[0]])
        best = np.zeros(n)
        inside = np.ones(n, dtype=bool)
        for o in (-1.5, -0.75, 0.0, 0.75, 1.5):
            pp = pts + o * nrm - 0.5
            xi = np.round(pp[:, 0]).astype(int)
            yi = np.round(pp[:, 1]).astype(int)
            ok = (xi >= 1) & (xi < w - 1) & (yi >= 1) & (yi < h - 1)
            inside &= ok
            v = np.zeros(n)
            v[ok] = mag[yi[ok], xi[ok]]
            best = np.maximum(best, v)
        if inside.sum() < 4:
            sup[k] = 0.0
        else:
            sup[k] = float(np.mean(best[inside] > thr)) * float(inside.mean())
    return sup


def _initial_candidates(small: np.ndarray, p: dict) -> list[np.ndarray]:
    """縮圖上產生頁面四邊形候選（連續座標，縮圖尺度）。替換點：可改用角點熱圖模型。"""
    h, w = small.shape[:2]
    blur = cv2.GaussianBlur(small, (0, 0), p["detect_blur_sigma"])
    lab = cv2.cvtColor(blur, cv2.COLOR_RGB2LAB)
    area_img = float(h * w)
    hulls = []
    # a. 邊緣輪廓
    kernel = np.ones((3, 3), np.uint8)
    for lo, hi in p["canny_thresholds"]:
        e = cv2.Canny(lab, lo, hi, L2gradient=True)
        e = cv2.dilate(e, kernel)
        cnts, _ = cv2.findContours(e, cv2.RETR_LIST, cv2.CHAIN_APPROX_SIMPLE)
        for c in cnts:
            if len(c) < 8:
                continue
            hull = cv2.convexHull(c)
            if cv2.contourArea(hull) >= p["min_area_frac"] * area_img:
                hulls.append(hull)
    # b. 顏色區域（由中央的眾數色長出去）
    labf = cv2.cvtColor(cv2.medianBlur(small, 5), cv2.COLOR_RGB2LAB).astype(np.float32)
    sw = p["seed_window_frac"]
    y0, y1 = int(h * (0.5 - sw / 2)), int(h * (0.5 + sw / 2))
    x0, x1 = int(w * (0.5 - sw / 2)), int(w * (0.5 + sw / 2))
    win = labf[y0:y1, x0:x1].reshape(-1, 3)
    qz = (win // 8).astype(np.int32)
    keys = qz[:, 0] * 1024 + qz[:, 1] * 32 + qz[:, 2]
    vals, counts = np.unique(keys, return_counts=True)
    mode = vals[np.argmax(counts)]
    seed = win[keys == mode].mean(axis=0)
    dist = np.linalg.norm(labf - seed, axis=2)
    k7 = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (7, 7))
    for t in p["region_de_thresholds"]:
        m = (dist < t).astype(np.uint8)
        m = cv2.morphologyEx(m, cv2.MORPH_CLOSE, k7)
        m = cv2.morphologyEx(m, cv2.MORPH_OPEN, k7)
        n, lbl, stats, _ = cv2.connectedComponentsWithStats(m, connectivity=4)
        if n <= 1:
            continue
        sub = lbl[y0:y1, x0:x1]
        ids, cnt = np.unique(sub[sub > 0], return_counts=True)
        if len(ids) == 0:
            continue
        cid = ids[np.argmax(cnt)]
        comp = (lbl == cid).astype(np.uint8)
        cnts, _ = cv2.findContours(comp, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        if not cnts:
            continue
        c = max(cnts, key=cv2.contourArea)
        hull = cv2.convexHull(c)
        if cv2.contourArea(hull) >= p["min_area_frac"] * area_img:
            hulls.append(hull)
    quads = []
    for hull in hulls:
        for q in _hull_to_quads(hull, p):
            q = q + 0.5  # 輪廓座標（像素中心在整數）→ 連續座標
            quads.append(q)
    return quads


def _dedup(quads: list[np.ndarray], tol: float) -> list[np.ndarray]:
    out: list[np.ndarray] = []
    for q in quads:
        if all(np.max(np.linalg.norm(q - o, axis=1)) > tol for o in out):
            out.append(q)
    return out


# ============================================================ 精修


def _sample(img: np.ndarray, xs: np.ndarray, ys: np.ndarray) -> np.ndarray:
    mx = (xs - 0.5).astype(np.float32)
    my = (ys - 0.5).astype(np.float32)
    return cv2.remap(img, mx, my, cv2.INTER_LINEAR, borderMode=cv2.BORDER_REPLICATE)


def _fit_line_robust(pts: np.ndarray, w0: np.ndarray, init: tuple[np.ndarray, float], scale0: float, p: dict) -> dict:
    """Tukey 雙權重 IRLS 的總體最小平方直線擬合。回傳法向量、偏移、殘差尺度、權重、協方差資訊。"""
    n_vec, d = init
    w = w0.copy()
    s = max(scale0, p["min_resid_scale"])
    for it in range(p["fit_iters"]):
        r = pts @ n_vec - d
        u = r / (p["tukey_c"] * s)
        w = w0 * np.where(np.abs(u) < 1, (1 - u * u) ** 2, 0.0)
        if w.sum() < 1e-9 or (w > 0).sum() < 3:
            break
        c = (w[:, None] * pts).sum(axis=0) / w.sum()
        X = pts - c
        cov = (w[:, None, None] * (X[:, :, None] * X[:, None, :])).sum(axis=0) / w.sum()
        evals, evecs = np.linalg.eigh(cov)
        n_new = evecs[:, 0]
        if n_new @ n_vec < 0:
            n_new = -n_new
        n_vec, d = n_new, float(n_new @ c)
        r = pts @ n_vec - d
        act = w > 0
        mad = 1.4826 * np.median(np.abs(r[act])) if act.any() else s
        s_new = max(mad, p["min_resid_scale"])
        # 尺度只縮不放，避免被離群點拉大
        s = min(s, s_new) if it > 0 else s_new
    r = pts @ n_vec - d
    act = w > 0
    if act.sum() >= 3:
        c = (w[:, None] * pts).sum(axis=0) / w.sum()
        t_dir = np.array([n_vec[1], -n_vec[0]])
        t = (pts - c) @ t_dir
        wn = w / w.sum()
        n_eff = (w.sum() ** 2) / max((w * w).sum(), 1e-12) / p["corr_samples"]
        sig = float(np.sqrt(np.sum(wn * r * r) / max(1e-9, 1 - (wn * wn).sum())))
        sig = max(sig, 0.05)
        var_off = sig * sig / max(n_eff, 1.0)
        spread = float(np.sum(wn * t * t))
        var_th = var_off / max(spread, 1e-9)
    else:
        c, sig, var_off, var_th = pts.mean(axis=0) if len(pts) else np.zeros(2), 99.0, 99.0, 1.0
    return {"n": n_vec, "d": d, "w": w, "sigma": sig, "scale": s, "c": c, "var_off": var_off, "var_th": var_th}


def _refine(img: np.ndarray, quad: np.ndarray, r0: float, p: dict) -> Optional[dict]:
    """在原圖（已模糊的 float32 影像）上以邊緣點直線擬合精修四邊形。"""
    H, W = img.shape[:2]
    q = quad.astype(np.float64).copy()
    radii = [r0 if r is None else float(r) for r in p["refine_radii"]]
    edge_sigma = 0.0  # 上一輪量到的邊緣模糊寬度（像素）
    hstep = p["profile_step"]
    res = None
    for R in radii:
        # 搜尋半徑要涵蓋邊緣的模糊寬度（嚴重模糊時自動放大）
        R = float(min(p["max_search_radius"], max(R, p["radius_per_sigma"] * edge_sigma)))
        cen = q.mean(axis=0)
        lines, info = [], []
        for k in range(4):
            a, b = q[k], q[(k + 1) % 4]
            L = float(np.linalg.norm(b - a))
            if L < 8:
                return None
            tdir = (b - a) / L
            nout = np.array([tdir[1], -tdir[0]])
            if (0.5 * (a + b) - cen) @ nout < 0:
                nout = -nout
            ns = int(np.clip(L / p["sample_step"], 16, p["max_samples_per_side"]))
            m = p["corner_margin_frac"]
            ts = np.linspace(m, 1 - m, ns)
            base = a[None, :] + ts[:, None] * (b - a)[None, :]
            offs = np.arange(-R, R + hstep / 2, hstep)
            X = base[:, 0:1] + offs[None, :] * nout[0]
            Y = base[:, 1:2] + offs[None, :] * nout[1]
            valid = np.all((X >= 0.5) & (X <= W - 0.5) & (Y >= 0.5) & (Y <= H - 0.5), axis=1)
            P = _sample(img, X, Y)
            if P.ndim == 2:
                P = P[:, :, None]
            D = (P[:, 2:] - P[:, :-2]) / (2 * hstep)
            so = offs[1:-1]
            if valid.sum() >= 3:
                diff = np.median(P[valid, -1] - P[valid, 0], axis=0)
            else:
                diff = np.zeros(P.shape[2])
            contrast = float(np.linalg.norm(diff))
            if contrast >= p["min_side_contrast"]:
                g = D @ (diff / contrast)
            else:
                g = np.linalg.norm(D, axis=2)
            gi = g[:, 1:-1]
            j = np.argmax(gi, axis=1) + 1
            idx = np.arange(ns)
            y0, y1, y2 = g[idx, j - 1], g[idx, j], g[idx, j + 1]
            den = y0 - 2 * y1 + y2
            delta = np.where(np.abs(den) > 1e-9, 0.5 * (y0 - y2) / np.where(np.abs(den) > 1e-9, den, 1), 0.0)
            delta = np.clip(delta, -0.5, 0.5)
            s_hat = so[j] + delta * hstep
            peak = y1
            # 邊緣寬度：導數剖面在峰值一半以上的連續寬度（FWHM），換算成高斯 σ
            above = g >= 0.5 * peak[:, None]
            jj = np.arange(g.shape[1])[None, :]
            left_edge = np.where(~above & (jj < j[:, None]), jj, -1).max(axis=1)
            right_edge = np.where(~above & (jj > j[:, None]), jj, g.shape[1]).min(axis=1)
            fwhm = (right_edge - left_edge - 1) * hstep
            pts = base + s_hat[:, None] * nout[None, :]
            medpk = np.median(peak[valid]) if valid.any() else 0.0
            keep = valid & (peak >= p["min_edge_grad"]) & (peak >= p["rel_edge_grad"] * medpk)
            init = _line_through(a, b)
            if keep.sum() < 6:
                fit = {"n": init[0], "d": init[1], "w": np.zeros(ns), "sigma": 99.0, "var_off": 99.0, "var_th": 1.0, "c": 0.5 * (a + b)}
                support = 0.0
            else:
                fit = _fit_line_robust(pts[keep], np.sqrt(peak[keep]), init, R / 2.0, p)
                r = np.full(ns, np.inf)
                r[keep] = np.abs(pts[keep] @ fit["n"] - fit["d"])
                tol = max(p["inlier_px"], 2.5 * fit["sigma"])
                vis = max(int(valid.sum()), 1)
                support = float(np.sum((r <= tol) & keep)) / vis
            lines.append((fit["n"], fit["d"]))
            info.append(
                {
                    "fit": fit,
                    "support": support,
                    "visible": float(valid.mean()),
                    "contrast": contrast,
                    "n_pts": int(keep.sum()),
                    "edge_sigma": float(np.median(fwhm[keep])) / 2.355 if keep.sum() >= 6 else 0.0,
                }
            )
        newq = []
        for k in range(4):
            x = _intersect(lines[k - 1], lines[k])  # 第 k 角 = 第 k−1 邊（左側）與第 k 邊的交點
            if x is None:
                return None
            newq.append(x)
        newq = np.array(newq)
        if not _is_convex(newq):
            return None
        q = newq
        edge_sigma = float(np.median([i["edge_sigma"] for i in info]))
        res = {"corners": q, "lines": lines, "info": info, "edge_sigma": edge_sigma, "radius": R}
    return res


def _corner_errors(res: dict, p: dict) -> np.ndarray:
    q, info = res["corners"], res["info"]
    errs = np.zeros(4)
    for k in range(4):
        e_sq = 0.0
        for side in (k - 1, k):
            f = info[side % 4]["fit"]
            tdir = np.array([f["n"][1], -f["n"][0]])
            dt = float((q[k] - f["c"]) @ tdir)
            e_sq += f["var_off"] + dt * dt * f["var_th"]
        n1, n2 = info[(k - 1) % 4]["fit"]["n"], info[k]["fit"]["n"]
        sin_phi = abs(float(n1[0] * n2[1] - n1[1] * n2[0]))
        errs[k] = math.sqrt(e_sq) / max(sin_phi, 0.2)
    # 模糊造成的系統誤差（角落變圓、邊緣兩側內容不對稱）：與邊緣寬度成正比
    errs = np.hypot(errs, p["blur_err_coef"] * res.get("edge_sigma", 0.0))
    return np.maximum(errs, p["corner_err_floor"])


# ============================================================ 長寬比


def _aspect_core(c: np.ndarray, w: int, h: int, focal: Optional[float]) -> tuple[Optional[float], Optional[float], float]:
    """Zhang & He（2007）：回傳（高÷寬、焦距像素、透視強度）。焦距解不出來時前兩者為 None。"""
    s = math.hypot(w, h)
    pp = np.array([w / 2.0, h / 2.0])
    def hm(v):
        u = (v - pp) / s
        return np.array([u[0], u[1], 1.0])
    m1, m2, m4, m3 = hm(c[0]), hm(c[1]), hm(c[2]), hm(c[3])  # m1 左上、m2 右上、m3 左下、m4 右下
    den2 = np.dot(np.cross(m2, m4), m3)
    den3 = np.dot(np.cross(m3, m4), m2)
    if abs(den2) < 1e-12 or abs(den3) < 1e-12:
        return None, None, 0.0
    k2 = np.dot(np.cross(m1, m4), m3) / den2
    k3 = np.dot(np.cross(m1, m4), m2) / den3
    strength = float(max(abs(k2 - 1), abs(k3 - 1)))
    n2 = k2 * m2 - m1
    n3 = k3 * m3 - m1
    if focal is not None:
        f = focal / s
    else:
        if abs(n2[2]) < 1e-9 or abs(n3[2]) < 1e-9:
            return None, None, strength
        f2 = -(n2[0] * n3[0] + n2[1] * n3[1]) / (n2[2] * n3[2])
        if not np.isfinite(f2) or f2 <= 0:
            return None, None, strength
        f = math.sqrt(f2)
    num = n2[0] ** 2 + n2[1] ** 2 + f * f * n2[2] ** 2
    dnm = n3[0] ** 2 + n3[1] ** 2 + f * f * n3[2] ** 2
    if num <= 0 or dnm <= 0:
        return None, None, strength
    return float(math.sqrt(dnm / num)), float(f * s), strength


def _side_ratio(c: np.ndarray) -> float:
    top = np.linalg.norm(c[1] - c[0])
    bottom = np.linalg.norm(c[2] - c[3])
    left = np.linalg.norm(c[3] - c[0])
    right = np.linalg.norm(c[2] - c[1])
    return float((left + right) / max(top + bottom, 1e-9))


def estimate_aspect(corners: np.ndarray, image_size: tuple[int, int], corner_err: Optional[np.ndarray] = None, params: dict | None = None) -> tuple[Estimate, str, Optional[float]]:
    """由四角（左上、右上、右下、左下）在透視下反推真實長寬比（高 ÷ 寬）。

    image_size 為（寬, 高）像素。回傳（Estimate、方法、焦距像素或 None）。
    方法："perspective"（解出焦距）、"known_focal"（params["focal_px"]）、"side_ratio"（近乎正面）、
    "focal_prior"（單向透視，焦距用先驗範圍）。
    """
    p = _p(params)
    w, h = image_size
    c = np.asarray(corners, dtype=np.float64)
    diag = math.hypot(w, h)
    rng = np.random.default_rng(12345)
    sig = np.full(4, p["mc_min_corner_sigma"]) if corner_err is None else np.maximum(np.asarray(corner_err, dtype=np.float64), p["mc_min_corner_sigma"])
    noise = rng.standard_normal((p["mc_samples"], 4, 2)) * sig[None, :, None]
    samples = c[None] + noise

    def mc(focal):
        vals = []
        for s_ in samples:
            a, _, _ = _aspect_core(s_, w, h, focal)
            if a is not None:
                vals.append(a)
        return np.array(vals)

    a_side = _side_ratio(c)
    top, bottom = np.linalg.norm(c[1] - c[0]), np.linalg.norm(c[2] - c[3])
    left, right = np.linalg.norm(c[3] - c[0]), np.linalg.norm(c[2] - c[1])
    keystone = max(abs(math.log(max(top, 1e-9) / max(bottom, 1e-9))), abs(math.log(max(left, 1e-9) / max(right, 1e-9))))

    def conf_of(rel):
        return float(1.0 / (1.0 + (rel / p["aspect_conf_scale"]) ** 2))

    if p["focal_px"]:
        a0, f0, _ = _aspect_core(c, w, h, float(p["focal_px"]))
        vals = mc(float(p["focal_px"]))
        if a0 is not None and len(vals) > 3:
            err = float(np.std(vals))
            return Estimate(a0, err, conf_of(err / a0)), "known_focal", float(p["focal_px"])
    a0, f0, strength = _aspect_core(c, w, h, None)
    if a0 is not None:
        fs = []
        vals = []
        for s_ in samples:
            a, f, _ = _aspect_core(s_, w, h, None)
            if a is not None:
                vals.append(a)
                fs.append(f)
        ok_frac = len(vals) / len(samples)
        if (
            ok_frac >= 0.9
            and p["focal_min_frac"] * diag <= f0 <= p["focal_max_frac"] * diag
            and np.std(vals) / a0 <= p["persp_max_rel_err"]
        ):
            err = float(math.hypot(np.std(vals), abs(np.median(vals) - a0)))
            return Estimate(a0, err, conf_of(err / a0)), "perspective", f0
    if strength < p["frontal_k_tol"]:
        vals = np.array([_side_ratio(s_) for s_ in samples])
        sys_err = a_side * (p["side_ratio_sys_coef"] * keystone + 0.5 * strength)
        err = float(math.hypot(np.std(vals), sys_err))
        return Estimate(a_side, err, conf_of(err / a_side)), "side_ratio", None
    lo, mid, hi = p["focal_prior_fracs"]
    a_mid, _, _ = _aspect_core(c, w, h, mid * diag)
    ends = [_aspect_core(c, w, h, fr * diag)[0] for fr in (lo, hi)]
    ends = [e for e in ends if e is not None]
    if a_mid is None:
        err = abs(a_side) * 0.15
        return Estimate(a_side, err, conf_of(0.15)), "side_ratio", None
    vals = mc(mid * diag)
    spread = max([abs(e - a_mid) for e in ends] + [0.0]) / 2.0
    err = float(math.sqrt(spread**2 + (np.std(vals) if len(vals) > 3 else 0.0) ** 2))
    return Estimate(a_mid, err, conf_of(err / a_mid)), "focal_prior", None


# ============================================================ 公開介面


def _full_image(image: np.ndarray, conf: float, notes: list[str]) -> PageQuad:
    h, w = image.shape[:2]
    corners = np.array([[0, 0], [w, 0], [w, h], [0, h]], dtype=np.float64)
    a = h / w
    est = Estimate(a, a * (1.0 / min(w, h)), conf)
    return PageQuad(
        corners=corners,
        confidence=float(conf),
        method="full_image",
        aspect_ratio=float(a),
        notes=notes,
        corner_error_px=np.zeros(4),
        aspect=est,
        aspect_method="image",
        focal_px=None,
        edge_support=None,
    )


def find_page_quad(image: np.ndarray, params: dict | None = None) -> PageQuad:
    """找出頁面四角（左上、右上、右下、左下，連續像素座標）、信心值與真實長寬比。"""
    p = _p(params)
    img = np.ascontiguousarray(image[..., :3]) if image.ndim == 3 else np.ascontiguousarray(np.dstack([image] * 3))
    H, W = img.shape[:2]
    notes: list[str] = []
    itype = p["input_type"]
    if itype in ("screenshot", "scan"):
        return _full_image(img, p["conf_full_digital"], [f"上游判定為{'截圖' if itype == 'screenshot' else '掃描'}：整張圖就是頁面"])

    gray = cv2.cvtColor(img, cv2.COLOR_RGB2GRAY)
    noise = _noise_sigma(gray)
    digital = noise < p["digital_noise_max"]

    scale = min(1.0, p["detect_max_side"] / max(H, W))
    small = cv2.resize(img, (max(8, int(round(W * scale))), max(8, int(round(H * scale)))), interpolation=cv2.INTER_AREA) if scale < 1 else img.copy()
    sx, sy = small.shape[1] / W, small.shape[0] / H
    small_lab = cv2.cvtColor(small, cv2.COLOR_RGB2LAB)
    margin_uniform = _border_uniform(small_lab, p)

    persp_sides = 0  # 有邊緣支持、且不與影像軸平行的長直邊數（透視線索：照片才會有）

    contour_cand = None

    def fallback(reason: str) -> PageQuad:
        if contour_cand is not None and not digital:
            qc = _order_corners(contour_cand)
            err = np.full(4, p["contour_corner_err_px"] / min(sx, sy))
            est, amethod, focal = estimate_aspect(qc, (W, H), err, p)
            return PageQuad(
                corners=qc, confidence=p["conf_contour"], method="contour", aspect_ratio=float(est.value),
                notes=notes + [reason, f"邊緣精修失敗（可能嚴重模糊），四角取自縮圖輪廓近似，誤差約 {err[0]:.1f} 像素；建議請使用者確認四角"],
                corner_error_px=err, aspect=est, aspect_method=amethod, focal_px=focal, edge_support=None,
            )
        if persp_sides >= 1:
            return _full_image(img, p["conf_full_fail"], notes + [reason, "畫面中有傾斜的長直邊（透視線索），應是照片；找不到完整四邊，以整張圖為頁面；建議請使用者手動拉四角"])
        if digital:
            return _full_image(img, p["conf_full_digital"], notes + [f"影像幾乎無雜訊（σ≈{noise:.2f}），判為截圖或軟體匯出：整張圖就是頁面"])
        if margin_uniform:
            return _full_image(img, p["conf_full_margin"], notes + ["四周是均勻的頁邊色，判為掃描或整頁拍攝：整張圖就是頁面"])
        return _full_image(img, p["conf_full_fail"], notes + [reason, "找不到可信的頁面四邊，以整張圖為頁面；建議請使用者手動拉四角"])

    cands = _initial_candidates(small, p)
    if not cands:
        return fallback("沒有找到夠大的四邊形輪廓")
    blur_s = cv2.GaussianBlur(small, (0, 0), p["detect_blur_sigma"]).astype(np.float32)
    gx = cv2.Sobel(blur_s, cv2.CV_32F, 1, 0, ksize=3) / 8.0
    gy = cv2.Sobel(blur_s, cv2.CV_32F, 0, 1, ksize=3) / 8.0
    mag = np.sqrt((gx * gx + gy * gy).max(axis=2))
    thr = max(p["min_edge_grad"] * 1.5, float(np.percentile(mag, 75)))
    area_s = small.shape[0] * small.shape[1]
    scored = []
    for q in _dedup(cands, tol=3.0):
        af = _poly_area(q) / area_s
        if af < p["min_area_frac"]:
            continue
        sup = _quick_support(mag, q, thr)
        score = float(np.mean(sup)) * min(1.0, float(np.min(sup)) / 0.5) * math.sqrt(af)
        scored.append((score, q, sup))
    if not scored:
        return fallback("候選四邊形面積都太小")
    scored.sort(key=lambda t: -t[0])

    img_f = cv2.GaussianBlur(img.astype(np.float32), (0, 0), p["refine_sigma"])
    r0 = 3.0 / min(sx, sy) + 4.0
    best = None
    diag = math.hypot(W, H)
    contour_cand = None  # 退路：精修全部失敗時，用縮圖輪廓近似的四邊形
    for _, qs, qsup in scored[: p["max_candidates_refined"]]:
        if contour_cand is None and float(np.min(qsup)) >= p["contour_min_support"]:
            contour_cand = qs / np.array([sx, sy])
        qf = qs / np.array([sx, sy])
        res = _refine(img_f, qf, r0, p)
        if res is None:
            continue
        q = res["corners"]
        sup = np.array([i["support"] for i in res["info"]])
        af = _poly_area(q) / (W * H)
        out_d = 0.0
        for v in q:
            dx = max(0.0, -v[0], v[0] - W)
            dy = max(0.0, -v[1], v[1] - H)
            out_d = max(out_d, math.hypot(dx, dy))
        ang = _corner_angles(q)
        geom_ok = (
            p["min_area_frac"] <= af
            and out_d <= p["corner_out_max_frac"] * diag
            and ang.min() >= p["min_corner_angle_deg"]
            and ang.max() <= 180 - p["min_corner_angle_deg"]
        )
        score = float(np.mean(sup)) * min(1.0, float(np.min(sup)) / 0.6) * math.sqrt(min(af, 1.0))
        if not geom_ok:
            score *= 0.01
        res.update({"support": sup, "area_frac": af, "out_d": out_d, "score": score, "geom_ok": geom_ok})
        if best is None or score > best["score"]:
            best = res
    if best is None:
        return fallback("四邊形精修失敗")

    q = best["corners"]
    sup = best["support"]
    side_names = ["上", "右", "下", "左"]
    angs = []
    for k in range(4):
        d = q[(k + 1) % 4] - q[k]
        a = math.degrees(math.atan2(d[1], d[0])) % 90.0
        angs.append(min(a, 90.0 - a))
    lens = [float(np.linalg.norm(q[(k + 1) % 4] - q[k])) for k in range(4)]
    persp_sides = sum(
        1
        for k in range(4)
        if sup[k] >= p["persp_edge_support"] and angs[k] > p["persp_edge_min_deg"] and lens[k] >= p["persp_edge_min_len_frac"] * min(W, H)
    )
    found = [side_names[k] for k in range(4) if sup[k] >= p["min_side_support"]]
    if not best["geom_ok"]:
        return fallback("精修後的四邊形不合理（角度、面積或超出畫面太多）")
    if sup.min() < p["min_side_support"]:
        miss = [side_names[k] for k in range(4) if sup[k] < p["min_side_support"]]
        msg = f"只找到{'、'.join(found) or '零'}邊（{'、'.join(miss)}邊缺乏邊緣支持，頁面可能超出畫面、被遮住或與背景同色）"
        return fallback(msg)
    if best["area_frac"] >= p["max_area_frac"]:
        return fallback("四邊形幾乎等於整張圖")
    frame = np.array([[0, 0], [W, 0], [W, H], [0, H]], dtype=np.float64)
    if np.max(np.linalg.norm(q - frame, axis=1)) <= p["frame_tol_frac"] * min(W, H):
        return _full_image(img, p["conf_full_digital"] if digital else p["conf_full_margin"], ["頁面邊緣就是影像邊緣：整張圖就是頁面"])
    # 截圖內的色塊、表格：四邊與影像軸平行且無雜訊
    if max(angs) <= p["axis_tol_deg"] and digital:
        return _full_image(img, p["conf_full_digital"], ["找到的四邊形與影像軸完全平行且影像無雜訊：判為截圖內的色塊或表格，整張圖就是頁面"])

    cerr = _corner_errors(best, p)
    est, amethod, focal = estimate_aspect(q, (W, H), cerr, p)
    contrast = min(i["contrast"] for i in best["info"])
    # 信心值：邊緣支持率 × 對比 × 四角誤差（已含擬合殘差、邊緣彎曲與模糊）× 是否有角落在畫面外
    f_sup = float(np.clip((sup.min() - 0.3) / 0.5, 0.0, 1.0))
    f_con = float(np.clip(contrast / p["good_contrast"], 0.3, 1.0))
    f_err = 1.0 / (1.0 + (float(np.mean(cerr)) / p["err_conf_px"]) ** 2)
    f_vis = 0.6 if best["out_d"] > 0.5 else 1.0
    if best.get("edge_sigma", 0.0) > 4.0:
        notes.append(f"影像模糊（邊緣寬度 σ≈{best['edge_sigma']:.1f} 像素），四角誤差放大")
    conf = 0.05 + 0.93 * f_sup * math.sqrt(f_con) * f_err * f_vis
    if best["out_d"] > 0.5:
        notes.append("有角落落在畫面外，由邊線延長相交推得")
    if sup.min() < 0.75:
        notes.append(f"部分邊緣被遮住或對比低（最低支持率 {sup.min():.2f}）")
    if contrast < p["good_contrast"]:
        notes.append(f"頁面與背景對比低（最低內外色差 {contrast:.1f} 色階）")
    if amethod in ("side_ratio", "focal_prior"):
        notes.append("透視解不穩定，長寬比" + ("退回邊長比" if amethod == "side_ratio" else "用焦距先驗範圍估計，誤差較大"))
    return PageQuad(
        corners=q,
        confidence=float(conf),
        method="edge_fit",
        aspect_ratio=float(est.value),
        notes=notes,
        corner_error_px=cerr,
        aspect=est,
        aspect_method=amethod,
        focal_px=focal,
        edge_support=[float(v) for v in sup],
    )


def rectify(image: np.ndarray, quad: PageQuad, params: dict | None = None) -> tuple[np.ndarray, np.ndarray]:
    """透視攤平。回傳（攤平後影像、3×3 單應矩陣：原圖連續座標 → 攤平影像連續座標）。"""
    p = _p(params)
    c = np.asarray(quad.corners, dtype=np.float64)
    a = float(quad.aspect_ratio) if quad.aspect_ratio and quad.aspect_ratio > 0 else _side_ratio(c)
    top, bottom = np.linalg.norm(c[1] - c[0]), np.linalg.norm(c[2] - c[3])
    left, right = np.linalg.norm(c[3] - c[0]), np.linalg.norm(c[2] - c[1])
    if max(top, bottom) >= max(left, right):
        Wf = max(top, bottom)
        Hf = Wf * a
    else:
        Hf = max(left, right)
        Wf = Hf / a
    s = min(1.0, p["max_output_side"] / max(Wf, Hf))
    Wo, Ho = max(1, int(round(Wf * s))), max(1, int(round(Hf * s)))
    dst = np.array([[0, 0], [Wo, 0], [Wo, Ho], [0, Ho]], dtype=np.float64)
    Hm = homography_from_points(c, dst)
    interp = cv2.INTER_CUBIC if p["rectify_interp"] == "cubic" else cv2.INTER_LINEAR
    out = cv2.warpPerspective(image, _shift_h(Hm), (Wo, Ho), flags=interp, borderMode=cv2.BORDER_REPLICATE)
    if p["dewarp"] and _DEWARPER is not None:
        dw = _DEWARPER(out, quad, p)
        if dw is not None:
            out = dw
    return out, Hm


def enhance(image: np.ndarray, params: dict | None = None) -> np.ndarray:
    """去陰影與光照不均、白平衡，供偵測與辨識用（量顏色時請用原圖）。回傳 RGB uint8。"""
    p = _p(params)
    img = image[..., :3].astype(np.float32)
    H, W = img.shape[:2]
    s = min(1.0, p["enhance_work_side"] / max(H, W))
    small = cv2.resize(img, (max(4, int(round(W * s))), max(4, int(round(H * s)))), interpolation=cv2.INTER_AREA) if s < 1 else img.copy()
    luma = small @ np.array([0.299, 0.587, 0.114], dtype=np.float32)
    dark = float(np.median(luma)) < p["dark_bg_luma"]
    k = int(round(p["bg_kernel_frac"] * max(small.shape[:2])))
    k = max(3, k | 1)
    ker = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (k, k))
    op = cv2.MORPH_OPEN if dark else cv2.MORPH_CLOSE
    bg = cv2.morphologyEx(small, op, ker)
    mk = min(k, 31) | 1
    bg8 = np.clip(bg, 0, 255).astype(np.uint8)
    bg = cv2.medianBlur(bg8, mk).astype(np.float32)
    bg = cv2.GaussianBlur(bg, (0, 0), k / 2.0)
    bg = np.maximum(bg, 1.0)
    bl = bg @ np.array([0.299, 0.587, 0.114], dtype=np.float32)
    if dark:
        ref = np.full(3, float(np.median(bl)), dtype=np.float32)
    else:
        ref_rgb = np.percentile(bg.reshape(-1, 3), 90, axis=0)
        lab = cv2.cvtColor(np.clip(ref_rgb, 0, 255).astype(np.uint8)[None, None], cv2.COLOR_RGB2LAB)[0, 0].astype(np.float32)
        chroma = float(math.hypot(lab[1] - 128, lab[2] - 128))
        if chroma <= p["wb_max_chroma"]:
            ref = np.full(3, p["white_target"], dtype=np.float32)
        else:
            ref = ref_rgb.astype(np.float32) * (p["white_target"] / max(float(ref_rgb.max()), 1.0))
    gain = np.clip(ref[None, None, :] / bg, p["min_gain"], p["max_gain"])
    gain_full = cv2.resize(gain, (W, H), interpolation=cv2.INTER_LINEAR) if s < 1 else gain
    out = np.clip(img * gain_full, 0, 255)
    return out.astype(np.uint8)
